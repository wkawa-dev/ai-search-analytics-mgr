---
name: seo-report
description: Run a full Search Console + GA4 + technical/AI-search analysis of a site and turn it into prioritized actions, optionally implementing fixes in the site's codebase. Use when asked for an SEO audit, analytics report, growth plan, "full analysis", or periodic review.
---

# Full SEO & analytics analysis

1. **Preflight**: `doctor_run site=<site>`. Fix what you can; if GA4 isn't accessible, continue with
   `include_ga4=false` and say so.
2. **Run**: `analyze_report site=<site>` (add `compare=yoy` for seasonal businesses or when asked about
   year-over-year). It writes `asam-reports/report-<host>-<stamp>.md` + `.json` and returns the summary.
3. **Deepen the top findings** (don't just restate the report):
   - For each top quick win: `web_audit` the page and check whether the query appears in the title/H1/intro. Note
     what's missing.
   - For CTR gaps: fetch the page's title/description with `web_audit`; draft 2 alternative titles + descriptions
     (<= 60 / <= 155 chars, lead with the query's intent, include a concrete benefit or number).
   - For cannibalization: decide the winner (best position x conversions) and propose merge/redirect/re-target.
   - For decaying pages: `gsc_query filters=["page equals <url>"] dimensions=[query] date_range=last3m` vs the
     previous period to see which queries fell.
   - For not-indexed important pages: `gsc_inspect_batch urls=[...]` and explain each coverage state.
4. **Deliver**: a concise summary with (a) headline numbers and deltas, (b) the 5-10 highest-impact actions, each
   with evidence, expected impact and effort, and (c) the report file path. Tables > prose.
5. **Implement (if you're in the site's repo and the user agrees)**: titles/meta descriptions, internal links, JSON-LD
   (Organization, WebSite, BreadcrumbList, Article/Product/FAQ as appropriate), robots.txt (allow AI search bots),
   sitemap fixes, llms.txt, image alts, canonical fixes. Keep diffs focused; after deploy run
   `indexnow_submit urls=[changed URLs]` and `gsc_sitemap_submit`.
6. **Measure later**: note the date of changes; on the next review run
   `gsc_query filters=["page equals <url>"] date_range=<since change>` with `gsc_compare` to show the effect.

Priorities: fix indexing/crawl blockers first, then CTR gaps (fast wins, no ranking change needed), then quick wins,
then content decay, then new content. Performance matters most when Core Web Vitals field data fails.
