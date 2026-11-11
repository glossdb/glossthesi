# The forecast

What `/forecast` answers, how it works, what was measured and where
it is weak. The request and answer format is in `docs/contract.md`.
Status: preview. A tag in brackets (GK-26) names a finding in the
evaluation; the code and the contract use the same tags.

- **The use case** is planning: a forecast a quarter and a year
  ahead, for the periods and for their total, with a prediction
  interval and its measured coverage.
- **One model:** Chronos-2, as its package ships it.

## What the endpoint answers

- **A forecast more accurate than a naive baseline** at the horizons
  a plan is made for. Measured: 20 to 40% lower quantile loss on
  monthly and weekly data, on the datasets the configuration was
  chosen on and on held-out datasets.
- **A prediction interval with its measured coverage, by grain.**
  Measured on held-out data: on weekly and quarterly data the 80%
  interval covers close to 80% of the actual values at every step
  asked and for the totals; on daily data for the nearest days. On
  monthly data it covers less at every step: 75% one month ahead, 67%
  at twelve, 71% averaged over the months of a year. Every answer carries the coverage at
  each step asked, per grain (GK-46, `coverage`), so a caller reads
  it before it shows a range.
- **Totals from the same forecast.** The total of a quarter or a
  year comes with its own interval, combined from the periods'
  intervals.
- **A rolling-origin backtest.** With `origins`, the last periods of
  a series are each forecast from the history before them. The answer
  carries the actual value and its PIT (probability integral
  transform): the quantile level of the actual value in the forecast
  distribution. glossdb computes its expected ranges from it.
- **The published model, unchanged.** Measured through the API on a
  public benchmark, the service reproduces the scores published for
  Chronos-2.

## How it works

1. **The series of a request are forecast together** (GK-26), in
   groups of at most 100, so that they inform each other. A series at
   least half of whose known values are zero is forecast by itself
   (GK-66): forecast with the others, its intervals cover too little.
   An answer that held such a series has an entry for it in
   `assumptions`. Columns that are not targets are covariates. A
   covariate is known ahead where the rows ahead hold it for the
   whole horizon; where they do not, the answer names it. Nothing is
   recalibrated.
2. **Several target columns of one member** are one multivariate
   series (GK-36).
3. **A total** (GK-27, GK-34, GK-45, GK-48). Its median is the sum of
   the periods' medians. Its interval combines the periods' intervals
   as partly dependent, by one number per grain: 0.43 for months,
   0.22 for weeks, 0.12 for days, 0.25 for quarters. Each number is
   fitted: it is the equicorrelation at which the total's 80%
   interval covered 0.80 of the actual totals in the backtests. It is
   a width for the interval, written as a correlation, and it takes
   up whatever bias the medians carry. It is not a measured
   dependence between the periods' errors; per dataset it runs from
   0.0 to 1.0. Every answer with a total names the value it used and
   the data it was fitted on.
4. **`width`** (GK-35): a factor from the caller, applied to every
   quantile's distance from the median after the model has answered.
   For a caller whose stored forecasts and actual values show that
   the intervals are too narrow or too wide.
5. **The backtest** (GK-42): with `origins`, each origin is forecast
   from the history up to it. The PIT is read against the model's
   own 21 quantile levels, by linear interpolation. With a backtest
   the answer carries a result: a score and a state over the newest
   point of every member (`docs/contract.md`).
6. **Coverage** (GK-46): every answer carries, per step and span and
   per interval the request's levels form, the share of actual values
   inside such an interval on held-out data at that grain, with its
   standard error, the points behind it and the number of datasets
   pooled (`coverage.py`). A span's share was measured at the
   dependence of its grain. If a grain's dependence changes, the
   service serves that grain's steps and no spans until the table is
   measured again.

## What was measured

The method is the rolling-origin backtest, through the HTTP API: a
series is cut at a past date, the periods after it are forecast and
compared with the actual values. Origins are three months apart, up
to nine per series. Loss is the quantile loss relative to a naive
baseline: the better of the last value and the value a year earlier,
chosen on the twelve origins before the first tested one. A loss
under 1.00 is better than the baseline.

- **Development data:** 316 public and 218 business-shaped monthly
  series (Stack Exchange counts, a bank's accounts, SAP orders of 36
  months, shelf scenarios; all public datasets, none a company's
  finance data), and three weekly retail datasets (113 series). The
  table calls the business-shaped series "enterprise data".
- **Held-out data,** run after the configuration was fixed: 69
  monthly series in 4 datasets (headcount, product reviews, clinical
  trials) and 80 weekly series in 2 datasets (a fashion retailer, a
  grocery). For the coverage by step, also 87 daily series in 3
  datasets (cash machines, a grocery delivery's warehouses, a
  grocery) and 96 quarterly series in 4 (tourism, clinical trials).
- **A public benchmark:** the 25 tasks of fev-bench that forecast a
  quarter to two years ahead, scored by the benchmark's own package.

| Question | Measured |
|---|---|
| How accurate is it? (GK-26) | Loss 0.79 and 0.78 a quarter and a year ahead on public data, 0.74 and 0.67 on enterprise data. The 80% interval covers 0.78 to 0.80 |
| Does forecasting together help? | Better or level on 18 of 21 datasets a year ahead; worse by 0.02 at most, better by up to 0.11 |
| Does the accuracy hold on held-out data? (GK-28) | Yes. Monthly: loss 0.64 to 0.78. Weekly: 0.66 to 0.75 |
| Do the intervals hold on held-out data? (GK-28) | The next month: 0.79. Months of the quarter: 0.74. Months of the year: 0.71. Weekly: 0.75 for the next week, 0.80 and 0.82 for the weeks of the quarter and the year |
| Is the total's interval right? (GK-27) | With the periods taken as partly dependent, the 80% interval of a year's total covered 0.78 on enterprise data (1,293 points). Added up as fully dependent: 0.88. As independent: 0.41 |
| How dependent are the periods? (GK-45) | The value at which a total's interval covers 0.80: 0.43 ± 0.02 for months (7,816 totals), 0.22 ± 0.04 for weeks (947). Per dataset it runs from 0.0 to 1.0 |
| How dependent are days and quarters? (GK-48) | 0.12 ± 0.01 for days (421 calendar-month totals), 0.25 ± 0.06 for quarters (864 year totals), fitted on the held-out daily and quarterly datasets, the only daily and quarterly data in the evaluation. Fitted on the other datasets and read on the one left out, the month's total of days covers 0.81 and the year's total of quarters 0.81. Per dataset 0.09 to 0.23 for days, 0.17 to 1.0 for quarters |
| Does a total need the grain? (GK-34) | Yes. With the monthly value, weekly totals covered 0.91 to 0.97. With the weekly value: 0.78 and 0.81 |
| Is the one-step backtest calibrated? (GK-42) | Coverage 0.78 to 0.84 at a nominal 0.80. The PITs are within 0.05 of uniform, a fifth of them at the edges |
| Does a covariate changed ahead move the forecast as the member's history says? (GK-47) | The direction: on 88% and 95% of the series whose history shows an effect (the what-if's tables, development and held out), against 93% and 96% for `/whatif` on the same series. The size: 0.51 and 0.24 of the history's at the median. Where the history shows no effect the forecast still moves, by a tenth of the column's spread, on 31% and 51% of the series |
| At which steps does the 80% interval hold, on held-out data? (GK-46, GK-48) | Weeks: 0.79 to 0.84 at each of 13 steps, 0.85 for the quarter's total. Quarters: 0.79 to 0.82 at each of 4 steps, 0.80 for the year's total. Months: 0.75 one month ahead, 0.67 at twelve, no step at 0.80; 0.72 and 0.67 for the quarter's and the year's total. Days: 0.79 one day ahead, 0.69 to 0.80 from four to thirty-one days; 0.79 for the calendar month's total. The totals of days and quarters are fits on the datasets their dependence was fitted on |
| Several columns per series? (GK-36) | The same forecast as the columns sent as separate series in one request, to the last digit. A column sent alone loses 1 to 4% on two columns of four |
| Does the service reproduce the published scores? | Skill score 38.0% against 38.0% published; 18 tasks equal to three decimals, the rest within 0.005 |
| Weekly data? (GK-31) | Loss 0.68 to 0.74 on development data, 0.66 to 0.72 held out. On fev-bench's weekly tasks 42% skill, level with the published model |

The coverage numbers the service serves are in `coverage.py`, with
the standard error and the points behind each.

## Where it is weak

- **Series that are mostly zero** (spare parts). Forecast with the
  others, the 80% interval covered 0.57 to 0.71 (GK-29). Forecast by
  themselves, as the service does, 0.83 and 0.85 (GK-66), at a loss
  of 0.5 to 0.7 of the naive baseline's.
- **Macroeconomic indicators:** worse than the naive baseline for the
  next month and the quarter (loss 1.24 and 1.12).
- **Short history:** 36 months give one origin; coverage 0.54 to
  0.83.
- **A year ahead on held-out monthly data** the interval is too
  narrow: it covers 0.71 where 0.80 is nominal. The datasets behind
  it are product reviews that grow year on year (0.69) and a
  headcount (0.72) (GK-28).
- **The total of a year on a single dataset:** coverage between 0.47
  and 1.00 where the pooled value is 0.78. One dependence per grain
  is a default for a caller without stored forecasts of its own.
- **A covariate changed ahead** moves the forecast whether or not
  the member's own history shows an effect, and by about half the
  history's size where it does (GK-47). The effect of a changed input
  is the question `/whatif` answers.
- **The dependence of days and quarters is fitted on few datasets:**
  three daily and four quarterly, all held out (GK-48). Per dataset
  the value runs from 0.09 to 0.23 for days and from 0.17 to 1.0 for
  quarters. At the value the other datasets give, a grocery
  delivery's month totals cover 0.72 (36 totals) and the clinical
  trials' year totals 0.56 and 0.71 (72 each), where the cash
  machines, the grocery and the tourism sets cover 0.81 to 0.84.

## What a caller should know

- **A request holds series that belong together:** the metrics of
  one company, the stores of one chain.
- **A member needs 6 known values** of a target behind an origin. A
  column that has fewer fails on its own. The error names its origin
  in the table's own type, as text where it is a date.
- **Name the grain** when you ask for a total on data that is not
  monthly. Only the interval of a total uses it.
- **Store the forecasts** and the actual values that follow them.
  They show a user how good past forecasts were, and a `width` is
  computed from them.
- **Flagging an unusual value with the next period's interval.**
  Measured on held-out data: the false-alarm rate was 0.10 on monthly
  data and 0.13 on weekly data, where 0.10 is nominal.

## Not measured

- **Timings on a server GPU** (GK-20). On a laptop with `mps`,
  Chronos-2 runs on the CPU.
- **The dependence of days and quarters on data outside the fit.**
  Every daily and quarterly dataset of the evaluation went into the
  fit; leaving one dataset out is the check that was run (GK-48).
- **Five benchmark tasks** of more than 5,000 forecasts each.
