"""Hands-off onboarding: get a site into Search Console (verified, programmatically) and GA4
(property + web stream + tag), then keep it wired (sitemaps, IndexNow)."""

from __future__ import annotations

from typing import Any

from . import config, ga4, gsc, web
from .errors import AsamError
from .http import api

SV = "https://www.googleapis.com/siteVerification/v1"
METHODS = {"META", "FILE", "DNS_TXT", "DNS_CNAME", "ANALYTICS", "TAG_MANAGER"}


def _ids(domain: str) -> tuple[str, str, str]:
    dom = gsc.bare_domain(domain)
    home = web.normalize_site(domain) + "/"
    return dom, home, f"sc-domain:{dom}"


def verification_token(domain: str, method: str = "META") -> dict[str, Any]:
    method = method.upper()
    if method not in METHODS:
        raise AsamError("BAD_METHOD", f"method {method}", fix=f"Use one of {sorted(METHODS)}")
    dom, home, _ = _ids(domain)
    site = {"type": "INET_DOMAIN", "identifier": dom} if method.startswith("DNS") else {"type": "SITE", "identifier": home}
    tok = api("POST", f"{SV}/token", json_body={"site": site, "verificationMethod": method})
    token = tok.get("token")
    out: dict[str, Any] = {"method": method, "site": site, "token": token}
    if method == "META":
        out["do"] = f"Add this inside <head> of {home} (every page is fine), deploy: {token}"
    elif method == "FILE":
        out["do"] = f"Create file '{token}' in the web root containing exactly: google-site-verification: {token}"
        out["file"] = {"name": token, "content": f"google-site-verification: {token}"}
    elif method == "DNS_TXT":
        out["do"] = f"Add a DNS TXT record on {dom} (host @) with value: {token}"
        out["dns"] = {"type": "TXT", "name": dom, "value": token}
    elif method == "ANALYTICS":
        out["do"] = "The site's homepage must already load the GA4 tag of a property this account can edit."
    out["then"] = f"asam setup verify {dom} --method {method}"
    return out


def verify(domain: str, method: str = "META", add_to_search_console: bool = True,
           submit_sitemaps: bool = True) -> dict[str, Any]:
    method = method.upper()
    dom, home, domain_prop = _ids(domain)
    site = {"type": "INET_DOMAIN", "identifier": dom} if method.startswith("DNS") else {"type": "SITE", "identifier": home}
    try:
        res = api("POST", f"{SV}/webResource", params={"verificationMethod": method}, json_body={"site": site})
    except AsamError as e:
        if e.code in ("BAD_REQUEST", "HTTP_400"):
            live = web.detect_tags(home)
            raise AsamError("VERIFICATION_FAILED", f"Google could not verify {site['identifier']} via {method}: {e.message}",
                            fix=("Make sure the token is deployed and publicly reachable (DNS may take minutes-hours), "
                                 "then retry. Get the token again with `asam setup token`."),
                            details={"live_page_tags": live}) from e
        raise
    out: dict[str, Any] = {"verified": True, "resource": res.get("id"), "owners": res.get("owners")}
    prop = domain_prop if method.startswith("DNS") else home
    if add_to_search_console:
        gsc.add_site(prop)
        out["search_console_property"] = prop
        if not config.get("default_site"):
            config.set_value("default_site", prop)
    if submit_sitemaps and add_to_search_console:
        submitted = []
        for sm in web.discover_sitemaps(home):
            try:
                gsc.submit_sitemap(sm, prop)
                submitted.append(sm)
            except AsamError as e:
                submitted.append({"sitemap": sm, "error": e.code})
        out["sitemaps_submitted"] = submitted
    return out


def setup_site(domain: str, create_ga4: bool = True, ga4_account: str | None = None,
               method: str = "META") -> dict[str, Any]:
    """Idempotent: inspect what exists for `domain` in GSC + GA4 + on the live site, create what's missing,
    and return an ordered list of actions (code edits / DNS / commands) for the agent to perform."""
    dom, home, domain_prop = _ids(domain)
    out: dict[str, Any] = {"domain": dom, "homepage": home, "actions": []}
    acts = out["actions"]

    # --- Search Console
    try:
        prop = gsc.resolve_site(dom)
        perm = next((s.get("permissionLevel") for s in gsc.list_sites() if s["siteUrl"] == prop), None)
        out["search_console"] = {"property": prop, "permission": perm}
        if perm in ("siteRestrictedUser", "siteUnverifiedUser"):
            acts.append({"why": "Current access is read-limited", "do": "verify ownership",
                         **verification_token(dom, method)})
    except AsamError as e:
        if e.code not in ("SITE_NOT_FOUND",):
            raise
        out["search_console"] = {"property": None}
        tok = verification_token(dom, method)
        acts.append({"why": "Site not in Search Console", "do": tok["do"], "token": tok["token"],
                     **({"file": tok["file"]} if "file" in tok else {}), **({"dns": tok["dns"]} if "dns" in tok else {})})
        acts.append({"why": "Verify + add property + submit sitemaps", "run": tok["then"]})
        if method != "DNS_TXT":
            acts.append({"why": "Optional: a Domain property (all subdomains + protocols) needs DNS",
                         "optional": True, "run": f"asam setup token {dom} --method DNS_TXT"})

    # --- GA4
    live = web.detect_tags(home)
    out["live_site"] = live
    try:
        p = ga4.resolve_property(dom)
        streams = ga4.list_streams(p)
        mids = [(s.get("webStreamData") or {}).get("measurementId") for s in streams if s.get("webStreamData")]
        out["ga4"] = {"property": p, "measurement_ids": mids}
        if mids and not set(mids) & set(live["ga4_measurement_ids"]) and not live["gtm_containers"]:
            acts.append({"why": "GA4 tag not detected on homepage", "do": "Add to <head> of every page, deploy",
                         "html": ga4.gtag_snippet(mids[0])})
    except AsamError as e:
        if e.code != "PROPERTY_NOT_FOUND":
            raise
        if create_ga4:
            created = ga4.create_property(display_name=dom, account=ga4_account, url=home)
            mid = created["stream"]["measurement_id"]
            out["ga4"] = {"property": created["property"]["name"], "measurement_ids": [mid], "created": True,
                          "data_retention": created["data_retention"]}
            acts.append({"why": "New GA4 property created", "do": "Add to <head> of every page, deploy",
                         "html": ga4.gtag_snippet(mid)})
        else:
            out["ga4"] = {"property": None}
            acts.append({"why": "No GA4 property for this domain", "run": f"asam setup site {dom} --create-ga4"})
    if out.get("ga4", {}).get("property") and not config.get("default_property"):
        config.set_value("default_property", out["ga4"]["property"].split("/")[-1])

    # --- discoverability basics
    rc = web.robots_check(home)
    if not rc["sitemaps"] and not web.discover_sitemaps(home):
        acts.append({"why": "No sitemap found", "do": "Generate one (framework plugin or `asam web sitemap-generate`), "
                     "serve at /sitemap.xml and add `Sitemap: https://" + dom + "/sitemap.xml` to robots.txt"})
    if not config.get("indexnow_key"):
        k = web_indexnow_key()
        acts.append({"why": "IndexNow gets Bing/ChatGPT-search/Copilot/Yandex to recrawl instantly",
                     "do": f"Serve file /{k['key_file_name']} with content {k['key']} from the web root", "optional": True})
    acts.append({"why": "Search Console <-> GA4 link has no API", "optional": True,
                 "human": "GA4 Admin > Product links > Search Console links (asam already joins both datasets itself)"})
    out["done"] = not [a for a in acts if not a.get("optional")]
    return out


def web_indexnow_key() -> dict[str, Any]:
    from .indexing import indexnow_key

    return indexnow_key()


def status(domain: str) -> dict[str, Any]:
    """One-glance health of a site's wiring: GSC access, sitemaps, GA4 property + tag, robots, IndexNow."""
    dom, home, _ = _ids(domain)
    out: dict[str, Any] = {"domain": dom}
    try:
        prop = gsc.resolve_site(dom)
        sms = gsc.list_sitemaps(prop)["sitemaps"]
        out["search_console"] = {"property": prop, "sitemaps": [
            {"path": s.get("path"), "last_downloaded": s.get("lastDownloaded"), "errors": s.get("errors"),
             "warnings": s.get("warnings"), "pending": s.get("isPending")} for s in sms]}
    except AsamError as e:
        out["search_console"] = {"error": e.to_dict()}
    try:
        p = ga4.resolve_property(dom)
        out["ga4"] = {"property": p, "measurement_ids": [
            (s.get("webStreamData") or {}).get("measurementId") for s in ga4.list_streams(p)]}
    except AsamError as e:
        out["ga4"] = {"error": e.to_dict()}
    out["live_tags"] = web.detect_tags(home)
    rc = web.robots_check(home)
    out["robots"] = {"sitemaps": rc["sitemaps"], "issues": rc["issues"]}
    out["indexnow_key_configured"] = bool(config.get("indexnow_key"))
    return out
