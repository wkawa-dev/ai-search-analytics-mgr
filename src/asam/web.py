"""Batteries-included web tools for discoverability: page audits, crawler, robots.txt (Google-spec +
AI crawlers), sitemaps, structured data, PageSpeed Insights / Core Web Vitals, AI-search readiness."""

from __future__ import annotations

import gzip
import json
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import config
from .errors import AsamError

UA = "Mozilla/5.0 (compatible; asam-audit/0.1; +https://github.com/wkawa-dev/ai-search-analytics-mgr)"
SKIP_EXT = re.compile(r"\.(jpe?g|png|gif|webp|avif|svg|ico|pdf|zip|gz|mp4|mp3|webm|woff2?|ttf|css|js|json|xml|txt)$", re.I)

# AI crawlers. "search" ones fetch pages to answer/cite in AI search -> blocking them hurts visibility.
# "training" ones collect model-training data -> blocking is a policy choice with no ranking impact.
AI_BOTS: dict[str, str] = {
    "OAI-SearchBot": "search", "ChatGPT-User": "search", "Claude-SearchBot": "search", "Claude-User": "search",
    "PerplexityBot": "search", "Perplexity-User": "search", "DuckAssistBot": "search", "MistralAI-User": "search",
    "Applebot": "search",
    "GPTBot": "training", "ClaudeBot": "training", "Google-Extended": "training", "Applebot-Extended": "training",
    "CCBot": "training", "Bytespider": "training", "meta-externalagent": "training", "Amazonbot": "training",
}
SEARCH_BOTS = ["Googlebot", "Bingbot"]


# ============================================================ fetching
_session = None


def _sess() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": UA, "Accept-Language": "en;q=0.9,*;q=0.5"})
        adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=16)
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
    return _session


def normalize_site(site: str) -> str:
    site = site.strip()
    if site.startswith("sc-domain:"):
        site = site[len("sc-domain:"):]
    if "://" not in site:
        site = "https://" + site
    p = urlparse(site)
    return f"{p.scheme}://{p.netloc}"


def fetch(url: str, timeout: int = 20, method: str = "GET", user_agent: str | None = None) -> dict[str, Any]:
    t0 = time.time()
    headers = {"User-Agent": user_agent} if user_agent else {}
    try:
        r = _sess().request(method, url, timeout=timeout, allow_redirects=True, headers=headers)
    except requests.RequestException as e:
        return {"url": url, "status": 0, "error": str(e)[:200], "elapsed_ms": int((time.time() - t0) * 1000)}
    return {
        "url": url, "final_url": r.url, "status": r.status_code,
        "redirects": [{"url": h.url, "status": h.status_code} for h in r.history],
        "elapsed_ms": int(r.elapsed.total_seconds() * 1000) if r.elapsed else int((time.time() - t0) * 1000),
        "headers": {k.lower(): v for k, v in r.headers.items()},
        "content": r.content if method == "GET" else b"",
        "text": r.text if method == "GET" and "html" in r.headers.get("content-type", "") else None,
    }


# ============================================================ robots.txt (Google spec: longest match wins)
class Robots:
    def __init__(self, text: str):
        self.groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        self.sitemaps: list[str] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k, v = k.strip().lower(), v.strip()
            if k == "user-agent":
                if not last_was_agent and agents:
                    self.groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(v.lower())
                last_was_agent = True
            elif k in ("allow", "disallow"):
                last_was_agent = False
                if agents and v:
                    rules.append((k == "allow", v))
                elif agents and k == "disallow":
                    pass  # empty disallow = allow all
            elif k == "sitemap":
                self.sitemaps.append(v)
        if agents:
            self.groups.append((agents, rules))

    def rules_for(self, ua: str) -> tuple[str, list[tuple[bool, str]]]:
        ua_l = ua.lower()
        best, best_len = None, -1
        for agents, _ in self.groups:
            for a in agents:
                if a != "*" and ua_l.startswith(a) and len(a) > best_len:
                    best, best_len = a, len(a)
        if best is None:
            best = "*" if any("*" in ag for ag, _ in self.groups) else None
        if best is None:
            return "(none)", []
        merged: list[tuple[bool, str]] = []
        for agents, rules in self.groups:
            if best in agents:
                merged += rules
        return best, merged

    @staticmethod
    def _match(pattern: str, path: str) -> bool:
        anchored = pattern.endswith("$")
        pat = re.escape(pattern.rstrip("$")).replace(r"\*", ".*")
        return re.match(pat + ("$" if anchored else ""), path) is not None

    def can_fetch(self, ua: str, url: str) -> bool:
        p = urlparse(url)
        path = (p.path or "/") + (("?" + p.query) if p.query else "")
        _, rules = self.rules_for(ua)
        best: tuple[int, bool] | None = None
        for allow, pat in rules:
            if self._match(pat, path):
                ln = len(pat)
                if best is None or ln > best[0] or (ln == best[0] and allow):
                    best = (ln, allow)
        return True if best is None else best[1]


def get_robots(site: str) -> tuple[dict[str, Any], Robots]:
    base = normalize_site(site)
    f = fetch(base + "/robots.txt")
    text = f["content"].decode("utf-8", "replace") if f.get("status") == 200 else ""
    meta = {"url": base + "/robots.txt", "status": f.get("status"), "bytes": len(f.get("content") or b"")}
    if f.get("error"):
        meta["error"] = f["error"]
    return meta, Robots(text)


def robots_check(site: str, paths: list[str] | None = None) -> dict[str, Any]:
    """Analyse robots.txt: sitemaps declared, search + AI crawler access, rules that block important paths."""
    base = normalize_site(site)
    meta, rb = get_robots(base)
    test_paths = paths or ["/"]
    matrix = {}
    for bot in SEARCH_BOTS + list(AI_BOTS):
        group, _ = rb.rules_for(bot)
        matrix[bot] = {"kind": AI_BOTS.get(bot, "search-engine"), "group": group,
                       "allowed": {p: rb.can_fetch(bot, base + p) for p in test_paths}}
    issues = []
    if not meta["status"]:
        return {"robots": meta, "sitemaps": [], "crawlers": {}, "training_bots_blocked": [],
                "issues": [_iss("error", "SITE_UNREACHABLE", f"Could not connect to {base} (DNS/TLS/network).")]}
    if meta["status"] >= 500:
        issues.append(_iss("error", "ROBOTS_5XX", "robots.txt returns 5xx: Google treats the whole site as disallowed."))
    if meta["status"] == 404:
        issues.append(_iss("info", "ROBOTS_MISSING", "No robots.txt (everything allowed). Add one with a Sitemap: line."))
    for bot in SEARCH_BOTS:
        if not matrix[bot]["allowed"].get("/", True):
            issues.append(_iss("error", "SEARCH_BOT_BLOCKED", f"{bot} is blocked from the homepage."))
    blocked_ai_search = [b for b, k in AI_BOTS.items() if k == "search" and not matrix[b]["allowed"].get("/", True)]
    if blocked_ai_search:
        issues.append(_iss("warning", "AI_SEARCH_BOTS_BLOCKED",
                           f"AI search/answer crawlers blocked (hurts AI search visibility/citations): {blocked_ai_search}"))
    if not rb.sitemaps:
        issues.append(_iss("warning", "NO_SITEMAP_IN_ROBOTS", "robots.txt does not declare a Sitemap: URL."))
    for p in test_paths:
        if not rb.can_fetch("Googlebot", base + p):
            issues.append(_iss("warning", "PATH_BLOCKED", f"Googlebot blocked from {p}"))
    return {"robots": meta, "sitemaps": rb.sitemaps, "crawlers": matrix, "issues": issues,
            "training_bots_blocked": [b for b, k in AI_BOTS.items() if k == "training" and not matrix[b]["allowed"].get("/", True)]}


# ============================================================ page audit
def _iss(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _norm_url(u: str) -> str:
    u = urldefrag(u)[0]
    p = urlparse(u)
    return p._replace(netloc=p.netloc.lower(), path=p.path or "/").geturl()


def analyze_html(url: str, html: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    headers = headers or {}
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(url).netloc

    def meta(name=None, prop=None):
        t = soup.find("meta", attrs={"name": re.compile(f"^{name}$", re.I)} if name else {"property": prop})
        return (t.get("content") or "").strip() if t else None

    title = (soup.title.get_text(strip=True) if soup.title else None) or None
    desc = meta("description")
    robots_meta = ", ".join(filter(None, [meta("robots"), meta("googlebot")])) or None
    xrt = headers.get("x-robots-tag")
    noindex = "noindex" in (robots_meta or "").lower() or "noindex" in (xrt or "").lower()
    canon_tag = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
    canonical = urljoin(url, canon_tag["href"]) if canon_tag and canon_tag.get("href") else None
    h1 = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    hreflang = [{"lang": link.get("hreflang"), "href": link.get("href")}
                for link in soup.find_all("link", rel="alternate") if link.get("hreflang")]
    og = {p: meta(prop=f"og:{p}") for p in ("title", "description", "image", "type", "url")}
    jsonld, jsonld_errors = [], []
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or s.get_text() or "")
            jsonld.append(data)
        except json.JSONDecodeError as e:
            jsonld_errors.append(str(e)[:120])
    imgs = soup.find_all("img")
    missing_alt = [i.get("src") for i in imgs if i.get("alt") is None][:20]
    internal, external = set(), set()
    nofollow_internal = 0
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue
        absu = _norm_url(urljoin(url, href))
        if not absu.startswith("http"):
            continue
        if urlparse(absu).netloc.lower() == host.lower():
            internal.add(absu)
            if "nofollow" in (a.get("rel") or []):
                nofollow_internal += 1
        else:
            external.add(absu)
    for t in soup(["script", "style", "noscript", "template"]):
        t.decompose()
    text = soup.get_text(" ", strip=True)
    words = len(text.split())
    mixed = 0
    if url.startswith("https://"):
        mixed = len(re.findall(r'(?:src|href)=["\']http://', html, re.I))
    ga = sorted(set(re.findall(r"\b(G-[A-Z0-9]{6,12})\b", html)))
    gtm = sorted(set(re.findall(r"\b(GTM-[A-Z0-9]{4,10})\b", html)))
    ver = re.findall(r'name=["\']google-site-verification["\']\s+content=["\']([^"\']+)', html, re.I)
    types = sorted({t for d in jsonld for t in _ld_types(d)})
    lang = (BeautifulSoup(html[:5000], "html.parser").find("html") or {}).get("lang") if html else None

    issues = []
    if not title:
        issues.append(_iss("error", "TITLE_MISSING", "No <title>."))
    elif len(title) > 60:
        issues.append(_iss("warning", "TITLE_LONG", f"Title {len(title)} chars (>60, likely truncated)."))
    elif len(title) < 15:
        issues.append(_iss("warning", "TITLE_SHORT", f"Title only {len(title)} chars."))
    if not desc:
        issues.append(_iss("warning", "DESCRIPTION_MISSING", "No meta description (Google will improvise a snippet)."))
    elif len(desc) > 160:
        issues.append(_iss("info", "DESCRIPTION_LONG", f"Meta description {len(desc)} chars (>160)."))
    elif len(desc) < 50:
        issues.append(_iss("info", "DESCRIPTION_SHORT", f"Meta description {len(desc)} chars (<50)."))
    if not h1:
        issues.append(_iss("warning", "H1_MISSING", "No <h1>."))
    elif len(h1) > 1:
        issues.append(_iss("info", "H1_MULTIPLE", f"{len(h1)} <h1> tags."))
    if noindex:
        issues.append(_iss("warning", "NOINDEX", f"Page is noindex (meta: {robots_meta}, header: {xrt})."))
    if not canonical:
        issues.append(_iss("info", "CANONICAL_MISSING", "No rel=canonical."))
    elif _norm_url(canonical).rstrip("/") != _norm_url(url).rstrip("/"):
        issues.append(_iss("info", "CANONICAL_OTHER", f"Canonical points elsewhere: {canonical}"))
    if not soup.find("meta", attrs={"name": "viewport"}) and "viewport" not in html[:20000]:
        issues.append(_iss("warning", "NO_VIEWPORT", "No meta viewport (mobile-unfriendly)."))
    if not lang:
        issues.append(_iss("info", "NO_LANG", "<html> has no lang attribute."))
    if words < 200 and not noindex:
        issues.append(_iss("info", "THIN_CONTENT", f"Only ~{words} words of server-rendered text (JS-only content "
                                                  "is invisible to many AI crawlers)."))
    if missing_alt:
        issues.append(_iss("info", "IMG_ALT_MISSING", f"{len(missing_alt)}+ images without alt."))
    if jsonld_errors:
        issues.append(_iss("error", "JSONLD_INVALID", f"Invalid JSON-LD: {jsonld_errors[0]}"))
    if not jsonld:
        issues.append(_iss("info", "NO_STRUCTURED_DATA", "No JSON-LD structured data."))
    if not og.get("title") or not og.get("image"):
        issues.append(_iss("info", "OG_INCOMPLETE", "Open Graph title/image missing (poor social/AI previews)."))
    if mixed:
        issues.append(_iss("warning", "MIXED_CONTENT", f"{mixed} http:// resources on an https page."))
    issues += validate_structured_data(jsonld)

    return {
        "title": title, "title_length": len(title or ""), "description": desc, "description_length": len(desc or ""),
        "h1": h1[:5], "canonical": canonical, "robots_meta": robots_meta, "x_robots_tag": xrt, "noindex": noindex,
        "lang": lang, "hreflang": hreflang, "og": og, "twitter_card": meta("twitter:card"),
        "structured_data_types": types, "word_count": words, "images": len(imgs), "images_missing_alt": len(missing_alt),
        "internal_links": len(internal), "external_links": len(external), "nofollow_internal_links": nofollow_internal,
        "ga4_ids": ga, "gtm_ids": gtm, "google_site_verification": ver,
        "issues": issues, "_internal": sorted(internal), "_jsonld": jsonld,
    }


def _ld_types(d: Any) -> list[str]:
    out: list[str] = []
    if isinstance(d, list):
        for x in d:
            out += _ld_types(x)
    elif isinstance(d, dict):
        t = d.get("@type")
        if isinstance(t, list):
            out += [str(x) for x in t]
        elif t:
            out.append(str(t))
        for x in d.get("@graph", []) or []:
            out += _ld_types(x)
    return out


REQUIRED_LD = {
    "Article": ["headline", "image", "datePublished", "author"],
    "BlogPosting": ["headline", "image", "datePublished", "author"],
    "NewsArticle": ["headline", "image", "datePublished", "author"],
    "Product": ["name", "offers|review|aggregateRating"],
    "Organization": ["name", "url", "logo"],
    "LocalBusiness": ["name", "address"],
    "BreadcrumbList": ["itemListElement"],
    "FAQPage": ["mainEntity"],
    "Recipe": ["name", "image"],
    "Event": ["name", "startDate", "location"],
    "JobPosting": ["title", "description", "datePosted", "hiringOrganization", "jobLocation|applicantLocationRequirements"],
    "WebSite": ["name", "url"],
    "SoftwareApplication": ["name", "offers|aggregateRating|review"],
    "VideoObject": ["name", "thumbnailUrl", "uploadDate"],
}


def validate_structured_data(items: list[Any]) -> list[dict[str, str]]:
    issues = []

    def walk(d):
        if isinstance(d, list):
            for x in d:
                walk(x)
            return
        if not isinstance(d, dict):
            return
        for x in d.get("@graph", []) or []:
            walk(x)
        types = d.get("@type")
        for t in types if isinstance(types, list) else [types]:
            for req in REQUIRED_LD.get(str(t), []):
                if not any(k in d for k in req.split("|")):
                    issues.append(_iss("warning", "JSONLD_MISSING_FIELD", f"{t} missing recommended '{req}'."))

    walk(items)
    return issues


def audit_page(url: str, user_agent: str | None = None) -> dict[str, Any]:
    """Fetch one URL and audit on-page SEO, indexability, structured data and tags."""
    if "://" not in url:
        url = "https://" + url
    f = fetch(url, user_agent=user_agent)
    out: dict[str, Any] = {k: f.get(k) for k in ("url", "final_url", "status", "redirects", "elapsed_ms", "error")}
    h = f.get("headers") or {}
    out["content_type"] = h.get("content-type")
    out["bytes"] = len(f.get("content") or b"")
    issues = []
    if f.get("status", 0) >= 400 or f.get("status") == 0:
        issues.append(_iss("error", "HTTP_ERROR", f"HTTP {f.get('status')} {f.get('error') or ''}".strip()))
    if len(f.get("redirects") or []) > 1:
        issues.append(_iss("warning", "REDIRECT_CHAIN", f"{len(f['redirects'])} redirects before final URL."))
    if (f.get("elapsed_ms") or 0) > 1500:
        issues.append(_iss("info", "SLOW_RESPONSE", f"Server responded in {f['elapsed_ms']} ms."))
    if f.get("text"):
        a = analyze_html(f["final_url"], f["text"], h)
        issues += a.pop("issues")
        a.pop("_internal")
        a.pop("_jsonld")
        out.update(a)
    out["issues"] = issues
    out["score"] = _score(issues)
    return out


def _score(issues: list[dict]) -> int:
    w = {"error": 15, "warning": 5, "info": 1}
    return max(0, 100 - sum(w.get(i["severity"], 0) for i in issues))


def structured_data(url: str) -> dict[str, Any]:
    f = fetch(url if "://" in url else "https://" + url)
    if not f.get("text"):
        raise AsamError("NOT_HTML", f"{url} returned no HTML (status {f.get('status')}).")
    a = analyze_html(f["final_url"], f["text"], f.get("headers"))
    return {"url": f["final_url"], "types": a["structured_data_types"], "items": a["_jsonld"],
            "issues": [i for i in a["issues"] if i["code"].startswith("JSONLD") or i["code"] == "NO_STRUCTURED_DATA"],
            "validator": f"https://search.google.com/test/rich-results?url={url}"}


# ============================================================ sitemaps
def _xml_root(content: bytes) -> ET.Element:
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    content = re.sub(rb"\sxmlns(:\w+)?=\"[^\"]+\"", b"", content, count=0)
    return ET.fromstring(content)


def read_sitemap(url: str, max_sitemaps: int = 50) -> dict[str, Any]:
    seen, queue = [], [url]
    urls: list[dict[str, Any]] = []
    errors = []
    while queue and len(seen) < max_sitemaps:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.append(sm)
        f = fetch(sm)
        if f.get("status") != 200:
            errors.append({"sitemap": sm, "status": f.get("status")})
            continue
        try:
            root = _xml_root(f["content"])
        except (ET.ParseError, OSError) as e:
            errors.append({"sitemap": sm, "error": f"XML parse error: {e}"})
            continue
        tag = root.tag.split("}")[-1].split(":")[-1]
        if tag == "sitemapindex":
            queue += [(e.findtext("loc") or "").strip() for e in root.findall("sitemap") if e.findtext("loc")]
        else:
            for e in root.findall("url"):
                loc = (e.findtext("loc") or "").strip()
                if loc:
                    urls.append({"loc": loc, "lastmod": (e.findtext("lastmod") or "").strip() or None, "sitemap": sm})
    return {"sitemaps_read": seen, "errors": errors, "urls": urls, "truncated": bool(queue)}


def discover_sitemaps(site: str) -> list[str]:
    base = normalize_site(site)
    _, rb = get_robots(base)
    if rb.sitemaps:
        return rb.sitemaps
    for p in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml", "/sitemap-index.xml"):
        f = fetch(base + p)
        if f.get("status") == 200 and b"<" in (f.get("content") or b"")[:200]:
            return [base + p]
    return []


def sitemap_check(target: str, sample: int = 25) -> dict[str, Any]:
    """Validate sitemap(s): discovery, structure, duplicates, off-host URLs, lastmod coverage, and a live sample
    of URLs for non-200 / redirects / noindex / canonical-elsewhere (URLs that should not be in a sitemap)."""
    sitemaps = [target] if target.endswith((".xml", ".xml.gz")) or "sitemap" in target else discover_sitemaps(target)
    if not sitemaps:
        return {"sitemaps": [], "issues": [_iss("error", "NO_SITEMAP", "No sitemap found (robots.txt, /sitemap.xml...).")]}
    all_urls, errors, read = [], [], []
    truncated = False
    for s in sitemaps:
        r = read_sitemap(s)
        all_urls += r["urls"]
        errors += r["errors"]
        read += r["sitemaps_read"]
        truncated = truncated or r["truncated"]
    locs = [u["loc"] for u in all_urls]
    host = urlparse(normalize_site(target if "://" in target else "https://" + target)).netloc
    dupes = [u for u, c in Counter(locs).items() if c > 1]
    off = [u for u in locs if urlparse(u).netloc.lower().removeprefix("www.") != host.lower().removeprefix("www.")]
    lastmods = sorted(u["lastmod"][:10] for u in all_urls if u["lastmod"])
    step = max(1, len(locs) // max(1, sample))
    sample_urls = locs[::step][:sample]

    def chk(u):
        f = fetch(u)
        res = {"url": u, "status": f.get("status"), "final_url": f.get("final_url")}
        if f.get("text"):
            a = analyze_html(f["final_url"], f["text"], f.get("headers"))
            res["noindex"] = a["noindex"]
            res["canonical"] = a["canonical"]
        return res

    with ThreadPoolExecutor(8) as ex:
        checked = list(ex.map(chk, sample_urls))
    issues = []
    for e in errors:
        issues.append(_iss("error", "SITEMAP_FETCH_ERROR", json.dumps(e)))
    if dupes:
        issues.append(_iss("warning", "SITEMAP_DUPLICATES", f"{len(dupes)} duplicate URLs."))
    if off:
        issues.append(_iss("warning", "SITEMAP_OFF_HOST", f"{len(off)} URLs on another host (e.g. {off[0]})."))
    bad = [c for c in checked if c["status"] != 200 or (c.get("final_url") and _norm_url(c["final_url"]) != _norm_url(c["url"]))]
    if bad:
        issues.append(_iss("error", "SITEMAP_NON_200", f"{len(bad)}/{len(checked)} sampled URLs are not 200 or redirect."))
    ni = [c for c in checked if c.get("noindex")]
    if ni:
        issues.append(_iss("error", "SITEMAP_NOINDEX", f"{len(ni)} sampled URLs are noindex."))
    co = [c for c in checked if c.get("canonical") and _norm_url(c["canonical"]).rstrip("/") != _norm_url(c["url"]).rstrip("/")]
    if co:
        issues.append(_iss("warning", "SITEMAP_CANONICAL_ELSEWHERE", f"{len(co)} sampled URLs canonicalize elsewhere."))
    if truncated:
        issues.append(_iss("info", "SITEMAP_PARTIAL", "Large sitemap index: only the first 50 child sitemaps were read."))
    if locs and len(lastmods) < len(locs) * 0.5:
        issues.append(_iss("info", "SITEMAP_LASTMOD_SPARSE", "Most URLs lack <lastmod> (helps recrawl prioritization)."))
    return {"sitemaps_count": len(read), "sitemaps": read[:20], "url_count": len(locs), "duplicates": len(dupes), "off_host": len(off),
            "lastmod": {"with": len(lastmods), "oldest": lastmods[0] if lastmods else None,
                        "newest": lastmods[-1] if lastmods else None},
            "sample_checked": checked, "issues": issues}


# ============================================================ crawler
def crawl(start_url: str, max_pages: int = 100, workers: int = 8, respect_robots: bool = True,
          include_sitemap: bool = True, save: bool = True) -> dict[str, Any]:
    """Bounded same-host crawl + site-wide SEO audit (duplicates, broken links, noindex, thin, depth, orphans)."""
    base = normalize_site(start_url)
    start = start_url if start_url.startswith("http") else base + "/"
    first = fetch(start)
    if first.get("final_url"):
        base = normalize_site(first["final_url"])
        start = first["final_url"]
    host = urlparse(base).netloc.lower()
    rb = get_robots(base)[1] if respect_robots else Robots("")
    sm_urls: set[str] = set()
    sm_complete = include_sitemap
    if include_sitemap:
        for s in discover_sitemaps(base):
            rs = read_sitemap(s, 20)
            sm_urls |= {_norm_url(u["loc"]) for u in rs["urls"]}
            sm_complete = sm_complete and not rs["truncated"]
    pages: dict[str, dict[str, Any]] = {}
    depth = {_norm_url(start): 0}
    inlinks: dict[str, set[str]] = defaultdict(set)
    frontier = [_norm_url(start)]
    blocked = []

    def visit(u: str) -> tuple[str, dict[str, Any], list[str]]:
        f = fetch(u)
        rec: dict[str, Any] = {"url": u, "status": f.get("status"), "final_url": f.get("final_url"),
                               "elapsed_ms": f.get("elapsed_ms"), "redirects": len(f.get("redirects") or [])}
        links: list[str] = []
        if f.get("text") and f.get("status") == 200:
            a = analyze_html(f["final_url"], f["text"], f.get("headers"))
            links = a.pop("_internal")
            a.pop("_jsonld")
            rec.update({k: a[k] for k in ("title", "description", "h1", "canonical", "noindex", "word_count",
                                          "structured_data_types", "issues")})
        return u, rec, links

    with ThreadPoolExecutor(workers) as ex:
        while frontier and len(pages) < max_pages:
            batch = []
            for u in frontier:
                if u in pages or len(pages) + len(batch) >= max_pages:
                    continue
                if respect_robots and not rb.can_fetch("Googlebot", u):
                    if u not in blocked:
                        blocked.append(u)
                    continue
                batch.append(u)
            frontier = []
            for u, rec, links in ex.map(visit, batch):
                pages[u] = rec
                for link in links:
                    if urlparse(link).netloc.lower() != host or SKIP_EXT.search(urlparse(link).path):
                        continue
                    inlinks[link].add(u)
                    if link not in depth:
                        depth[link] = depth[u] + 1
                        frontier.append(link)
    # link-target status for links pointing at pages we fetched
    broken, redirected = [], []
    for target, sources in inlinks.items():
        p = pages.get(target)
        if not p or not isinstance(p.get("status"), int):
            continue
        if p["status"] >= 400 or p["status"] == 0:
            broken.append({"target": target, "status": p["status"], "linked_from": sorted(sources)[:5]})
        elif p.get("redirects"):
            redirected.append({"target": target, "final": p.get("final_url"), "linked_from": sorted(sources)[:3]})
    ok = [p for p in pages.values() if "issues" in p]
    titles = defaultdict(list)
    descs = defaultdict(list)
    for p in ok:
        if p.get("title"):
            titles[p["title"]].append(p["url"])
        if p.get("description"):
            descs[p["description"]].append(p["url"])
    dup_titles = {t: us for t, us in titles.items() if len(us) > 1}
    dup_descs = {d: us for d, us in descs.items() if len(us) > 1}
    issue_counts = Counter(i["code"] for p in ok for i in p.get("issues", []))
    crawled = set(pages)
    orphans = sorted(sm_urls - crawled - set(inlinks))[:50] if len(pages) < max_pages and sm_complete else []
    not_in_sitemap = sorted(u for u in crawled if pages[u].get("status") == 200 and not pages[u].get("noindex")
                            and sm_complete and sm_urls and u not in sm_urls)[:50]
    summary = {
        "site": base, "pages_crawled": len(pages), "hit_limit": len(pages) >= max_pages,
        "status_codes": dict(Counter(str(p.get("status")) for p in pages.values())),
        "issue_counts": dict(issue_counts.most_common()),
        "duplicate_titles": len(dup_titles), "duplicate_descriptions": len(dup_descs),
        "broken_internal_links": len(broken), "internal_redirects": len(redirected),
        "noindex_pages": sum(1 for p in pages.values() if p.get("noindex")),
        "blocked_by_robots": len(blocked), "blocked_by_robots_sample": blocked[:10],
        "max_depth": max(depth.get(u, 0) for u in pages) if pages else 0,
        "deep_pages_gt3": sum(1 for u in pages if depth.get(u, 0) > 3),
        "sitemap_urls": len(sm_urls), "sitemap_fully_read": sm_complete, "sitemap_orphans_sample": orphans, "indexable_not_in_sitemap": not_in_sitemap,
        "avg_response_ms": int(sum(p.get("elapsed_ms") or 0 for p in pages.values()) / max(1, len(pages))),
    }
    result = {"summary": summary, "broken_links": broken[:100], "internal_redirects": redirected[:100],
              "duplicate_titles": dict(list(dup_titles.items())[:30]),
              "duplicate_descriptions": dict(list(dup_descs.items())[:30]),
              "worst_pages": sorted(({"url": p["url"], "score": _score(p.get("issues", [])),
                                      "issues": [i["code"] for i in p.get("issues", [])]} for p in ok),
                                    key=lambda x: x["score"])[:25]}
    if save:
        path = config.reports_dir() / f"crawl-{host}-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps({**result, "pages": list(pages.values()),
                                    "depth": depth, "inlinks": {k: sorted(v) for k, v in inlinks.items()}}, indent=1))
        result["full_results_file"] = str(path)
    return result


def generate_sitemap(start_url: str, max_pages: int = 500, out_file: str | None = None) -> dict[str, Any]:
    """Crawl the site and write a sitemap.xml of indexable, self-canonical 200 pages."""
    res = crawl(start_url, max_pages=max_pages, include_sitemap=False, save=True)
    data = json.loads(Path(res["full_results_file"]).read_text())
    keep = []
    for p in data["pages"]:
        if p.get("status") != 200 or p.get("noindex"):
            continue
        c = p.get("canonical")
        if c and _norm_url(c).rstrip("/") != _norm_url(p["url"]).rstrip("/"):
            continue
        keep.append(p.get("final_url") or p["url"])
    keep = sorted(set(keep))
    today = time.strftime("%Y-%m-%d")
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in keep:
        esc = u.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        xml.append(f"  <url><loc>{esc}</loc><lastmod>{today}</lastmod></url>")
    xml.append("</urlset>")
    out = Path(out_file or (config.reports_dir() / "sitemap.xml"))
    out.write_text("\n".join(xml) + "\n")
    return {"written": str(out), "url_count": len(keep), "crawled": res["summary"]["pages_crawled"],
            "next": "Deploy it to your web root, add `Sitemap: <url>` to robots.txt, then `asam gsc sitemap-submit`."}


# ============================================================ PageSpeed Insights / CWV
PSI = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def pagespeed(url: str, strategy: str = "mobile") -> dict[str, Any]:
    """Lighthouse lab scores + real-user Core Web Vitals (CrUX field data) + top fixes."""
    if "://" not in url:
        url = "https://" + url
    params: list[tuple[str, str]] = [("url", url), ("strategy", strategy)]
    params += [("category", c) for c in ("performance", "seo", "accessibility", "best-practices")]
    key = config.get("google_api_key")
    if key:
        params.append(("key", key))
    r = None
    for attempt in range(3):
        r = requests.get(PSI, params=params, timeout=150)
        if r.status_code != 429 and r.status_code < 500:
            break
        time.sleep(3 * (attempt + 1))
    assert r is not None
    if r.status_code != 200:
        msg = r.text[:300]
        fix = "Set an API key for more quota: `asam config set google_api_key <key>` (APIs & Services > Credentials)."
        raise AsamError("PSI_FAILED", f"PageSpeed Insights HTTP {r.status_code}: {msg}", fix=fix)
    j = r.json()
    lh = j.get("lighthouseResult", {})
    cats = {k: round((v.get("score") or 0) * 100) for k, v in lh.get("categories", {}).items()}
    audits = lh.get("audits", {})
    lab = {k: audits.get(k, {}).get("displayValue") for k in
           ("first-contentful-paint", "largest-contentful-paint", "total-blocking-time", "cumulative-layout-shift",
            "speed-index", "server-response-time")}
    opps = []
    for k, a in audits.items():
        det = a.get("details") or {}
        if det.get("type") == "opportunity" and (a.get("score") is not None and a["score"] < 0.9):
            opps.append({"id": k, "title": a.get("title"), "savings_ms": int(det.get("overallSavingsMs") or 0),
                         "display": a.get("displayValue")})
    opps.sort(key=lambda x: -x["savings_ms"])
    failed = []
    for cat in ("seo", "accessibility", "best-practices"):
        for ref in lh.get("categories", {}).get(cat, {}).get("auditRefs", []):
            a = audits.get(ref["id"], {})
            if a.get("score") == 0 and ref.get("weight", 0) > 0:
                failed.append({"category": cat, "id": ref["id"], "title": a.get("title")})

    def field(le):
        if not le or not le.get("metrics"):
            return None
        return {"overall": le.get("overall_category"), **{
            k.replace("_MS", "").replace("EXPERIMENTAL_", "").lower(): {"p75": v.get("percentile"), "rating": v.get("category")}
            for k, v in le["metrics"].items()}}

    return {"url": url, "strategy": strategy, "scores": cats, "lab": lab,
            "field_page": field(j.get("loadingExperience")), "field_origin": field(j.get("originLoadingExperience")),
            "opportunities": opps[:10], "failed_audits": failed[:25]}


# ============================================================ AI-search readiness
def ai_readiness(site: str) -> dict[str, Any]:
    """How visible/consumable is the site for AI search (ChatGPT, Claude, Perplexity, Gemini, Copilot)?"""
    base = normalize_site(site)
    rc = robots_check(base)
    llms = fetch(base + "/llms.txt")
    llms_full = fetch(base + "/llms-full.txt", method="HEAD")
    home = audit_page(base + "/")
    issues = [i for i in rc["issues"] if i["code"] in ("AI_SEARCH_BOTS_BLOCKED", "SEARCH_BOT_BLOCKED", "ROBOTS_5XX")]
    has_llms = llms.get("status") == 200 and b"<html" not in (llms.get("content") or b"")[:500].lower()
    if not has_llms:
        issues.append(_iss("info", "NO_LLMS_TXT", "No /llms.txt (optional, emerging convention; a curated markdown "
                                                  "index of key pages for LLMs)."))
    if home.get("word_count", 0) < 150:
        issues.append(_iss("warning", "LOW_SSR_TEXT", "Homepage has little server-rendered text; most AI crawlers "
                                                      "don't run JavaScript. Use SSR/SSG for key content."))
    if not home.get("structured_data_types"):
        issues.append(_iss("warning", "NO_ENTITY_MARKUP", "No JSON-LD on homepage: add Organization/WebSite (+ sameAs) "
                                                          "so engines can resolve your entity."))
    if not home.get("description"):
        issues.append(_iss("info", "NO_DESCRIPTION", "Homepage lacks meta description."))
    ai_bots = {b: v["allowed"].get("/") for b, v in rc["crawlers"].items() if b in AI_BOTS}
    if home.get("status") == 0:
        issues.append(_iss("error", "SITE_UNREACHABLE", f"Could not fetch {base}/"))
    return {"site": base, "ai_crawlers_allowed": ai_bots, "training_bots_blocked": rc["training_bots_blocked"],
            "llms_txt": has_llms, "llms_full_txt": llms_full.get("status") == 200,
            "homepage": {k: home.get(k) for k in ("status", "word_count", "structured_data_types", "title", "og")},
            "bing_note": "ChatGPT search & Copilot rely heavily on Bing's index: submit via IndexNow "
                         "(`asam indexnow submit`) and verify in Bing Webmaster Tools (can import from GSC).",
            "issues": issues}


def detect_tags(url: str) -> dict[str, Any]:
    f = fetch(url if "://" in url else "https://" + url)
    html = f.get("text") or ""
    return {"url": f.get("final_url") or url, "status": f.get("status"),
            "ga4_measurement_ids": sorted(set(re.findall(r"\b(G-[A-Z0-9]{6,12})\b", html))),
            "gtm_containers": sorted(set(re.findall(r"\b(GTM-[A-Z0-9]{4,10})\b", html))),
            "google_site_verification": re.findall(
                r'name=["\']google-site-verification["\']\s+content=["\']([^"\']+)', html, re.I),
            "ua_legacy_ids": sorted(set(re.findall(r"\b(UA-\d{4,10}-\d{1,4})\b", html)))}
