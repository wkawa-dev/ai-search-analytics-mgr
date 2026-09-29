from asam import analysis


def r(q, page, clicks, impr, pos):
    return {"query": q, "page": page, "clicks": clicks, "impressions": impr, "ctr": clicks / impr if impr else 0,
            "position": pos}


ROWS = [
    r("blue shoes", "https://x.com/blue", 10, 2000, 7.5),      # quick win
    r("red shoes", "https://x.com/red", 300, 1000, 1.2),        # already top
    r("green shoes", "https://x.com/green", 2, 1500, 2.1),      # ctr gap
    r("shoes", "https://x.com/a", 20, 600, 8.0),                # cannibalized
    r("shoes", "https://x.com/b", 15, 500, 9.0),
    r("acme shoes", "https://x.com/", 500, 900, 1.0),           # brand
]


def test_quick_wins_orders_by_potential():
    qw = analysis.quick_wins(ROWS, min_impressions=100)
    assert qw[0]["query"] == "blue shoes"
    assert all(4 <= x["position"] <= 20 for x in qw)
    assert qw[0]["potential_extra_clicks"] > 0


def test_ctr_gaps():
    gaps = analysis.ctr_gaps(ROWS, min_impressions=200)
    assert [g["query"] for g in gaps][0] == "green shoes"
    assert "red shoes" not in [g["query"] for g in gaps]


def test_cannibalization():
    c = analysis.cannibalization(ROWS, min_impressions=100)
    assert len(c) == 1 and c[0]["query"] == "shoes" and len(c[0]["pages"]) == 2


def test_compare_and_decay():
    cur = [{"page": "/a", "clicks": 10, "impressions": 100, "position": 5},
           {"page": "/new", "clicks": 5, "impressions": 50, "position": 9}]
    prev = [{"page": "/a", "clicks": 100, "impressions": 900, "position": 3},
            {"page": "/gone", "clicks": 7, "impressions": 70, "position": 4}]
    cmp = analysis.compare(cur, prev, ["page"])
    assert cmp["losers"][0]["page"] == "/a" and cmp["losers"][0]["clicks_pct"] == -90.0
    assert cmp["new"][0]["page"] == "/new" and cmp["lost"][0]["page"] == "/gone"
    assert analysis.decay(cmp)[0]["page"] == "/a"


def test_brand_split_and_default_terms():
    terms = analysis.brand_terms_for("sc-domain:acme.com")
    assert terms == ["acme"]
    b = analysis.brand_split(ROWS, terms)
    assert b["brand"]["clicks"] == 500 and b["non_brand"]["queries"] == 5


def test_ctr_curve_monotonic():
    curve = analysis.ctr_curve(ROWS * 5, min_impressions=1)
    vals = [curve[p] for p in range(1, 11)]
    assert vals == sorted(vals, reverse=True)


def test_join_and_engagement_flags():
    gsc_pages = [{"page": f"https://x.com/p{i}", "clicks": 100 + i, "impressions": 1000, "ctr": 0.1, "position": 6}
                 for i in range(6)]
    ga = [{"landingPage": f"/p{i}", "sessions": 100, "engagementRate": 0.2 if i == 0 else 0.7, "keyEvents": i}
          for i in range(6)]
    joined = analysis.join_pages(gsc_pages, ga)
    assert joined[0]["engagement_rate"] == 0.2
    flags = analysis.engagement_flags(joined)
    assert flags["low_engagement"][0]["page"].endswith("/p0")
    assert flags["converting_underexposed"]


def test_recommendations_and_markdown():
    rep = {"site": "sc-domain:x.com", "period": {"current": ["a", "b"], "previous": ["c", "d"], "mode": "previous"},
           "gsc_totals": {"current": {"clicks": 50, "impressions": 1000, "ctr": 0.05, "position": 8},
                          "previous": {"clicks": 100, "impressions": 1200, "ctr": 0.08, "position": 7},
                          "change_pct": {"clicks": -50.0, "impressions": -16.7}, "ctr_delta": -0.03,
                          "position_delta": 1},
           "quick_wins": analysis.quick_wins(ROWS), "ctr_gaps": analysis.ctr_gaps(ROWS),
           "cannibalization": analysis.cannibalization(ROWS), "warnings": [], "ga4_key_events": []}
    recs = analysis.recommendations(rep)
    assert recs[0]["priority"] == "high"
    titles = " ".join(x["title"] for x in recs)
    assert "down -50.0%" in titles and "key events" in titles
    rep["recommendations"] = recs
    md = analysis.to_markdown(rep)
    assert "# SEO & Analytics report" in md and "Quick wins" in md
