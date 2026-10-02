"""The owner's queue: jobs answered one at a time, callers in turn, and
refused before memory is."""

import threading
import time

import pytest

from glossthesi.owner import Busy, Owner, TooLarge


class Gated:
    """A kernel that holds each job until told, and records the order."""

    def __init__(self):
        self.gate = threading.Event()
        self.entered = threading.Event()
        self.served: list[float] = []


def held(kernel: Gated, **kw) -> Owner:
    """An owner already inside a job, so what is submitted next waits."""
    own = Owner(lambda: kernel, **kw)

    def first(k):
        k.entered.set()
        k.gate.wait(5)
        return "warm"

    own.exclusive("warm", "forecast", 1, first)
    assert kernel.entered.wait(5)
    return own


def job(tag: float):
    def run(k):
        k.served.append(tag)
        return tag

    return run


def test_callers_are_taken_in_turn_and_hear_their_own_answers():
    kernel = Gated()
    own = held(kernel)
    flood = [own.exclusive("a", "forecast", 10, job(10.0 + i)) for i in range(3)]
    late = own.exclusive("b", "misfit", 10, job(99.0))
    kernel.gate.set()
    assert [f.result(5) for f in flood] == [10.0, 11.0, 12.0] and late.result(5) == 99.0
    # The late caller is served after the flood's first job, not behind the flood.
    assert kernel.served == [10.0, 99.0, 11.0, 12.0]
    assert own.stats["jobs"] == 5 and own.stats["cycles"] == 5  # the warm job counts


def test_the_queue_pushes_back_before_memory_does():
    kernel = Gated()
    own = held(kernel, queue_mb=1)  # 131072 cells in all, half of it a caller's share
    big = 30_000
    own.exclusive("a", "forecast", big, job(1.0))
    own.exclusive("a", "forecast", big, job(2.0))
    with pytest.raises(Busy) as busy:  # a third would pass a's share
        own.exclusive("a", "forecast", big, job(3.0))
    assert busy.value.retry_after >= 1
    own.exclusive("b", "forecast", big, job(4.0))  # another caller still gets in
    with pytest.raises(TooLarge, match="in parts"):
        own.exclusive("b", "forecast", 80_000, job(5.0))
    assert own.stats["refused_busy"] == 1
    kernel.gate.set()


def test_a_failed_job_fails_its_own_caller_recovers_the_device_and_leaves_the_rate_alone():
    class Dying:
        def __init__(self):
            self.recovered = 0

        def recover(self):
            self.recovered += 1

    kernel = Dying()
    own = Owner(lambda: kernel)

    def die(_k):
        raise RuntimeError("CUDA out of memory")

    with pytest.raises(RuntimeError, match="out of memory"):
        own.exclusive("a", "forecast", 10, die).result(5)
    # The caller hears first; the recovery follows once the traceback is dropped.
    for _ in range(100):
        if kernel.recovered:
            break
        time.sleep(0.01)
    assert kernel.recovered == 1 and own.stats["failed"] == 1 and own._rate == 0.0
    assert own.exclusive("a", "forecast", 10, lambda _k: "fine").result(5) == "fine"
    assert own.stats["cycles"] == 2 and own._rate > 0
