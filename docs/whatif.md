# The what-if

What `/whatif` answers, how it works, what was measured and where it
is weak. The request and answer format is in `docs/contract.md`.
Status: preview. glossdb computes its scenarios from this endpoint. A
tag in brackets (GK-50) names a finding in the evaluation.

- **The use case** is steering a business: the direction a number
  moves under a change, roughly how much, and which plan is better,
  with a range.
- **One model:** TabICL's forecaster. The measurements use real data.
- **The answer is explained.** It carries the reason, the confidence
  and the error, so that a user sees it is an estimate and how
  uncertain it is.

## What the endpoint answers

- **A direction, not a size.** The sign is stated where two estimates
  agree: the member's own history shows the column higher where the
  input was higher, with the calendar and the other inputs held
  constant, and the model moves it the same way. It is an association
  in that history, read for the same period and the period before
  (one lag of the input is held). Whether the input caused it rests
  on the caller's assumption that nothing which set the input also
  moved the column. Every answer declares that assumption.
- **The size as a range between two estimates.** The model's shift
  and the history's own estimate are both returned. The model errs
  low more often than high. On an input that rarely changed in the
  history the model barely moves, and the history's estimate is the
  one to read (measured against a known causal graph, below).
- **The error, measured on the caller's own table.** The last periods
  of the history are held back, predicted and compared: the error of
  the level, one window. Beside it is the residual spread of the
  history's regression (`history_error`): the spread the history's
  estimate of the change is read against.
- **"No effect visible" is not "no effect".** It says that this
  member's data does not show one.

No table holds what would have happened under an input that was not
changed. So on business data the direction cannot be scored against
a known answer. The agreement of the two estimates is the evidence,
and it is one piece of evidence, not two. The model reads the same
rows with the same features as the regression: the calendar's cycles
and the inputs. TabICL's forecaster holds no lagged target, and the
lag of the changed input is the regression's alone. So their
agreement says the association holds under a linear and under a
flexible fit.

An input set by something that also moved the column (a promotion
scheduled when demand was expected high) gets a sign from both
estimates, and the wrong one where the input's own effect goes the
other way. Synthetic series with such an input showed this on every
series.

## How it works

Every member is read on its own, and every target on its own
(`src/glossthesi/whatif.py`).

1. **Three predictions per target** by TabICL's forecaster as its
   package ships it: the held-back periods, the periods ahead with
   the inputs as planned, and the same under the change. The package
   builds its own time features; the other columns are inputs known
   ahead. The weights are the ones the service has loaded.
2. **A regression on the history** gives how the target moved with
   the changed input: a slope, with the other inputs, the trend, the
   year's cycle and, by grain, the day of the week and the hour of
   the day held constant.
   - Beside the input is its value one period before (`LAG`). The
     period after the input moves has its own slope: a dip after a
     promotion, a price's carry-over. The effect of a sustained
     change is the sum of the two slopes.
   - The history's estimate for a period ahead is the change made
     there times the first slope, plus the change made the period
     before times the second. The first period ahead has only the
     first: the row before it is history.
   - Where the lag's slope is off zero on both readings, the reason
     says whether the effect carries into the period after or the
     column moves back then.
   - The slope is read twice: on the rows, and on their changes from
     one row to the next. A drift that two columns merely share shows
     only in the first.
   - For a 90% interval the history is resampled 300 times in runs of
     neighbouring rows (a block bootstrap). A run is at most a tenth
     of the history, so that there are at least ten runs to draw
     from.
3. **The sign.**
   - `+` or `-` where both readings point the same way with neither
     interval over zero, and the model's shift goes that way.
   - `0` where the history shows nothing and the model's shift is
     under a tenth of the residual spread of the history's
     regression.
   - The sign is the horizon's as a whole: the mean change over the
     periods ahead. Where the history's estimate is off zero upward
     in some periods and downward in others (two inputs changed in
     different periods, a change that reverses), the reason says so
     beside the sign, with the counts. Where such a change nets to
     nothing over the horizon the answer is `?`, not `0`.
   - `?` otherwise, with the reason: the two estimates disagree; the
     input did not vary; it cannot be told apart from another input;
     the change, or the input as planned, goes beyond the history's
     range; the history is too short.

## What was measured

The procedure: cut a table at a date, predict the period after from
the rows before, and compare with the actual values. Then change one
input for that period by an ordinary amount, and compare the model's
shift with how the column moved with that input in the history.

- **Data:** the 30 real tasks of fev-bench that carry inputs known
  ahead, from eleven sources in four domains: retail, electricity
  prices, electricity load and generation, air quality.
- **Five rounds of measurement,** the last on six sources kept back
  until the rules were fixed.
- **An instance space analysis** (Smith-Miles and Muñoz, ACM
  Computing Surveys 2023) on 20,918 instances: one series in one
  grain and window, one input changed, one dependent column. Fifteen
  features, each computable from a caller's table beforehand. Each
  source was left out in turn.

Two limits of this evidence:

- The reference is the history's regression line. It is itself an
  estimate, read on the same rows the model reads. "Agrees on 94%" is
  agreement with it, not with a known answer. A model indifferent to
  the input would agree on about half.
- The instances are not independent. One series gives several, and
  one source (Rossmann, 152 stores) gives most of the instances where
  the history shows an effect. Counts of instances overstate the
  evidence.

| Question | Measured |
|---|---|
| Does the model invent effects? | Where the history shows nothing (17,179 instances) its shift is under a tenth of the column's whole spread on 91%. The `0` sign uses a stricter threshold, a tenth of the residual spread; at that threshold the model stays under it on 58% of 71 series and columns (below) |
| Does it get the direction? | Where the history shows an effect (3,739 instances) it agrees on 94%. On a source left out: 95 to 100% on four sources with many series, 87% on M5, 77 to 90% on three energy sources with few series |
| Does it get the size? | Not reliably. Its shift is within half to one and a half times the history's on 47%, under half on another 47%, the wrong way on 5% |
| Is there a shape of table under which the size holds? | None that carries to a new source. The analysis found a region with 85% good against 34% outside, on the sources it was built from. With Rohlik left out: 42% inside it. With Rossmann left out: none of its 990 instances fell inside, though 83% were good |
| Where was the size right? | For a switch someone sets (a promotion, a discount) that explains a fifth or more of the column, that the history estimates to within about ±30% and that can be told apart from the other inputs: 98% of 597 instances. Only two sources hold such an input: Rossmann (152 stores, 99%) and Rohlik (25 products, 80%) |
| And for measured inputs? | A forecast, a temperature, a count of orders: the size is about half the history's, whatever the number of rows |
| Is the plain prediction close? | The error is under one spread of the column on nine columns in ten. The 80% interval covered between 70 and 90% on most |

The size depends on the source more than on any feature found: good
on 83% of Rossmann's instances, 55% of the electricity prices', 45%
of Favorita's, 38% of Rohlik's, and 6 to 30% on the rest.

## Measured through the endpoint

The evaluation sends real tables through `/whatif`. Every series is
cut one horizon before its end. The period after the cut is sent as
it was and with one input changed by an ordinary amount. Twelve
tables, chosen by the kind of input: a switch someone sets, a
calendar flag, a count, a price, a forecast, a measured temperature.
Seven are development tables (retail, electricity prices, electricity
load, air quality); five were held out (a grocery delivery, a fashion
retailer, a store chain, Nord Pool prices, a load competition).

| | Development | Held out |
|---|---|---|
| Series and columns answered | 219 | 234 |
| A sign stated | 117 | 53 |
| The model agrees where the history shows an effect | 93% of 126 | 96% of 55 |
| The model's shift is under the `0` threshold where the history shows none | 58% of 71 | 86% of 80, at a tenth of the whole spread |

The development column is measured under the current rules. The
held-out tables were measured with the `0` threshold at a tenth of
the column's whole spread and without the lag, and were not run
again. Under those rules they gave no effect visible on 69 answers
and not answerable on 112.

What the runs showed beyond the counts:

- **An input that works in both directions.** Warmer weather lowers
  the electricity load in winter and raises it in summer. The
  history's regression line over half a year takes one direction for
  both. On the development table, cut in June, the line points down,
  the last 45 days alone point up, and so does the model; on the
  held-out table, cut in December, it is the other way round. No sign
  is stated on 18 of these 19 series. All eleven disagreements of the
  first run are these two tables. Here a disagreement means the
  history's line is the wrong reference, not that the model is wrong.
- **A planned input beyond the history.** 31 of the 112 held-out
  answers without a sign: the input after the cut already lay above
  every value before it (the orders of a growing business), before
  any change. The endpoint states no sign there, and the reason says
  that it is the planned input, not the change.
- **An input that follows the calendar.** Air quality, a year of
  days: the season leaves a tenth of the temperature's variation, the
  humidity columns a third of that, and none of the four columns is
  answered. Nord Pool's load forecast: the hour, the weekday and the
  season leave 6% of it. One year of data cannot tell a temperature
  from the time of year.
- **A mostly empty input.** The store chain's five markdown columns
  are empty in nine rows of ten. They are left out of the history's
  regression, so a holiday week is read on all 30 stores (a sign on
  4, no effect visible on 25). A changed markdown gets no sign on any
  store. The chain's fuel price is not told apart from the other
  inputs and the calendar on 25 of 30.
- **An input that did not vary.** 46 series and columns were not
  sent: the input did not vary in their history (26 of 30 M5 items
  kept one price), so there was no ordinary amount to change it by.
- **Where the `0` threshold matters.** Two development tables hold
  most answers where the history shows nothing and the model moves
  more than a tenth of the residual spread: Rossmann's school
  holiday (14 of 34) and the hourly load's temperature and radiation
  (14 of 17). On those columns the whole spread is many times the
  residual spread.
- **The lag** (GK-57, GK-58). On the development tables the lag's
  slope is off zero on both readings on 31 of 219 answers. The effect
  carries into the period after on 13: the German market's PV and
  wind forecast on 7 of 8 cuts, Rossmann's school holiday on 6 of 60.
  The column moves back in the period after on 18: the German
  market's load forecast on 8 of 8, the hourly load's temperature on
  7 of 12, the school holiday on 3. On Rossmann's promotion it shows
  on none of 60: a weekly promotion's effect is in its week. The
  model's scenario lies inside the history's 90% interval on 67% of
  197 series: 76 to 90% on Rossmann's weekly stores, 55 to 71% on the
  daily tables, 10 to 44% on the hourly ones, where the interval is
  narrow (below).

A table of one series or a few is cut at several dates, one horizon
apart. Those answers share most of their history and count for less
than their number.

## Several inputs changed at once

A request may change several inputs between the plan and the
scenario (GK-50). The model predicts the scenario rows as they are.
The history's estimate is the sum of what each changed input does,
from the one regression that holds every input, with its interval
from the same resamples summed the same way. The sign is stated for
the joint change. An input that fails a check (mostly empty, did not
vary, beyond the history's range, not told apart) is named in the
reason.

The history's regression does not read an interaction, such as a
promotion that works differently at a lower price: its line adds the
two effects. A model that read an interaction would disagree with it,
and no sign would be stated.

Two things support reading an answer for several inputs:

- **`parts`.** The answer object carries, per member, column and
  changed input, what that input alone does over the periods ahead,
  with its interval, from the same regression. The parts' estimates
  add up to the joint estimate. Their intervals do not: the joint
  interval is the one to read. A user sees which input carries the
  effect and when two cancel. A test in this repository checks that
  the parts equal the single-input readings.
- **A check on the combination.** Each input may lie inside its own
  range while the history holds no row with that combination (a
  promotion and a holiday that did not fall together). Where the
  scenario's combination lies farther from every history row than
  the distance within which 99 of 100 history rows find their
  nearest neighbour, in more than a tenth of the periods ahead, no
  sign is stated, and the reason says whether the plan or the change
  goes beyond the history. The reach is a quantile so that one
  mistyped row does not widen it. Both numbers are declared on every
  answer.

Measured on the three development tables with two inputs changed (80
series and columns):

- The combination check fires on 22 (GK-56): the German market's two
  forecasts as planned on 4, and 18 of Rossmann's 60 series, the
  stores where a promotion and a school holiday did not fall
  together.
- Where the history shows the joint effect, the model agrees on 46
  of 47, with a size of 1.06 of the history's estimate.
- 15 of Rossmann's signs carry the note that the history's estimate
  points both ways over the periods ahead, since a store's weeks
  differ in which input changes. 3 series whose change nets to
  nothing answer `?`.
- The German market's two forecasts show an effect each and none
  together: their estimates cancel, and no sign is stated.
- The history's joint estimate is the sum of the parts by
  construction. So agreement on the size shows consistency with
  additivity; it does not validate the size.

## Measured against a known causal graph

The measurements above score the sign against the history's own
regression line, which the model also reads. The sign was also
scored against what a change really did, on the Causal Chambers wind
tunnel (Gamella, Peters and Bühlmann, Nature Machine Intelligence
2025; GK-60). It is a real device whose actuators are set by protocol
and whose causal graph is published. The intake fan's load drives its
speed, its current and the pressures inside the tunnel. The outlet
fan's load drives its own speed and current. Neither affects the
ambient pressure or the speaker's signals. The data: ten runs where
the intake load alone steps between four values, and sixteen where
every actuator moves at random; 104 members, with a week per row by
convention.

- **Every sign stated was right:** 233 of 233, on the fan speeds, the
  currents and the two pressures inside the tunnel, under both loads.
- **Columns the changed input cannot affect answered `0` or `?` on
  335 of 344.** A sign on 9 (2.6%). The rule's two 90% intervals
  allow up to 10%. This is the false-positive rate against the known
  graph.
- **A sign was stated where there is an effect** on 75 to 100% of the
  answers in the random runs, and on 17 to 75% in the step runs,
  where the load took four values and stepped nine times in 2,100
  rows. There the model's shift was a median 0.3% of the true change
  on the fan's speed, 1 to 2% on the pressures and 22% on the
  current, while the history's regression had 79 to 89% of it, and
  most answers said the two disagree. On an input that rarely
  changed, the model barely moves; a list price that changed four
  times in four years is such a case.
- **The device is not a business.** The fan has a threshold near a
  load of 0.15 that a regression line averages over. The business
  tables above measure the agreement; this device measures the sign.

On the business tables the same two questions were asked without a
known answer, by refutation tests (GK-61):

- **The chance rate.** The changed input's history is shifted along
  the member's rows, so that its own autocorrelation stays and its
  alignment with the column goes. A sign is stated on 8 of 219
  answers: 3.7%, with a standard error of 0.9 clustered by table.
  The rule's two 90% intervals allow up to 10%.
- **The false-positive rate.** Another member's column under this
  member's changed input gets a sign on 0 of 32 where the input is
  the member's own. Where every member shares the input (a chain's
  promotion, a market's forecast, the weather) the other member's
  column responds to it, so that control carries no information.
- **Stability.** A column of noise among the inputs leaves the sign
  on 116 of 117. Half the history keeps it on 104 of 117 and flips
  none. The mirrored decrease flips it on 105 of 111 and loses 6
  where the decrease meets the history's lower bound.

## Where the history's interval is weak

The 90% interval of the history's slope comes from the block
bootstrap described above. Measured on synthetic series with no
effect and autocorrelated noise:

- on 30 weekly rows it covers zero on about 35 of 40 series;
- on 400 daily rows, on 34 of 40;
- on 1,000 hourly rows whose noise carries over at 0.97 from one
  hour to the next, on 28 of 40 on the rows and 36 of 40 on their
  changes. A run of one day is shorter than that dependence (Künsch
  1989; Politis and White 2004 on the block length).

The sign needs both readings off zero the same way. It is stated on 2
to 9% of such series, where the rule allows up to 10%. The run length
per grain is declared in `assumptions`.

## Not covered

- A change beyond the history's range.
- One member's input moving another member.
- Columns tied by a definition being made to agree with it.
- An interaction between two changed inputs.
- An effect that reaches further back than one period.
