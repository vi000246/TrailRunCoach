"""SP-569 ②–⑤ (found writing the manual, SP-558): the 心率 ? says the max-HR order the code uses; the
English settings' 資料同步 section and the 課表 toolbar menus are English; the replan preview's weekday
reads Thu, not 「週T」; a free user's TrainingPeaks login says it is not open to accounts rather than
「訂閱後可用」 (a subscription never adds it). Plus the push ▾ menu, which shell.js locked as plan scopes.
Reads repo files and the membership table only."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from backend.i18n import pages as P

STATIC = Path(__file__).resolve().parents[1] / "static"
CJK = re.compile(r"[㐀-鿿]")


def cat(loc: str, ns: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8"))


def _without_scripts(html: str) -> str:
    return re.sub(r"<script\b.*?</script\s*>", "", html, flags=re.S | re.I)


class _Section(HTMLParser):
    """The visible text and title / aria-label / placeholder of the element with `id` (scripts excluded;
    `skip`: leave out <details> and these ids' subtrees)."""
    VOID = ("input", "br", "hr", "img", "meta", "link", "option")       # <option> is often left open

    def __init__(self, id_: str, skip: tuple = ()):
        super().__init__(convert_charrefs=True)
        self.id, self.depth, self.out, self.skip, self.skipping = id_, 0, [], skip, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.depth == 0 and a.get("id") == self.id:
            self.depth = 1
        elif self.depth and tag not in self.VOID:
            self.depth += 1
            if self.skipping or (self.skip and (tag == "details" or a.get("id") in self.skip)):
                self.skipping += 1
        if self.depth and not self.skipping:
            self.out += [v for k, v in attrs if k in ("title", "aria-label", "placeholder") and v]

    def handle_endtag(self, tag):
        if self.depth and tag not in self.VOID:
            self.depth -= 1
            if self.skipping:
                self.skipping -= 1

    def handle_data(self, data):
        if self.depth and not self.skipping and data.strip():
            self.out.append(data.strip())


def section_text(html: str, id_: str, skip: tuple = ()) -> list[str]:
    p = _Section(id_, skip)
    p.feed(_without_scripts(html))
    assert p.out, id_
    return p.out


# ---------------------------------------------------------------------------------- ② the 心率 ?

@pytest.mark.parametrize("loc, words", [
    ("zh-TW", ("你填的值", "手錶（COROS 帳號）的值", "近 365 天跑步", "年齡公式")),
    ("en", ("the value you enter", "the watch's value", "last 365 days", "age formula")),
])
def test_hr_help_follows_the_code_order(loc, words):
    """hr_profile.max_hr: your value → the watch → the 365-day runs → Tanaka's age formula."""
    t = cat(loc, "settings")["hr.help"]
    pos = [t.find(w) for w in words]
    assert all(p >= 0 for p in pos) and pos == sorted(pos), (t, pos)
    html = (STATIC / "settings.html").read_text("utf-8")
    if loc == "zh-TW":
        assert f'title="{t}"' in html                       # the page's own fallback says the same


def test_hr_profile_order_is_what_the_help_says():
    import inspect
    from backend.engine import hr_profile as HP
    src = inspect.getsource(HP.max_hr)
    order = [src.find(x) for x in ('_plan_row(ds, "mhr"', "watch is not None", "ev is not None", "age_max_hr(a)")]
    assert all(p >= 0 for p in order) and order == sorted(order)


# ---------------------------------------------------------------------------------- ③ English buttons

def test_settings_sync_section_is_english():
    html = P.render("settings", "en")
    # the 進階設定 panel (chart source, time zone, TL / RPE fits …), the FIT import and the dialogs are
    # not part of this ticket: the top rows and the three source boxes
    words = section_text(html, "sync", skip=("src-upload",))
    for zh in ("立即同步", "資料來源", "儲存同步設定", "資料同步", "每日自動同步", "第一次同步從", "刪除這個來源的同步檔案",
               "登入", "密碼", "啟用", "同步中"):
        assert not [w for w in words if zh in w], zh
    for en in ("Sync now", "Data source", "Save sync settings", "Data & sync", "First sync from", "Log in"):
        assert any(en in w for w in words), en
    # the box state written by the script reads the catalog
    s = (STATIC / "settings.html").read_text("utf-8")
    for zh in ('"未登入"', "`已登入 ${", '"已儲存同步設定"', "<dt>上次同步</dt>", '"登入已過期"'):
        assert zh not in s, zh
    en = cat("en", "settings")
    keys = [k for k in cat("zh-TW", "settings") if k.startswith("sync.")]
    assert all(en.get(k) and not CJK.search(en[k]) for k in keys), [k for k in keys if not en.get(k) or CJK.search(en[k])]


@pytest.mark.parametrize("menu", ["push-menu", "more-menu"])
def test_schedule_menus_are_english(menu):
    html = P.render("schedule", "en")
    words = section_text(html, menu)
    assert not [w for w in words if CJK.search(w)], words


def test_schedule_toolbar_labels_and_the_replan_preview_use_the_catalog():
    s = (STATIC / "schedule.html").read_text("utf-8")
    for zh in ('"過去的日子不能推送"', '"先點選日曆上的一天"', '"這週已經過去"', '"先點選一天"', "上次推送 <b>",
               '"＋ 新增"', "課表已經是最新的", "`套用 ${k} 項變動`", "錯過的課標成跳過，之後還沒改過的課", "已重排：",
               'const SCOPE_NAME = { day: "這一天"', 'ok = "確定"'):
        assert zh not in s, zh
    en, zh = cat("en", "schedule"), cat("zh-TW", "schedule")
    for k in [k for k in zh if k.startswith(("rc.", "push.", "more.", "st.", "scope.", "cf."))]:
        v = en.get(k)
        assert v, k
        vals = v.values() if isinstance(v, dict) else [v]
        assert not any(CJK.search(x) for x in vals), (k, v)
    assert en["rc.apply"] == {"one": "Apply 1 change", "other": "Apply {n} changes"}


# ---------------------------------------------------------------------------------- ④ the weekday

def test_no_week_prefix_glued_to_a_weekday_letter():
    """「週」 + the narrow weekday read 「週T 10/27」 in English: every such place takes the short name
    (週四 / Thu) now, as the calendar heads did since SP-502."""
    s = (STATIC / "schedule.html").read_text("utf-8")
    assert not re.search(r"週\$\{wd\(", s)
    assert "const wdl = (iso) => WD_HEAD[(dOf(iso).getDay() + 6) % 7];" in s
    assert 'const WD_HEAD = I18N.fmt.weekdays("short");' in s
    assert "${wdl(d)} ${md(d)}" in s                                  # the replan preview's day heads


# ---------------------------------------------------------------------------------- ⑤ TrainingPeaks

def test_the_session_says_what_a_subscription_adds():
    from backend import membership as MS, tenancy
    up = set(MS.session_view(tenancy.owner())["upgrade_scopes"])
    assert up == set(MS.ROLE_SCOPES[MS.SUBSCRIBER] - MS.ROLE_SCOPES[MS.USER])
    assert "calc:backtest" in up and "charts:365d" in up
    assert "sync:tp_web" not in up and "wko5" not in up and "admin" not in up


def test_the_lock_reason_is_not_subscribe_for_the_sites_own():
    s = (STATIC / "shell.js").read_text("utf-8")
    assert "訂閱後可用" not in s and "你的帳號沒有這個功能" not in s
    assert 'if (MS.role === "user" && missing.every((s) => UPGRADABLE.has(s))) return UPGRADE;' in s
    assert 'return T(MS.role === "user" ? "scope.site_only" : "scope.none");' in s
    for loc in ("zh-TW", "en"):
        c = cat(loc, "shell")
        assert c["scope.upgrade"] and c["scope.site_only"] and c["scope.none"], loc
    assert "站主" in cat("zh-TW", "shell")["scope.site_only"]
    assert "NT$99" not in cat("zh-TW", "shell")["scope.site_only"]


def test_the_push_range_menu_is_not_a_plan_scope():
    """shell.js locks every [data-scope] element as a plan scope (SP-330); the push ▾ menu's 這一天／這一週／
    整個周期 carried data-scope="day" … and were disabled for everyone (「你的帳號沒有這個功能」)."""
    s = _without_scripts((STATIC / "schedule.html").read_text("utf-8"))
    assert not re.search(r'data-scope="(day|week|phase)"', s)
    full = (STATIC / "schedule.html").read_text("utf-8")
    assert 'data-range="day"' in full and 'closest("[data-range]:not([disabled])")' in full
