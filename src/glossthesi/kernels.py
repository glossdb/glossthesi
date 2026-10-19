"""The models, loaded once: TabICL's regressor and classifier on one
device for the misfit read and the what-if, and Chronos-2 for the
forecasts (`forecast.py`).

- `misfit`: the chain-rule density over one frame, fit and scored on
  the same rows, log space, mean over permutations — higher fits the
  frame better. A numeric column's conditional is the regressor's
  density, a categorical column's the classifier's probability of the
  category seen.

- `series`: TabICL's forecaster as its package ships it, for the
  what-if (`whatif.py`): a series' history and the periods to predict
  with their inputs in, quantiles per period out.

The checkpoints load once and are shared with the density read the
way `TabICLUnsupervised` shares its own, and with the forecaster.
"""

from __future__ import annotations

import os
import threading
import warnings
from typing import Any

import numpy as np
import torch
from huggingface_hub import hf_hub_download
from tabicl import TabICLClassifier, TabICLRegressor, TabICLUnsupervised

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


class _Unsupervised(TabICLUnsupervised):
    """The density read over the shared regressor and classifier."""

    def __init__(self, regressor: torch.nn.Module, classifier: torch.nn.Module, **kwargs: Any):
        super().__init__(**kwargs)
        self._regressor, self._classifier = regressor, classifier

    def _load_shared_model(self, estimator_cls):
        return self._classifier if estimator_cls is TabICLClassifier else self._regressor


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

    def misfit(
        self,
        x: np.ndarray,
        *,
        perms: list[np.ndarray] | None = None,
        random_state: int = 0,
        train: np.ndarray | None = None,
        workers: int | None = None,
        columns: bool = False,
        folds: int = 1,
        categorical: list[int] | None = None,
    ) -> np.ndarray | tuple[np.ndarray, np.ndarray]:
        """Mean log density per row over the orderings in `perms`, fit on
        `train` (default: the frame itself). Log space end to end — the
        package's `score_samples` exponentiates the same mean.

        The columns in `categorical` hold category codes (whole numbers
        from 0, NaN where missing); their conditional is the classifier's
        probability of the code seen, the other columns' the regressor's
        density. A categorical column with one category in the context
        says nothing and is skipped, as a column with too few values is.

        With `columns`, also (rows, cols): each column's share of that
        score — its conditional log density, averaged over the orderings
        as the score is — so a row's shares sum to its score and the
        lowest one names the cell that made the row improbable. A column
        with too few values to fit is NaN, and adds nothing to the score.

        With `folds` over 1 no row is in the context that scores it: the
        frame is split, and each part is scored from the rest. A frame
        that scores itself lets every row find itself in the context, which
        hides a value that is ordinary for its column and wrong for its
        row (planted sign flips: their cells ranked 9-344 of 1200 lowest
        self-fit, 4-77 over two folds); a value far outside its column is
        named either way. The cost is that many times the fits.

        The orderings default to the port's protocol: identity and
        reverse, so every column conditions both early and late and the
        read costs two conditionals per column. What a column is read
        against depends on its place among the columns, so the order the
        caller sends them in is part of the read: the rows' scores
        correlate 0.53 to 0.90 between column orders and the cell named
        changes on a quarter of the planted rows (GK-67). Read in the
        order of their names instead, the tables lose (a swapped amount
        0.99 to 0.97, the SAP order items 0.98 to 0.94): the order a
        caller writes a table in, the columns a value is computed from
        before it, is what the chain rule reads, and the contract says
        so. Three order-free reads were judged against this one and none
        won across the frames (GK-68): each column against all the others
        only (better on categories and the bank's transactions, worse on
        a row far out but consistent with itself, on a wrong unit and on
        the company's payments), that read and the column alone
        averaged, and the lower of the two. A category the context never
        held gets the package's floor of `UNSEEN`, lifted to one in n + 1
        of the conditional's training rows: with `folds`, a category that
        appears in the scored fold only is unseen by the classifier that
        scores it, and at 1e-10 (minus 23 nats) it named the row's
        strangest cell whether or not the row was wrong (43 of the 100
        strangest SAP order items clean rows so named; 24 with the lift,
        GK-69). Not the package's own
        `score_samples`, whose latin shuffler ignores the count it is
        asked for and returns one ordering per column — sixteen on a
        sixteen-column frame, eight times the work.

        The chain rule is one conditional per column per ordering, each
        independent given the frame: the same fits the package runs one
        after another (`_compute_log_density`), dispatched here over
        `workers` threads so the accelerator is not idle while the next
        conditional is prepared. Every conditional is computed exactly as
        in the sequential loop, and the sums are taken in the loop's
        order, so the numbers do not depend on the schedule. The one
        stateful thing in that loop is the dummy column for the first
        conditional of each ordering, drawn from one random stream in
        loop order; it is drawn here in that order before dispatch.
        """
        rows, cols = x.shape
        if rows < 2 or cols < 2:
            raise KernelError(f"misfit: {rows} rows x {cols} features — the read needs two of each")
        if folds > 1:
            if train is not None or rows < 2 * folds:
                raise KernelError(f"misfit: {folds} folds split one frame, of at least {2 * folds} rows")
            order = np.random.default_rng(random_state).permutation(rows)
            scores, shares = np.empty(rows), np.empty((rows, cols))
            for f in range(folds):
                out = np.sort(order[f::folds])
                rest = np.setdiff1d(order, out)
                scores[out], shares[out] = self.misfit(
                    x[out], perms=perms, random_state=random_state, train=x[rest], workers=workers, columns=True, categorical=categorical
                )
            return (scores, shares) if columns else scores
        u = _Unsupervised(
            self.model,
            self.classifier,
            n_estimators=1,
            categorical_features=sorted(set(categorical or [])),
            random_state=random_state,
            device=self.device,
            estimator_params={
                "norm_methods": "none",
                "feat_shuffle_method": "none",
                "use_amp": self.use_amp,
            },
        )
        try:
            u.fit(x if train is None else train)
        except Exception as e:
            raise KernelError(f"misfit: {e}") from e
        xf = x.astype(np.float32)
        rng = np.random.default_rng(random_state)
        if perms is None:
            perms = [np.arange(cols), np.arange(cols)[::-1]]
        perms = [np.asarray(p, dtype=np.int64) for p in perms]
        n_permutations = len(perms)

        # (ordering, column, conditioning columns, train mask, noise) —
        # in the package's loop order, the noise drawn where it draws it.
        tasks = []
        for pi, perm in enumerate(perms):
            for i, col in enumerate(perm):
                col = int(col)
                train_mask = ~np.isnan(u.X_[:, col])
                if train_mask.sum() < u._MIN_SAMPLES_PER_CONDITIONAL:
                    continue
                if col in u.categorical_features_ and u.categories_[col].size < 2:
                    continue
                cond = [int(c) for c in perm[:i]]
                noise = None
                if not cond:
                    noise = (
                        rng.standard_normal((int(train_mask.sum()), 1)).astype(np.float32),
                        rng.standard_normal((rows, 1)).astype(np.float32),
                    )
                tasks.append((pi, col, cond, train_mask, noise))

        def conditional(task):
            pi, col, cond, train_mask, noise = task
            if noise is None:
                x_train, x_test = u.X_[train_mask][:, cond], xf[:, cond]
            else:
                x_train, x_test = noise
            y_train = u.X_[train_mask, col]
            est, is_categorical = u._fit_conditional_estimator(col, x_train, y_train)
            if not is_categorical:
                return pi, col, u._log_prob_numerical(est, x_test, xf[:, col])
            lp = u._log_prob_categorical(est, x_test, xf[:, col])
            # The package's floor for a category the context never held, lifted to one in n + 1 of the training rows.
            return pi, col, np.where(lp <= np.log(UNSEEN) + 1e-6, np.log(1.0 / (int(train_mask.sum()) + 1)), lp)

        workers = workers or misfit_workers(self.device)
        try:
            if workers <= 1:
                results = [conditional(t) for t in tasks]
            else:
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(max_workers=workers) as pool:
                    results = list(pool.map(conditional, tasks))
        except Exception as e:
            raise KernelError(f"misfit: {e}") from e
        sums = np.zeros((n_permutations, rows))
        shares = np.full((n_permutations, rows, cols), np.nan)
        for pi, col, lp in results:  # task order — the loop's summation order
            sums[pi] += lp
            shares[pi, :, col] = lp
        scores = np.mean(sums, axis=0)
        return (scores, np.mean(shares, axis=0)) if columns else scores


SPREAD_FLOOR = 1.0  # nats: a column's spread is never taken finer than this
UNSEEN = 1e-10  # the package's probability for a category the context never held; lifted to 1/(n + 1) in `misfit` (GK-69)


def floor_assumption() -> dict:
    """What the served score rests on, for the answer object: the floor
    on a column's spread and where it comes from."""
    return {
        "field": "score",
        "floor_nats": SPREAD_FLOOR,
        "basis": "a constant: a cell is read in its column's spread, never finer than one nat; chosen among 0.5, 1 and 2 on development tables, 1 and 2 reading alike and 0.5 worse (GK-38)",
    }


def strangeness(shares: np.ndarray, floor: float = SPREAD_FLOOR) -> tuple[np.ndarray, np.ndarray]:
    """From the per-cell log densities of a frame (rows, cols), what is
    served: per cell how far it lies below what is typical for its
    column — the share minus the column's median, in units of the
    column's spread (1.4826 times the median absolute deviation) where
    that exceeds `floor`, else in nats — and per row the lowest of its
    cells. A density's level depends on the column's scale and a
    probability's does not, so the shares of different columns are
    not compared as they come; centered they are, and a column whose
    shares swing by more than a nat between ordinary rows is read in
    units of that swing. Lower is stranger; around zero and above is
    ordinary. A cell without a share is NaN and never the row's
    lowest; a row without any share is NaN."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a column without a share has no median
        median = np.nanmedian(shares, axis=0)
        spread = 1.4826 * np.nanmedian(np.abs(shares - median), axis=0)
        cells = (shares - median) / np.maximum(np.nan_to_num(spread, nan=0.0), floor)
        scores = np.nanmin(cells, axis=1)
    return scores, cells


def misfit_workers(device: str) -> int:
    """`GLOSSTHESI_MISFIT_WORKERS`, else four on CUDA and one elsewhere
    (Metal runs one queue, and the CPU is the bottleneck on the CPU)."""
    named = os.environ.get("GLOSSTHESI_MISFIT_WORKERS", "").strip()
    if named.isdigit() and int(named) > 0:
        return int(named)
    return 4 if device == "cuda" else 1


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
