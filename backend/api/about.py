"""
關於 (SP-495): the version, the third-party data and software with their licences, and the trademark
notice. Public (no sign-in), Chinese and English, also in the demo. Where the calculations come from is
the 方法 page (api/methods.py): the contents' 「計算方法與出處」 goes there (SP-1067: the section that listed
the methods here is gone, the owner's call 2026-10-30).

  GET /about                     the page, in the request's language (?lang=, the lang cookie,
                                 Accept-Language)

What the page says lives in two places:
  * the words: static/i18n/<locale>/about.json (headings, each data source's description, the trademark
    notice);
  * the facts that do not change with the language: DATA (each data source's links), SOFTWARE (name,
    licence, home page) — and SOURCES, the one list of the literature (the citation and, where there is
    one, a public link; citations in their original English form), which the 方法 page and the 介紹頁
    show and count.

Only methods the program really uses, each with public literature or a public method; no page names
the products whose screens it replaces (SP-498). Links are external addresses only, except the 方法 page
(/methods) and, in sign-in mode, the last section listing the terms of service, the privacy policy and
the other documents agreed to at sign-up (/legal, SP-847). Version: backend.__version__ and the commit of the
image (engine/debug_view.git_sha: TRC_GIT_SHA, else `git rev-parse`).
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter

from backend import __version__, i18n
from backend.i18n.pages import brand, page_catalog, render_page

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
    # SP-975: the 賽事周期 (engine/planning.py: TAPER_DAYS, SPECIFIC_WEEKS, REC_BY_SIZE, 下坡升級)
    "bosquet": Source("Bosquet L, Montpetit J, Arvisais D, Mujika I. Effects of tapering on performance: a meta-analysis. "
                      "Medicine & Science in Sports & Exercise 2007;39(8):1358–1365.",
                      "https://doi.org/10.1249/mss.0b013e31806010e0"),
    "wang_taper": Source("Wang Z, et al. Effects of tapering on performance in endurance athletes: a systematic review "
                         "and meta-analysis. PLOS ONE 2023.", "https://doi.org/10.1371/journal.pone.0282838"),
    "ua_ultra": Source("Uphill Athlete. Intro to Ultra Marathons: 20-week training plan (accessed 2026).",
                       "https://uphillathlete.com/training-plans/20-week-intro-ultra-running-training-plan/"),
    "pfitzinger": Source("Pfitzinger P, Douglas S. Advanced Marathoning, 3rd ed. Champaign, IL: Human Kinetics; 2019."),
    "canova": Source("Running Writings. Review and summary of Marathon Training – A Scientific Approach by Renato "
                     "Canova. 2023.", "https://runningwritings.com/2023/06/canova-marathon-book.html"),
    "jiang": Source("Chiang YC. A periodization guide for trail running (in Chinese). Garmin Taiwan blog; 2020.",
                    "https://www.garmin.com/zh-TW/blog/running/trail-run/"),
    "johnston_rec": Source("Johnston S. Optimizing ultra race recovery (interview). TrainingPeaks Coach Blog; 2024.",
                           "https://www.trainingpeaks.com/coach-blog/optimizing-ultra-race-recovery-scott-johnston/"),
    "torrence": Source("Torrence I. Quick turnarounds: recover and train during a full race season. iRunFar; 2019.",
                       "https://www.irunfar.com/quick-turnarounds-recover-and-train-during-a-full-race-season"),
    "millet_ultra": Source("Millet GY, et al. Neuromuscular consequences of an extreme mountain ultra-marathon. "
                           "PLoS One 2011.", "https://pmc.ncbi.nlm.nih.gov/articles/PMC3043077/"),
    "bontemps": Source("Bontemps B, et al. Downhill running: what are the effects and how can we adapt? A narrative "
                       "review. Sports Medicine 2020.", "https://pmc.ncbi.nlm.nih.gov/articles/PMC7674385/"),
    # SP-973 (Q1-2 (a)): what the 方法 page (api/methods.py) cites, on the one list 方法 and the 介紹頁's
    # count share. Citations as the research docs give them: docs/research/unsourced-rules.md (Clark,
    # Fornasiero), long-race-durability-shape.md [L1] [P2], public-datasets.md (Kerhervé 2015),
    # effort-distance-formulas.md §1 (UTMB), paywalled-sources.md (Péronnet, Bassett), baiyue-from-running.md
    # (Wehrlin); author lists cut to "et al." where the doc does.
    "fornasiero": Source("Fornasiero A, Savoldelli A, Fruet D, Boccia G, Pellegrini B, Schena F. Physiological "
                         "intensity profile, exercise load and performance predictors of a 65-km mountain "
                         "ultra-marathon. Journal of Sports Sciences 2018;36:1287–1295.",
                         "https://doi.org/10.1080/02640414.2017.1374707"),
    "kerherve": Source("Kerhervé H, et al. The dynamics of speed selection and psycho-physiological load during a "
                       "mountain ultramarathon. PLOS ONE 2015;10:e0145482.",
                       "https://doi.org/10.1371/journal.pone.0145482"),
    "clark": Source("Clark IE, et al. Dynamics of the power-duration relationship during prolonged endurance "
                    "exercise and influence of carbohydrate ingestion. Journal of Applied Physiology "
                    "2019;127:726–736.", "https://doi.org/10.1152/japplphysiol.00207.2019"),
    "bossi": Source("Bossi AH, Matta GG, Millet GY, Lima P, Pertence LC, de Lima JP, Hopker JG. Pacing strategy "
                    "during 24-hour ultramarathon-distance running. International Journal of Sports Physiology "
                    "and Performance 2017.", "https://kar.kent.ac.uk/id/document/52115"),
    "markovic": Source("Markovic S, et al. Pacing in ultra-marathon running: the Western States 100-mile endurance "
                       "run 2006–2023. Scientific Reports 2025.", "https://doi.org/10.1038/s41598-025-92141-2"),
    "itra": Source("UTMB World Series. Sports system: KM-Effort = distance (km) + elevation gain (m) / 100, the "
                   "ITRA km-effort (accessed 2026).", "https://utmb.world/sports-system"),
    "peronnet": Source("Péronnet F, Thibault G, Cousineau DL. A theoretical analysis of the effect of altitude on "
                       "running performance. Journal of Applied Physiology 1991;70:399–404.",
                       "https://doi.org/10.1152/jappl.1991.70.1.399"),
    "wehrlin": Source("Wehrlin JP, Hallén J. Linear decrease in VO2max and performance with increasing altitude in "
                      "endurance athletes. European Journal of Applied Physiology 2006;96:404–412.",
                      "https://doi.org/10.1007/s00421-005-0081-9"),
    "bassett": Source("Bassett DR, Kyle CR, Passfield L, Broker JP, Burke ER. Comparing cycling world hour records, "
                      "1967–1996: modeling with empirical data. Medicine & Science in Sports & Exercise "
                      "1999;31:1665–1676.", "https://doi.org/10.1097/00005768-199911000-00025"),
}

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


# SP-1067: the contents' 「計算方法與出處」 is the 方法 page itself (api/methods.URL), not a section here
TOC_URL = {"methods": "/methods"}


# the app bar (shell.js); ?embed=1 (the 介紹頁's page dialog) goes without it
SHELL = '<script src="/api/v1/static/shell.js" data-page="about"></script>'


def _section(sid: str, title: str, body: str) -> str:
    return f'<section class="ab-sec" id="{sid}"><h2>{_E(title, quote=False)}</h2>{body}</section>'


def _legal() -> str:
    """SP-847: the documents a person agrees to at sign-up (/legal, accounts/terms.DOCUMENTS) — in
    sign-in mode only: the owner's app and the demo have no /legal (404 there)."""
    from backend.accounts import terms
    return "".join(f'<li><a href="{terms.URL_PREFIX}{d.id}">{_E(i18n._(d.title), quote=False)}</a></li>'
                   for d in terms.DOCUMENTS)


def _fill(loc: str, legal: bool = False, embed: bool = False) -> dict:
    cat = page_catalog((NS,), loc)
    v = version()
    ver = (f'<dl class="ab-ver"><dt>{_E(_t(cat, "version.app"))}</dt><dd><code>{_E(v["version"])}</code></dd>'
           f'<dt>{_E(_t(cat, "version.build"))}</dt><dd><code>{_E(v["build"])}</code></dd></dl>'
           f'<p class="ab-note">{_E(_t(cat, "version.note"))}</p>')
    data = "".join(
        f'<li class="ab-item" id="d-{did}"><h3>{_E(_t(cat, f"d.{did}.name"))}</h3>'
        f'<p>{_E(_t(cat, f"d.{did}.desc"))}</p><p class="ab-links">'
        + " · ".join(_a(_t(cat, "ogdl") if url == OGDL[1] else text, url) for text, url in links) + "</p></li>"
        for did, links in DATA)
    sw = "".join(f"<tr><td>{_a(n, u)}</td><td><code>{_E(lic)}</code></td></tr>" for n, lic, u in SOFTWARE)
    main = (
        f'<header class="ab-hero"><h1>{_E(_t(cat, "h1"))}</h1><p>{_E(_t(cat, "lead"))}</p>'
        f'<nav class="ab-toc" aria-label="{_E(_t(cat, "toc"))}">'
        + "".join(f'<a href="{TOC_URL.get(k, "#" + k)}">{_E(_t(cat, f"{k}.h"))}</a>'
                  for k in ("version", "cairn", "methods", "data", "software", "trademarks")
                  + (("legal",) if legal else ()))
        + "</nav></header>"
        + _section("version", _t(cat, "version.h"), ver)
        # SP-766: our other service, 疊石 Cairn — what it does and how a route goes between the two (words
        # only: the manual's 疊石 Cairn section has the steps)
        + _section("cairn", _t(cat, "cairn.h"),
                   "".join(f"<p>{_E(_t(cat, k))}</p>" for k in ("cairn.what", "cairn.routes", "cairn.link")))
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
    return {"title": _E(f'{_t(cat, "title")} · {brand(loc)}', quote=False), "main": main,
            "shell": "" if embed else SHELL, "embed": ' data-embed="1"' if embed else ""}


@router.get(URL)
def about_page(embed: bool = False):
    # the page inlines the session (a signed-in user's name): private, like every other page
    from backend.accounts import login as LG
    return render_page("about", headers={"Cache-Control": "private, no-cache"},
                       fill=_fill(i18n.current_locale(), legal=LG.required(), embed=embed))
