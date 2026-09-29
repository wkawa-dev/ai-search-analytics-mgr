# Tool reference

Generated from the op registry (`asam guide tools`). Every tool is available as an MCP tool and as a CLI command; full parameter docs: `asam <group> <action> --help` or the MCP tool schema.

| MCP tool | CLI | Kind | Summary |
|---|---|---|---|
| `guide_show` | `asam guide` | read | Built-in documentation for agents. |
| `doctor_run` | `asam doctor` | read | Diagnose everything (credentials, scopes, API enablement, GSC + GA4 access, config) and return concrete fixes. |
| `auth_status` | `asam auth status` | read | Which credentials are active, identity (email), granted scopes, and what is missing. |
| `auth_login` | `asam auth login` | write | Start OAuth login. |
| `auth_finish` | `asam auth finish` | write | Finish OAuth login with the full redirect URL (http://127.0.0.1:8765/?state=...&code=...) or the bare code. |
| `auth_use` | `asam auth use` | write | Register a credential file: service-account JSON, authorized-user JSON, or OAuth client JSON. |
| `auth_logout` | `asam auth logout` | write (confirm) | Delete stored tokens / service-account key from asam's config dir. |
| `auth_enable_apis` | `asam auth enable-apis` | write | Enable all Google APIs asam uses on the GCP project behind your OAuth client (requires login with cloud=true, or ADC with cloud-platform scope). |
| `config_show` | `asam config show` | read | Show config (defaults, brand terms, learned site->property map) and all known keys. |
| `config_set` | `asam config set` | write | Set a config key (empty value unsets). |
| `cache_clear` | `asam cache clear` | write | Clear cached API responses (report queries are cached 6h by default). |
| `setup_site` | `asam setup site` | write | Onboard a site end-to-end, idempotently. |
| `setup_token` | `asam setup token` | write | Get a Search Console ownership verification token (META tag, FILE, DNS_TXT, ANALYTICS, TAG_MANAGER). |
| `setup_verify` | `asam setup verify` | write | Verify ownership (after the token is deployed), add the property to Search Console and submit discovered sitemaps. |
| `setup_status` | `asam setup status` | read | Wiring health for a domain: GSC property + sitemaps, GA4 property + live tag, robots, IndexNow. |
| `gsc_sites` | `asam gsc sites` | read | List Search Console properties you can access, with permission level. |
| `gsc_query` | `asam gsc query` | read | Search Analytics query (clicks, impressions, ctr, position), auto-paginated up to `limit` rows (max ~50k/day per property). |
| `gsc_compare` | `asam gsc compare` | read | Period-over-period movers for one dimension (page or query): losers, gainers, new, lost. |
| `gsc_inspect` | `asam gsc inspect` | read | URL Inspection: index verdict, coverage state, last crawl, Google vs user canonical, robots, rich results. |
| `gsc_inspect_batch` | `asam gsc inspect-batch` | read | Inspect many URLs in parallel and summarize by verdict. |
| `gsc_sitemaps` | `asam gsc sitemaps` | read | Sitemaps submitted in Search Console with errors, warnings, last download, pending state. |
| `gsc_sitemap_submit` | `asam gsc sitemap-submit` | write | Submit (or resubmit, to prompt a re-fetch) a sitemap. |
| `gsc_sitemap_delete` | `asam gsc sitemap-delete` | write (confirm) | Remove a sitemap from Search Console (does not delete the file). |
| `gsc_site_add` | `asam gsc site-add` | write | Add a property (https://example.com/ or sc-domain:example.com). |
| `gsc_site_delete` | `asam gsc site-delete` | write (confirm) | Remove a property from your Search Console account. |
| `ga4_properties` | `asam ga4 properties` | read | List GA4 accounts and properties you can access. |
| `ga4_property` | `asam ga4 property` | read | Property details incl. |
| `ga4_report` | `asam ga4 report` | read | GA4 Data API report. |
| `ga4_realtime` | `asam ga4 realtime` | read | Realtime report (last 30 min). |
| `ga4_metadata` | `asam ga4 metadata` | read | Valid dimension/metric API names for this property (incl. |
| `ga4_create_property` | `asam ga4 create-property` | write | Create a GA4 property (+ web stream if url given) with 14-month data retention. |
| `ga4_create_stream` | `asam ga4 create-stream` | write | Add a web data stream to a property; returns measurement ID + gtag snippet. |
| `ga4_key_events` | `asam ga4 key-events` | read | List key events (conversions). |
| `ga4_key_event_create` | `asam ga4 key-event-create` | write | Mark an event as a key event (conversion), e.g. |
| `ga4_key_event_delete` | `asam ga4 key-event-delete` | write (confirm) | Unmark a key event. |
| `ga4_custom_definitions` | `asam ga4 custom-definitions` | read | List custom dimensions and metrics. |
| `ga4_custom_dimension_create` | `asam ga4 custom-dimension-create` | write | Register an event/user parameter as a custom dimension. |
| `ga4_custom_metric_create` | `asam ga4 custom-metric-create` | write | Register a numeric event parameter as a custom metric. |
| `ga4_grant_access` | `asam ga4 grant-access` | write | Grant a user or service account access to a property. |
| `ga4_retention_set` | `asam ga4 retention-set` | write | Set event data retention (default for new properties is only 2 months). |
| `ga4_links` | `asam ga4 links` | read | Google Ads / Firebase / BigQuery links on the property. |
| `analyze_report` | `asam analyze report` | read | FULL analysis in one call: GSC totals + deltas, top queries/pages, quick wins, CTR gaps (vs the site's own CTR curve), cannibalization, content decay, query/page movers, brand split, devices/countries, sitemaps, GA4 channels + organic landing-page engagement joined to GSC, robots/sitemap/homepage/AI-readiness/PageSpeed checks, and a prioritized recommendation list. |
| `analyze_quick_wins` | `asam analyze quick-wins` | read | Query+page pairs at positions 4-20 with demand, ranked by extra clicks if moved to target_position. |
| `analyze_ctr_gaps` | `asam analyze ctr-gaps` | read | Top-10 rankings whose CTR is < 60% of expected (site-calibrated curve): rewrite titles/descriptions. |
| `analyze_cannibalization` | `asam analyze cannibalization` | read | Queries where multiple of your URLs compete (each >=10% of impressions). |
| `analyze_decay` | `asam analyze decay` | read | Pages whose clicks dropped by threshold_pct or more vs previous period / last year. |
| `analyze_brand` | `asam analyze brand` | read | Brand vs non-brand clicks/impressions. |
| `analyze_landing_pages` | `asam analyze landing-pages` | read | Join GSC pages with GA4 organic landing pages: visibility + engagement + key events per URL, with flags for high-traffic/low-engagement and converting-but-underexposed pages. |
| `web_audit` | `asam web audit` | read | On-page SEO audit of one URL: status/redirects, title, description, H1, canonical, robots/noindex, hreflang, Open Graph, JSON-LD (validated), alt text, word count, mixed content, GA4/GTM tags. |
| `web_crawl` | `asam web crawl` | read | Crawl a site (same host, bounded) and audit site-wide: status codes, broken internal links, redirects, duplicate titles/descriptions, noindex, thin pages, depth, sitemap orphans, pages missing from the sitemap. |
| `web_robots` | `asam web robots` | read | robots.txt analysis (Google longest-match semantics): declared sitemaps, access for Googlebot/Bingbot and AI crawlers (search vs training bots), and whether given paths are blocked. |
| `web_sitemap_check` | `asam web sitemap-check` | read | Validate sitemap(s) for a site or sitemap URL: structure, duplicates, off-host, lastmod, and live-checks a sample of URLs for non-200/redirect/noindex/non-canonical entries. |
| `web_sitemap_generate` | `asam web sitemap-generate` | write | Crawl and write sitemap.xml of indexable self-canonical pages (use when the site has none). |
| `web_pagespeed` | `asam web pagespeed` | read | PageSpeed Insights: Lighthouse scores (perf/SEO/a11y/best-practices), lab metrics, real-user Core Web Vitals (CrUX page + origin), top opportunities, failed SEO audits. |
| `web_structured_data` | `asam web structured-data` | read | Extract + sanity-check JSON-LD structured data on a page (required/recommended fields for common types). |
| `web_ai_readiness` | `asam web ai-readiness` | read | AI search visibility check: AI crawler access (OAI-SearchBot, ChatGPT-User, Claude-SearchBot, PerplexityBot...), llms.txt, server-rendered text, entity markup, Bing/IndexNow note. |
| `web_tags` | `asam web tags` | read | Detect GA4 measurement IDs, GTM containers, legacy UA ids and google-site-verification tags on a live page. |
| `indexnow_key` | `asam indexnow key` | write | Get/create the IndexNow key. |
| `indexnow_submit` | `asam indexnow submit` | write | Ping IndexNow (Bing -> ChatGPT search/Copilot, Yandex, Seznam, Naver...) about new/changed URLs. |
| `google_indexing_publish` | `asam google-indexing publish` | write | Google Indexing API URL_UPDATED/URL_DELETED notifications. |
| `google_indexing_status` | `asam google-indexing status` | read | Last Indexing API notification Google received for a URL. |
