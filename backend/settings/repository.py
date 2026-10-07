"""
Per-user settings repository.

Everything that differs per person — time zone, which sync source is primary,
connection details — lives in the `user_settings` table keyed by
(user_id, key), with a JSON value. The app is single-user today (user_id 1 =
athlete 1); callers always pass the id, so adding accounts later doesn't
change this interface.

Known keys and their defaults are declared in `DEFAULTS`; unknown keys are
rejected so a typo can't silently create a new setting.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, tzinfo
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import UserSetting

DEFAULT_USER = 1
SOURCES = ("coros", "trainingpeaks", "local")

DEFAULTS: dict[str, Any] = {
    # IANA zone for local workout dates; None = WKO5COACH_TZ env, then the system zone
    "athlete.timezone": None,
    # detected zone (engine/localtime.py): {"fit": {"offset_min", "file", "at"}, "browser": IANA}
    "athlete.timezone.auto": None,
    # 地區 (engine/region.py): tw | intl; None = auto from the home weather grid
    "athlete.region": None,
    # the first-run 精靈 (一般設定) was saved or dismissed (engine/athlete_profile.py)
    "athlete.setup.done": False,
    # 「稍後再說」 on the 精靈 (SP-211): ISO time; it asks again REMIND_DAYS later while
    # weight / sex / age are still missing (engine/athlete_profile.setup_remind)
    "athlete.setup.later_at": None,
    # 跑步經驗問卷 (engine/experience.py, SP-290): {runs_per_week, minutes_per_run, longest_min,
    # can_run_30, at}; None = never answered (asked by the 精靈 unless the data has 4 good weeks)
    "athlete.experience": None,
    # 每人校正 last run (engine/calibrate.py, SP-320 ④): ISO time; a sync refits at most once
    # per CALIB_EVERY_DAYS, 「重新校正」 (POST /api/v1/calib/run) always runs
    "athlete.calib_last_run": None,
    # 比賽成績 (engine/race_results.py, SP-290): [{date, distance_km, time_s, trail, source,
    # confirmed, name?}] — one list shared by the questionnaire, SP-293 and SP-276 (the E pace,
    # engine/e_pace.py: the newest confirmed road row; its 設定 block writes here too)
    "athlete.race_results": [],
    # 主要訓練項目 (engine/primary_sport.py): auto (follow the suggestion from the data / the
    # next A race) | trail (越野跑, the original behaviour) | road (路跑／馬拉松)
    "athlete.primary_sport": "auto",
    # 資料來源 (backend/sync/primary.py): the ONE synced source in use — charts,
    # automatic syncs, CP scan and totals read only it. coros | trainingpeaks;
    # None / an old "auto" is migrated on first read (primary.current)
    "sync.primary_source": None,
    "sync.coros.enabled": True,
    "sync.trainingpeaks.enabled": True,
    # log in with OAuth client credentials from TP_CLIENT_ID/SECRET or
    # ~/.wko5coach/tp_client.json (see .env.example). None = auto: on only when the
    # credentials are configured on this machine; False = website login only.
    "sync.trainingpeaks.use_wko5_client": None,
    # outcome of the last run: {at, trigger, status, downloaded, checked, errors, error}
    "sync.coros.last_result": None,
    "sync.trainingpeaks.last_result": None,
    # the last run that worked (status ok / partial): {at, trigger, downloaded}. A
    # failed run never moves it (SP-88: 上次成功同步 on the overview banner)
    "sync.coros.last_ok": None,
    # the one-time backfill of COROS's post-run self-rating over the last 8 weeks (SP-231,
    # sync/coros_client.backfill_feel): {done, passes, checked, filled, at}; internal
    "sync.coros.rpe_backfill": None,
    "sync.trainingpeaks.last_ok": None,
    # daily automatic sync: "HH:MM" local time, None = off
    "sync.schedule.daily_time": None,
    "sync.schedule.last_run": None,          # local ISO date of the last scheduled run
    # sync when a page is opened and the last sync is older than N hours
    "sync.auto_on_open.enabled": True,
    "sync.auto_on_open.hours": 6,
    # which data the charts / overview / race power read: source (the 資料來源
    # above, its folder only) | wko5 (cross-check). Old values (synced / coros /
    # tp) read as source (wko5expr/datasource.current_source)
    "charts.data_source": "source",
    # set when the user picks the chart source (設定 → 進階設定, the viewer's source chip);
    # no longer read (single 資料來源), kept so stored rows stay valid
    "charts.data_source.chosen": False,
    # COROS / TP source: read thresholds / weight from the WKO5 athlete file
    # (opt-in cross-check; default = plan → athlete_settings → estimates, fitdataset.py)
    "charts.fit_settings_from_wko5": False,
    # folder searched (recursively) for your own exported WKO5 views (*.wko5chart);
    # None = no imported WKO5 views (env WKO5_VIEWS_DIR wins; api/wko5views.views_dir)
    "charts.wko5_views_dir": None,
    # power-based models (race-power envelope / CP / PD, power TSS, power effort
    # checks) also read watch-estimated (wrist) power; False = Stryd only
    # (backend/engine/power_source.py). HR / pace paths always use every run.
    "power.accept_watch_power": False,
    # 使用功率: False = the athlete trains by HR only — the viewer, overview and activity
    # pages hide power-only charts, cards and fields (wko5expr/power_use.py); the models
    # themselves are unchanged
    # None = auto (engine/athlete_profile.use_power): on for Stryd, watch power only when
    # power.accept_watch_power is on, off without a power meter
    "charts.power.enabled": None,
    # leave bad activity files (a run recorded in a car / on a bike, impossible
    # power; backend/engine/bad_activity.py) out of every model; the per-activity
    # overrides (keep / exclude) apply either way
    "activities.exclude_bad": True,
    # 心率 (engine/hr_profile.py): the COROS account's HR settings, written by the COROS
    # login / sync — {max_hr, rest_hr, lthr, ratios: {lthr, hrr, hrmax}, hr_zone_type, at};
    # None = never read. Max / rest HR the user enters are dated plan thresholds (mhr / rhr).
    "athlete.coros_profile": None,
    # the COROS account's HR settings over time (engine/coros_compare.py, SP-67): one entry per
    # change [{at, lthr, max_hr, rest_hr, ratios, hr_zone_type}], oldest first; written by the
    # COROS login / sync only. None = nothing recorded yet
    "athlete.coros_profile_history": None,
    # 起始 CTL／ATL (engine/load_guard.py PMC_START_KEY, SP-68): {date: ISO, ctl, atl} — the
    # PMC's values at the start of that date (charts, status, guardrails); None = automatic
    # (the first 4 weeks' mean daily TSS). Replaces athlete_settings.initial_ctl_run /
    # initial_atl_run (React app, no longer read)
    "athlete.pmc_start": None,
    # 課表心率區間 (engine/hr_profile.py): lthr (COROS % LTHR, default) | hrr | hrmax — the
    # 課表's HR targets only; the HR-zone charts keep their own selector
    "plan.hr_zone_model": "lthr",
    # workout route map (viewer 單次活動): default basemap id and overlay ids;
    # the map can switch them temporarily (remembered per browser)
    "charts.map.basemap": None,               # None = by 地區 (engine/region.py): tw 魯地圖, intl OSM
    "charts.map.overlays": [],
    # 課表偏好 (engine/plan_prefs.py). Every default reproduces the planner's
    # own behaviour, so an athlete who never opens the panel gets today's plan.
    "plan.prefs.days": None,                  # 7 bools Mon..Sun (False = rest day); None = every day
    "plan.prefs.long_day": "auto",            # sat | sun | auto (the athlete's usual long day)
    "plan.prefs.cap_weekday": None,           # minutes per session; None = no cap
    "plan.prefs.cap_long": None,              # minutes for the long day; None = same as weekday
    "plan.prefs.cap_mode": "soft",            # soft (盡量不超過) | hard (絕對不超過)
    "plan.prefs.runs_per_week": None,         # 3-7; None = auto
    "plan.prefs.quality_per_week": None,      # 0-2; None = auto (at most 1, gated)
    "plan.prefs.strength_per_week": None,     # 0-3; None = auto
    "plan.prefs.strength_days": [],           # weekdays 0-6 for strength; [] = with easy runs
    "plan.prefs.weekly_hours": None,          # custom weekly cap (h); None = CTL ramp rules
    "plan.prefs.terrain_easy": "any",         # road | trail | any
    "plan.prefs.terrain_long": "auto",        # road | trail | hike | auto
    "plan.prefs.terrain_quality": "any",      # flat | hill | any
    "plan.prefs.interval_target": "power",    # power | hr (legacy; hr reads as target_basis = hr)
    "plan.prefs.target_basis": "auto",        # 目標依據 (engine/target_policy.py): auto | hr | power
    # CP 測試方式 (engine/cp_protocols.py): quick 20 min all-out | standard 12′ +
    # 30′ + 3′ | race (a 5–10 K race instead). The athlete chose quick as the default.
    "plan.prefs.cp_test_protocol": "quick",
    # 熱適應課 (engine/heat_plan.py): auto = only before a hot A/B race; off = never
    "plan.prefs.heat": "auto",
    "plan.prefs.heat_method": "run",          # run | overdress | bath | sauna | mixed
    # 間歇門檻 (engine/quality_gate.py): what unlocks base-phase intervals
    "plan.prefs.quality_gate": "auto",        # auto | ua_gap | friel_drift | xu_drift | plateau | weeks | none
    "plan.prefs.quality_gate_weeks": 8,       # weeks mode: base-phase weeks before intervals (2-16)
    # AeT 飄移測試 (engine/aet_test.py, 50 min): weekday = Mon–Fri only (the athlete
    # trail-runs on weekends) | any = the interval days (Tue first)
    "plan.prefs.aet_test_days": "weekday",
    # AeT 測試方式 (engine/aet_test.py PROTOCOLS): auto = 徐國峰 90 分 (backup UA 40) | xu90 | ua60 | ua40 | evoke60 | friel
    "plan.prefs.aet_test_protocol": "auto",
    # 間歇暖身／緩和 (engine/interval_library.py §C3): the city run to the riverside (never cut)
    # and the cool-down (10 when running home)
    "plan.prefs.warmup_commute_min": 10,
    "plan.prefs.cooldown_min": 5,
    # 偏好的星期 (engine/plan_prefs.py day_conflicts / place): {kind: [first, second]}; [] / missing = 不指定
    "plan.prefs.pref_days": {},
    "plan.prefs.pref_keep": [],               # conflict codes kept anyway (照我的偏好)
    "plan.prefs.b2b": True,                   # suggest a due B2B weekend (engine/b2b.py); off = never
    # 轉換期 after an A race's 恢復期 (engine/planning.auto_phases, SP-73): weeks, 0 = off;
    # 5–6 only after a 超馬級+ race (planning.transition_weeks_for, SP-109), else ≤ 4
    "plan.prefs.transition_weeks": 3,
    # 減量期天數 of a road marathon / an ultra (engine/planning.taper_days, SP-96): 14–21
    "plan.prefs.taper_days": 14,
    # 肌力動作 (engine/strength_moves.py, SP-191): {type: move} the athlete picked ({} = the defaults)
    # and the equipment they don't have (bar | band)
    "plan.prefs.strength_moves": {},
    "plan.prefs.strength_no_gear": [],
    # accepted B2B weekends (engine/b2b.py ACCEPTED_KEY): [{week, days, minutes, uids, at}]
    "plan.b2b.accepted": [],
    # the floating suggestion box (engine/suggestions.py): {suggestion id: {action, at, week}}
    "plan.suggestions.dismissed": {},
    # 不排課日期 (engine/blackouts.py): one-off ranges [{id, start, end, label}]
    # on which nothing is planned; separate from the weekly plan.prefs.days
    "plan.blackouts": [],
    # 睡在高處的紀錄 (engine/altitude.py NIGHTS_KEY, SP-259): [{day, m}] — the night from `day`'s
    # evening slept at m metres, marked on the 課表 calendar; counted by the 高度適應提醒
    "altitude.nights": [],
    # 自動調整課表 (engine/plan_auto.py): after a sync that imported an activity,
    # reconcile + adapt (engine/adapt.py) + push the next N days to COROS
    "plan.auto.enabled": True,
    "plan.auto.push": None,                   # push the window to COROS automatically; None = auto: on when COROS is logged in
    "plan.auto.push_days": 7,                 # 1-14 days from today
    # where the plan is pushed (sync/workout_targets): one active provider; 進階設定.
    # Only enabled providers can be chosen (Garmin / intervals.icu are stubs for now)
    "plan.push.provider": "coros",
    "plan.auto.confirm_big": True,            # hold big changes for the user's approval
    "plan.auto.notify": None,                 # watch (a 課表待確認 workout on COROS) | overview (banner only); None = auto (watch with COROS)
    # 跑後自評 (SP-231, engine/adapt.py rule D): an easy / long run rated Hard or more pushes a
    # hard session within 48 h back (推估); on by default
    "plan.auto.rpe_rule": True,
    # internal: {stamp, phase, rejected: [fingerprint]} of the last automatic run
    "plan.auto.state": None,
    # 傷病紀錄 (engine/injuries.py; docs/plans/injury-tracking.plan.md §4): 「跟受傷前很像」
    # 提醒 (off; can be turned on only with ≥ 5 analysed injuries), 傷停後恢復期往上一級 (on,
    # 推估), the user's own body areas (reused in the picker)
    "injury.pattern_alerts": False,
    "injury.reentry_step_up": True,
    "injury.custom_areas": [],
    # activities the user unlinked from a planned session (engine/plan_match.py):
    # [{start, index}] — never auto-matched again (start: the activity's local start)
    "plan.match.unlinked": [],
    # 備份 (engine/backup.py, api/backup.py): target folder (absolute; None = not set up),
    # daily automatic backup, include the synced FIT originals (進階),
    # outcome of the last attempt {at, trigger, status, name, size, error} and the last good one.
    # Backups are not encrypted (owner 2026-10-02).
    "backup.dir": None,
    "backup.auto": True,
    "backup.include_fit": False,
    "backup.last_result": None,
    "backup.last_ok": None,
    # 課表訂閱 (engine/calendar_feed.py, api/calendar_feed.py): {token, origin} of the
    # secret ICS feed URL /share/calendar/<token>.ics; None = off. origin = the address the
    # settings page was opened at (the feed's 「編輯」 links); 重設網址 replaces the token
    "plan.calendar": None,
    # 賽事計算機「匯出至課表」 race TSS calibration (engine/racepower/tss_calib.py):
    # {races: {ext_key: {raw, day, actual}}} — the raw estimate per exported race and the
    # matched activity's TSS once it is done; None = no export yet
    "racepower.race_tss_calib": None,
    # COROS TL ↔ TSS (engine/coros_tl.py, SP-38): the per-athlete refit after each sync
    # {groups: {power|hr|linear: {family, params, n, w, loo, backtest, fitted_at}}, checked_at};
    # None = the defaults (推估). Written by the refit only
    "coros.tl_model": None,
    # closed loop: pushed 「負荷」 steps and the TSS actually run in them
    # {sessions: {uid: {day, steps: [{i, n, tss, tl}], actual}}} (engine/coros_tl.py)
    "coros.tl_load_calib": None,
    # 「負荷」 entered by RPE (engine/rpe_load.py, SP-57): TSS per session-RPE unit fitted on the
    # activities with a watch RPE {factor, n, w, ratio, loo, fitted_at}; None = the default (推估).
    # Planning targets only — the recorded load is never corrected by RPE. Written by the refit only
    "rpe.load_model": None,
}
# keys that were removed: db/database.py init_db deletes any stored row
# (backup.encryption held the sealed scrypt-derived backup key)
# athlete.race_result: SP-276's single E-pace race, moved into athlete.race_results (SP-290) by
# db/database._migrate_schema before it is deleted (owner 2026-10-06: one shared list)
RETIRED_KEYS = ("backup.encryption", "athlete.race_result")
AUTO_NOTIFY = ("watch", "overview")
AUTO_KEYS = ("plan.auto.enabled", "plan.auto.push", "plan.auto.push_days", "plan.auto.confirm_big",
             "plan.auto.notify", "plan.auto.rpe_rule")
MAP_BASEMAPS = ("rudy", "google-terrain", "nlsc-emap", "nlsc-photo", "osm")
MAP_OVERLAYS = ("contour", "google-roads", "nlsc-roads")
PREF_ENUMS = {
    "plan.prefs.long_day": ("sat", "sun", "auto", "mon", "tue", "wed", "thu", "fri"),
    "plan.prefs.cap_mode": ("soft", "hard"),
    "plan.prefs.terrain_easy": ("road", "trail", "any"),
    "plan.prefs.terrain_long": ("road", "trail", "hike", "auto"),
    "plan.prefs.terrain_quality": ("flat", "hill", "any"),
    "plan.prefs.interval_target": ("power", "hr"),
    "plan.prefs.target_basis": ("auto", "hr", "power"),
    "plan.prefs.cp_test_protocol": ("quick", "standard", "race"),
    "plan.prefs.heat": ("auto", "off"),
    "plan.prefs.heat_method": ("run", "overdress", "bath", "sauna", "mixed"),
    "plan.prefs.quality_gate": ("auto", "ua_gap", "friel_drift", "xu_drift", "plateau", "weeks", "none"),
    "plan.prefs.aet_test_days": ("weekday", "any"),
    "plan.prefs.aet_test_protocol": ("auto", "xu90", "ua60", "ua40", "evoke60", "friel"),
}
PREF_INTS = {                                 # key -> (lo, hi); None always allowed
    "plan.prefs.cap_weekday": (20, 300),
    "plan.prefs.cap_long": (20, 600),
    "plan.prefs.runs_per_week": (3, 7),
    "plan.prefs.quality_per_week": (0, 2),
    "plan.prefs.strength_per_week": (0, 3),
    "plan.prefs.quality_gate_weeks": (2, 16),
    "plan.prefs.warmup_commute_min": (0, 30),
    "plan.prefs.cooldown_min": (0, 20),
    "plan.prefs.transition_weeks": (0, 6),
    "plan.prefs.taper_days": (14, 21),
}


class UnknownSetting(KeyError):
    pass


CALIB_PREFIX = "athlete.calib."


def known(key: str) -> bool:
    """A declared key, or `athlete.calib.<name>` for a registered calibration
    item (engine/calibrate.py; default None = not fitted yet)."""
    if key in DEFAULTS:
        return True
    if key.startswith(CALIB_PREFIX):
        from backend.engine import calibrate
        return calibrate.is_key(key)
    return False


class SettingsRepository:
    def __init__(self, db: AsyncSession, user_id: int = DEFAULT_USER):
        self.db, self.user_id = db, user_id

    async def _row(self, key: str) -> Optional[UserSetting]:
        res = await self.db.execute(select(UserSetting).where(
            UserSetting.user_id == self.user_id, UserSetting.key == key))
        return res.scalar_one_or_none()

    async def get(self, key: str) -> Any:
        if not known(key):
            raise UnknownSetting(key)
        row = await self._row(key)
        return DEFAULTS.get(key) if row is None else json.loads(row.value_json)

    async def set(self, key: str, value: Any) -> None:
        if not known(key):
            raise UnknownSetting(key)
        validate(key, value)
        row = await self._row(key)
        if row is None:
            row = UserSetting(user_id=self.user_id, key=key)
            self.db.add(row)
        row.value_json = json.dumps(value)
        row.updated_at = datetime.utcnow()
        await self.db.flush()

    async def all(self) -> dict[str, Any]:
        res = await self.db.execute(select(UserSetting).where(UserSetting.user_id == self.user_id))
        stored = {r.key: json.loads(r.value_json) for r in res.scalars()}
        return {k: stored.get(k, v) for k, v in DEFAULTS.items()}

    async def timezone(self) -> tzinfo:
        return resolve_tz(await self.get("athlete.timezone"), auto=await self.get("athlete.timezone.auto"))


def validate(key: str, value: Any) -> None:
    if key.startswith(CALIB_PREFIX):
        from backend.engine import calibrate
        calibrate.validate_entry(value)
        return
    if key == "athlete.experience":
        from backend.engine import experience
        experience.validate(value)
    if key == "athlete.race_results":
        from backend.engine import race_results
        race_results.validate(value)
    if key == "athlete.timezone.auto" and value is not None and not isinstance(value, dict):
        raise ValueError("athlete.timezone.auto must be an object or null")
    if key == "athlete.region" and value not in (None, "tw", "intl"):
        raise ValueError("athlete.region must be tw, intl or null (auto)")
    if key == "athlete.timezone" and value is not None:
        resolve_tz(value, strict=True)
    if key == "athlete.primary_sport" and value not in ("auto", "trail", "road"):
        raise ValueError("primary sport must be auto, trail or road")
    if key == "sync.primary_source" and value not in ("coros", "trainingpeaks"):
        raise ValueError("data source must be coros or trainingpeaks")
    if key == "sync.schedule.daily_time" and value is not None:
        import re
        if not (isinstance(value, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value)):
            raise ValueError("daily sync time must be HH:MM (24 h) or null")
    if key == "sync.auto_on_open.hours" and not (isinstance(value, (int, float)) and 0 < value <= 168):
        raise ValueError("auto-sync threshold must be 1-168 hours")
    if key == "charts.data_source" and value not in ("source", "wko5"):
        raise ValueError("chart data source must be source or wko5")
    if key == "charts.wko5_views_dir" and value is not None and not (isinstance(value, str) and value.strip()):
        raise ValueError("charts.wko5_views_dir must be a folder path or null")
    if key == "charts.map.basemap" and value is not None and value not in MAP_BASEMAPS:
        raise ValueError(f"map basemap must be one of {MAP_BASEMAPS}")
    if key == "charts.map.overlays" and not (
            isinstance(value, list) and all(v in MAP_OVERLAYS for v in value)
            and len(set(value)) == len(value)):
        raise ValueError(f"map overlays must be a list of distinct {MAP_OVERLAYS}")
    if key == "sync.trainingpeaks.use_wko5_client" and value not in (None, True, False):
        raise ValueError(f"{key} must be true/false/null")
    if key in ("charts.fit_settings_from_wko5", "power.accept_watch_power", "activities.exclude_bad", "athlete.setup.done",
               "charts.data_source.chosen") \
            and not isinstance(value, bool):
        raise ValueError(f"{key} must be true/false")
    if key.endswith(".enabled") and not isinstance(value, bool) and not (key == "charts.power.enabled" and value is None):
        raise ValueError(f"{key} must be true/false")
    if key.startswith("plan.prefs."):
        _validate_pref(key, value)
    if key == "plan.blackouts":
        from backend.engine.blackouts import validate as validate_blackouts
        validate_blackouts(value)
    if key == "altitude.nights":
        from backend.engine.altitude import validate_nights
        validate_nights(value)
    if key in ("plan.auto.confirm_big", "plan.auto.rpe_rule") and not isinstance(value, bool):
        raise ValueError(f"{key} must be true/false")
    if key == "plan.auto.push" and value is not None and not isinstance(value, bool):
        raise ValueError(f"{key} must be true/false")
    if key == "plan.auto.push_days" and (isinstance(value, bool) or not isinstance(value, int)
                                         or not 1 <= value <= 14):
        raise ValueError("plan.auto.push_days must be an integer 1-14")
    if key == "plan.push.provider":
        from backend.sync import workout_targets as WT
        if value not in WT.enabled_ids():
            raise ValueError(f"plan.push.provider must be one of {WT.enabled_ids()} (others are not enabled yet)")
    if key == "plan.auto.notify" and value is not None and value not in AUTO_NOTIFY:
        raise ValueError(f"plan.auto.notify must be one of {AUTO_NOTIFY}")
    if key == "plan.b2b.accepted" and not (isinstance(value, list) and all(
            isinstance(e, dict) and isinstance(e.get("week"), str) and isinstance(e.get("days"), list)
            and len(e["days"]) == 2 for e in value)):
        raise ValueError("plan.b2b.accepted must be a list of {week, days: [d1, d2], minutes}")
    if key == "plan.suggestions.dismissed" and not (isinstance(value, dict) and all(
            isinstance(k, str) and isinstance(v, dict) for k, v in value.items())):
        raise ValueError("plan.suggestions.dismissed must be {id: {action, at}}")
    if key in ("injury.pattern_alerts", "injury.reentry_step_up") and not isinstance(value, bool):
        raise ValueError(f"{key} must be true/false")
    if key == "injury.custom_areas":
        from backend.engine import injuries as INJ
        if not (isinstance(value, list) and len(value) <= INJ.CUSTOM_MAX
                and all(INJ.clean_custom(v) == v for v in value) and len(set(value)) == len(value)):
            raise ValueError(f"injury.custom_areas must be distinct labels of 1-{INJ.CUSTOM_MAX_LEN} characters")
    if key == "athlete.coros_profile" and value is not None and not isinstance(value, dict):
        raise ValueError("athlete.coros_profile must be an object or null")
    if key == "athlete.coros_profile_history":
        from backend.engine.coros_compare import validate as validate_history
        validate_history(value)
    if key == "athlete.pmc_start" and value is not None:
        from backend.engine.load_guard import parse_manual
        if parse_manual(value) is None:
            raise ValueError("athlete.pmc_start must be {date: YYYY-MM-DD, ctl, atl} (0-300) or null")
    if key == "plan.hr_zone_model" and value not in ("lthr", "hrr", "hrmax"):
        raise ValueError("plan.hr_zone_model must be lthr, hrr or hrmax")
    if key == "sync.coros.rpe_backfill" and value is not None and not isinstance(value, dict):
        raise ValueError(f"{key} must be an object or null")
    if key == "plan.auto.state" and value is not None and not isinstance(value, dict):
        raise ValueError("plan.auto.state must be an object or null")
    if key == "backup.dir" and value is not None and not (
            isinstance(value, str) and value.strip() and os.path.isabs(value)):
        raise ValueError("backup.dir must be an absolute folder path or null")
    if key in ("backup.auto", "backup.include_fit") and not isinstance(value, bool):
        raise ValueError(f"{key} must be true/false")
    if key in ("backup.last_result", "backup.last_ok") and value is not None and not isinstance(value, dict):
        raise ValueError(f"{key} must be an object or null")
    if key == "plan.calendar" and value is not None:
        from backend.engine.calendar_feed import validate_setting
        validate_setting(value)
    if key == "coros.tl_model":
        from backend.engine.coros_tl import validate as validate_tl
        validate_tl(value)
    if key == "rpe.load_model":
        from backend.engine.rpe_load import validate as validate_rpe
        validate_rpe(value)
    if key == "coros.tl_load_calib":
        from backend.engine.coros_tl import validate_load
        validate_load(value)
    if key == "racepower.race_tss_calib":
        from backend.engine.racepower.tss_calib import validate as validate_calib
        validate_calib(value)
    if key == "plan.match.unlinked" and not (isinstance(value, list) and all(
            isinstance(e, dict) and isinstance(e.get("start"), str) for e in value)):
        raise ValueError("plan.match.unlinked must be a list of {start, index}")


def _validate_pref(key: str, value: Any) -> None:
    if key == "plan.prefs.b2b" and not isinstance(value, bool):
        raise ValueError("plan.prefs.b2b must be true/false")
    if key in PREF_ENUMS and value not in PREF_ENUMS[key]:
        raise ValueError(f"{key} must be one of {PREF_ENUMS[key]}")
    if key in PREF_INTS and value is not None:
        lo, hi = PREF_INTS[key]
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise ValueError(f"{key} must be an integer {lo}-{hi} or null")
    if key == "plan.prefs.days" and value is not None:
        if not (isinstance(value, list) and len(value) == 7 and all(isinstance(v, bool) for v in value)):
            raise ValueError("plan.prefs.days must be 7 true/false (Mon..Sun) or null")
        if not any(value):
            raise ValueError("至少要有一天可以練")
    if key == "plan.prefs.strength_days" and not (
            isinstance(value, list) and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 6
                                            for v in value) and len(set(value)) == len(value)):
        raise ValueError("plan.prefs.strength_days must be distinct weekdays 0-6")
    if key == "plan.prefs.pref_days" and value is not None:
        kinds = ("quality", "aet_test", "cp_test", "strides", "rest")   # rest: 休息日偏好 (SP-82)
        ok = isinstance(value, dict) and all(
            k in kinds and isinstance(v, list) and len(v) <= 2 and len(set(v)) == len(v)
            and all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 6 for x in v)
            for k, v in value.items())
        if not ok:
            raise ValueError(f"plan.prefs.pref_days must be {{kind: [weekday 0-6, (second)]}} for {kinds}")
    if key == "plan.prefs.pref_keep" and value is not None and not (
            isinstance(value, list) and all(isinstance(x, str) and len(x) <= 40 for x in value)):
        raise ValueError("plan.prefs.pref_keep must be a list of conflict codes")
    if key in ("plan.prefs.strength_moves", "plan.prefs.strength_no_gear"):
        from backend.engine import strength_moves as SM
        (SM.clean_moves if key.endswith("moves") else SM.clean_gear)(value, strict=True)
    if key == "plan.prefs.weekly_hours" and value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 40):
        raise ValueError("plan.prefs.weekly_hours must be 1-40 hours or null")


def resolve_tz(name: Optional[str], strict: bool = False, auto: Optional[dict] = None) -> tzinfo:
    """Setting -> WKO5COACH_TZ env -> the detected zone (`auto`, the
    athlete.timezone.auto setting: engine/localtime.py — the latest FIT's
    local-time offset, the browser's zone when it agrees) -> the machine's
    local zone."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    for cand in (name, None if strict else os.getenv("WKO5COACH_TZ")):
        if not cand:
            continue
        try:
            return ZoneInfo(cand)
        except (ZoneInfoNotFoundError, ValueError):
            if strict:
                raise ValueError(f"unknown time zone {cand!r}")
    if not strict and auto:
        from backend.engine.localtime import zone_of_auto
        z = zone_of_auto(auto)
        if z is not None:
            return z
    return datetime.now().astimezone().tzinfo
