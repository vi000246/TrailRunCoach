"""
方法 (SP-973): one card per calculation model — what it works out, where the app uses it, the formula, the
sources, whether it is calibrated with the athlete's own records and what in it is an estimate. Public (no
sign-in), Chinese and English; the way in is 關於's 「計算方法與出處」 (SP-1067). The 介紹頁 does not send
its reader here: its 「為什麼不一樣」 cards, waterfall layers and 賽事周期 open the card's words in a dialog
(popup(), SP-1067); their hrefs (/methods#<id>) stay for a modified click and a page without its script.

  GET /methods                   the page, in the request's language (?lang=, the lang cookie,
                                 Accept-Language)

What the page says lives in two places, like 關於 (api/about.py):
  * the words: static/i18n/<locale>/methods.json — per model m.<id>.name / short (the card's face: one or
    two sentences, SP-973: the owner found the pages too wordy) / plain / where / calib / est (only when
    MODELS says est) / f<n>.k + f<n>.v (the formula lines) / nosrc (only a model without literature);
  * the facts that do not change with the language: MODELS (group, sources, calibration, estimate, the
    waterfall layer it explains). Sources are about.SOURCES ids — one list for 方法 and the 介紹頁's count
    (SP-973 Q1-2 (a)). The page's last section lists all of it (#all-sources), with the authors' credit and
    the SuperPower Calculator port's (all.lead / all.credit; SP-1067: 關於 listed them before).

Every number on the cards is the repo's own (engine code, docs/spec/racepower.spec.md, docs/research);
the card says 推估 where the code does. Layout (the owner's pick, SP-973 Q1 = B): a filterable wall of
cards, the open card's detail beside it (on a phone: under the card) — static/methods.html.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter

from backend import i18n
from backend.api import about as AB
from backend.i18n.pages import brand, page_catalog, render_page

URL = "/methods"
NS = "methods"

router = APIRouter(tags=["methods"], include_in_schema=False)
_E = html.escape

GROUPS = ("plan", "read", "race")
CALIB = ("yes", "part", "no")


@dataclass(frozen=True)
class Model:
    id: str                         # the card's anchor (/methods#<id>) and its catalog keys m.<id>.*
    group: str                      # GROUPS: 排課表 / 讀訓練 / 賽事計算機
    sources: tuple[str, ...]        # about.SOURCES ids; () only with an m.<id>.nosrc text (and est)
    calib: str                      # CALIB: calibrated with the athlete's records / partly / not
    est: bool                       # something in it is 推估 (m.<id>.est says what)
    rules: bool = False             # the formula lines are rules and lengths, not equations
    layer: Optional[str] = None     # the 介紹頁 waterfall layer it explains (api/intro.BREAKDOWN_KEYS)


MODELS: tuple[Model, ...] = (
    # 排課表 — engine/planning.py (docstring, TAPER_DAYS, SPECIFIC_WEEKS, REC_BY_SIZE, 下坡升級),
    # docs/research/coach-schools-zones-periodization.md, engine/hr_profile.py, engine/load_guard.py
    Model("cycle", "plan", ("ua_ultra", "pfitzinger", "canova", "daniels", "jiang", "bosquet", "wang_taper"),
          "no", True, rules=True),
    Model("recovery", "plan", ("johnston_rec", "torrence", "millet_ultra", "bontemps"), "part", True, rules=True),
    Model("zones", "plan", ("friel_zones", "palladino_zones", "daniels", "uphill"), "part", True, rules=True),
    Model("load", "plan", ("allen_coggan", "banister"), "no", False),
    Model("ramp", "plan", ("friel_ramp",), "no", True, rules=True),
    # SP-1072: engine/rpe_load.py (LEVELS, DEFAULT_IF, the per-level fit), workout_steps.RPE_FRAC
    Model("rpe", "plan", ("borg", "foster", "allen_coggan"), "yes", True),
    # 讀訓練 — engine/aet_test.py, engine/heat_calib.py, heat.py
    Model("cp", "read", ("monod", "puchowicz"), "yes", False),
    Model("aet", "read", ("uphill",), "part", True, rules=True),
    Model("heat-hr", "read", ("superpower", "racinais"), "yes", True),
    Model("hrmax", "read", ("tanaka",), "no", True),
    # 賽事計算機 — racepower/trailhr.py, terrain_calib.py, terrain_difficulty.py, env.py, grade_model.py,
    # hike.py, riegel.py; docs/spec/racepower.spec.md「Models」「Trail HR pace model」
    Model("trail-hr", "race", ("fornasiero", "kerherve"), "yes", True, layer="flat"),
    Model("effort", "race", ("itra",), "yes", True, layer="climb"),
    Model("terrain", "race", (), "yes", True, layer="terrain"),
    Model("durability", "race", ("clark", "bossi", "markovic"), "part", True, layer="durability"),
    Model("heat", "race", ("superpower", "racinais"), "part", True, layer="heat"),
    Model("altitude", "race", ("peronnet", "wehrlin", "bassett", "superpower"), "part", True, layer="altitude"),
    Model("grade", "race", ("minetti",), "yes", True),
    Model("hike", "race", ("tobler",), "yes", True),
    Model("pack", "race", ("pandolf",), "part", True),
    Model("riegel", "race", ("riegel", "superpower"), "part", True),
)
BY_ID = {m.id: m for m in MODELS}
# the 介紹頁 waterfall layer → the card that explains it (intro.html links /methods#<id>)
LAYER_CARD = {m.layer: m.id for m in MODELS if m.layer}
# the 介紹頁's 賽事周期 links here (SP-975)
CYCLE_CARD = "cycle"
# hashes that set a filter instead of opening a card (the 介紹頁's 「自動校正」 card links #calibrated)
FILTERS = ("all",) + GROUPS + ("calibrated", "estimates")
# SP-1067: the page's last section, every source (about.SOURCES; 關於 no longer lists them)
ALL_SOURCES = "all-sources"


# ---------------------------------------------------------------------------- the page

def _t(cat: dict, key: str, **kw) -> str:
    v = cat.get(f"{NS}.{key}", key)
    v = v.get("other", "") if isinstance(v, dict) else v
    return v.format(**kw) if kw else v


def formula_lines(cat: dict, mid: str) -> list[tuple[str, str]]:
    """[(label, text)] of m.<id>.f1…, in order, while both halves are there."""
    out, i = [], 1
    while f"{NS}.m.{mid}.f{i}.k" in cat and f"{NS}.m.{mid}.f{i}.v" in cat:
        out.append((_t(cat, f"m.{mid}.f{i}.k"), _t(cat, f"m.{mid}.f{i}.v")))
        i += 1
    return out


def _chips(cat: dict, m: Model, layer: bool) -> str:
    c = [f'<span class="md-chip cal-{m.calib}">{_E(_t(cat, f"chip.cal.{m.calib}"))}</span>']
    if m.est:
        c.append(f'<span class="md-chip est">{_E(_t(cat, "chip.est"))}</span>')
    if layer and m.layer:
        c.append(f'<span class="md-chip layer">{_E(_t(cat, f"layer.{m.layer}"))}</span>')
    if layer and m.id == CYCLE_CARD:
        c.append(f'<span class="md-chip layer">{_E(_t(cat, "layer.cycle"))}</span>')
    return '<span class="md-chips">' + "".join(c) + "</span>"


def _card(cat: dict, m: Model) -> str:
    return (f'<a class="md-card" href="#{m.id}" data-id="{m.id}" data-g="{m.group}" data-cal="{m.calib}" '
            f'data-est="{int(m.est)}" aria-controls="{m.id}" aria-expanded="false">'
            f'<span class="md-g">{_E(_t(cat, f"g.{m.group}"))}</span>'
            f'<span class="md-nm">{_E(_t(cat, f"m.{m.id}.name"))}</span>'
            f'<span class="md-short">{_E(_t(cat, f"m.{m.id}.short"))}</span>'
            f'{_chips(cat, m, False)}<span class="md-more">{_E(_t(cat, "more"))} →</span></a>')


def _source(cat: dict, sid: str) -> str:
    s = AB.SOURCES[sid]
    link = (f' <a href="{_E(s.url)}" target="_blank" rel="noopener">{_E(_t(cat, "src.link"))}</a>' if s.url else "")
    return f"<li>{_E(s.cite, quote=False)}{link}</li>"


def _body(cat: dict, m: Model, layer: bool = True) -> str:
    """A card's detail under its heading: the chips, the plain words, where / calibration / 推估, the formula and
    the sources — the page's pane and the 介紹頁's dialog (popup()) both show it; `layer` False (the dialog, the
    owner 2026-10-30: the reader is on the 介紹頁 already): without the chip naming its 首頁 waterfall layer /
    賽事周期."""
    meta = (f'<dt>{_E(_t(cat, "lbl.where"))}</dt><dd>{_E(_t(cat, f"m.{m.id}.where"))}</dd>'
            f'<dt>{_E(_t(cat, "lbl.calib"))}</dt><dd>{_E(_t(cat, f"m.{m.id}.calib"))}</dd>'
            + (f'<dt>{_E(_t(cat, "lbl.est"))}</dt><dd>{_E(_t(cat, f"m.{m.id}.est"))}</dd>' if m.est else ""))
    lines = formula_lines(cat, m.id)
    fx = ""
    if lines:
        fx = (f'<details class="md-fx{" rules" if m.rules else ""}" open><summary>'
              f'{_E(_t(cat, "lbl.rules" if m.rules else "lbl.formula"))}</summary><dl>'
              + "".join(f"<dt>{_E(k)}</dt><dd>{_E(v)}</dd>" for k, v in lines) + "</dl></details>")
    if m.sources:
        src = (f'<div class="md-src"><h3>{_E(_t(cat, "lbl.src"))}</h3><ol>'
               + "".join(_source(cat, s) for s in m.sources) + "</ol></div>")
    else:
        src = (f'<div class="md-src"><h3>{_E(_t(cat, "lbl.nosrc"))}</h3>'
               f'<p>{_E(_t(cat, f"m.{m.id}.nosrc"))}</p></div>')
    return (f'{_chips(cat, m, layer)}<p class="md-plain">{_E(_t(cat, f"m.{m.id}.plain"))}</p>'
            f'<dl class="md-meta">{meta}</dl>{fx}{src}')


def _detail(cat: dict, m: Model) -> str:
    return (f'<section class="md-d" id="{m.id}" data-g="{m.group}" aria-labelledby="{m.id}-h" tabindex="-1">'
            f'<p class="md-k">{_E(_t(cat, f"g.{m.group}"))}</p>'
            f'<div class="md-dh"><h2 id="{m.id}-h">{_E(_t(cat, f"m.{m.id}.name"))}</h2>'
            f'<button type="button" class="md-x" aria-label="{_E(_t(cat, "close"))}">×</button></div>'
            f'{_body(cat, m)}</section>')


def _fill(loc: str) -> dict:
    cat = page_catalog((NS,), loc)
    n = {g: sum(1 for m in MODELS if m.group == g) for g in GROUPS}
    counts = {"all": len(MODELS), **n, "calibrated": sum(1 for m in MODELS if m.calib != "no"),
              "estimates": sum(1 for m in MODELS if m.est)}
    filters = (f'<div class="md-filters" role="group" aria-label="{_E(_t(cat, "filter.label"))}">'
               + "".join(f'<button type="button" data-f="{f}" aria-pressed="{"true" if f == "all" else "false"}">'
                         f'{_E(_t(cat, f"filter.{f}"))}<span class="n">{counts[f]}</span></button>'
                         for f in FILTERS) + "</div>")
    main = (
        f'<header class="md-hero"><h1>{_E(_t(cat, "h1"))}</h1><p>{_E(_t(cat, "lead"))}</p>'
        f'<p class="md-count"><a href="#{ALL_SOURCES}">'
        f'{_E(_t(cat, "count", n=len(AB.SOURCES), m=len(MODELS)))} →</a></p></header>'
        + filters
        + '<div class="md-split"><div class="md-wall" id="md-wall">'
        + "".join(_card(cat, m) for m in MODELS)
        + f'<p class="md-empty" hidden>{_E(_t(cat, "empty"))}</p></div>'
        + f'<div class="md-pane" id="md-pane"><p class="md-hint">{_E(_t(cat, "hint"))}</p>'
        + "".join(_detail(cat, m) for m in MODELS)
        + f'<p class="md-about"><a href="/about#data">{_E(_t(cat, "about.all"))} →</a></p></div></div>'
        # SP-1067: every source, the authors' credit and the SuperPower port's — 關於 no longer lists them
        + f'<section class="md-all" id="{ALL_SOURCES}" aria-labelledby="{ALL_SOURCES}-h">'
          f'<h2 id="{ALL_SOURCES}-h">{_E(_t(cat, "all.h"))}</h2><p>{_E(_t(cat, "all.lead"))}</p>'
          f'<p>{_E(_t(cat, "all.credit"))}</p><ol>{"".join(_source(cat, s) for s in AB.SOURCES)}</ol></section>')
    return {"title": _E(f'{_t(cat, "title")} · {brand(loc)}', quote=False), "main": main}


def popup(loc: str) -> dict:
    """SP-1067: what the 介紹頁's dialog shows instead of sending the reader here (api/intro, GET
    /api/v1/intro/methods): the groups' names; per card its group, name, short face, calibration (CALIB, the
    chips, how) and the detail (_body: no ids, the dialog lives in the 介紹頁's document); the whole source
    list (about.SOURCES) and the SuperPower port's credit for 「看全部出處」."""
    cat = page_catalog((NS,), loc)
    return {
        "groups": {g: _t(cat, f"g.{g}") for g in GROUPS},
        "models": [{"id": m.id, "group": m.group, "name": _t(cat, f"m.{m.id}.name"),
                    "short": _t(cat, f"m.{m.id}.short"), "calib": m.calib, "chips": _chips(cat, m, False),
                    "calib_text": _t(cat, f"m.{m.id}.calib"), "html": _body(cat, m, layer=False)} for m in MODELS],
        "sources": "".join(_source(cat, s) for s in AB.SOURCES),
        "credit": _t(cat, "all.credit"),
    }


@router.get(URL)
def methods_page():
    # private like 關於: the page inlines the session (a signed-in user's name)
    return render_page("methods", headers={"Cache-Control": "private, no-cache"}, fill=_fill(i18n.current_locale()))
