# The what-if

What `/whatif` is for, how it works, what was tested and found, and
what is not known. The wire form is in `docs/contract.md`.

Decided on 2026-10-01:

- **The use case** is business information: steering a company. Which
  way a number moves under a change, roughly how much, which plan is
  better, with a range. Not exploring the depths of a dataset.
- **One model:** TabICL. **Real data only.**
- **The answer has to be explained.** Not just "42": how the data
  points relate, the confidence, the error. Showing that it is a
  guess, and by how much, helps a user understand it.

## What the route promises

- **A direction, not a size.** The sign is stated where two estimates
  agree: the member's own history shows the column higher where the
  input was higher, the calendar and the other inputs held, and the
  model moves it the same way. It is an association in that history,
  read for the same period; whether the input caused it rests on the
  caller's assumption that nothing which set the input also moved the
  column, declared on every answer.
- **The size as a guess between two estimates.** The model's shift
  and the history's own estimate are both returned. The model errs
  low more often than high.
- **The error, measured on the caller's own table.** The last periods
  of the history are held back, predicted and compared.
- **"No effect visible" is not "no effect".** It says that this
  member's data does not show one.

No table records what would have happened under an input that was
not changed. So the direction cannot be scored against a truth; the
agreement of the two estimates is the evidence there is, and it is
one piece of evidence, not two. The model reads the same rows with
the same features as the regression (the calendar's cycles and the
inputs; TabICL's forecaster holds no lagged target), so their
agreement says the association holds under a linear and under a
flexible fit. A lever set by something that also moved the column (a
promotion scheduled when demand was expected high) gets a confident
sign from both, and the wrong one where the lever's own effect goes
the other way; on synthetic series with such a lever the review of
2026-10-17 showed it on every series. Evidence of another kind is
listed under what could come next.

## How it works

Every member is read on its own, and every target on its own
(`src/glossthesi/whatif.py`).

1. **Three predictions per target** by TabICL's forecaster as its
   package ships it: the held-back periods, the periods ahead with
   the inputs as planned, and the same under the change. The package
   builds its own time features; the other columns are inputs known
   ahead. The weights are the ones the service already holds.
2. **The history is read** for how the target moved with the changed
   input: a slope, with the other inputs, the trend, the year's cycle
   and, by grain, the day of the week and the hour of the day held.
   It is read twice, on the rows and on their changes from one row to
   the next; a drift two columns merely share shows only in the
   first. The history is drawn again 300 times in runs of
   neighbouring rows for a 90% interval; a run is at most a tenth of
   the history, so that there are runs to draw from (a run as long as
   the history gave the history back 300 times and an interval of no
   width, found on a 5-minute table in the review of 2026-10-17).
3. **The sign.** `+` or `-` where both readings point the same way
   with neither interval over zero, and the model's shift goes that
   way. `0` where the history shows nothing and the model's shift is
   under a tenth of what the history's regression leaves of the
   column, its residual spread (since 2026-10-17; before, a tenth of
   the column's whole spread, which on a trending column is mostly
   the trend, so that a shift of several residual spreads passed as
   none). `?` otherwise, with the
   reason: the two disagree; the input never varied; it cannot be
   told apart from another input; the change, or the input as planned,
   goes beyond what the history holds; the history is too short.

## What was tested

Between 2026-10-01 and 2026-10-02, by one procedure: cut a
table at a date, predict the period after from the rows before,
compare with what was recorded; change one input for that period by
an ordinary amount; compare the model's shift with how the column
moved with that input in the history.

- **Data:** the 30 real tasks of fev-bench that carry inputs known
  ahead, from eleven sources in four domains: retail, electricity
  prices, electricity load and generation, air quality.
- **Five checkpoints,** the last on six sources kept back until the
  rules were fixed.
- **An instance space analysis** (Smith-Miles and Muñoz, ACM
  Computing Surveys 2023, with the authors' package) on 20,918
  instances: one series in one grain and window, one input changed,
  one dependent column. Fifteen features, each computable from a
  caller's table beforehand. Each source was left out in turn.

Two limits of this evidence, to read before the table. The yardstick
is the history's straight line, itself an estimate, read on the same
rows the model reads; "agrees on 94%" is agreement with it, not with a
truth, and a model indifferent to the input would agree on about half.
And the instances are not independent: one series gives several, and
one source (Rossmann, 152 stores) gives most of the instances where
the history shows an effect, so counts of instances overstate the
evidence.

| Question | Finding |
|---|---|
| Does the model invent effects? | No. Where the history shows nothing (17,179 instances) its shift is under a tenth of the column's spread on 91% |
| Does it get the direction? | Where the history shows an effect (3,739 instances) it agrees on 94%. On a source left out: 95 to 100% on four sources with many series, 87% on M5, 77 to 90% on three energy sources with few series |
| Does it get the size? | Not reliably. Its shift is within half to one and a half times the history's on 47%, under half on another 47% (the wrong way on 5%) |
| Is there a shape of table under which the size holds? | None that carries to a new source. The analysis found a region with 85% good against 34% outside, on the sources it was built from. With Rohlik left out: 42% inside it. With Rossmann left out: none of its 990 instances fell inside, though 83% were good |
| Where was the size right? | For a switch someone sets (a promotion, a discount) that explains a fifth or more of the column, is pinned by the history to within about ±30% and stands apart from the other inputs: 98% of 597 instances. Only two sources hold such an input: Rossmann (152 stores, 99%) and Rohlik (25 products, 80%) |
| And for measured inputs? | A forecast, a temperature, a count of orders: the size is about half the history's, whatever the number of rows |
| Is the plain prediction close? | The error is under one spread of the column on nine columns in ten; the stated 80% range held between 70 and 90% on most |

The size depends on the source more than on any feature found: good
on 83% of Rossmann's instances, 55% of the electricity prices', 45%
of Favorita's, 38% of Rohlik's, and 6 to 30% on the rest.

## The evaluation that runs today

The evaluation sends real tables through the route. Every series
is cut one horizon before its end; the period after the cut is sent
as recorded and with one input moved by an ordinary amount. Twelve
tables, chosen by the kind of input: a switch someone sets, a
calendar flag, a count, a price, a forecast, a measured temperature.
Seven are development tables (retail, electricity prices, electricity
load, air quality); five were held out (a grocery delivery, a fashion
retailer, a store chain, Nord Pool prices, a load competition).

Run of 2026-10-02:

| | Development | Held out |
|---|---|---|
| Series and columns answered | 219 | 234 |
| A sign stated | 117 | 53 |
| No effect visible | 64 | 69 |
| Not answerable | 38 | 112 |
| The model agrees where the history shows an effect | 93% of 126 | 96% of 55 |
| The model stays still where it shows none | 90% of 71 | 86% of 80 |

What the run showed beyond the counts:

- **An input that works in both directions.** Warmer weather lowers
  the electricity load in winter and raises it in summer. The
  history's straight line over half a year takes one direction for
  both. On the development table, cut in June, the line points down,
  the last 45 days alone point up, and so does the model; on the
  held-out table, cut in December, it is the other way round. The
  route states no sign on 18 of these 19 series. All eleven
  disagreements of the run are these two tables: here a disagreement
  says the history's line is the wrong yardstick, not that the model
  is wrong.
- **A planned input beyond the history.** 31 of the 112 held-out
  answers without a sign: the input as recorded after the cut already
  lay above anything before it (the orders of a growing business),
  before any change. The route does not answer there and says that it
  is the planned input, not the change.
- **An input that follows the calendar.** Air quality, a year of
  days: the season leaves a tenth of the temperature's variation, the
  humidity columns a third of that, and none of the four columns is
  answered. Nord Pool's load forecast: the hour, the weekday and the
  season leave 6% of it. One year cannot tell a temperature from the
  time of year.
- **A mostly empty input.** The store chain's five markdown columns
  are empty in nine rows of ten. Left out of the history's reading,
  they no longer stand in the way: a holiday week is read on all 30
  stores (a sign on 4, no effect visible on 25), where before the
  rule none could be read. Asked about, a markdown gets no answer on
  any store. Its fuel price is not told apart from the other inputs
  and the calendar on 25 of 30.
- **An input that never moved.** 46 series and columns were not asked
  at all: the input never varied in their history (26 of 30 M5 items
  kept one price), so there was nothing ordinary to change it by.

A table of one series or a few is cut at several dates, one horizon
apart; those answers share most of their history and count for less
than their number.

**Run of 2026-10-17, development tables, after the still rule moved
to the residual spread** (step 3.2 of the plan of that day). A sign on
117 as before, and the model agrees on 93% of 126 as before. No effect
visible on 41 where it was 64; not answerable on 61 where it was 38;
where the history shows nothing the model stays still on 58% of 71,
against 90% at a tenth of the column's whole spread. The difference
is two tables: Rossmann's school holiday, where 14 of 34 answers that
were "no effect visible" are now "the model moves it", and the hourly
load's temperature and radiation, 14 of 17. There the model answers a
change the history's straight line does not show on both readings;
at a tenth of the whole spread, which on those columns is many times
the spread the regression leaves, those moves had passed as none. The
90% was the threshold's, not the model's. The held-out tables were not
run (the held-out budget, `../glosstheval/docs/evaluation.md`).

## Candidates behind the three predictions

On 2026-10-16 two forecasting models, Timestral-209M and t0-gamma,
were put behind the what-if's three predictions in place of TabICL's
forecaster, with the history's reading unchanged, on the seven
development tables of the run above (GK-53 in the evaluation's
findings). Neither reached it: the model moved the column the
history's way on 84% and 91% where TabICL's does on 93%, and stayed
still where the history shows nothing on 82% and 75% against 90%; the
size ran at 0.88 and 0.77 of the history's against 0.99. Both took a
tenth of its time. A member's targets forecast together, which both
can and TabICL's forecaster cannot, changed nothing (GK-55). TabICL
stays; the adapters live in the repository's history.

## Several inputs changed at once

Built on 2026-10-16, after the forecast route had been read on it
(GK-50). A request may change several inputs between the plan and
the scenario. The model predicts the scenario rows as they are; the
history's estimate is the sum of what each changed input does, from
the one regression that holds every input, with its interval from the
same resampled draws summed the same way, and the sign is stated for
the joint change. An input that fails a check (mostly empty, never
varied, beyond the history, not told apart) is named in the reason.
What the history cannot read is an interaction, a promotion working
differently at a lower price: its straight line adds the two
effects, so a model that read an interaction would disagree with it
and the route would state no sign.

Two things make the multi-lever answer as safe to read as the
single one (on Philipp's word, the same day). The answer object
carries `parts`: per member, column and changed input, what that
lever alone does over the periods ahead with its interval, from the
same regression, so the parts' estimates add up to the joint
estimate (their intervals do not: the joint interval is the one to
read) and a user sees which lever carries the effect and when two
cancel. The single and the joint reading are one regression, so that
the parts equal the single readings is a property of the code, kept
by a test, not a finding. And
several changed inputs are checked together: each may lie inside its
own range while the history never held the combination (a promotion
and a holiday that never fell together); where no row of the history
lies near the scenario's combination, nearer than the loneliest
history row lies from its own nearest neighbour in that space, the
route states no sign and says whether the plan or the change goes
beyond together. The reach is the history's own, with no number to
choose; it is declared as an assumption on every answer. On the three
tables the check fires on 5 of 80 series and columns: the German
market's two forecasts as planned on 4, a change on the hourly load
on 1, none on Rossmann, where promotion and holiday have fallen
together; the parts equal the single-lever readings to the digit,
and the direction holds on all 64 where the history shows the joint
effect.

Tested the same day on the three development tables with two inputs
named (80 series and columns; GK-50 in the evaluation's findings):
where the history's joint estimate is off zero on both readings (65),
the model moves the column that way on 98%, its shift 1.06 of the
history's estimate at the median and within half to one and a half
times on 89% (the history's joint estimate is the sum of the parts by
construction, so this is consistency with additivity, not a
validation of the size); Rossmann's promotion with school holiday 1.06 on sales
and 1.04 on customers with the direction on every store. Where the
history shows nothing of the pair (15, the energy tables) the model
moves anyway, as it does there with one input. The German market's
two forecasts show an effect each and none together, their estimates
pulling apart, and the route states no sign: the right answer to a
pair that cancels.

## Where the history's interval is weak

The 90% interval of the history's slope comes from the history drawn
again in runs of neighbouring rows. Under no effect and autocorrelated
noise it covers zero on about 35 of 40 synthetic series on 30 weekly
rows (31 of 40 before the run was capped at a tenth of the history)
and 34 of 40 on 400 daily rows; on 1,000 hourly rows whose noise
carries over at 0.97 from one hour to the next it covers 28 of 40 on
the rows and 36 of 40 on their changes: a run of a day is shorter
than that dependence (Künsch 1989; Politis and White 2004 on the
block length). The sign, which needs both readings off zero the same
way, fires on 2 to 9% of such series where 10% is the most it should.
The run length per grain is declared in `assumptions`; a longer run
or a random one (Politis and Romano 1994) is step 3 of the plan of
2026-10-17.

## Not covered

A change beyond what the history holds; one member's input moving
another member; columns tied by a definition being made to agree
with it; an interaction between two changed inputs.

## What could come next

- **Evidence of another kind for the direction** (the plan of
  2026-10-17, step 4): a placebo on the lead of the input (a lever
  that predicts the column before it is applied signals anticipation
  or a common cause), a step check at past changes of the lever (the
  forecaster run with the lever held, against what happened),
  negative-control columns the lever cannot touch, a permuted lever
  as the chance line, decreases beside increases; then data with a
  truth.
- **One lag of the input in the history's reading** (step 3): the
  same-period slope misses a dip after a promotion and a price's
  carry-over, and can state the sign of a sustained change wrong.
- **Trial data** as the history for a what-if.
- **A model trained on interventions.** CausalLongPFN (Zare et al.,
  arXiv 2606.05797, 2026) takes a history and a planned future input,
  the what-if's own form. It takes up to 60 rows of history, 5 ahead
  and an input of four actions, and was checked against a truth only
  on medical simulators. It could be tried on the weekly switch
  inputs here.
- **A response curve:** several strengths of a change in one request.
- **Formulas for defined columns** in the specification, evaluated by
  the service, for definitions that are plain arithmetic.
- **The history read near the scenario's own conditions** (the same
  season, the latest periods), for an input that works in both
  directions.
- **A margin beyond the history's range,** for an input that grows.
  What margin still gives the right direction would have to be
  measured.
- **Other test data and scenarios.**

## What was read

- Golfarelli, Rizzi and Proli, "Designing What-if Analysis: Towards a
  Methodology" (DOLAP 2006), in full. A what-if measures how changes
  in what a manager controls move what the manager watches. It is
  validated by "running the simulation on a past period and comparing
  the prediction obtained with the actual values recorded". A model
  built from history "captures effects, not causes".
- Gathani, Liu, Haas and Demiralp, "What-if Analysis for Business
  Professionals" (CHI 2025), in full. 22 professionals; what they
  need most is to see how a prediction was made and how reliable it
  is.
- Smith-Miles and Muñoz, "Instance Space Analysis for Algorithm
  Testing" (ACM Computing Surveys 2023), in full.
- Druzdzel and Henrion, "Efficient Reasoning in Qualitative
  Probabilistic Networks" (AAAI 1993), but for the proofs: the
  propagation of signs. Carter (arXiv 2208.09344, 2024), abstract and
  conclusions: passing a sign backwards against a link is wrong
  unless the variables are yes or no.
- Do-PFN (NeurIPS 2025) and CausalPFN (2025), abstracts and stated
  limits: for tables without time. CausalLongPFN: methods,
  experiments, limits.

## Where the record is

The evaluation, the records of its runs and the step-by-step account
of the checkpoints and the instance space analysis are kept with the
service's evaluation, in a repository of its own that is not public.
