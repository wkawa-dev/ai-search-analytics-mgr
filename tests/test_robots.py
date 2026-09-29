from asam.web import Robots

TXT = """
User-agent: *
Disallow: /private/
Allow: /private/public-page
Disallow: /*.pdf$
Disallow: /search?

User-agent: GPTBot
User-agent: CCBot
Disallow: /

User-agent: Googlebot
Disallow: /nogoogle/

Sitemap: https://example.com/sitemap.xml
"""


def test_longest_match_and_wildcards():
    r = Robots(TXT)
    assert r.sitemaps == ["https://example.com/sitemap.xml"]
    assert r.can_fetch("Bingbot", "https://example.com/")
    assert not r.can_fetch("Bingbot", "https://example.com/private/x")
    assert r.can_fetch("Bingbot", "https://example.com/private/public-page")
    assert not r.can_fetch("Bingbot", "https://example.com/files/a.pdf")
    assert r.can_fetch("Bingbot", "https://example.com/files/a.pdf?x=1")
    assert not r.can_fetch("Bingbot", "https://example.com/search?q=1")


def test_specific_group_wins_and_grouped_agents():
    r = Robots(TXT)
    # Googlebot has its own group, so the * group's /private/ rule does NOT apply to it
    assert r.can_fetch("Googlebot", "https://example.com/private/x")
    assert not r.can_fetch("Googlebot", "https://example.com/nogoogle/a")
    assert r.can_fetch("Googlebot-Image", "https://example.com/private/x")  # prefix match to Googlebot group
    assert not r.can_fetch("GPTBot", "https://example.com/")
    assert not r.can_fetch("CCBot", "https://example.com/anything")
    assert r.can_fetch("ClaudeBot", "https://example.com/")


def test_empty_robots_allows_all():
    assert Robots("").can_fetch("Googlebot", "https://example.com/x")
