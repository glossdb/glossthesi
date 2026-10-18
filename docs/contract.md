# The contract: what crosses the wire

The service's API, as glossql and any other caller meet it. Written
2026-09-30. Every route takes one table and
a specification, and answers one table with what produced it. The
what-if route was agreed and built on 2026-10-02
(`docs/whatif.md`).

## One table, two encodings

A request is one table and a specification. The table has a row per
member and period and a column per value; the specification says
what to do with it. An answer is one table and an object that names
what produced it and what failed.

| Encoding | Request | Answer |
|---|---|---|
| `application/vnd.apache.arrow.stream` | An Arrow IPC stream: one schema, one or more record batches. The schema's metadata holds the specification as JSON under the key `spec`. | An Arrow IPC stream. The schema's metadata holds the answer object as JSON under the key `answer`. |
| `application/json` | `{"spec": {…}, "table": {"member": […], "period": […], …}}`: the table column by column, a `null` where a value is missing. | `{"table": {…}, "answer": {…}}`, the table column by column. |

The two encodings carry the same table and the same specification,
and answer the same. Arrow is what glossql sends: column names and
types travel with the rows, and nothing is parsed twice. JSON is for
small bodies and for reading by eye. The answer is in the request's
encoding. A refusal is always JSON: `{"error": "…"}` with a 4xx, and
where the specification is at fault, `{"error": "…", "errors":
[{"field": "…", "error": "…"}]}` naming the field an agent has to
repair, the first found.

The body is at most 32 MB (`GLOSSTHESI_MAX_BODY_MB`).

## The table

| Column | Type | Meaning |
|---|---|---|
| `member` | string | The series, or the member of the dimension the rows are by: a metric's name, a segment, a store. Required by `/forecast`; a frame column for `/misfit`. |
| `period` | string, date, timestamp or integer | The period of the row. The service orders a member's rows by it and never parses it: the calendar is the caller's, and `grain` in the specification says what a period is. A string period has to sort in time order as text (`2026-07`, `2026-W27`), or it is sent as a date. A member with two rows of one period is refused. Required by `/forecast`; a frame column for `/misfit`. |
| `status` | string | `actual`, `plan`, `forecast` or `scenario`. Absent, every row is `actual`. What a row's status means is in each route below; a frame column for `/misfit`. |
| every other column | numeric, or string or boolean for a category | A value per row. Numbers are read as float64, a null as missing. A string or boolean column is a category: a covariate for `/forecast`, a categorical column for `/misfit`. |

Nothing in the table names a dataset, a metric's meaning or an
actor. The caller keeps the names and reads them back from the
answer, where every row carries its `member`, `period` and `column`.

## The answer object

Under `answer` in every encoding:

| Field | Meaning |
|---|---|
| `service` | The service's name: `glossthesi`. |
| `version` | The build: `<version>+<commit>` from the image, `dev` on a laptop. |
| `errors` | The items that failed alone, each with what names it (`member`, `column`, `origin`) and `error` in words. Empty when nothing failed. |
| `assumptions` | What the answer rests on that the model did not produce: per entry the `field` it bears on, the value, and its `basis` in words (`measured: …` with the finding, or `assumed: …`). Absent when the answer rests on nothing but the model. |

A route may add fields of its own; they are listed below.

## `/forecast`: the periods ahead, or the walk

Chronos-2, as its package ships it, the series of a request forecast
together in groups of one shape (`docs/forecast.md`, GK-26).

**The table.** Rows with status `actual` are the history. Rows of
any other status after a member's last actual row are the periods
ahead, in order: they carry the values of the covariates known
ahead, and the target's values in them are ignored. They are
optional: without them the periods ahead have no labels and the
answer's `period` is null there.

**The specification.**

| Field | Meaning | Absent |
|---|---|---|
| `targets` | The columns to forecast. Every other value column is a covariate: its past values ride with the history, and it is known ahead where the member's rows past the last actual hold it for the whole horizon. Where a member has rows ahead that do not (a missing value, fewer rows than the horizon), the covariate informs the forecast over the history only, and `assumptions` says so for that member and column (`field` `covariates`). | required |
| `horizon` | Periods ahead, 1 to 1024. | required, except with `origins`, where it is 1 |
| `levels` | The quantile levels asked for, each between 0.01 and 0.99, the model's own grid; a level outside it is refused, not answered with the grid's edge under its name. | required |
| `totals` | Spans in periods: for each, the quantiles of the total of the first so many periods come back beside the periods' own. The total's interval combines the periods' intervals with a dependence per grain; the answer's `assumptions` names the value and on what data it was measured (`docs/forecast.md`, GK-45, GK-48). | none |
| `grain` | `month`, `week`, `day` or `quarter`: only the interval of a total depends on it, and `coverage` reads the grain's table. Every grain's dependence is measured, and the answer says on what data (GK-45, GK-48). | `month` |
| `width` | The distance of every quantile from the median is multiplied by it after the model has answered, for a caller whose record says this dataset's intervals run too narrow or too wide: one number, or one per period ahead. The rule for learning it from a record is `glossthesi.forecast.learned_width` (GK-35). | 1 |
| `origins` | The walk, or a backtest: a number k, the k actual periods before every member's last each taken as an origin, so that every period walked has its actual value; or a list of periods, the same origins for every member. From each origin the target is forecast `horizon` periods ahead from the history up to and including it, with the covariates' later values taken as known ahead. Where the table holds the target's actual value for a period forecast, the answer carries it and the PIT. | one forecast per member, from its last actual period |

**The answer table.** One row per member, origin, step, column and
level.

| Column | Meaning |
|---|---|
| `member` | The member. |
| `origin` | The period the history ends at, in the table's own type. |
| `step` | Periods ahead of the origin, from 1. For a total, the span. |
| `period` | The period forecast, where the table has a row for it; null otherwise. For a total, the period the span ends at. |
| `span` | Null for a period row; the span for a total row. |
| `column` | The target. |
| `level` | The quantile level. |
| `value` | The quantile. |
| `actual` | The target's value at that period where the table holds it, else null. The same on every level row of the point. |
| `pit` | The quantile at which the actual landed in the model's own distribution, 0 to 1, read against the raw answer before any `width`; null without an actual. The same on every level row of the point. |

A member that cannot be forecast fails alone, and so does one of its
targets and one origin: the failure is in `answer.errors` with the
member, the column and the origin named, and the other rows are
answered. A member needs 6 known values of a target behind an
origin. What the model itself refuses fails the members forecast with
it and no others.

**The answer object** carries `coverage` beside `errors` and
`assumptions`: how often the bands the request's levels form held on
held-out data, at this grain (`docs/forecast.md`, GK-46). One entry
per step of the horizon and per span asked, per band: `step`, `span`
(null for a period), `low` and `high` (the levels that bound the
band), `share` (the actual values inside it), `se` (its standard
error, clustered by series and by origin), `points` (how many the
share counts) and `datasets` (how many held-out datasets it pools). A
share with an error of 0.05 and one with 0.01 are not the same
knowledge, and a caller's rule reads both numbers. The bands measured are 0.10 to 0.90 and 0.05 to
0.95; a band the levels do not form, a step past the table (12
months, 13 weeks, 31 days, 4 quarters) and a span at a dependence
other than the one it was measured at have no entry. The spans of
days and quarters stand at a dependence fitted on the same datasets
(GK-48), so their shares are fits, not held-out readings;
`assumptions` names the basis. The shares are pooled over datasets: a
caller whose own record of forecasts and actual values is long enough
reads that first.

**The walk as glossql declares it.** One member per metric, one
value column, no covariates, `origins` 6, `horizon` 1, the levels of
the bands. The answer holds per metric and walked period the bands,
the actual and the PIT. The record of PITs stays on glossql's side;
the service reads no record. A dataset whose record says the bands
run narrow sends a `width` (GK-35: on records of one to two years it
does not pay yet).

## `/misfit`: errors that break a relation between columns

TabICL, the chain-rule density per cell (`docs/misfit.md`, GK-37,
GK-38). The frame is every column of the table: a frame has no
member, period or status of its own, and a column so named (an
invoice's status) is a column like any other.

**The specification.**

| Field | Meaning | Absent |
|---|---|---|
| `categorical` | Columns to take as categories whatever their values: account numbers, codes. A string or boolean column is a category anyway. | none |
| `cells` | Whether to answer per cell as well as per row. | false |
| `folds` | 2 to 10: every row is scored from a context it is not in, at that many times the fits. | 1 |

**The answer table.** One row per frame row, in the frame's order.

| Column | Meaning |
|---|---|
| `row` | The row's position in the frame, from 0. |
| `score` | How far the row's strangest cell lies below what is typical for that cell's column, in units of the column's spread or in nats where the spread is under a nat. Lower is stranger; around zero and above is ordinary. The floor of one nat on a column's spread is a constant, declared in the answer's `assumptions` (`docs/misfit.md`, GK-38). |
| one column per frame column, with `cells` | The same per cell. The row's lowest cell names the column to look at: right, or one the error touches, in 0.97 of the injected errors on our tests (GK-21). |

A frame needs two rows and two columns. No threshold is served: the
caller ranks. The scope is errors that break a relation between the
columns of one table; the caller's join decides what is checked.
General anomaly detection is another field (GK-23) and is not
offered.

## `/whatif`: which way a column moves under a changed input

Agreed and built on 2026-10-02. What it rests on is in
`docs/whatif.md`: five checkpoints on thirty real tables and an
instance space analysis.

TabICL's forecaster as its package ships it, every member on its
own. The route promises a direction, not a size:

- **The direction** is stated where two estimates agree: the
  member's own history shows the column higher where the input was
  higher, the calendar and the other inputs held, and the model
  moves it the same way. It is an association in that history, read
  for the same period and the period before: the input's value one
  period back is held beside it, so what the column does in the
  period after the input moves has its own slope, and a sustained
  change's effect is the sum of the two (since 2026-10-17). The two
  estimates read the same rows with
  the same features, so their agreement is one piece of evidence;
  that nothing which set the input also moved the column is the
  caller's assumption, declared in `assumptions`. No table records
  what would have happened under an input that was not changed, so
  this agreement is the evidence there is, with one exception: on a
  real device whose causal graph is known (a wind tunnel, 2026-10-17),
  every sign stated was right and 3% of the sensors the lever cannot
  touch got one (`docs/whatif.md`, against a truth). On 3,737 tested series-instances where
  the history showed an effect, the model agreed with its direction
  on 94%; on a source judged only after the rule was fixed, on 87
  to 100%.
- **The size is a guess between two estimates:** the model's and
  the history's. In the same tests the model's shift was within
  half to one and a half times the history's on 47%, and under half
  of it on another 47%. No shape of table is known under which the
  size can be trusted on a new table.
- **The error** is measured on the caller's own table: the last
  periods of the history are held back, predicted and compared.

**The table.** Rows with status `actual` are the history. The
periods ahead come twice: once with the inputs as planned (status
`plan`) and once with the change (status `scenario`), the same
periods in both. The service applies no factors: the caller writes
the changed values. `period` is a date or a timestamp (in the JSON
encoding, text such as `2026-07-06`), since the forecaster builds
the calendar from it. A timestamp with a time zone is read in UTC:
the weekday and the hour the history's reading holds are UTC's. A
member has one row per period and status.
A member needs, behind the periods ahead, 26 actual periods or as
many as the history's reading holds columns plus eight, whichever is
more: with two inputs, both changed, 26 on a monthly or weekly table,
26 on a daily one, 48 on an hourly one (the level, the trend, the
year's cycle, the weekday, the hour, every input, and one lag of each
changed input, which also costs the first row); a member with fewer
fails alone and the error names the number. Its last 4,096 are read.
A target's values in the rows ahead are ignored.

An input is a number; a yes-or-no column is read as 1 and 0, and a
text column as whether there is an entry (a holiday's name).

An empty cell of a number or of a yes-or-no column is unknown to the
service, not zero and not no. Where empty means something (no
markdown that week), the caller fills it before sending. An input
that is empty in more than half of a member's history is not read:
asked about, it gets `?`; otherwise it is left out of what the
history's reading holds, and `basis` names it. A row with a gap in an
input that is read is left out of that reading.

One input or several differ between `plan` and `scenario`; a
request where none differs is refused. Several inputs changed at once
are read together (built 2026-10-16): the history's estimate is the
sum of what each does, from the one regression that holds every
input, with its interval from the same draws; an interaction between
two inputs is not something the history reads, so a model that read
one would show as a disagreement.

**The specification.**

| Field | Meaning | Absent |
|---|---|---|
| `targets` | Every column the caller wants an answer for. Each is answered from its own history, also one that is defined from others (revenue from price and units): nothing is carried from one column to another, so no definition has to cross the wire and none is recomputed by the caller. Every other value column is an input known ahead. | required |
| `levels` | The quantile levels asked for, each strictly between 0 and 1. | required |

**The answer object** carries `changed`, the inputs that differ, as
a list in the table's order, and `parts`, beside `errors` and
`assumptions`: per member, column and changed input, what that input
alone does to the column over the periods ahead by the member's
history (`history_shift`, the mean over the periods, the lag's part
included, with `history_low` and `history_high`, its 90% interval),
from the same regression as the joint estimate, so that the parts'
estimates add up to it; their intervals do not add, and the joint
interval is the one to read. The joint regression holds the lag of
every changed input, so a part differs from the single-input reading
of the same lever by a little. A user reads the single levers and the pair in one
picture: which lever carries the effect, and when two cancel.

Several changed inputs are also checked together: each may lie
inside its own range while the history never held the combination.
Where the scenario's combination of the changed inputs lies farther
from every history row than the distance within which 99 of 100
history rows find a neighbour in that space, each input scaled by its
spread, in more than a tenth of the periods ahead, the route states
no sign and `basis` says whether the inputs as planned or the change
go beyond together. For switch inputs that is whether the two have
been on together before. The reach and the share are declared in
`assumptions`.

**The answer table.** One row per member, column, status, period
and level.

| Column | Meaning |
|---|---|
| `member`, `period`, `column` | The member, the period, the target. |
| `status` | `held_back`: the last periods of the history, as many as there are ahead, predicted from the rows before them. `plan`: the periods ahead, inputs as planned. `scenario`: the same under the change. |
| `level`, `value` | The quantile level and the model's quantile. |
| `actual` | On `held_back` rows, what was recorded. The same on every level row of the point. |
| `history_value`, `history_low`, `history_high` | On `scenario` rows: the plan's median plus what the member's own history says the change does at that period, from the change made there and the change made the period before (the input's lag, held since 2026-10-17; nothing from before the first period ahead, whose row before is history), and the 90% interval of that. The other inputs, the trend and the calendar's cycles are held. The same on every level row of the point. |

And per member and column, the same on each of its rows:

| Column | Meaning |
|---|---|
| `sign` | `+` or `-`: the history shows the input, or the inputs together, moving the column, read on the rows and on their changes, the same way, neither 90% interval over zero; and the model's shift goes that way. `0`: the history shows no effect and the model's shift is under a tenth of what the history's regression leaves of the column (its residual spread). `=`: the changed inputs are the same as planned and under the scenario for this member, so nothing is asked of it. `?`: nothing can be said; `basis` says why, naming the input where one of several fails a check. |
| `confidence` | The sign in words: `direction`, `no effect visible`, `not answerable`, `unchanged`. |
| `basis` | The reason, in words; read before the numbers. For `?`: the model and the history disagree on the direction; the input never varied; it cannot be told apart from another input (which); the change, or the input as planned, goes beyond what the history holds; the input is mostly empty; the history is too short to read; the history shows nothing, or shows an effect on one reading only (the rows, or their changes from row to row), and the model moves the column; the history's estimate of the change points both ways over the periods ahead (a change that reverses, or two levers moving in different periods) and nets to nothing over the horizon as a whole. Where it points both ways and the horizon's mean is off zero, the sign is stated for the horizon as a whole and `basis` says so with the counts. |
| `explains` | The share of the column the changed input explains in this member's history, the other inputs and the calendar held; the inputs together, where several are changed. |
| `error` | The error of the level: the mean distance of the held-back median from what was recorded over the periods held back, in the column's own units. It is one window; it says how far the model's level was off, not how far the change is. The held-back rows carry the quantiles and the actual, so a caller counts how many of them fell inside a band. |
| `history_error` | What the history leaves of the column: the spread of what remains once the level, the trend, the calendar's cycles and the inputs are held, in the column's own units. The history's estimate of the change (`history_value`) is read against it, and a tenth of it is the `0` sign's threshold. Null where the history was not read. |

`no effect visible` means that this member's data does not show
one, not that there is none.

**The bracket.** For a period under the scenario, the range
between the model's median and the history's interval (the lowest
and the highest of `value` at the median, `history_low` and
`history_high`). It is a range between two estimates and has no
stated coverage. The model errs low more often than high.

A member that cannot be answered fails alone, and so does one of
its targets: the failure is in `answer.errors`. A read costs three
predictions per target (held back, plan, scenario) and the reading
of the history: six stores of 120 weeks with two targets take about
twelve seconds on a laptop. The route needs the packages of TabICL's
forecaster (`tabicl[forecast]`); a build without them refuses the
read with a 422 that says so. The thresholds above (the 90% interval, a tenth of
what the history leaves of the spread, how far an input must stand apart) are in the answer's
`assumptions`, each with its basis.

**The whole algorithm is here; the caller holds none of it.**
The what-if is not to be split over two code bases (decided
2026-10-02). So:

- **Every number wanted is a target.** Sign, bracket and error come
  per column from that column's own history. The caller builds the
  table and reads the rows.
- **A total is sent as a member:** a series the caller has summed,
  as it sums any series. The service adds nothing up, since only
  the caller knows whether a column adds.
- **Break-even is a use of the answer,** not part of it: a
  threshold the caller compares with the bracket, in SQL.

TabICL reads one column at a time, so two columns tied by a
definition are each read on their own, and their brackets need not
agree with the definition exactly. Where both move the same way,
that they belong together shows in the answer itself.

**What the calling actor may do with the answer** is described in a
skill, not built into the caller (decided 2026-10-02): carry a
bracket through a definition it knows, compare it with a threshold
for a break-even, or sum. The actor may have that knowledge or not;
the contract does not depend on it.

## Statuses

| Status | When |
|---|---|
| 200 | Served. Items that failed alone are in `answer.errors`. |
| 400 | The body, the table or the specification has the wrong shape; the error names what, and `errors` names a specification field at fault. |
| 401 | No bearer this service accepts. |
| 404 | No such route. |
| 413 | The body is over the cap, as sent or as the Arrow stream decodes to (a compressed stream may be many times its wire size): send it in parts. |
| 422 | The service refuses the read: a specification that does not fit the table (`errors` names the field), a frame it cannot score. |
| 429 | The queue is full for this caller; `Retry-After` says how long. |
| 500 | The service failed; the traceback is in its log, not on the wire. The device's memory running out is this, never members that failed alone. |
| 503 | The instance is stopping; `Retry-After` says when to retry. |

`GET /healthz` answers `status`, `service`, `version`, `device`,
`loaded`, `checkpoints` (the TabICL files and the Chronos-2 commit the
build serves) and the owner's counters, outside the gate.

## Authentication and naming

As before: `Authorization: Bearer …`, a Google-signed ID token for
`GLOSSTHESI_AUDIENCE` from one of `GLOSSTHESI_CALLERS`, or one of
`GLOSSTHESI_KEYS`. With neither set every door refuses and the
service does not start; `GLOSSTHESI_INSECURE_OPEN=true` is the one
way around, for a laptop. glossql names a URL per
point (the walk, the misfit); every one names this
service. Nothing on the wire names a point.

## What went

`/bands` is gone with the context cache, the prepared batching of
walk rows and the pinned walk and grid fixtures. Its callers were the
walk, now `/forecast` with `origins`, and the what-if replay grid,
which `/whatif` replaces with the periods ahead sent as planned and
under the scenario.

## For glossql

What each point sends and receives, and what the engine may delete.
The kernel draft's "one general service call" is this contract: a
table in, a table out, Arrow IPC, the service's name and version on
every answer, one URL per point.

**The walk (`metric_bands`, `metric_band_walk`).** Send
`metric_series()` at month grain as the one table: `member` the
metric's name, `period` the month, one value column, every row
`actual`; the specification `{"targets": ["value"], "origins": 6,
"horizon": 1, "levels": [0.05, 0.1, 0.5, 0.9, 0.95]}`. Receive per
metric and walked period the five band values (one row per level),
the `actual` and the `pit`. A metric whose history is too short for
an origin is in `answer.errors` with the reason, and the origins it
does have are walked: the service needs six known values behind an
origin, so a full walk of six needs twelve months and a metric of
eight months gets two walked periods. The `withheld` and `partial`
rules stay glossql's: the service places a tied actual (a series on
a grid) at the midpoint of the tied levels, and says nothing about
the calendar. The record of PITs stays glossql's; the service reads
none. The bands are the model's own; the detector's threshold was
set on TabICL's bands and is re-read against the walk's coverage on
the record (`docs/forecast.md`, GK-42).

May go: the feature recipe and the point-in-time fills in
`crates/session/src/search/bands.rs`, `BandRead` and `band_points`
on the runtime trait, `pit_history` on the wire, `band_grid`, and in
`crates/scripts/src/remote.rs` the JSON matrices: one client sends a
table and a specification and reads a table and an answer.

**The misfit (`misfit.<frame>()`).** Send the frame's rows as the
table, every column as it is (a frame has no reserved names), text
for categories, codes named in `categorical`; `{"cells": true}` to have the column named. Receive
`row`, `score` and a column per frame column. The caps (2,000 rows,
16 columns) stay the door's own; the service refuses only a frame
under two rows or two columns.

**The what-if (`whatif.<scenario>()`).** Send
the table at the grain at which the overridden column varies from
row to row, the history as `actual`, and the periods ahead twice,
as planned and under the plan rewrite, with every concept to be
answered as a target. Receive the rows above and serve them: the
sign, the model's rows, the history's estimate and `basis` per
concept. No what-if logic stays in the door: the grid of strengths
around the declared factor, the support worlds and the reading
across them have no successor there. The exact recomputation at the
declared factor (`replay`) is the door's own arithmetic and not
part of this route; whether it is still served beside the answer is
glossql's call.

**Configuration.** One URL per point, each naming this service;
`GLOSSQL_TABICL_URL` goes. The bearer as today: a key, or an ID
token for the service's audience.

**Two facts the kernel draft has wrong:** the walk's row design has one copy, in Rust, and is not
reproduced; the current service ignores `actual` and `pit_history`,
so the walk's PIT is NaN today.
