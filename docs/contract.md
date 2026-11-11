# The API contract

The service's API, as glossdb and any other caller use it. Every
endpoint takes one table and a specification, and answers one table
and an object that says what produced it. The endpoints serve
glossdb's preview features: forecasts, expected ranges and scenarios.

## One table, two encodings

A request is one table and a specification. The table has a row per
member and period and a column per value. The specification says
what to do with it. An answer is one table and an answer object that
names the service and what failed.

| Encoding | Request | Answer |
|---|---|---|
| `application/vnd.apache.arrow.stream` | An Arrow IPC stream: one schema, one or more record batches. The schema's metadata holds the specification as JSON under the key `spec`. | An Arrow IPC stream. The schema's metadata holds the answer object as JSON under the key `answer`. |
| `application/json` | `{"spec": {…}, "table": {"member": […], "period": […], …}}`: the table column by column, a `null` where a value is missing. | `{"table": {…}, "answer": {…}}`, the table column by column. |

The two encodings carry the same table and the same specification,
and get the same answer. Arrow is what glossdb sends: column names
and types travel with the rows. JSON is for small bodies and for
reading by eye. A request without a content type is read as JSON. The
answer is in the request's encoding.

An error response is JSON in both encodings: `{"error": "…"}` with a
4xx or 5xx status. Where a field of the specification is at fault,
the body is `{"error": "…", "errors": [{"field": "…", "error":
"…"}]}`. It names the first field found at fault, so that an agent
can repair it.

The body is at most 32 MB (`GLOSSTHESI_MAX_BODY_MB`).

## The table

| Column | Type | Meaning |
|---|---|---|
| `member` | string | The series, or the member of the dimension the rows are by: a metric's name, a segment, a store. Required. A column of another type is read as text. |
| `period` | string, date, timestamp or integer | The period of the row. Required. `/forecast` orders a member's rows by it and does not parse it: the calendar is the caller's, and `grain` in the specification says what a period is. A string period has to sort in time order as text (`2026-07`, `2026-W27`), or it is sent as a date. `/whatif` needs a date or a timestamp. A member with two rows of one period gets a 400; `/whatif` takes each period ahead twice, once per status. |
| `status` | string | `actual`, `plan`, `forecast` or `scenario`. Absent, every row is `actual`. What a row's status means is in each endpoint's section below. |
| every other column | numeric, or string or boolean for a category | A value per row. Numbers are read as float64, a null as missing. A string or boolean column is a category: a covariate for `/forecast`, an input for `/whatif`. |

Nothing in the table names a dataset, a metric's meaning or an
actor. The caller keeps the names and reads them back from the
answer, where every row carries its `member`, `period` and `column`.

## The answer object

Under `answer` in both encodings:

| Field | Meaning |
|---|---|
| `service` | The service's name: `glossthesi`. |
| `version` | The build: `<version>+<commit>` from the image, `dev` on a laptop. |
| `errors` | The members, columns or origins that failed on their own, each with what names it (`member`, `column`, `origin`) and `error` in words. Empty when nothing failed. |
| `assumptions` | What the answer rests on that the model did not produce: per entry the `field` it bears on, the value, and its `basis` in words (`measured: …` with the finding, `fitted: …`, or `assumed: …`). Absent when the answer rests on nothing but the model. |

An endpoint may add fields of its own; they are listed below.

## `/forecast`: the periods ahead, or a backtest

The model is Chronos-2, as its package ships it. The series of a
request with the same covariates are forecast together
(`docs/forecast.md`, GK-26). A series at least half of whose known
values are zero is forecast by itself (GK-66); `assumptions` has an
entry for it (`field` `members`) with the number of such series in
the request.

**The table.** Rows with status `actual` are the history. Rows of
any other status after a member's last actual row are the periods
ahead, in order. They carry the values of the covariates known
ahead; the target's values in them are ignored. They are optional:
without them the periods ahead have no labels and the answer's
`period` is null there. A row of another status before a member's
last actual row gets a 400.

**The specification.**

| Field | Meaning | Absent |
|---|---|---|
| `targets` | The columns to forecast. Every other value column is a covariate. Its past values go to the model with the history. It is known ahead where the member's rows past the last actual hold it for the whole horizon. Where a member has rows ahead that do not (a missing value, fewer rows than the horizon), the model uses the covariate over the history only, and `assumptions` has an entry for that member and column (`field` `covariates`). | required |
| `horizon` | Periods ahead, 1 to 1024. | required, except with `origins`, where it is 1 |
| `levels` | The quantile levels asked for, each between 0.01 and 0.99, the model's own grid. A level outside it gets a 400. | required |
| `totals` | Spans in periods, each from 1 to the horizon. For each, the quantiles of the total of the first so many periods come back beside the periods' own. The total's interval combines the periods' intervals with a dependence per grain. The answer's `assumptions` names the value and the data it was fitted on (`docs/forecast.md`, GK-45, GK-48). | none |
| `grain` | `month`, `week`, `day` or `quarter`. The interval of a total depends on it, and `coverage` reads the grain's table. | `month` |
| `width` | A number above zero, or one per period ahead. The distance of every quantile from the median is multiplied by it after the model has answered. For a caller whose stored forecasts and actual values show that this dataset's intervals are too narrow or too wide. `glossthesi.forecast.learned_width` computes it from them (GK-35). | 1 |
| `origins` | A rolling-origin backtest. A number k: the k actual periods before every member's last are each taken as an origin, so that every period forecast one step ahead has its actual value. Or a list of periods: the same origins for every member. From each origin the target is forecast `horizon` periods ahead from the history up to and including it. The covariates' later values in the table are taken as known ahead. Where the table holds the target's actual value for a period forecast, the answer carries it and the PIT, and the answer object carries the backtest's result (`backtest`, below). | one forecast per member, from its last actual period |
| `threshold` | The backtest score above which the result's state is `red`. Above zero and at most 1. | 0.98 |

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
| `pit` | The PIT (probability integral transform): the quantile level of the actual value in the model's own forecast distribution, 0 to 1. It is read against the model's quantiles before any `width`. Null without an actual, and null where `withheld` gives a reason. The same on every level row of the point. |
| `withheld` | Why the point has no PIT, else null. A series that repeats values has a resolution: the smallest gap between two of its values (a count, a ratio over a fixed denominator). The range is the distance between the outermost levels asked. A range narrower than the resolution, with the actual within one resolution step of the model's median, is the model's noise around a value the series takes exactly, and the PIT is withheld. A constant history whose actual equals it is the same case. An actual more than one step away is a real change whatever the range, and keeps its PIT. A series that repeats no value has no resolution, and its PIT is not withheld. The same on every level row of the point. |

A member that cannot be forecast fails on its own, and so does one
of its targets and one origin. The failure is in `answer.errors` with
the member, the column and the origin named, and the other rows are
answered. A member needs 6 known values of a target behind an
origin. An error of the model itself fails the members forecast in
the same group and no others.

**The answer object** carries `coverage` beside `errors` and
`assumptions`: how often the intervals that the request's levels
form covered the actual values on held-out data, at this grain
(`docs/forecast.md`, GK-46). One entry per step of the horizon and
per span asked, per interval:

- `step`, and `span` (null for a period);
- `low` and `high`, the levels that bound the interval;
- `share`, the share of actual values inside it;
- `se`, its standard error, clustered by series and by origin;
- `points`, how many points the share counts;
- `datasets`, how many held-out datasets it pools.

A share with a standard error of 0.05 and one with 0.01 carry
different evidence, so a caller's rule reads both numbers. The
intervals measured are 0.10 to 0.90 and 0.05 to 0.95. There is no
entry for an interval the levels do not form, for a step past the
table (12 months, 13 weeks, 31 days, 4 quarters), or for a span at a
dependence other than the one it was measured at. The dependence of
days and quarters was fitted on the same datasets (GK-48), so the
shares of their spans are fits, not held-out measurements;
`assumptions` names the basis. The shares are pooled over datasets. A
caller that stores enough of its own forecasts and actual values
reads those first.

**The backtest's result.** With `origins`, the answer object carries
`backtest`:

- `members`: per member and target, `backtested`, the number of points
  backtested one step ahead; `held_80` and `held_90`, the share of
  those points with a PIT inside the 10-90 and the 5-95 interval; and
  `newest`, the point one step ahead of the latest origin: its
  `origin`, its `period`, its `pit`, its `displacement` and
  `withheld` where the PIT is withheld.
- The displacement is |2·pit − 1|: 0 at the median, 0.8 at the edge
  of the 10-90 interval, 0.9 at the edge of the 5-95 interval.
- `score`: over the newest points that have a PIT, the largest
  displacement raised to the power of their number. This keeps the
  base rate of each edge the same at any number of members.
- `state`: the result's state. `green` up to a score of 0.8, `yellow`
  up to 0.9, `orange` up to the `threshold`, `red` above it. With no
  point scored the score is 0.0 and the state `green`.
- `threshold`: the threshold used.

The rule and the threshold are in `assumptions` (`field` `backtest`). The
service computes the result. glossdb stores the state as the test's
state and applies no rule of its own.

**The backtest as glossdb sends it.** One member per metric, one
value column, no covariates, `origins` 6, `horizon` 1, the levels
0.05, 0.1, 0.5, 0.9 and 0.95, and complete periods only. The calendar
is the caller's: a period that the data covers in part is not sent,
so the newest point backtested is the newest complete period. The
answer holds, per metric and backtested period, the quantiles, the
actual value, the PIT or the reason it is withheld, and the result.
glossdb stores the answer; the service stores no forecasts. A dataset
whose stored results show that the intervals are too narrow sends a
`width` (GK-35: on one to two years of stored forecasts it does not
improve the forecast).

## `/whatif`: the direction a column moves under a changed input

The model is TabICL's forecaster, as its package ships it. Every
member is answered on its own. The endpoint answers a direction, not
a size. The measurements are in `docs/whatif.md`.

- **The direction** is stated where two estimates agree. The
  member's own history shows the column higher where the input was
  higher, with the calendar and the other inputs held constant, and
  the model moves it the same way. It is an association in that
  history, read for the same period and the period before: the
  input's value one period back is held beside it, so the period
  after the input moves has its own slope, and the effect of a
  sustained change is the sum of the two. The two estimates read the
  same rows with the same features, so their agreement is one piece
  of evidence. That nothing which set the input also moved the
  column is the caller's assumption, declared in `assumptions`.
- **The size** lies between two estimates: the model's and the
  history's. Measured: the model's shift was within half to one and
  a half times the history's on 47% of the tested series, and under
  half of it on another 47% (`docs/whatif.md`). No shape of table was
  found under which the size holds on a new table.
- **The error** is measured on the caller's own table: the last
  periods of the history are held back, predicted and compared.

**The table.** Rows with status `actual` are the history. The
periods ahead come twice: once with the inputs as planned (status
`plan`) and once with the change (status `scenario`), the same
periods in both. The service applies no factors: the caller writes
the changed values. A `forecast` row gets a 400.

- `period` is a date or a timestamp (in the JSON encoding, text such
  as `2026-07-06`): the forecaster builds the calendar from it. A
  timestamp with a time zone is read in UTC, so the weekday and the
  hour in the history's regression are UTC's.
- A member has one row per period and status.
- A target's values in the rows ahead are ignored.
- A member needs, behind the periods ahead, 26 actual periods, or as
  many as the history's regression has columns plus eight, whichever
  is more. The regression's columns are the level, the trend, four
  for the year's cycle, six for the weekday on a daily or finer
  table, 23 for the hour on a finer one, every input, and one lag of
  each changed input, which also costs the first row. With two
  inputs, both changed, that is 26 on a monthly, weekly or daily
  table and 48 on an hourly one. A member with fewer fails on its own
  and the error names the number.
- The last 4,096 actual periods of a member are read.

An input is a number. A yes-or-no column is read as 1 and 0. A text
column is read as whether there is an entry (a holiday's name).

An empty cell of a number or of a yes-or-no column is unknown to the
service, not zero and not no. Where empty means something (no
markdown that week), the caller fills it before sending. An input
that is empty in more than half of a member's history is not read.
If it is the changed input, the sign is `?`. Otherwise it is left out
of the history's regression, and `basis` names it. A row with a gap
in an input that is read is left out of the regression.

One input or several differ between `plan` and `scenario`. A request
where none differs gets a 422. Several inputs changed at once are
read together: the history's estimate is the sum of what each does,
from the one regression that holds every input, with its interval
from the same resamples. The history's regression does not read an
interaction between two inputs. Where the model reads one, the two
estimates disagree and no sign is stated.

**The specification.**

| Field | Meaning | Absent |
|---|---|---|
| `targets` | Every column the caller wants an answer for. Each is answered from its own history, also one that is defined from others (revenue from price and units). Nothing is carried from one column to another, so the caller sends no definition and recomputes none. Every other value column is an input known ahead. The table needs at least one input. | required |
| `levels` | The quantile levels asked for, each strictly between 0 and 1. | required |

**The answer object** carries `changed` and `parts` beside `errors`
and `assumptions`.

- `changed`: the inputs that differ, as a list in the table's order.
- `parts`: per member, column and changed input, what that input
  alone does to the column over the periods ahead by the member's
  history: `history_shift`, the mean over the periods with the lag's
  share included, and `history_low` and `history_high`, its 90%
  interval. The parts come from the same regression as the joint
  estimate, so their estimates add up to it. Their intervals do not
  add; the joint interval is the one to read. The joint regression
  holds the lag of every changed input, so a part differs a little
  from the same input changed alone. A user sees which input carries
  the effect, and when two cancel.

Several changed inputs are also checked together: each may lie
inside its own range while the history holds no row with that
combination. The check measures distance in the space of the changed
inputs, each scaled by its spread. The reach is the distance within
which 99 of 100 history rows find their nearest neighbour. Where the
combination lies farther than the reach from every history row, in
more than a tenth of the periods ahead, no sign is stated and `basis`
says whether the inputs as planned or the change go beyond the
history. For two switch inputs this asks whether both have been on
together before. The reach and the share are declared in
`assumptions`.

**The answer table.** One row per member, column, status, period
and level.

| Column | Meaning |
|---|---|
| `member`, `period`, `column` | The member, the period, the target. |
| `status` | `held_back`: the last periods of the history, as many as there are ahead, predicted from the rows before them. `plan`: the periods ahead, inputs as planned. `scenario`: the same under the change. |
| `level`, `value` | The quantile level and the model's quantile. |
| `actual` | On `held_back` rows, the value in the table. The same on every level row of the point. |
| `history_value`, `history_low`, `history_high` | On `scenario` rows: the plan's median plus the history's estimate of the change at that period, and the 90% interval of that. The estimate uses the change made in that period and the change made the period before (the input's lag). The first period ahead has no change before it: the row before is history. The other inputs, the trend and the calendar's cycles are held constant. The same on every level row of the point. |

And per member and column, the same on each of its rows:

| Column | Meaning |
|---|---|
| `sign` | `+` or `-`: the history shows the input, or the inputs together, moving the column the same way on the rows and on their changes from row to row, neither 90% interval over zero; and the model's shift goes that way. `0`: the history shows no effect and the model's shift is under a tenth of the residual spread of the history's regression. `=`: the changed inputs are the same as planned and under the scenario for this member. `?`: no sign can be stated; `basis` says why, and names the input where one of several fails a check. |
| `confidence` | The sign in words: `direction`, `no effect visible`, `not answerable`, `unchanged`. |
| `basis` | The reason, in words; read it before the numbers. For `?`: the model and the history disagree on the direction; the input did not vary; it cannot be told apart from another input (which one); the change, or the input as planned, goes beyond the history's range; the input is mostly empty; the history is too short; the history shows nothing, or shows an effect on the rows only or on their changes only, and the model moves the column; the history's estimate points up in some periods and down in others (a change that reverses, or two inputs changed in different periods) and nets to nothing over the horizon. Where it points both ways and the horizon's mean is off zero, the sign is stated for the horizon as a whole and `basis` gives the counts. |
| `explains` | The share of the column the changed input explains in this member's history, with the other inputs and the calendar held constant; the inputs together, where several are changed. |
| `error` | The error of the level: the mean distance of the held-back median from the actual values over the periods held back, in the column's own units. It is one window. It says how far the model's level was off, not how far the change is. The held-back rows carry the quantiles and the actual, so a caller can count how many fell inside an interval. |
| `history_error` | The residual spread of the history's regression: the standard deviation of what remains once the level, the trend, the calendar's cycles and the inputs are fitted, in the column's own units. The history's estimate of the change (`history_value`) is read against it, and a tenth of it is the threshold of the `0` sign. Null where the history was not read. |

`no effect visible` means that this member's data does not show an
effect, not that there is none.

**The range between the two estimates.** For a period under the
scenario: from the lowest to the highest of `value` at the median,
`history_low` and `history_high`. It lies between two estimates and
has no stated coverage. The model errs low more often than high.

A member that cannot be answered fails on its own, and so does one
of its targets: the failure is in `answer.errors`. A request costs
three predictions per target (held back, plan, scenario) and the
regression on the history. The endpoint needs the packages of
TabICL's forecaster (`tabicl[forecast]`); a build without them
answers a 422 that says so. The thresholds above (the 90% interval,
a tenth of the residual spread, how much of an input's variation the
other inputs must leave) are in the answer's `assumptions`, each
with its basis.

**The service holds the whole method; the caller holds none of it.**

- **Every number wanted is a target.** The sign, the range and the
  error come per column from that column's own history. The caller
  builds the table and reads the rows.
- **A total is sent as a member:** a series the caller has summed.
  The service sums nothing, since only the caller knows whether a
  column is additive.
- **Break-even is a use of the answer:** a threshold the caller
  compares with the range, in SQL.

TabICL reads one column at a time, so two columns tied by a
definition are each read on their own, and their ranges need not
agree with the definition exactly.

## Statuses

| Status | When |
|---|---|
| 200 | Served. Members, columns or origins that failed on their own are in `answer.errors`. |
| 400 | The body, the table or the specification has the wrong shape. The error says what, and `errors` names a specification field at fault. |
| 401 | No bearer token this service accepts. |
| 404 | No such path. |
| 413 | The body is over the limit, as sent or as the Arrow stream decodes to (a compressed stream can decode to many times its size), or the request is larger than one caller's share of the queue. Send it in parts. |
| 422 | The request cannot be answered: no input differs between `plan` and `scenario` (`errors` names the field), or the build lacks the packages of TabICL's forecaster. |
| 429 | The queue is full for this caller. `Retry-After` says how long to wait. |
| 500 | The service failed. The traceback is in its log, not in the response. The device's memory running out is a 500; it is not reported as members that failed on their own. |
| 503 | The instance is stopping. `Retry-After` says when to retry. |

`GET /healthz` answers `status`, `service`, `version`, `device`,
`loaded`, `checkpoints` (the TabICL files and the Chronos-2 commit the
build serves) and `owner`, the queue's counters. It needs no
authentication.

## Authentication

`Authorization: Bearer …` with a Google-signed ID token for
`GLOSSTHESI_AUDIENCE` from one of `GLOSSTHESI_CALLERS`, or with one
of `GLOSSTHESI_KEYS`. With neither set, every request gets a 401 and
the service does not start. `GLOSSTHESI_INSECURE_OPEN=true` turns
authentication off, for a laptop.

## For glossdb

What glossdb sends and receives. The contract is one general service
call: a table in, a table out, the service's name and version in
every answer, one URL and a path per call.

**The backtest (`metric_ranges`).** glossdb sends the metric series
at month grain as the one table, complete months only: `member` the
metric's name, `period` the month, one value column, every row
`actual`. The specification is `{"targets": ["value"], "origins": 6,
"horizon": 1, "levels": [0.05, 0.1, 0.5, 0.9, 0.95], "grain":
"month", "threshold": …}`, the threshold taken from the test.

It receives, per metric and backtested period, the five quantiles
(one row per level), the `actual`, and the `pit` or `withheld`. The
answer object carries the result: per metric the shares inside the
two intervals and the newest point, and one score and state over all
metrics.

A metric whose history is too short for an origin is in
`answer.errors` with the reason, and the origins it does have are
backtested. The service needs six known values behind an origin. So
a backtest of six origins needs twelve months, and a metric of eight
months gets two backtested periods.

The service owns the backtest's rules: when a PIT is withheld, the
displacement, the score and the states. The calendar is the caller's,
which sends no partial period. glossdb stores the results; the
service stores none. The quantiles are the model's own.

**The what-if (`metric_scenario`).** glossdb sends the table at the
grain at which the changed column varies from row to row: the history
as `actual`, and the periods ahead twice, as planned and under the
scenario, with every column to be answered as a target. It receives
the rows above and serves them: the sign, the model's rows, the
history's estimate and `basis` per column.

**Configuration.** One URL that names this service, glossdb's
`GLOSSDB_SERVICE_URL`; each call adds its path. The bearer token is
`GLOSSDB_SERVICE_TOKEN`, a key sent as it is, or an ID token issued
for `GLOSSDB_SERVICE_AUDIENCE`.
