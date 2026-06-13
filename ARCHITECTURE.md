# Architecture Notes

## Analytics

Tool: Cloudflare Web Analytics
Cost: Free, no page view limits
Account: Cloudflare (same account used for PublicSense domain and hosting)

Chosen because it is free with no limits, backed by Cloudflare rather than a
single maintainer, privacy-respecting with no cookies or cross-site tracking,
and already integrates with the Cloudflare infrastructure planned for
PublicSense. When the site moves to Cloudflare Pages, analytics becomes
automatic and this script tag can be removed.

Tracks: total visitors and page-level breakdown.

Google Analytics is not used. It sends behavioral data to a third-party
advertising platform, which conflicts with the site's civic transparency
mission.
