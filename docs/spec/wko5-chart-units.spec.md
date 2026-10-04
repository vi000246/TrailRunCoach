# Module Spec: wko5-chart-units

> **Last Updated**: 2026-10-04
> **Status**: Active
> **Domain Layer**: Supporting Domain

## Overview

Every chart shows its values in the right unit with sensible decimals, in metric, and the
design mistakes found in the imported WKO5 charts are corrected — without editing
the `.wko5chart` files. It is a display layer on top of `wko5-engine`: a unit registry, a
render-time pass that attaches unit metadata and converts imperial / pace values, and a JSON
file of chart fixes. Parity mode (side-by-side checking against WKO5) keeps WKO5's own ids,
values and designs; only decimals are tidied.

## Architecture

```
 WKO5_VIEWS_DIR / charts.wko5_views_dir
 *.wko5chart ── read_view + ensure_ids ──┬─ parity: raw views
                                         └─ not parity: apply_fixes(views/wko5_fixes.json)
                                       │   then translate_views (views/i18n/<locale>.json)
                                       │
 render_chart ── prepare (english→metric, imperial y id) ── evaluate ── finish (scale, pace, unit meta)
                                       │
                            axes(): metric twins, unit per axis ── JSON ── viewer
```

| Piece | Responsibility | Entry point |
|---|---|---|
| Unit registry | label, kind, decimals, display scale, metric twin | `backend/engine/wko5expr/units.py:47` |
| Render units | per-series prepare / finish, axis metadata | `backend/engine/wko5expr/render_units.py:64` |
| Chart fixes | load, validate and apply overrides | `backend/engine/wko5expr/chartfixes.py:117` |
| Integration | `render_chart` calls the pass; views API gates fixes by parity | `backend/api/wko5views.py:261` |
| Audit | render every chart and flag unit problems | `backend/scripts/audit_chart_units.py:420` |

## Unit registry

- `Unit` (`backend/engine/wko5expr/units.py:47`): id, label, kind (number / duration / pace /
  percent / date), decimals rule, display scale, metric twin with factor and offset.
- **Decimals**: an ordered list of (threshold, decimals) on the *displayed* value; `AUTO` =
  ≥ 100 → 0, ≥ 10 → 1, else 2 (`backend/engine/wko5expr/units.py:37`).
- **Stored vs displayed**: PERCENT is a fraction (× 100). MILLISECONDS and CM have no
  scale: as in WKO5, expressions see `stancetime` in ms and `verticaloscillation` in cm
  (the evaluator scales the .wko4 s / m values on read, `EXPR_UNIT_SCALE` in
  `backend/engine/wko5expr/evaluator.py`), so the numbers already are ms / cm.
- **Imperial → metric**: FT → m, MI → km, MPH → km/h, PACEMI → min/km, °F → °C
  (`backend/engine/wko5expr/units.py:125`).
- **Pace base** (`backend/engine/wko5expr/units.py:212`): a PACEKM series may hold s/km
  (median ≥ 60), km/h (speed-like expressions such as `ngp`) or min/km; it is converted to
  min/km outside parity.
- **CUSTOM<label> / NONE**: the label after the prefix (with known labels refined), NONE has
  no label; both use AUTO decimals. RPM is labelled spm for runs, rpm for rides.

## Render-time flow

- `render_chart` (`backend/engine/wko5expr/render.py:327`) takes parity from `ds.config.parity`
  and runs `prepare` → evaluate → `finish` for each series.
- `prepare` (`backend/engine/wko5expr/render_units.py:64`): outside parity, `english(` becomes
  `metric(` (the evaluator stores everything metric) and an imperial y id becomes its metric
  twin; both add a fix note.
- `finish` (`backend/engine/wko5expr/render_units.py:81`): applies a fix's scale, resolves the
  pace base (converting outside parity), sets `entry.unit` {id, label, kind, dec[, scale, base]}
  and `entry.x_unit`.
- `axes` (`backend/engine/wko5expr/render_units.py:102`): imperial axes become their metric twin
  (min / max converted), each used axis carries its unit.
- The chart JSON carries `axes`, `parity`, `fixes` (notes) and `series`.

## Display effects of the period toggle and render cache

The toggle and the cache themselves are `wko5-engine` features (see
[wko5-engine.spec.md](./wko5-engine.spec.md) §Viewer); what they change on screen:

- **Titles and legend names follow the period.** A re-bucketed chart's leading 每日／每週／
  每月／每季／每年 is rewritten (`backend/engine/wko5expr/periods.py:66`), and so is each series
  name — 每週 → 每月, or a leading character naming the default period, 週爬升 → 月爬升, so
  words like 年齡 are left alone (`backend/engine/wko5expr/periods.py:72`).
- **Category x axis.** A period chart draws one category per bucket from the response's
  `buckets` (empty buckets included); a bucket total sits on the bucket's first day and
  per-workout values become dots in their bucket (`backend/static/wko5_viewer.html:2273`,
  `backend/static/wko5_viewer.html:2365`). The tooltip heads with the bucket label
  (`backend/static/wko5_viewer.html:2465`); on stacked charts it ends with a 合計 row in the
  series' unit, left out for percent shares (`backend/static/wko5_viewer.html:2488`).
- **Tooltip units follow the drawn series.** Each ECharts series records the source series it
  came from (`srcOf`, `backend/static/wko5_viewer.html:2331`), and the tooltip rows and the 合計
  row take their unit from `srcOf[p.seriesIndex]`, so a vline skipped on a category axis no
  longer shifts units onto the wrong series.
- **Look-back note.** When the floor widens the range the card shows `range_note`, e.g.
  「顯示近 12 個月」 (`backend/api/wko5views.py:466`, `backend/static/wko5_viewer.html:1172`).
- **Enlarged chart.** The overlay shows the full legend and a zoom slider under the x axis,
  now also on the log (duration) axis with duration tick labels
  (`backend/static/wko5_viewer.html:2526`); the "已修正單位" notes are printed in full above the
  chart (`backend/static/wko5_viewer.html:1221`).
- **Source stamp in the cache key.** A COROS / TP FIT-folder dataset's file stamp is part of
  the data fingerprint, so a chart never shows another source's cached numbers
  (`backend/engine/wko5expr/render_cache.py:104`).
- **Locale in the cache key.** Outside zh-TW the request locale is part of the cache key, so
  translated titles / legend names are never served to the other language
  (`backend/engine/wko5expr/render_cache.py:141`).
- Timing footnotes for the cache (cold loads, `--reload` invalidating the code signature) are
  in `docs/reports/chart-sweep.md:101`.

## Chart fixes

- File `views/wko5_fixes.json` = `{description, fixes: [...]}`; each fix names the chart by
  `chart_id` (the title-derived id `ensure_ids` gives every imported WKO5 chart,
  `backend/engine/wko5expr/viewids.py:41`; a `chart` title still works, and next to an id it is
  only a reminder), optionally `dashboard_id` / `dashboard`, and at most one target: `series`,
  `series_index` or `axis`. Actions: `set` (series / axis keys), `scale`, `drop`, plus `note`
  (`backend/engine/wko5expr/chartfixes.py:55`).
- `view` is optional and the repo file uses none: a fix without it applies to every imported
  view, so fixes follow the charts whatever the exported `.wko5chart` file is called
  (`backend/engine/wko5expr/chartfixes.py:127`).
- Applied to a deep copy; scales multiply; unmatched fixes are ignored and reported by
  `unmatched()`. Each applied fix adds its note to the chart's `fixes` list (the viewer's
  "已修正單位" badge).
- Applied only outside parity; cached by the file's mtime, so an edited file is picked up on
  the next request; a broken file falls back to raw views (`backend/api/wko5views.py:250`).
- The `.wko5chart` files are not in the repo: they are read only from `WKO5_VIEWS_DIR` or the
  `charts.wko5_views_dir` setting (`backend/api/wko5views.py:61`); with neither set there are no
  WKO5 views and the fixes have nothing to hit.
- View translations (`views/i18n/<locale>.json`) are applied after the fixes, by chart id
  (`backend/engine/wko5expr/viewi18n.py:113`).
- The custom-view loader skips the file (`backend/engine/wko5expr/customviews.py:57`).
- Current content: 51 fixes — axis ranges that clipped data, VAM on a unitless axis, single-leg
  cadence ×2, series on the wrong axis, the Palladino summary's ft / mile / percent rows.
  The findings behind each are in `docs/reports/chart-units-audit.md`.

## Audit

`backend/scripts/audit_chart_units.py` renders every view's charts (season charts over the
last 365 days; workout charts on a road run, a trail run and a hike), checks imperial ids,
percent range, duration scale, plausible magnitude per unit, axis mismatch, clipping by fixed
ranges (> 5 % of points), cadence, pace base and errors, re-audits charts after fixes and writes
`docs/reports/chart-units-audit.md`. The WKO5 views it audits come from the same views folder
as the API (`WKO5_VIEWS_DIR` / `charts.wko5_views_dir`); a fix without `view` counts as touching
that chart in every view. Last run (2026-09-30, before fixes were keyed by chart id): 160 charts /
886 series; clip, axis-mismatch and cadence findings 0 after fixes.

## Testing

`backend/tests/test_wko5expr_units.py`: registry labels / kinds / decimals, CUSTOM and NONE,
RPM by sport, imperial conversions, pace base, formatting; `render_chart` metric conversion
outside parity and untouched values in parity; fixes apply / validate / skip, a fix without `view` applies to every
imported view; the repo's fixes file is valid, every entry has a `chart_id` and none a `view`, and —
only when `WKO5_VIEWS_DIR` points at exported views (skipped otherwise) — none is unmatched.

## Domain Model

### Bounded Context
- **Context Name**: ChartPresentation（圖表呈現）
- **Domain Layer**: Supporting Domain
- **Parent Module**: N/A (display layer of `wko5-engine`)

### Ubiquitous Language
| Term | Definition |
|------|-----------|
| unit id | WKO5's unit name on an axis or a series' y_axis / x_axis |
| parity mode | reproduce WKO5 exactly — no fixes, no metric conversion |
| metric twin | the metric id an imperial id maps to |
| pace base | what a pace series stores: s/km, km/h or min/km |
| display scale | multiplier from the stored value to the labelled unit |
| fix | an override from `views/wko5_fixes.json` applied to a parsed chart |
| chart id | stable, title-derived id of an imported WKO5 chart; what a fix matches on |
| fix note / badge | the "已修正單位" label and its tooltip text |
| clip | a fixed axis range that hides data points |
| single-leg cadence | per-leg cadence; ×2 = steps per minute |
| bucket | one category of a period chart (day / week / month / quarter / year start) |

## Change History

| Date | Source | Feature SRS | Summary |
|------|--------|-------------|---------|
| 2026-09-30 | code-sync | N/A | Created from brownfield analysis — unit registry, metric display outside parity, 50 WKO5 chart-design fixes, chart unit audit |
| 2026-09-30 | code-sync | N/A | MILLISECONDS / CM display scale 1000 / 100 → none: stancetime evaluates in ms and verticaloscillation in cm (WKO5's convention); audit plausible ranges now ms / cm; Palladino "Pwr-GCT" gets a W/ms axis fix |
| 2026-09-30 | code-sync | N/A | Display effects of the period toggle (titles and legend names follow, bucket axis, look-back note), enlarged-chart slider on log axes, FIT-source stamp in the render-cache key; refreshed drifted anchors |
| 2026-09-30 | bugfix | N/A | Tooltip / 合計 units from the ECharts series' source series (`srcOf`), not `series[seriesIndex]`; refreshed anchors |
| 2026-10-04 | code-sync | N/A | Fixes keyed by chart_id / dashboard_id with `view` optional (51 fixes, none per view); .wko5chart only from WKO5_VIEWS_DIR / charts.wko5_views_dir; translations after fixes; locale in render-cache key; refreshed drifted anchors |
