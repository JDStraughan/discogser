# Evidence ledger

Receipts for every change. "No receipts = didn't happen." Lighthouse/axe JSON
are not produced because the artifact has no public URL and there is no Chrome
runner in this environment; the receipts below are what can be generated and
verified honestly (test output, live header dumps, byte deltas, scans).

## Floor (loop 0)
- ruff: clean · mypy: clean · pytest: **63 passed** · pip-audit: **0 vulns**

## Item 1+2 - Web UI security headers + Host guard
**Before:** no response headers; any `Host` accepted (DNS-rebinding/CSRF risk).
**After (live `curl -D -` against 127.0.0.1:8765/):**
```
Content-Security-Policy: default-src 'none'; img-src 'self' https://*.discogs.com data:;
  style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self';
  base-uri 'none'; form-action 'self'; frame-ancestors 'none'
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Cross-Origin-Opener-Policy: same-origin
```
DNS-rebinding probe: `Host: evil.example.com -> HTTP 403`.
Tests added: `test_security_headers_present`, `test_host_guard_blocks_foreign_allows_localhost`.

## Item 3-6 - Web UI accessibility (WCAG 2.2 AA)
**Before:** drop zone was a `<div>` with a click listener only (not keyboard
operable); inputs labelled by placeholder only; status updates not announced;
no focus-visible or reduced-motion handling.
**After (asserted against the served HTML):** drop zone is `role="button"`,
`tabindex="0"`, `aria-label`, Enter/Space activated; `<label for>` on every text
input; `aria-live="polite"` on status/summary and `role="alert"` on the banner;
candidate covers carry `alt`; `:focus-visible` ring and a
`prefers-reduced-motion` block present.
Test added: `test_web_ui_accessibility_attributes`. Page still serves HTTP 200,
~12.9 KB, single request, CSP attached.

## DARPA security pass
- Secret scan over tracked files: **none**.
- `pip-audit`: **No known vulnerabilities found**.
- `vulture` (>=80% confidence): **no dead code**.
- Existing defenses verified intact: SSRF allowlist, decompression-bomb cap,
  token redaction, CSV-injection guard, CodeQL green.

## Loop 1 - fresh adversary findings, all fixed
Cold auditor (never saw the work) returned verdict BLOCK on a real XSS. Fixed:
- **CRITICAL XSS** (`esc()` didn't escape quotes -> attribute breakout via
  community-editable Discogs title/thumb under `'unsafe-inline'`): `esc()` now
  escapes `"` and `'`. Receipt: `&quot;`/`&#39;` present in served page; test
  `test_escape_helper_neutralises_attribute_breakout`.
- **HIGH CSRF** (cross-site simple-request could drive POSTs): all POSTs now
  require `X-Requested-With` (forces a preflight). Receipt: tests
  `test_post_without_csrf_header_is_blocked` (403) + updated legit-client tests.
- **MEDIUM contrast** (`#6b7383` = 3.96:1, below AA): -> `#868fa3` = **5.82:1**
  (computed via WCAG formula). Receipt: `#6b7383` absent from served page.
- **LOW** host-parser edge cases (userinfo `user@host`, bare `::1`) and
  `rel=noopener` on `_blank` links. Receipt: test `test_is_localhost_parser`.
- Adversary could NOT break: download path traversal, upload traversal, SSRF,
  CSV injection, ledger SQL, token redaction (all confirmed solid).

## Regression budget
floor metrics never dropped across all commits: ruff clean, mypy clean,
pip-audit 0 vulns, test count **63 -> 69** (monotonic up). No metric regressed.
