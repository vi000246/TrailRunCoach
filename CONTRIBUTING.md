# Contributing

The program is developed in a private repository (since 2026-10, SP-500). This public repository
is an automatic mirror of it **without the calculation core** (the chart expression engine and
dataset, the metric algorithms, the WKO5 file readers, the built-in chart definitions): every
sync replaces its contents with a new snapshot, and the core is not developed here.

External contributions are not accepted: no pull requests, patches or code in issues (a change
made here would be overwritten by the next sync anyway).

- Bug reports from users of the hosted service go through the service's own contact channel.
- Contributions received earlier under the [Contributor License Agreement](CLA.md) keep its
  terms; the agreement stays here for that record.

## Working on the code (the author and the people the author works with)

- Setup, running and tests: see [README.md](README.md). In the public mirror the tests that need
  the core are skipped (「核心未包含（公開版）」).
- Keep changes focused; one topic per change.
- Add or update tests for behaviour changes and run the test suite before merging.
- If any part of a change comes from somewhere else (another project, a paper's published
  code, a spreadsheet), say where and under which license.
