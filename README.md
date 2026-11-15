# glossthesi

The model service behind glossdb's preview features: forecasts,
expected ranges and scenarios. glossdb holds no model. It is
configured with this service's URL and calls it over HTTP.

| Task | Endpoint | Model |
|---|---|---|
| Forecast of time series with prediction intervals. With `origins`, a rolling-origin backtest: past periods forecast from the history before them, with the PIT and the displacement per point and a score and state per member. glossdb computes its expected ranges from the backtest | `POST /forecast` | Chronos-2 (`chronos-forecasting`, Apache-2.0), as its package ships it |
| What-if: the direction a column moves when one or several inputs change, stated where the member's own history and the model agree, with the reason, both estimates, and the error on a held-back period. glossdb computes its scenarios from it | `POST /whatif` | TabICL's forecaster, as its package ships it, and a regression on the member's own history |

Status: preview. What each endpoint answers, how it works and what
was measured is in `docs/forecast.md` and `docs/whatif.md`. The
request and answer format is in `docs/contract.md`.

## Run

```bash
uv sync
GLOSSTHESI_KEYS=k1 uv run glossthesi   # 127.0.0.1:8100, the first available device, callers send the key as a bearer token
GLOSSTHESI_INSECURE_OPEN=true uv run glossthesi   # no authentication: for trying it out on a laptop
```

| variable | meaning |
|---|---|
| `GLOSSTHESI_ADDR` | Address to listen on. Default `127.0.0.1:8100`. |
| `GLOSSTHESI_DEVICE` | `cuda`, `mps` or `cpu`. Default: the first available in that order. On `mps`, Chronos-2 runs on the CPU. |
| `GLOSSTHESI_AUDIENCE`, `GLOSSTHESI_CALLERS` | Authentication by Google-signed ID token: the audience the token is issued for (the service's URL) and the service accounts allowed to call, comma-separated. A caller on GCP gets the token from its metadata server, so no key is stored. Needs the `auth` extra. |
| `GLOSSTHESI_KEYS` | Authentication by bearer key instead: comma-separated keys (a laptop, a test host). The logs name a caller by the key's position in the list (`key-1`), not by the key. With neither keys nor an audience and callers set, the service does not start. |
| `GLOSSTHESI_INSECURE_OPEN` | `true` (the literal) turns authentication off. Every caller is logged as `insecure_dev_mode`, the name glossdb's switch of the same kind uses. For trying the service out on a laptop, not for a deployment. |
| `GLOSSTHESI_VERSION` | The build's name: in every answer, on `/healthz` and in the start log line. The image sets it to `<version>+<commit>`. Default `dev`. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | A collector for traces, metrics and logs (OTLP/HTTP). Unset, the JSON log lines on stdout are the only output. Needs the `otel` extra. |
| `GLOSSTHESI_QUEUE_MB` | How much may wait for the device, in MB of table values. Default 512; one caller may hold half of it. A request that does not fit now gets a `429` with `Retry-After`; a request larger than one caller's share gets a `413`. |
| `GLOSSTHESI_MAX_BODY_MB` | The largest request body. Default 32. A larger one gets a `413`. The limit applies to the bytes received and to what an Arrow stream decodes to, batch by batch: a compressed stream can decode to many times its size. |
| `GLOSSTHESI_PRELOAD` | `0` loads Chronos-2 at the first forecast. Default: all models load before the port opens. |
| `GLOSSTHESI_FLUSHING_PROBE` | `1` keeps TabICL's own free-memory check, which empties the allocator cache before every pass. For timing comparisons. |
| `HF_HUB_OFFLINE` | `1` in the image: the checkpoints are in the image and nothing is downloaded at start. |

The checkpoints come from the Hugging Face hub cache: `jingang/TabICL`
(the v2 regressor and classifier, dated in their file names) and
`amazon/chronos-2` at the commit `forecast.REVISION` names. The
container image includes them (`Dockerfile`). A laptop downloads them
at first start. `/healthz` lists the checkpoints served: every
measured number describes those weights, and a change of checkpoint
is a change of model.

## The API

The contract is `docs/contract.md`. In short:

- Every endpoint takes one table and a specification, and answers
  one table and an answer object.
- The table has a row per member and period, a column per value, and
  a status per row (`actual`, `plan`, `forecast`, `scenario`).
- A request is an Arrow IPC stream with the specification as JSON in
  the schema's metadata under `spec`, or JSON with the table column
  by column beside `spec`. The answer comes back in the request's
  encoding. Its `answer` object names the service, the version and
  the members or columns that failed on their own, each with a `code`.
- An error response is JSON, `{"error": …, "code": …}` with a 4xx or
  5xx status. Where a field of the specification is at fault, `errors`
  names it. The codes are listed in `docs/contract.md`.

| endpoint | specification | answer table |
|---|---|---|
| `POST /forecast` | `targets`, `horizon`, `levels`; `totals`, `grain`, `width`; `origins` for a backtest, `threshold` for its red state | A row per member, origin, step, column and level: `value`; `actual`, `pit` and `displacement` where the table holds the actual value; `withheld` with the reason where a PIT is not given; `coverage_80` and `coverage_90` with their standard errors, points and datasets: how often that interval covered the actual values on held-out data at that grain and step. With `origins`, the answer object's `backtest` carries per member the share of points inside the 80% and the 90% interval, and the score and state of its newest point |
| `POST /whatif` | `targets`, `levels` | A row per member, column, status (`held_back`, `plan`, `scenario`), period and level: the model's `value`, `effect` (the scenario's median minus the plan's), the history's estimate with its interval, `sign` and `basis` |
| `GET /healthz` | | `status`, `service`, `version`, `device`, `loaded`, `checkpoints`, `owner` (the queue's counters) |

A forecast, in the JSON encoding:

```json
{
  "spec": {"targets": ["sales"], "horizon": 3, "levels": [0.1, 0.5, 0.9], "totals": [3], "grain": "month"},
  "table": {
    "member": ["north", "north", "north", "north", "north", "north", "north", "north", "north"],
    "period": ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"],
    "status": ["actual", "actual", "actual", "actual", "actual", "actual", "forecast", "forecast", "forecast"],
    "sales":  [120, 132, 138, 141, 150, 148, null, null, null],
    "promotion": [0, 1, 0, 0, 0, 1, 1, 0, 0]
  }
}
```

The six actual rows are the history. `promotion` is a covariate. The
table holds its values for all three periods ahead, so the model
takes them as known ahead. The answer's table holds nine rows for the
periods (three steps by three levels) and three for the total, each
with the member, the origin (`2026-06`), the step, the period, the
column and the level.

The backtest as glossdb sends it: one member per metric, one value
column, `origins` 6 and `horizon` 1. The answer holds, per metric and
backtested period, the quantiles, the actual value and the PIT. The
PIT is read against the model's own quantile grid, before any
`width`. glossdb stores the results; the service stores nothing.

What a client should know:

- **Send the members of a dataset in one request.** Members with the
  same targets and the same covariates known ahead are forecast
  together, in groups of at most 100. Measured: a member sent alone
  loses 3 to 4% accuracy (GK-26).
- **Several metrics of one entity go as several targets** of one
  member, answered per column. The forecast is the same as sending
  them as separate members of one request. Measured: a column sent
  alone loses 1 to 4% (GK-36). A column with too short a history
  fails on its own.
- **A member needs 6 known values** of a target behind an origin.
- **Name the `grain` when you ask for a total.** The interval of a
  total depends on it (GK-34). The grains are `month`, `week`, `day`
  and `quarter`; the default is `month`.
- **`width` is for a caller that stores its forecasts and the actual
  values.** `glossthesi.forecast.learned_width` computes a width from
  them. Measured: on one to two years of stored forecasts it does not
  improve the forecast (GK-35), so the default is no width.

A tag such as GK-26 names a finding in the evaluation.

## Hosting

One container (`Dockerfile`). The same process runs on the GPU the
host has, or on the CPU without one. It runs as an unprivileged user.
The image holds the Python environment, the checkpoints and the
version; it holds no build tools and no source tree.

- **Readiness.** All models load before the port opens, so the open
  port is the readiness signal.
- **Shutdown.** On SIGTERM the requests in flight are finished. A new
  request on a kept connection gets a `503` with `Retry-After`.
- **A failed job.** A job that fails, for example when the device's
  memory runs out, fails its own caller with a `500`. The service
  frees the device's cached memory and goes on. Such a failure is not
  reported as members that failed on their own.
- **Authentication.** By Google-signed ID token or by bearer key
  (the table above). The deployment's settings are kept outside this
  repository.

The image is `ghcr.io/glossdb/glossthesi:<version>` (and `:latest`),
built by `.github/workflows/release.yml`:

- A manual run of the workflow builds the commit and pushes `:main`
  (the branch's name) and `:sha-<commit>`. A deployment can pin the
  second.
- A release: set `version` in `pyproject.toml`, then
  `git tag v<version> && git push origin v<version>`. The tag has to
  match the file. If the commit already has a `:sha-<commit>` image,
  that image gets the tags `:<version>` and `:latest` and is not
  built again. `:latest` moves at a tag only.
- Every build sets `GLOSSTHESI_VERSION` to `<version>+<commit>`.
  Before the push, the workflow starts the image on the runner's CPU,
  reads `/healthz`, and sends one forecast and one what-if.

Logs, metrics and traces (`telemetry.py`). The service writes one
JSON line on stdout:

- at start;
- per job (`cycle`: kind, cells, callers, device seconds, cells still
  waiting);
- per error response (`refused`: status, caller, path, reason);
- per member or column that failed on its own inside a served answer
  (`item_failed`);
- per failure, with the traceback.

No line holds a request body, an answer body or a key. With
`OTEL_EXPORTER_OTLP_ENDPOINT` set, the same goes to a collector:
spans per request and per job, counters for jobs, error responses by
status and failures, a histogram of job seconds and a gauge of the
queue.

The benchmark, on any host that runs the container:

```bash
python -m glossthesi.measure                      # timings of a forecast and a backtest on this device, as JSON
```

## Evaluation

What was measured, and what was found, is on a page per endpoint:
`docs/forecast.md` and `docs/whatif.md`.

The evaluation is kept in a separate repository that is not public.
It holds the code that sends datasets to every endpoint through the
HTTP API as a client would, the stored results of its runs and the
result tables. Some of its datasets are licensed.

## Tests

```bash
uv run pytest                          # the API in both encodings, the queue, the what-if's regression on a history, TabICL's forecaster with the real weights
```

## License

Copyright © 2026 Philipp Suter. GNU Affero General Public License,
version 3 or any later version (`LICENSE`). The service may be used,
changed and run freely. Whoever changes it and runs it for others
over a network has to offer them the changed source (the licence's
section 13). glossdb is a separate program that talks to the service
over HTTP. The checkpoints in the image have licences of their own:
Chronos-2 under Apache-2.0, TabICL under BSD-3-Clause with its
forecasting part under Apache-2.0.

Contributions are welcome. Each commit certifies the Developer
Certificate of Origin (`DCO`) with a `Signed-off-by` line;
`CONTRIBUTING.md` says how.
