"""Google Search Console: properties, search analytics, sitemaps, URL inspection."""

from __future__ import annotations

import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import config, dates
from .errors import AsamError
from .http import api

WM = "https://www.googleapis.com/webmasters/v3"
SC = "https://searchconsole.googleapis.com/v1"

GSC_DIMENSIONS = {"query", "page", "country", "device", "date", "searchAppearance", "hour"}
SEARCH_TYPES = {"web", "image", "video", "news", "discover", "googleNews"}
OPS = {
    "equals": "equals", "=": "equals", "==": "equals",
    "notequals": "notEquals", "!=": "notEquals",
    "contains": "contains", "~": "contains",
    "notcontains": "notContains", "!~": "notContains", "!contains": "notContains",
    "includingregex": "includingRegex", "regex": "includingRegex", "=~": "includingRegex",
    "excludingregex": "excludingRegex", "!regex": "excludingRegex", "!=~": "excludingRegex",
}


def enc(s: str) -> str:
    return urllib.parse.quote(s, safe="")


# ------------------------------------------------------------------ properties
def list_sites() -> list[dict[str, Any]]:
    return api("GET", f"{WM}/sites").get("siteEntry", [])


def _host(s: str) -> str:
    if s.startswith("sc-domain:"):
        return s[len("sc-domain:"):].lower()
    if "://" not in s:
        s = "https://" + s
    return (urllib.parse.urlparse(s).hostname or "").lower()


def bare_domain(s: str) -> str:
    h = _host(s)
    return h[4:] if h.startswith("www.") else h


def resolve_site(site: str | None) -> str:
    """Map anything (domain, URL, page URL, property id) to an accessible GSC property string."""
    site = site or config.get("default_site")
    sites = list_sites()
    names = [s["siteUrl"] for s in sites if s.get("permissionLevel") != "siteUnverifiedUser"]
    if not site:
        if len(names) == 1:
            return names[0]
        raise AsamError("SITE_REQUIRED", "No site given and no default_site configured.",
                        fix="Pass site=<domain> or `asam config set default_site <domain>`.",
                        details={"accessible_sites": names})
    if site in names:
        return site
    if site.startswith("http") and not site.endswith("/") and site + "/" in names:
        return site + "/"
    host = _host(site)
    dom = bare_domain(site)
    is_page = site.startswith("http") and urllib.parse.urlparse(site).path not in ("", "/")
    candidates = []
    for n in names:
        if n.startswith("sc-domain:"):
            d = n[len("sc-domain:"):]
            if host == d or host.endswith("." + d):
                candidates.append((0, n))
        else:
            nh = _host(n)
            url = site if site.startswith("http") else f"https://{site}/"
            if is_page and url.startswith(n):
                candidates.append((1, n))
            elif not is_page and bare_domain(nh) == dom:
                pref = [f"https://{dom}/", f"https://www.{dom}/", f"http://{dom}/", f"http://www.{dom}/"]
                candidates.append((1 + (pref.index(n) if n in pref else 5), n))
    if candidates:
        return sorted(candidates)[0][1]
    raise AsamError("SITE_NOT_FOUND", f"No accessible Search Console property matches '{site}'.",
                    fix=f"Add + verify it: `asam setup site {dom}` (fully programmatic), or check `asam sites`.",
                    details={"accessible_sites": names})


def add_site(site: str) -> dict[str, Any]:
    api("PUT", f"{WM}/sites/{enc(site)}")
    return {"added": site}


def delete_site(site: str) -> dict[str, Any]:
    api("DELETE", f"{WM}/sites/{enc(site)}")
    return {"deleted": site}


# ------------------------------------------------------------------ search analytics
def parse_filters(filters: list[str] | None) -> list[dict[str, str]]:
    out = []
    for f in filters or []:
        parts = f.strip().split(None, 2)
        if len(parts) != 3:
            raise AsamError("BAD_FILTER", f"Filter '{f}' must be '<dimension> <operator> <value>'.",
                            fix="e.g. 'query contains shoes', 'page notContains /blog/', 'country equals usa', "
                                "'query regex ^how', 'device equals MOBILE'")
        dim, op, val = parts
        op_n = OPS.get(op.lower())
        if not op_n:
            raise AsamError("BAD_FILTER_OPERATOR", f"Unknown operator '{op}'.", fix=f"Use one of {sorted(set(OPS.values()))}")
        if dim not in GSC_DIMENSIONS:
            raise AsamError("BAD_FILTER_DIMENSION", f"Unknown dimension '{dim}'.", fix=f"Use one of {sorted(GSC_DIMENSIONS)}")
        out.append({"dimension": dim, "operator": op_n, "expression": val})
    return out


def query(site: str | None = None, date_range: str = "last28d", dimensions: list[str] | None = None,
          filters: list[str] | None = None, search_type: str = "web", limit: int = 1000,
          data_state: str = "final", aggregation: str = "auto", start: str | None = None,
          end: str | None = None) -> dict[str, Any]:
    """Run a Search Analytics query with automatic pagination (up to `limit` rows)."""
    prop = resolve_site(site)
    dims = list(dimensions or ["query"])
    bad = [d for d in dims if d not in GSC_DIMENSIONS]
    if bad:
        raise AsamError("BAD_DIMENSION", f"Unknown GSC dimension(s) {bad}", fix=f"Use {sorted(GSC_DIMENSIONS)}")
    if search_type not in SEARCH_TYPES:
        raise AsamError("BAD_SEARCH_TYPE", f"search_type '{search_type}'", fix=f"Use {sorted(SEARCH_TYPES)}")
    if start and end:
        s, e = dates._d(start), dates._d(end)
    else:
        s, e = dates.parse_range(date_range, lag=0 if data_state == "all" else dates.GSC_LAG_DAYS)
    body: dict[str, Any] = {
        "startDate": dates.iso(s), "endDate": dates.iso(e), "dimensions": dims, "type": search_type,
        "dataState": data_state, "aggregationType": aggregation,
    }
    fl = parse_filters(filters)
    if fl:
        body["dimensionFilterGroups"] = [{"groupType": "and", "filters": fl}]
    rows: list[dict[str, Any]] = []
    start_row = 0
    while len(rows) < limit:
        page = min(25000, limit - len(rows))
        res = api("POST", f"{WM}/sites/{enc(prop)}/searchAnalytics/query",
                  json_body={**body, "rowLimit": page, "startRow": start_row}, cache=True)
        batch = res.get("rows", [])
        for r in batch:
            row = dict(zip(dims, r.get("keys", [])))
            row.update(clicks=r["clicks"], impressions=r["impressions"], ctr=round(r["ctr"], 4),
                       position=round(r["position"], 2))
            rows.append(row)
        if len(batch) < page:
            break
        start_row += page
    return {"site": prop, "start": body["startDate"], "end": body["endDate"], "dimensions": dims,
            "search_type": search_type, "row_count": len(rows), "rows": rows}


def totals(site: str | None, start: str, end: str, search_type: str = "web",
           filters: list[str] | None = None) -> dict[str, Any]:
    prop = resolve_site(site)
    body: dict[str, Any] = {"startDate": start, "endDate": end, "type": search_type, "dataState": "final"}
    fl = parse_filters(filters)
    if fl:
        body["dimensionFilterGroups"] = [{"groupType": "and", "filters": fl}]
    res = api("POST", f"{WM}/sites/{enc(prop)}/searchAnalytics/query", json_body=body, cache=True)
    r = (res.get("rows") or [{"clicks": 0, "impressions": 0, "ctr": 0, "position": 0}])[0]
    return {"clicks": r["clicks"], "impressions": r["impressions"], "ctr": round(r["ctr"], 4),
            "position": round(r["position"], 2)}


# ------------------------------------------------------------------ sitemaps
def list_sitemaps(site: str | None = None) -> dict[str, Any]:
    prop = resolve_site(site)
    sm = api("GET", f"{WM}/sites/{enc(prop)}/sitemaps").get("sitemap", [])
    return {"site": prop, "sitemaps": sm}


def submit_sitemap(sitemap_url: str, site: str | None = None) -> dict[str, Any]:
    prop = resolve_site(site or sitemap_url)
    api("PUT", f"{WM}/sites/{enc(prop)}/sitemaps/{enc(sitemap_url)}")
    return {"site": prop, "submitted": sitemap_url}


def delete_sitemap(sitemap_url: str, site: str | None = None) -> dict[str, Any]:
    prop = resolve_site(site or sitemap_url)
    api("DELETE", f"{WM}/sites/{enc(prop)}/sitemaps/{enc(sitemap_url)}")
    return {"site": prop, "deleted": sitemap_url}


# ------------------------------------------------------------------ URL inspection
def inspect(url: str, site: str | None = None, language: str = "en-US") -> dict[str, Any]:
    prop = resolve_site(site or url)
    res = api("POST", f"{SC}/urlInspection/index:inspect",
              json_body={"inspectionUrl": url, "siteUrl": prop, "languageCode": language})
    ir = res.get("inspectionResult", {})
    idx = ir.get("indexStatusResult", {})
    summary = {
        "url": url,
        "verdict": idx.get("verdict"),
        "coverage": idx.get("coverageState"),
        "indexing_state": idx.get("indexingState"),
        "robots_txt": idx.get("robotsTxtState"),
        "page_fetch": idx.get("pageFetchState"),
        "last_crawl": idx.get("lastCrawlTime"),
        "crawled_as": idx.get("crawledAs"),
        "google_canonical": idx.get("googleCanonical"),
        "user_canonical": idx.get("userCanonical"),
        "canonical_mismatch": bool(idx.get("googleCanonical") and idx.get("userCanonical")
                                   and idx["googleCanonical"] != idx["userCanonical"]),
        "sitemaps": idx.get("sitemap"),
        "referring_urls": (idx.get("referringUrls") or [])[:5],
        "rich_results": (ir.get("richResultsResult") or {}).get("verdict"),
        "rich_result_items": [d.get("richResultType") for d in (ir.get("richResultsResult") or {}).get("detectedItems", [])],
        "mobile_usability": (ir.get("mobileUsabilityResult") or {}).get("verdict"),
        "link": ir.get("inspectionResultLink"),
    }
    return summary


def inspect_many(urls: list[str], site: str | None = None, workers: int = 4) -> dict[str, Any]:
    prop = resolve_site(site or urls[0])
    results: list[dict[str, Any]] = []

    def one(u: str) -> dict[str, Any]:
        try:
            return inspect(u, prop)
        except AsamError as e:
            return {"url": u, "error": e.to_dict()}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(one, urls))
    by_verdict: dict[str, int] = {}
    for r in results:
        by_verdict[r.get("verdict") or "ERROR"] = by_verdict.get(r.get("verdict") or "ERROR", 0) + 1
    return {"site": prop, "count": len(results), "by_verdict": by_verdict,
            "not_indexed": [r for r in results if r.get("verdict") != "PASS"], "results": results}
