# Architecture Notes

## Analytics

### Cloudflare Web Analytics
Tool: Cloudflare Web Analytics
Cost: Free, no page view limits
Account: Cloudflare (same account used for PublicSense domain and hosting)
Chosen because it is free with no limits, backed by Cloudflare rather than a
single maintainer, privacy-respecting with no cookies or cross-site tracking,
and already integrates with the Cloudflare infrastructure planned for
PublicSense. When the site moves to Cloudflare Pages, analytics becomes
automatic and this script tag can be removed.
Tracks: total visitors and page-level breakdown.

### Umami Analytics
Tool: Umami Cloud (cloud.umami.is), Hobby tier
Cost: Free (100K events/month, 6 months data retention)
Website ID: 507d2340-9a98-4f50-848e-14ac20c833ad
Added: June 2026
Chosen to complement Cloudflare Web Analytics, which provides page-level
totals but no session-level data. Umami adds session tracking and a richer
per-page breakdown without cookies or cross-site tracking. Both tools run
simultaneously — Cloudflare for infrastructure-level totals, Umami for
behavioral questions when they arise.
Tracks: sessions, page views, referrers, device types, custom events.
Injected via board-template.js for board pages and script tag in head for
index.html, faq.html, and calendar.html.

### Policy
Google Analytics is not used. It sends behavioral data to a third-party
advertising platform, which conflicts with the site's civic transparency
mission.
