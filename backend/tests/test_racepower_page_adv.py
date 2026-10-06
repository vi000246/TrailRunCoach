"""SP-214: the calculator page keeps the core inputs in view and the tuning
options (with working defaults) in the collapsed 進階計算選項 panel.
Reads backend/static/racepower.html and its catalogs only."""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = STATIC / "racepower.html"

CORE = ["event", "type", "tripkind", "mode", "target", "tpace", "tpower", "effort", "csrc", "dist", "gain", "loss",
        "days", "drop", "ogain", "tripdays", "date", "start", "pack", "peak", "latlon", "stops-box", "calc"]
ADVANCED = ["split", "sigma", "eps", "minlen", "flatpct", "strategy", "amount", "hup", "hdown", "accl",
            "hourlyheat", "heat-line", "heat-accl", "heat-s", "hrband", "packdays", "heatz", "formula"]


class _Ids(HTMLParser):
    """id -> the ids of its open ancestors (script / style content is skipped)."""
    VOID = {"input", "br", "img", "meta", "link", "hr", "source", "col", "area", "base", "embed", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack, self.ids, self.dup, self.keys = [], {}, [], set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            if a["id"] in self.ids:
                self.dup.append(a["id"])
            self.ids[a["id"]] = [x for _t, x in self.stack if x]
        if a.get("data-i18n"):
            self.keys.add(a["data-i18n"])
        if tag not in self.VOID:
            self.stack.append((tag, a.get("id")))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def _page():
    p = _Ids()
    p.feed(PAGE.read_text("utf-8"))
    return p


def test_core_inputs_stay_in_view_and_tuning_options_are_in_the_panel():
    p = _page()
    for i in CORE:
        assert i in p.ids, i
        assert "calc-adv" not in p.ids[i], f"{i} is a core input, not an advanced one"
    for i in ADVANCED:
        assert "calc-adv" in p.ids.get(i, []), f"{i} should be inside 進階計算選項"
    # the panel sits in the target section, before the 計算 button; its summary is visible text
    assert "target-sec" in p.ids["calc-adv"] and "calc-adv" in p.ids["adv-sum"]


def test_page_ids_are_unique_and_the_split_details_no_longer_take_adv():
    p = _page()
    assert p.dup == []
    # id="adv" belongs to the 你的數據 table's details (built in the script), not the 分段 options
    assert "adv" not in p.ids and "split-adv" in p.ids


def test_panel_starts_closed_and_its_strings_have_both_languages():
    src = PAGE.read_text("utf-8")
    assert re.search(r'<details class="calc-adv" id="calc-adv">', src)          # no `open` attribute
    used = set(re.findall(r'T\("(adv\.[\w.]+)"', src)) | {k.split(".", 1)[1] for k in _page().keys
                                                            if k.startswith("racepower.adv.")}
    assert {"adv.title", "adv.hint", "adv.changed", "adv.sep", "adv.n.split", "adv.n.formula"} <= used
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "racepower.json").read_text("utf-8"))
        missing = [k for k in used if not cat.get(k)]
        assert missing == [], (loc, missing)
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "racepower.json").read_text("utf-8"))
    en = json.loads((STATIC / "i18n" / "en" / "racepower.json").read_text("utf-8"))
    assert "{list}" in zh["adv.changed"] and "{list}" in en["adv.changed"]
