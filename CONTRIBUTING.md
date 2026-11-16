# Contributing

The service is kept small: one table and a specification in, one
table and an answer out, on every endpoint (`docs/contract.md`), and
one model per task, chosen by measurement. A change that adds a
model, an endpoint or an option needs a measurement first. The
evaluation that produces it is in a separate repository that is not
public, so open an issue that says what you would like measured.

## Before a pull request

```bash
uv sync
uv run ruff format --check src tests
uv run ruff check src tests
uv run pytest
```

Keep to the conventions you find:

- Plain words in the docs, the comments and the commit messages: the
  words a data engineer already uses (endpoint, backtest, expected
  range, prediction interval, test).
- A reason beside every constant.
- Nothing in the service names a dataset or a customer.
- A test asserts status codes, field names and values, not the
  wording of an error or of a `basis`.

## The sign-off

Every commit in a pull request carries a line

    Signed-off-by: Your Name <you@example.org>

which `git commit -s` adds. It certifies the Developer Certificate of
Origin in `DCO`: that you wrote the change or have the right to submit
it under the project's licence, under your real name. A workflow
checks the line on every pull request; a commit without it is not
merged. The check skips commits by the maintainers named in that
workflow. There is no contributor licence agreement to sign: your
contribution is received under the project's licence, and you keep
your copyright.

## Licence

The project is under the GNU Affero General Public License, version 3
or any later version (`LICENSE`).
