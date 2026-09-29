---
name: site-onboarding
description: Connect a website to Google Search Console and GA4 end-to-end with minimal human input - programmatic ownership verification, GA4 property/stream creation, tag installation in the codebase, sitemap/robots/IndexNow/llms.txt setup. Use when adding a new site, "set up analytics", "add to search console", or when doctor/setup reports a site is missing.
---

# Onboard a site (hands-off)

Prereq: `auth_status` shows authenticated. If not, run the auth flow first (`guide_show topic=auth`; with OAuth:
`auth_login` -> human opens `auth_url` once -> `auth_finish redirect_url=...`).

1. `setup_site domain=<domain>` (idempotent; safe to re-run). It inspects GSC, GA4 and the live homepage, creates
   the GA4 property + web stream (14-month retention) if missing, and returns ordered `actions`.
   - `method=META` (default): URL-prefix property verified by a `<meta>` tag - best when you control the code.
   - `method=DNS_TXT`: Domain property (all subdomains/protocols) - best when you control DNS (e.g. via a DNS
     provider CLI/API in this environment).
   - `method=FILE`: an HTML file in the web root. `method=ANALYTICS`: reuse an existing GA4 tag.
2. **Carry out each action in the site's code** (find the global layout/head):
   - Next.js app router: `app/layout.tsx` -> `<head>` or `metadata.verification.google` + `next/script` for gtag
     (`strategy="afterInteractive"`). Pages router: `pages/_document.tsx`.
   - Astro: base layout `<head>`. Nuxt: `nuxt.config` `app.head`. SvelteKit: `src/app.html`. Remix: `root.tsx`.
   - Hugo/Jekyll/Eleventy: base template partial `head`. Plain HTML: every page's `<head>`.
   - WordPress: theme `header.php` or a header-scripts plugin setting.
   - Static files (IndexNow key `<key>.txt`, verification `google*.html`, `robots.txt`, `llms.txt`) go in the public/
     static dir (`public/`, `static/`, `assets/` root, etc.).
   - Ensure `robots.txt` has `Sitemap: https://<domain>/sitemap.xml` and does not block Googlebot, Bingbot or AI
     search bots (OAI-SearchBot, ChatGPT-User, Claude-SearchBot, Claude-User, PerplexityBot).
   - Ensure a sitemap exists (framework plugin preferred; else `web_sitemap_generate` and commit the file).
   - Optional but recommended: Organization + WebSite JSON-LD on the homepage; `/llms.txt` listing key pages.
3. **Deploy** (use the repo's deploy mechanism if you have it; otherwise ask the human to deploy - the one step
   that may need them).
4. Confirm live: `web_tags url=https://<domain>/` shows the verification token and measurement ID.
5. `setup_verify domain=<domain> method=<same method>` -> verifies, adds the Search Console property, submits
   sitemaps, sets `default_site`. DNS can take minutes: retry a few times with pauses if it fails.
6. `ga4_realtime` (after visiting the site or waiting for traffic) to confirm hits; create key events for the
   site's conversions: `ga4_key_event_create event_name=generate_lead` (etc.).
7. `indexnow_submit sitemap=https://<domain>/sitemap.xml` once the key file is live.
8. `setup_status domain=<domain>` for a final wiring check. Report what was done and what (if anything) remains
   human-only (e.g. linking Search Console inside GA4: Admin > Product links > Search Console links).
