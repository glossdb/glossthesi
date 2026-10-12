"""The wire, without the models: the two encodings, the table's
shape, the specification's fields, errors in place, refusals."""

import asyncio
import io
import json
import os
import threading
import time

import httpx
import numpy as np
import pyarrow as pa
import pytest

from glossthesi import app as app_module
from glossthesi import forecast, kernels, wire


class FakeChronos(forecast.Chronos2):
    """The forecaster with a model that continues every column of a
    series at its last value plus the level asked, and adds a known
    covariate's future."""

    def __init__(self):
        super().__init__("cpu")
        self.groups = []

    def pipeline(self):
        return self

    def predict_quantiles(self, inputs, prediction_length, quantile_levels, batch_size, cross_learning):
        self.groups.append((len(inputs), batch_size, cross_learning))
        self.kinds = [{name: v.dtype.kind for name, v in one["past_covariates"].items()} for one in inputs]
        out = []
        for one in inputs:
            if (one["target"] < 0).any():
                raise ValueError("a value under zero")
            numbers = [v for v in one["future_covariates"].values() if v.dtype.kind == "f"]
            ahead = sum(numbers) if numbers else np.zeros(prediction_length)
            last = np.atleast_2d(one["target"])[:, -1]  # (columns,)
            out.append(last[:, None, None] + ahead[None, :, None] + np.asarray(quantile_levels)[None, None, :])
        return out, None


class Fake:
    device = "fake"

    def __init__(self):
        self.chronos = FakeChronos()

    effect = 2.0  # what the stand-in for TabICL's forecaster adds per unit of the input `x`, on a series whose level lies above 40

    def series(self, jobs, levels):
        """Every period at the history's mean plus `effect` per unit of `x`, the levels spread around it."""
        self.jobs = jobs
        out = []
        for job in jobs:
            if np.nanmean(job.y) < 0:
                out.append(ValueError("a level under zero"))
                continue
            effect = self.effect if np.nanmean(job.y) > 40 else 0.0
            out.append(np.nanmean(job.y) + effect * (job.future["x"] - np.nanmean(job.inputs["x"]))[:, None] + (np.asarray(levels) - 0.5)[None, :])
        return out

    def misfit(self, x, columns=False, folds=1, categorical=None):
        self.table, self.categorical = x, categorical
        scores = np.array([0.1, float("nan")])
        shares = np.array([[0.3, -0.2], [float("nan"), 1.0]] if x.shape[0] == 2 else [[float("nan")] * x.shape[1]] * x.shape[0])
        return (scores, shares) if columns else scores


class _Sync:
    """httpx's ASGI transport is async; the tests read better synchronous."""

    def __init__(self):
        self.transport = httpx.ASGITransport(app=app_module.app)

    def _run(self, method, path, **kw):
        async def go():
            async with httpx.AsyncClient(transport=self.transport, base_url="http://service") as c:
                return await c.request(method, path, **kw)

        return asyncio.run(go())

    def get(self, path, **kw):
        return self._run("GET", path, **kw)

    def post(self, path, **kw):
        return self._run("POST", path, **kw)

    def send(self, path, spec, table, **kw):
        """A request in the JSON encoding."""
        return self.post(path, json={"spec": spec, "table": table}, **kw)

    def arrow(self, path, spec, table, **kw):
        """The same request in the Arrow encoding."""
        t = pa.table({name: pa.array(values) for name, values in table.items()}).replace_schema_metadata({"spec": json.dumps(spec)})
        out = io.BytesIO()
        with pa.ipc.new_stream(out, t.schema) as writer:
            writer.write_table(t)
        return self.post(path, content=out.getvalue(), headers={"content-type": wire.ARROW}, **kw)


@pytest.fixture
def client(monkeypatch):
    fake = Fake()
    monkeypatch.setattr(kernels, "get", lambda: fake)
    monkeypatch.setattr(kernels, "peek", lambda: fake)
    monkeypatch.setattr(app_module, "_OWNER", None)
    monkeypatch.setenv("GLOSSTHESI_INSECURE_OPEN", "true")  # the tests are about the reads; the doors have tests of their own
    return _Sync()


HISTORY = [1, 2, None, 4, 5, 6, 7, 8]
LONG = [1, 2, None, 4, 5, 6, 7, 8, 9, 10, 11, 12]
PERIODS = list(range(1, 13))


def _table(**columns):
    """A table of members with one value column `y` and periods from 1, or the columns given per member."""
    given = {name: series if isinstance(series, dict) else {"y": series} for name, series in columns.items()}
    names = sorted({column for rows in given.values() for column in rows}, key=lambda c: (c != "y", c))
    members, periods, values = [], [], {column: [] for column in names}
    for name, rows in given.items():
        n = len(next(iter(rows.values())))
        members += [name] * n
        periods += PERIODS[:n]
        for column in names:
            values[column] += list(rows.get(column, [None] * n))
    return {"member": members, "period": periods, **values}


def _rows(answer, **where):
    """The answer's rows that match `where`, as dicts."""
    table = answer["table"]
    return [{k: table[k][i] for k in table} for i in range(len(table["member"])) if all(table[k][i] == v for k, v in where.items())]


SPEC = {"targets": ["y"], "horizon": 3, "levels": [0.1, 0.5, 0.9], "totals": [3]}
MISFIT = {"a": [1, 3], "b": [2, 4]}


def test_a_forecast_answers_every_member_with_its_periods_and_its_totals(client):
    r = client.send("/forecast", SPEC, _table(a=HISTORY, b=[3] * 8))
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["answer"]["service"] == "glossthesi" and got["answer"]["version"] == "dev" and got["answer"]["errors"] == []
    a = _rows(got, member="a", span=None)
    assert [row["value"] for row in a] == pytest.approx([8.1, 8.5, 8.9] * 3) and [row["step"] for row in a] == [1, 1, 1, 2, 2, 2, 3, 3, 3]
    assert all(row["origin"] == 8 and row["period"] is None and row["column"] == "y" and row["actual"] is None and row["pit"] is None for row in a)
    assert [row["value"] for row in _rows(got, member="b", span=None)] == pytest.approx([3.1, 3.5, 3.9] * 3)
    # The total's median is the sum of the periods' medians. Its interval is wider than
    # independent periods would give (0.4 x sqrt 3) and narrower than the sum of theirs (0.4 x 3).
    low, median, high = (row["value"] for row in _rows(got, member="a", span=3))
    assert median == pytest.approx(25.5) and high - median == pytest.approx(median - low)
    assert 0.4 * 3**0.5 < high - median < 0.4 * 3
    assert all(row["step"] == 3 for row in _rows(got, member="a", span=3))
    without = client.send("/forecast", SPEC | {"totals": []}, _table(a=HISTORY)).json()
    assert _rows(without, span=3) == [] and "assumptions" not in without["answer"]
    # Weekly periods are taken as less dependent than monthly ones: the total's interval is narrower.
    weekly = client.send("/forecast", SPEC | {"grain": "week"}, _table(a=HISTORY)).json()
    values = [row["value"] for row in _rows(weekly, span=3)]
    assert values[1] == pytest.approx(25.5) and values[2] - values[1] < high - median
    # A total's answer declares the dependence it rests on and where it comes from.
    assert got["answer"]["assumptions"] == [{"field": "totals", "grain": "month", "dependence": forecast.DEPENDENCE["month"], "basis": forecast.dependence("month")["basis"]}]
    assert weekly["answer"]["assumptions"][0]["dependence"] == forecast.DEPENDENCE["week"] and weekly["answer"]["assumptions"][0]["basis"].startswith("measured")
    daily = client.send("/forecast", SPEC | {"grain": "day"}, _table(a=HISTORY)).json()["answer"]["assumptions"][0]
    assert daily["grain"] == "day" and daily["basis"].startswith("assumed")


def test_the_arrow_encoding_answers_the_same_as_json(client):
    table = _table(a=HISTORY, b=[3] * 8)
    r = client.arrow("/forecast", SPEC, table)
    assert r.status_code == 200 and r.headers["content-type"].startswith(wire.ARROW), r.text
    back = pa.ipc.open_stream(r.content).read_all()
    answer = json.loads(back.schema.metadata[b"answer"])
    assert answer == {"service": "glossthesi", "version": "dev", "errors": [], "assumptions": [forecast.dependence("month")]}
    assert back.schema.field("origin").type == pa.int64() and back.schema.field("period").type == pa.int64()
    listed = {name: back.column(name).to_pylist() for name in back.column_names}
    as_json = client.send("/forecast", SPEC, table).json()
    assert listed == as_json["table"] and answer == as_json["answer"]
    # The specification rides in the schema's metadata; without it the stream is refused, and so is a body that is not a stream.
    t = pa.table({"member": ["a"] * 8, "period": PERIODS[:8], "y": HISTORY})
    out = io.BytesIO()
    with pa.ipc.new_stream(out, t.schema) as writer:
        writer.write_table(t)
    r = client.post("/forecast", content=out.getvalue(), headers={"content-type": wire.ARROW})
    assert r.status_code == 400 and "spec" in r.json()["error"]
    r = client.post("/forecast", content=b"not a stream", headers={"content-type": wire.ARROW})
    assert r.status_code == 400 and "Arrow" in r.json()["error"]
    r = client.post("/forecast", content=b"x", headers={"content-type": "text/plain"})
    assert r.status_code == 400 and "content type" in r.json()["error"]


def test_several_targets_are_answered_per_column_and_a_short_one_fails_alone(client):
    spec = SPEC | {"targets": ["sales", "orders"]}
    r = client.send("/forecast", spec, _table(a={"sales": HISTORY, "orders": [3] * 8}, b={"sales": HISTORY, "orders": [None] * 6 + [1, 2]}))
    assert r.status_code == 200, r.text
    got = r.json()
    assert [row["value"] for row in _rows(got, member="a", column="sales", span=None)] == pytest.approx([8.1, 8.5, 8.9] * 3)
    assert [row["value"] for row in _rows(got, member="a", column="orders", span=3)][1] == pytest.approx(10.5)
    assert [row["value"] for row in _rows(got, member="b", column="sales", span=None)] == pytest.approx([8.1, 8.5, 8.9] * 3)
    assert _rows(got, member="b", column="orders") == []
    assert got["answer"]["errors"] == [{"member": "b", "origin": 8, "column": "orders", "error": "2 known values of column 1 of the history: a forecast needs 6"}]
    assert kernels.get().chronos.groups == [(2, forecast.GROUP, True)]  # both members have two columns: one group
    # No column with enough history fails the member.
    r = client.send("/forecast", spec | {"horizon": 2, "totals": []}, _table(a={"sales": [None] * 8, "orders": [None] * 8}))
    assert r.status_code == 200 and r.json()["table"]["member"] == [] and "no column has 6 known values" in r.json()["answer"]["errors"][0]["error"]


def test_a_width_stretches_the_intervals_around_the_median(client):
    raw = _rows(client.send("/forecast", SPEC, _table(a=HISTORY)).json(), member="a")
    wide = _rows(client.send("/forecast", SPEC | {"width": 2}, _table(a=HISTORY)).json(), member="a")
    narrow = _rows(client.send("/forecast", SPEC | {"width": 0.5}, _table(a=HISTORY)).json(), member="a")
    assert [row["value"] for row in wide[:3]] == pytest.approx([7.7, 8.5, 9.3]) and [row["value"] for row in narrow[:3]] == pytest.approx([8.3, 8.5, 8.7])
    assert wide[-2]["value"] == raw[-2]["value"] == pytest.approx(25.5)
    assert wide[-1]["value"] - 25.5 == pytest.approx(2 * (raw[-1]["value"] - 25.5))
    # One factor per period ahead.
    got = _rows(client.send("/forecast", SPEC | {"width": [1, 2, 3], "levels": [0.1, 0.9], "totals": []}, _table(a=HISTORY)).json(), member="a")
    assert [row["value"] for row in got] == pytest.approx([8.1, 8.9, 7.7, 9.3, 7.3, 9.7])
    r = client.send("/forecast", SPEC | {"width": [1, 2]}, _table(a=HISTORY))
    assert r.status_code == 400 and "2 factors for a horizon of 3" in r.json()["error"]


def test_the_width_learned_from_a_record():
    rng = np.random.default_rng(0)
    actual = rng.normal(size=4000)
    # Intervals served at half their right width cover 80% of the record only when doubled (1.28 is the 90th percentile of the normal).
    lower, upper = np.full(4000, -0.64), np.full(4000, 0.64)
    assert forecast.learned_width(lower, np.zeros(4000), upper, actual, prior=0) == pytest.approx(2.0, abs=0.1)
    # A short record moves the width little; an empty one not at all; missing values are left out.
    assert 1.0 < forecast.learned_width(lower[:10], np.zeros(10), upper[:10], actual[:10]) < 1.5
    assert forecast.learned_width([], [], [], []) == 1.0
    assert forecast.learned_width([np.nan], [0], [1], [0.5]) == 1.0
    # Intervals too wide are narrowed.
    assert forecast.learned_width(np.full(4000, -5.0), np.zeros(4000), np.full(4000, 5.0), actual, prior=0) < 0.5


def test_the_members_of_a_request_are_forecast_together(client):
    assert client.send("/forecast", SPEC | {"horizon": 2, "totals": []}, _table(a=HISTORY, b=HISTORY, c=HISTORY)).status_code == 200
    assert kernels.get().chronos.groups == [(3, forecast.GROUP, True)]


def test_covariates_reach_the_model_and_members_with_the_same_ones_go_together(client):
    spec = {"targets": ["y"], "horizon": 2, "levels": [0.5]}
    # `promotion` is known ahead for a and c (two forecast rows), past-only for b.
    table = _table(a={"y": HISTORY, "promotion": [0] * 8}, b={"y": HISTORY, "oil": [1.5] * 8}, c={"y": HISTORY, "promotion": [0] * 8})
    table["member"] += ["a", "a", "c", "c"]
    table["period"] += [9, 10, 9, 10]
    table["status"] = ["actual"] * 24 + ["forecast"] * 4
    table["y"] += [None] * 4
    table["promotion"] += [10, 20, 10, 20]
    table["oil"] += [None] * 4
    r = client.send("/forecast", spec, table)
    assert r.status_code == 200, r.text
    got = r.json()
    assert [row["value"] for row in _rows(got, member="a")] == [row["value"] for row in _rows(got, member="c")] == [18.5, 28.5]
    assert [row["period"] for row in _rows(got, member="a")] == [9, 10] and [row["value"] for row in _rows(got, member="b")] == [8.5, 8.5]
    assert sorted(size for size, _, _ in kernels.get().chronos.groups) == [1, 2]
    # A category is text, a number a number; a missing category is empty text.
    mixed = _table(a={"y": HISTORY, "season": ["low"] * 7 + [None], "price": [1, None, 3, 4, 5, 6, 7, 8]})
    r = client.send("/forecast", spec, mixed)
    assert r.status_code == 200 and kernels.get().chronos.kinds == [{"season": "O", "price": "f"}]
    # A covariate with a value missing in the periods ahead is not known ahead.
    table["promotion"][-1] = None
    r = client.send("/forecast", spec, table)
    assert [row["value"] for row in _rows(r.json(), member="c")] == [8.5, 8.5]


def test_a_member_that_cannot_be_forecast_fails_alone(client):
    spec = {"targets": ["y"], "horizon": 2, "levels": [0.5]}
    r = client.send("/forecast", spec, _table(short=[1, None, 3], a=HISTORY))
    assert r.status_code == 200, r.text
    got = r.json()
    assert [row["value"] for row in _rows(got, member="a")] == [8.5, 8.5] and _rows(got, member="short") == []
    assert got["answer"]["errors"] == [{"member": "short", "origin": 3, "column": "y", "error": "2 known values of history: a forecast needs 6"}]
    # What the model itself refuses fails the members forecast with it, and no others.
    table = _table(bad=[-1] * 8, a=HISTORY, c={"y": HISTORY, "promotion": [0] * 8})
    table["member"] += ["c", "c"]
    table["period"] += [9, 10]
    table["status"] = ["actual"] * 24 + ["forecast"] * 2
    table["y"] += [None, None]
    table["promotion"] += [1, 1]
    r = client.send("/forecast", spec, table)
    got = r.json()
    assert r.status_code == 200 and {e["member"] for e in got["answer"]["errors"]} == {"bad", "a"} and all("a value under zero" in e["error"] for e in got["answer"]["errors"])
    assert [row["value"] for row in _rows(got, member="c")] == [9.5, 9.5]


def test_the_walk_forecasts_the_last_periods_from_the_history_before_them_and_reads_the_pit(client):
    spec = {"targets": ["y"], "origins": 3, "levels": [0.1, 0.5, 0.9]}
    r = client.send("/forecast", spec, _table(a=LONG, flat=[10] * 11 + [10.5]))
    assert r.status_code == 200, r.text
    got = r.json()
    a = _rows(got, member="a")
    # Origins 9, 10, 11 forecast the periods 10, 11, 12, one step each; the fake continues the last value.
    assert [(row["origin"], row["step"], row["period"]) for row in a[::3]] == [(9, 1, 10), (10, 1, 11), (11, 1, 12)]
    assert [row["value"] for row in a[::3]] == pytest.approx([9.1, 10.1, 11.1]) and [row["actual"] for row in a[::3]] == [10, 11, 12]
    # The actual lies above the model's highest quantile every time: the PIT is 1, the same on every level row of the point.
    assert [row["pit"] for row in a] == [1.0] * 9
    # A flat series that lands exactly on the median of its own grid: the PIT is the median's level.
    flat = _rows(got, member="flat", origin=11)
    assert flat[0]["actual"] == 10.5 and flat[0]["pit"] == pytest.approx(0.5)
    assert got["answer"]["errors"] == []
    # Origins named: the same, from the periods asked; one that is no period is an error.
    r = client.send("/forecast", spec | {"origins": [8, 11, 99]}, _table(a=LONG))
    got = r.json()
    assert [row["origin"] for row in _rows(got)[::3]] == [8, 11] and got["answer"]["errors"] == [{"member": "a", "origin": 99, "error": "no actual period of that name"}]
    # Too short a history for the walk asked: what can be walked is, and the answer says what could not.
    r = client.send("/forecast", spec | {"origins": 6}, _table(a=[1, 2, 3, 4, 5, 6, 7]))
    got = r.json()
    assert [row["origin"] for row in _rows(got)[::3]] == [6] and len(got["answer"]["errors"]) == 5  # the five earlier origins have under six known values
    r = client.send("/forecast", spec | {"origins": 9}, _table(a=HISTORY))
    assert "a walk of 9 needs 10" in r.json()["answer"]["errors"][0]["error"]
    # A backtest a horizon out: the actual and the PIT where the period has landed, none where it has not.
    r = client.send("/forecast", spec | {"origins": [10], "horizon": 3, "totals": [2]}, _table(a=LONG))
    got = r.json()
    rows = _rows(got, span=None)
    assert [(row["step"], row["period"], row["actual"]) for row in rows[::3]] == [(1, 11, 11), (2, 12, 12), (3, None, None)]
    assert [row["pit"] for row in rows[::3]] == [1.0, 1.0, None]
    total = _rows(got, span=2)
    assert total[0]["actual"] == 23 and total[0]["period"] == 12 and total[0]["pit"] is None


def test_forecast_refusals(client):
    base = _table(a=HISTORY)
    for change, word in (
        ({"targets": []}, "targets"), ({"targets": ["z"]}, "no value column"), ({"targets": ["y", "y"]}, "twice"), ({"horizon": 0}, "horizon"), ({"horizon": 2.5}, "horizon"),
        ({"levels": [1.0]}, "levels"), ({"totals": [4]}, "totals"), ({"grain": "fortnight"}, "grain"), ({"width": 0}, "width"), ({"width": "wide"}, "width"),
        ({"width": [1, None, 1]}, "width"), ({"origins": 0}, "origins"), ({"origins": []}, "origins"),
    ):  # fmt: skip
        r = client.send("/forecast", SPEC | change, base)
        assert r.status_code == 400 and word in r.json()["error"], (change, r.text)
    for table, word in (
        ({"period": PERIODS[:8], "y": HISTORY}, "`member`"), ({"member": ["a"] * 8, "y": HISTORY}, "`period`"),
        ({"member": ["a"] * 8, "period": [1, 1, 2, 3, 4, 5, 6, 7], "y": HISTORY}, "two rows of period 1"),
        ({"member": ["a"] * 8, "period": PERIODS[:8], "y": ["x"] * 8}, "holds text"),
        ({"member": ["a"] * 8, "period": PERIODS[:8], "y": HISTORY, "status": ["actual"] * 7 + ["done"]}, "status"),
        ({"member": ["a"] * 8, "period": PERIODS[:8], "y": HISTORY, "status": ["actual", "plan"] + ["actual"] * 6}, "before its last actual row"),
        ({"member": ["a"] * 8, "period": [None] + PERIODS[1:8], "y": HISTORY}, "`period` is missing"),
        ({"member": ["a", None] + ["a"] * 6, "period": PERIODS[:8], "y": HISTORY}, "`member` is missing"),
    ):
        r = client.send("/forecast", SPEC, table)
        assert r.status_code == 400 and word in r.json()["error"], (table, r.text)
    for body, word in (({"spec": SPEC}, "table"), ({"spec": SPEC, "table": {"y": [1, "a"]}}, "mixes"), ({"spec": SPEC, "table": {"y": [1], "member": []}}, "same number"), ({"table": base}, "spec"), ([1], "object")):
        r = client.post("/forecast", json=body)
        assert r.status_code == 400 and word in r.json()["error"], (body, r.text)
    r = client.post("/forecast", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_misfit_serves_how_far_each_cell_lies_below_its_column(client):
    # The fake's shares are [[0.3, -0.2], [nan, 1.0]]: centered by column (0.3 and 0.4) in nats, the spreads being under a nat.
    r = client.send("/misfit", {}, MISFIT)
    assert r.status_code == 200, r.text
    assert r.json()["table"] == {"row": [0, 1], "score": [pytest.approx(-0.6), pytest.approx(0.6)]}
    # The answer declares the floor the score rests on.
    assert r.json()["answer"] == {"service": "glossthesi", "version": "dev", "errors": [], "assumptions": [kernels.floor_assumption()]}
    assert kernels.floor_assumption()["floor_nats"] == kernels.SPREAD_FLOOR
    got = client.send("/misfit", {"cells": True}, MISFIT).json()["table"]
    assert got["a"] == [pytest.approx(0.0), None] and got["b"] == [pytest.approx(-0.6), pytest.approx(0.6)]
    # A row without a share is null, not a number. A frame has no reserved names: a column called `status` is a column of it.
    r = client.send("/misfit", {}, {"status": ["open", "paid", "open"], "a": [1, 3, 5], "b": [2, 4, 6]})
    assert r.status_code == 200 and r.json()["table"]["score"] == [None, None, None] and kernels.get().table.shape == (3, 3) and kernels.get().categorical == [0]
    for spec, word in (({"cells": "yes"}, "cells"), ({"folds": 0}, "folds"), ({"categorical": ["z"]}, "no column `z`"), ({"categorical": "a"}, "categorical")):
        r = client.send("/misfit", spec, MISFIT)
        assert r.status_code == 400 and word in r.json()["error"], (spec, r.text)
    r = client.send("/misfit", {}, {"a": [1, 2]})
    assert r.status_code == 400 and "two of each" in r.json()["error"]


def test_misfit_takes_categories_as_text_or_by_name(client):
    r = client.send("/misfit", {}, {"k": ["b", "a", ""], "n": [1, None, 3], "f": [True, False, True], "v": [7, 7, 8]})
    assert r.status_code == 200, r.text
    k = kernels.get()
    assert k.categorical == [0, 2] and np.array_equal(np.isnan(k.table), [[False, False, False, False], [False, True, False, False], [True, False, False, False]])
    assert k.table[:, 0].tolist()[:2] == [1.0, 0.0] and k.table[:, 2].tolist() == [1.0, 0.0, 1.0] and k.table[:, 3].tolist() == [7.0, 7.0, 8.0]
    # A numeric column named categorical is coded by its values as text.
    r = client.send("/misfit", {"categorical": ["account"]}, {"account": [4000, 4010], "rate": [1.5, 2.5]})
    assert r.status_code == 200 and k.categorical == [0] and k.table[:, 0].tolist() == [0.0, 1.0]
    r = client.arrow("/misfit", {"categorical": ["account"]}, {"account": [4000, 4010], "rate": [1.5, 2.5]})
    assert r.status_code == 200 and k.table[:, 0].tolist() == [0.0, 1.0]


def test_keys_gate_when_set(client, monkeypatch):
    monkeypatch.delenv("GLOSSTHESI_INSECURE_OPEN")
    monkeypatch.setenv("GLOSSTHESI_KEYS", "k1, k2")
    assert client.send("/misfit", {}, MISFIT).status_code == 401
    assert client.send("/misfit", {}, MISFIT, headers={"authorization": "Bearer nope"}).status_code == 401
    assert client.send("/misfit", {}, MISFIT, headers={"authorization": "Bearer k2"}).status_code == 200


def test_nothing_is_served_where_nothing_says_who_may_call(client, monkeypatch):
    """No keys and no audience is not "open": every door refuses, the
    refusal names the switch, and the service does not start."""
    from glossthesi import __main__ as entry

    monkeypatch.delenv("GLOSSTHESI_INSECURE_OPEN")
    for name in ("GLOSSTHESI_KEYS", "GLOSSTHESI_AUDIENCE", "GLOSSTHESI_CALLERS"):
        monkeypatch.delenv(name, raising=False)
    r = client.send("/misfit", {}, MISFIT, headers={"authorization": "Bearer anything"})
    assert r.status_code == 401 and "GLOSSTHESI_INSECURE_OPEN=true" in r.json()["error"]
    assert client.get("/healthz").status_code == 200  # outside the gate, as before
    with pytest.raises(SystemExit, match="GLOSSTHESI_INSECURE_OPEN=true"):
        entry.main()
    # Only the literal `true` opens the doors: the switch is a statement, never a fallback.
    for said in ("1", "TRUE", "yes", "false", ""):
        monkeypatch.setenv("GLOSSTHESI_INSECURE_OPEN", said)
        assert client.send("/misfit", {}, MISFIT).status_code == 401
    monkeypatch.setenv("GLOSSTHESI_INSECURE_OPEN", " true ")
    assert client.send("/misfit", {}, MISFIT).status_code == 200 and app_module.arrangement() == "insecure-open"


def test_id_tokens_gate_by_audience_and_account(client, monkeypatch):
    """With an audience set, only a verified token for it from a listed
    account passes; the verifier is asked once per token until it expires."""
    monkeypatch.delenv("GLOSSTHESI_INSECURE_OPEN")
    monkeypatch.setenv("GLOSSTHESI_AUDIENCE", "https://service.example")
    monkeypatch.setenv("GLOSSTHESI_CALLERS", "glossql@proj.iam.gserviceaccount.com")
    monkeypatch.setattr(app_module, "_TOKENS", {})
    asked = []

    def verify(token, audience):
        asked.append(token)
        assert audience == "https://service.example"
        if token == "good":
            return "glossql@proj.iam.gserviceaccount.com", time.time() + 3600
        if token == "other":
            return "someone@else.iam.gserviceaccount.com", time.time() + 3600
        raise ValueError("not ours")

    monkeypatch.setattr(app_module, "_verify_id_token", verify)
    assert client.send("/misfit", {}, MISFIT).status_code == 401
    assert client.send("/misfit", {}, MISFIT, headers={"authorization": "Bearer forged"}).status_code == 401
    assert client.send("/misfit", {}, MISFIT, headers={"authorization": "Bearer other"}).status_code == 401
    assert client.send("/misfit", {}, MISFIT, headers={"authorization": "Bearer good"}).status_code == 200
    assert client.send("/misfit", {}, MISFIT, headers={"authorization": "Bearer good"}).status_code == 200
    assert asked == ["forged", "other", "good"]
    # A shared key is not accepted once an audience is set.
    monkeypatch.setenv("GLOSSTHESI_KEYS", "k1")
    assert client.send("/misfit", {}, MISFIT, headers={"authorization": "Bearer k1"}).status_code == 401


def test_a_failure_is_a_500_as_json_and_a_log_line(client, monkeypatch, capsys):
    import logging

    from glossthesi import telemetry

    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", list(root.handlers))
    telemetry.configure("test")
    monkeypatch.setattr(app_module.Misfit, "parse", staticmethod(lambda table, spec: 1 / 0))
    r = client.send("/misfit", {}, MISFIT)
    assert r.status_code == 500 and r.json() == {"error": "the service failed: ZeroDivisionError"}
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    failed = next(line for line in lines if line["event"] == "request_failed")
    assert failed["severity"] == "ERROR" and failed["route"] == "/misfit" and "ZeroDivisionError" in failed["traceback"]
    refused = next(line for line in lines if line["event"] == "refused")
    assert refused["status"] == 500 and refused["caller"] == "insecure_dev_mode"


def test_a_draining_instance_sends_requests_back_to_retry(client, monkeypatch):
    monkeypatch.setattr(app_module, "DRAINING", threading.Event())
    app_module.DRAINING.set()
    r = client.send("/misfit", {}, MISFIT)
    assert r.status_code == 503 and r.headers["retry-after"] == "2" and "retry" in r.json()["error"]
    assert client.get("/healthz").status_code == 200


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["device"] == "fake" and r.json()["version"] == "dev" and r.json()["service"] == "glossthesi"
    client.send("/misfit", {}, MISFIT)
    own = client.get("/healthz").json()["owner"]
    assert own["cycles"] == 1 and own["jobs"] == 1 and own["last_cycle_at"] > 0 and own["waiting_cells"] == 0


def test_a_full_queue_answers_429_with_retry_after(client, monkeypatch):
    from glossthesi.owner import Busy

    class Full:
        def exclusive(self, *_a, **_k):
            raise Busy("the service's queue is full — retry shortly", retry_after=7)

    monkeypatch.setattr(app_module, "_OWNER", Full())
    r = client.send("/misfit", {}, MISFIT)
    assert r.status_code == 429 and r.headers["retry-after"] == "7" and "full" in r.json()["error"]


def test_a_body_over_the_cap_is_refused_before_it_is_parsed(client, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_BODY_MB", 0)
    r = client.send("/misfit", {}, MISFIT)
    assert r.status_code == 413 and "in parts" in r.json()["error"]


def test_the_pit_reads_the_actual_against_the_models_own_grid():
    grid = 10.0 + np.asarray(forecast.GRID)  # the quantile at level l is 10 + l
    assert forecast.pit(grid, 9.0) == 0.0 and forecast.pit(grid, 12.0) == 1.0
    assert forecast.pit(grid, 10.5) == pytest.approx(0.5) and forecast.pit(grid, 10.475) == pytest.approx(0.475)
    assert forecast.pit(grid, 10.99) == pytest.approx(0.99) and forecast.pit(grid, 10.01) == pytest.approx(0.01)
    # A grid that repeats a value (a mostly-zero series) places a tied actual at the midpoint of its tied levels.
    zeros = np.zeros(len(forecast.GRID))
    assert forecast.pit(zeros, 0.0) == pytest.approx(0.5)
    tied = np.where(np.asarray(forecast.GRID) < 0.3, 0.0, 1.0)
    assert forecast.pit(tied, 0.0) == pytest.approx(np.mean([l for l in forecast.GRID if l < 0.3]))
    assert np.isnan(forecast.pit(grid, float("nan"))) and np.isnan(forecast.pit(grid, None))


# -- /whatif ------------------------------------------------------------------


def _weeks(n, first="2024-01-01"):
    return [str(np.datetime64(first) + np.timedelta64(7 * i, "D")) for i in range(n)]


def _scenario(seed=0, ahead=4, **members):
    """Weekly members with an on-or-off input `x` and a second input
    `z`: 60 actual weeks, then `ahead` weeks as planned (x off) and
    under the scenario (x on). `members` gives per member its level and
    what one unit of x does to `y`."""
    rng = np.random.default_rng(seed)
    out = {name: [] for name in ("member", "period", "status", "y", "x", "z")}
    weeks = _weeks(60 + ahead)
    for name, (level, effect) in members.items():
        x = (rng.uniform(size=60) < 0.5).astype(float)
        y = level + effect * x + rng.normal(0, 0.3, 60)
        z, later = rng.uniform(size=60), rng.uniform(size=ahead)
        for status, periods, xs, ys, zs in (("actual", weeks[:60], x, y, z), ("plan", weeks[60:], np.zeros(ahead), [None] * ahead, later), ("scenario", weeks[60:], np.ones(ahead), [None] * ahead, later)):
            n = len(periods)
            out["member"] += [name] * n
            out["period"] += periods
            out["status"] += [status] * n
            out["y"] += [None if v is None else float(v) for v in ys]
            out["x"] += [float(v) for v in xs]
            out["z"] += [float(v) for v in zs]
    return out


WHATIF = {"targets": ["y"], "levels": [0.1, 0.5, 0.9]}


def test_a_whatif_states_the_sign_where_the_history_and_the_model_agree(client):
    r = client.send("/whatif", WHATIF, _scenario(a=(50.0, 2.0), b=(10.0, 0.0)))
    assert r.status_code == 200, r.text
    answer = r.json()
    assert answer["answer"]["errors"] == [] and answer["answer"]["changed"] == "x"
    assert {a["field"] for a in answer["answer"]["assumptions"]} == {"sign", "basis", "history_low"}
    # Three kinds of rows per member and period, each at the levels asked: held back, as planned, under the scenario.
    assert len(_rows(answer, member="a")) == 3 * 4 * 3
    held = _rows(answer, member="a", status="held_back", level=0.5)
    assert [row["period"] for row in held] == _weeks(60)[-4:] and all(row["actual"] is not None and row["history_value"] is None for row in held)
    # The input moves y by 2 in this member's history, and the model moves it the same way: the sign is stated.
    plan, scenario = _rows(answer, member="a", status="plan", level=0.5), _rows(answer, member="a", status="scenario", level=0.5)
    assert {row["sign"] for row in scenario} == {"+"} and scenario[0]["confidence"] == "direction" and "the model moves it the same way" in scenario[0]["basis"]
    assert all(abs((s["value"] - p["value"]) - 2.0) < 1e-9 for s, p in zip(scenario, plan))
    # Beside the model's value, the history's own estimate of the scenario with its interval: the plan plus what the history says the change does.
    assert all(abs(s["history_value"] - p["value"] - 2.0) < 0.3 and s["history_low"] < s["history_value"] < s["history_high"] and p["history_value"] is None for s, p in zip(scenario, plan))
    assert scenario[0]["explains"] > 0.8 and 0 < scenario[0]["error"] < 1.5 and [row["period"] for row in scenario] == _weeks(64)[-4:]
    # A member whose history shows nothing, and the model moves nothing: no effect visible, which is not "no effect".
    still = _rows(answer, member="b", status="scenario", level=0.5)
    assert {row["sign"] for row in still} == {"0"} and still[0]["confidence"] == "no effect visible" and still[0]["history_low"] < still[0]["value"] < still[0]["history_high"]


def test_a_whatif_says_what_it_cannot_answer_and_why(client, monkeypatch):
    # The model moves the column against what the history shows: no sign.
    monkeypatch.setattr(Fake, "effect", -2.0)
    against = _rows(client.send("/whatif", WHATIF, _scenario(a=(50.0, 2.0))).json(), status="scenario", level=0.5)
    assert against[0]["sign"] == "?" and against[0]["confidence"] == "not answerable" and "disagree" in against[0]["basis"]
    monkeypatch.setattr(Fake, "effect", 2.0)
    # A change beyond what the history holds: the history's x lay between 0 and 1.
    table = _scenario(a=(50.0, 2.0))
    table["x"] = [3.0 if status == "scenario" else x for status, x in zip(table["status"], table["x"])]
    beyond = _rows(client.send("/whatif", WHATIF, table).json(), status="scenario", level=0.5)
    assert beyond[0]["sign"] == "?" and beyond[0]["basis"].startswith("the change goes beyond what the history holds") and beyond[0]["history_value"] is None
    # The input as planned already lies beyond it: said as that, not as the change.
    table["x"] = [{"plan": 2.0, "scenario": 3.0}.get(status, x) for status, x in zip(table["status"], table["x"])]
    assert _rows(client.send("/whatif", WHATIF, table).json(), status="scenario", level=0.5)[0]["basis"].startswith("the input as planned goes beyond what the history holds")
    # An input that never varied in the history, and one that only repeats another.
    table = _scenario(a=(50.0, 2.0))
    table["x"] = [x if status != "actual" else 0.0 for status, x in zip(table["status"], table["x"])]
    assert "never varied" in _rows(client.send("/whatif", WHATIF, table).json(), status="scenario", level=0.5)[0]["basis"]
    table = _scenario(a=(50.0, 2.0))
    table["z"] = [2.0 * x + 1.0 if status == "actual" else z for status, x, z in zip(table["status"], table["x"], table["z"])]
    tied = _rows(client.send("/whatif", WHATIF, table).json(), status="scenario", level=0.5)[0]
    assert tied["sign"] == "?" and "cannot be told apart from the input `z`" in tied["basis"]


def test_a_member_or_a_target_that_cannot_be_read_fails_alone(client):
    table = _scenario(a=(50.0, 2.0), short=(50.0, 2.0), under=(-50.0, 2.0))
    keep = [not (m == "short" and s == "actual" and p < _weeks(60)[45]) for m, s, p in zip(table["member"], table["status"], table["period"])]
    table = {name: [v for v, k in zip(values, keep) if k] for name, values in table.items()}
    r = client.send("/whatif", WHATIF, table)
    assert r.status_code == 200, r.text
    errors = r.json()["answer"]["errors"]
    assert {"member": "short", "error": "15 actual periods: 4 ahead need 30 behind them"} in errors
    assert {"member": "under", "column": "y", "error": "a level under zero"} in errors
    assert {row["member"] for row in _rows(r.json())} == {"a"}


def test_the_whatif_takes_arrow_with_dates_and_answers_the_same(client):
    table = _scenario(a=(50.0, 2.0))
    as_json = client.send("/whatif", WHATIF, table).json()
    dated = dict(table, period=[np.datetime64(p).astype(object) for p in table["period"]])
    r = client.arrow("/whatif", WHATIF, dated)
    assert r.status_code == 200, r.text
    got = pa.ipc.open_stream(r.content).read_all()
    assert pa.types.is_date(got.schema.field("period").type)
    assert np.allclose(got.column("value").to_numpy(), np.array(as_json["table"]["value"], dtype=float))
    assert got.column("sign").to_pylist() == as_json["table"]["sign"]
    assert json.loads(got.schema.metadata[b"answer"])["changed"] == "x"


def test_whatif_refusals(client):
    table = _scenario(a=(50.0, 2.0))

    def refused(spec=WHATIF, **changes):
        return client.send("/whatif", spec, dict(table, **changes))

    # One input is changed per request: none or several are refused, with the fields named.
    same = refused(x=[0.0 if status == "scenario" else x for status, x in zip(table["status"], table["x"])])
    assert same.status_code == 422 and "nothing is changed" in same.json()["error"]
    both = refused(z=[9.0 if status == "scenario" else z for status, z in zip(table["status"], table["z"])])
    assert both.status_code == 422 and {e["field"] for e in both.json()["errors"]} == {"x", "z"}
    # The calendar is read from the period.
    assert refused(period=list(range(len(table["period"])))).status_code == 400
    # The periods ahead come twice, the same periods in both.
    lost = [s if i != len(table["status"]) - 1 else "plan" for i, s in enumerate(table["status"])]
    assert "the same periods in both" in refused(status=lost, period=table["period"][:-1] + ["2030-01-07"]).json()["error"]
    assert "takes `actual` rows" in refused(status=["forecast" if s == "plan" else s for s in table["status"]]).json()["error"]
    assert "needs an input" in client.send("/whatif", WHATIF, {k: v for k, v in table.items() if k not in ("x", "z")}).json()["error"]
    assert "`targets`" in refused(spec={"targets": ["nope"], "levels": [0.5]}).json()["error"]
    assert "`levels`" in refused(spec={"targets": ["y"]}).json()["error"]
