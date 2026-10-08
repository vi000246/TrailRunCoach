"""
Data registry: the class of every per-tenant file and DB table (SP-311,
docs/research/local-first-sync.md §3.2, §11 ④).

Backup (engine/backup.py) reads this list to decide what a backup holds; the device sync,
account deletion and data retention (SP-317) are to read it too, instead of each keeping
its own list. Classes:

  USER      使用者改的: what the user typed or chose. Not recomputable; small; the
            candidates for a device sync (§5.1).
  IMPORTED  匯入的: raw input (synced FIT files, uploaded GPX). Not recomputable; the
            source of everything DERIVED.
  DERIVED   衍生的: recomputable from IMPORTED + USER (+ the code), or refetchable. Safe to
            delete; never synced (§5.4); not backed up.
  SECRET    機密: credentials (sync_state's tokens and sealed passwords, the server key,
            API keys). Server only: never exported, never synced to a device (§5.5).

`deidentify` (SP-319, owner 2026-10-07; investigation SP-326): what has to be de-identified
before the data could feed a cross-user analysis — GPS points (start / end), timestamps,
account links, free text. It replaces the research doc's 「敏感度」 (no end-to-end encryption).

Files: `pattern` is relative to a tenant folder (POSIX; `*` stays inside one segment, `**`
spans segments, a leading `**/` may match nothing); the first matching entry wins, so the
specific ones come first. `where` = the tenant folder that holds it (tenancy.py): ROOT
(private), BASE (a demo sandbox reads its base's), SHARED (FIT, caches), ANYWHERE (a
temporary file next to its target). `scope`: TENANT (any tenant), INSTANCE (only
$WKO5COACH_HOME — the server's own files; the owner's tenant folder is that same folder),
DEMO (only a demo base / sandbox).

A backup (SP-355) holds the DB, every tenant file the user made (USER, and the GPX the user
uploaded) and, opted in, the FIT originals; see `backup` on each entry. A new USER / IMPORTED
tenant file kind must say backup=ALWAYS (or OPT_IN) — a test fails otherwise — and
engine/backup.py then carries it without a change there. `restore=False`: kept in a backup for
manual recovery, never written back by a restore (share links: a revoked one stays revoked).

What stays on this machine (never in a backup, never overwritten by a restore): a `local` table's
rows (debug tokens, the debug API's call / failure logs with IPs), a table's `local_fields`
(sync_state's credentials and account identity, matched by primary key) and the user_settings keys
in LOCAL_SETTINGS. A table's `cursor_fields` (sync cursors) travel with the data, so an older
backup makes the next sync fetch the gap; a restore never leaves one newer than the backup.

Tests: every table of the schema and every file a built demo tenant holds has an entry
(backend/tests/test_data_registry.py, test_demo_smoke.py); a new table or file kind without
one fails them.
"""
from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

USER, IMPORTED, DERIVED, SECRET = "user", "imported", "derived", "secret"
CLASSES = (USER, IMPORTED, DERIVED, SECRET)
PER_TABLE = "per-table"          # the DB file: each table has its own class (TABLES)

ROOT, BASE, SHARED, ANYWHERE = "root", "base", "shared", "any"
TENANT, INSTANCE, DEMO = "tenant", "instance", "demo"
ALWAYS, OPT_IN, NEVER = "always", "opt-in", "never"   # in a backup (OPT_IN: backup.include_fit)


@dataclass(frozen=True)
class Table:
    name: str
    cls: str
    note: str
    user_fields: tuple = ()       # columns the user edits inside a table of another class
    secret_fields: tuple = ()     # credentials: never in an API response, an export or a sync
    deidentify: tuple = ()        # columns (SP-319)
    invalidated_by: str = ""      # DERIVED: what makes a row stale
    local: bool = False           # rows stay on this machine: emptied in a backup, kept on a restore
    local_fields: tuple = ()      # columns that stay on this machine: NULL in a backup, this machine's on a restore
    cursor_fields: tuple = ()     # sync cursors: travel with a backup, never newer than it after a restore


@dataclass(frozen=True)
class File:
    pattern: str
    cls: str
    where: str
    note: str
    scope: str = TENANT
    backup: str = NEVER
    deidentify: tuple = ()        # fields / content (SP-319)
    invalidated_by: str = ""      # DERIVED: what makes an entry stale
    restore: bool = True          # backup=ALWAYS but False: kept in the backup, never restored


# ---------------------------------------------------------------------------
# DB tables (backend/db/models.py; event_gpx / race_calc also have plain-sqlite DDL)
# ---------------------------------------------------------------------------

TABLES: tuple = (
    Table("athletes", USER, "the athlete row the other rows hang on (created by the first sync / login)",
          deidentify=("name", "tp_athlete_id", "data_dir")),
    Table("athlete_settings", USER, "dated thresholds / weight (run FTP, threshold pace, weight, the COROS "
          "account's LTHR — the cold-start LTHR prior, initial CTL); an older DB's ftp_w column is unread",
          deidentify=("effective_date",)),
    Table("user_settings", USER, "key / value settings (settings/repository.py); values are free-form JSON "
          "(backup.dir is a local path, plan.calendar holds the ICS feed's link token); a few keys are the "
          "server's bookkeeping, not choices: run results (sync.*.last_result / last_ok / check*) and the "
          "one-time jobs' done marks (sync.coros.rpe_backfill; weather.rain_backfill, api/rain_backfill.py — "
          "deleting it only reruns the backfill, which asks for nothing the weather cache already holds)",
          deidentify=("value_json",)),
    Table("activity_tags", USER, "the user's activity marks: type, effort, note, title, tags, pain, exclusion",
          deidentify=("start_local", "file", "workout_id", "label", "name", "note", "tags_json")),
    Table("injury_events", USER, "傷病紀錄 (engine/injuries.py); never sent to COROS / TP or the AI coach",
          deidentify=("onset_date", "onset_key", "onset_file", "resolved_date", "walkrun_from", "note")),
    Table("plan_sessions", USER, "the stored plan: origin=custom and edited rows are the user's, the "
          "auto rows are rewritten by plan_auto (engine/plan_store.py)",
          deidentify=("day", "title", "note", "done_by")),
    Table("plan_change_log", USER, "each automatic plan run / user decision with before / after (復原); "
          "a history, not recomputable", deidentify=("created_at", "summary", "items_json", "before_json",
                                                    "after_json")),
    Table("plan_week_snapshots", USER, "每週課表存檔 (SP-71): the plan frozen at each week's start; "
          "a history, not recomputable", deidentify=("week_start",)),
    Table("workout_templates_user", USER, "the user's 課表範本 (SP-36); the route GPX is template_gpx/",
          deidentify=("name", "note", "gpx_filename")),
    Table("workout_template_cats", USER, "categories the user added to the 範本 page", deidentify=("label",)),
    Table("race_calc", USER, "the race calculator's saved inputs + last result per event",
          deidentify=("saved_at",)),
    Table("event_gpx", USER, "metadata of an event's uploaded course (the file is event_gpx/<id>.gz)",
          deidentify=("filename", "uploaded_at")),
    Table("workout_files", IMPORTED, "one row per synced / imported activity file; rpe / feel / coros_feel "
          "are the athlete's own rating but entered on the watch / in COROS and imported, not edited here",
          user_fields=("trail_classification", "classification_overridden"),
          deidentify=("file_path", "workout_date", "start_time_utc", "coros_activity_id", "tp_workout_id",
                      "imported_at")),
    Table("coros_plan_push", IMPORTED, "COROS's ids of the plan sessions pushed to it (sync/workout_targets): "
          "the external account's state, server-side bookkeeping",
          deidentify=("day", "title", "program_id", "plan_id", "id_in_plan", "plan_program_id")),
    Table("sync_failures", IMPORTED, "activities a sync listed but could not fetch (sync/failures.py, SP-362): "
          "retried by id; the cursor has moved past them, so a deleted row is an activity no sync asks for "
          "again (not recomputable); server-side bookkeeping like coros_plan_push",
          deidentify=("provider_id", "workout_date", "last_error", "first_at", "last_at")),
    Table("sync_state", SECRET, "COROS / TP tokens, the sealed 「記住密碼」 passwords (settings/secrets.py), "
          "the accounts' ids and the sync cursors",
          secret_fields=("tp_access_token", "tp_refresh_token", "tp_web_cookie", "coros_access_token",
                         "coros_password_sealed", "tp_password_sealed"),
          deidentify=("coros_email", "coros_user_id", "tp_username", "coros_base_url"),
          # the login (credentials, their expiry, the account identity) stays on this machine; the
          # cursors travel with the activities they describe (SP-355)
          local_fields=("tp_access_token", "tp_refresh_token", "tp_token_expires", "tp_web_cookie",
                        "coros_access_token", "coros_token_expires", "coros_password_sealed",
                        "tp_password_sealed", "coros_email", "coros_user_id", "coros_base_url",
                        "tp_username"),
          cursor_fields=("last_sync_at", "last_sync_cursor", "coros_last_sync_at")),
    Table("debug_tokens", SECRET, "debug API tokens (SP-371, debug_auth.py): the SHA-256 of each token "
          "(the token itself is never stored), its scopes, expiry and the source IPs seen; tenant-bound",
          secret_fields=("token_hash",), deidentify=("name", "last_ip", "ips_json", "created_at", "last_used_at"),
          local=True),
    Table("debug_audit", DERIVED, "the debug API's call log (SP-371): time, token name, endpoint, parameters, "
          "source IP, status, size; the newest debug_auth.AUDIT_KEEP rows are kept",
          deidentify=("at", "query", "ip"), local=True,
          invalidated_by="never recomputed: a log, pruned to the newest rows; safe to delete"),
    Table("debug_auth_failures", DERIVED, "failed debug API authentications (SP-371), counted per hour, "
          "source IP and code; the newest debug_auth.FAIL_ROWS_KEEP rows are kept",
          deidentify=("hour", "ip", "last_at"), local=True,
          invalidated_by="never recomputed: a log, pruned to the newest rows; safe to delete"),
)

# tables no code uses any more: db/database._migrate_schema drops them from every tenant DB
# (DROP TABLE IF EXISTS); unclassified_tables leaves them out until then
RETIRED_TABLES: tuple = (
    "pmc_cache",        # SP-341: never read or written (cache-tiering.md §3 B)
)

# tables an older DB may still hold that no code reads or writes any more, and that are NOT
# dropped (no destructive migration, owner 2026-10-08): the rows just stay there, orphaned.
# Not in the schema (create_all no longer makes them); unclassified_tables leaves them out.
# A backup still carries them inside the DB snapshot.
LEGACY_TABLES: tuple = (
    "workout_metrics",  # per-activity hrTSS / rTSS / NP / TSS written at import; never read (2026-10-08)
    "mmp_cache",        # per-activity power mean-max, read only for those metrics' runFTP (2026-10-08)
)

# ---------------------------------------------------------------------------
# files (first match wins: the specific entries first)
# ---------------------------------------------------------------------------

DB = File("wko5coach.db", PER_TABLE, ROOT, "the app DB (db/database.py); its tables: TABLES", backup=ALWAYS)
FIT = File("fit/**", IMPORTED, SHARED, "synced FIT originals fit/{coros,tp}/<year>/<file>.fit "
           "(sync/storage.py)", backup=OPT_IN,
           deidentify=("GPS track (start / end)", "timestamps", "device serial / user profile in the file",
                       "file name: COROS labelId or TP workout id + date"))

FILES: tuple = (
    DB,
    File("wko5coach.db-*", PER_TABLE, ROOT, "the DB's WAL / shared memory / journal: in a backup through "
         "the snapshot of wko5coach.db"),
    # -- private (tenancy.private_path) ------------------------------------
    File("plan.json", USER, ROOT, "season plan: events, phases, thresholds, profile (engine/planning.py)",
         backup=ALWAYS, deidentify=("events: name, date, note", "profile")),
    File("racepower_solo_hikes.json", USER, ROOT, "which hikes were solo (racepower/athlete.py)",
         backup=ALWAYS, deidentify=("activity files / starts",)),
    File("racepower_hike_meta.json", USER, ROOT, "the pack carried per trip (racepower/athlete.py)",
         backup=ALWAYS, deidentify=("activity files / starts",)),
    File("racepower_shares/**", USER, ROOT, "race plans the user shared by link (racepower/share.py); "
         "never restored: a deleted / revoked link stays so", backup=ALWAYS, restore=False,
         deidentify=("the event's name / date / course",)),
    File("event_gpx/**", IMPORTED, ROOT, "uploaded event course GPX, gzip (engine/event_gpx.py); uploaded "
         "by the user, so backed up (no source to sync it from again)", backup=ALWAYS,
         deidentify=("GPS track",)),
    File("template_gpx/**", IMPORTED, ROOT, "uploaded training-route GPX of a 範本, gzip "
         "(engine/user_templates.py); backed up like event_gpx", backup=ALWAYS,
         deidentify=("GPS track (start / end)",)),
    File("backups/**", SECRET, ROOT, "pre-restore copies (made like a backup: no SECRET rows) and restore "
         "work files (api/backup.py _local_dir); they stay on the server"),
    # -- read from the base by a demo sandbox (tenancy.base_path) ----------
    File("engine.json", USER, BASE, "chart engine settings (wko5expr/config.py)", backup=ALWAYS),
    File("corrections.json", USER, BASE, "approved data corrections (wko5expr/corrections.py)",
         backup=ALWAYS, deidentify=("activity files",)),
    File("annotations.json", USER, BASE, "achievement names, 上河 times, notes, route names "
         "(engine/achievements.py)", backup=ALWAYS, deidentify=("names / notes (free text)", "activity starts")),
    File("views/**", USER, BASE, "the user's custom chart views (wko5expr/customviews.py)", backup=ALWAYS),
    # -- shared (tenancy.shared_path) ----------------------------------------
    FIT,
    File("routes/names.json", USER, SHARED, "the user's names for detected routes (engine/routes.py)",
         backup=ALWAYS, deidentify=("names (free text)",)),
    File("routes/**", DERIVED, SHARED, "route index, per-activity tracks, route weather (engine/routes.py, "
         "route_weather.py)", deidentify=("tracks: GPS points", "activity_weather: positions + times"),
         invalidated_by="INDEX_VERSION, the activity files' stamps; weather refetched"),
    File("cache/fit/**", DERIVED, SHARED, "FIT dataset cache: per-second channels (npz), index, estimate / "
         "PD memos, series, activity_auto (wko5expr/fitcache.py, fitdataset.py, api/activity_auto.py)",
         deidentify=("channels: GPS", "start times"),
         invalidated_by="file stamp + per-field versions (fitcache.versions); code_hash (SP-320 ①); "
                        "activity_auto.json per activity key (SP-334)"),
    File("cache/render/slots/*.json", DERIVED, SHARED, "per chart request (view, chart, range as asked): the key "
         "it was last drawn with, so a changed chart first shows that drawing marked as updating "
         "(wko5expr/render_cache.py, SP-336)",
         invalidated_by="repointed when the chart is drawn again; pruned with the render cache (LRU)"),
    File("cache/render/**", DERIVED, SHARED, "chart render cache, LRU 300 MB (wko5expr/render_cache.py)",
         invalidated_by="per chart (SP-336, wko5expr/chartscope.py): the activities of its range + warm-up, "
                        "today only when it reads it, the code its chart type reaches (codehash) + CACHE_VERSION"),
    File("achievements_cache.json", DERIVED, SHARED, "per-activity GPS summaries (engine/achievements.py)",
         deidentify=("footprint cells (GPS)", "days (dates)", "paths of the activity files"),
         invalidated_by="file stamp + ALGO_VERSION + peak count; an entry whose file is gone is dropped, "
                        "written atomically (SP-341)"),
    File("channel_peaks.json", DERIVED, SHARED, "per-activity channel peaks (wko5expr/dataset.py)",
         invalidated_by="file stamp + corrections; the file's code version dataset.per_workout_code "
                        "(code_hash + PER_WORKOUT_V + the FIT parse version, SP-341)"),
    File("workout_curves.json", DERIVED, SHARED, "per-activity mean-max curves (wko5expr/dataset.py)",
         invalidated_by="file stamp + corrections; the file's code version dataset.per_workout_code (SP-341)"),
    File("power_source_v1.json", DERIVED, SHARED, "WKO5 .wko4 power source (wko5expr/dataset.py)",
         invalidated_by="file stamp; the file's code version dataset.per_workout_code (SP-341)"),
    File("bad_activity_v1.json", DERIVED, SHARED, "WKO5 .wko4 bad-file features (wko5expr/dataset.py)",
         invalidated_by="file stamp + corrections; the file's code version dataset.per_workout_code (SP-341)"),
    File("series_*.json", DERIVED, SHARED, "WKO5 dataset per-activity values (wko5expr/dataset.py "
         "cached_series)", invalidated_by="file stamp + corrections + the day's thresholds; _vN in the key"),
    File("tp_tss.json", DERIVED, SHARED, "WKO5 TSS synced from TP, per .wko4 (wko5expr/dataset.py)",
         invalidated_by="file stamp"),
    File("moving_hrtss.json", DERIVED, SHARED, "WKO5 moving-time hrTSS per .wko4 (wko5expr/dataset.py)",
         invalidated_by="file stamp + LTHR"),
    File("activity_auto_*.json", DERIVED, SHARED, "WKO5 dataset's activity auto-classification "
         "(api/activity_auto.py)", invalidated_by="per activity: its file, metadata, the day's thresholds and their "
                                                 "runs window, the day's plan rows, the road rule's cross-run "
                                                 "values, its branch's code_hash (SP-334); a version-1 file is "
                                                 "recomputed once"),
    File("racepower_cptests.json", DERIVED, SHARED, "CP tests found in the FIT files (racepower/cptest.py)",
         invalidated_by="file stamp + _KEY_VERSION"),
    File("racepower_power_source.json", DERIVED, SHARED, "power source per FIT file (racepower/cptest.py)",
         invalidated_by="file stamp; the file's code version cptest.power_cache_code (fitcache parse + power "
                        "versions + FILE_CACHE_V, SP-341)"),
    File("racepower_bad_activity.json", DERIVED, SHARED, "bad-file features per FIT file "
         "(racepower/cptest.py)", invalidated_by="file stamp; the file's code version cptest.bad_cache_code "
                                                 "(SP-341)"),
    File("racepower_training_env.json", DERIVED, SHARED, "Open-Meteo averages of the training runs' weather "
         "(racepower/athlete.py)", invalidated_by="its stamp; refetched"),
    File("racepower_backtest.json", DERIVED, SHARED, "the last race-power back-test (racepower/backtest.py)",
         invalidated_by="rerun"),
    File("cwa_*.json", DERIVED, SHARED, "CWA forecasts / town elevations (racepower/weather.py)",
         invalidated_by="age (CWA_MAX_AGE_H); refetched"),
    File("climatology/**", DERIVED, SHARED, "Open-Meteo climatology (racepower/weather.py)",
         invalidated_by="CLIM_VERSION"),
    File("weather.json", SECRET, SHARED, "the CWA API key (racepower/weather.py save_key)"),
    # -- the server's own files: only in $WKO5COACH_HOME ---------------------
    File("secret.key", SECRET, ROOT, "the key that seals sync_state's secrets (settings/secrets.py)",
         scope=INSTANCE),
    File("tp_client.json", SECRET, ROOT, "TP OAuth client id / secret (sync/tp_client.py)", scope=INSTANCE),
    File("logs/**", DERIVED, ROOT, "the server log (applog.py); disposable, not tenant data (a demo base "
         "holds one: its build runs there)", scope=INSTANCE),
    File("research/**", DERIVED, ROOT, "probe-script output (backend/scripts/probe_coros_tl.py)",
         scope=INSTANCE),
    File("fits/**", IMPORTED, ROOT, "the COROS folder before fit/coros (backend/scripts/"
         "migrate_fit_folders.py); a duplicate the app no longer reads", scope=INSTANCE,
         deidentify=("as fit/**",)),
    File("base/**", DERIVED, ROOT, "demo instance: the synthetic athlete's tenant folders (each base/<name>/ "
         "is a tenant folder of its own, demo/sandbox.py)", scope=INSTANCE),
    File("sandboxes/**", DERIVED, ROOT, "demo instance: the visitors' sandboxes (each a tenant folder of its "
         "own; dropped after 24 h)", scope=INSTANCE),
    # -- demo only --------------------------------------------------------------
    File("meta.json", DERIVED, ROOT, "a demo sandbox's bookkeeping (demo/sandbox.py)", scope=DEMO),
    File("demo_manifest.json", DERIVED, ROOT, "the demo build's manifest (demo/build.py)", scope=DEMO),
    File("demo_courses/**", IMPORTED, ROOT, "the demo events' course GPX (demo/generate.py)", scope=DEMO),
    # -- anywhere ---------------------------------------------------------------
    File("**/*.tmp", DERIVED, ANYWHERE, "a half-written file of an atomic write, next to its target "
         "(renamed over it when done); safe to delete"),
)


# ---------------------------------------------------------------------------
# lookups
# ---------------------------------------------------------------------------

def _regex(pattern: str) -> re.Pattern:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


_RX = {f.pattern: _regex(f.pattern) for f in FILES}
_BY_TABLE = {t.name: t for t in TABLES}


def classify(rel: str) -> Optional[File]:
    """The entry of a path relative to a tenant folder (None = not registered)."""
    rel = rel.replace("\\", "/").lstrip("/")
    for f in FILES:
        if _RX[f.pattern].match(rel):
            return f
    return None


def table(name: str) -> Optional[Table]:
    return _BY_TABLE.get(name)


def of_class(cls: str) -> tuple[list[Table], list[File]]:
    return [t for t in TABLES if t.cls == cls], [f for f in FILES if f.cls == cls]


def backup_entries(include_fit: bool) -> list[File]:
    """What a backup holds: ALWAYS, plus OPT_IN with backup.include_fit."""
    return [f for f in FILES if f.backup == ALWAYS or (include_fit and f.backup == OPT_IN)]


# user_settings keys (SQL LIKE) that stay on this machine: this machine's backup folder / state,
# the debug API switch, the 課表訂閱 ICS link token (SP-355); compare debug_view.EXPORT_EXCLUDE
LOCAL_SETTINGS: tuple = ("backup.%", "debug.%", "plan.calendar")


def local_tables() -> list[str]:
    """Tables whose rows stay on this machine: emptied in a backup, kept on a restore."""
    return [t.name for t in TABLES if t.local]


def unclassified_tables(con: sqlite3.Connection) -> list[str]:
    """The tables of a DB without an entry in TABLES."""
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return [n for n in names if n not in _BY_TABLE and n not in RETIRED_TABLES and n not in LEGACY_TABLES]


def unclassified_files(folder: Path, skip: Iterable[str] = ()) -> list[str]:
    """The files under a tenant folder (relative, POSIX) without an entry; `skip` = top-level
    names left out (e.g. the demo root's base/ when walking each base on its own)."""
    folder = Path(folder)
    skip = set(skip)
    out = []
    for dirpath, dirnames, filenames in os.walk(folder):
        rel_dir = Path(dirpath).relative_to(folder).as_posix()
        if rel_dir == ".":
            dirnames[:] = [d for d in dirnames if d not in skip]
            rel_dir = ""
        for name in filenames:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if rel in skip or Path(dirpath, name).is_symlink():
                continue
            if classify(rel) is None:
                out.append(rel)
    return sorted(out)
