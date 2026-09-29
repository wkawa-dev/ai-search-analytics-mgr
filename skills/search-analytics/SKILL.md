---
name: search-analytics
description: Use for anything involving Google Search Console, Google Analytics 4, organic/SEO traffic, rankings, indexing, sitemaps, robots.txt, Core Web Vitals, AI-search visibility (ChatGPT/Claude/Perplexity crawlers), IndexNow, or "why did traffic drop". Drives the asam MCP tools / `asam` CLI hands-off.
---

# Search Console + GA4 + SEO with asam

You manage Search Console, GA4 and on-site SEO **yourself**, using the `asam` tools. The human should only be
asked for the few things that truly need them (listed at the end). Never ask them to click around in the GSC/GA4 UI
for something a tool can do.

## Interfaces (same operations, pick whichever is loaded)
- MCP tools: `gsc_query`, `ga4_report`, `analyze_report`, ... (server `asam`; in Claude Code they appear as
  `mcp__asam__<tool>` or `mcp__plugin_asam_asam__<tool>`).
- CLI via Bash: `asam <group> <action> ...` (JSON on stdout, errors carry `fix`). `asam guide tools` lists everything.
  If `asam` isn't on PATH: `uvx --from git+https://github.com/wkawa-dev/ai-search-analytics-mgr asam ...`.
- Error `fix` hints use CLI syntax; the MCP tool is `<group>_<action>` with underscores
  (e.g. `asam gsc sitemap-submit` = `gsc_sitemap_submit`).

## Always start with
1. `doctor_run` (optionally `site=`). It checks credentials, API enablement, GSC + GA4 access and returns
   `fixes`. Apply every fix you can yourself (e.g. `gcloud services enable ...` if gcloud is present); only
   escalate what's left.
2. If there are several sites and no default: `config_set key=default_site value=<domain>` (and
   `default_property`) so later calls need no ids.

## Core playbooks
- **"How is my site doing?" / weekly review**: `analyze_report` (30-90 s). Read `files.markdown` and present the
  top recommendations with evidence. If you are inside the site's codebase, offer to implement the HIGH items
  (see the `seo-report` skill).
- **Traffic drop**: `gsc_compare dimension=page` and `dimension=query`, then `gsc_query dimensions=[date]` to date
  the drop, `gsc_inspect_batch urls=[top losers]`, `web_audit` the losers, `web_robots`, `web_sitemap_check`,
  and `ga4_report dimensions=[sessionDefaultChannelGroup] compare=previous` to see whether only organic fell.
  Distinguish: tracking break (GA4 down, GSC flat), indexing problem (inspect verdicts), ranking loss (position up),
  demand/seasonality (impressions down, position flat; check `compare=yoy`).
- **Content opportunities**: `analyze_quick_wins`, `analyze_ctr_gaps`, `analyze_cannibalization`, `analyze_decay`,
  question queries via `gsc_query filters=["query regex ^(how|what|why|can|is) "]`.
- **Technical sweep**: `web_crawl max_pages=300`, `web_pagespeed`, `web_ai_readiness`,
  `gsc_inspect_batch top_pages=50`.
- **New site**: follow the `site-onboarding` skill (`setup_site` -> edit code -> deploy -> `setup_verify`).
- **After shipping changes**: `indexnow_submit urls=[...]` (Bing/ChatGPT search/Copilot) + `gsc_sitemap_submit`
  (prompts a Google refetch) + `gsc_inspect url=<key page>` later to confirm.

## Interpreting data correctly
- GSC data lags 2-3 days; ranges already end at today-3. Use `data_state=all` for fresher partial data.
- GSC `position` is an impression-weighted average: a page ranking #3 for a few big queries and #40 for many
  small ones can average 25. Always segment by query before judging a page.
- Query-level rows omit anonymized queries, so the sum of query rows < site totals (normal). Page-level totals are
  complete.
- CTR comparisons use the site's own CTR curve (`ctr_curve` in the report), not a generic one.
- GA4 `keyEvents` are conversions. If none are configured, recommend `ga4_key_event_create` (lead/signup/purchase).
- GA4 organic = `sessionDefaultChannelGroup == Organic Search`. GA4 and GSC never match exactly (clicks != sessions,
  consent mode, bots); compare trends, not absolute numbers.
- Treat period comparisons with < ~50 clicks per row as noise.

## Safety rules
- Read-only tools are always fine. Write tools that are low risk and reversible (submit sitemaps, IndexNow, create
  key events/custom dims, set retention): do them when they serve the user's goal and say what you did.
- Destructive tools (`*_delete`, `auth_logout`) need `confirm=true`: only when the user explicitly asked.
- Creating GA4 properties / adding GSC properties: fine during onboarding the user asked for.
- Never print or commit token files, service-account keys or `client_secret*.json`.
- `google_indexing_publish` is officially only for JobPosting/BroadcastEvent pages; don't use it for general pages.

## Only these need a human
1. One-time Google Cloud setup (OAuth desktop client or service account): walk them through `guide_show topic=auth`.
2. Opening the `auth_url` from `auth_login` once and pasting back the redirect URL (then call `auth_finish`).
3. Creating the very first GA account (ToS acceptance), if they have none.
4. Deploying code/DNS if you don't have deploy access.
5. Linking Search Console in GA4's UI (optional; no API, and asam joins the data itself).
