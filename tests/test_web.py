import http.server
import threading

import pytest

from asam import web

PAGES = {
    "/": """<html lang="en"><head><title>Acme Widgets - Industrial widgets since 1990</title>
<meta name="description" content="Acme makes widgets for industry. Browse catalog, pricing and support resources.">
<meta name="viewport" content="width=device-width"><link rel="canonical" href="/">
<meta property="og:title" content="Acme"><meta property="og:image" content="/og.png">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"Acme","url":"/"}</script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-TEST12345"></script>
</head><body><h1>Widgets</h1>""" + ("word " * 300) + """
<a href="/a">A</a><a href="/b">B</a><a href="/missing">M</a><a href="/private/x">P</a><a href="https://ext.com/">E</a>
</body></html>""",
    "/a": "<html><head><title>Dup</title></head><body><h1>A</h1><a href='/'>home</a></body></html>",
    "/b": "<html><head><title>Dup</title><meta name='robots' content='noindex'></head><body>"
          "<script type='application/ld+json'>{bad json</script><img src='x.png'></body></html>",
    "/robots.txt": "User-agent: *\nDisallow: /private/\n\nUser-agent: OAI-SearchBot\nDisallow: /\n\nSitemap: {base}/sitemap.xml\n",
    "/sitemap.xml": """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>{base}/</loc><lastmod>2026-09-01</lastmod></url><url><loc>{base}/b</loc></url>
<url><loc>{base}/orphan</loc></url><url><loc>{base}/</loc></url></urlset>""",
}


@pytest.fixture(scope="module")
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = PAGES.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            body = body.replace("{base}", base).encode()
            self.send_response(200)
            ct = "text/plain" if self.path.endswith(".txt") else "application/xml" if self.path.endswith(".xml") else "text/html"
            self.send_header("Content-Type", ct)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield base
    srv.shutdown()


def test_audit_page(site):
    a = web.audit_page(site + "/")
    assert a["status"] == 200 and a["ga4_ids"] == ["G-TEST12345"]
    assert a["structured_data_types"] == ["Organization"]
    codes = {i["code"] for i in a["issues"]}
    assert "JSONLD_MISSING_FIELD" in codes  # Organization without logo
    assert "TITLE_MISSING" not in codes and a["score"] > 80


def test_audit_bad_page(site):
    codes = {i["code"] for i in web.audit_page(site + "/b")["issues"]}
    assert {"NOINDEX", "JSONLD_INVALID", "H1_MISSING", "DESCRIPTION_MISSING", "IMG_ALT_MISSING"} <= codes


def test_robots_check(site):
    rc = web.robots_check(site, ["/", "/private/x"])
    assert rc["sitemaps"] == [site + "/sitemap.xml"]
    assert rc["crawlers"]["Googlebot"]["allowed"] == {"/": True, "/private/x": False}
    assert rc["crawlers"]["OAI-SearchBot"]["allowed"]["/"] is False
    assert "AI_SEARCH_BOTS_BLOCKED" in {i["code"] for i in rc["issues"]}


def test_unreachable_site_is_an_error():
    rc = web.robots_check("http://127.0.0.1:9")
    assert rc["issues"][0]["code"] == "SITE_UNREACHABLE"


def test_sitemap_check(site):
    sc = web.sitemap_check(site + "/sitemap.xml", sample=10)
    codes = {i["code"] for i in sc["issues"]}
    assert sc["url_count"] == 4 and sc["duplicates"] == 1
    assert {"SITEMAP_DUPLICATES", "SITEMAP_NON_200", "SITEMAP_NOINDEX"} <= codes


def test_crawl(site):
    res = web.crawl(site + "/", max_pages=20)
    s = res["summary"]
    assert s["broken_internal_links"] == 1 and res["broken_links"][0]["target"].endswith("/missing")
    assert s["duplicate_titles"] == 1 and s["noindex_pages"] == 1 and s["blocked_by_robots"] == 1
    assert any(u.endswith("/orphan") for u in s["sitemap_orphans_sample"])
    assert any(u.endswith("/a") for u in s["indexable_not_in_sitemap"])


def test_generate_sitemap(site, tmp_path):
    out = web.generate_sitemap(site + "/", out_file=str(tmp_path / "sm.xml"))
    xml = (tmp_path / "sm.xml").read_text()
    assert out["url_count"] == 2 and "/b<" not in xml  # noindex page excluded
