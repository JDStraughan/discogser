# Council findings

Lane review of discogser. Each lane gives a verdict; the RED TEAM veto (applied
up front) cuts lanes that cannot be measured on a localhost CLI + single-user
web tool. Cutting an inapplicable lane is not dodging; fabricating its score
would be.

## Cut by RED TEAM (cargo-cult on this artifact)
- **Google Search** (crawl/index/CWV/canonical/robots/sitemap): nothing is
  served publicly or crawled. No URL exists to index. CUT.
- **OpenAI / Anthropic surfacing, AEO/GEO, llms.txt, schema.org**: there is no
  public content for an answer engine to retrieve or cite. CUT. (The *README* is
  the retrievable surface and is already clean, structured Markdown.)
- **Social Maven** (OG/Twitter cards): nothing is unfurled. CUT.
- **Cloudflare edge** (CDN cache/TTFB/WAF): the app runs on the user's machine;
  there is no edge. The *bundle-diet* sub-point partially applies and is kept
  under Perf (the UI is one ~12 KB inline document, already minimal). CUT edge.
- **Lighthouse perf score**: no public URL and no Chrome/Lighthouse runner in
  this environment, so a perf *number* cannot be produced honestly. The page is
  a single small request with no blocking JS bundle; perf is green by
  construction. CUT the fabricated number, KEEP page-weight as a receipt.

## Kept (real, measurable findings)

### Security / DARPA (highest impact)
1. **No response security headers** on the Flask UI. Add `X-Content-Type-Options`,
   `X-Frame-Options`/`frame-ancestors`, `Referrer-Policy`, `COOP`, and a CSP that
   restricts img to Discogs, connect to self, and bans base-uri/foreign frames.
2. **No Host-header validation** -> a malicious site the user visits could use
   DNS-rebinding to drive the local server (which holds the user's tokens). Add a
   localhost-only Host allowlist.
3. Secret hygiene, vuln deps, CSRF on JSON endpoints (preflight-protected),
   SSRF, decompression bombs: already handled in prior work. Verify, don't churn.

### Accessibility / Apple-design / Superhuman (real UI, even if local)
4. **Drop zone is a `<div>` with a click listener only** -> not keyboard
   operable (WCAG 2.1.1). Make it a focusable `role="button"` with Enter/Space.
5. **Inputs rely on placeholders, not labels** (WCAG 1.3.1/4.1.2). Add labels.
6. **Status/banner updates are not announced** to screen readers. Add
   `aria-live` regions.
7. **No `:focus-visible` styling** and **no `prefers-reduced-motion`** (the
   spinner animates unconditionally). Add both.
8. Candidate cover `<img>` lacks `alt`. Add it.

### Code
9. Lint/types/tests already green; coverage spans the core flows. No unjustified
   deps. Keep the floor; add tests for every new behavior (characterization
   first so changes show in the diff).
