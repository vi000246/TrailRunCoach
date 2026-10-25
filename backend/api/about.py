"""
關於 (SP-495): the version, where the calculations come from, the third-party data and software with
their licences, and the trademark notice. Public (no sign-in), Chinese and English, also in the demo.

  GET /about                     the page, in the request's language (?lang=, the lang cookie,
                                 Accept-Language)

What the page says lives in two places:
  * the words: static/i18n/<locale>/about.json (headings, each method's name and description, each
    data source's description, the trademark notice);
  * the facts that do not change with the language: METHODS (which sources each method cites),
    SOURCES (the citation and, where there is one, a public link), DATA (each data source's links),
    SOFTWARE (name, licence, home page). Citations stay in their original (English) form.

Only methods the program really uses, each with public literature or a public method; the page
never names the products whose screens it replaces (SP-498) and says the methods belong to their
authors. Links are external addresses only, except in sign-in mode: the last section then lists the
terms of service, the privacy policy and the other documents agreed to at sign-up (/legal, SP-847). Version: backend.__version__ and the commit of the
image (engine/debug_view.git_sha: TRC_GIT_SHA, else `git rev-parse`).
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter

from backend import __version__, i18n
from backend.i18n.pages import page_catalog, render_page

URL = "/about"
NS = "about"

router = APIRouter(tags=["about"], include_in_schema=False)
_E = html.escape


@dataclass(frozen=True)
class Source:
    cite: str                       # the citation, as published
    url: Optional[str] = None       # a public link (DOI, the author's page); None: print only


SOURCES: dict[str, Source] = {
    "allen_coggan": Source("Allen H, Coggan AR, McGregor SL. Training and Racing with a Power Meter, 3rd ed. "
                           "Boulder, CO: VeloPress; 2019."),
    "banister": Source("Banister EW, Calvert TW, Savage MV, Bach T. A systems model of training for athletic "
                       "performance. Australian Journal of Sports Medicine 1975;7:57–61."),
    "friel_ramp": Source("Friel J. The CTL ramp rate.", "https://joefrieltraining.com/the-ctl-ramp-rate/"),
    "monod": Source("Monod H, Scherrer J. The work capacity of a synergic muscular group. Ergonomics "
                    "1965;8(3):329–338.", "https://doi.org/10.1080/00140136508930810"),
    "puchowicz": Source("Puchowicz MJ, Baker J, Clarke DC. Development and field validation of an omni-domain "
                        "power-duration model. Journal of Sports Sciences 2020;38(7):801–813.",
                        "https://doi.org/10.1080/02640414.2020.1735609"),
    "minetti": Source("Minetti AE, Moia C, Roi GS, Susta D, Ferretti G. Energy cost of walking and running at "
                      "extreme uphill and downhill slopes. Journal of Applied Physiology 2002;93(3):1039–1046.",
                      "https://doi.org/10.1152/japplphysiol.01177.2001"),
    "tobler": Source("Tobler W. Three presentations on geographical analysis and modeling. NCGIA Technical "
                     "Report 93-1, 1993."),
    "pandolf": Source("Pandolf KB, Givoni B, Goldman RF. Predicting energy expenditure with loads while standing "
                      "or walking very slowly. Journal of Applied Physiology 1977;43(4):577–581.",
                      "https://doi.org/10.1152/jappl.1977.43.4.577"),
    "riegel": Source("Riegel PS. Athletic records and human endurance. American Scientist 1981;69(3):285–290."),
    "superpower": Source("Palladino S. SuperPower Calculator (spreadsheet, revision 4.2)."),
    "palladino_zones": Source(
        "Palladino S. Running Power Zones 2017 (Palladino Power Project).",
        "https://docs.google.com/document/d/e/2PACX-1vTHqzlWwp2Dp6f1cMlS45PycEf-hCAjy61KXG7fRoR2e4mxDyWH6gXo5ZnIvj5b9cTBWBcj9kcfJHel/pub"),
    "friel_zones": Source("Friel J. A quick guide to setting zones.",
                          "https://joefrieltraining.com/a-quick-guide-to-setting-zones/"),
    "tanaka": Source("Tanaka H, Monahan KD, Seals DR. Age-predicted maximal heart rate revisited. Journal of the "
                     "American College of Cardiology 2001;37(1):153–156.",
                     "https://doi.org/10.1016/S0735-1097(00)01054-8"),
    "foster": Source("Foster C, Florhaug JA, Franklin J, et al. A new approach to monitoring exercise training. "
                     "Journal of Strength and Conditioning Research 2001;15(1):109–115.",
                     "https://doi.org/10.1519/1533-4287(2001)015%3C0109:ANATME%3E2.0.CO;2"),
    "borg": Source("Borg GA. Psychophysical bases of perceived exertion. Medicine & Science in Sports & Exercise "
                   "1982;14(5):377–381.", "https://doi.org/10.1249/00005768-198205000-00012"),
    "uphill": Source("Johnston S, House S. Training for the Uphill Athlete. Ventura, CA: Patagonia; 2019. "
                     "Heart rate drift test: uphillathlete.com.",
                     "https://uphillathlete.com/aerobic-training/heart-rate-drift/"),
    "daniels": Source("Daniels J. Daniels' Running Formula. Champaign, IL: Human Kinetics."),
    "racinais": Source("Racinais S, Alonso JM, Coutts AJ, et al. Consensus recommendations on training and "
                       "competing in the heat. British Journal of Sports Medicine 2015;49(18):1164–1173.",
                       "https://doi.org/10.1136/bjsports-2015-094915"),
}

# (id, the sources it cites): the words are about.json "m.<id>.name" / "m.<id>.desc"
METHODS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("weighted", ("allen_coggan",)),
    ("load", ("banister", "allen_coggan")),
    ("ramp", ("friel_ramp",)),
    ("cp", ("monod",)),
    ("pd", ("monod", "puchowicz")),
    ("grade", ("minetti",)),
    ("hike", ("tobler",)),
    ("pack", ("pandolf",)),
    ("riegel", ("riegel",)),
    ("racecalc", ("superpower",)),
    ("pzones", ("palladino_zones",)),
    ("hrzones", ("friel_zones",)),
    ("hrmax", ("tanaka",)),
    ("rpe", ("borg", "foster")),
    ("aet", ("uphill",)),
    ("reentry", ("daniels",)),
    ("heat", ("racinais",)),
)

# (id, [(link text, address)]): the words are about.json "d.<id>.name" / "d.<id>.desc"
OGDL = ("OGDL-Taiwan-1.0", "https://data.gov.tw/license")     # link text: about.json "ogdl"
DATA: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("osm", (("© OpenStreetMap contributors", "https://www.openstreetmap.org/copyright"),
             ("ODbL 1.0", "https://opendatacommons.org/licenses/odbl/1-0/"))),
    ("nlsc", (("maps.nlsc.gov.tw", "https://maps.nlsc.gov.tw/"), OGDL)),
    ("rudy", (("rudy.basecamp.tw", "https://rudy.basecamp.tw/"),)),
    # SP-946 (SP-844 Q1 option A): data/baiyue.json — happyman's points, the 百岳 list by the 山岳協會
    ("baiyue", (("twmap.happyman.idv.tw", "https://twmap.happyman.idv.tw/"),)),
    ("google", (("Google Maps", "https://www.google.com/maps"),)),
    ("cwa", (("opendata.cwa.gov.tw", "https://opendata.cwa.gov.tw/"), OGDL)),
    # SP-797: the forecast CWA does not give; terms ask for 「Data from MET Norway」 and the licence
    ("metno", (("api.met.no", "https://api.met.no/"),
               ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"))),
    ("openmeteo", (("open-meteo.com", "https://open-meteo.com/"),
                   ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"))),
)

# what runs in the browser, then on the server (requirements.txt); licences from each package's metadata
SOFTWARE: tuple[tuple[str, str, str], ...] = (
    ("Leaflet", "BSD-2-Clause", "https://leafletjs.com/"),
    ("Apache ECharts", "Apache-2.0", "https://echarts.apache.org/"),
    ("FastAPI", "MIT", "https://fastapi.tiangolo.com/"),
    ("Uvicorn", "BSD-3-Clause", "https://www.uvicorn.org/"),
    ("Starlette / sse-starlette", "BSD-3-Clause", "https://www.starlette.io/"),
    ("Pydantic", "MIT", "https://docs.pydantic.dev/"),
    ("SQLAlchemy", "MIT", "https://www.sqlalchemy.org/"),
    ("Alembic", "MIT", "https://alembic.sqlalchemy.org/"),
    ("aiosqlite", "MIT", "https://github.com/omnilib/aiosqlite"),
    ("HTTPX", "BSD-3-Clause", "https://www.python-httpx.org/"),
    ("NumPy", "BSD-3-Clause", "https://numpy.org/"),
    ("fitparse", "MIT", "https://github.com/dtcooper/python-fitparse"),
    ("fitdecode", "MIT", "https://github.com/polyvertex/fitdecode"),
    ("cryptography", "Apache-2.0 OR BSD-3-Clause", "https://cryptography.io/"),
    ("Authlib", "BSD-3-Clause", "https://authlib.org/"),
    ("python-garminconnect", "MIT", "https://github.com/cyberjunky/python-garminconnect"),
    ("python-multipart", "Apache-2.0", "https://github.com/Kludex/python-multipart"),
)


def version() -> dict:
    from backend.engine import debug_view as DV
    return {"version": __version__, "build": DV.git_sha()}


# ---------------------------------------------------------------------------- the page

def _t(cat: dict, key: str) -> str:
    v = cat.get(f"{NS}.{key}", key)
    return v.get("other", "") if isinstance(v, dict) else v


def _a(text: str, url: str) -> str:
    return f'<a href="{_E(url)}" target="_blank" rel="noopener">{_E(text, quote=False)}</a>'


def _cite(sid: str) -> str:
    s = SOURCES[sid]
    return f'<li class="ab-cite">{_E(s.cite, quote=False)}' + (f" {_a(s.url, s.url)}" if s.url else "") + "</li>"


def _section(sid: str, title: str, body: str) -> str:
    return f'<section class="ab-sec" id="{sid}"><h2>{_E(title, quote=False)}</h2>{body}</section>'


def _legal() -> str:
    """SP-847: the documents a person agrees to at sign-up (/legal, accounts/terms.DOCUMENTS) — in
    sign-in mode only: the owner's app and the demo have no /legal (404 there)."""
    from backend.accounts import terms
    return "".join(f'<li><a href="{terms.URL_PREFIX}{d.id}">{_E(i18n._(d.title), quote=False)}</a></li>'
                   for d in terms.DOCUMENTS)


def _fill(loc: str, legal: bool = False) -> dict:
    cat = page_catalog((NS,), loc)
    v = version()
    ver = (f'<dl class="ab-ver"><dt>{_E(_t(cat, "version.app"))}</dt><dd><code>{_E(v["version"])}</code></dd>'
           f'<dt>{_E(_t(cat, "version.build"))}</dt><dd><code>{_E(v["build"])}</code></dd></dl>'
           f'<p class="ab-note">{_E(_t(cat, "version.note"))}</p>')
    methods = "".join(
        f'<li class="ab-item" id="m-{mid}"><h3>{_E(_t(cat, f"m.{mid}.name"))}</h3>'
        f'<p>{_E(_t(cat, f"m.{mid}.desc"))}</p><ul class="ab-cites">{"".join(_cite(s) for s in srcs)}</ul></li>'
        for mid, srcs in METHODS)
    data = "".join(
        f'<li class="ab-item" id="d-{did}"><h3>{_E(_t(cat, f"d.{did}.name"))}</h3>'
        f'<p>{_E(_t(cat, f"d.{did}.desc"))}</p><p class="ab-links">'
        + " · ".join(_a(_t(cat, "ogdl") if url == OGDL[1] else text, url) for text, url in links) + "</p></li>"
        for did, links in DATA)
    sw = "".join(f"<tr><td>{_a(n, u)}</td><td><code>{_E(lic)}</code></td></tr>" for n, lic, u in SOFTWARE)
    main = (
        f'<header class="ab-hero"><h1>{_E(_t(cat, "h1"))}</h1><p>{_E(_t(cat, "lead"))}</p>'
        f'<nav class="ab-toc" aria-label="{_E(_t(cat, "toc"))}">'
        + "".join(f'<a href="#{k}">{_E(_t(cat, f"{k}.h"))}</a>' for k in ("version", "cairn", "methods", "data",
                                                                           "software", "trademarks")
                  + (("legal",) if legal else ()))
        + "</nav></header>"
        + _section("version", _t(cat, "version.h"), ver)
        # SP-766: our other service, 疊石 Cairn — what it does and how a route goes between the two (words
        # only: the manual's 疊石 Cairn section has the steps)
        + _section("cairn", _t(cat, "cairn.h"),
                   "".join(f"<p>{_E(_t(cat, k))}</p>" for k in ("cairn.what", "cairn.routes", "cairn.link")))
        + _section("methods", _t(cat, "methods.h"),
                   f'<p class="ab-lead">{_E(_t(cat, "methods.lead"))}</p><ol class="ab-list">{methods}</ol>')
        + _section("data", _t(cat, "data.h"),
                   f'<p class="ab-lead">{_E(_t(cat, "data.lead"))}</p><ul class="ab-list">{data}</ul>')
        + _section("software", _t(cat, "software.h"),
                   f'<p class="ab-lead">{_E(_t(cat, "software.lead"))}</p><table class="ab-sw"><thead><tr>'
                   f'<th>{_E(_t(cat, "software.name"))}</th><th>{_E(_t(cat, "software.license"))}</th></tr></thead>'
                   f"<tbody>{sw}</tbody></table>")
        + _section("trademarks", _t(cat, "trademarks.h"),
                   f'<p>{_E(_t(cat, "trademarks.registered"))}</p><p>{_E(_t(cat, "trademarks.others"))}</p>'
                   f'<p>{_E(_t(cat, "trademarks.own"))}</p>')
        + (_section("legal", _t(cat, "legal.h"),
                    f'<p class="ab-lead">{_E(_t(cat, "legal.lead"))}</p><ul class="ab-legal">{_legal()}</ul>')
           if legal else ""))
    return {"title": _E(f'{_t(cat, "title")} · TrailRunCoach', quote=False), "main": main}


@router.get(URL)
def about_page():
    # the page inlines the session (a signed-in user's name): private, like every other page
    from backend.accounts import login as LG
    return render_page("about", headers={"Cache-Control": "private, no-cache"},
                       fill=_fill(i18n.current_locale(), legal=LG.required()))
