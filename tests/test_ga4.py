import json

import pytest
import responses

from asam import config, ga4
from asam.errors import AsamError

SUMMARIES = {"accountSummaries": [{"account": "accounts/1", "displayName": "Acme", "propertySummaries": [
    {"property": "properties/111", "displayName": "Acme Web"},
    {"property": "properties/222", "displayName": "Other"}]}]}


@pytest.fixture
def ga(fake_google):
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rs:
        rs.get(f"{ga4.ADMIN}/accountSummaries", json=SUMMARIES)
        rs.get(f"{ga4.ADMIN}/properties/111/dataStreams", json={"dataStreams": [
            {"type": "WEB_DATA_STREAM", "webStreamData": {"defaultUri": "https://www.acme.com", "measurementId": "G-AAA111"}}]})
        rs.get(f"{ga4.ADMIN}/properties/222/dataStreams", json={"dataStreams": []})
        yield rs


def test_resolve_by_id_domain_and_name(ga):
    assert ga4.resolve_property("111") == "properties/111"
    assert ga4.resolve_property("properties/9") == "properties/9"
    assert ga4.resolve_property("Other") == "properties/222"
    assert ga4.resolve_property("https://acme.com/blog") == "properties/111"
    assert config.load()["site_properties"]["acme.com"] == "111"  # learned for next time


def test_resolve_unknown(ga):
    with pytest.raises(AsamError) as ei:
        ga4.resolve_property("nope.com")
    assert ei.value.code == "PROPERTY_NOT_FOUND"


def test_filters():
    d, m = ga4.parse_filters(["sessionDefaultChannelGroup == Organic Search", "landingPage !contains /admin",
                              "country in US,CA", "sessions > 10"], ["sessions"])
    ex = d["andGroup"]["expressions"]
    assert ex[0]["filter"]["stringFilter"] == {"matchType": "EXACT", "value": "Organic Search", "caseSensitive": False}
    assert "notExpression" in ex[1]
    assert ex[2]["filter"]["inListFilter"]["values"] == ["US", "CA"]
    assert m["filter"]["numericFilter"] == {"operation": "GREATER_THAN", "value": {"int64Value": "10"}}


def test_report_converts_and_compares(ga):
    def cb(req):
        body = json.loads(req.body)
        assert len(body["dateRanges"]) == 2 and body["orderBys"][0] == {"desc": True, "metric": {"metricName": "sessions"}}
        return 200, {}, json.dumps({
            "dimensionHeaders": [{"name": "sessionDefaultChannelGroup"}, {"name": "dateRange"}],
            "metricHeaders": [{"name": "sessions", "type": "TYPE_INTEGER"}, {"name": "engagementRate", "type": "TYPE_FLOAT"}],
            "rowCount": 2,
            "rows": [
                {"dimensionValues": [{"value": "Organic Search"}, {"value": "current"}],
                 "metricValues": [{"value": "150"}, {"value": "0.61234"}]},
                {"dimensionValues": [{"value": "Organic Search"}, {"value": "previous"}],
                 "metricValues": [{"value": "100"}, {"value": "0.5"}]}]})

    ga.add_callback(responses.POST, f"{ga4.DATA}/properties/111:runReport", callback=cb)
    res = ga4.report("111", dimensions=["sessionDefaultChannelGroup"], metrics=["sessions", "engagementRate"],
                     compare="previous")
    row = res["rows"][0]
    assert row["sessions"] == 150 and row["sessions_prev"] == 100 and row["sessions_pct"] == 50.0
    assert row["engagementRate"] == 0.6123


def test_create_property_sets_retention_and_stream(ga):
    ga.post(f"{ga4.ADMIN}/properties", json={"name": "properties/333", "displayName": "new.com"})
    ga.patch(f"{ga4.ADMIN}/properties/333/dataRetentionSettings", json={"eventDataRetention": "FOURTEEN_MONTHS"})
    ga.post(f"{ga4.ADMIN}/properties/333/dataStreams", json={"webStreamData": {"measurementId": "G-NEW333"}})
    out = ga4.create_property("new.com", url="new.com")
    assert out["stream"]["measurement_id"] == "G-NEW333"
    assert "gtag/js?id=G-NEW333" in out["stream"]["snippet"]
    assert config.load()["site_properties"]["new.com"] == "333"
