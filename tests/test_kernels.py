"""The kernels on the device: TabICL's forecaster over the weights
loaded once."""

import numpy as np
import pytest

from glossthesi.kernels import Kernels


@pytest.fixture(scope="module")
def k():
    return Kernels()


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
