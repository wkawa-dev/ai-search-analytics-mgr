"""Opinionated SEO analysis over GSC + GA4 data. Pure functions over rows (testable offline) plus
`full_report`, which pulls everything and writes a prioritized, evidence-backed Markdown/JSON report."""

from __future__ import annotations

import json
import re
import statistics
import time
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

from . import config, dates, ga4, gsc
from .errors import AsamError

# Fallback organic CTR by position (blend of public industry studies); the site's own curve is preferred.
DEFAULT_CTR = {1: 0.28, 2: 0.155, 3: 0.105, 4: 0.075, 5: 0.055, 6: 0.043, 7: 0.035, 8: 0.028, 9: 0.023, 10: 0.02}


def expected_ctr(position: float, curve: dict[int, float] | None = None) -> float:
    curve = curve or DEFAULT_CTR
    p = max(1, int(round(position)))
    if p in curve:
        return curve[p]
    return 0.01 if p <= 20 else 0.004


def ctr_curve(rows: list[dict[str, Any]], min_impressions: int = 50) -> dict[int, float]:
    """Site-specific CTR curve (median CTR per rounded position), blended with defaults when data is thin."""
    buckets: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        if r["impressions"] >= min_impressions and r["position"] <= 10.5:
            buckets[max(1, int(round(r["position"])))].append(r["ctr"])
    curve = dict(DEFAULT_CTR)
    for p, vals in buckets.items():
        if len(vals) >= 5:
            curve[p] = round(0.5 * statistics.median(vals) + 0.5 * DEFAULT_CTR.get(p, 0.02), 4)
    # keep monotonic non-increasing
    for p in range(2, 11):
        curve[p] = min(curve[p], curve[p - 1])
    return curve


def quick_wins(rows: list[dict[str, Any]], min_impressions: int = 100, pos_min: float = 4, pos_max: float = 20,
               target_position: int = 3, curve: dict[int, float] | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Queries/pages ranking on page 1 bottom / page 2 with demand: small ranking gains = big click gains."""
    out = []
    for r in rows:
        if r["impressions"] < min_impressions or not (pos_min <= r["position"] <= pos_max):
            continue
        potential = r["impressions"] * expected_ctr(target_position, curve) - r["clicks"]
        if potential <= 0:
            continue
        out.append({**r, "potential_extra_clicks": int(potential)})
    return sorted(out, key=lambda x: -x["potential_extra_clicks"])[:limit]


def ctr_gaps(rows: list[dict[str, Any]], min_impressions: int = 200, ratio: float = 0.6,
             curve: dict[int, float] | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Top-10 rankings whose CTR is far below expected: titles/descriptions/rich results need work."""
    out = []
    for r in rows:
        if r["impressions"] < min_impressions or r["position"] > 10.5:
            continue
        exp = expected_ctr(r["position"], curve)
        if r["ctr"] < exp * ratio:
            out.append({**r, "expected_ctr": round(exp, 4),
                        "missed_clicks": int(r["impressions"] * exp - r["clicks"])})
    return sorted(out, key=lambda x: -x["missed_clicks"])[:limit]


def cannibalization(rows: list[dict[str, Any]], min_impressions: int = 50, min_share: float = 0.1,
                    limit: int = 50) -> list[dict[str, Any]]:
    """Queries where 2+ of your URLs split impressions (rows need query+page dims)."""
    by_q: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_q[r["query"]].append(r)
    out = []
    for q, rs in by_q.items():
        total = sum(r["impressions"] for r in rs)
        if total < min_impressions:
            continue
        comp = [r for r in rs if r["impressions"] / total >= min_share]
        if len(comp) < 2:
            continue
        comp.sort(key=lambda r: -r["impressions"])
        out.append({"query": q, "impressions": total, "clicks": sum(r["clicks"] for r in rs),
                    "pages": [{"page": r["page"], "impressions": r["impressions"], "clicks": r["clicks"],
                               "position": r["position"]} for r in comp],
                    "severity": round(1 - comp[0]["impressions"] / total, 2)})
    return sorted(out, key=lambda x: -(x["impressions"] * x["severity"]))[:limit]


def compare(cur: list[dict[str, Any]], prev: list[dict[str, Any]], key: list[str],
            min_clicks: int = 5, limit: int = 30) -> dict[str, Any]:
    """Period-over-period movers keyed on `key` dims: losers, gainers, new, lost."""
    def k(r):
        return tuple(r[x] for x in key)

    pm = {k(r): r for r in prev}
    cm = {k(r): r for r in cur}
    rows = []
    for kk in set(pm) | set(cm):
        c, p = cm.get(kk), pm.get(kk)
        row = dict(zip(key, kk))
        row.update(clicks=c["clicks"] if c else 0, clicks_prev=p["clicks"] if p else 0,
                   impressions=c["impressions"] if c else 0, impressions_prev=p["impressions"] if p else 0,
                   position=c["position"] if c else None, position_prev=p["position"] if p else None)
        row["clicks_delta"] = row["clicks"] - row["clicks_prev"]
        row["clicks_pct"] = round(row["clicks_delta"] / row["clicks_prev"] * 100, 1) if row["clicks_prev"] else None
        if row["position"] is not None and row["position_prev"] is not None:
            row["position_delta"] = round(row["position"] - row["position_prev"], 2)
        rows.append(row)
    losers = sorted([r for r in rows if r["clicks_delta"] < 0 and r["clicks_prev"] >= min_clicks],
                    key=lambda r: r["clicks_delta"])[:limit]
    gainers = sorted([r for r in rows if r["clicks_delta"] > 0 and r["clicks"] >= min_clicks],
                     key=lambda r: -r["clicks_delta"])[:limit]
    new = sorted([r for r in rows if r["impressions_prev"] == 0 and r["impressions"] > 0],
                 key=lambda r: -r["impressions"])[:limit]
    lost = sorted([r for r in rows if r["impressions"] == 0 and r["impressions_prev"] > 0],
                  key=lambda r: -r["impressions_prev"])[:limit]
    return {"losers": losers, "gainers": gainers, "new": new, "lost": lost}


def decay(page_cmp: dict[str, Any], threshold_pct: float = -30) -> list[dict[str, Any]]:
    return [r for r in page_cmp["losers"] if r["clicks_pct"] is not None and r["clicks_pct"] <= threshold_pct]


def brand_terms_for(site: str, explicit: str | list[str] | None = None) -> list[str]:
    if explicit:
        terms = explicit.split(",") if isinstance(explicit, str) else explicit
    else:
        cfg = config.get("brand_terms")
        if cfg:
            terms = cfg.split(",") if isinstance(cfg, str) else cfg
        else:
            label = gsc.bare_domain(site).split(".")[0]
            terms = [label] + ([re.sub(r"[-_]", " ", label)] if re.search(r"[-_]", label) else [])
    return [t.strip().lower() for t in terms if t.strip()]


def brand_split(rows: list[dict[str, Any]], terms: list[str]) -> dict[str, Any]:
    pat = re.compile("|".join(re.escape(t).replace(r"\ ", r"\s*") for t in terms), re.I) if terms else None
    agg = {"brand": {"clicks": 0, "impressions": 0, "queries": 0}, "non_brand": {"clicks": 0, "impressions": 0, "queries": 0}}
    for r in rows:
        b = "brand" if pat and pat.search(r["query"]) else "non_brand"
        agg[b]["clicks"] += r["clicks"]
        agg[b]["impressions"] += r["impressions"]
        agg[b]["queries"] += 1
    tot = agg["brand"]["clicks"] + agg["non_brand"]["clicks"]
    agg["non_brand_click_share"] = round(agg["non_brand"]["clicks"] / tot, 3) if tot else None
    agg["terms"] = terms
    return agg


def _path(u: str) -> str:
    p = urlparse(u)
    return (p.path or "/") if p.scheme else (u.split("?")[0] or "/")


def join_pages(gsc_pages: list[dict[str, Any]], ga_pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join GSC page rows with GA4 organic landing-page rows on URL path."""
    ga = {}
    for r in ga_pages:
        path = _path(r.get("landingPage") or r.get("landingPagePlusQueryString") or "")
        ga[path.rstrip("/") or "/"] = r
    out = []
    for r in gsc_pages:
        path = _path(r["page"]).rstrip("/") or "/"
        g = ga.get(path, {})
        out.append({"page": r["page"], "clicks": r["clicks"], "impressions": r["impressions"], "ctr": r["ctr"],
                    "position": r["position"], "sessions": g.get("sessions"), "engagement_rate": g.get("engagementRate"),
                    "key_events": g.get("keyEvents"), "avg_engagement_s": g.get("averageSessionDuration")})
    return out


def engagement_flags(joined: list[dict[str, Any]], min_clicks: int = 50) -> dict[str, list[dict[str, Any]]]:
    rated = [j for j in joined if j.get("engagement_rate") is not None and j["clicks"] >= min_clicks]
    if not rated:
        return {"low_engagement": [], "converting_underexposed": []}
    med = statistics.median(j["engagement_rate"] for j in rated)
    low = sorted([j for j in rated if j["engagement_rate"] < med * 0.7], key=lambda j: -j["clicks"])[:20]
    seen: set[str] = set()
    conv = [j for j in joined if (j.get("key_events") or 0) > 0 and j["position"] > 5 and j["impressions"] > 100
            and not (j["page"] in seen or seen.add(j["page"]))]
    conv = sorted(conv, key=lambda j: -((j["key_events"] or 0) / max(1, j.get("sessions") or 1)))[:20]
    return {"low_engagement": low, "converting_underexposed": conv, "median_engagement_rate": round(med, 3)}


# ============================================================ orchestration
def _pct(a: float, b: float) -> float | None:
    return round((a - b) / b * 100, 1) if b else None


def full_report(site: str | None = None, property: str | None = None, date_range: str = "last28d",
                compare_mode: str = "previous", brand_terms: str | None = None, web_checks: bool = True,
                include_ga4: bool = True, save: bool = True, row_limit: int = 25000) -> dict[str, Any]:
    t0 = time.time()
    prop = gsc.resolve_site(site)
    s, e = dates.parse_range(date_range, lag=dates.GSC_LAG_DAYS)
    ps, pe = dates.previous_period(s, e, compare_mode)
    S, E, PS, PE = map(dates.iso, (s, e, ps, pe))
    rep: dict[str, Any] = {"site": prop, "period": {"current": [S, E], "previous": [PS, PE], "mode": compare_mode},
                           "warnings": []}

    tot_c = gsc.totals(prop, S, E)
    tot_p = gsc.totals(prop, PS, PE)
    rep["gsc_totals"] = {"current": tot_c, "previous": tot_p,
                         "change_pct": {k: _pct(tot_c[k], tot_p[k]) for k in ("clicks", "impressions")},
                         "ctr_delta": round(tot_c["ctr"] - tot_p["ctr"], 4),
                         "position_delta": round(tot_c["position"] - tot_p["position"], 2)}

    qp = gsc.query(prop, start=S, end=E, dimensions=["query", "page"], limit=row_limit)["rows"]
    q = gsc.query(prop, start=S, end=E, dimensions=["query"], limit=row_limit)["rows"]
    q_prev = gsc.query(prop, start=PS, end=PE, dimensions=["query"], limit=row_limit)["rows"]
    pg = gsc.query(prop, start=S, end=E, dimensions=["page"], limit=row_limit)["rows"]
    pg_prev = gsc.query(prop, start=PS, end=PE, dimensions=["page"], limit=row_limit)["rows"]
    dev = gsc.query(prop, start=S, end=E, dimensions=["device"], limit=10)["rows"]
    ctry = gsc.query(prop, start=S, end=E, dimensions=["country"], limit=15)["rows"]
    daily = gsc.query(prop, start=PS, end=E, dimensions=["date"], limit=1000)["rows"]
    if len(q) >= row_limit:
        rep["warnings"].append(f"Query rows hit the {row_limit} limit; long-tail truncated.")

    curve = ctr_curve(q)
    rep["ctr_curve"] = curve
    rep["top_queries"] = sorted(q, key=lambda r: -r["clicks"])[:25]
    rep["top_pages"] = sorted(pg, key=lambda r: -r["clicks"])[:25]
    rep["devices"] = dev
    rep["countries"] = ctry
    rep["daily"] = daily
    rep["quick_wins"] = quick_wins(qp, curve=curve, limit=30)
    rep["ctr_gaps"] = ctr_gaps(qp, curve=curve, limit=30)
    rep["cannibalization"] = cannibalization(qp, limit=25)
    rep["query_movers"] = compare(q, q_prev, ["query"], limit=20)
    page_cmp = compare(pg, pg_prev, ["page"], limit=30)
    rep["page_movers"] = page_cmp
    rep["content_decay"] = decay(page_cmp)
    rep["brand"] = brand_split(q, brand_terms_for(prop, brand_terms))
    try:
        rep["sitemaps"] = gsc.list_sitemaps(prop)["sitemaps"]
    except AsamError as ex:
        rep["warnings"].append(f"sitemaps: {ex.code}")

    if include_ga4:
        try:
            gp = ga4.resolve_property(property or prop)
            rep["ga4_property"] = gp
            mets = ["sessions", "totalUsers", "engagementRate", "keyEvents", "averageSessionDuration"]
            ch = ga4.report(gp, start=S, end=E, dimensions=["sessionDefaultChannelGroup"], metrics=mets, limit=20)
            ch_prev = ga4.report(gp, start=PS, end=PE, dimensions=["sessionDefaultChannelGroup"], metrics=mets, limit=20)
            prevmap = {r["sessionDefaultChannelGroup"]: r for r in ch_prev["rows"]}
            rep["ga4_channels"] = [{**r, "sessions_prev": prevmap.get(r["sessionDefaultChannelGroup"], {}).get("sessions", 0),
                                    "sessions_pct": _pct(r["sessions"], prevmap.get(r["sessionDefaultChannelGroup"], {}).get("sessions", 0))}
                                   for r in ch["rows"]]
            org = ga4.report(gp, start=S, end=E, dimensions=["landingPage"], metrics=mets,
                             filters=["sessionDefaultChannelGroup == Organic Search"], limit=5000)
            rep["ga4_organic_landing_pages"] = org["rows"][:25]
            joined = join_pages(pg, org["rows"])
            rep["engagement"] = engagement_flags(joined)
            try:
                rep["ga4_key_events"] = [k.get("eventName") for k in ga4.list_key_events(gp)]
            except AsamError:
                rep["ga4_key_events"] = None
        except AsamError as ex:
            rep["warnings"].append(f"GA4 skipped: [{ex.code}] {ex.message}")

    if web_checks:
        from . import web

        home = web.normalize_site(prop) + "/"
        try:
            rep["robots"] = web.robots_check(home)
            rep["sitemap_health"] = web.sitemap_check(home, sample=15)
            rep["homepage_audit"] = web.audit_page(home)
            rep["ai_readiness"] = web.ai_readiness(home)
        except Exception as ex:  # web checks must never sink the report
            rep["warnings"].append(f"web checks partial: {ex}")
        try:
            rep["pagespeed_mobile"] = web.pagespeed(home, "mobile")
        except Exception as ex:
            rep["warnings"].append(f"pagespeed: {ex}")

    rep["recommendations"] = recommendations(rep)
    rep["elapsed_s"] = round(time.time() - t0, 1)
    if save:
        host = gsc.bare_domain(prop)
        stamp = time.strftime("%Y%m%d-%H%M")
        d = config.reports_dir()
        jp = d / f"report-{host}-{stamp}.json"
        mp = d / f"report-{host}-{stamp}.md"
        jp.write_text(json.dumps(rep, indent=1, default=str))
        mp.write_text(to_markdown(rep))
        rep["files"] = {"json": str(jp), "markdown": str(mp)}
    return rep


def recommendations(rep: dict[str, Any]) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []

    def add(priority, area, title, evidence, action):
        recs.append({"priority": priority, "area": area, "title": title, "evidence": evidence, "action": action})

    t = rep.get("gsc_totals", {})
    cp = (t.get("change_pct") or {}).get("clicks")
    if cp is not None and cp <= -15:
        add("high", "traffic", f"Organic clicks down {cp}% vs previous period",
            f"{t['previous']['clicks']} -> {t['current']['clicks']}",
            "Check content_decay + page_movers.losers first; inspect top losing URLs (`asam gsc inspect`) for "
            "indexing/canonical changes; compare with Google algorithm update dates.")
    qw = rep.get("quick_wins") or []
    if qw:
        top = qw[:5]
        add("high", "rankings", f"{len(qw)} quick-win query/page pairs ranking 4-20",
            "; ".join(f"'{r['query']}' pos {r['position']} ({r['impressions']} impr)" for r in top),
            "For each page: expand content to fully answer the query, add the query to title/H1/intro, add internal "
            "links from strong pages with the query as anchor text, add FAQ/structured data.")
    cg = rep.get("ctr_gaps") or []
    if cg:
        add("high", "ctr", f"{len(cg)} top-10 rankings with CTR far below expected",
            "; ".join(f"'{r['query']}' pos {r['position']} ctr {r['ctr']:.1%} vs {r['expected_ctr']:.1%}" for r in cg[:5]),
            "Rewrite <title> and meta description to match intent (numbers, year, benefit), add rich-result "
            "structured data, check that Google isn't rewriting the title (compare SERP).")
    cn = rep.get("cannibalization") or []
    if cn:
        add("medium", "content", f"{len(cn)} queries split across multiple URLs (cannibalization)",
            "; ".join(f"'{c['query']}' -> {len(c['pages'])} pages" for c in cn[:5]),
            "Pick one canonical target per query: merge/redirect weaker pages or differentiate intent; point internal "
            "links to the winner.")
    dc = rep.get("content_decay") or []
    if dc:
        add("high" if len(dc) > 5 else "medium", "content", f"{len(dc)} pages lost >=30% clicks",
            "; ".join(f"{urlparse(r['page']).path} {r['clicks_prev']}->{r['clicks']}" for r in dc[:5]),
            "Refresh: update facts/dates, expand sections competitors cover, fix intent drift; re-submit via "
            "`asam gsc inspect` + sitemap lastmod.")
    b = rep.get("brand") or {}
    if b.get("non_brand_click_share") is not None and b["non_brand_click_share"] < 0.3:
        add("medium", "strategy", "Organic traffic is mostly branded",
            f"non-brand share {b['non_brand_click_share']:.0%}",
            "Build topic clusters targeting non-brand informational/commercial queries (see quick_wins and "
            "query_movers.new for seeds).")
    for sm in rep.get("sitemaps") or []:
        if int(sm.get("errors") or 0) > 0:
            add("high", "indexing", f"Sitemap {sm.get('path')} has {sm.get('errors')} errors", json.dumps(sm)[:200],
                "Open the sitemap report in GSC or run `asam web sitemap-check` and fix invalid URLs.")
    for key in ("robots", "sitemap_health", "ai_readiness"):
        for i in (rep.get(key) or {}).get("issues", []):
            if i["severity"] in ("error", "warning"):
                add("high" if i["severity"] == "error" else "medium",
                    "ai-search" if key == "ai_readiness" else "technical", i["message"], i["code"], _fix_for(i["code"]))
    ha = rep.get("homepage_audit") or {}
    for i in ha.get("issues", []):
        if i["severity"] == "error":
            add("high", "technical", f"Homepage: {i['message']}", i["code"], _fix_for(i["code"]))
    ps = rep.get("pagespeed_mobile") or {}
    fo = ps.get("field_origin") or {}
    if fo and fo.get("overall") and fo["overall"] != "FAST":
        bad = {k: v for k, v in fo.items() if isinstance(v, dict) and v.get("rating") not in ("FAST", None)}
        add("medium", "performance", f"Core Web Vitals (real users, origin) not passing: {fo['overall']}",
            json.dumps(bad)[:300], "Work through pagespeed_mobile.opportunities (largest savings first).")
    elif ps.get("scores", {}).get("performance", 100) < 50:
        add("medium", "performance", f"Mobile Lighthouse performance {ps['scores']['performance']}/100",
            ", ".join(o["title"] for o in ps.get("opportunities", [])[:3]), "Fix top opportunities.")
    eng = rep.get("engagement") or {}
    if eng.get("low_engagement"):
        add("medium", "ux", f"{len(eng['low_engagement'])} high-traffic organic landing pages with weak engagement",
            "; ".join(f"{urlparse(j['page']).path} er={j['engagement_rate']}" for j in eng["low_engagement"][:5]),
            "Match search intent above the fold, improve load speed, add clear next steps/internal links.")
    if eng.get("converting_underexposed"):
        add("medium", "growth", "Converting pages that rank below position 5",
            "; ".join(urlparse(j["page"]).path for j in eng["converting_underexposed"][:5]),
            "Prioritize these for link building/internal links/content upgrades: they already convert.")
    if rep.get("ga4_key_events") == []:
        add("medium", "measurement", "GA4 has no key events configured",
            "keyEvents list empty", "Define conversions: `asam ga4 key-event-create <event_name>` (e.g. generate_lead, "
            "sign_up, purchase).")
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(recs, key=lambda r: order[r["priority"]])


FIXES = {
    "ROBOTS_5XX": "Make /robots.txt return 200 (or 404) immediately; 5xx blocks crawling of the whole site.",
    "SEARCH_BOT_BLOCKED": "Remove the Disallow rule matching / for Googlebot/Bingbot in robots.txt.",
    "AI_SEARCH_BOTS_BLOCKED": "Allow OAI-SearchBot, ChatGPT-User, Claude-SearchBot, Claude-User, PerplexityBot in "
                              "robots.txt (you can still block training bots like GPTBot/ClaudeBot/CCBot).",
    "NO_SITEMAP_IN_ROBOTS": "Add `Sitemap: https://<host>/sitemap.xml` to robots.txt.",
    "NO_SITEMAP": "Generate a sitemap (`asam web sitemap-generate` or framework plugin) and submit it.",
    "SITEMAP_NON_200": "Only list final 200 URLs in sitemaps (no redirects/404s).",
    "SITEMAP_NOINDEX": "Remove noindex URLs from sitemaps (or drop the noindex).",
    "SITEMAP_CANONICAL_ELSEWHERE": "List only canonical URLs in sitemaps.",
    "SITEMAP_FETCH_ERROR": "Fix the sitemap URL/XML so it returns 200 valid XML.",
    "SITEMAP_DUPLICATES": "Deduplicate sitemap entries.",
    "SITEMAP_OFF_HOST": "Sitemaps may only list URLs on their own host (unless cross-verified).",
    "LOW_SSR_TEXT": "Server-render primary content (SSR/SSG); AI crawlers mostly don't execute JS.",
    "NO_ENTITY_MARKUP": "Add Organization + WebSite JSON-LD (name, url, logo, sameAs links to profiles).",
    "TITLE_MISSING": "Add a unique descriptive <title>.",
    "HTTP_ERROR": "Fix the server error for this URL.",
    "JSONLD_INVALID": "Fix the JSON syntax in the ld+json block.",
}


def _fix_for(code: str) -> str:
    return FIXES.get(code, "See issue message.")


def to_markdown(rep: dict[str, Any]) -> str:
    L: list[str] = []
    t = rep.get("gsc_totals", {})
    cur, prev = t.get("current", {}), t.get("previous", {})
    L.append(f"# SEO & Analytics report: {rep['site']}")
    L.append(f"Period {rep['period']['current'][0]} to {rep['period']['current'][1]} vs "
             f"{rep['period']['previous'][0]} to {rep['period']['previous'][1]} ({rep['period']['mode']})\n")
    if cur:
        L.append("## Search performance (Google)")
        L.append("| Metric | Current | Previous | Change |\n|---|---:|---:|---:|")
        for k in ("clicks", "impressions"):
            L.append(f"| {k} | {cur[k]:,} | {prev[k]:,} | {t['change_pct'][k]}% |")
        L.append(f"| CTR | {cur['ctr']:.2%} | {prev['ctr']:.2%} | {t['ctr_delta'] * 100:+.2f} pp |")
        L.append(f"| Avg position | {cur['position']} | {prev['position']} | {t['position_delta']:+} |\n")
    if rep.get("recommendations"):
        L.append("## Prioritized recommendations")
        for i, r in enumerate(rep["recommendations"], 1):
            L.append(f"{i}. **[{r['priority'].upper()}] {r['title']}** ({r['area']})  \n"
                     f"   Evidence: {r['evidence']}  \n   Action: {r['action']}")
        L.append("")

    def table(title, rows, cols):
        if not rows:
            return
        L.append(f"## {title}")
        L.append("| " + " | ".join(cols) + " |\n|" + "---|" * len(cols))
        for r in rows:
            vals = []
            for c in cols:
                v = r.get(c)
                if c == "page" and isinstance(v, str):
                    v = urlparse(v).path or v
                if isinstance(v, float) and c in ("ctr", "expected_ctr", "engagement_rate", "engagementRate"):
                    v = f"{v:.1%}"
                vals.append(str(v if v is not None else "").replace("|", "\\|"))
            L.append("| " + " | ".join(vals) + " |")
        L.append("")

    table("Top queries", rep.get("top_queries", [])[:15], ["query", "clicks", "impressions", "ctr", "position"])
    table("Top pages", rep.get("top_pages", [])[:15], ["page", "clicks", "impressions", "ctr", "position"])
    table("Quick wins (pos 4-20 with demand)", rep.get("quick_wins", [])[:15],
          ["query", "page", "impressions", "position", "potential_extra_clicks"])
    table("CTR gaps (top-10, CTR below expected)", rep.get("ctr_gaps", [])[:15],
          ["query", "page", "position", "ctr", "expected_ctr", "missed_clicks"])
    if rep.get("cannibalization"):
        L.append("## Cannibalization")
        for c in rep["cannibalization"][:10]:
            L.append(f"- **{c['query']}** ({c['impressions']} impr): " +
                     ", ".join(f"{urlparse(p['page']).path} (pos {p['position']}, {p['impressions']} impr)" for p in c["pages"]))
        L.append("")
    table("Content decay (pages -30% clicks or worse)", rep.get("content_decay", [])[:15],
          ["page", "clicks_prev", "clicks", "clicks_pct", "position_prev", "position"])
    qm = rep.get("query_movers") or {}
    table("Biggest losing queries", qm.get("losers", [])[:10], ["query", "clicks_prev", "clicks", "position_prev", "position"])
    table("Biggest gaining queries", qm.get("gainers", [])[:10], ["query", "clicks_prev", "clicks", "position_prev", "position"])
    table("New queries", qm.get("new", [])[:10], ["query", "impressions", "clicks", "position"])
    b = rep.get("brand")
    if b:
        L.append(f"## Brand vs non-brand\nBrand terms: {', '.join(b['terms'])}. Non-brand click share: "
                 f"{(b['non_brand_click_share'] or 0):.0%} (brand {b['brand']['clicks']:,} / non-brand "
                 f"{b['non_brand']['clicks']:,} clicks)\n")
    table("Devices", rep.get("devices", []), ["device", "clicks", "impressions", "ctr", "position"])
    table("Countries", rep.get("countries", [])[:10], ["country", "clicks", "impressions", "ctr", "position"])
    table("GA4 channels", rep.get("ga4_channels", []),
          ["sessionDefaultChannelGroup", "sessions", "sessions_prev", "sessions_pct", "engagementRate", "keyEvents"])
    table("GA4 organic landing pages", rep.get("ga4_organic_landing_pages", [])[:15],
          ["landingPage", "sessions", "engagementRate", "keyEvents"])
    eng = rep.get("engagement") or {}
    table("High-traffic pages with weak engagement", eng.get("low_engagement", [])[:10],
          ["page", "clicks", "sessions", "engagement_rate", "position"])
    ps = rep.get("pagespeed_mobile")
    if ps:
        L.append("## PageSpeed (mobile, homepage)")
        L.append(f"Scores: {ps['scores']}  \nLab: {ps['lab']}  \nField (origin): {ps.get('field_origin')}")
        for o in ps.get("opportunities", [])[:5]:
            L.append(f"- {o['title']} (~{o['savings_ms']} ms)")
        L.append("")
    ai = rep.get("ai_readiness")
    if ai:
        L.append("## AI search readiness")
        blocked = [b for b, ok in ai["ai_crawlers_allowed"].items() if not ok]
        L.append(f"AI crawlers blocked: {blocked or 'none'}  \nllms.txt: {ai['llms_txt']}  \n"
                 f"Homepage structured data: {ai['homepage'].get('structured_data_types')}\n")
    if rep.get("warnings"):
        L.append("## Warnings\n" + "\n".join(f"- {w}" for w in rep["warnings"]))
    return "\n".join(L) + "\n"
