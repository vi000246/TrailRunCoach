# Contributing

The program is developed in a private repository (since 2026-10, SP-500). This public repository
is an automatic mirror of it **without the calculation core** (the chart expression engine and
dataset, the metric algorithms, the WKO5 file readers, the built-in chart definitions): every
sync replaces its contents with a new snapshot of the private repository.

## Pull requests: welcome outside the core

Pull requests that fix or improve the parts published here are welcome: the pages and their
scripts, the API routes, COROS / Garmin sync, the schedule, season planning, the injury log,
routes, backups, translations, docs, and tests that run without the core.

Not accepted:

- Changes to the core. Its files are not here, or are stubs whose first line is
  `# PUBLIC-MIRROR-STUB`; a pull request that touches one of them (or adds a file in a core
  folder such as `backend/engine/wko5expr/` or `views/`) is closed. If you think the core
  computes something wrong, open an issue with what you saw and what you expected (no code).
- Code copied from somewhere else without saying where it comes from and under which license.

How a pull request is taken:

1. Open it against `main`. In the description write **"I agree to the Contributor License
   Agreement in CLA.md"** ([CLA.md](CLA.md)); a pull request without it is not taken.
2. The maintainer reviews it here. It is **not merged on GitHub**: the change is applied to the
   private repository with your commit and authorship kept, and the next sync brings it back
   here as part of a snapshot commit. The pull request is then closed with a link to that
   snapshot.
3. Base it on a recent `main` and keep it small (one topic); a sync can land between your branch
   and the review, and a small change applies cleanly across it.

Bug reports from users of the hosted service go through the service's own contact channel.

## Working on the code

- Setup, running and tests: see [README.md](README.md). In this mirror the tests that need the
  core are skipped (「核心未包含（公開版）」), and whatever needs the core answers 503
  `CORE_NOT_INCLUDED`.
- Keep changes focused; one topic per change.
- Add or update tests for behaviour changes and run the test suite before opening the pull
  request.
- If any part of a change comes from somewhere else (another project, a paper's published
  code, a spreadsheet), say where and under which license.
