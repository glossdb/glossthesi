"""The models, loaded once: TabICL's regressor and classifier on one
device for the what-if, and Chronos-2 for the forecasts
(`forecast.py`).

- `series`: TabICL's forecaster as its package ships it, for the
  what-if (`whatif.py`): a series' history and the periods to predict
  with their inputs in, quantiles per period out.

The checkpoints load once and are shared with the forecaster.
"""

from __future__ import annotations

import os
import threading
import warnings

import numpy as np
import torch
from huggingface_hub import hf_hub_download
from tabicl import TabICLClassifier, TabICLRegressor

from . import forecast

HUB_REPO = "jingang/TabICL"
REGRESSOR = "tabicl-regressor-v2-20260212.ckpt"
CLASSIFIER = "tabicl-classifier-v2-20260212.ckpt"
# What the service serves, by checkpoint, on /healthz: the TabICL files are dated in their names, Chronos-2 by its commit.
CHECKPOINTS = {HUB_REPO: [REGRESSOR, CLASSIFIER], forecast.MODEL: forecast.REVISION}


class KernelError(Exception):
    """A read the model refuses, with the reason the door reports."""


def fetch_checkpoints() -> None:
    """Pull the TabICL checkpoints and Chronos-2 into the hub cache — the image bakes them."""
    hf_hub_download(repo_id=HUB_REPO, filename=REGRESSOR)
    hf_hub_download(repo_id=HUB_REPO, filename=CLASSIFIER)
    forecast.fetch_checkpoints()


def pick_device() -> str:
    """`GLOSSTHESI_DEVICE`, else cuda, else mps, else cpu."""
    named = os.environ.get("GLOSSTHESI_DEVICE", "").strip()
    if named:
        return named
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _free_without_flushing(manager) -> float:
    """What the package's probe answers, without what it does to get it.

    Before every forward pass (three times a pass: columns, rows, the
    ICL stack) the package sizes its batches from free device memory, and
    to read that it synchronizes the device and empties PyTorch's
    allocator cache — so every pass waits for the device, then buys its
    memory back from the driver. For a small table that is most of the
    pass. The same number is there without the flush: what the driver
    has free, plus what the allocator holds and is not using."""
    device = manager.exe_device
    free, _total = torch.cuda.mem_get_info(device)
    idle = torch.cuda.memory_reserved(device) - torch.cuda.memory_allocated(device)
    return (free + idle) / (1024 * 1024)


def _probe_memory_cheaply() -> None:
    from tabicl._model.inference import InferenceManager

    flushing = InferenceManager.get_available_gpu_memory

    def available(self) -> float:
        if getattr(self.exe_device, "type", None) == "cuda":
            return _free_without_flushing(self)
        return flushing(self)

    InferenceManager.get_available_gpu_memory = available


if os.environ.get("GLOSSTHESI_FLUSHING_PROBE", "") != "1":  # the package's own, for a comparison
    _probe_memory_cheaply()


class Kernels:
    def __init__(self, device: str | None = None, use_amp: bool = False):
        self.device = device or pick_device()
        self.use_amp = use_amp
        offline = os.environ.get("HF_HUB_OFFLINE", "") == "1"
        loader = TabICLRegressor(device=self.device, allow_auto_download=not offline)
        loader._resolve_device()
        loader._load_model()
        loader.model_.to(loader.device_)
        self.model = loader.model_
        self._regressor = (loader.model_path_, loader.model_config_)
        self._forecaster = None  # TabICL's forecaster, built on first use (`series`)
        classes = TabICLClassifier(device=self.device, allow_auto_download=not offline)
        classes._resolve_device()
        classes._load_model()
        classes.model_.to(classes.device_)
        self.classifier = classes.model_
        self.chronos = forecast.Chronos2(self.device)  # loads on first use, or at start (`load_forecaster`)

    def load_forecaster(self) -> None:
        """Chronos-2's weights on the device now, so a start pays all of
        its load at once and the port opening means the whole service."""
        self.chronos.pipeline()

    def series(self, jobs: list, levels: list[float]) -> list[np.ndarray | Exception]:
        """TabICL's forecaster on every job (`whatif.Job`): per job the
        quantiles at `levels` of every period asked, (periods, levels),
        or the exception that job raised. The package's own pipeline,
        its time features and its regressor's defaults; the weights
        are the ones this service holds, on its device, the series in
        turn."""
        import pandas as pd

        forecaster = self._tabicl_forecaster()

        def frames(which: list[int]):
            past = pd.concat([pd.DataFrame({"item_id": str(i), "timestamp": jobs[i].stamps, "target": jobs[i].y, **jobs[i].inputs}) for i in which])
            ahead = pd.concat([pd.DataFrame({"item_id": str(i), "timestamp": jobs[i].ahead, **jobs[i].future}) for i in which])
            return past, ahead

        def read(which: list[int]) -> dict[int, np.ndarray]:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                got = forecaster.predict_df(*frames(which), quantiles=levels).reset_index()
            out = {}
            for i in which:
                mine = got[got["item_id"] == str(i)].sort_values("timestamp")
                out[i] = np.sort(mine[levels].to_numpy(dtype=np.float64), axis=-1)
            return out

        everyone = list(range(len(jobs)))
        try:
            done: dict[int, np.ndarray | Exception] = dict(read(everyone))
        except Exception as e:  # one series the pipeline refuses fails the lot: read them one by one, so that it fails alone
            if forecast.out_of_memory(e):
                raise  # the device's memory, not a series: the owner's failure
            done = {}
            for i in everyone:
                try:
                    done.update(read([i]))
                except Exception as e:
                    if forecast.out_of_memory(e):
                        raise
                    done[i] = e
        return [done[i] for i in everyone]

    def _tabicl_forecaster(self):
        if self._forecaster is None:
            try:
                import pandas as pd
                from tabicl.forecast import TabICLForecaster, TimeSeriesDataFrame
                from tabicl.forecast._dispatch import SeriesDispatcher
            except ImportError as e:
                raise KernelError(
                    "the what-if needs TabICL's forecaster, and this build does not carry its packages: install `tabicl[forecast]` (pandas, statsmodels, matplotlib)"
                ) from e
            kernel = self

            def infer(train_x, train_y, test_x, quantiles=None) -> dict:
                """One series: the package's regressor with its defaults, over the weights already on the device."""
                model = TabICLRegressor(device=kernel.device)

                def loaded() -> None:
                    model.model_path_, model.model_config_ = kernel._regressor
                    model.model_ = kernel.model

                model._load_model = loaded
                model.fit(np.asarray(train_x), np.asarray(train_y))
                raw = model.predict(np.asarray(test_x), output_type=["median", "quantiles"], alphas=quantiles)
                return {"target": raw["median"], **{q: raw["quantiles"][:, i] for i, q in enumerate(quantiles)}}

            class InTurn(SeriesDispatcher):
                """The package spreads series over worker processes, each loading the weights again; here they are read in turn on the one device."""

                def run(self, train_tsdf, test_tsdf, **kwargs):
                    got = pd.concat([self._process_single(item, train_tsdf.loc[item], test_tsdf.loc[item], **kwargs) for item in train_tsdf.item_ids])
                    return TimeSeriesDataFrame(got.loc[train_tsdf.item_ids])

            forecaster = TabICLForecaster(point_estimate="median")
            forecaster.predictor._dispatcher = InTurn(inference_fn=infer, num_workers=1)
            self._forecaster = forecaster
        return self._forecaster

    def recover(self) -> None:
        """After a failed pass: hand the allocator's reserved memory back,
        so the next cycle does not start in the failed one's fragments."""
        if self.device == "cuda":
            torch.cuda.empty_cache()
        elif self.device == "mps":
            torch.mps.empty_cache()


# -- the process's one instance ---------------------------------------------

_LOCK = threading.Lock()
_KERNELS: Kernels | None = None


def get() -> Kernels:
    global _KERNELS
    with _LOCK:
        if _KERNELS is None:
            _KERNELS = Kernels()
        return _KERNELS


def peek() -> Kernels | None:
    return _KERNELS
