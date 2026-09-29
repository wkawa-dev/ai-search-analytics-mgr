# asam: AI Search Analytics Manager

Lets an AI agent manage **Google Search Console + Google Analytics 4** and the on-site SEO work around
them, with as little human input as possible. It ships as three things built from one codebase:

- a **Claude Code plugin** (MCP server + skills + `asam` on PATH), the most seamless option,
- an **MCP server** for any MCP client (61 tools),
- a **CLI** (`asam ...`, JSON in/out, every error includes a `fix`).

Agents get the tools and humans keep the web UIs. After a one-time Google Cloud setup (about 3 minutes, covered below),
an agent can onboard a site, verify ownership, create GA4 properties, install tags in your codebase, run full
analyses, and push fixes.

```text
> /asam:seo-report acme.com
  doctor_run -> analyze_report -> web_audit (quick-win pages) -> recommendations + report file
```

## What it does

| Area | Capabilities |
|---|---|
| **Full analysis** | One call (`analyze_report`) returns period-over-period GSC totals, top queries/pages, **quick wins** (pos 4-20 with demand), **CTR gaps** (vs the site's *own* CTR curve), **cannibalization**, **content decay**, query/page movers, new/lost queries, brand vs non-brand, devices/countries, sitemap errors, GA4 channels, organic landing-page engagement **joined with GSC**, robots/sitemap/homepage/AI-readiness/PageSpeed checks, and a **prioritized recommendation list with evidence**. Writes Markdown + JSON. |
| **Search Console** | Properties (list/add/delete), Search Analytics (auto-pagination past 25k rows, simple filter syntax, all search types incl. Discover/News), period compare, **URL Inspection** (single + parallel batch from URLs / top pages / a sitemap), sitemaps (list/submit/resubmit/delete). |
| **GA4** | Reports (friendly filters, ordering, pagination, previous/YoY compare columns), realtime, metadata discovery, **admin writes**: create properties + web streams (returns gtag snippet), key events, custom dimensions/metrics, data retention, access grants, product links. |
| **Onboarding** | `setup_site`: idempotent end-to-end wiring. **Programmatic ownership verification** (Site Verification API: META / FILE / DNS / GA tag), GA4 property + stream creation with 14-month retention, live-site tag detection, and an ordered action list the agent applies to your codebase. `setup_verify` then verifies, adds the property, and submits sitemaps. |
| **Web / discoverability** | Page audit (title, meta, H1, canonical, noindex/X-Robots, hreflang, OG, **JSON-LD validation**, alt text, thin content, mixed content, tags), **site crawler** (broken links, redirects, duplicate titles/descriptions, depth, sitemap orphans, pages missing from the sitemap), **robots.txt** analysis with Google longest-match semantics, sitemap validation (index recursion, gzip, live sampling for non-200/noindex/non-canonical), sitemap generator, **PageSpeed Insights** (Lighthouse + real-user Core Web Vitals), structured-data extraction. |
| **AI search** | Access check for AI *search* crawlers (OAI-SearchBot, ChatGPT-User, Claude-SearchBot, Claude-User, PerplexityBot, ...) vs *training* crawlers (GPTBot, ClaudeBot, Google-Extended, CCBot, ...), llms.txt, server-rendered text, entity markup. |
| **Indexing** | **IndexNow** (key generation, key-file check, bulk submit, submit-from-sitemap-since-date) for Bing, which feeds ChatGPT search and Copilot, plus Yandex and others. Google Indexing API (JobPosting/BroadcastEvent). |

The full list is in [docs/TOOLS.md](docs/TOOLS.md).

## Install

Requires [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

**Claude Code plugin (recommended): MCP server, skills, and `asam` on the Bash PATH**
```text
/plugin marketplace add wkawa-dev/ai-search-analytics-mgr
/plugin install asam@ai-search-analytics-mgr
```
Skills: `/asam:search-analytics` (general playbook), `/asam:seo-report` (full analysis to actions),
`/asam:site-onboarding` (connect a site end-to-end). Claude also uses them automatically when relevant.

**MCP server only (any client)**
```bash
claude mcp add --scope user asam -- uvx --from git+https://github.com/wkawa-dev/ai-search-analytics-mgr asam mcp
```
```json
{ "mcpServers": { "asam": { "command": "uvx",
  "args": ["--from", "git+https://github.com/wkawa-dev/ai-search-analytics-mgr", "asam", "mcp"] } } }
```

**CLI**
```bash
uv tool install git+https://github.com/wkawa-dev/ai-search-analytics-mgr   # then: asam --help
```

## One-time auth (the only real human step)

Run `asam guide auth` for the full text. Short version:

**A) OAuth as yourself (personal use, simplest)**
1. Create a Google Cloud project and [enable the six APIs in one click](https://console.cloud.google.com/flows/enableapi?apiid=searchconsole.googleapis.com,analyticsadmin.googleapis.com,analyticsdata.googleapis.com,siteverification.googleapis.com,indexing.googleapis.com,pagespeedonline.googleapis.com).
2. Google Auth Platform: set Branding (app name + email), set Audience to External, then **Publish app** ("In production").
   In "Testing" mode Google expires refresh tokens every 7 days. Verification isn't needed for personal use.
3. Clients: create a **Desktop app** client and download the JSON. Leave it in `~/Downloads` or the working dir; asam finds it.
4. `asam auth login`. At a terminal it opens the browser. From an agent it returns an `auth_url`; you approve,
   then paste back the URL the browser lands on. If that `127.0.0.1` page fails to load, that's fine, because the
   URL still carries the code. The agent then calls `auth_finish`. Done for good.

**B) Service account (headless, CI, servers)**
```bash
gcloud iam service-accounts create asam --project P
gcloud iam service-accounts keys create sa.json --iam-account asam@P.iam.gserviceaccount.com
asam auth use sa.json
```
- Search Console: the service account can become a verified owner **with no UI**. Run `setup_site`, deploy the META tag,
  then run `setup_verify`.
- GA4: grant it Editor once in the GA4 UI, or from an OAuth session with `asam ga4 grant-access <sa-email> --role editor`.

Application Default Credentials also work. Credentials live in `~/.config/asam` (mode 0600; override with
`ASAM_HOME`). Optional: `asam config set google_api_key <key>` raises the PageSpeed quota.

Then run **`asam doctor`**. It checks credentials, scopes, API enablement, and GSC + GA4 access, and prints exact fixes
(for example the one-click API activation URL for your project).

## Usage examples

```bash
asam doctor
asam config set default_site example.com            # then `site` can be omitted everywhere
asam report example.com --md                        # full analysis -> Markdown
asam report --compare yoy
asam gsc query --dimensions query,page --filters "query contains pricing" --filters "country equals usa"
asam gsc compare --dimension page --compare yoy
asam gsc inspect-batch --top-pages 50               # index coverage of your 50 most-seen pages
asam ga4 report --dimensions landingPage --filters "sessionDefaultChannelGroup == Organic Search" --compare previous
asam ga4 key-event-create generate_lead
asam setup site newsite.com                         # -> actions: meta tag, gtag snippet, IndexNow key, ...
asam setup verify newsite.com
asam crawl https://example.com --max-pages 300
asam web ai-readiness example.com
asam indexnow submit --sitemap https://example.com/sitemap.xml --since 2026-09-01
```

The same calls from an MCP client look like `analyze_report {"site": "example.com"}` and
`gsc_query {"dimensions": ["query","page"], "filters": ["query contains pricing"]}`.

## Design choices (agent-first, hard to misuse)

- **One registry, two interfaces.** CLI commands and MCP tools are generated from the same typed functions
  (`src/asam/ops.py`), so they can't drift apart.
- **Forgiving inputs.** `site` accepts `example.com`, any page URL, `https://www.example.com/` or `sc-domain:...` and resolves
  to the right property (domain property preferred). `property` accepts an id, `properties/id`, a domain (matched via
  web streams and remembered), or a display name. With a single site or property, the id can be omitted.
- **Context-safe output.** Big results return `show` rows inline and write the full set to CSV/JSON, with the path included in the result.
- **Errors carry fixes.** API-not-enabled errors include the activation URL and gcloud command. Permission errors name the
  identity and where to grant access. Expired tokens explain the 7-day "Testing" trap.
- **Safety.** Destructive operations need `confirm=true`. Tool annotations mark read-only and destructive tools. Report
  queries are cached for 6h (`asam cache clear`), and 429/5xx responses are retried with backoff.
- **Correct semantics.** GSC ranges end at today-3 (data lag). CTR expectations are calibrated to the site's own curve. Robots rules
  use Google's longest-match with `*`/`$` handling. Most analysis code is pure functions with tests.

## Why not an existing FOSS tool?

Surveyed September 2026:

| Project | License | Scope | Gap for this use case |
|---|---|---|---|
| [googleanalytics/google-analytics-mcp](https://github.com/googleanalytics/google-analytics-mcp) (official) | Apache-2.0 | GA4 reports, realtime, account listing | **Read-only**, GA4 only, no Search Console, no admin writes |
| [saurabhsharma2u/search-console-mcp](https://github.com/saurabhsharma2u/search-console-mcp) | MIT | GSC + Bing + GA4 read, sitemaps, IndexNow, PSI | Closest match, but GA4 is read-only (service account), no GA4 admin, no programmatic verification/onboarding, no GSC to GA4 join |
| [Suganthan-Mohanadasan/Suganthans-GSC-MCP](https://github.com/Suganthan-Mohanadasan/Suganthans-GSC-MCP) | Apache-2.0 | GSC analysis (quick wins, decay, cannibalization), Indexing API | GSC only |
| [AminForou/mcp-gsc](https://github.com/AminForou/mcp-gsc), [ahonn/mcp-server-gsc](https://github.com/ahonn/mcp-server-gsc), [getmcpads-com/google-search-console-mcp-server](https://github.com/getmcpads-com/google-search-console-mcp-server) | MIT / Apache-2.0 | GSC queries, inspection, sitemaps | GSC only, mostly read-only |

None of them covers **both products with write access, programmatic onboarding and verification, a joined GSC + GA4
analysis, and site-side SEO tooling** in one agent-oriented package. Forking the closest one would still have meant writing
most of this from scratch. asam can run alongside any of them.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest -q          # 55 tests: Google APIs mocked, web tools against a local HTTP fixture site
.venv/bin/ruff check src tests
claude plugin validate .
```
Layout: `src/asam/{auth,http,gsc,ga4,setup,web,indexing,analysis,ops,cli,mcp_server,guide}.py`,
`skills/` (plugin skills), `.claude-plugin/` (plugin + marketplace), `bin/asam` (PATH shim), `.mcp.json` (project dev).

MIT licensed.
