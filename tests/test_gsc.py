import json

import pytest
import responses

from asam import gsc
from asam.errors import AsamError

SITES = {"siteEntry": [
    {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"},
    {"siteUrl": "https://shop.other.org/", "permissionLevel": "siteFullUser"},
    {"siteUrl": "https://www.third.net/", "permissionLevel": "siteOwner"},
]}


@pytest.fixture
def sites(fake_google):
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rs:
        rs.get(f"{gsc.WM}/sites", json=SITES)
        yield rs


@pytest.mark.parametrize("inp,expected", [
    ("example.com", "sc-domain:example.com"),
    ("https://www.example.com/some/page", "sc-domain:example.com"),
    ("blog.example.com", "sc-domain:example.com"),
    ("https://shop.other.org/p/1", "https://shop.other.org/"),
    ("third.net", "https://www.third.net/"),
    ("sc-domain:example.com", "sc-domain:example.com"),
])
def test_resolve_site(sites, inp, expected):
    assert gsc.resolve_site(inp) == expected


def test_resolve_site_missing_has_fix(sites):
    with pytest.raises(AsamError) as ei:
        gsc.resolve_site("nope.io")
    assert ei.value.code == "SITE_NOT_FOUND" and "setup site" in ei.value.fix


def test_resolve_requires_default_when_many(sites):
    with pytest.raises(AsamError) as ei:
        gsc.resolve_site(None)
    assert ei.value.code == "SITE_REQUIRED"


def test_query_paginates_and_filters(sites):
    calls = []

    def cb(req):
        body = json.loads(req.body)
        calls.append(body)
        start = body["startRow"]
        n = body["rowLimit"] if start == 0 else 3
        rows = [{"keys": [f"q{start + i}"], "clicks": 1, "impressions": 10, "ctr": 0.1, "position": 4.123}
                for i in range(n)]
        return 200, {}, json.dumps({"rows": rows})

    sites.add_callback(responses.POST, f"{gsc.WM}/sites/sc-domain%3Aexample.com/searchAnalytics/query", callback=cb)
    res = gsc.query("example.com", limit=30000, filters=["query contains shoes", "device = MOBILE"])
    assert res["row_count"] == 25003
    assert calls[0]["rowLimit"] == 25000 and calls[1]["startRow"] == 25000
    fl = calls[0]["dimensionFilterGroups"][0]["filters"]
    assert fl == [{"dimension": "query", "operator": "contains", "expression": "shoes"},
                  {"dimension": "device", "operator": "equals", "expression": "MOBILE"}]
    assert res["rows"][0] == {"query": "q0", "clicks": 1, "impressions": 10, "ctr": 0.1, "position": 4.12}


def test_bad_filter_and_dimension():
    with pytest.raises(AsamError):
        gsc.parse_filters(["query shoes"])
    with pytest.raises(AsamError):
        gsc.parse_filters(["title contains x"])


def test_inspect_summary(sites):
    sites.post(f"{gsc.SC}/urlInspection/index:inspect", json={"inspectionResult": {
        "indexStatusResult": {"verdict": "NEUTRAL", "coverageState": "Crawled - currently not indexed",
                              "googleCanonical": "https://example.com/a", "userCanonical": "https://example.com/b"},
        "inspectionResultLink": "https://search.google.com/..."}})
    s = gsc.inspect("https://example.com/b")
    assert s["verdict"] == "NEUTRAL" and s["canonical_mismatch"] is True


def test_api_not_enabled_error_maps_to_fix(sites):
    sites.get(f"{gsc.WM}/sites/sc-domain%3Aexample.com/sitemaps", status=403, json={"error": {
        "code": 403, "status": "PERMISSION_DENIED",
        "message": "Google Search Console API has not been used in project 123456 before or it is disabled.",
        "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SERVICE_DISABLED",
                     "metadata": {"activationUrl": "https://console.developers.google.com/apis/api/x/overview?project=123456"}}]}})
    with pytest.raises(AsamError) as ei:
        gsc.list_sitemaps("example.com")
    e = ei.value
    assert e.code == "API_NOT_ENABLED"
    assert "console.developers.google.com" in e.fix and "--project 123456" in e.fix


def test_permission_denied_names_identity(sites):
    sites.get(f"{gsc.WM}/sites/sc-domain%3Aexample.com/sitemaps", status=403,
              json={"error": {"code": 403, "message": "User does not have sufficient permission"}})
    with pytest.raises(AsamError) as ei:
        gsc.list_sitemaps("example.com")
    assert ei.value.code == "PERMISSION_DENIED" and "me@example.com" in ei.value.fix
