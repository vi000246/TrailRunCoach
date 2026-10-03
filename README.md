# TrailRunCoach

A self-hosted training-analysis and planning app for trail and road runners.
It reads your activities (COROS, TrainingPeaks, or a local WKO5 library), computes
WKO5-style metrics and charts, and builds a season plan, weekly schedule and
race-day pacing from them. The UI is Traditional Chinese first, with an English catalog.

> Not affiliated with TrainingPeaks, WKO5 or COROS. Bring your own accounts and data.

## Features

- **Overview dashboard**: fitness / fatigue / form (PMC), load ratio, recent bests, status cards.
- **Chart viewer**: bundled views (training load, aerobic fitness, power-duration, zones,
  drift, climbing) plus your own exported WKO5 views (`*.wko5chart`) if you point the app at them.
- **Activity pages**: per-workout charts, map, laps, zone time, heart-rate drift.
- **Season plan and schedule**: periodization, weekly sessions, workout editor, COROS workout push.
- **Race power**: course (GPX) + weather + fitness model → target pace / power and finish time.
- **Achievements**: 百岳 / 小百岳 summit detection from GPS tracks.
- **Sync**: COROS and TrainingPeaks, incremental, with tokens encrypted at rest.

Every rule and threshold carries its source (paper, book, coach) or is labelled 推估 (estimate).

## Run locally

Requirements: Python 3.12, Node 20+ (only for the optional React frontend).

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt          # Windows: .venv\Scripts\pip
cp .env.example .env                               # optional, every variable has a default
.venv/bin/python -m uvicorn backend.main:app --port 8000
```

Open <http://localhost:8000>. Data and settings live in `~/.wko5coach/` (SQLite DB,
caches, secret key); nothing is written inside the repo.

Optional React frontend (dev server on port 5173):

```bash
cd frontend && npm install && npm run dev
```

Or with Docker: `docker compose up --build` (mounts `~/.wko5coach` and `~/WKO5`).

### Data sources

- **COROS / TrainingPeaks**: log in on the settings page (資料同步). TrainingPeaks OAuth
  needs your own client (`TP_CLIENT_ID` / `TP_CLIENT_SECRET`); without it the website login is used.
- **WKO5 library** (optional): `WKO5_ATHLETE_DIR=/path/to/<athlete folder>`.
- **WKO5 views** (optional): `WKO5_VIEWS_DIR=/path/to/folder/with/*.wko5chart`, or the setting
  on the chart page. WKO5 chart packs are not shipped with this repo.

See [`.env.example`](.env.example) for every variable and
[`docs/secrets-and-keys.md`](docs/secrets-and-keys.md) for how secrets are stored.

## Demo

There is no hosted demo yet. To look around without an account, start the app with no data
configured: every page renders its empty state, and the chart help (?) explains each chart.
`WKO5COACH_MODE=demo` hides personal features (injury log, pain marks) for a public instance.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest backend/tests -q
```

The default run uses synthetic data only. Tests against your own data live in
`backend/tests/realdata/` and run only with `WKO5COACH_REALDATA=1` (see its README).

## License

[MIT](LICENSE). Third-party data: GoldenCheetah OpenData and the Lovdal dataset used by the
validation scripts are CC0; they are fetched or placed outside the repo, not bundled.
