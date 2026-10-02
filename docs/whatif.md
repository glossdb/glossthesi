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
  agree: the member's own history shows the input moving the column,
  and the model moves it the same way.
- **The size as a guess between two estimates.** The model's shift
  and the history's own estimate are both returned. The model errs
  low more often than high.
- **The error, measured on the caller's own table.** The last periods
  of the history are held back, predicted and compared.
- **"No effect visible" is not "no effect".** It says that this
  member's data does not show one.

No table records what would have happened under an input that was
not changed. So the direction cannot be scored against a truth; the
agreement of the two estimates is the evidence there is.

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
   neighbouring rows for a 90% interval.
3. **The sign.** `+` or `-` where both readings point the same way
   with neither interval over zero, and the model's shift goes that
   way. `0` where the history shows nothing and the model's shift is
   under a tenth of the column's spread. `?` otherwise, with the
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

Two limits of this evidence. The yardstick is the history's straight
line, itself an estimate; "agrees on 94%" is agreement with it, not
with a truth. And the instances are not independent: one series
gives several, so counts of instances overstate the evidence.

## The evaluation that runs today

The evaluation sends real tables through the route. Every series
is cut one horizon before its end; the period after the cut is sent
as recorded and with one input moved by an ordinary amount. Eleven
tables, chosen by the kind of input: a switch someone sets, a
calendar flag, a count, a price, a forecast, a measured temperature.
Seven are development tables (retail, electricity prices, electricity
load, air quality); four were held out (a grocery delivery, a fashion
retailer, Nord Pool prices, a load competition).

Run of 2026-10-02:

| | Development | Held out |
|---|---|---|
| Series and columns answered | 219 | 144 |
| A sign stated | 117 | 50 |
| No effect visible | 64 | 43 |
| Not answerable | 38 | 51 |
| The model agrees where the history shows an effect | 93% of 126 | 96% of 52 |
| The model stays still where it shows none | 90% of 71 | 81% of 53 |

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
- **A planned input beyond the history.** 27 of the 51 held-out
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
- **An input that never moved.** 46 series and columns were not asked
  at all: the input never varied in their history (26 of 30 M5 items
  kept one price), so there was nothing ordinary to change it by.

A table of one series or a few is cut at several dates, one horizon
apart; those answers share most of their history and count for less
than their number.

## What a caller should know about its table

Findings, not tested rules.

- **Grain:** fine enough that the input changes from row to row. One
  table's promotions showed nothing on weeks or months and were
  readable on days.
- **Window:** more rows help. The shortest histories that worked were
  52 weeks, 365 days, 672 hours. A member needs 26 periods more than
  there are ahead; at most 4,096 are read.
- **The input must have varied in each member's own history.**
- **Inputs that determine one another cannot be told apart,** nor can
  an input that follows the calendar (a load forecast by the hour, a
  temperature within one year).
- **A row with a gap in any input is left out of the history's
  reading.** A table whose inputs are mostly empty cannot be read.
- **A member that has barely started** is predicted badly.
- **The change stays inside what the history has seen,** and so does
  the input as planned. An input that grows past its own history, as
  the orders of a growing business do, is not answered.
- **An input that works in both directions** (temperature on load) is
  read by one straight line. Around the turn of a season the route
  states no sign.

Four assumptions under which a predicted effect of a set input can
be read as its effect; they hold for any method, and only the first
can be seen in the table:

| Assumption | In plain words |
|---|---|
| Positivity | The change asked has happened before, on members with a comparable history |
| Exchangeability | Nothing unrecorded decided both when the input was set and how the column went |
| A well-defined lever | The recorded input is something someone sets. A promotion is; a wind forecast is not |
| No interference | Setting the input for one member does not move another |

A trial (an A/B test) makes the first two true by design. The
service cannot run one, but a table that holds a past trial is the
kind of history from which a size can be read.

## What the calling actor may do with the answer

Guidance for a skill, not code in the caller.

- **The bracket:** for a period under the scenario, the range between
  the model's median and the history's interval. It has no stated
  coverage.
- **Through a definition the actor knows** (revenue = price × units):
  recompute at the model's value and at both ends of the history's
  interval. Where every end lies on the same side of the plan, the
  direction of the defined number is certain; otherwise it is
  ambiguous. Signs multiply along a chain of links and add where
  chains meet; a plus and a minus add to "ambiguous". Only forwards
  from the changed input.
- **Break-even:** how large the shift must be for a plan to pay off
  is arithmetic over the definitions. Outside the bracket the
  decision holds whatever the size; inside it, the size decides and
  the what-if cannot.
- **A total** is asked for by sending the summed series as a member.

## Not covered

Several inputs changed at once; a change beyond what the history
holds; one member's input moving another member; columns tied by a
definition being made to agree with it.

**A known defect: gaps in inputs.** A row with a gap in any input is
left out of the history's reading for every input. One input column
that is mostly empty therefore makes the whole member unreadable,
though the input asked about is complete. To be fixed: an input that
is mostly empty is left out of the reading, and the answer says so.

## What could come next

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
