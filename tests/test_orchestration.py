"""End-to-end orchestration with Google APIs stubbed at the module boundary."""

import json
from pathlib import Path

import pytest

from asam import analysis, ga4, gsc, ops, setup, web
from asam.errors import AsamError


def _rows(dims, n, seed):
    out = []
    for i in range(n):
        row = {}
        for d in dims:
            row[d] = {"query": f"widget {i % 7} {'acme' if i % 5 == 0 else 'buy'}",
                      "page": f"https://acme.com/p{i % 4}", "device": "MOBILE", "country": "usa",
                      "date": f"2026-09-{1 + i % 28:02d}"}[d]
        impr = 100 + (i * 37 + seed) % 900
        pos = 1 + (i * 13 + seed) % 19
        clicks = max(0, int(impr * analysis.expected_ctr(pos) * (0.3 if i % 3 == 0 else 1.0)) - seed % 3)
        row.update(clicks=clicks, impressions=impr, ctr=round(clicks / impr, 4), position=float(pos))
        out.append(row)
    return out


@pytest.fixture
def stubbed(monkeypatch):
    import datetime as dt

    from asam import dates

    monkeypatch.setattr(dates, "today", lambda: dt.date(2026, 9, 29))  # current = 08-30..09-26
    monkeypatch.setattr(gsc, "resolve_site", lambda s=None: "sc-domain:acme.com")
    monkeypatch.setattr(gsc, "totals", lambda site, s, e, **k: {"clicks": 800 if s > "2026-08-15" else 1200,
                                                                 "impressions": 20000, "ctr": 0.04, "position": 9.1})

    def query(site=None, date_range="last28d", dimensions=None, filters=None, search_type="web", limit=1000,
              data_state="final", aggregation="auto", start=None, end=None):
        seed = 0 if (start or "") > "2026-08-15" else 5
        return {"site": "sc-domain:acme.com", "start": start, "end": end, "dimensions": dimensions,
                "rows": _rows(dimensions, 60, seed)}

    monkeypatch.setattr(gsc, "query", query)
    monkeypatch.setattr(gsc, "list_sitemaps", lambda s=None: {"sitemaps": [{"path": "https://acme.com/sitemap.xml",
                                                                            "errors": "2"}]})
    monkeypatch.setattr(ga4, "resolve_property", lambda p=None: "properties/111")
    monkeypatch.setattr(ga4, "list_key_events", lambda p=None: [])

    def report(prop=None, date_range=None, dimensions=None, metrics=None, filters=None, order_by=None,
               limit=1000, compare=None, start=None, end=None):
        if dimensions == ["landingPage"]:
            rows = [{"landingPage": f"/p{i}", "sessions": 100, "engagementRate": 0.2 if i == 0 else 0.7,
                     "keyEvents": i, "averageSessionDuration": 30} for i in range(4)]
        else:
            rows = [{"sessionDefaultChannelGroup": "Organic Search", "sessions": 500, "keyEvents": 3,
                     "engagementRate": 0.6}]
        return {"property": "properties/111", "rows": rows, "dimensions": dimensions}

    monkeypatch.setattr(ga4, "report", report)


def test_full_report_end_to_end(stubbed):
    rep = analysis.full_report("acme.com", web_checks=False)
    assert rep["gsc_totals"]["change_pct"]["clicks"] == pytest.approx(-33.3)
    assert rep["quick_wins"] and rep["ctr_gaps"] and rep["cannibalization"]
    assert rep["brand"]["terms"] == ["acme"]
    titles = [r["title"] for r in rep["recommendations"]]
    assert any("Organic clicks down" in t for t in titles)
    assert any("Sitemap" in t for t in titles)
    assert any("no key events" in t for t in titles)
    md = Path(rep["files"]["markdown"]).read_text()
    assert "Prioritized recommendations" in md and "GA4 channels" in md
    json.loads(Path(rep["files"]["json"]).read_text())


def test_analyze_report_op_is_compact(stubbed):
    out = ops.analyze_report("acme.com", web_checks=False)
    assert set(out) >= {"recommendations", "files", "gsc_totals"} and "daily" not in out


def test_rows_out_truncates_and_saves(stubbed, tmp_path):
    out = ops.gsc_query("acme.com", dimensions=["query", "page"], show=10)
    assert len(out["rows"]) == 10 and Path(out["full_rows_file"]).read_text().count("\n") == 61


def test_setup_site_new_domain(monkeypatch):
    monkeypatch.setattr(gsc, "resolve_site", lambda s=None: (_ for _ in ()).throw(AsamError("SITE_NOT_FOUND", "x")))
    monkeypatch.setattr(ga4, "resolve_property", lambda p=None: (_ for _ in ()).throw(AsamError("PROPERTY_NOT_FOUND", "x")))
    monkeypatch.setattr(ga4, "create_property", lambda **k: {"property": {"name": "properties/9"}, "data_retention": "FOURTEEN_MONTHS",
                                                              "stream": {"measurement_id": "G-NEW"}})
    monkeypatch.setattr(setup, "api", lambda *a, **k: {"token": '<meta name="google-site-verification" content="abc" />'})
    monkeypatch.setattr(web, "detect_tags", lambda u: {"ga4_measurement_ids": [], "gtm_containers": []})
    monkeypatch.setattr(web, "robots_check", lambda u: {"sitemaps": [], "issues": []})
    monkeypatch.setattr(web, "discover_sitemaps", lambda u: [])
    out = setup.setup_site("https://www.acme.com/")
    assert out["domain"] == "acme.com" and out["ga4"]["created"] and not out["done"]
    joined = json.dumps(out["actions"])
    assert "google-site-verification" in joined and "G-NEW" in joined and "setup verify acme.com" in joined
    assert "sitemap" in joined.lower()
    from asam import config

    assert config.load()["default_property"] == "9" and config.load()["indexnow_key"]


def test_doctor_unauthenticated_gives_next_step():
    out = ops.doctor_run()
    assert out["ok"] is False and "auth login" in out["next"]
