"""Google Analytics 4: Admin API (accounts, properties, streams, key events, custom defs, access)
and Data API (reports, realtime, metadata)."""

from __future__ import annotations

import os
import re
from typing import Any

from . import config, dates
from .errors import AsamError
from .http import api

ADMIN = "https://analyticsadmin.googleapis.com/v1beta"
ADMIN_ALPHA = "https://analyticsadmin.googleapis.com/v1alpha"
DATA = "https://analyticsdata.googleapis.com/v1beta"


# ------------------------------------------------------------------ discovery / resolution
def account_summaries() -> list[dict[str, Any]]:
    out, token = [], None
    while True:
        res = api("GET", f"{ADMIN}/accountSummaries", params={"pageSize": 200, **({"pageToken": token} if token else {})})
        out += res.get("accountSummaries", [])
        token = res.get("nextPageToken")
        if not token:
            return out


def list_properties() -> list[dict[str, Any]]:
    props = []
    for a in account_summaries():
        for p in a.get("propertySummaries", []):
            props.append({"property": p["property"], "id": p["property"].split("/")[-1],
                          "name": p.get("displayName"), "account": a.get("account"),
                          "account_name": a.get("displayName"), "type": p.get("propertyType")})
    return props


def _norm(prop: str) -> str:
    prop = prop.strip()
    if re.fullmatch(r"\d+", prop):
        return f"properties/{prop}"
    if re.fullmatch(r"properties/\d+", prop):
        return prop
    return ""


def _host(u: str) -> str:
    u = u if "://" in u else "https://" + u
    from urllib.parse import urlparse

    h = (urlparse(u).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def resolve_property(prop: str | None) -> str:
    """Accepts 123, properties/123, a domain/URL (matched against web data streams), or a display name."""
    prop = prop or config.get("default_property")
    if prop and _norm(str(prop)):
        return _norm(str(prop))
    props = list_properties()
    if not prop:
        if len(props) == 1:
            return props[0]["property"]
        raise AsamError("PROPERTY_REQUIRED", "No GA4 property given and no default_property configured.",
                        fix="Pass property=<id or domain> or `asam config set default_property <id>`.",
                        details={"properties": [{"id": p["id"], "name": p["name"]} for p in props][:50]})
    host = _host(prop)
    learned = (config.load().get("site_properties") or {}).get(host)
    if learned:
        return _norm(learned)
    for p in props:
        if (p["name"] or "").lower() == prop.lower():
            return p["property"]
    for p in props:
        for s in list_streams(p["property"]):
            uri = (s.get("webStreamData") or {}).get("defaultUri")
            if uri and _host(uri) == host:
                config.remember_property(host, p["id"])
                return p["property"]
    raise AsamError("PROPERTY_NOT_FOUND", f"No accessible GA4 property matches '{prop}'.",
                    fix=f"Create one: `asam setup site {host}` (or `asam ga4 create-property`), or list: `asam ga4 properties`.",
                    details={"properties": [{"id": p["id"], "name": p["name"]} for p in props][:50]})


# ------------------------------------------------------------------ admin
def get_property(prop: str | None = None) -> dict[str, Any]:
    p = resolve_property(prop)
    out = api("GET", f"{ADMIN}/{p}")
    out["streams"] = list_streams(p)
    try:
        out["data_retention"] = api("GET", f"{ADMIN}/{p}/dataRetentionSettings").get("eventDataRetention")
    except AsamError:
        pass
    return out


def list_streams(prop: str) -> list[dict[str, Any]]:
    return api("GET", f"{ADMIN}/{resolve_property(prop)}/dataStreams", params={"pageSize": 200}).get("dataStreams", [])


def local_timezone() -> str:
    tz = os.environ.get("TZ")
    if tz and "/" in tz:
        return tz
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "Etc/GMT"


def create_property(display_name: str, account: str | None = None, url: str | None = None,
                    time_zone: str | None = None, currency: str = "USD",
                    industry: str = "OTHER") -> dict[str, Any]:
    if not account:
        accts = account_summaries()
        if len(accts) != 1:
            raise AsamError("ACCOUNT_REQUIRED", "Multiple (or zero) GA accounts accessible; specify account.",
                            fix="Pass account=accounts/<id>. If zero: create a GA account once at "
                                "https://analytics.google.com (account creation requires accepting ToS in the UI).",
                            details=[{"account": a["account"], "name": a.get("displayName")} for a in accts])
        account = accts[0]["account"]
    if not account.startswith("accounts/"):
        account = f"accounts/{account}"
    prop = api("POST", f"{ADMIN}/properties", json_body={
        "parent": account, "displayName": display_name, "timeZone": time_zone or local_timezone(),
        "currencyCode": currency, "industryCategory": industry, "propertyType": "PROPERTY_TYPE_ORDINARY"})
    out: dict[str, Any] = {"property": prop}
    set_retention(prop["name"], "FOURTEEN_MONTHS")
    out["data_retention"] = "FOURTEEN_MONTHS"
    if url:
        out["stream"] = create_web_stream(prop["name"], url)
    return out


def create_web_stream(prop: str | None, url: str, name: str | None = None) -> dict[str, Any]:
    p = resolve_property(prop)
    if not url.startswith("http"):
        url = "https://" + url
    s = api("POST", f"{ADMIN}/{p}/dataStreams", json_body={
        "type": "WEB_DATA_STREAM", "displayName": name or _host(url), "webStreamData": {"defaultUri": url}})
    mid = (s.get("webStreamData") or {}).get("measurementId")
    config.remember_property(_host(url), p.split("/")[-1])
    return {"stream": s, "measurement_id": mid, "snippet": gtag_snippet(mid) if mid else None}


def gtag_snippet(measurement_id: str) -> str:
    return (
        "<!-- Google tag (gtag.js) -->\n"
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={measurement_id}"></script>\n'
        "<script>\n  window.dataLayer = window.dataLayer || [];\n  function gtag(){dataLayer.push(arguments);}\n"
        f"  gtag('js', new Date());\n  gtag('config', '{measurement_id}');\n</script>"
    )


def set_retention(prop: str | None, retention: str = "FOURTEEN_MONTHS") -> dict[str, Any]:
    p = resolve_property(prop)
    return api("PATCH", f"{ADMIN}/{p}/dataRetentionSettings", params={"updateMask": "eventDataRetention"},
               json_body={"eventDataRetention": retention})


def list_key_events(prop: str | None = None) -> list[dict[str, Any]]:
    p = resolve_property(prop)
    return api("GET", f"{ADMIN}/{p}/keyEvents", params={"pageSize": 200}).get("keyEvents", [])


def create_key_event(event_name: str, prop: str | None = None, once_per_session: bool = False) -> dict[str, Any]:
    p = resolve_property(prop)
    return api("POST", f"{ADMIN}/{p}/keyEvents", json_body={
        "eventName": event_name, "countingMethod": "ONCE_PER_SESSION" if once_per_session else "ONCE_PER_EVENT"})


def delete_key_event(event_name: str, prop: str | None = None) -> dict[str, Any]:
    p = resolve_property(prop)
    for k in list_key_events(p):
        if k.get("eventName") == event_name:
            api("DELETE", f"{ADMIN}/{k['name']}")
            return {"deleted": k["name"]}
    raise AsamError("NOT_FOUND", f"Key event '{event_name}' not found on {p}.")


def list_custom_definitions(prop: str | None = None) -> dict[str, Any]:
    p = resolve_property(prop)
    return {
        "dimensions": api("GET", f"{ADMIN}/{p}/customDimensions", params={"pageSize": 200}).get("customDimensions", []),
        "metrics": api("GET", f"{ADMIN}/{p}/customMetrics", params={"pageSize": 200}).get("customMetrics", []),
    }


def create_custom_dimension(parameter_name: str, display_name: str | None = None, scope: str = "EVENT",
                            description: str = "", prop: str | None = None) -> dict[str, Any]:
    p = resolve_property(prop)
    return api("POST", f"{ADMIN}/{p}/customDimensions", json_body={
        "parameterName": parameter_name, "displayName": display_name or parameter_name,
        "scope": scope.upper(), "description": description})


def create_custom_metric(parameter_name: str, display_name: str | None = None, unit: str = "STANDARD",
                         description: str = "", prop: str | None = None) -> dict[str, Any]:
    p = resolve_property(prop)
    return api("POST", f"{ADMIN}/{p}/customMetrics", json_body={
        "parameterName": parameter_name, "displayName": display_name or parameter_name,
        "measurementUnit": unit.upper(), "scope": "EVENT", "description": description})


def grant_access(email: str, role: str = "editor", prop: str | None = None) -> dict[str, Any]:
    p = resolve_property(prop)
    role = role.lower()
    if role not in {"viewer", "analyst", "editor", "admin"}:
        raise AsamError("BAD_ROLE", f"role '{role}'", fix="Use viewer, analyst, editor or admin.")
    return api("POST", f"{ADMIN_ALPHA}/{p}/accessBindings",
               json_body={"user": email, "roles": [f"predefinedRoles/{role}"]})


def list_links(prop: str | None = None) -> dict[str, Any]:
    p = resolve_property(prop)
    out: dict[str, Any] = {}
    for kind in ("googleAdsLinks", "firebaseLinks"):
        try:
            out[kind] = api("GET", f"{ADMIN}/{p}/{kind}").get(kind, [])
        except AsamError as e:
            out[kind] = {"error": e.code}
    try:
        out["bigQueryLinks"] = api("GET", f"{ADMIN_ALPHA}/{p}/bigQueryLinks").get("bigqueryLinks", [])
    except AsamError as e:
        out["bigQueryLinks"] = {"error": e.code}
    out["searchConsoleLink"] = ("Not exposed by the GA4 Admin API. Link manually once: GA4 Admin > Product links > "
                                "Search Console links (asam joins GSC+GA4 data itself, so this is optional).")
    return out


# ------------------------------------------------------------------ data API
_NUM_OPS = {">": "GREATER_THAN", ">=": "GREATER_THAN_OR_EQUAL", "<": "LESS_THAN", "<=": "LESS_THAN_OR_EQUAL",
            "==": "EQUAL", "=": "EQUAL"}
_STR_OPS = {"==": ("EXACT", False), "=": ("EXACT", False), "equals": ("EXACT", False),
            "!=": ("EXACT", True), "notequals": ("EXACT", True),
            "contains": ("CONTAINS", False), "~": ("CONTAINS", False),
            "!contains": ("CONTAINS", True), "notcontains": ("CONTAINS", True), "!~": ("CONTAINS", True),
            "begins": ("BEGINS_WITH", False), "startswith": ("BEGINS_WITH", False),
            "ends": ("ENDS_WITH", False), "endswith": ("ENDS_WITH", False),
            "regex": ("FULL_REGEXP", False), "=~": ("FULL_REGEXP", False),
            "!regex": ("FULL_REGEXP", True), "!=~": ("FULL_REGEXP", True),
            "partial_regex": ("PARTIAL_REGEXP", False)}


def parse_filters(filters: list[str] | None, metrics: list[str]) -> tuple[dict | None, dict | None]:
    dim_ex, met_ex = [], []
    for f in filters or []:
        parts = f.strip().split(None, 2)
        if len(parts) != 3:
            raise AsamError("BAD_FILTER", f"Filter '{f}' must be '<field> <op> <value>'.",
                            fix="e.g. 'sessionDefaultChannelGroup == Organic Search', 'landingPage begins /blog', "
                                "'sessions > 10', 'country in United States,Canada'")
        field, op, val = parts
        opl = op.lower()
        if opl in ("in", "!in"):
            ex: dict[str, Any] = {"filter": {"fieldName": field, "inListFilter": {"values": [v.strip() for v in val.split(",")]}}}
            (dim_ex if field not in metrics else met_ex).append({"notExpression": ex} if opl == "!in" else ex)
        elif field in metrics or (opl in (">", ">=", "<", "<=") and _isnum(val)):
            if opl not in _NUM_OPS and opl != "!=":
                raise AsamError("BAD_FILTER_OPERATOR", f"Numeric filter op '{op}'", fix="Use > >= < <= == !=")
            ex = {"filter": {"fieldName": field, "numericFilter": {
                "operation": _NUM_OPS.get(opl, "EQUAL"), "value": _numval(val)}}}
            met_ex.append({"notExpression": ex} if opl == "!=" else ex)
        else:
            if opl not in _STR_OPS:
                raise AsamError("BAD_FILTER_OPERATOR", f"Unknown operator '{op}'", fix=f"Use {sorted(_STR_OPS)} or in/!in")
            mt, neg = _STR_OPS[opl]
            ex = {"filter": {"fieldName": field, "stringFilter": {"matchType": mt, "value": val, "caseSensitive": False}}}
            dim_ex.append({"notExpression": ex} if neg else ex)

    def group(xs):
        if not xs:
            return None
        return xs[0] if len(xs) == 1 else {"andGroup": {"expressions": xs}}

    return group(dim_ex), group(met_ex)


def _isnum(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def _numval(v: str) -> dict[str, Any]:
    return {"int64Value": v} if re.fullmatch(r"-?\d+", v) else {"doubleValue": float(v)}


def _convert(v: str, t: str) -> Any:
    if t in ("TYPE_INTEGER",):
        return int(v)
    if t.startswith("TYPE_") and t != "TYPE_STRING":
        try:
            return round(float(v), 4)
        except ValueError:
            return v
    return v


def report(prop: str | None = None, date_range: str = "last28d", dimensions: list[str] | None = None,
           metrics: list[str] | None = None, filters: list[str] | None = None, order_by: list[str] | None = None,
           limit: int = 1000, compare: str | None = None, start: str | None = None,
           end: str | None = None) -> dict[str, Any]:
    """GA4 runReport with friendly filters/order, auto-pagination, optional period comparison."""
    p = resolve_property(prop)
    dims = list(dimensions or [])
    mets = list(metrics or ["sessions", "totalUsers", "engagementRate", "keyEvents"])
    if start and end:
        s, e = dates._d(start), dates._d(end)
    else:
        s, e = dates.parse_range(date_range, lag=1 if (date_range or "").startswith("last") else 0)
    ranges = [{"startDate": dates.iso(s), "endDate": dates.iso(e), "name": "current"}]
    if compare:
        ps, pe = dates.previous_period(s, e, "yoy" if compare == "yoy" else "previous")
        ranges.append({"startDate": dates.iso(ps), "endDate": dates.iso(pe), "name": "previous"})
    body: dict[str, Any] = {"dateRanges": ranges, "dimensions": [{"name": d} for d in dims],
                            "metrics": [{"name": m} for m in mets], "keepEmptyRows": False}
    df, mf = parse_filters(filters, mets)
    if df:
        body["dimensionFilter"] = df
    if mf:
        body["metricFilter"] = mf
    obs = []
    for o in order_by or ([f"-{mets[0]}"] if mets else []):
        desc = o.startswith("-")
        n = o.lstrip("-+")
        obs.append({"desc": desc, **({"dimension": {"dimensionName": n}} if n in dims else {"metric": {"metricName": n}})})
    body["orderBys"] = obs
    rows: list[dict[str, Any]] = []
    offset = 0
    total = None
    while len(rows) < limit:
        page = min(100000, limit - len(rows))
        res = api("POST", f"{DATA}/{p}:runReport", json_body={**body, "limit": page, "offset": offset}, cache=True)
        dh = [h["name"] for h in res.get("dimensionHeaders", [])]
        mh = [(h["name"], h.get("type", "TYPE_STRING")) for h in res.get("metricHeaders", [])]
        total = res.get("rowCount", 0)
        batch = res.get("rows", [])
        for r in batch:
            row = {k: v.get("value") for k, v in zip(dh, r.get("dimensionValues", []))}
            for (name, t), v in zip(mh, r.get("metricValues", [])):
                row[name] = _convert(v.get("value", "0"), t)
            rows.append(row)
        offset += len(batch)
        if not batch or offset >= (total or 0):
            break
    if compare:
        rows = _pivot_compare(rows, dims, mets)
    return {"property": p, "start": ranges[0]["startDate"], "end": ranges[0]["endDate"],
            **({"previous": ranges[1]} if compare else {}), "dimensions": dims, "metrics": mets,
            "total_rows": total, "row_count": len(rows), "rows": rows}


def _pivot_compare(rows: list[dict], dims: list[str], mets: list[str]) -> list[dict]:
    """GA4 returns a `dateRange` dimension when comparing; pivot into current/previous/delta columns."""
    merged: dict[tuple, dict] = {}
    for r in rows:
        key = tuple(r.get(d) for d in dims)
        m = merged.setdefault(key, {d: r.get(d) for d in dims})
        tag = r.get("dateRange", "current")
        for k in mets:
            m[f"{k}" if tag == "current" else f"{k}_prev"] = r.get(k)
    out = []
    for m in merged.values():
        for k in mets:
            cur, prev = m.get(k) or 0, m.get(f"{k}_prev") or 0
            m.setdefault(k, 0)
            m.setdefault(f"{k}_prev", 0)
            if isinstance(cur, (int, float)) and isinstance(prev, (int, float)):
                m[f"{k}_delta"] = round(cur - prev, 4)
                m[f"{k}_pct"] = round((cur - prev) / prev * 100, 1) if prev else None
        out.append(m)
    return out


def realtime(prop: str | None = None, dimensions: list[str] | None = None,
             metrics: list[str] | None = None, limit: int = 50) -> dict[str, Any]:
    p = resolve_property(prop)
    dims = dimensions or ["unifiedScreenName"]
    mets = metrics or ["activeUsers"]
    res = api("POST", f"{DATA}/{p}:runRealtimeReport", json_body={
        "dimensions": [{"name": d} for d in dims], "metrics": [{"name": m} for m in mets], "limit": limit})
    dh = [h["name"] for h in res.get("dimensionHeaders", [])]
    mh = [h["name"] for h in res.get("metricHeaders", [])]
    rows = []
    for r in res.get("rows", []):
        row = {k: v.get("value") for k, v in zip(dh, r.get("dimensionValues", []))}
        row.update({k: int(float(v.get("value", 0))) for k, v in zip(mh, r.get("metricValues", []))})
        rows.append(row)
    return {"property": p, "rows": rows}


def metadata(prop: str | None = None, search: str | None = None) -> dict[str, Any]:
    p = resolve_property(prop)
    res = api("GET", f"{DATA}/{p}/metadata", cache=True)

    def slim(items, kind):
        out = []
        for i in items:
            name = i.get("apiName")
            if search and search.lower() not in (name + " " + i.get("uiName", "") + " " + i.get("category", "")).lower():
                continue
            out.append({"name": name, "ui": i.get("uiName"), "category": i.get("category"),
                        **({"type": i.get("type")} if kind == "m" else {}),
                        **({"custom": True} if i.get("customDefinition") else {})})
        return out

    return {"property": p, "dimensions": slim(res.get("dimensions", []), "d"),
            "metrics": slim(res.get("metrics", []), "m")}


def check_compatibility(prop: str | None, dimensions: list[str], metrics: list[str]) -> dict[str, Any]:
    p = resolve_property(prop)
    return api("POST", f"{DATA}/{p}:checkCompatibility", json_body={
        "dimensions": [{"name": d} for d in dimensions], "metrics": [{"name": m} for m in metrics]})

