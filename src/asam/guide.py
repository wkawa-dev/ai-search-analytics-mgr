"""Built-in docs (`asam guide <topic>` / MCP guide_show) so agents never need to leave the tool."""

GUIDES = {
    "overview": """\
asam = AI Search Analytics Manager. Agent-first control of Google Search Console + GA4 + web SEO tooling.

Start here:
  1. doctor_run                      -> tells you exactly what's missing (auth, APIs, access) and how to fix it
  2. analyze_report site=<domain>    -> full analysis + prioritized recommendations (Markdown + JSON on disk)
  3. setup_site domain=<domain>      -> onboard a new site (GSC verification + GA4 property/tag), then setup_verify

Groups: auth, config, setup, gsc (Search Console), ga4, analyze, web (audits/crawl/robots/sitemaps/PSI/AI),
indexnow, google_indexing. `guide_show topic=tools` lists every tool. Topics: auth, workflows, filters.

Inputs are forgiving: site may be example.com, a page URL, https://example.com/, or sc-domain:example.com.
property may be 123456789, properties/123, a domain (matched via web streams) or the display name.
Set defaults once: config_set default_site example.com / default_property 123456789.
Large results: `show` rows inline, the full set written to CSV (path in full_rows_file).
Every error has a `fix` (written in CLI syntax: `asam gsc sitemap-submit` == MCP tool gsc_sitemap_submit).
Destructive tools need confirm=true. Reports/exports land in ./asam-reports (config: reports_dir).""",
    "auth": """\
ONE-TIME SETUP (human, ~3 minutes). Pick A (personal, simplest) or B (headless/CI/servers).

A) OAuth as yourself (sees everything your Google account sees)
  1. https://console.cloud.google.com/projectcreate -> create a project (any name).
  2. Enable APIs (one click each, or `gcloud services enable ...`):
     searchconsole.googleapis.com analyticsadmin.googleapis.com analyticsdata.googleapis.com
     siteverification.googleapis.com indexing.googleapis.com pagespeedonline.googleapis.com
     Console: https://console.cloud.google.com/flows/enableapi?apiid=searchconsole.googleapis.com,analyticsadmin.googleapis.com,analyticsdata.googleapis.com,siteverification.googleapis.com,indexing.googleapis.com,pagespeedonline.googleapis.com
  3. Google Auth Platform > Branding: fill app name + your email. Audience: External, add yourself as test user,
     then PUBLISH APP ("In production") - otherwise refresh tokens expire every 7 days. No verification needed
     for personal use (you'll click through an "unverified app" screen once).
  4. Clients > Create client > Desktop app > Download JSON (client_secret_....json; leave it in ~/Downloads).
  5. `asam auth browser` (human at a terminal)  OR  agent flow:
       auth_login -> give auth_url to the human -> they approve and paste back the final URL
       (the 127.0.0.1 page may fail to load on remote machines; the URL still contains the code) -> auth_finish.

B) Service account (fully headless)
  gcloud iam service-accounts create asam --project P
  gcloud iam service-accounts keys create sa.json --iam-account asam@P.iam.gserviceaccount.com
  asam auth use sa.json
  - Search Console: no UI needed. `asam setup site <domain>` + deploy the META tag + `asam setup verify` makes the
    service account a verified owner. (Or add it as Owner in GSC Settings > Users and permissions.)
  - GA4: grant it Editor once: GA4 Admin > Property access management, or from an OAuth login:
    `asam ga4 grant-access asam@P.iam.gserviceaccount.com --role editor --property <id>`.

Also supported: Application Default Credentials (GOOGLE_APPLICATION_CREDENTIALS or `gcloud auth application-default
login --client-id-file=client.json --scopes=...`). Optional PageSpeed API key: `asam config set google_api_key K`.
Everything is stored in ~/.config/asam (0600). Override with ASAM_HOME.""",
    "workflows": """\
WEEKLY HEALTH CHECK
  analyze_report -> read files.markdown -> act on HIGH recommendations -> for pages you change,
  indexnow_submit urls=[...] and gsc_sitemap_submit (prompts recrawl).

ONBOARD A NEW SITE (from inside the site's repo)
  setup_site domain=example.com -> for each action: add the HTML to the layout <head> (verification meta + gtag),
  add the IndexNow key file to the public/static dir, ensure sitemap + robots.txt -> deploy ->
  setup_verify domain=example.com -> ga4_realtime to confirm hits -> ga4_key_event_create for conversions.

TRAFFIC DROP INVESTIGATION
  gsc_compare dimension=page (and query) -> gsc_inspect_batch urls=<top losers> -> web_audit on losers ->
  web_robots / web_sitemap_check -> ga4_report compare=previous dimensions=[sessionDefaultChannelGroup] to see if
  it's organic-only -> gsc_query dimensions=[date] to find the day it started.

CONTENT OPPORTUNITIES
  analyze_quick_wins, analyze_ctr_gaps (rewrite titles/meta), analyze_cannibalization (merge/redirect),
  gsc_query filters=['query regex ^(how|what|why)'] for question queries -> FAQ sections.

TECHNICAL SEO SWEEP
  web_crawl url=<site> max_pages=300 -> fix broken links/duplicates/noindex -> web_pagespeed -> web_ai_readiness ->
  gsc_inspect_batch top_pages=50 for index coverage.

AFTER DEPLOYING CHANGES
  indexnow_submit sitemap=<url> since=<today> ; gsc_sitemap_submit ; gsc_inspect url=<key page>.""",
    "filters": """\
GSC filters: '<dimension> <operator> <value>' ANDed together.
  dimensions: query page country device searchAppearance
  operators: equals(=) notEquals(!=) contains(~) notContains(!~) includingRegex(regex,=~) excludingRegex(!regex)
  e.g. 'query contains pricing', 'page regex /blog/.*', 'country equals deu', 'device equals MOBILE'
  (country = ISO-3166-1 alpha-3 lowercase; RE2 regex syntax)

GA4 filters: '<field> <op> <value>'
  strings: == != contains !contains begins ends regex !regex in !in (in: comma-separated list)
  numbers (metrics): > >= < <= == !=
  e.g. 'sessionDefaultChannelGroup == Organic Search', 'landingPage begins /blog', 'sessions > 10',
       'deviceCategory in mobile,tablet'
  order_by: ['-sessions', 'date']. Discover names with ga4_metadata search=<word>.""",
}
