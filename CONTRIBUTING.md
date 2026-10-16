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

## The contributor licence agreement

Every contributor signs the agreement in `CLA.md` once. A bot asks on
your first pull request; you sign by commenting the sentence it
names, and the signature is recorded in the repository on the branch
`cla-signatures`. If you contribute for an employer or another
entity, someone authorised to sign for it does so. Pull requests from
contributors who have not signed are not merged.

## Licence

The project is under the GNU Affero General Public License, version 3
or any later version (`LICENSE`). Your contribution is received under
it and under the terms of `CLA.md`.
