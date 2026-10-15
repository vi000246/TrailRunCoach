"""
SP-579 (跟團 G3): the race calculator's 「跟團對照」 card and the profile coloured by tier.

  * the card: each leg's group time, your time, the tier and its zone in words — never a bpm (β ≈ 0,
    SP-575 Q1-4); per day the time above AeT and the legs behind / waiting;
  * the profile: each leg in its tier's colour (light and dark), the legs behind outlined;
  * the pack reminder (porters carry the shared food) and the closing-time reminder are text only;
  * zh-TW and en. The screenshots are with the ticket (desktop and phone).
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "backend/static/racepower.html").read_text("utf-8")
TIERS = ("wait", "easy", "right", "hard", "behind")


def _fn(name, end):
    return HTML[HTML.index(f"function {name}"):HTML.index(end, HTML.index(f"function {name}"))]


def cat(loc):
    return json.loads((ROOT / f"backend/static/i18n/{loc}/racepower.json").read_text("utf-8"))


def test_the_card_is_there_and_never_prints_a_bpm():
    assert '<section id="group-sec" class="tab-calc hidden"><div id="group"></div></section>' in HTML
    assert "renderResults(); renderGroup();" in HTML
    card = _fn("renderGroup()", "const SIGMA_ZH")
    for col in ("group.col.group", "group.col.you", "group.col.effort", "group.col.zone", "group.col.above"):
        assert col in card
    assert "bpm" not in card and "hr_cap" not in card
    assert 'T("group.pack")' in card and 'T("group.close")' in card
    for loc in ("zh-TW", "en"):
        c = cat(loc)
        texts = [v for k, v in c.items() if k.startswith("group.")]
        assert texts and not any(re.search(r"\bbpm\b|\d{3}\s*bpm", t) for t in texts)


def test_the_profile_colours_each_leg_by_tier_in_both_themes():
    prof = _fn("renderProfile", "function courseLatLngs")
    assert 'css("--g-" + x.tier)' in prof and 'x.tier === "behind" ? css("--g-behind")' in prof
    assert "group.legend" in prof
    for t in TIERS:
        assert HTML.count(f"--g-{t}:") == 3                       # light, dark (media), dark (attribute)
    assert "gLegAt(" in _fn("rowTip", "function renderProfile")   # the hover says the leg's tier


def test_zh_and_en_for_every_tier_and_zone():
    for loc in ("zh-TW", "en"):
        c = cat(loc)
        for t in TIERS:
            assert c[f"group.tier.{t}"] and c[f"group.zone.{t}"] and c[f"group.zone_down.{t}"] and c[f"group.cut.{t}"]
        assert all(c[f"group.{k}"] for k in ("title", "sub", "tip", "head", "head_behind", "head_nobehind", "pack", "close",
                                              "rest_ratio", "rest_cairn", "speed_only", "behind_by", "legend"))
    en = cat("en")
    assert "AeT" in en["group.zone.right"] and "estimate" in en["group.tip"]
