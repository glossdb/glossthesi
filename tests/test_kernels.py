"""The misfit read against the pinned density oracle (fixtures/, copied
from the port repo — see fixtures/SOURCE): the same package produced
it, so the read here reproduces it up to device drift (it was
generated on CPU)."""

from pathlib import Path

import numpy as np
import pytest

from glossthesi.kernels import KernelError, Kernels

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
pytestmark = pytest.mark.skipif(not FIXTURES.is_dir(), reason="no fixtures/ in the repo")


@pytest.fixture(scope="module")
def k():
    return Kernels()


def _tol(k):
    # CPU reproduces the CPU oracle to float noise; an accelerator drifts
    # in the fourth decimal on standardized values.
    return (1e-5, 1e-6) if k.device == "cpu" else (2e-3, 1e-3)


def test_misfit_reproduces_the_density_oracle(k):
    d = np.load(FIXTURES / "density_scores.npz")
    # The fixture recorded the package's own orderings; the read takes them
    # as given and defaults to identity and reverse otherwise.
    logs = k.misfit(d["x_test"].astype(np.float64), perms=list(d["perms"]), random_state=42, train=d["x_train"].astype(np.float64))
    # A score is the product of 2 x cols conditional densities, so the
    # estimators' float noise compounds: a CPU with another BLAS than the
    # oracle's (x86 oneDNN against arm64 Accelerate) lands 1e-3 off in
    # relative terms; an accelerator further.
    rtol, atol = _tol(k)
    rtol = max(rtol * 10, 5e-3)
    assert np.allclose(np.exp(logs), d["scores"], rtol=rtol, atol=atol), f"{np.exp(logs)} vs {d['scores']}"
    # The read the door consumes: one finite log density per row.
    assert logs.shape == (d["x_test"].shape[0],) and np.isfinite(logs).all()


def test_self_fit_misfit_ranks_an_outlier_last(k):
    rng = np.random.default_rng(3)
    x = rng.normal(size=(60, 4))
    x[:, 1] = 0.8 * x[:, 0] + 0.2 * x[:, 1]
    x[7] = [6.0, -6.0, 6.0, -6.0]
    logs = k.misfit(x)
    assert int(np.argmin(logs)) == 7


def test_misfit_columns_sum_to_the_score_and_name_the_cell(k):
    rng = np.random.default_rng(3)
    x = rng.normal(size=(60, 4))
    x[:, 1] = 0.8 * x[:, 0] + 0.2 * x[:, 1]
    x[7, 2] = 9.0  # one cell off; the rest of the row ordinary
    logs, shares = k.misfit(x, columns=True)
    assert shares.shape == x.shape
    assert np.array_equal(logs, k.misfit(x))  # asking per column changes no score
    assert np.allclose(shares.sum(axis=1), logs, rtol=1e-6, atol=1e-6)
    assert int(np.argmin(shares[7])) == 2
    # Scored from a context it is not in, the cell is the frame's least likely.
    held, held_shares = k.misfit(x, columns=True, folds=2)
    assert np.allclose(held_shares.sum(axis=1), held, rtol=1e-6, atol=1e-6)
    assert int(np.argmin(held)) == 7
    assert np.unravel_index(np.argmin(held_shares), held_shares.shape) == (7, 2)
    with pytest.raises(KernelError, match="folds"):
        k.misfit(x[:3], folds=2)


def test_misfit_scores_a_category_by_its_probability_given_the_rest(k):
    # A tax code names the rate; a code that does not match the tax is found through the classifier's conditional.
    rng = np.random.default_rng(0)
    n = 120
    amount = np.round(np.exp(rng.normal(3, 0.7, n)), 2)
    code = rng.integers(0, 3, n)
    tax = np.round(amount * np.array([0.19, 0.07, 0.0])[code], 2)
    x = np.column_stack([amount, code, tax]).astype(np.float64)
    x[5, 1] = (x[5, 1] + 1) % 3
    logs, shares = k.misfit(x, columns=True, categorical=[1])
    assert np.isfinite(logs).all() and shares.shape == x.shape and np.allclose(shares.sum(axis=1), logs, atol=1e-6)
    assert int(np.argmin(logs)) == 5
    assert (shares[:, 1] <= 1e-6).all()  # a log probability, never above zero
    # A categorical column with one category says nothing and is skipped: its share is NaN.
    x[:, 1] = 0
    _, shares = k.misfit(x, columns=True, categorical=[1])
    assert np.isnan(shares[:, 1]).all()


def test_strangeness_reads_each_cell_against_its_own_column():
    from glossthesi.kernels import strangeness

    shares = np.array([[-10.0, -0.1], [-9.0, -0.1], [-11.0, -0.1], [-30.0, -0.1], [-10.0, -3.0], [np.nan, -0.1]])
    scores, cells = strangeness(shares)
    # Column 0 swings by a nat and more between ordinary rows: read in units of its spread. Column 1 is tight: read in nats.
    assert cells[3, 0] < cells[4, 1] < -2.0 and abs(cells[0, 0]) < 1.0 and cells[0, 1] == pytest.approx(0.0)
    assert int(np.argmin(scores)) == 3 and np.argmin(cells[4]) == 1
    assert np.isnan(cells[5, 0]) and scores[5] == pytest.approx(0.0)
    scores, cells = strangeness(np.full((3, 2), np.nan))
    assert np.isnan(scores).all() and np.isnan(cells).all()


def test_the_forecaster_reads_each_series_in_turn_and_moves_with_an_input(k):
    # TabICL's forecaster as its package ships it, over the weights already loaded (the what-if's model).
    pytest.importorskip("statsmodels")
    from glossthesi import whatif

    rng = np.random.default_rng(0)
    stamps = (np.datetime64("2023-01-02") + np.arange(84) * np.timedelta64(7, "D")).astype("datetime64[ms]")
    x = (rng.uniform(size=80) < 0.5).astype(np.float64)
    y = 50.0 + 5.0 * x + rng.normal(0, 0.3, 80)
    off, on = (whatif.Job(stamps[:80], y, {"x": x}, stamps[80:], {"x": np.full(4, v)}) for v in (0.0, 1.0))
    empty = whatif.Job(stamps[:0], y[:0], {"x": x[:0]}, stamps[80:], {"x": np.zeros(4)})  # no history at all
    got = k.series([off, on, empty], [0.1, 0.5, 0.9])
    assert got[0].shape == got[1].shape == (4, 3) and (np.diff(got[0], axis=1) >= 0).all()
    # The model has seen y five higher wherever x was on, and predicts it so.
    assert 3.5 < (got[1][:, 1] - got[0][:, 1]).mean() < 6.5
    # A series the pipeline refuses fails alone.
    assert isinstance(got[2], Exception)
