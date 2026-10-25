"""
The plan × feature table both plan pages show (SP-846): 方案與訂閱 (/plans, signed in) and 方案與價格
(/pricing, public). One table: the rows are membership.PLAN_ROWS, the words static/i18n/<locale>/plans.json,
the numbers the admin's limits (membership.plan_limits, SP-351 / SP-718). The page puts it at its
`<!--fill:plan_rows-->` (i18n.pages.render_page), already in the request's language.

Each cell with a quota keeps `data-limit="<plan>.<key>" data-tpl="row.<key>.free_n"`: the plans page's
script still renumbers it from GET /api/v1/billing after a 重新整理.

The rest is the public pricing page (pricing_page): its prices (billing.PRICES), the yearly saving, the
admin's numbers in its plan cards, and the buttons for who is looking (signed out / free / subscriber)
and whether the subscription is on sale yet (SP-846 Q1-2: before that, the prices show and 「即將開放」
is a button that does nothing; no trial, Q1-3).
"""
from __future__ import annotations

import html
import re
from typing import Optional
from urllib.parse import urlencode

from backend import billing as B
from backend import i18n
from backend import membership as MS
from backend.i18n.pages import PageResponse, namespaces_for, page_catalog, render_page

_E = html.escape


def _pick(v, n: Optional[int]) -> str:
    """A catalog entry (a plural {"one", "other"} picks by n) with {n} put in."""
    if isinstance(v, dict):
        v = v.get("one") if n is not None and i18n.is_one(n) and v.get("one") else v.get("other")
    v = v or ""
    return v.replace("{n}", str(n)) if n is not None else v


def _cell(cat: dict, row: MS.PlanRow, side: str, limits: dict) -> str:
    kind = row.free if side == "free" else row.paid
    plan = MS.FREE if side == "free" else MS.PAID
    quota = row.free_limit if side == "free" else row.paid_limit
    cls = f"pt-{side}"
    if kind == "yes":
        return (f'<td class="{cls} pt-yes"><span aria-hidden="true">✓</span>'
                f'<span class="pt-sr">{_E(cat.get("plans.cell.yes", ""))}</span></td>')
    if kind == "no":
        return (f'<td class="{cls} pt-no"><span aria-hidden="true">—</span>'
                f'<span class="pt-sr">{_E(cat.get("plans.cell.no", ""))}</span></td>')
    key = f"row.{row.key}.{side}"
    text = cat.get(f"plans.{key}", "")
    attrs = f'class="{cls}" data-i18n="plans.{key}"'
    if quota:
        tpl = f"{key}_n"
        n = ((limits.get(plan) or {}).get(quota) or {}).get("limit")
        if n is not None and f"plans.{tpl}" in cat:
            text = _pick(cat[f"plans.{tpl}"], n)
        attrs += f' data-limit="{plan}.{quota}" data-tpl="{tpl}"'
    return f"<td {attrs}>{_E(_pick(text, None))}</td>"


def rows_html(page: str, loc: Optional[str] = None, limits: Optional[dict] = None) -> str:
    """The table's <tbody> rows for `page` ("plans" | "pricing"; its catalogs hold plans.json) in `loc`."""
    loc = loc or i18n.current_locale()
    cat = page_catalog(namespaces_for(page), loc)
    limits = MS.plan_limits() if limits is None else limits
    out, group = [], None
    for r in MS.PLAN_ROWS:
        if r.group != group:
            group = r.group
            out.append(f'<tr class="pt-grp"><th colspan="3" scope="colgroup" data-i18n="plans.grp.{group}">'
                       f'{_E(cat.get(f"plans.grp.{group}", group))}</th></tr>')
        out.append(f'<tr><td data-i18n="plans.row.{r.key}">{_E(cat.get(f"plans.row.{r.key}", r.key))}</td>'
                   + _cell(cat, r, "free", limits) + _cell(cat, r, "paid", limits) + "</tr>")
    return "\n".join(out)


def row_count() -> int:
    return len(MS.PLAN_ROWS)


# ---------------------------------------------------------------------------- the public pricing page

PAGE = "pricing"
URL = "/pricing"
SUBSCRIBE_NEXT = "/plans"
_LIMIT_EL = re.compile(r'(<(\w+)\b[^<>]*\bdata-limit="(\w+)\.(\w+)"[^<>]*\bdata-tpl="(pricing\.[\w.]+)"[^<>]*>)([^<]*)(</\2>)')


def fill_limits(src: str, cat: dict, limits: dict) -> str:
    """The pricing page's own `data-limit` / `data-tpl="pricing.…"` elements with the admin's numbers."""
    def one(m: re.Match) -> str:
        n = ((limits.get(m.group(3)) or {}).get(m.group(4)) or {}).get("limit")
        tpl = cat.get(m.group(5))
        if n is None or tpl is None:
            return m.group(0)
        return m.group(1) + _E(_pick(tpl, n)) + m.group(7)
    return _LIMIT_EL.sub(one, src)


def prices() -> dict:
    """{month, year, save (a year of monthly minus the yearly), pct (rounded down), avg (the yearly a month)}."""
    m, y = B.PRICES[B.MONTH], B.PRICES[B.YEAR]
    save = m * 12 - y
    return {"month": m, "year": y, "save": save, "pct": int(100 * save / (m * 12)), "avg": round(y / 12)}


def _fmt(cat: dict, key: str, **p) -> str:
    t = _pick(cat.get(key, ""), None)
    for k, v in p.items():
        t = t.replace("{" + k + "}", f"{v:,}" if isinstance(v, int) else str(v))
    return t


def _link(href: str, text: str, cls: str) -> str:
    return f'<a class="{cls}" href="{_E(href)}">{_E(text)}</a>'


def viewer() -> dict:
    """Who is looking: {signed_in, role} (role "user" | "subscriber" | "owner"; None signed out)."""
    from backend.accounts import login as LG
    u = LG.current_user()
    if u is None:
        return {"signed_in": False, "role": None}
    return {"signed_in": True, "role": MS.role_of_user(u)}


def ctas(cat: dict, who: dict, open_: bool) -> dict:
    """The page's buttons (fill key -> html): the top bar's, the free card's, the subscription card's, the end's."""
    def t(k: str) -> str:
        return _pick(cat.get(f"pricing.{k}", k), None)
    out = {}
    if who["signed_in"]:
        out["nav_cta"] = _link("/", t("nav.app"), "btn small primary")
        out["free_cta"] = _link("/", t("free.open"), "btn")
        out["end_cta"] = _link("/", t("nav.app"), "btn primary")
    else:
        out["nav_cta"] = _link("/login", t("nav.login"), "btn small primary")
        out["free_cta"] = _link("/login", t("free.start"), "btn")
        out["end_cta"] = _link("/login", t("end.start"), "btn primary")
    if not open_:
        out["paid_cta"] = (f'<button type="button" class="btn primary" id="pr-soon" disabled aria-describedby="pr-soon-note">'
                           f'{_E(t("paid.soon"))}</button>'
                           f'<p class="pr-note" id="pr-soon-note">{_E(t("paid.soon_note"))}</p>')
    elif who["role"] in (MS.SUBSCRIBER, MS.OWNER):
        out["paid_cta"] = _link(SUBSCRIBE_NEXT, t("paid.manage"), "btn")
    elif who["signed_in"]:
        out["paid_cta"] = _link(SUBSCRIBE_NEXT, t("paid.go"), "btn primary")
    else:
        out["paid_cta"] = _link("/login?" + urlencode({"next": SUBSCRIBE_NEXT}), t("paid.go"), "btn primary")
    return out


def pricing_page():
    loc = i18n.current_locale()
    cat = page_catalog(namespaces_for(PAGE), loc)
    limits = MS.plan_limits()
    p = prices()
    fill = {"plan_rows": rows_html(PAGE, loc, limits),
            "row_count": _E(_fmt(cat, "pricing.all.summary", n=row_count())),
            "price_year": _E(_fmt(cat, "pricing.price.year", year=p["year"])),
            "price_month": _E(_fmt(cat, "pricing.price.month", month=p["month"])),
            "year_note": _E(_fmt(cat, "pricing.price.year_note", avg=p["avg"], save=p["save"])),
            "month_note": _E(_fmt(cat, "pricing.price.month_note", year=p["year"], save=p["save"])),
            "save_pct": _E(_fmt(cat, "pricing.seg.save", pct=p["pct"]))}
    fill.update(ctas(cat, viewer(), B.open_for_sale()))
    resp = render_page(PAGE, fill=fill)
    return PageResponse(fill_limits(resp.body.decode("utf-8"), cat, limits), resp.path,
                        headers={"Cache-Control": "private, no-cache"})
