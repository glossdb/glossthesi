"""The wire: one table and a specification in, one table and an
answer object out, in two encodings (`docs/contract.md`).

An Arrow IPC stream carries the table with its column names and
types, and the specification as JSON in the schema's metadata under
`spec`; the answer goes back the same way, the answer object under
`answer`. JSON carries the same table column by column beside the
specification, a null where a value is missing, and the answer in
the same shape. The answer is in the request's encoding; a refusal
is always JSON.

The table's reserved columns are `member`, `period` and `status`.
Every other column is a value: numbers read as float64 with a null
as missing, and a column of text or booleans is a category."""

from __future__ import annotations

import datetime
import io
import json
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

ARROW = "application/vnd.apache.arrow.stream"
JSON = "application/json"
SPEC, ANSWER = b"spec", b"answer"
RESERVED = ("member", "period", "status")
STATUSES = ("actual", "plan", "forecast", "scenario")


class Refusal(Exception):
    """A request the service will not serve: the reason, the status,
    and for a specification that does not fit the table the fields an
    agent has to repair."""

    def __init__(self, message: str, status: int = 400, errors: list[dict] | None = None):
        super().__init__(message)
        self.status, self.errors = status, errors


@dataclass
class Request:
    table: pa.Table
    spec: dict[str, Any]
    encoding: str  # ARROW or JSON


def parse(content_type: str, raw: bytes, cap: int | None = None) -> Request:
    """The body as a table and a specification, by its content type.
    `cap` bounds what an Arrow stream decodes to, in bytes: its batches
    are read one at a time and the body is refused with a 413 once they
    pass it, since a compressed stream may be many times its wire size.
    One batch is decoded whole before it is counted."""
    media = content_type.split(";")[0].strip().lower()
    if media == ARROW:
        try:
            reader = pa.ipc.open_stream(raw)
            batches, size = [], 0
            for batch in reader:
                size += batch.nbytes
                if cap is not None and size > cap:
                    raise Refusal(f"the table decodes to more than {cap >> 20} MB — send it in parts", 413)
                batches.append(batch)
            table = pa.Table.from_batches(batches, reader.schema)
        except (pa.ArrowException, ValueError) as e:
            raise Refusal(f"the body is not an Arrow IPC stream: {e}") from e
        meta = table.schema.metadata or {}
        if SPEC not in meta:
            raise Refusal("the schema's metadata holds no `spec`: the specification goes there as JSON")
        spec = _object(meta[SPEC], "`spec`")
        table = table.replace_schema_metadata(None)
        encoding = ARROW
    elif media in (JSON, ""):
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise Refusal(f"the body is not JSON: {e}") from e
        if not isinstance(body, dict):
            raise Refusal("the body is a JSON object with `spec` and `table`")
        spec = body.get("spec")
        if not isinstance(spec, dict):
            raise Refusal("`spec` is a JSON object: what to do with the table")
        table = _from_json(body.get("table"))
        encoding = JSON
    else:
        raise Refusal(f"the content type is {ARROW} or {JSON}, not {media!r}")
    if table.num_columns == 0 or table.num_rows == 0:
        raise Refusal("the table holds no rows")
    names = table.column_names
    if len(set(names)) != len(names):
        raise Refusal("the table names a column twice")
    return Request(table, spec, encoding)


def _object(text: bytes | str, name: str) -> dict:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise Refusal(f"{name} is not JSON: {e}") from e
    if not isinstance(value, dict):
        raise Refusal(f"{name} is a JSON object")
    return value


def _from_json(columns: Any) -> pa.Table:
    if not isinstance(columns, dict) or not columns:
        raise Refusal("`table` is an object of columns, each a list of values of one length")
    lengths = set()
    arrays = {}
    for name, values in columns.items():
        if not isinstance(values, list):
            raise Refusal(f"`table`: column `{name}` is a list of values")
        lengths.add(len(values))
        try:
            arrays[name] = pa.array(values)
        except (pa.ArrowException, TypeError, ValueError) as e:
            raise Refusal(f"`table`: column `{name}` mixes kinds of values: {e}") from e
    if len(lengths) != 1:
        raise Refusal("`table`: every column holds the same number of values")
    return pa.table(arrays)


def encode(table: pa.Table, answer: dict[str, Any], encoding: str) -> tuple[bytes, str]:
    """The answer as bytes and its media type, in the request's encoding."""
    if encoding == ARROW:
        out = io.BytesIO()
        with pa.ipc.new_stream(out, table.schema.with_metadata({ANSWER: json.dumps(clean(answer), allow_nan=False)})) as writer:
            writer.write_table(table.replace_schema_metadata(None))
        return out.getvalue(), ARROW
    body = {"table": {name: clean(table.column(name).to_pylist()) for name in table.column_names}, "answer": clean(answer)}
    return json.dumps(body, allow_nan=False).encode(), JSON


def clean(value: Any) -> Any:
    """Arrays to lists, non-finite floats to null, dates and times to
    their ISO text: JSON has no NaN and no date. A table's period of
    any type can be named in the answer object (an error names its
    origin)."""
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, np.datetime64):
        return str(value)
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (float, np.floating)):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


# -- reading the table ----------------------------------------------------------


def is_category(field: pa.Field) -> bool:
    t = field.type
    if pa.types.is_dictionary(t):
        t = t.value_type  # a dictionary-encoded column is its values' kind
    return pa.types.is_string(t) or pa.types.is_large_string(t) or pa.types.is_string_view(t) or pa.types.is_boolean(t)


def value_columns(table: pa.Table) -> list[str]:
    return [name for name in table.column_names if name not in RESERVED]


def _plain(column: pa.ChunkedArray) -> pa.ChunkedArray:
    """A dictionary-encoded column as its values."""
    return column.cast(column.type.value_type) if pa.types.is_dictionary(column.type) else column


def numeric(table: pa.Table, name: str) -> np.ndarray:
    """A column as float64, NaN where a value is missing."""
    column = table.column(name)
    if pa.types.is_null(column.type):
        return np.full(table.num_rows, np.nan)
    if is_category(table.schema.field(name)):
        raise Refusal(f"column `{name}` holds text, not numbers")
    try:
        cast = _plain(column).cast(pa.float64())
    except (pa.ArrowException, TypeError) as e:
        raise Refusal(f"column `{name}` is not numeric ({column.type}): {e}") from e
    return np.asarray(cast.to_numpy(zero_copy_only=False), dtype=np.float64)


def categories(table: pa.Table, name: str) -> np.ndarray:
    """A column as text, empty where a value is missing."""
    column = table.column(name)
    if pa.types.is_null(column.type):
        return np.full(table.num_rows, "", dtype=object)
    try:
        cast = _plain(column).cast(pa.string())
    except (pa.ArrowException, TypeError) as e:
        raise Refusal(f"column `{name}` cannot be read as text ({column.type}): {e}") from e
    return np.array(["" if v is None else v for v in cast.to_pylist()], dtype=object)


def status(table: pa.Table) -> np.ndarray:
    """Per row, `actual`, `plan`, `forecast` or `scenario`; every row `actual` without the column."""
    if "status" not in table.column_names:
        return np.full(table.num_rows, "actual", dtype=object)
    got = categories(table, "status")
    bad = sorted({v for v in got if v not in STATUSES})
    if bad:
        raise Refusal(f"`status` is one of {', '.join(STATUSES)}; the table holds {', '.join(repr(v) for v in bad[:3])}")
    return got


def by_member(table: pa.Table, route: str, twice: bool = False) -> tuple[pa.Table, list[tuple[str, int, int]]]:
    """The table with a member's rows together and in period order, and
    per member its name and its rows as [start, end). A member has one
    row per period, unless the route takes a period `twice` (the
    what-if: as planned and under the scenario) and checks that itself."""
    for name in ("member", "period"):
        if name not in table.column_names:
            raise Refusal(f"{route} needs a `{name}` column")
    if pa.types.is_null(table.column("period").type):
        raise Refusal("`period` holds no values")
    if not is_category(table.schema.field("member")):
        try:
            table = table.set_column(table.schema.get_field_index("member"), "member", table.column("member").cast(pa.string()))
        except (pa.ArrowException, TypeError) as e:
            raise Refusal(f"`member` cannot be read as text ({table.column('member').type}): {e}") from e
    try:
        table = table.sort_by([("member", "ascending"), ("period", "ascending")])
    except pa.ArrowException as e:
        raise Refusal(f"the rows cannot be ordered by `member` and `period`: {e}") from e
    members = categories(table, "member")
    if (members == "").any():
        raise Refusal("`member` is missing on some rows")
    starts = np.flatnonzero(np.concatenate([[True], members[1:] != members[:-1]]))
    ends = np.concatenate([starts[1:], [len(members)]])
    periods = table.column("period")
    if periods.null_count:
        raise Refusal("`period` is missing on some rows")
    if len(members) > 1 and not twice:
        same = np.asarray(pc.equal(periods.slice(1), periods.slice(0, len(members) - 1)).to_numpy(zero_copy_only=False), dtype=bool)
        twice = np.flatnonzero(same & (members[1:] == members[:-1]))
        if twice.size:
            i = int(twice[0])
            raise Refusal(f"member `{members[i]}` has two rows of period {periods[i].as_py()!r}")
    return table, [(str(members[s]), int(s), int(e)) for s, e in zip(starts, ends)]


def taken(column: pa.ChunkedArray, indices: list[int | None]) -> pa.Array:
    """The column's values at `indices`, null where an index is None."""
    return pc.take(column.combine_chunks(), pa.array(indices, type=pa.int64()))
