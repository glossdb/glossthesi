# Contributing

The service is small on purpose: one table and a specification in,
one table and an answer out, on every route (`docs/contract.md`), and
one model per task, chosen by measurement. A change that adds a
model, a route or an option is argued with a measurement first; the
evaluation that produces it lives in a repository of its own, which
is not public, so an issue that states what you would like measured
is the usual way in.

## Before a pull request

```bash
uv sync
uv run pytest
```

Keep to the conventions you find: plain words in the docs and the
commit messages, a reason beside every constant, and nothing in the
service that names a dataset, a customer or a provider.

## The sign-off

Every commit in a pull request carries a line

    Signed-off-by: Your Name <you@example.org>

which `git commit -s` adds. It certifies the Developer Certificate of
Origin in `DCO`: that you wrote the change or have the right to submit
it under the project's licence, under your real name. A workflow
checks the line on every pull request; a commit without it is not
merged. The maintainers named in that workflow are not asked. There is no contributor licence agreement to sign; your
contribution is received under the project's licence, and you keep
your copyright.

## Licence

The project is under the GNU Affero General Public License, version 3
or any later version (`LICENSE`).
