# The misfit

What `/misfit` is for, how it works, what was tested and found, and
what is not known. The wire form is in `docs/contract.md`. A tag in
brackets (GK-38) is the name the code and the contract use for a
finding.

- **The use case** is checking business tables: finding the rows
  where a value does not fit the row's other columns. A tax code
  that does not match the rate, a document on the wrong plant, a
  payment linked to the wrong invoice.
- **One model:** TabICL.
- **Out of scope:** general anomaly detection (fraud, intrusion,
  monitoring one column). That is another field.

## What the route promises

- **A ranking.** Rows with an error that breaks a relation between
  columns are ranked ahead of clean rows.
- **The column to look at.** The row's strangest cell names it; it
  is one the error touches in 0.97 of the injected errors.
- **No threshold.** The caller ranks and decides how far down to
  read.
- **The caller's join decides what is checked.** The service sees
  one table.

## How it works

1. **Each cell is read against the columns before it, twice.** TabICL
   gives the density of a number, or the probability of a category,
   given the columns before it in the order sent, and again in that
   order reversed; the two log densities are averaged. The order is
   thus part of the read, and it is the caller's: the order a table is
   naturally written in, the columns a value is computed from before
   it, reads best (GK-67; order-free reads were judged and lose on
   some tables what they gain on others, GK-68). A column of text, of
   true and false, or one named in `categorical` is a category
   (GK-37).
2. **Against its own column** (GK-21). A cell's score is its log
   density minus the median of its column, in units of the column's
   spread, or in nats where the spread is under one nat. A density's
   level depends on the column's scale; its distance below the
   column's typical level does not.
3. **The row's score is its strangest cell** (GK-38), not the sum
   over its cells. Lower is stranger.
4. **With `folds`,** every row is scored by a model that has not
   seen it (GK-11). A category seen in the scored fold only is unseen
   by that model; its probability is lifted from the package's 1e-10
   to one in n + 1 of the rows the conditional was fit on (since
   2026-10-19, GK-69).

## What was tested

Errors are injected into tables, and the service scores every row
through the HTTP API. AUROC is the probability that
a row with an error is ranked ahead of a clean one; average
precision is about the share of real errors among the first rows
shown. Isolation Forest on the same rows in brackets.

| Table | Injected error | AUROC (Isolation Forest) | Average precision | Column found |
|---|---|---|---|---|
| Synthetic order lines, 200 to 2,000 rows, numbers | Extreme value | 1.00 (1.00) | 0.92 to 0.98 | 1.00 |
| | Wrong unit (x100) | 1.00 (0.91) | 0.81 to 0.96 | 1.00 |
| | Value from another row | 0.99 (0.62 to 0.69) | 0.76 to 0.92 | 1.00 |
| | Wrong sign | 0.95 to 0.96 (0.71 to 0.74) | 0.39 to 0.42 | 0.91 to 0.99 |
| Synthetic order lines, 16 columns of which 10 tie to nothing | The four above | 0.87 to 0.98 (0.55 to 0.99) | 0.19 to 0.50 | 0.83 to 0.97 |
| Synthetic order lines, numbers and categories | Tax code not matching the rate | 0.92 (0.71) | 0.23 | 0.91 |
| | Kind not matching the sign | 0.89 (0.69) | 0.22 | 0.93 |
| Generated company, 2,000 rows | Payment linked to the wrong invoice | 0.92 to 0.94 (0.75 to 0.88) | 0.63 to 0.68 | 0.93 to 0.95 |
| | One customer segment scaled by 1.15 | 0.79 to 0.86 (0.49 to 0.51) | 0.26 to 0.32 | 0.93 to 0.97 |
| Bank transactions (real), 2,000 rows | Transaction type flipped | 0.89 (0.91) | 0.18 | 0.94 |
| SAP sales order items (real), all categories | Shipping point of another plant | 0.98 (0.58) | 0.71 | 0.98 |
| Stock movements, 12,169 rows | Rows on a warehouse that does not exist | 1.00 (1.00) | 1.00 | 1.00 |

Other random seeds of the synthetic tables were held out: AUROC 0.86
to 1.00 against Isolation Forest's 0.54 to 1.00.

| Question | Finding |
|---|---|
| Is it better than a standard detector? | On errors that break a relation: well ahead of Isolation Forest, ahead of or level with k-nearest-neighbour distance. Extreme values every detector finds |
| How to read a cell? (GK-21) | As returned by the model, the column named was one the error touches in 0.83 of the cases: wide columns have low densities in every row. Read against its own column: 0.97 |
| How to score a row? (GK-38) | The strangest cell: AUROC 0.948 as a mean over 21 groups of tables, better on 15 and worse on 2. The sum over the cells: 0.910; it is dominated by wide numeric columns |
| Categories? (GK-37) | Served by the model's classifier. A table of categories only (SAP order items): 0.98 against 0.58 |
| Does scoring a row from a model that has not seen it help? (GK-11) | By 0.00 to 0.02, at 2 to 5 times the run time. Off by default |
| How large a table? (GK-12) | 12,169 rows by 4 columns in 13 to 18 seconds on a laptop |
| And general anomalies? (GK-23) | On ADBench, 38 tables: AUROC 0.76, third of four detectors (k-nearest-neighbour 0.79, Isolation Forest 0.78), within the standard error of 0.03 and at far higher cost |

## Where it is weak

- **Subtle errors at the top of the list** (GK-7). For a wrong unit,
  another row's amount or a document on the wrong plant, most of the
  first rows shown are real errors. For a wrong sign, a wrong code or
  a scaled segment about a fifth to a third are, when 5% of the rows
  hold one.
- **An error that affects a whole segment** (GK-8) is ranked less
  well: every row of the segment is wrong in the same way, which
  looks like a second group of normal rows. The segment's total
  shifts; the forecast's interval is the tool for that.
- **A flipped category on the bank table:** the two standard
  detectors are slightly ahead (0.91 to 0.93 against 0.89).
- **Cost:** 2 to 10 seconds per table of 2,000 rows on a laptop,
  against well under a second for the standard detectors.
- **The floor of one nat** on a column's spread was chosen with the
  bank table in view. The held-out seeds of the synthetic tables
  confirm it; the real tables were development data.
- **The clean rows of the real tables** may hold errors of their
  own; only the injected rows are labelled.

## What a caller should know

- **A suspicion about a relation needs the join in the frame.** A
  single table cannot show a payment on the wrong invoice.
- **Name codes as categories:** account numbers, tax codes.
- **Send the columns in the order a value is computed:** quantity and
  price before the amount, the plant before its shipping point. Each
  column is read against the ones before it, so the order is part of
  the read (GK-67).
- **A frame needs two rows and two columns.**
- **Read the column named as "look here",** not as "this cell is
  wrong": where an error breaks a relation between two columns,
  either end may be named.

## What was tried and dropped

| Tried | Measurement |
|---|---|
| A cell's log density as the model returns it | Points to the widest column: 0.25 to 0.32 right on payments linked to the wrong invoice |
| A cell's rank within its column; a robust z-score | The column named touches the error in 0.91 and 0.88, against 0.97 |
| The row's score as the sum of its cells' log densities | Mean AUROC 0.910 against 0.948; the bank table at 0.77 against 0.89 |
| A floor of half a nat, of two nats | 0.921 and 0.949 against 0.948 at one nat |

## Not covered

- **Timing on a server GPU** (GK-20), and from it the size of table
  a caller should send. glossql's limit of 2,000 rows and 16 columns
  rests on an older timing (GK-12).
- **Real tables with real, labelled errors.** Every error tested was
  injected. Raha's Hospital, a real table with typos planted in text
  fields, was read (GK-70): the route finds them no better than
  chance. A typo is a category seen once, not a relation broken
  between columns; a string check is the tool for it.

## What was read

- ADBench (Han et al., NeurIPS 2022): AUROC and average precision;
  injected anomalies of several types; the benchmark the service was
  run on, on tables cut to 1,000 rows and without the 9 widest.
