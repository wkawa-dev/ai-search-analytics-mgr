"""Authenticated Google API calls with retry/backoff, response caching, and error -> fix mapping."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

import requests

from . import auth, config
from .errors import AsamError

SERVICE_NAMES = {
    "webmasters": "searchconsole.googleapis.com",
    "searchconsole": "searchconsole.googleapis.com",
    "analyticsadmin": "analyticsadmin.googleapis.com",
    "analyticsdata": "analyticsdata.googleapis.com",
    "siteVerification": "siteverification.googleapis.com",
    "indexing": "indexing.googleapis.com",
    "pagespeedonline": "pagespeedonline.googleapis.com",
    "chromeuxreport": "chromeuxreport.googleapis.com",
}

RETRY_STATUS = {429, 500, 502, 503, 504}


def _cache_file(key: str):
    return config.cache_dir() / (hashlib.sha256(key.encode()).hexdigest()[:32] + ".json")


def _cache_get(key: str) -> Any:
    ttl = int(config.get("cache_ttl_seconds", 21600) or 0)
    if ttl <= 0:
        return None
    f = _cache_file(key)
    if f.exists() and time.time() - f.stat().st_mtime < ttl:
        try:
            return json.loads(f.read_text())
        except json.JSONDecodeError:
            return None
    return None


def _cache_put(key: str, value: Any) -> None:
    if int(config.get("cache_ttl_seconds", 21600) or 0) > 0:
        try:
            _cache_file(key).write_text(json.dumps(value))
        except OSError:
            pass


def clear_cache() -> int:
    n = 0
    for f in config.cache_dir().glob("*.json"):
        f.unlink(missing_ok=True)
        n += 1
    return n


def api(method: str, url: str, *, json_body: Any = None, params: dict | None = None, cache: bool = False,
        authed: bool = True, timeout: int = 120) -> Any:
    key = f"{method} {url} {json.dumps(params, sort_keys=True)} {json.dumps(json_body, sort_keys=True)}"
    if cache:
        hit = _cache_get(key)
        if hit is not None:
            return hit
    sess = auth.session() if authed else requests.Session()
    delay = 1.0
    for attempt in range(6):
        try:
            r = sess.request(method, url, json=json_body, params=params, timeout=timeout)
        except requests.RequestException as e:
            if attempt == 5:
                raise AsamError("NETWORK_ERROR", f"{method} {url}: {e}") from e
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code in RETRY_STATUS and attempt < 5:
            ra = r.headers.get("Retry-After")
            time.sleep(float(ra) if ra and ra.isdigit() else delay)
            delay = min(delay * 2, 30)
            continue
        break
    if r.status_code >= 400:
        raise map_error(r, method, url)
    if not r.content:
        out: Any = {}
    else:
        try:
            out = r.json()
        except ValueError:
            out = {"raw": r.text}
    if cache:
        _cache_put(key, out)
    return out


def map_error(r: requests.Response, method: str, url: str) -> AsamError:
    try:
        err = r.json().get("error", {})
    except ValueError:
        err = {"message": r.text[:500]}
    if isinstance(err, str):  # oauth-style
        err = {"message": err}
    msg = err.get("message") or r.reason
    status = err.get("status") or str(r.status_code)
    details = err.get("details") or []
    reason = ""
    activation = ""
    for d in details:
        if isinstance(d, dict):
            reason = d.get("reason") or reason
            md = d.get("metadata") or {}
            activation = md.get("activationUrl") or activation
            for link in d.get("links", []) or []:
                if "console" in link.get("url", "") and not activation:
                    activation = link["url"]
    host = re.sub(r"^https://([^/]+).*$", r"\1", url)
    api_name = next((v for k, v in SERVICE_NAMES.items() if k in url), host)

    if reason == "SERVICE_DISABLED" or "has not been used in project" in msg or "is disabled" in msg:
        proj = re.search(r"project (\d+)", msg)
        return AsamError(
            "API_NOT_ENABLED",
            f"{api_name} is not enabled for the Google Cloud project behind your credentials.",
            fix=(f"Open {activation} and click Enable (wait ~1 min), or run "
                 f"`gcloud services enable {api_name}" + (f" --project {proj.group(1)}`" if proj else "`")
                 + ", or `asam auth enable-apis` if you logged in with --cloud."),
            details={"api": api_name, "activation_url": activation, "google_message": msg},
        )
    if r.status_code == 401:
        return AsamError("UNAUTHENTICATED", msg, fix="Run `asam auth login` (or `asam auth status` to diagnose).")
    if r.status_code == 403 and ("insufficient" in msg.lower() and "scope" in msg.lower()):
        return AsamError("INSUFFICIENT_SCOPES", msg, fix="Re-run `asam auth login` to grant all scopes.")
    if r.status_code == 403:
        who = ""
        try:
            who = auth.identity().get("email") or ""
        except Exception:
            pass
        fix = "The authenticated identity lacks access to this resource."
        if "webmasters" in url or "searchconsole" in url or "urlInspection" in url or "indexing" in url:
            fix += (f" Add {who or 'the account'} as Owner/Full user in Search Console > Settings > Users and "
                    "permissions, or verify it with `asam setup verify` (makes it an owner).")
        elif "analytics" in url:
            fix += (f" Grant {who or 'the account'} Viewer (reads) or Editor (writes) in GA4 Admin > "
                    "Property access management, or run `asam ga4 grant-access` as an admin.")
        return AsamError("PERMISSION_DENIED", msg, fix=fix, details={"identity": who, "url": url})
    if r.status_code == 404:
        return AsamError("NOT_FOUND", msg, fix="Check the id/URL. List what you can access: `asam sites` / `asam ga4 properties`.",
                         details={"url": url})
    if r.status_code == 429:
        return AsamError("QUOTA_EXCEEDED", msg, fix="Quota exhausted; retry later or narrow the query (fewer rows/days).")
    if r.status_code == 400:
        return AsamError("BAD_REQUEST", msg, fix="Check parameter names/values. GA4: `asam ga4 metadata` lists valid "
                         "dimensions/metrics. GSC dimensions: query,page,country,device,date,searchAppearance.",
                         details={"status": status, "url": url})
    return AsamError(f"HTTP_{r.status_code}", msg, details={"status": status, "url": url})
