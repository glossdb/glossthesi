"""Timings at the reads' real shapes, on whatever device this process
has: what one call costs. The bench for any host with a GPU that runs
the container:

    python -m glossthesi.measure                      # the reads' timings

The forecast and the walk are Chronos-2 through `forecast.py` at the
shapes glossql sends: the metrics of a workspace as one request, six
walked periods each; a hundred series a year ahead."""

from __future__ import annotations

import json
import time

import numpy as np
import torch

from .forecast import Series
from .kernels import Kernels

LEVELS = [0.05, 0.10, 0.50, 0.90, 0.95]


def _sync(k: Kernels) -> None:
    if k.device == "cuda":
        torch.cuda.synchronize()


def run(use_amp: bool = False) -> dict:
    t0 = time.perf_counter()
    k = Kernels(use_amp=use_amp)
    k.load_forecaster()
    out = {
        "device": k.device,
        "gpu": torch.cuda.get_device_name(0) if k.device == "cuda" else None,
        "amp": use_amp,
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
    return out


def main(argv: list[str] | None = None) -> None:
    import argparse
    import os

    ap = argparse.ArgumentParser(prog="python -m glossthesi.measure", description=__doc__.split("\n\n")[0])
    ap.add_argument("--amp", action="store_true", help="reduced precision (autocast)")
    ap.add_argument("--flushing-probe", action="store_true", help="keep the package's own memory probe")
    args = ap.parse_args(argv)
    os.environ["GLOSSTHESI_FLUSHING_PROBE"] = "1" if args.flushing_probe else ""
    print(json.dumps(run(use_amp=args.amp), indent=1))


if __name__ == "__main__":
    main()
