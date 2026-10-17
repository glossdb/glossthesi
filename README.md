# glossthesi

The model service behind glossql: forecasts of time series with
prediction intervals and the walk that grades them, anomaly detection
in tables, and which way a column moves under a changed input. The
server carries no model; it names this service per point and calls
it over HTTP.

| Task | Route | Model |
|---|---|---|
| Forecasts of time series with prediction intervals; the walk, from past origins, with the PIT per point | `/forecast` | Chronos-2 (`chronos-forecasting`, Apache-2.0), as its package ships it |
| Anomaly detection in a table: errors that break a relation between columns | `/misfit` | TabICL (`tabicl` at a pinned version): the regressor for numeric columns, the classifier for categorical ones |
| What-if: which way a column has moved with one or several changed inputs in the member's own history, where the model agrees, with the reason, the model's and the history's estimate, and the error on a held-back period (`docs/whatif.md`) | `/whatif` | TabICL's forecaster, as its package ships it, and the series' own history |

There is one forecasting set-up. It was chosen on forecasts a quarter
and a year ahead, on public and enterprise datasets and on a public
benchmark (`docs/forecast.md`).

## Run

```bash
uv sync
GLOSSTHESI_KEYS=k1 uv run glossthesi   # 127.0.0.1:8100, the machine's best device, a bearer per caller
GLOSSTHESI_INSECURE_OPEN=true uv run glossthesi   # no check at all: a laptop trying it out
```

| variable | meaning |
|---|---|
| `GLOSSTHESI_ADDR` | where to listen; default `127.0.0.1:8100` |
| `GLOSSTHESI_DEVICE` | `cuda`, `mps` or `cpu`; default the first available in that order |
| `GLOSSTHESI_AUDIENCE`, `GLOSSTHESI_CALLERS` | the doors take Google-signed ID tokens for that audience (the service's URL) from the listed service accounts — what a caller on GCP mints from its metadata server, no key anywhere; the `auth` extra |
| `GLOSSTHESI_KEYS` | comma-separated bearer keys instead (a laptop, a test host); a caller is recorded as the key's place in the list (`key-1`), never as the key. With neither set the service does not start, and every door refuses |
| `GLOSSTHESI_INSECURE_OPEN` | `true` (the literal) serves every door without authentication, every caller recorded as `insecure_dev_mode`, as glossql's switch of the same name does. The name is the warning: a laptop trying the service out, never a deployment |
| `GLOSSTHESI_VERSION` | the build's name, on every answer, on `/healthz` and every log line; the image bakes the commit |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | a collector to send traces, metrics and logs to (OTLP/HTTP); unset, the JSON log lines on stdout are all; the `otel` extra |
| `GLOSSTHESI_QUEUE_MB` | what may wait for the device, in MB of tables; default 512, half of it at most one caller's — past it a `429` with `Retry-After` |
| `GLOSSTHESI_MAX_BODY_MB` | the largest body parsed; default 32 — past it a `413`, as the bytes arrive and as an Arrow stream decodes, batch by batch (a compressed stream may be many times its wire size) |
| `GLOSSTHESI_PRELOAD` | `0` leaves Chronos-2 to its first forecast; default all models load before the port opens |
| `GLOSSTHESI_MISFIT_WORKERS` | threads fitting the misfit read's conditionals side by side; default four on CUDA, one elsewhere |
| `HF_HUB_OFFLINE` | `1` in the image: the checkpoints are baked, nothing is fetched at start |

The checkpoints come from the Hugging Face hub cache (`jingang/TabICL`,
the v2 regressor and classifier, dated in their file names;
`amazon/chronos-2` at the commit `forecast.REVISION` names). A
container bakes them at build (`Dockerfile`); a laptop fetches them on
first start; `/healthz` says which are served, since every measured
number describes those weights and a move is a change of model.

## The wire

The contract is `docs/contract.md`. In short: every route takes one
table and a specification and answers one table and what produced
it. The table has a row per member and period, a column per value
and a status per row (`actual`, `plan`, `forecast`). It travels as
an Arrow IPC stream with the specification as JSON in the schema's
metadata under `spec`, or as JSON with the table column by column
beside `spec`; the answer comes back in the request's encoding, its
`answer` object naming the service, the version and the items that
failed alone. A refusal is JSON, `{"error": …}` with a 4xx, and names
the fields to repair where the specification is at fault.

| route | specification | answer table |
|---|---|---|
| `POST /forecast` | `targets`, `horizon`, `levels`; `totals`, `grain`, `width`; `origins` for the walk or a backtest | a row per member, origin, step, column and level: `value`, and `actual` and `pit` where the actual has landed; the answer object's `coverage` says how often each band held on held-out data at that grain and step |
| `POST /misfit` | `categorical`, `cells`, `folds` | a row per frame row: `score`, and with `cells` a column per frame column |
| `GET /healthz` | | `status`, `service`, `version`, `device`, `loaded`, `checkpoints`, `owner` |

A forecast, in the JSON encoding:

```json
{
  "spec": {"targets": ["sales"], "horizon": 3, "levels": [0.1, 0.5, 0.9], "totals": [3], "grain": "month"},
  "table": {
    "member": ["north", "north", "north", "north", "north", "north", "north", "north"],
    "period": ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"],
    "status": ["actual", "actual", "actual", "actual", "actual", "actual", "forecast", "forecast"],
    "sales":  [120, 132, null, 141, 150, 148, null, null],
    "promotion": [0, 1, 0, 0, 0, 1, 1, 0]
  }
}
```

The six actual rows are the history; `promotion` is a covariate,
known ahead for the two periods the table carries; the third period
ahead has no row and its `period` comes back null. The answer's
table holds nine rows for the periods (three steps by three levels)
and three for the total, each with the member, the origin
(`2026-06`), the step, the period, the column and the level.

The walk as glossql declares it: one member per metric, one value
column, `origins` 6 and `horizon` 1. The answer holds per metric and
walked period the bands, the actual and the PIT, read against the
model's own quantile grid before any `width`. The record of PITs
stays on glossql's side (`docs/forecast.md`, GK-35).

Points that shape a client:

- **Send the members of a dataset in one request.** Members of one
  shape are forecast together (cross-learning, groups of 100); alone
  they lose 3 to 4% (GK-26). The shape is the targets and the
  covariates known ahead.
- **Several metrics of one entity go as several targets** of one
  member, answered per column. The forecast is the same as sending
  them as separate members of one request; a column alone costs 1 to
  4% (GK-36). A column too short fails alone.
- **`grain` is needed for the interval of a total** (GK-34). All four
  grains are measured, month and week on development data, day and
  quarter on the held-out data that is the only daily and quarterly
  data (GK-45, GK-48).
- **`width`: keep the record, send nothing yet.** The rule for a
  width from a dataset's record of forecasts and actual values is
  `glossthesi.forecast.learned_width`; on records of one to two
  years it does not pay (GK-35).
- **`/misfit` reads the frame the caller joins.** A relation is
  visible only when its columns are in one frame: payments joined to
  their invoices, order lines to products and plants. Codes go as
  `categorical`. General anomaly detection is another field and is
  not offered (GK-23).

## Hosting

One container (`Dockerfile`): the same process on whatever GPU the
host has, CPU without one; not root, the checkpoints and the commit
baked. Two stages: uv makes the venv and pulls the checkpoints in the
first, and the image is the interpreter plus those two trees — no uv,
no cache, no source. Its size is torch and the CUDA libraries its
wheels carry (about 3 GiB compressed), plus half a gigabyte of
checkpoints. Both models load before the port opens, so the port is the
readiness signal and a cold start is one number. On SIGTERM what is in
flight is finished and a request on a kept connection is sent back to
retry (`503`, `Retry-After`). A job that fails (an out-of-memory)
fails its own caller, hands the device's memory back and leaves the
rate estimate alone. Nothing in it names a provider.

Production is GCP, on an L4 behind Cloud Run; the deployment — the
project, the region, the collector sidecar, who may call — lives in the
private deployment repo, not here. What it relies on: the service
scaled to zero and billed by instance time, the cold start waited out
by the caller (glossql's call timeout is 600 s); CUDA from torch's
wheels and the driver from the host, so a torch move is checked
against the host's driver; one instance until 429s say otherwise;
authentication by identity at the Cloud Run edge and again in the
service; the JSON log lines kept by Cloud Logging and, with the
collector, spans and metrics in Cloud Trace and Cloud Monitoring.
The device's memory running out fails the job as a `500`, the owner
hands the memory back, and the caller retries; it is never reported
as members that failed alone.

The image is `ghcr.io/glossdb/glossthesi:<version>` (and `:latest`),
public, from `.github/workflows/release.yml`; Cloud Run pulls it by
that name. A manual dispatch of the workflow builds the commit and
pushes `:main` and `:sha-<commit>` — a deployment pins that, and the
cloud tests run against it. A release names what was tested: bump
`version` in `pyproject.toml` (before the dispatch, so the image is
built with it), `git tag v<version> && git push origin v<version>`;
the tag must match the file, and a commit whose `:sha-` image is up is
not built again — `:<version>` and `:latest` are pointed at it (a
commit without an image is built first). Every build bakes
`<version>+<commit>` as `GLOSSTHESI_VERSION`, and starts the image
on the runner's CPU to read `/healthz` before it pushes, so a pushed
image has its checkpoints and its version. `:latest` moves at a tag only.

What the service says about itself (`telemetry.py`): a JSON line on
stdout per start, per job (kind, cells, callers, device seconds, what
still waits), per refusal (status, caller, route, reason), per member
or column that failed alone inside a served answer (`item_failed`, not
a refusal) and per failure (with the traceback) — never a payload and
never a key. A host that keeps
stdout has that as-is; with `OTEL_EXPORTER_OTLP_ENDPOINT` the same
goes to a collector as spans (per request, per job), counters (jobs,
refusals by status, failures), a histogram of job seconds and a gauge
of the queue.

The bench, on any host with a GPU that runs the container:

```bash
python -m glossthesi.measure                      # the reads' timings, this device
python -m glossthesi.measure --parity fixtures/   # the density oracle here; --amp grades reduced precision
```

## Evaluation

What each route promises, how it works, what was tested and what was
found is on a page per route: `docs/forecast.md`, `docs/misfit.md`,
`docs/whatif.md`.

The evaluation itself is kept in a repository of its own, which is
not public: the code that sends every route its datasets through the
HTTP API as a client would, the records of its runs and the tables of
results. Some of its corpora are licensed.

## Tests

```bash
uv run pytest                          # the wire in both encodings, the owner, the what-if's reading of a history, and the density read against fixtures/
```

## License

Copyright © 2026 Philipp Suter. GNU Affero General Public License,
version 3 or any later version (`LICENSE`). The service may be used,
changed and run freely; whoever changes it and runs it for others
over a network has to offer them the changed source (the licence's
section 13). glossql is a separate program that talks to the service
over HTTP. The checkpoints the image bakes carry licences of their
own: Chronos-2 under Apache-2.0, TabICL under BSD-3-Clause with its
forecasting part under Apache-2.0.

Contributions are welcome; each commit certifies the Developer
Certificate of Origin (`DCO`) with a `Signed-off-by` line, and
`CONTRIBUTING.md` says how.
