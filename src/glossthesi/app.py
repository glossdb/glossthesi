"""The doors of the service: one table and a specification in, one
table and an answer out (`docs/contract.md`, `wire.py`).

Three reads on the one table — `/forecast` for the periods ahead and
the walk, `/misfit` for the rows that break a relation between
columns, `/whatif` for which way a column moves under a changed
input — and `/healthz`. Nothing here knows about datasets, metrics
or actors: a request is numbers and a key.

Authentication, by what is set — the same `Authorization: Bearer …`
header either way, and the caller it names is whose share of the queue
the request counts against:

- `GLOSSTHESI_AUDIENCE` and `GLOSSTHESI_CALLERS`: a Google-signed ID
  token for that audience (the service's own URL) from one of the listed
  service accounts. That is what a caller on Cloud Run, GKE or a VM mints
  from its metadata server for the URL it calls — no key anywhere. Cloud
  Run checks the same token in front when the service requires
  authentication; the service checks it again, so one left open by
  mistake still refuses. The caller is the account's email.
- `GLOSSTHESI_KEYS`: shared bearer keys, comma-separated (a laptop, a
  test host). The caller is the key's place in the list (`key-1`),
  never the key itself: the caller's name goes to the logs.
- neither: every request is refused, and the service does not start.
- `GLOSSTHESI_INSECURE_OPEN=true`: the one way around, as in glossql.
  The doors serve without authentication and every caller is recorded
  as `insecure_dev_mode`. The name is the warning: a laptop trying the
  service out, never a deployment.
"""

from __future__ import annotations

import asyncio
import hmac
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import coverage, forecast, kernels, telemetry, whatif, wire
from .owner import Busy, Owner, TooLarge
from .wire import Refusal

SERVICE = "glossthesi"
VERSION = os.environ.get("GLOSSTHESI_VERSION", "") or "dev"

# Handlers never touch the model: one owner per device takes what is
# waiting from every caller and answers it in turn (owner.py).
_OWNER: Owner | None = None
_OWNER_LOCK = threading.Lock()

# A body is parsed before the queue can refuse it, so it is bounded first: the bytes as they arrive, and what an
# Arrow stream decodes to, batch by batch — a compressed stream may be many times its wire size (wire.parse).
MAX_BODY_MB = int(os.environ.get("GLOSSTHESI_MAX_BODY_MB", "") or 32)

# Set when the host has asked the process to stop: what is in flight is
# finished, and a request arriving on a kept connection is sent back to
# retry (a 503 with `Retry-After`) — the host has already moved traffic.
DRAINING = threading.Event()


def owner() -> Owner:
    global _OWNER
    with _OWNER_LOCK:
        if _OWNER is None:
            _OWNER = Owner(lambda: kernels.get())
        return _OWNER


def _names(variable: str) -> set[str]:
    raw = os.environ.get(variable, "")
    return {k.strip() for k in raw.split(",") if k.strip()}


def _keys() -> list[str]:
    """The bearer keys in the order set: a key is named by its place."""
    return [k.strip() for k in os.environ.get("GLOSSTHESI_KEYS", "").split(",") if k.strip()]


INSECURE_DEV_MODE = "insecure_dev_mode"  # who every caller is where nobody is verified: glossql's name for it
UNARRANGED = (
    "nothing says who may call: set GLOSSTHESI_AUDIENCE and GLOSSTHESI_CALLERS, or GLOSSTHESI_KEYS; "
    "GLOSSTHESI_INSECURE_OPEN=true serves open instead, every caller insecure_dev_mode"
)


def insecure_open() -> bool:
    """Only the literal `true` counts: the switch is a statement, never a fallback."""
    return os.environ.get("GLOSSTHESI_INSECURE_OPEN", "").strip() == "true"


def arrangement() -> str | None:
    """How a caller is verified; None where nothing is arranged, and then nothing is served."""
    if insecure_open():
        return "insecure-open"
    if os.environ.get("GLOSSTHESI_AUDIENCE", "").strip():
        return "id-token" if _names("GLOSSTHESI_CALLERS") else None  # an audience nobody is allowed for is nothing arranged
    return "keys" if _keys() else None


# Verified ID tokens, kept until they expire: Google's certificates are
# not fetched per request. token -> (email, exp).
_TOKENS: dict[str, tuple[str, float]] = {}
_TOKENS_LOCK = threading.Lock()


def _verify_id_token(token: str, audience: str) -> tuple[str, float]:
    """The email and expiry of a Google-signed ID token for `audience`;
    ValueError when it is not one. Replaced in tests."""
    from google.auth.transport import requests as transport
    from google.oauth2 import id_token

    claims = id_token.verify_oauth2_token(token, transport.Request(), audience=audience, clock_skew_in_seconds=10)
    email = claims.get("email")
    if not email:
        raise ValueError("the token names no account")
    return email, float(claims["exp"])


def _account(token: str, audience: str) -> str | None:
    now = time.time()
    with _TOKENS_LOCK:
        kept = _TOKENS.get(token)
        if kept is not None and kept[1] > now:
            return kept[0]
    try:
        email, exp = _verify_id_token(token, audience)
    except ValueError:
        return None
    with _TOKENS_LOCK:
        if len(_TOKENS) > 1024:
            for stale in [t for t, (_, e) in _TOKENS.items() if e <= now]:
                del _TOKENS[stale]
        _TOKENS[token] = (email, exp)
    return email


def _caller(request: Request) -> str | None:
    """Who is asking; None when the request carries nothing this service accepts."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer":
        token = ""
    if insecure_open():
        return INSECURE_DEV_MODE
    audience = os.environ.get("GLOSSTHESI_AUDIENCE", "").strip()
    if audience:
        callers = _names("GLOSSTHESI_CALLERS")
        if not token or not callers:
            return None
        email = _account(token, audience)
        return email if email is not None and email in callers else None
    if not token:
        return None
    # A key is never the caller's name — the name goes to the logs — and every key is compared, in constant time.
    found = None
    for i, key in enumerate(_keys(), start=1):
        if hmac.compare_digest(token.encode(), key.encode()):
            found = f"key-{i}"
    return found


async def _body(request: Request) -> bytes:
    """The body, refused with a 413 once it passes the cap: as declared, or as it arrives where nothing is declared."""
    cap = MAX_BODY_MB * 1024 * 1024
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise Refusal(f"the body is over {MAX_BODY_MB} MB — send it in parts", 413)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            raise Refusal(f"the body is over {MAX_BODY_MB} MB — send it in parts", 413)
        chunks.append(chunk)
    return b"".join(chunks)


def _door(read):
    """Wrap a read: auth, the body as a table and a specification, the
    owner's queue, the answer in the request's encoding, refusals as JSON."""

    async def endpoint(request: Request) -> Response:
        route = request.url.path
        if DRAINING.is_set():
            telemetry.refused(503, None, route, "draining")
            return JSONResponse({"error": "this instance is stopping — retry"}, status_code=503, headers={"Retry-After": "2"})
        caller = await run_in_threadpool(_caller, request)
        if caller is None:
            telemetry.refused(401, None, route, "unauthorized")
            said = "unauthorized: a bearer this service accepts" if arrangement() else f"unauthorized: {UNARRANGED}"
            return JSONResponse({"error": said}, status_code=401)
        try:
            raw = await _body(request)
            parsed = await run_in_threadpool(wire.parse, request.headers.get("content-type", ""), raw, MAX_BODY_MB * 1024 * 1024)
            plan = await run_in_threadpool(read.parse, parsed.table, parsed.spec)
            table, answer = await read.serve(owner(), caller, plan)
            body, media = await run_in_threadpool(wire.encode, table, {"service": SERVICE, "version": VERSION} | answer, parsed.encoding)
            return Response(body, media_type=media)
        except Refusal as e:
            refusal, status, headers = e, e.status, None
        except Busy as e:
            refusal, status, headers = e, 429, {"Retry-After": str(e.retry_after)}
        except TooLarge as e:
            refusal, status, headers = e, 413, None
        except (kernels.KernelError, forecast.ForecastError, whatif.WhatifError) as e:
            refusal, status, headers = e, 422, None
        except Exception as e:  # a failure is a 500 with the traceback in the log, not on the wire
            telemetry.log.error("request_failed", extra={"route": route, "caller": caller}, exc_info=e)
            telemetry.refused(500, caller, route, type(e).__name__)
            return JSONResponse({"error": f"the service failed: {type(e).__name__}"}, status_code=500)
        telemetry.refused(status, caller, route, str(refusal))
        body: dict[str, Any] = {"error": str(refusal)}
        if isinstance(refusal, Refusal) and refusal.errors:
            body["errors"] = refusal.errors
        return JSONResponse(body, status_code=status, headers=headers)

    return endpoint


def _spec(field: str, message: str, status: int = 400) -> Refusal:
    """A specification refused: the message, and the field at fault named for an agent to repair."""
    return Refusal(message, status, [{"field": field, "error": message}])


def _levels(spec: dict[str, Any], grid: tuple[float, float] | None = None) -> list[float]:
    """The quantile levels asked; with `grid`, the model's own lowest and highest, outside of which a level is refused
    rather than answered with the grid's edge under the level's name."""
    value = spec.get("levels")
    if not isinstance(value, list) or not value:
        raise _spec("levels", "`levels` is a non-empty list of quantile levels")
    low, high = grid or (0.0, 1.0)
    out = []
    for a in value:
        if not isinstance(a, (int, float)) or isinstance(a, bool) or not (low <= float(a) <= high if grid else 0.0 < float(a) < 1.0):
            raise _spec("levels", f"every one of `levels` lies between {low:g} and {high:g}, the model's own grid" if grid else "every one of `levels` lies strictly between 0 and 1")
        out.append(float(a))
    return out


def _period(column: pa.ChunkedArray, row: int) -> Any:
    return wire.clean(column[row].as_py())


# -- /forecast ----------------------------------------------------------------


@dataclass
class _Input:
    member: str
    start: int  # the member's first row in the table
    end: int  # one past its last
    origin: int  # the row of the last period of the history
    series: forecast.Series


@dataclass
class _ForecastPlan:
    table: pa.Table
    targets: list[str]
    horizon: int
    levels: list[float]
    spans: list[int]
    grain: str
    inputs: list[_Input]
    values: dict[str, np.ndarray]
    status: np.ndarray
    errors: list[dict] = field(default_factory=list)


class Forecast:
    """Quantile forecasts by Chronos-2 (`forecast.py`) of the `targets`
    of every member, `horizon` periods past its last actual row — or,
    with `origins`, from past origins: the walk and a backtest, with
    the actual value and the PIT where the table holds the actual.

    The table's actual rows are a member's history, in period order;
    its rows past the last actual carry the covariates known ahead.
    Every value column that is not a target is a covariate. The answer
    is one row per member, origin, step, column and level."""

    @staticmethod
    def parse(table: pa.Table, spec: dict[str, Any]) -> _ForecastPlan:
        targets = spec.get("targets")
        if not isinstance(targets, list) or not targets or any(not isinstance(t, str) for t in targets):
            raise _spec("targets", "`targets` is a non-empty list of the columns to forecast")
        for t in targets:
            if t in wire.RESERVED or t not in table.column_names:
                raise _spec("targets", f"`targets`: the table has no value column `{t}`")
            if wire.is_category(table.schema.field(t)):
                raise _spec("targets", f"`targets`: column `{t}` holds text, and a forecast needs numbers")
        if len(set(targets)) != len(targets):
            raise _spec("targets", "`targets` names a column twice")
        origins = spec.get("origins")
        if origins is not None and not ((isinstance(origins, int) and not isinstance(origins, bool) and origins >= 1) or (isinstance(origins, list) and origins)):
            raise _spec("origins", "`origins` is a number of periods to walk, or a non-empty list of periods to forecast from")
        horizon = spec.get("horizon", 1 if origins is not None else None)
        if not isinstance(horizon, int) or isinstance(horizon, bool) or not 1 <= horizon <= forecast.MAX_HORIZON:
            raise _spec("horizon", f"`horizon` is how many periods ahead, a whole number from 1 to {forecast.MAX_HORIZON}")
        spans = spec.get("totals", [])
        if not isinstance(spans, list) or any(not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= horizon for n in spans):
            raise _spec("totals", "`totals` is a list of spans in periods, each from 1 to the horizon")
        grain = spec.get("grain", "month")
        if grain not in forecast.GRAINS:
            raise _spec("grain", f"`grain` is one of {', '.join(forecast.GRAINS)}")
        width = spec.get("width", 1.0)
        factors = width if isinstance(width, list) else [width]
        if not factors or any(not isinstance(w, (int, float)) or isinstance(w, bool) or not 0 < w < math.inf for w in factors):
            raise _spec("width", "`width` is a number above zero, or a list of one per period of the horizon")
        if isinstance(width, list) and len(width) != horizon:
            raise _spec("width", f"`width`: {len(width)} factors for a horizon of {horizon}")
        width = np.array(width, dtype=np.float64) if isinstance(width, list) else float(width)
        levels = _levels(spec, (forecast.GRID[0], forecast.GRID[-1]))

        table, members = wire.by_member(table, "/forecast")
        status = wire.status(table)
        periods = table.column("period")
        numbers = [c for c in wire.value_columns(table) if not wire.is_category(table.schema.field(c))]
        texts = [c for c in wire.value_columns(table) if wire.is_category(table.schema.field(c))]
        values = {c: wire.numeric(table, c) for c in numbers}
        words = {c: wire.categories(table, c) for c in texts}
        covariates = [c for c in numbers + texts if c not in targets]
        errors: list[dict] = []
        walks: list[list[_Input]] = []  # per member, its inputs oldest origin first
        for member, s, e in members:
            actual = status[s:e] == "actual"
            n = int(actual.sum())
            if n and not actual[:n].all():
                first = int(np.flatnonzero(~actual)[0])
                raise Refusal(f"member `{member}`: a {status[s + first]} row before its last actual row — the history comes first, then the periods ahead")
            if n == 0:
                errors.append({"member": member, "error": "no actual rows to forecast from"})
                continue
            if origins is None:
                mine = [n - 1]
            elif isinstance(origins, int):
                mine = [n - 1 - j for j in range(origins, 0, -1) if n - 1 - j >= 0]
                if len(mine) < origins:
                    errors.append({"member": member, "error": f"{n} actual periods: a walk of {origins} needs {origins + 1}; {len(mine)} walked"})
            else:
                labels = [str(v) for v in wire.clean(periods.slice(s, n).to_pylist())]
                mine = []
                for asked in origins:
                    if str(asked) in labels:
                        mine.append(labels.index(str(asked)))
                    else:
                        errors.append({"member": member, "origin": asked, "error": "no actual period of that name"})
                mine.sort()
            inputs = []
            for o in mine:
                history = np.stack([values[t][s : s + o + 1] for t in targets])
                past: dict[str, np.ndarray] = {}
                future: dict[str, np.ndarray] = {}
                for c in covariates:
                    whole = values[c] if c in values else words[c]
                    past[c] = whole[s : s + o + 1]
                    ahead = whole[s + o + 1 : min(s + o + 1 + horizon, e)]
                    known = (~np.isnan(ahead)).all() if c in values else (ahead != "").all()
                    if ahead.shape[0] == horizon and known:
                        future[c] = ahead
                inputs.append(_Input(member, s, e, s + o, forecast.Series(history, past, future, width)))
            walks.append(inputs)
        # Origin-major, so that the series forecast together are those of one origin, as a request per origin would send them.
        ordered = [walk[j] for j in range(max((len(w) for w in walks), default=0)) for walk in walks if j < len(walk)]
        return _ForecastPlan(table, targets, horizon, levels, spans, grain, ordered, values, status, errors)

    @staticmethod
    async def serve(own: Owner, caller: str, plan: _ForecastPlan) -> tuple[pa.Table, dict]:
        series = [one.series for one in plan.inputs]
        work = sum((one.columns + len(one.past)) * one.length for one in series)
        got = await asyncio.wrap_future(own.exclusive(caller, "forecast", work, lambda k: k.chronos.forecast(series, plan.horizon, plan.levels, plan.spans, plan.grain)))
        periods = plan.table.column("period")
        h, levels = plan.horizon, np.asarray(plan.levels)
        cols: dict[str, list] = {name: [] for name in ("member", "origin", "step", "period", "span", "column", "level", "value", "actual", "pit")}
        errors = list(plan.errors)
        for one, answer in zip(plan.inputs, got):
            where = {"member": one.member, "origin": _period(periods, one.origin)}
            if isinstance(answer, Exception):
                errors.append(where | ({"column": plan.targets[0]} if len(plan.targets) == 1 else {}) | {"error": str(answer)})
                continue
            rows = one.end - one.origin - 1  # periods the table holds past the origin
            for target, column in zip(plan.targets, answer):
                if isinstance(column, Exception):
                    errors.append(where | {"column": target, "error": str(column)})
                    continue
                q, raw = column["quantiles"], column["raw"]
                actual = np.full(h, np.nan)
                ahead = min(h, rows)
                if ahead > 0:
                    known = plan.status[one.origin + 1 : one.origin + 1 + ahead] == "actual"
                    actual[:ahead] = np.where(known, plan.values[target][one.origin + 1 : one.origin + 1 + ahead], np.nan)
                pits = np.array([forecast.pit(raw[step], actual[step]) if np.isfinite(actual[step]) else np.nan for step in range(h)])
                n = h * len(levels)
                cols["member"] += [one.member] * n
                cols["origin"] += [one.origin] * n
                cols["step"] += np.repeat(np.arange(1, h + 1), len(levels)).tolist()
                cols["period"] += [one.origin + step if step <= rows else None for step in range(1, h + 1) for _ in levels]
                cols["span"] += [None] * n
                cols["column"] += [target] * n
                cols["level"] += np.tile(levels, h).tolist()
                cols["value"] += q.reshape(-1).tolist()
                cols["actual"] += np.repeat(actual, len(levels)).tolist()
                cols["pit"] += np.repeat(pits, len(levels)).tolist()
                for span, total in column["totals"].items():
                    summed = float(actual[:span].sum()) if span <= ahead and np.isfinite(actual[:span]).all() else np.nan
                    m = len(levels)
                    cols["member"] += [one.member] * m
                    cols["origin"] += [one.origin] * m
                    cols["step"] += [span] * m
                    cols["period"] += [one.origin + span if span <= rows else None] * m
                    cols["span"] += [span] * m
                    cols["column"] += [target] * m
                    cols["level"] += levels.tolist()
                    cols["value"] += np.asarray(total).reshape(-1).tolist()
                    cols["actual"] += [summed] * m
                    cols["pit"] += [np.nan] * m
        table = pa.table(
            {
                "member": pa.array(cols["member"], type=pa.string()),
                "origin": wire.taken(periods, cols["origin"]),
                "step": pa.array(cols["step"], type=pa.int64()),
                "period": wire.taken(periods, cols["period"]),
                "span": pa.array(cols["span"], type=pa.int64()),
                "column": pa.array(cols["column"], type=pa.string()),
                "level": pa.array(cols["level"], type=pa.float64()),
                "value": pa.array(np.asarray(cols["value"], dtype=np.float64), from_pandas=True),
                "actual": pa.array(np.asarray(cols["actual"], dtype=np.float64), from_pandas=True),
                "pit": pa.array(np.asarray(cols["pit"], dtype=np.float64), from_pandas=True),
            }
        )
        for e in errors:
            telemetry.item_failed(caller, "/forecast", e["error"])
        answer: dict[str, Any] = {"errors": errors, "coverage": coverage.held(plan.grain, plan.horizon, plan.spans, plan.levels)}
        assumptions = [coverage.basis(plan.grain)] if answer["coverage"] else []
        if plan.spans:
            assumptions.append(forecast.dependence(plan.grain))
        if assumptions:
            answer["assumptions"] = assumptions
        return table, answer


# -- /misfit ------------------------------------------------------------------


@dataclass
class _MisfitPlan:
    x: np.ndarray
    names: list[str]
    categorical: list[int]
    cells: bool
    folds: int


class Misfit:
    """Anomaly detection over one frame: the chain-rule density per
    cell (`kernels.misfit`), and what is served is how far each cell
    lies below what is typical for its column (`kernels.strangeness`):
    `score` per row is its lowest cell, and with `cells` every cell
    beside it. With `folds`, each row is scored from a context it is
    not in.

    The frame is every column of the table: a frame has no member,
    period or status of its own, and a column so named is a column
    like any other. A column of text or booleans is a category;
    `categorical` names, by column, further columns to take as
    categories whatever their values (an account number, a code). A
    null is a missing value, as is empty text in a column of text."""

    @staticmethod
    def parse(table: pa.Table, spec: dict[str, Any]) -> _MisfitPlan:
        cells = spec.get("cells", False)
        if not isinstance(cells, bool):
            raise _spec("cells", "`cells` is true or false")
        folds = spec.get("folds", 1)
        if not isinstance(folds, int) or isinstance(folds, bool) or not 1 <= folds <= 10:
            raise _spec("folds", "`folds` is a whole number from 1 to 10")
        names = list(table.column_names)
        if len(names) < 2 or table.num_rows < 2:
            raise Refusal(f"the frame is {table.num_rows} rows by {len(names)} columns: a read needs two of each")
        named = spec.get("categorical", [])
        if not isinstance(named, list) or any(not isinstance(c, str) for c in named):
            raise _spec("categorical", "`categorical` is a list of column names")
        for c in named:
            if c not in names:
                raise _spec("categorical", f"`categorical`: the frame has no column `{c}`")
        categorical = {names.index(c) for c in named} | {j for j, c in enumerate(names) if wire.is_category(table.schema.field(c))}
        x = np.full((table.num_rows, len(names)), np.nan)
        for j, name in enumerate(names):
            if j in categorical:
                words = wire.categories(table, name) if wire.is_category(table.schema.field(name)) else np.array(["" if v is None else str(v) for v in wire.clean(table.column(name).to_pylist())], dtype=object)
                codes = {key: i for i, key in enumerate(sorted({w for w in words if w != ""}))}
                x[:, j] = [np.nan if w == "" else codes[w] for w in words]
            else:
                x[:, j] = wire.numeric(table, name)
        return _MisfitPlan(x, names, sorted(categorical), cells, folds)

    @staticmethod
    async def serve(own: Owner, caller: str, plan: _MisfitPlan) -> tuple[pa.Table, dict]:
        x = plan.x
        # The chain rule fits a conditional per column per ordering: the frame, that many times — per fold.
        work = 2 * x.shape[1] * x.size * plan.folds
        waiting = own.exclusive(caller, "misfit", work, lambda k: k.misfit(x, columns=True, folds=plan.folds, categorical=plan.categorical))
        _logs, shares = await asyncio.wrap_future(waiting)
        scores, cells = kernels.strangeness(np.asarray(shares, dtype=np.float64))
        columns = {"row": pa.array(np.arange(x.shape[0]), type=pa.int64()), "score": pa.array(scores, from_pandas=True)}
        if plan.cells:
            for j, name in enumerate(plan.names):
                columns[name] = pa.array(cells[:, j], from_pandas=True)
        return pa.table(columns), {"errors": [], "assumptions": [kernels.floor_assumption()]}


# -- /whatif ------------------------------------------------------------------


@dataclass
class _WhatifPlan:
    table: pa.Table
    targets: list[str]
    levels: list[float]
    changed: list[str]  # the inputs that differ between the plan and the scenario, in the table's order
    members: list[whatif.Member]
    rows: list[tuple[np.ndarray, np.ndarray, np.ndarray]]  # per member, the table's rows of its held-back periods, its plan and its scenario
    errors: list[dict] = field(default_factory=list)


def _stamps(table: pa.Table) -> np.ndarray:
    """`period` as timestamps: the forecaster builds the calendar from them."""
    column = table.column("period")
    kind = column.type
    dated = pa.types.is_date(kind) or pa.types.is_timestamp(kind)
    if not dated and not (pa.types.is_string(kind) or pa.types.is_large_string(kind)):
        raise Refusal(f"/whatif needs `period` as a date or a timestamp ({kind} here): the calendar is read from it")
    try:
        cast = column.cast(pa.timestamp("ms")) if not (pa.types.is_timestamp(kind) and kind.tz) else column.cast(pa.timestamp("ms", tz=kind.tz))
        return np.asarray(cast.to_numpy(zero_copy_only=False)).astype("datetime64[ms]")
    except (pa.ArrowException, TypeError, ValueError) as e:
        raise Refusal(f"/whatif needs `period` as a date or a timestamp: {e}")


def _input(table: pa.Table, name: str) -> np.ndarray:
    """An input as numbers: a yes-or-no column as 1 and 0, a text
    column as whether there is an entry (as the tests read a holiday's
    name)."""
    kind = table.schema.field(name).type
    if pa.types.is_boolean(kind):
        return np.asarray(table.column(name).cast(pa.float64()).to_numpy(zero_copy_only=False), dtype=np.float64)
    if wire.is_category(table.schema.field(name)):
        return (wire.categories(table, name) != "").astype(np.float64)
    return wire.numeric(table, name)


class Whatif:
    """Which way a column moves under a changed input, by TabICL's
    forecaster and the member's own history (`whatif.py`,
    `docs/contract.md`).

    The table's actual rows are a member's history. The periods ahead
    come twice: as planned (`plan`) and under the change (`scenario`),
    the same periods in both, one input or several differing. Every
    target is answered on its own: the model's quantiles for the last
    periods of the history held back, for the plan and for the
    scenario; the history's own estimate of the scenario with its
    interval, the sum of what each changed input does; the sign, where
    the history and the model agree, with the reason."""

    @staticmethod
    def parse(table: pa.Table, spec: dict[str, Any]) -> _WhatifPlan:
        targets = spec.get("targets")
        if not isinstance(targets, list) or not targets or any(not isinstance(t, str) for t in targets):
            raise _spec("targets", "`targets` is a non-empty list of the columns to answer for")
        for t in targets:
            if t in wire.RESERVED or t not in table.column_names:
                raise _spec("targets", f"`targets`: the table has no value column `{t}`")
            if wire.is_category(table.schema.field(t)):
                raise _spec("targets", f"`targets`: column `{t}` holds text, and the what-if needs numbers")
        if len(set(targets)) != len(targets):
            raise _spec("targets", "`targets` names a column twice")
        levels = _levels(spec)

        table, groups = wire.by_member(table, "/whatif", twice=True)
        status = wire.status(table)
        if (status == "forecast").any():
            raise _spec("status", "`status`: /whatif takes `actual` rows, and the periods ahead as `plan` and as `scenario`")
        stamps = _stamps(table)
        inputs = [c for c in wire.value_columns(table) if c not in targets]
        if not inputs:
            raise Refusal("/whatif needs an input: a value column that is not a target")
        values = {t: wire.numeric(table, t) for t in targets} | {c: _input(table, c) for c in inputs}
        errors: list[dict] = []
        members: list[whatif.Member] = []
        rows: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        for member, s, e in groups:
            at = {name: s + np.flatnonzero(status[s:e] == name) for name in ("actual", "plan", "scenario")}
            for name, where in at.items():
                if len(where) > 1 and (np.diff(stamps[where]) <= np.timedelta64(0, "ms")).any():
                    raise Refusal(f"member `{member}` has two `{name}` rows of one period")
            actual, planned, changed = at["actual"], at["plan"], at["scenario"]
            if not len(actual):
                errors.append({"member": member, "error": "no actual rows to read"})
                continue
            if not len(planned) or len(planned) != len(changed) or (stamps[planned] != stamps[changed]).any():
                raise Refusal(f"member `{member}`: the periods ahead come twice, as `plan` and as `scenario`, the same periods in both")
            if stamps[planned][0] <= stamps[actual][-1]:
                raise Refusal(f"member `{member}`: a period ahead that is not after its last actual row — the history comes first")
            h = len(planned)
            actual = actual[-whatif.CONTEXT :]
            if len(actual) < whatif.MIN_HISTORY + h:
                errors.append({"member": member, "error": f"{len(actual)} actual periods: {h} ahead need {whatif.MIN_HISTORY + h} behind them"})
                continue
            members.append(
                whatif.Member(member, stamps[actual], stamps[planned], {t: values[t][actual] for t in targets}, {c: values[c][actual] for c in inputs},
                              {c: values[c][planned] for c in inputs}, {c: values[c][changed] for c in inputs})  # fmt: skip
            )
            rows.append((actual[-h:], planned, changed))

        def differs(c: str) -> bool:
            return any(not ((m.plan[c] == m.scenario[c]) | (np.isnan(m.plan[c]) & np.isnan(m.scenario[c]))).all() for m in members)

        moved = [c for c in inputs if differs(c)]
        if members and not moved:
            raise Refusal("the `plan` and `scenario` rows hold the same inputs: nothing is changed", 422, [{"field": "status", "error": "no input differs between `plan` and `scenario`"}])
        return _WhatifPlan(table, targets, levels, moved, members, rows, errors)

    @staticmethod
    async def serve(own: Owner, caller: str, plan: _WhatifPlan) -> tuple[pa.Table, dict]:
        # The median is always read: the shift and the history's estimate are taken from it.
        quantiles = sorted(set(plan.levels) | {whatif.MEDIAN})
        asked, median = [quantiles.index(a) for a in plan.levels], quantiles.index(whatif.MEDIAN)
        todo = [(i, t) for i in range(len(plan.members)) for t in plan.targets]
        jobs = [job for i, t in todo for job in whatif.jobs(plan.members[i], t)]
        errors = list(plan.errors)
        got: list = []
        if jobs:
            work = sum((len(j.stamps) + len(j.ahead)) * (len(j.inputs) + 1) for j in jobs)
            got = await asyncio.wrap_future(own.exclusive(caller, "whatif", work, lambda k: k.series(jobs, quantiles)))

        def read() -> list[tuple[int, str, list[np.ndarray], dict]]:
            starts = whatif.places(np.random.default_rng(0))
            out = []
            for n, (i, t) in enumerate(todo):
                three = got[3 * n : 3 * n + 3]
                failed = next((q for q in three if isinstance(q, Exception)), None)
                if failed is not None:
                    errors.append({"member": plan.members[i].name, "column": t, "error": str(failed)})
                    continue
                held, planned, changed = three
                out.append((i, t, three, whatif.reading(plan.members[i], t, plan.changed, held[:, median], planned[:, median], changed[:, median], starts)))
            return out

        periods = plan.table.column("period")
        names = ("member", "period", "column", "status", "level", "value", "actual", "history_value", "history_low", "history_high", "sign", "confidence", "basis", "explains", "error")
        cols: dict[str, list] = {name: [] for name in names}
        parts: list[dict] = []  # per member, column and changed input: what that input alone does, by the history
        for i, t, three, said in await run_in_threadpool(read):
            member = plan.members[i]
            parts += [{"member": member.name, "column": t} | part for part in said["parts"]]
            h, m = len(member.ahead), len(asked)
            recorded = member.targets[t][-h:]
            nothing = np.full(h, np.nan)
            for status, q, where in zip(("held_back", "plan", "scenario"), three, plan.rows[i]):
                changed = status == "scenario"
                cols["period"] += np.repeat(where, m).tolist()
                cols["status"] += [status] * (h * m)
                cols["level"] += np.tile(np.asarray(plan.levels), h).tolist()
                cols["value"] += q[:, asked].reshape(-1).tolist()
                cols["actual"] += np.repeat(recorded if status == "held_back" else nothing, m).tolist()
                for name in ("history_value", "history_low", "history_high"):
                    cols[name] += np.repeat(said[name] if changed else nothing, m).tolist()
            n = 3 * h * m
            cols["member"] += [member.name] * n
            cols["column"] += [t] * n
            cols["sign"] += [said["sign"]] * n
            cols["confidence"] += [whatif.CONFIDENCE[said["sign"]]] * n
            cols["basis"] += [said["basis"]] * n
            cols["explains"] += [said["explains"]] * n
            cols["error"] += [said["error"]] * n
        text = ("member", "column", "status", "sign", "confidence", "basis")
        table = pa.table(
            {name: wire.taken(periods, cols[name]) if name == "period" else pa.array(cols[name], type=pa.string()) if name in text else pa.array(np.asarray(cols[name], dtype=np.float64), from_pandas=True) for name in names}
        )
        for e in errors:
            telemetry.item_failed(caller, "/whatif", e["error"])
        return table, {"errors": errors, "changed": plan.changed, "parts": parts, "assumptions": whatif.assumptions()}


async def healthz(_: Request) -> Response:
    """Alive, and what the owner has done: `owner` carries its counters
    and the time of its last cycle, for a probe that wants more than the port."""
    k = kernels.peek()
    own = _OWNER
    return JSONResponse(
        {
            "status": "ok",
            "service": SERVICE,
            "version": VERSION,
            "device": k.device if k else None,
            "loaded": k is not None,
            "checkpoints": kernels.CHECKPOINTS,
            "owner": None if own is None else {**own.stats, "waiting_cells": own._cells},
        }
    )


app = telemetry.asgi(
    Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/forecast", _door(Forecast), methods=["POST"]),
            Route("/misfit", _door(Misfit), methods=["POST"]),
            Route("/whatif", _door(Whatif), methods=["POST"]),
        ]
    )
)
