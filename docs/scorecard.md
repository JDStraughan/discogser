# Scorecard

Artifact under test: **discogser**, a Python 3.11 CLI plus a localhost-only,
single-user Flask web UI (`discogser-web`, binds `127.0.0.1`). It has **no public
web surface**: nothing is deployed, crawled, indexed, shared, or served to
anonymous users. Metrics that only exist for a public website are marked N/A
with the reason; they are not scored (per the RED TEAM cargo-cult veto).

## Baseline (loop 0 - the floor)

| Lane | Metric | Baseline | Applicable? |
|---|---|---|---|
| Code | ruff lint | clean | yes |
| Code | mypy types | clean | yes |
| Code | pytest | 63 passed | yes |
| Code | core-flow coverage | resolver tiers, cataloguer, discogs client, ledger, web routes, doctor, security guards all covered | yes |
| Security | leaked secrets | none (`.env` git-ignored, redaction in errors) | yes |
| Security | vuln deps (pip-audit) | 0 high/critical | yes |
| Security | SAST (CodeQL) | green | yes |
| Security | web response headers | **none set** | yes -> FIX |
| Security | localhost CSRF / DNS-rebinding | **no Host guard** | yes -> FIX |
| A11y | web UI keyboard operability | drop zone not focusable | yes -> FIX |
| A11y | web UI labels / alt / live regions | partial | yes -> FIX |
| UX | states (loading/empty/error) | handled | yes |
| UX | reduced-motion / focus-visible | not handled | yes -> FIX |
| Perf | page weight (web UI) | single ~12 KB HTML doc, 1 request, no bundle | yes (trivially green) |
| Perf | Lighthouse perf/LCP/CLS/TBT | N/A | no public URL; no Chrome/Lighthouse in env |
| SEO | Lighthouse SEO / title/meta / sitemap / robots | N/A | localhost tool is not indexable |
| AEO/GEO | llms.txt / schema / Q&A retrievability | N/A | no public content to surface |
| Social | OG / Twitter cards / share copy | N/A | nothing is shared/unfurled |
| Edge | CDN cache / TTFB / WAF | N/A | runs on the user's machine, not hosted |

## Receipts location
`docs/evidence.md` (test output, header dumps, pip-audit, byte deltas).

## Exit criterion (applicable rubric)
All "yes" rows green, zero regression on the floor (ruff/mypy/63 tests/0 vulns),
fresh-adversary cold audit finds no unaddressed issue.

## Loop 1 (after security + a11y pass)

| Lane | Metric | Loop 1 | vs floor |
|---|---|---|---|
| Code | ruff / mypy / pytest | clean / clean / **69 passed** | +6 tests |
| Security | response headers | CSP + nosniff + XFO + Referrer + COOP | FIXED |
| Security | DNS-rebinding | Host allowlist (hardened parser) | FIXED |
| Security | CSRF | `X-Requested-With` required on POSTs | FIXED |
| Security | stored XSS | quote-safe `esc()` | FIXED (was a latent hole) |
| Security | vuln deps / secrets / dead code | 0 / 0 / 0 | held |
| A11y | keyboard / labels / live / alt | all present | FIXED |
| A11y | contrast (AA) | min 5.82:1 (was 3.96:1) | FIXED |
| UX | focus-visible / reduced-motion | present | FIXED |

Fresh adversary (loop 1) returned BLOCK on the XSS; all findings fixed and
re-verified. A second cold adversary re-audits in loop 2 to confirm SHIP.
