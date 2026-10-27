"""
操作手冊 (SP-558, SP-505): the user manual, public (no sign-in), Chinese and English.

The text is Markdown in backend/manual/<locale>/<chapter>.md — next to the code it describes, changed
in the same commit — and the pictures in backend/manual/img/ (taken by backend/scripts/manual_shots.py
on the synthetic demo athlete). Like the legal documents (backend/legal/*.md) it ships inside backend/,
so every image has it; docs/ is not in the image.

  GET /manual                    the contents: every chapter and its sections, the search box
  GET /manual/<chapter>          one chapter (#<section> jumps to a section; #<section>--deep opens its
                                 「深入」 part)
  GET /manual/index.json         the chunks the search reads (and, later, SP-306's AI chat): one per
                                 section part — {url, chapter, title, part: simple | deep, text}
  GET /manual/terms.json        the glossary's words as the screens write them, each with its section
                                 (SP-559: a page's ? help that names one links to it — shell.js ManualLink)
  GET /manual/img/<file>         a picture

The page is in the request's language (?lang=, the lang cookie, Accept-Language); a chapter's anchors
are the same in both languages, so a link from a page's ? help (data-manual="schedule#colors") works in
either. Public paths: accounts/login.PUBLIC_PREFIXES; offline: static/offline_core.js (MANUAL).

A chapter file:

    ---
    id: schedule                          # the URL: /manual/schedule (same in every language)
    order: 2                              # its place in the contents
    title: 課表
    summary: 課卡怎麼讀、改課與重排、推到手錶、行事曆訂閱
    icon: schedule                        # one of the shell's page icons (static/manual.js)
    deep_sub: 細節                         # optional: the 「深入」 fold's label in this chapter (SP-855: the
                                          #   FAQ's are not formulas), else manual.json's deep_sub
    ---
    Intro paragraph (optional).

    ## 課卡怎麼讀 {#read-card}             # a section: its anchor is required, [a-z0-9-]
    The 「簡單說」 part: plain words, a picture, how to do it.
    ![alt](../img/schedule-week.zh-TW.webp "caption")
    :::tip
    A tip box.
    :::
    :::deep
    The 「深入」 part (folded): formulas, models, sources.
    ### a sub-heading
    :::

Blocks: paragraphs, - / 1. lists, | tables |, > notes, ![images](…), :::tip / :::warn boxes, live parts
drawn in the browser — :::chart <name> [key=value …] (CHARTS: pmc with the app's own static/pmc_chart.js,
the glossary's others with static/manual_charts.js) and :::legend (the 課表 cards' legend,
static/sesschip.js) — and :::terms (SP-559: the words a glossary section stands for, as the screens write
them, one per line; shown as a line 「畫面上的寫法」, and /manual/terms.json gives them to the pages' ?
help so a popup that names one links here). Inline: **bold**, *italic*, `code`,
[links](…) — `other.md#anchor` becomes /manual/other#anchor. Everything is escaped first: nothing in
the text becomes markup of its own.
"""
from __future__ import annotations

import html
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from backend import i18n
from backend.i18n.pages import brand, page_catalog, render_page

ROOT = Path(__file__).resolve().parents[1] / "manual"
IMG_DIR = ROOT / "img"
LOCALES = ("zh-TW", "en")
URL = "/manual"
IMG_URL = URL + "/img/"
# :::chart <name> [key=value …] the page knows how to draw: pmc (static/pmc_chart.js, manual.js), the rest
# static/manual_charts.js (SP-559's glossary)
CHARTS = {"pmc", "zones", "cp", "wbal", "power", "drift", "grade", "rpe"}
_ARG = re.compile(r"^[a-z][a-z0-9_]*=[A-Za-z0-9_.-]+$")
IMG_TYPES = {".webp": "image/webp", ".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg"}

_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_H2 = re.compile(r"^##\s+(.+?)\s*\{#([a-z0-9][a-z0-9-]*)\}\s*$")
_H3 = re.compile(r"^###\s+(.+?)\s*$")
_IMG = re.compile(r'^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)$')
_OL = re.compile(r"^\d+\.\s+")

router = APIRouter(tags=["manual"], include_in_schema=False)


class ManualError(ValueError):
    """A chapter file the parser refuses (the tests read every chapter, so this never reaches a page)."""


# ---------------------------------------------------------------------------- inline marks

_E = html.escape


def _href(url: str) -> str:
    """A link's target: other.md#x → /manual/other#x, #x stays, /path and https:// stay; else nothing."""
    m = re.fullmatch(r"([a-z0-9-]+)\.md(#[a-z0-9-]+)?", url)
    if m:
        return f"{URL}/{m.group(1)}{m.group(2) or ''}"
    if url.startswith(("#", "/", "https://", "http://")) and not url.startswith("//"):
        return url
    return ""


def inline(text: str) -> str:
    """**bold**, *italic*, `code`, [text](url) — on escaped text."""
    out = _E(text, quote=False)
    codes: list[str] = []

    def keep(m):
        codes.append(f"<code>{m.group(1)}</code>")
        return f"\x00{len(codes) - 1}\x00"
    out = re.sub(r"`([^`]+)`", keep, out)

    def link(m):
        h = _href(html.unescape(m.group(2)))
        if not h:
            return m.group(1)
        ext = h.startswith("http")
        return (f'<a href="{_E(h)}"' + (' target="_blank" rel="noopener"' if ext else "") + f">{m.group(1)}</a>")
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", out)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], out)


def plain(text: str) -> str:
    """The words of an inline-marked line, for the search and the AI chunks."""
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    t = re.sub(r"[*`]", "", t)
    return t.strip()


# ---------------------------------------------------------------------------- blocks

def _img_src(src: str) -> str:
    name = src.rsplit("/", 1)[-1]
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or Path(name).suffix.lower() not in IMG_TYPES:
        raise ManualError(f"bad image name {src!r}")
    return name


def img_size(name: str) -> Optional[tuple[int, int]]:
    """(width, height) of a WebP / PNG picture from its header (the page reserves its space, so a jump to
    a section lands where it should while the pictures above it load); None when unknown."""
    try:
        with open(IMG_DIR / name, "rb") as f:
            h = f.read(32)
    except OSError:
        return None
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        kind = h[12:16]
        if kind == b"VP8X":
            return 1 + int.from_bytes(h[24:27], "little"), 1 + int.from_bytes(h[27:30], "little")
        if kind == b"VP8 ":
            return int.from_bytes(h[26:28], "little") & 0x3FFF, int.from_bytes(h[28:30], "little") & 0x3FFF
        if kind == b"VP8L":
            b = int.from_bytes(h[21:25], "little")
            return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
    if h[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(h[16:20], "big"), int.from_bytes(h[20:24], "big")
    return None


def blocks_html(lines: list[str], images: list[str], words: list[str], terms: Optional[list[str]] = None,
                cat: Optional[dict] = None) -> str:
    """Markdown lines (no ## headings) as HTML. The image file names go to `images`, the plain text
    (paragraphs, list items, table cells, image captions) to `words`, a :::terms block's words to `terms`."""
    out: list[str] = []
    para: list[str] = []
    lst: Optional[str] = None
    quote: list[str] = []
    table: list[list[str]] = []

    def flush() -> None:
        nonlocal lst
        if para:
            out.append("<p>" + " ".join(inline(x) for x in para) + "</p>")
            words.append(" ".join(plain(x) for x in para))
            para.clear()
        if lst:
            out.append(f"</{lst}>")
            lst = None
        if quote:
            out.append('<aside class="m-note">' + "<br>".join(inline(x) for x in quote) + "</aside>")
            words.append(" ".join(plain(x) for x in quote))
            quote.clear()
        if table:
            head, body = table[0], [r for r in table[1:] if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]
            out.append('<div class="m-tw"><table><thead><tr>' + "".join(f"<th>{inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body)
                       + "</tbody></table></div>")
            words.append(" ".join(plain(c) for r in [head] + body for c in r))
            table.clear()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line:
            flush()
            continue
        if line.startswith(":::"):
            flush()
            kind = line[3:].strip()
            body: list[str] = []
            while i < len(lines) and lines[i].strip() != ":::":
                body.append(lines[i])
                i += 1
            if i >= len(lines):
                raise ManualError(f"{line!r} is not closed with :::")
            i += 1
            if kind in ("tip", "warn"):
                inner = blocks_html(body, images, words, terms, cat)
                out.append(f'<div class="m-box m-{kind}">{inner}</div>')
            elif kind.startswith("chart "):
                name, *args = kind.split()[1:]
                if name not in CHARTS:
                    raise ManualError(f"unknown chart {name!r}")
                if not all(_ARG.match(a) for a in args):
                    raise ManualError(f"bad chart arguments {args!r}")
                cap = blocks_html(body, images, words, terms, cat)
                arg = f' data-args="{_E(" ".join(args))}"' if args else ""
                out.append(f'<figure class="m-chart" data-chart="{_E(name)}"{arg}><div class="m-chart-ui"></div>'
                           f'<figcaption>{cap}</figcaption></figure>')
            elif kind == "terms":
                got = [t.strip() for t in body if t.strip()]
                if not got:
                    raise ManualError(":::terms is empty")
                if terms is None:
                    raise ManualError(":::terms belongs in a section")
                terms += got
                words.append(" ".join(got))
                label = _t(cat, "terms") if cat else "terms"
                out.append(f'<p class="m-terms"><span>{_E(label)}</span> '
                           + '<span class="m-terms-sep"> · </span>'.join(f"<b>{_E(t)}</b>" for t in got) + "</p>")
            elif kind == "legend":
                cap = blocks_html(body, images, words, terms, cat)
                out.append(f'<figure class="m-legend" data-legend="1"><div class="m-legend-ui"></div>'
                           f'<figcaption>{cap}</figcaption></figure>')
            else:
                raise ManualError(f"unknown block {line!r}")
            continue
        m = _IMG.match(line)
        if m:
            flush()
            alt, src, cap = m.group(1), m.group(2), m.group(3)
            name = _img_src(src)
            images.append(name)
            words.append(plain(cap or alt))
            wh = img_size(name)
            size = f' width="{wh[0]}" height="{wh[1]}"' if wh else ""
            out.append(f'<figure class="m-fig"><img src="{IMG_URL}{_E(name)}" alt="{_E(alt)}"{size} loading="lazy" '
                       f'decoding="async">' + (f"<figcaption>{inline(cap)}</figcaption>" if cap else "") + "</figure>")
            continue
        h3 = _H3.match(line)
        if h3:
            flush()
            out.append(f"<h3>{inline(h3.group(1))}</h3>")
            words.append(plain(h3.group(1)))
            continue
        if line.startswith("|") and line.endswith("|"):
            if para or lst or quote:
                flush()
            table.append([c.strip() for c in line[1:-1].split("|")])
            continue
        kind = "ul" if line.startswith("- ") else "ol" if _OL.match(line) else None
        if kind:
            if para or quote or table or (lst and lst != kind):
                flush()
            if lst is None:
                out.append(f"<{kind}>")
                lst = kind
            item = line[2:] if kind == "ul" else _OL.sub("", line, count=1)
            out.append(f"<li>{inline(item.strip())}</li>")
            words.append(plain(item))
            continue
        if line.startswith(">"):
            if para or lst or table:
                flush()
            quote.append(line[1:].strip())
            continue
        if lst or quote or table:
            flush()
        para.append(line)
    flush()
    return "".join(out)


# ---------------------------------------------------------------------------- chapters

@dataclass
class Section:
    anchor: str
    title: str
    simple_html: str
    deep_html: str
    simple_text: str
    deep_text: str
    images: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)      # :::terms (the glossary's sections)


@dataclass
class Chapter:
    id: str
    locale: str
    order: int
    title: str
    summary: str
    icon: str
    intro_html: str
    intro_text: str
    sections: list[Section]
    images: list[str]
    deep_sub: str = ""                                  # front matter deep_sub: the fold's label (SP-855)

    @property
    def url(self) -> str:
        return f"{URL}/{self.id}"


def _front(text: str, where: str) -> tuple[dict, list[str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ManualError(f"{where}: no front matter")
    meta: dict = {}
    for k, ln in enumerate(lines[1:], 1):
        if ln.strip() == "---":
            return meta, lines[k + 1:]
        if ":" in ln:
            key, val = ln.split(":", 1)
            meta[key.strip()] = val.strip()
    raise ManualError(f"{where}: front matter not closed")


def parse(text: str, locale: str, where: str = "chapter") -> Chapter:
    meta, lines = _front(text, where)
    cid = meta.get("id", "")
    if not _ID.match(cid):
        raise ManualError(f"{where}: bad id {cid!r}")
    parts: list[tuple[Optional[tuple[str, str]], list[str]]] = [(None, [])]
    for ln in lines:
        m = _H2.match(ln.strip())
        if m:
            parts.append(((m.group(2), m.group(1)), []))
        elif ln.strip().startswith("## "):
            raise ManualError(f"{where}: a section heading needs an anchor {{#id}}: {ln.strip()!r}")
        else:
            parts[-1][1].append(ln)
    images: list[str] = []
    intro_words: list[str] = []
    cat = page_catalog(("manual",), locale)
    intro_html = blocks_html(parts[0][1], images, intro_words, None, cat)
    sections: list[Section] = []
    seen: set[str] = set()
    for head, body in parts[1:]:
        anchor, title = head
        if anchor in seen:
            raise ManualError(f"{where}: anchor {anchor!r} twice")
        seen.add(anchor)
        deep_at = next((k for k, ln in enumerate(body) if ln.strip() == ":::deep"), None)
        simple_lines, deep_lines = body, []
        if deep_at is not None:
            end = next((k for k in range(len(body) - 1, deep_at, -1) if body[k].strip() == ":::"), None)
            if end is None:
                raise ManualError(f"{where}: :::deep in {anchor!r} is not closed")
            simple_lines, deep_lines = body[:deep_at], body[deep_at + 1:end]
            if any(ln.strip() for ln in body[end + 1:]):
                raise ManualError(f"{where}: text after the deep part of {anchor!r} (put it before :::deep)")
        imgs: list[str] = []
        sw: list[str] = []
        dw: list[str] = []
        tw: list[str] = []
        s_html = blocks_html(simple_lines, imgs, sw, tw, cat)
        d_html = blocks_html(deep_lines, imgs, dw, None, cat) if deep_lines else ""
        images += imgs
        sections.append(Section(anchor, plain(title), s_html, d_html, " ".join(w for w in sw if w),
                                " ".join(w for w in dw if w), imgs, tw))
    try:
        order = int(meta.get("order", "99"))
    except ValueError:
        raise ManualError(f"{where}: order is not a number") from None
    return Chapter(cid, locale, order, meta.get("title", cid), meta.get("summary", ""), meta.get("icon", ""),
                   intro_html, " ".join(intro_words), sections, images, meta.get("deep_sub", ""))


_CACHE: dict[str, tuple[tuple, list[Chapter]]] = {}
_LOCK = threading.Lock()


def chapters(locale: str) -> list[Chapter]:
    """The locale's chapters in their order (parsed again when a file changes)."""
    loc = locale if locale in LOCALES else i18n.DEFAULT_LOCALE
    files = sorted((ROOT / loc).glob("*.md"))
    stamp = tuple((p.name, p.stat().st_mtime_ns) for p in files)
    with _LOCK:
        hit = _CACHE.get(loc)
        if hit and hit[0] == stamp:
            return hit[1]
    out = [parse(p.read_text("utf-8"), loc, f"{loc}/{p.name}") for p in files]
    out.sort(key=lambda c: (c.order, c.id))
    with _LOCK:
        _CACHE[loc] = (stamp, out)
    return out


def chapter(locale: str, cid: str) -> Optional[Chapter]:
    return next((c for c in chapters(locale) if c.id == cid), None)


def anchors(locale: str = i18n.DEFAULT_LOCALE) -> set[str]:
    """Every "<chapter>#<section>" (and the chapters themselves, "<chapter>")."""
    out: set[str] = set()
    for c in chapters(locale):
        out.add(c.id)
        out.update(f"{c.id}#{s.anchor}" for s in c.sections)
    return out


def terms(locale: str) -> list[dict]:
    """SP-559: the glossary's words as the screens write them (:::terms), each with its section —
    [{"term", "url"}], longest first (so a popup's 「長期訓練負荷」 is that, not 「訓練負荷」)."""
    out = [{"term": t, "url": f"{c.url}#{s.anchor}"} for c in chapters(locale) for s in c.sections for t in s.terms]
    return sorted(out, key=lambda x: -len(x["term"]))


def chunks(locale: str) -> list[dict]:
    """The search's (and SP-306's) pieces: one per section part, with where it lives."""
    out = []
    for c in chapters(locale):
        if c.intro_text:
            out.append({"url": c.url, "chapter": c.id, "chapter_title": c.title, "anchor": "", "title": c.title,
                        "part": "simple", "text": c.intro_text})
        for s in c.sections:
            base = {"chapter": c.id, "chapter_title": c.title, "anchor": s.anchor, "title": s.title}
            out.append({**base, "url": f"{c.url}#{s.anchor}", "part": "simple", "text": s.simple_text})
            if s.deep_text:
                out.append({**base, "url": f"{c.url}#{s.anchor}--deep", "part": "deep", "text": s.deep_text})
    return out


# ---------------------------------------------------------------------------- pages

def _t(cat: dict, key: str) -> str:
    v = cat.get("manual." + key, key)
    return v.get("other", "") if isinstance(v, dict) else v


def _toc(chs: list[Chapter], cur: Optional[Chapter]) -> str:
    items = []
    for c in chs:
        on = cur is not None and c.id == cur.id
        subs = "".join(f'<li><a href="{c.url}#{s.anchor}" data-anchor="{s.anchor}">{_E(s.title)}</a></li>'
                       for s in c.sections) if on else ""
        items.append(f'<li class="m-toc-ch{" on" if on else ""}"><a href="{c.url}"'
                     + (' aria-current="page"' if on else "")
                     + f' data-icon="{_E(c.icon)}"><span>{_E(c.title)}</span></a>'
                     + (f"<ol>{subs}</ol>" if subs else "") + "</li>")
    return "<ol>" + "".join(items) + "</ol>"


def _section_html(s: Section, cat: dict, sub: str = "") -> str:
    """One section; `sub` = the chapter's own label for the 「深入」 fold (front matter deep_sub), else
    manual.json's 「公式、模型與來源」."""
    deep = ""
    if s.deep_html:
        deep = (f'<details class="m-deep" id="{s.anchor}--deep"><summary><span class="m-tag m-tag-deep">'
                f'{_E(_t(cat, "deep"))}</span><span class="m-deep-sub">{_E(sub or _t(cat, "deep_sub"))}</span></summary>'
                f'<div class="m-deep-body">{s.deep_html}</div></details>')
    return (f'<section class="m-sec" id="{s.anchor}" data-anchor="{s.anchor}"><h2><a class="m-hash" '
            f'href="#{s.anchor}" aria-label="{_E(_t(cat, "link_here"))}">#</a>{_E(s.title)}</h2>'
            f'<div class="m-simple"><span class="m-tag m-tag-simple">{_E(_t(cat, "simple"))}</span>'
            f"{s.simple_html}</div>{deep}</section>")


def _fill(loc: str, chs: list[Chapter], cur: Optional[Chapter]) -> dict:
    cat = page_catalog(("manual",), loc)
    name = _t(cat, "title")
    if cur is None:
        cards = "".join(
            f'<a class="m-card" href="{c.url}"><span class="m-card-h" data-icon="{_E(c.icon)}"><b>{_E(c.title)}</b></span>'
            f'<span class="m-card-s">{_E(c.summary)}</span><ol>'
            + "".join(f"<li>{_E(s.title)}</li>" for s in c.sections) + "</ol></a>" for c in chs)
        main = (f'<header class="m-hero"><h1>{_E(name)}</h1><p>{_E(_t(cat, "lead"))}</p></header>'
                f'<div class="m-levels"><span><span class="m-tag m-tag-simple">{_E(_t(cat, "simple"))}</span>'
                f'{_E(_t(cat, "simple_for"))}</span><span><span class="m-tag m-tag-deep">{_E(_t(cat, "deep"))}'
                f'</span>{_E(_t(cat, "deep_for"))}</span></div><nav class="m-cards" aria-label="{_E(name)}">'
                f"{cards}</nav>")
        title = name
    else:
        k = chs.index(cur)
        prev = chs[k - 1] if k > 0 else None
        nxt = chs[k + 1] if k + 1 < len(chs) else None
        pager = ('<nav class="m-pager">'
                 + (f'<a class="m-prev" href="{prev.url}"><small>{_E(_t(cat, "prev"))}</small>{_E(prev.title)}</a>'
                    if prev else "<span></span>")
                 + (f'<a class="m-next" href="{nxt.url}"><small>{_E(_t(cat, "next"))}</small>{_E(nxt.title)}</a>'
                    if nxt else "<span></span>") + "</nav>")
        main = (f'<header class="m-head"><a class="m-crumb" href="{URL}">{_E(name)}</a><h1>{_E(cur.title)}</h1>'
                + (f'<div class="m-intro">{cur.intro_html}</div>' if cur.intro_html else "") + "</header>"
                + "".join(_section_html(s, cat, cur.deep_sub) for s in cur.sections) + pager)
        title = f"{cur.title} · {name}"
    return {"title": _E(f"{title} · {brand(loc)}", quote=False), "toc": _toc(chs, cur), "main": main,
            "chapter": _E(cur.id if cur else "")}


def _page(cur_id: Optional[str]):
    loc = i18n.current_locale()
    chs = chapters(loc)
    cur = None
    if cur_id is not None:
        cur = next((c for c in chs if c.id == cur_id), None)
        if cur is None:
            raise HTTPException(404, "not found")
    # no-cache, not no-store: the offline copy (offline_core.js MANUAL) keeps it; the page inlines the
    # session (a signed-in user's name), so never a shared cache
    return render_page("manual", headers={"Cache-Control": "private, no-cache"}, fill=_fill(loc, chs, cur))


@router.get("/manual")
def manual_index():
    return _page(None)


@router.get("/manual/index.json")
def manual_search_index():
    loc = i18n.current_locale()
    chs = chapters(loc)
    body = {"locale": loc if loc in LOCALES else i18n.DEFAULT_LOCALE,
            "chapters": [{"id": c.id, "title": c.title, "url": c.url,
                          "sections": [{"anchor": s.anchor, "title": s.title} for s in c.sections],
                          # the offline copy fetches these too (offline_core.js warmManual)
                          "images": sorted({IMG_URL + n for n in c.images})} for c in chs],
            "chunks": chunks(loc)}
    return JSONResponse(body, headers={"Cache-Control": "public, no-cache", "Vary": "Cookie, Accept-Language"})


@router.get("/manual/terms.json")
def manual_terms():
    """The words the pages' ? help links to the glossary (shell.js ManualLink); ?lang= picks the language."""
    loc = i18n.current_locale()
    loc = loc if loc in LOCALES else i18n.DEFAULT_LOCALE
    return JSONResponse({"locale": loc, "terms": terms(loc)},
                        headers={"Cache-Control": "private, max-age=3600", "Vary": "Cookie, Accept-Language"})


@router.get("/manual/img/{name}")
def manual_image(name: str):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or name.startswith("."):
        raise HTTPException(404, "not found")
    p = IMG_DIR / name
    kind = IMG_TYPES.get(p.suffix.lower())
    if kind is None or not p.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type=kind, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/manual/{cid}")
def manual_chapter(cid: str):
    if not _ID.match(cid):
        raise HTTPException(404, "not found")
    return _page(cid)


def dump(locale: str) -> str:
    """The chunks as JSON text (python -m backend.api.manual <locale>: what the search / AI read)."""
    return json.dumps(chunks(locale), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    import sys
    print(dump(sys.argv[1] if len(sys.argv) > 1 else i18n.DEFAULT_LOCALE))
