"""Get URLs discovered faster: IndexNow (Bing, Yandex, Seznam, Naver, Yep...) and Google's Indexing API."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from . import config
from .errors import AsamError
from .http import api

INDEXING = "https://indexing.googleapis.com/v3"
INDEXNOW = "https://api.indexnow.org/indexnow"
UA = "asam/0.1 (+https://github.com/wkawa-dev/ai-search-analytics-mgr)"


# ------------------------------------------------------------------ IndexNow
def indexnow_key(write_to: str | None = None, regenerate: bool = False) -> dict[str, Any]:
    """Return (and create once) the IndexNow key. Optionally write <key>.txt into a web root dir."""
    key = config.get("indexnow_key")
    if not key or regenerate:
        key = secrets.token_hex(16)
        config.set_value("indexnow_key", key)
    out: dict[str, Any] = {"key": key, "key_file_name": f"{key}.txt", "key_file_content": key,
                           "instructions": f"Serve the file at https://<your-host>/{key}.txt (web root), deploy, "
                                           "then `asam indexnow submit <urls>`."}
    if write_to:
        d = Path(write_to).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{key}.txt").write_text(key)
        out["written"] = str(d / f"{key}.txt")
    return out


def indexnow_submit(urls: list[str], key_location: str | None = None) -> dict[str, Any]:
    if not urls:
        raise AsamError("NO_URLS", "No URLs given.")
    key = config.get("indexnow_key")
    if not key:
        raise AsamError("NO_INDEXNOW_KEY", "No IndexNow key yet.",
                        fix="Run `asam indexnow key --write-to <site public dir>` and deploy the key file.")
    hosts = {urlparse(u).netloc for u in urls}
    if len(hosts) != 1:
        raise AsamError("MIXED_HOSTS", f"IndexNow batches must be single-host, got {sorted(hosts)}.",
                        fix="Submit one call per host.")
    host = hosts.pop()
    scheme = urlparse(urls[0]).scheme or "https"
    loc = key_location or f"{scheme}://{host}/{key}.txt"
    try:
        kr = requests.get(loc, timeout=15, headers={"User-Agent": UA})
        if kr.status_code != 200 or kr.text.strip() != key:
            raise AsamError("INDEXNOW_KEY_NOT_LIVE", f"Key file {loc} not served correctly (HTTP {kr.status_code}).",
                            fix="Deploy the key file from `asam indexnow key` to your web root first.")
    except requests.RequestException as e:
        raise AsamError("INDEXNOW_KEY_NOT_LIVE", f"Cannot fetch {loc}: {e}") from e
    results = []
    for i in range(0, len(urls), 10000):
        chunk = urls[i:i + 10000]
        r = requests.post(INDEXNOW, json={"host": host, "key": key, "keyLocation": loc, "urlList": chunk},
                          headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": UA}, timeout=30)
        results.append({"count": len(chunk), "status": r.status_code, "body": r.text[:300]})
    meaning = {200: "accepted", 202: "accepted (key validation pending)", 400: "bad request", 403: "key not valid",
               422: "URLs don't match host/key", 429: "too many requests"}
    return {"host": host, "submitted": len(urls),
            "batches": [{**b, "meaning": meaning.get(b["status"], "")} for b in results]}


# ------------------------------------------------------------------ Google Indexing API
def google_publish(urls: list[str], deleted: bool = False) -> dict[str, Any]:
    """Notify Google of updated/removed URLs. Officially supported only for JobPosting / BroadcastEvent pages;
    for everything else prefer sitemaps + `gsc inspect` (Google may ignore it). Requires GSC owner."""
    out = []
    for u in urls:
        try:
            res = api("POST", f"{INDEXING}/urlNotifications:publish",
                      json_body={"url": u, "type": "URL_DELETED" if deleted else "URL_UPDATED"})
            out.append({"url": u, "ok": True, "notify_time": (res.get("urlNotificationMetadata") or {})
                        .get("latestUpdate", {}).get("notifyTime")})
        except AsamError as e:
            out.append({"url": u, "ok": False, "error": e.to_dict()})
            if e.code in ("API_NOT_ENABLED", "QUOTA_EXCEEDED", "UNAUTHENTICATED"):
                break
    return {"results": out, "note": "Google Indexing API is officially for JobPosting/BroadcastEvent pages. "
                                    "Default daily quota: 200 publish requests."}


def google_status(url: str) -> dict[str, Any]:
    return api("GET", f"{INDEXING}/urlNotifications/metadata", params={"url": url})
