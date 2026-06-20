# Plan (SRE scoring: impact / effort / risk, sorted by impact-to-risk)

Done-check is measurable for every item. Each ships as one gated atomic commit;
build+lint+types+tests must be green or the commit reverts.

| # | Item | Impact | Effort | Risk | Done-check |
|---|---|---|---|---|---|
| 1 | Security response headers on web UI | High | Low | Low | curl/test: CSP + nosniff + frame-ancestors present on every response |
| 2 | Host-header allowlist (anti DNS-rebinding) | High | Low | Low | test: foreign Host -> 403; localhost -> 200 |
| 3 | Keyboard-operable drop zone | High | Low | Low | served HTML has role=button + tabindex=0 + keydown; aria-label |
| 4 | Labels for inputs + alt on imgs | Med | Low | Low | served HTML has `<label>` + alt; no orphan placeholder-only inputs |
| 5 | aria-live status + banner regions | Med | Low | Low | served HTML has aria-live="polite" / role=alert |
| 6 | :focus-visible + prefers-reduced-motion | Med | Low | Low | CSS present; motion disabled under reduced-motion |

Out of scope (RED TEAM cut, see council.md): Lighthouse number, SEO, AEO/GEO,
social cards, edge/CDN. Reason: no public web surface on a localhost tool.

Order: 1 -> 2 (security first), then 3-6 (a11y/UX) which all touch the same
served page and can be verified by parsing the HTML the Flask test client
returns. Fresh adversary re-audits after.
