"""Timings at the reads' real shapes, on whatever device this process
has: what one call costs. The bench for any host with a GPU that runs
the container:

    python -m glossthesi.measure                      # the reads' timings
    python -m glossthesi.measure --parity fixtures/   # the density oracle on this device

The forecast and the walk are Chronos-2 through `forecast.py` at the
shapes glossql sends: the metrics of a workspace as one request, six
walked periods each; a hundred series a year ahead. The misfit read is
a frame at the door's usual cap."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import torch

from .forecast import Series
from .kernels import Kernels, misfit_workers as default_misfit_workers

LEVELS = [0.05, 0.10, 0.50, 0.90, 0.95]


def _sync(k: Kernels) -> None:
    if k.device == "cuda":
        torch.cuda.synchronize()


def run(use_amp: bool = False, misfit_workers: int | None = None) -> dict:
    t0 = time.perf_counter()
    k = Kernels(use_amp=use_amp)
    k.load_forecaster()
    out = {
        "device": k.device,
        "gpu": torch.cuda.get_device_name(0) if k.device == "cuda" else None,
        "amp": use_amp,
        "misfit_workers": misfit_workers or default_misfit_workers(k.device),
        "torch": torch.__version__,
        "load_s": round(time.perf_counter() - t0, 3),
    }
    rng = np.random.default_rng(0)
    cuda = k.device == "cuda"

    def timed(name, fn, n=1, warm=True):
        if warm:
            fn()
        if cuda:
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
        t = time.perf_counter()
        for _ in range(n):
            fn()
        if cuda:
            torch.cuda.synchronize()
        out[name] = {"s_per_call": round((time.perf_counter() - t) / n, 4)}
        if cuda:
            out[name]["peak_mb"] = round(torch.cuda.max_memory_allocated() / 1e6, 1)

    # A hundred series of five years, a year ahead with the quarter's and the year's totals.
    panel = 100.0 * (1 + rng.uniform(0, 5, size=(100, 1))) * (1 + 0.2 * np.sin(np.arange(60) / 12 * 2 * np.pi) + rng.normal(0, 0.05, size=(100, 60)))
    ahead = [Series(panel[i]) for i in range(100)]
    timed("forecast_100x60_to_12", lambda: k.chronos.forecast(ahead, 12, LEVELS, [3, 12]), n=2)
    # The walk of a workspace: a hundred metrics, six origins each, one step ahead — six hundred series of one shape.
    walked = [Series(panel[i, : 54 + j]) for j in range(6) for i in range(100)]
    timed("walk_100x6_origins", lambda: k.chronos.forecast(walked, 1, LEVELS), n=2)
    # A misfit frame at the door's cap.
    mx = rng.normal(size=(2000, 16))
    timed("misfit_2000x16", lambda: k.misfit(mx, workers=misfit_workers), n=1, warm=False)
    timed("misfit_500x8_folds2", lambda: k.misfit(rng.normal(size=(500, 8)), workers=misfit_workers, folds=2), n=1, warm=False)
    return out


def parity(fixtures: Path, use_amp: bool = False) -> dict:
    """The density read against the pinned oracle, as numbers rather
    than assertions, for a device the tests do not run on."""
    k = Kernels(use_amp=use_amp)
    dens = np.load(fixtures / "density_scores.npz")
    logs = k.misfit(dens["x_test"].astype(np.float64), perms=list(dens["perms"]), random_state=42, train=dens["x_train"].astype(np.float64))
    expect = dens["scores"]
    dev = float(np.max(np.abs(np.exp(logs) - expect) / np.maximum(1.0, np.abs(expect))))
    return {"device": k.device, "amp": use_amp, "density": {"max_rel_dev": dev, "finite": bool(np.isfinite(logs).all())}}


def main(argv: list[str] | None = None) -> None:
    import argparse
    import os

    ap = argparse.ArgumentParser(prog="python -m glossthesi.measure", description=__doc__.split("\n\n")[0])
    ap.add_argument("--amp", action="store_true", help="reduced precision (autocast)")
    ap.add_argument("--misfit-workers", type=int, default=0)
    ap.add_argument("--flushing-probe", action="store_true", help="keep the package's own memory probe")
    ap.add_argument("--parity", metavar="FIXTURES", help="the pinned-oracle parity numbers on this device")
    args = ap.parse_args(argv)
    os.environ["GLOSSTHESI_FLUSHING_PROBE"] = "1" if args.flushing_probe else ""
    if args.parity:
        print(json.dumps(parity(Path(args.parity), use_amp=args.amp), indent=1))
    else:
        print(json.dumps(run(use_amp=args.amp, misfit_workers=args.misfit_workers or None), indent=1))


if __name__ == "__main__":
    main()
