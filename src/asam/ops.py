"""The single registry of operations. The CLI (`asam <group> <op>`) and the MCP server
(`<group>_<op>` tools) are both generated from these functions, so they never drift.

Conventions (agent-first):
- Every argument is optional where a sensible default exists; `site`/`property` fall back to config
  defaults, then to "the only one you have access to".
- Sites/properties accept loose input: example.com, https://www.example.com/page, sc-domain:..., 123456.
- Big row sets are never dumped into context: `show` rows are returned inline, the full set is
  written to a file whose path is returned.
- Destructive operations require confirm=true.
"""

import csv
import inspect
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import analysis, auth, config, dates, ga4, gsc, indexing, setup, web
from .errors import AsamError
from .http import api, clear_cache


@dataclass
class Op:
    name: str  # "gsc.query"
    fn: Callable[..., Any]
    write: bool = False
    destructive: bool = False
    cli_only: bool = False

    @property
    def group(self) -> str:
        return self.name.split(".")[0]

    @property
    def action(self) -> str:
        return self.name.split(".", 1)[1].replace("_", "-")

    @property
    def tool_name(self) -> str:
        return self.name.replace(".", "_").replace("-", "_")

    @property
    def doc(self) -> str:
        return inspect.cleandoc(self.fn.__doc__ or "")


REGISTRY: dict[str, Op] = {}


def op(name: str, write: bool = False, destructive: bool = False, cli_only: bool = False):
    def deco(fn):
        REGISTRY[name] = Op(name, fn, write, destructive, cli_only)
        return fn

    return deco


def _first_sentence(doc: str) -> str:
    text = " ".join(doc.split())
    cut = text.find(". ")
    return text if cut == -1 else text[:cut + 1]


def _confirm(confirm: bool, what: str) -> None:
    if not confirm:
        raise AsamError("CONFIRM_REQUIRED", f"{what} is destructive.", fix="Re-run with confirm=true (CLI: --confirm).")


def _rows_out(res: dict[str, Any], show: int, save_to: str | None, stem: str) -> dict[str, Any]:
    rows = res.get("rows", [])
    if save_to or len(rows) > show:
        path = Path(save_to) if save_to else config.reports_dir() / f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".json":
            path.write_text(json.dumps(rows, indent=1))
        else:
            keys: list[str] = []
            for r in rows:
                keys += [k for k in r if k not in keys]
            with path.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                w.writerows(rows)
        res["full_rows_file"] = str(path)
    if len(rows) > show:
        res["rows"] = rows[:show]
        res["rows_truncated"] = f"showing {show} of {len(rows)}; all rows in full_rows_file"
    return res


# =============================================================== meta / auth / config
@op("guide.show")
def guide_show(topic: str = "overview") -> dict:
    """Built-in documentation for agents. topic: overview | auth | workflows | filters | tools."""
    from .guide import GUIDES

    if topic == "tools":
        return {"tools": [{"tool": o.tool_name, "write": o.write,
                           "cli": f"asam {o.group}" if o.group in ("doctor", "guide")
                           else f"asam {o.group.replace('_', '-')} {o.action}",
                           "destructive": o.destructive, "summary": _first_sentence(o.doc)}
                          for o in REGISTRY.values() if not o.cli_only]}
    if topic not in GUIDES:
        raise AsamError("UNKNOWN_TOPIC", topic, fix=f"Topics: {sorted(GUIDES) + ['tools']}")
    return {"topic": topic, "text": GUIDES[topic]}


@op("doctor.run")
def doctor_run(site: str | None = None) -> dict:
    """Diagnose everything (credentials, scopes, API enablement, GSC + GA4 access, config) and return
    concrete fixes. Run this first whenever anything fails."""
    out: dict[str, Any] = {"checks": [], "config": {k: v for k, v in config.load().items() if k != "indexnow_key"}}

    def check(name, fn):
        try:
            res = fn()
            out["checks"].append({"check": name, "ok": True, "result": res})
            return res
        except AsamError as e:
            out["checks"].append({"check": name, "ok": False, "error": e.to_dict()})
        except Exception as e:  # pragma: no cover
            out["checks"].append({"check": name, "ok": False, "error": {"code": "UNEXPECTED", "message": str(e)}})
        return None

    st = auth.status()
    out["auth"] = st
    if not st.get("authenticated"):
        out["next"] = st.get("error", {}).get("fix")
        out["ok"] = False
        return out
    sites = check("search_console_access", lambda: [
        {"site": s["siteUrl"], "permission": s.get("permissionLevel")} for s in gsc.list_sites()])
    check("ga4_access", lambda: [{"id": p["id"], "name": p["name"]} for p in ga4.list_properties()])
    if site or config.get("default_site") or (sites and len(sites) == 1):
        rs = check("resolve_site", lambda: gsc.resolve_site(site))
        if rs:
            s, e = dates.parse_range("last7d", lag=dates.GSC_LAG_DAYS)
            check("gsc_query", lambda: gsc.totals(rs, dates.iso(s), dates.iso(e)))
            check("ga4_property_for_site", lambda: ga4.resolve_property(site or rs))
    check("site_verification_api", lambda: len(api("GET", f"{setup.SV}/webResource").get("items", [])))
    out["ok"] = all(c["ok"] for c in out["checks"])
    fixes = [c["error"].get("fix") for c in out["checks"] if not c["ok"] and c["error"].get("fix")]
    out["fixes"] = list(dict.fromkeys(fixes))
    return out


@op("auth.status")
def auth_status() -> dict:
    """Which credentials are active, identity (email), granted scopes, and what is missing."""
    return auth.status()


@op("auth.login", write=True)
def auth_login(client: str | None = None, port: int = 8765, cloud: bool = False) -> dict:
    """Start OAuth login. Returns `auth_url` for the human to open ONCE; then call auth_finish with the URL
    the browser was redirected to. client: path to an OAuth 'Desktop app' client JSON (auto-discovered in
    ./, ~/Downloads, ~/.config/asam). cloud=true also grants cloud-platform so asam can enable APIs itself."""
    return auth.start_login(client, port=port, cloud=cloud)


@op("auth.finish", write=True)
def auth_finish(redirect_url: str) -> dict:
    """Finish OAuth login with the full redirect URL (http://127.0.0.1:8765/?state=...&code=...) or the bare code."""
    return auth.finish_login(redirect_url)


@op("auth.browser", write=True, cli_only=True)
def auth_browser(client: str | None = None, port: int = 8765, cloud: bool = False, no_browser: bool = False) -> dict:
    """Interactive login for a human at a terminal: opens the browser and captures the redirect automatically."""
    return auth.login_interactive(client, port=port, cloud=cloud, open_browser=not no_browser)


@op("auth.use", write=True)
def auth_use(path: str) -> dict:
    """Register a credential file: service-account JSON, authorized-user JSON, or OAuth client JSON."""
    return auth.use_file(path)


@op("auth.logout", write=True, destructive=True)
def auth_logout(confirm: bool = False) -> dict:
    """Delete stored tokens / service-account key from asam's config dir."""
    _confirm(confirm, "Logout")
    return auth.logout()


@op("auth.enable_apis", write=True)
def auth_enable_apis(project: str | None = None) -> dict:
    """Enable all Google APIs asam uses on the GCP project behind your OAuth client (requires login with
    cloud=true, or ADC with cloud-platform scope). project: project number or id (auto-detected)."""
    apis = ["searchconsole.googleapis.com", "analyticsadmin.googleapis.com", "analyticsdata.googleapis.com",
            "siteverification.googleapis.com", "indexing.googleapis.com", "pagespeedonline.googleapis.com"]
    if not project:
        cid = ""
        try:
            data = json.loads(auth.token_path().read_text())
            cid = data.get("client_id", "")
        except (OSError, json.JSONDecodeError):
            pass
        project = cid.split("-")[0] if cid[:1].isdigit() else None
    if not project:
        raise AsamError("PROJECT_REQUIRED", "Could not infer the GCP project.", fix="Pass project=<number or id>.")
    res = api("POST", f"https://serviceusage.googleapis.com/v1/projects/{project}/services:batchEnable",
                    json_body={"serviceIds": apis})
    return {"project": project, "enabled": apis, "operation": res.get("name")}


@op("config.show")
def config_show() -> dict:
    """Show config (defaults, brand terms, learned site->property map) and all known keys."""
    return {"path": str(config.config_path()), "values": config.load(), "known_keys": config.KNOWN_KEYS}


@op("config.set", write=True)
def config_set(key: str, value: str = "") -> dict:
    """Set a config key (empty value unsets). Keys: default_site, default_property, brand_terms,
    google_api_key, cache_ttl_seconds, reports_dir, credentials_file, impersonate."""
    v: Any = value
    if key == "cache_ttl_seconds" and value:
        v = int(value)
    return {"values": config.set_value(key, v)}


@op("cache.clear", write=True)
def cache_clear() -> dict:
    """Clear cached API responses (report queries are cached 6h by default)."""
    return {"removed": clear_cache()}


# =============================================================== setup
@op("setup.site", write=True)
def setup_site(domain: str, create_ga4: bool = True, method: str = "META", ga4_account: str | None = None) -> dict:
    """Onboard a site end-to-end, idempotently. Checks GSC, GA4 and the live site; creates the GA4 property +
    web stream (14-month retention) if missing; returns ordered `actions` (HTML to add to <head>, DNS records,
    commands) for the agent to carry out, then call setup_verify. method: META | FILE | DNS_TXT | ANALYTICS."""
    return setup.setup_site(domain, create_ga4=create_ga4, ga4_account=ga4_account, method=method)


@op("setup.token", write=True)
def setup_token(domain: str, method: str = "META") -> dict:
    """Get a Search Console ownership verification token (META tag, FILE, DNS_TXT, ANALYTICS, TAG_MANAGER)."""
    return setup.verification_token(domain, method)


@op("setup.verify", write=True)
def setup_verify(domain: str, method: str = "META", submit_sitemaps: bool = True) -> dict:
    """Verify ownership (after the token is deployed), add the property to Search Console and submit
    discovered sitemaps. DNS methods create a Domain property (sc-domain:), others a URL-prefix property."""
    return setup.verify(domain, method, submit_sitemaps=submit_sitemaps)


@op("setup.status")
def setup_status(domain: str) -> dict:
    """Wiring health for a domain: GSC property + sitemaps, GA4 property + live tag, robots, IndexNow."""
    return setup.status(domain)


# =============================================================== search console
@op("gsc.sites")
def gsc_sites() -> dict:
    """List Search Console properties you can access, with permission level."""
    return {"sites": gsc.list_sites()}


@op("gsc.query")
def gsc_query(site: str | None = None, date_range: str = "last28d", dimensions: list[str] | None = None,
              filters: list[str] | None = None, search_type: str = "web", limit: int = 1000,
              data_state: str = "final", show: int = 100, save_to: str | None = None) -> dict:
    """Search Analytics query (clicks, impressions, ctr, position), auto-paginated up to `limit` rows (max ~50k/day
    per property). dimensions: query,page,country,device,date,searchAppearance (default query).
    filters: ['query contains shoes', 'page notContains /blog/', 'country equals usa', 'query regex ^how', 'device equals MOBILE'].
    search_type: web|image|video|news|discover|googleNews. date_range: last7d|last28d|last3m|last16m|ytd|YYYY-MM-DD..YYYY-MM-DD.
    data_state: final | all (includes fresh, partial data). Returns `show` rows inline; full set saved to CSV."""
    res = gsc.query(site, date_range, dimensions, filters, search_type, limit, data_state)
    return _rows_out(res, show, save_to, "gsc-" + "-".join(res["dimensions"]))


@op("gsc.compare")
def gsc_compare(site: str | None = None, dimension: str = "page", date_range: str = "last28d",
                compare: str = "previous", min_clicks: int = 5, limit: int = 25) -> dict:
    """Period-over-period movers for one dimension (page or query): losers, gainers, new, lost.
    compare: previous | yoy."""
    s, e = dates.parse_range(date_range, lag=dates.GSC_LAG_DAYS)
    ps, pe = dates.previous_period(s, e, compare)
    cur = gsc.query(site, start=dates.iso(s), end=dates.iso(e), dimensions=[dimension], limit=25000)
    prev = gsc.query(site, start=dates.iso(ps), end=dates.iso(pe), dimensions=[dimension], limit=25000)
    return {"site": cur["site"], "current": [cur["start"], cur["end"]], "previous": [prev["start"], prev["end"]],
            **analysis.compare(cur["rows"], prev["rows"], [dimension], min_clicks=min_clicks, limit=limit)}


@op("gsc.inspect")
def gsc_inspect(url: str, site: str | None = None) -> dict:
    """URL Inspection: index verdict, coverage state, last crawl, Google vs user canonical, robots, rich results.
    Quota ~2000/day/property."""
    return gsc.inspect(url, site)


@op("gsc.inspect_batch")
def gsc_inspect_batch(urls: list[str] | None = None, site: str | None = None, top_pages: int = 0,
                      from_sitemap: str | None = None, max_urls: int = 50) -> dict:
    """Inspect many URLs in parallel and summarize by verdict. Give `urls`, or top_pages=N (your N pages with most
    impressions), or from_sitemap=<sitemap URL>. Returns not_indexed first."""
    targets = list(urls or [])
    if top_pages:
        targets += [r["page"] for r in sorted(gsc.query(site, dimensions=["page"], limit=top_pages)["rows"],
                                              key=lambda r: -r["impressions"])]
    if from_sitemap:
        targets += [u["loc"] for u in web.read_sitemap(from_sitemap)["urls"]]
    targets = list(dict.fromkeys(targets))[:max_urls]
    if not targets:
        raise AsamError("NO_URLS", "Nothing to inspect.", fix="Pass urls, top_pages=N or from_sitemap=<url>.")
    res = gsc.inspect_many(targets, site)
    res["results"] = [r for r in res["results"] if r.get("verdict") == "PASS"][:20]
    return res


@op("gsc.sitemaps")
def gsc_sitemaps(site: str | None = None) -> dict:
    """Sitemaps submitted in Search Console with errors, warnings, last download, pending state."""
    return gsc.list_sitemaps(site)


@op("gsc.sitemap_submit", write=True)
def gsc_sitemap_submit(sitemap_url: str | None = None, site: str | None = None) -> dict:
    """Submit (or resubmit, to prompt a re-fetch) a sitemap. Without sitemap_url, submits every sitemap discovered
    via robots.txt / common paths."""
    if sitemap_url:
        return gsc.submit_sitemap(sitemap_url, site)
    prop = gsc.resolve_site(site)
    found = web.discover_sitemaps(web.normalize_site(prop))
    if not found:
        raise AsamError("NO_SITEMAP", "No sitemap discovered.", fix="Pass sitemap_url or generate one.")
    return {"site": prop, "submitted": [gsc.submit_sitemap(s, prop)["submitted"] for s in found]}


@op("gsc.sitemap_delete", write=True, destructive=True)
def gsc_sitemap_delete(sitemap_url: str, site: str | None = None, confirm: bool = False) -> dict:
    """Remove a sitemap from Search Console (does not delete the file)."""
    _confirm(confirm, "Deleting a sitemap")
    return gsc.delete_sitemap(sitemap_url, site)


@op("gsc.site_add", write=True)
def gsc_site_add(site: str) -> dict:
    """Add a property (https://example.com/ or sc-domain:example.com). Needs verification to get data; prefer setup_site."""
    return gsc.add_site(site)


@op("gsc.site_delete", write=True, destructive=True)
def gsc_site_delete(site: str, confirm: bool = False) -> dict:
    """Remove a property from your Search Console account."""
    _confirm(confirm, "Deleting a Search Console property")
    return gsc.delete_site(site)


# =============================================================== GA4
@op("ga4.properties")
def ga4_properties() -> dict:
    """List GA4 accounts and properties you can access."""
    return {"properties": ga4.list_properties()}


@op("ga4.property")
def ga4_property(property: str | None = None) -> dict:
    """Property details incl. data streams (measurement IDs), timezone, currency, data retention."""
    return ga4.get_property(property)


@op("ga4.report")
def ga4_report(property: str | None = None, date_range: str = "last28d", dimensions: list[str] | None = None,
               metrics: list[str] | None = None, filters: list[str] | None = None,
               order_by: list[str] | None = None, limit: int = 1000, compare: str | None = None,
               show: int = 100, save_to: str | None = None) -> dict:
    """GA4 Data API report. property: id, 'properties/id', domain, or display name.
    dimensions e.g. date, sessionDefaultChannelGroup, sessionSourceMedium, landingPage, pagePath, country, deviceCategory, eventName.
    metrics default: sessions,totalUsers,engagementRate,keyEvents (others: screenPageViews, newUsers, bounceRate,
    averageSessionDuration, eventCount, totalRevenue, conversions...). Use ga4_metadata to discover names.
    filters: ['sessionDefaultChannelGroup == Organic Search', 'landingPage begins /blog', 'country in United States,Canada',
    'sessions > 10', 'pagePath regex ^/docs/.*']. order_by: ['-sessions'] ('-' = desc).
    compare: previous | yoy adds *_prev, *_delta, *_pct columns."""
    res = ga4.report(property, date_range, dimensions, metrics, filters, order_by, limit, compare)
    return _rows_out(res, show, save_to, "ga4-" + ("-".join(res["dimensions"]) or "totals"))


@op("ga4.realtime")
def ga4_realtime(property: str | None = None, dimensions: list[str] | None = None,
                 metrics: list[str] | None = None, limit: int = 50) -> dict:
    """Realtime report (last 30 min). Default: activeUsers by unifiedScreenName. Great to confirm a new tag works."""
    return ga4.realtime(property, dimensions, metrics, limit)


@op("ga4.metadata")
def ga4_metadata(property: str | None = None, search: str | None = None) -> dict:
    """Valid dimension/metric API names for this property (incl. custom). Use `search` to filter."""
    return ga4.metadata(property, search)


@op("ga4.create_property", write=True)
def ga4_create_property(display_name: str, url: str | None = None, account: str | None = None,
                        time_zone: str | None = None, currency: str = "USD") -> dict:
    """Create a GA4 property (+ web stream if url given) with 14-month data retention. Returns measurement ID and
    the gtag.js snippet to add to <head>. account auto-selected if you have exactly one."""
    return ga4.create_property(display_name, account, url, time_zone, currency)


@op("ga4.create_stream", write=True)
def ga4_create_stream(url: str, property: str | None = None, name: str | None = None) -> dict:
    """Add a web data stream to a property; returns measurement ID + gtag snippet."""
    return ga4.create_web_stream(property, url, name)


@op("ga4.key_events")
def ga4_key_events(property: str | None = None) -> dict:
    """List key events (conversions)."""
    return {"key_events": ga4.list_key_events(property)}


@op("ga4.key_event_create", write=True)
def ga4_key_event_create(event_name: str, property: str | None = None, once_per_session: bool = False) -> dict:
    """Mark an event as a key event (conversion), e.g. generate_lead, sign_up, purchase, form_submit."""
    return ga4.create_key_event(event_name, property, once_per_session)


@op("ga4.key_event_delete", write=True, destructive=True)
def ga4_key_event_delete(event_name: str, property: str | None = None, confirm: bool = False) -> dict:
    """Unmark a key event."""
    _confirm(confirm, "Deleting a key event")
    return ga4.delete_key_event(event_name, property)


@op("ga4.custom_definitions")
def ga4_custom_definitions(property: str | None = None) -> dict:
    """List custom dimensions and metrics."""
    return ga4.list_custom_definitions(property)


@op("ga4.custom_dimension_create", write=True)
def ga4_custom_dimension_create(parameter_name: str, display_name: str | None = None, scope: str = "EVENT",
                                description: str = "", property: str | None = None) -> dict:
    """Register an event/user parameter as a custom dimension. scope: EVENT | USER | ITEM."""
    return ga4.create_custom_dimension(parameter_name, display_name, scope, description, property)


@op("ga4.custom_metric_create", write=True)
def ga4_custom_metric_create(parameter_name: str, display_name: str | None = None, unit: str = "STANDARD",
                             description: str = "", property: str | None = None) -> dict:
    """Register a numeric event parameter as a custom metric. unit: STANDARD | CURRENCY | SECONDS | MILLISECONDS..."""
    return ga4.create_custom_metric(parameter_name, display_name, unit, description, property)


@op("ga4.grant_access", write=True)
def ga4_grant_access(email: str, role: str = "editor", property: str | None = None) -> dict:
    """Grant a user or service account access to a property. role: viewer | analyst | editor | admin."""
    return ga4.grant_access(email, role, property)


@op("ga4.retention_set", write=True)
def ga4_retention_set(property: str | None = None, retention: str = "FOURTEEN_MONTHS") -> dict:
    """Set event data retention (default for new properties is only 2 months). TWO_MONTHS | FOURTEEN_MONTHS
    (standard); 360 properties also TWENTY_SIX/THIRTY_EIGHT/FIFTY_MONTHS."""
    return ga4.set_retention(property, retention)


@op("ga4.links")
def ga4_links(property: str | None = None) -> dict:
    """Google Ads / Firebase / BigQuery links on the property."""
    return ga4.list_links(property)


# =============================================================== analysis
def _pair_rows(site: str | None, date_range: str, dims: list[str]) -> tuple[str, list[dict]]:
    res = gsc.query(site, date_range, dims, limit=25000)
    return res["site"], res["rows"]


@op("analyze.report")
def analyze_report(site: str | None = None, property: str | None = None, date_range: str = "last28d",
                   compare: str = "previous", brand_terms: str | None = None, web_checks: bool = True,
                   include_ga4: bool = True) -> dict:
    """FULL analysis in one call: GSC totals + deltas, top queries/pages, quick wins, CTR gaps (vs the site's own CTR
    curve), cannibalization, content decay, query/page movers, brand split, devices/countries, sitemaps, GA4 channels +
    organic landing-page engagement joined to GSC, robots/sitemap/homepage/AI-readiness/PageSpeed checks, and a
    prioritized recommendation list. Writes Markdown + JSON reports and returns the summary + file paths.
    Takes ~30-90s. compare: previous | yoy."""
    rep = analysis.full_report(site, property, date_range, compare, brand_terms, web_checks, include_ga4)
    return {"site": rep["site"], "period": rep["period"], "gsc_totals": rep["gsc_totals"],
            "recommendations": rep["recommendations"], "quick_wins_top5": rep["quick_wins"][:5],
            "content_decay_top5": rep["content_decay"][:5], "brand": rep["brand"],
            "ga4_channels": rep.get("ga4_channels"), "warnings": rep["warnings"], "files": rep.get("files"),
            "hint": "Read files.markdown for the full human-readable report; files.json has every table."}


@op("analyze.quick_wins")
def analyze_quick_wins(site: str | None = None, date_range: str = "last28d", min_impressions: int = 100,
                       target_position: int = 3, limit: int = 30) -> dict:
    """Query+page pairs at positions 4-20 with demand, ranked by extra clicks if moved to target_position."""
    prop, rows = _pair_rows(site, date_range, ["query", "page"])
    curve = analysis.ctr_curve(rows)
    return {"site": prop, "items": analysis.quick_wins(rows, min_impressions, target_position=target_position,
                                                       curve=curve, limit=limit)}


@op("analyze.ctr_gaps")
def analyze_ctr_gaps(site: str | None = None, date_range: str = "last28d", min_impressions: int = 200,
                     limit: int = 30) -> dict:
    """Top-10 rankings whose CTR is < 60% of expected (site-calibrated curve): rewrite titles/descriptions."""
    prop, rows = _pair_rows(site, date_range, ["query", "page"])
    curve = analysis.ctr_curve(rows)
    return {"site": prop, "ctr_curve": curve, "items": analysis.ctr_gaps(rows, min_impressions, curve=curve, limit=limit)}


@op("analyze.cannibalization")
def analyze_cannibalization(site: str | None = None, date_range: str = "last3m", min_impressions: int = 50,
                            limit: int = 30) -> dict:
    """Queries where multiple of your URLs compete (each >=10% of impressions)."""
    prop, rows = _pair_rows(site, date_range, ["query", "page"])
    return {"site": prop, "items": analysis.cannibalization(rows, min_impressions, limit=limit)}


@op("analyze.decay")
def analyze_decay(site: str | None = None, date_range: str = "last28d", compare: str = "previous",
                  threshold_pct: float = -30, min_clicks: int = 10) -> dict:
    """Pages whose clicks dropped by threshold_pct or more vs previous period / last year."""
    res = gsc_compare(site, "page", date_range, compare, min_clicks, limit=200)
    return {"site": res["site"], "current": res["current"], "previous": res["previous"],
            "items": analysis.decay(res, threshold_pct)}


@op("analyze.brand")
def analyze_brand(site: str | None = None, date_range: str = "last28d", brand_terms: str | None = None) -> dict:
    """Brand vs non-brand clicks/impressions. brand_terms: comma-separated (default from config or domain name)."""
    prop, rows = _pair_rows(site, date_range, ["query"])
    return {"site": prop, **analysis.brand_split(rows, analysis.brand_terms_for(prop, brand_terms))}


@op("analyze.landing_pages")
def analyze_landing_pages(site: str | None = None, property: str | None = None, date_range: str = "last28d",
                          show: int = 50) -> dict:
    """Join GSC pages with GA4 organic landing pages: visibility + engagement + key events per URL, with flags for
    high-traffic/low-engagement and converting-but-underexposed pages."""
    prop, pg = _pair_rows(site, date_range, ["page"])
    s, e = dates.parse_range(date_range, lag=dates.GSC_LAG_DAYS)
    org = ga4.report(property or prop, start=dates.iso(s), end=dates.iso(e), dimensions=["landingPage"],
                     metrics=["sessions", "engagementRate", "keyEvents", "averageSessionDuration"],
                     filters=["sessionDefaultChannelGroup == Organic Search"], limit=10000)
    joined = sorted(analysis.join_pages(pg, org["rows"]), key=lambda r: -r["clicks"])
    return _rows_out({"site": prop, "property": org["property"], **analysis.engagement_flags(joined), "rows": joined},
                     show, None, "landing-pages")


# =============================================================== web / discoverability
@op("web.audit")
def web_audit(url: str) -> dict:
    """On-page SEO audit of one URL: status/redirects, title, description, H1, canonical, robots/noindex, hreflang,
    Open Graph, JSON-LD (validated), alt text, word count, mixed content, GA4/GTM tags. Returns issues + score."""
    return web.audit_page(url)


@op("web.crawl")
def web_crawl(url: str, max_pages: int = 100, respect_robots: bool = True) -> dict:
    """Crawl a site (same host, bounded) and audit site-wide: status codes, broken internal links, redirects,
    duplicate titles/descriptions, noindex, thin pages, depth, sitemap orphans, pages missing from the sitemap."""
    return web.crawl(url, max_pages=max_pages, respect_robots=respect_robots)


@op("web.robots")
def web_robots(site: str, paths: list[str] | None = None) -> dict:
    """robots.txt analysis (Google longest-match semantics): declared sitemaps, access for Googlebot/Bingbot and AI
    crawlers (search vs training bots), and whether given paths are blocked."""
    return web.robots_check(site, paths)


@op("web.sitemap_check")
def web_sitemap_check(target: str, sample: int = 25) -> dict:
    """Validate sitemap(s) for a site or sitemap URL: structure, duplicates, off-host, lastmod, and live-checks a
    sample of URLs for non-200/redirect/noindex/non-canonical entries."""
    return web.sitemap_check(target, sample)


@op("web.sitemap_generate", write=True)
def web_sitemap_generate(url: str, max_pages: int = 500, out_file: str | None = None) -> dict:
    """Crawl and write sitemap.xml of indexable self-canonical pages (use when the site has none)."""
    return web.generate_sitemap(url, max_pages, out_file)


@op("web.pagespeed")
def web_pagespeed(url: str, strategy: str = "mobile") -> dict:
    """PageSpeed Insights: Lighthouse scores (perf/SEO/a11y/best-practices), lab metrics, real-user Core Web Vitals
    (CrUX page + origin), top opportunities, failed SEO audits. strategy: mobile | desktop."""
    return web.pagespeed(url, strategy)


@op("web.structured_data")
def web_structured_data(url: str) -> dict:
    """Extract + sanity-check JSON-LD structured data on a page (required/recommended fields for common types)."""
    return web.structured_data(url)


@op("web.ai_readiness")
def web_ai_readiness(site: str) -> dict:
    """AI search visibility check: AI crawler access (OAI-SearchBot, ChatGPT-User, Claude-SearchBot, PerplexityBot...),
    llms.txt, server-rendered text, entity markup, Bing/IndexNow note."""
    return web.ai_readiness(site)


@op("web.tags")
def web_tags(url: str) -> dict:
    """Detect GA4 measurement IDs, GTM containers, legacy UA ids and google-site-verification tags on a live page."""
    return web.detect_tags(url)


# =============================================================== indexing
@op("indexnow.key", write=True)
def indexnow_key(write_to: str | None = None, regenerate: bool = False) -> dict:
    """Get/create the IndexNow key. write_to=<web root dir> writes <key>.txt there for deployment."""
    return indexing.indexnow_key(write_to, regenerate)


@op("indexnow.submit", write=True)
def indexnow_submit(urls: list[str] | None = None, sitemap: str | None = None, since: str | None = None) -> dict:
    """Ping IndexNow (Bing -> ChatGPT search/Copilot, Yandex, Seznam, Naver...) about new/changed URLs.
    Give urls, or sitemap=<url> (optionally since=YYYY-MM-DD to only send URLs with newer lastmod)."""
    targets = list(urls or [])
    if sitemap:
        for u in web.read_sitemap(sitemap)["urls"]:
            if not since or (u["lastmod"] and u["lastmod"][:10] >= since):
                targets.append(u["loc"])
    return indexing.indexnow_submit(list(dict.fromkeys(targets)))


@op("google_indexing.publish", write=True)
def google_indexing_publish(urls: list[str], deleted: bool = False) -> dict:
    """Google Indexing API URL_UPDATED/URL_DELETED notifications. Officially only for JobPosting and
    BroadcastEvent pages (200/day); for normal pages resubmit sitemaps instead."""
    return indexing.google_publish(urls, deleted)


@op("google_indexing.status")
def google_indexing_status(url: str) -> dict:
    """Last Indexing API notification Google received for a URL."""
    return indexing.google_status(url)
