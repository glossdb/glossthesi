"""One owner per device, fed by a bounded queue.

Handlers do not touch the model: they hand the owner a job and await
it. The owner is one thread. It takes the waiting jobs one at a time,
callers in turn, so one caller's burst does not hold the device from
the rest, and an idle service answers at once. A job has the device to
itself: a forecast of a request's series, the misfit read of a frame.

The queue pushes back before memory does. Waiting jobs are arrays in
host memory, so the bound is on what they hold — cells, not requests —
with a share of it per caller. A job that does not fit is refused with
how long to wait (`Busy`, a 429), and one too large ever to fit is
refused outright (`TooLarge`, a 413)."""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

from . import telemetry

BYTES_PER_CELL = 8  # the reads wait as float64


def _mb(name: str, default: int) -> int:
    named = os.environ.get(name, "").strip()
    return int(named) if named.isdigit() and int(named) > 0 else default


class Busy(Exception):
    """The queue is full for this caller: come back in `retry_after` seconds."""

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


class TooLarge(Exception):
    """A request larger than the queue will ever hold."""


@dataclass
class _Job:
    caller: str
    kind: str  # the job's name: "forecast", "misfit"
    cells: int
    future: Future
    run: Callable[[Any], Any]  # the kernel in, the answer out


class Owner:
    def __init__(self, kernels_get: Callable[[], Any], queue_mb: int | None = None, caller_share: float = 0.5):
        self._kernels_get = kernels_get
        # What may wait, in cells.
        self.max_cells = (queue_mb or _mb("GLOSSTHESI_QUEUE_MB", 512)) * 1024 * 1024 // BYTES_PER_CELL
        self.caller_share = caller_share
        self._waiting: OrderedDict[str, deque[_Job]] = OrderedDict()  # caller -> its jobs, oldest first
        self._cells = 0
        self._cells_of: dict[str, int] = {}
        self._rate = 0.0  # cells answered per second, smoothed
        self._wake = threading.Condition()
        self.stats = {"cycles": 0, "jobs": 0, "refused_busy": 0, "failed": 0, "last_cycle_at": None}
        self._thread = threading.Thread(target=self._loop, daemon=True, name="gpu-owner")
        self._thread.start()

    # -- the callers' side ---------------------------------------------------

    def exclusive(self, caller: str, name: str, cells: int, run: Callable[[Any], Any]) -> Future:
        """A job that has the device to itself for its turn: `run` takes
        the kernel and returns the answer, or raises."""
        job = _Job(caller, name, cells, Future(), run)
        with self._wake:
            if job.cells > self.max_cells * self.caller_share:
                raise TooLarge(
                    f"the request holds {job.cells * BYTES_PER_CELL >> 20} MB and one caller's share of the "
                    f"queue is {int(self.max_cells * self.caller_share) * BYTES_PER_CELL >> 20} MB — send it in parts"
                )
            mine = self._cells_of.get(job.caller, 0)
            # A caller alone may fill its share; the queue as a whole is never overfilled.
            if self._cells + job.cells > self.max_cells or mine + job.cells > self.max_cells * self.caller_share:
                self.stats["refused_busy"] += 1
                wait = (self._cells / self._rate) if self._rate > 0 else 1.0
                raise Busy("the service's queue is full — retry shortly", retry_after=max(1, min(60, int(wait) + 1)))
            self._waiting.setdefault(job.caller, deque()).append(job)
            self._cells += job.cells
            self._cells_of[job.caller] = mine + job.cells
            self._wake.notify()
        return job.future

    # -- the owner's side ----------------------------------------------------

    def _take(self) -> _Job:
        """The next job: the oldest of the caller longest unserved; that
        caller then goes to the back."""
        caller, jobs = next(iter(self._waiting.items()))
        job = jobs.popleft()
        if jobs:
            self._waiting.move_to_end(caller)
        else:
            del self._waiting[caller]
        self._cells -= job.cells
        self._cells_of[caller] -= job.cells
        if not self._cells_of[caller]:
            del self._cells_of[caller]
        return job

    def _loop(self) -> None:
        while True:
            with self._wake:
                while not self._waiting:
                    self._wake.wait()
                job = self._take()
            if not job.future.set_running_or_notify_cancel():
                continue  # cancelled while it waited: nothing to run, nothing to count
            started = time.perf_counter()
            failed = False
            try:
                with telemetry.span("owner.cycle", kind=job.kind, jobs=1):
                    job.future.set_result(job.run(self._kernels_get()))
            except BaseException as e:  # noqa: BLE001 — the owner must outlive any one job
                failed = True
                if not job.future.done():
                    job.future.set_exception(e)
                self.stats["failed"] += 1
                telemetry.failed(job.kind, 1, e)
                del e  # its traceback holds the pass's tensors
                self._recover()
            seconds = time.perf_counter() - started
            if not failed:  # a failed job says nothing about the rate
                rate = job.cells / max(seconds, 1e-6)
                self._rate = rate if self._rate == 0 else 0.8 * self._rate + 0.2 * rate
            self.stats["cycles"] += 1
            self.stats["jobs"] += 1
            self.stats["last_cycle_at"] = time.time()
            telemetry.cycle(jobs=1, reads=1, cells=job.cells, callers=1, kind=job.kind, seconds=seconds, waiting_cells=self._cells)

    def _recover(self) -> None:
        try:
            recover = getattr(self._kernels_get(), "recover", None)
            if recover is not None:
                recover()
        except Exception as e:  # noqa: BLE001 — recovery failing is logged, never fatal
            telemetry.log.error("recover_failed", extra={"error": f"{type(e).__name__}: {e}"})
