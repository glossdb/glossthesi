# The forecast

What `/forecast` is for, how it works, what was tested and found, and
what is not known. The wire form is in `docs/contract.md`. A tag in
brackets (GK-26) is the name the code and the contract use for a
finding.

- **The use case** is planning: a forecast a quarter and a year
  ahead, for the periods and for their total, with a range that holds
  what it states.
- **One model:** Chronos-2, as its package ships it.

## What the route promises

- **More accurate than a naive baseline** at the distances a plan is
  made for: 20 to 40% lower loss, on monthly and weekly data, on the
  datasets the set-up was chosen on and on datasets held out.
- **A range that holds for the next period.** An 80% interval holds
  close to 80% of the actual values one period ahead. Further ahead
  it holds on weekly data and on the development datasets; on
  held-out monthly data a year ahead it held 71%. The coverage at
  every step a caller asks, per grain, rides on every answer (GK-46,
  `coverage`): the table a caller reads before handing out a range.
- **Totals from the same forecast.** The total of a quarter or a
  year comes with its own interval, from the periods' intervals.
- **The walk.** The last periods of a series each forecast one step
  ahead, with the actual value and where it landed in the forecast's
  distribution (the PIT).
- **The published model, unchanged.** Through the API the service
  gives the numbers published for Chronos-2 on a public benchmark.

## How it works

1. **The series of a request are forecast together** (GK-26), in
   groups of at most 100, so that they inform each other. Columns
   that are not targets are covariates, known ahead where the rows
   ahead carry them. Nothing is recalibrated.
2. **Several target columns of one member** are one multivariate
   series (GK-36).
3. **A total** (GK-27, GK-34, GK-45, GK-48). Its median is the sum of
   the periods' medians. Its interval combines the periods' intervals
   as partly dependent, by one number per grain, each measured: 0.43
   for months, 0.22 for weeks, 0.12 for days, 0.25 for quarters.
   Every answer with a total says which value it used and on what
   data it was measured.
4. **`width`** (GK-35): a factor from the caller, applied to every
   quantile's distance from the median after the model has answered.
   For a caller whose own record says the intervals run too narrow
   or too wide.
5. **The walk** (GK-42): with `origins`, each of the last periods is
   forecast from the history before it. The PIT is read against the
   model's own 21 quantile levels, by linear interpolation.
6. **How often the bands held** (GK-46): every answer carries, per
   step and span and per band the request's levels form, the share of
   actual values that landed inside such a band on held-out data at
   that grain, with the points behind it (`coverage.py`). A span's
   share was measured at the dependence of its grain, so a grain whose
   dependence moves serves its steps and no spans until the table is
   measured again.

## What was tested

Rolling-origin backtests through the HTTP API: a series is cut at a
past date, the periods after it are forecast and compared with what
happened. Origins three months apart, up to nine per series. Loss is
the quantile loss relative to a naive baseline (the better of the
last value and the value a year earlier); under 1.00 is better than
the baseline.

- **Development data:** 316 public and 218 enterprise monthly
  series, three weekly retail datasets (113 series).
- **Held-out data,** run after the set-up was decided: 69 monthly
  series in 4 datasets (headcount, product reviews, clinical
  trials), 80 weekly series in 2 datasets (a fashion retailer, a
  grocery); for the coverage by step, 87 daily series in 3 datasets
  (cash machines, a grocery delivery's warehouses, a grocery) and 96
  quarterly series in 4 (tourism, clinical trials).
- **A public benchmark:** the 25 tasks of fev-bench that forecast a
  quarter to two years ahead, scored by the benchmark's own package.

| Question | Finding |
|---|---|
| Which set-up? (GK-26) | Chronos-2 with the series together: loss 0.79 and 0.78 a quarter and a year ahead on public data, 0.74 and 0.67 on enterprise data; 80% interval covering 0.78 to 0.80. TabICL with features built by the client: 0.85, 0.85, 0.78, 0.83, covering 0.64 to 0.71. An ensemble of the two: level a quarter ahead, worse a year ahead |
| Does forecasting together help? | Better or level on 18 of 21 datasets a year ahead; worse by 0.02 at most, better by up to 0.11 |
| Does the accuracy hold on held-out data? (GK-28) | Yes. Monthly: loss 0.64 to 0.78. Weekly: 0.66 to 0.75 |
| Do the intervals hold on held-out data? (GK-28) | The next month: 0.79. Months of the quarter: 0.74. Months of the year: 0.71. Weekly: 0.75 for the next week, 0.80 and 0.82 for the weeks of the quarter and the year |
| Is the total right? (GK-27) | With the periods taken as partly dependent the 80% interval of a year's total covered 0.78 on enterprise data (1,293 points). Added up as fully dependent: 0.88. As independent: 0.41. Forecasting the series of trailing sums, as before: 0.64 |
| How dependent are the periods? (GK-45) | The value at which a total's interval holds 0.80: 0.43 ± 0.02 for months (7,816 totals), 0.22 ± 0.04 for weeks (947). Per dataset it runs from 0.0 to 1.0 |
| How dependent are days and quarters? (GK-48) | 0.12 ± 0.01 for days (421 calendar-month totals), 0.25 ± 0.06 for quarters (864 year totals), measured on the held-out daily and quarterly datasets, the only ones there are. Fitted on the other datasets and read on the one left out, the month's total of days covers 0.81 and the year's total of quarters 0.81. Per dataset 0.09 to 0.23 for days, 0.17 to 1.0 for quarters |
| Does a total need the grain? (GK-34) | Yes. With the monthly value, weekly totals covered 0.91 to 0.97. With the weekly value: 0.78 and 0.81 |
| Is the walk calibrated? (GK-42) | Coverage 0.78 to 0.84 at a nominal 0.80; the PITs within 0.05 of uniform, a fifth of them at the edges; within a percent of the backtest that cuts per origin |
| Does a covariate moved ahead move the forecast as the member's history says? (GK-47) | The direction: on 88% and 95% of the series whose history shows an effect (the what-if's tables, development and held out), against 93% and 96% for the what-if route on the same series. The size: about half the history's where the what-if's model had it right (a promotion, a discount), 0.51 and 0.24 at the median over all. Where the history shows no effect the forecast still moves, by a tenth of the column's spread, on 31% and 51% of the series |
| At which steps does the 80% interval hold, on held-out data? (GK-46) | Weeks: 0.79 to 0.84 at each of 13 steps, 0.85 for the quarter's total. Quarters: 0.79 to 0.82 at each of 4 steps, 0.84 for the year's total. Months: 0.75 one month ahead, 0.67 at twelve, no step at 0.80; 0.72 and 0.67 for the quarter's and the year's total. Days: 0.79 one day ahead, 0.70 to 0.80 from four to thirty-one days; the calendar month's total 0.94 at the monthly dependence the day grain took then. Read again at the day's and the quarter's own dependence (GK-48), what the service serves: the calendar month's total 0.79, the year's total 0.80, both fits on the datasets the value was fitted on; every other entry unchanged |
| Several columns per series? (GK-36) | The same forecast as the columns sent as separate series in one request, to the last digit. A column sent alone costs 1 to 4% on two columns of four |
| Does the service give the published numbers? | Skill score 38.0% against 38.0% published, first of 14 models; 18 tasks equal to three decimals, the rest within 0.005 |
| Weekly data? (GK-31) | Loss 0.68 to 0.74 on development data, 0.66 to 0.72 held out; on fev-bench's weekly tasks 42% skill, level with the published model |

## Where it is weak

- **Series that are mostly zero** (spare parts): the 80% interval
  covers 0.57 to 0.71, and forecasting the series together makes it
  worse there (GK-29).
- **Macroeconomic indicators:** worse than the naive baseline for the
  next month and the quarter (loss 1.24 and 1.12).
- **Short history:** 36 months give one origin; coverage 0.54 to
  0.83.
- **A year ahead on held-out monthly data** the interval is too
  narrow: 0.71 where 0.80 is stated. Behind it are product reviews
  that grow year on year (0.69) and a headcount (0.72) (GK-28).
- **The total of a year on a single dataset:** between 0.47 and 1.00
  where the pooled value is 0.78. One number per grain is the
  default for a caller with no record of its own.
- **A covariate moved ahead** moves the forecast whether or not the
  member's own history says it should, and by about half the
  history's size where it does (GK-47). The route carries the plan's
  world; a lever's effect is the what-if's question.
- **The dependence of days and quarters rests on few datasets,** all
  held out, since no other daily or quarterly data exists (GK-48):
  three daily, four quarterly. Per dataset the value runs from 0.09
  to 0.23 for days and from 0.17 to 1.0 for quarters. At the value
  the other datasets give, a grocery delivery's month totals cover
  0.72 (36 totals) and the clinical trials' year totals 0.56 and
  0.71 (72 each), where the cash machines, the grocery and the
  tourism sets cover 0.81 to 0.84.

## What was tried and dropped

| Tried | Measurement |
|---|---|
| TabICL as the forecaster, with features built by the client | Behind Chronos-2 at every distance. On fev-bench 14% skill against 25% (GK-30) |
| The forecaster of the `tabicl` package | Not better than the naive baseline on enterprise data a quarter and a year ahead |
| An ensemble of TabICL and Chronos-2; one of three with seasonal naive | Level or worse, at two or three models |
| Recalibrating the intervals | Moved TabICL's coverage from 0.66 to 0.72 where Chronos-2 had 0.76 without it; lowered both models' benchmark score |
| Every series by itself | Worse or level on 18 of 21 datasets |
| Totals as a series of trailing sums | Coverage 0.64 against 0.78 |
| Widening the intervals from the request's own history (GK-33) | What the intervals missed in the year before does not predict what they miss after. Only ever widening: coverage up 0.02 at 1 to 4% more loss and three forecasts per request |
| A width learned from a dataset's record of forecasts and actual values (GK-35) | Moves the coverage the right way on most datasets and costs 5 to 15% loss on records of one to two years. Not served as a default; the `width` field stays for a caller with a longer record |
| A dependence that decays with the distance between periods (GK-34) | No single value serves both grains and both spans |
| Timestral-209M or t0-gamma in place of Chronos-2 (2026-10-16, read through the service's own contract on the development datasets) | Timestral 1 to 6% lower loss for the next period and the quarter, level to 3% higher a year ahead and 5 to 10% higher on the totals of a year, intervals over-covering at 0.82 to 0.86; t0-gamma behind at planning distances. The vendors' first places rest on hourly data: 2 to 4% over Chronos-2 at the business grains on the leaderboard's own files. GK-49, GK-52 and GK-54 in the evaluation's findings; the adapters live in the branch's history |

## What a caller should know

- **A request holds series that belong together:** the metrics of
  one company, the stores of one chain.
- **A member needs 6 known values** of a target behind an origin. A
  column that has fewer fails alone, and the error names its origin
  in the table's own type, said as text where it is a date.
- **Name the grain** when a total is asked on data that is not
  monthly. Only the interval of a total uses it.
- **Keep the record** of forecasts and the actual values that land
  after them. It shows a user how good past forecasts were, and it
  is what a `width` is learned from.
- **The interval of the next period can be trusted** for flagging:
  it raised 0.10 false alarms where 0.10 is stated on monthly
  held-out data, 0.13 on weekly.

## Not covered

- **Timing on a server GPU** (GK-20). On the laptop Chronos-2 runs
  on the CPU.
- **A reading of the day and quarter dependence on data outside the
  fit.** None exists: every daily and quarterly dataset went into
  it. Leaving one dataset out stands in (GK-48). Daily data from our
  own corpora: none of the held-out corpora has a day-exact table.
- **Five benchmark tasks** of more than 5,000 forecasts each.

## What could come next

- **A dataset's own dependence for totals,** from its record, as
  `width` does for the periods.
- **A width from a longer record** than one to two years.

## What was read

- fev-bench (Shchur et al., 2025, arXiv:2509.26468) and GIFT-Eval
  (Aksu et al., 2024): rolling-origin evaluation, quantile loss
  relative to seasonal naive, the geometric mean, the win rate.
- The M4 competition (Makridakis et al., 2020): coverage as the test
  of an interval.
- "Beyond Accuracy: Are Time Series Foundation Models
  Well-Calibrated?" (2026): calibration within about 0.05 as the
  working standard.
- Chronos-2 (arXiv:2510.15821): the model, with covariates and the
  series of a request forecast together, as in its published results.
- TabPFN-TS (Hoo et al., 2025, arXiv:2501.02945): forecasting with a
  tabular model, the approach measured against Chronos-2 and removed.
- Kuleshov et al., 2018: the recalibration that was tried.
- Timestral (Mistral AI, 2026-10-15): the model card and the package
  (github.com/mistralai/timestral); its technical report had not
  appeared. t0 (The Forecasting Company, arXiv:2609.24559): the model
  card and the package (`tfc-t0`). Both read as candidates and
  dropped (GK-49).

## A rule kept from the tests

A defect in test data is fixed and the run repeated; the model is
not touched for it (GK-32). Two were found in the held-out data: a
snapshot of clinical trials that thins out over its last years, and
a newest month of headcount read from a placeholder date.
