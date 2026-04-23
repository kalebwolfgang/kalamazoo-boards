"""
Kalamazoo Civil Rights Board Meeting Scraper
=============================================

Pulls CRB meetings from the CivicClerk API and past recordings from the
Kalamazoo City TV YouTube channel (live streams tab), then writes everything
to data/crb.json in the format the website expects.

CivicClerk supplies: meeting dates, agenda URLs, minutes URLs, cancelled status.
YouTube supplies: video IDs for recordings. The two are matched by date.

Requires the YOUTUBE_API_KEY environment variable to be set.
Never put the key in this file or commit it to GitHub.

Default mode (no arguments):
    Fetches the last 6 months of CivicClerk events and YouTube streams.
    Merges new data into the existing data/crb.json. This is what GitHub
    Actions runs on a weekly cron.

    python crb_scraper.py

Backfill mode (custom start date):
    Fetches everything from the given date forward. Use this once to build
    the full historical record. Safe to re-run — it won't duplicate records.

    python crb_scraper.py --start-date 2020-01-01

    You can also cap the end date:
    python crb_scraper.py --start-date 2020-01-01 --end-date 2024-01-01

Smart merge behavior:
    Existing records outside the scrape window are never touched.
    If the scraper returns null for youtube_id, agenda_url, or minutes_url
    but the existing record has a real value there (manually entered), the
    manual value is kept. Scraper data wins for everything else.
"""

import argparse
import json
import os
import re
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CIVICCLERK_TENANT       = "kalamazoomi"
CRB_CATEGORY_ID         = 32
CRB_KEYWORDS            = ["civil rights board", "civil rights"]

YOUTUBE_CHANNEL_ID      = "UCIgXSSXLSDxThVaaiRMsR5Q"   # Kalamazoo City TV
YOUTUBE_SEARCH_QUERY    = "Civil Rights Board"
# How many days either side of a meeting date to accept a YouTube match.
# CRB meets bimonthly and sometimes uploads a day late, so 3 days is safe.
YOUTUBE_DATE_TOLERANCE  = 3

LOOKBACK_MONTHS         = 6
CUTOFF_DATE             = "2020-01-01"   # don't go earlier than this in default mode

OUTPUT_PATH             = Path("data") / "crb.json"

PRESERVE_IF_SCRAPE_EMPTY = ("youtube_id", "agenda_url", "minutes_url")


# ---------------------------------------------------------------------------
# CivicClerk helpers
# ---------------------------------------------------------------------------

def build_civicclerk_url(start_date: str, end_date: str) -> str:
    base  = f"https://{CIVICCLERK_TENANT}.api.civicclerk.com/v1/Events"
    query = (
        f"?$filter=startDateTime ge {start_date} and startDateTime lt {end_date}"
        f"&$orderby=startDateTime desc, eventName asc"
    )
    return base + query


def fetch_all_civicclerk_events(url: str) -> list[dict]:
    """Fetch all events from CivicClerk, following pagination."""
    all_events = []
    page = 1
    while url:
        print(f"  [CivicClerk] Fetching page {page}...")
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        events = data.get("value", [])
        all_events.extend(events)
        print(f"    got {len(events)} events (running total: {len(all_events)})")
        url = data.get("@odata.nextLink")
        page += 1
    return all_events


def is_crb_event(event: dict) -> bool:
    """Return True only for genuine CRB events."""
    if event.get("eventCategoryId") != CRB_CATEGORY_ID:
        return False
    name_lower = event.get("eventName", "").lower()
    return any(kw in name_lower for kw in CRB_KEYWORDS)


def find_file_id(published_files: list[dict], file_type: str) -> int | None:
    for f in published_files or []:
        if f.get("type") == file_type:
            return f.get("fileId")
    return None


def build_document_url(event_id: int, file_id: int) -> str:
    return (
        f"https://{CIVICCLERK_TENANT}.portal.civicclerk.com"
        f"/event/{event_id}/files/agenda/{file_id}"
    )


def transform_civicclerk_event(event: dict) -> dict:
    """Turn a raw CivicClerk event into a CRB meeting record."""
    event_id  = event["id"]
    date_only = event["startDateTime"].split("T")[0]

    published_files = event.get("publishedFiles", [])
    agenda_file_id  = find_file_id(published_files, "Agenda")
    minutes_file_id = find_file_id(published_files, "Minutes")

    # CivicClerk marks cancelled events in the eventName
    name_lower = event.get("eventName", "").lower()
    cancelled  = "cancel" in name_lower

    return {
        "date":        date_only,
        "display":     format_display_date(date_only),
        "event_id":    event_id,
        "url":         (
            build_document_url(event_id, agenda_file_id)
            if agenda_file_id
            else f"https://{CIVICCLERK_TENANT}.portal.civicclerk.com/event/{event_id}/overview"
        ),
        "link_label":  "Minutes" if minutes_file_id else ("Agenda" if agenda_file_id else "Meeting Overview"),
        "cancelled":   cancelled,
        # minutes stored separately so the HTML can link to either
        "minutes_url": build_document_url(event_id, minutes_file_id) if minutes_file_id else None,
        "agenda_url":  build_document_url(event_id, agenda_file_id)  if agenda_file_id  else None,
    }


def format_display_date(iso: str) -> str:
    """'2026-04-01' → 'April 1, 2026'"""
    d = datetime.strptime(iso, "%Y-%m-%d")
    return d.strftime("%B %#d, %Y")   # Linux/Mac; use %#d on Windows


# ---------------------------------------------------------------------------
# YouTube helpers
# ---------------------------------------------------------------------------

def get_youtube_api_key() -> str:
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "ERROR: YOUTUBE_API_KEY environment variable is not set.\n"
            "  Local:          export YOUTUBE_API_KEY='your-key-here'\n"
            "  GitHub Actions: add it as a repository secret named YOUTUBE_API_KEY"
        )
    return key


def fetch_youtube_streams(api_key: str, start_date: str, end_date: str) -> list[dict]:
    """Search the Kalamazoo City TV channel for CRB live stream recordings.

    Uses the YouTube Data API v3 search endpoint, which returns live stream
    completedBefore/After results for a channel. Results are paginated.
    We search for 'Civil Rights Board' and filter by date in Python.
    """
    print(f"  [YouTube] Searching for '{YOUTUBE_SEARCH_QUERY}' streams...")

    # YouTube API expects RFC 3339 timestamps
    start_rfc = f"{start_date}T00:00:00Z"
    end_rfc   = f"{end_date}T23:59:59Z"

    base_url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "key":          api_key,
        "channelId":    YOUTUBE_CHANNEL_ID,
        "q":            YOUTUBE_SEARCH_QUERY,
        "type":         "video",
        "eventType":    "completed",        # completed live streams only
        "publishedAfter":  start_rfc,
        "publishedBefore": end_rfc,
        "maxResults":   50,
        "part":         "snippet",
        "order":        "date",
    }

    all_items = []
    page_token = None
    page = 1

    while True:
        if page_token:
            params["pageToken"] = page_token

        print(f"  [YouTube] Fetching page {page}...")
        r = requests.get(base_url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        items = data.get("items", [])
        all_items.extend(items)
        print(f"    got {len(items)} videos (running total: {len(all_items)})")

        page_token = data.get("nextPageToken")
        if not page_token:
            break
        page += 1

    print(f"  [YouTube] Total streams found: {len(all_items)}")
    return all_items


def youtube_items_to_recordings(items: list[dict]) -> list[dict]:
    """Convert raw YouTube API items to recording dicts keyed by date."""
    recordings = []
    for item in items:
        video_id    = item["id"]["videoId"]
        snippet     = item["snippet"]
        title       = snippet.get("title", "")
        published   = snippet.get("publishedAt", "")   # '2026-04-01T23:45:00Z'
        date_only   = published[:10] if published else None

        if not date_only:
            continue

        recordings.append({
            "date":        date_only,
            "display":     format_display_date(date_only),
            "youtube_id":  video_id,
            "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
            "title":       title,
        })
        print(f"    {date_only}  {video_id}  {title[:60]}")

    return recordings


def match_recordings_to_meetings(
    meetings:   list[dict],
    recordings: list[dict],
    tolerance:  int = YOUTUBE_DATE_TOLERANCE,
) -> tuple[list[dict], list[dict]]:
    """
    Try to pair each recording with the closest meeting within `tolerance` days.

    Returns:
        updated_meetings  - meetings list with youtube_id filled where matched
        unmatched         - recordings that didn't match any meeting (to keep
                            as standalone entries in the recordings array)
    """
    meetings = [dict(m) for m in meetings]  # don't mutate originals
    unmatched = []

    for rec in recordings:
        rec_date = datetime.strptime(rec["date"], "%Y-%m-%d").date()

        # Find the closest meeting within tolerance
        best_meeting = None
        best_delta   = timedelta(days=tolerance + 1)

        for m in meetings:
            m_date = datetime.strptime(m["date"], "%Y-%m-%d").date()
            delta  = abs(rec_date - m_date)
            if delta <= timedelta(days=tolerance) and delta < best_delta:
                best_delta   = delta
                best_meeting = m

        if best_meeting:
            # Only fill in if the meeting doesn't already have a youtube_id
            if not best_meeting.get("youtube_id"):
                best_meeting["youtube_id"]  = rec["youtube_id"]
                best_meeting["youtube_url"] = rec["youtube_url"]
                print(f"  ~ MATCHED:  {rec['date']} YouTube → {best_meeting['date']} meeting")
            else:
                print(f"  = KEPT:     {best_meeting['date']} already has youtube_id, skipping")
        else:
            print(f"  ? UNMATCHED: {rec['date']} {rec['youtube_id']} (no meeting within {tolerance} days)")
            unmatched.append(rec)

    return meetings, unmatched


# ---------------------------------------------------------------------------
# Upcoming meetings
# ---------------------------------------------------------------------------

def compute_upcoming_meetings(n: int = 6) -> list[dict]:
    """
    Generate the next N CRB meeting dates.

    CRB meets the 1st Wednesday of every other month:
    February, April, June, August, October, December.
    """
    meeting_months = {2, 4, 6, 8, 10, 12}
    today = date.today()
    results = []

    # Start from the current or next meeting month
    year  = today.year
    month = today.month

    # Walk forward month by month until we have n upcoming dates
    checked = 0
    while len(results) < n and checked < 36:   # 36 month safety cap
        if month in meeting_months:
            first_wed = first_weekday_of_month(year, month, weekday=2)  # 2 = Wednesday
            if first_wed >= today:
                results.append({
                    "date":    first_wed.strftime("%Y-%m-%d"),
                    "display": first_wed.strftime("%A, %B %#d, %Y"),
                    "time":    "5:00 PM",
                })
        month += 1
        if month > 12:
            month = 1
            year += 1
        checked += 1

    return results


def first_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Return the first occurrence of `weekday` (0=Mon … 6=Sun) in the month."""
    d = date(year, month, 1)
    days_ahead = weekday - d.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return d + timedelta(days=days_ahead)


# ---------------------------------------------------------------------------
# Merge logic  (mirrors your existing City Commission scraper)
# ---------------------------------------------------------------------------

def load_existing(path: Path) -> dict:
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "last_updated":     None,
        "upcoming_meetings": [],
        "meetings":         [],
        "recordings":       [],
    }


def smart_merge_record(existing: dict, scraped: dict) -> tuple[dict, list[str]]:
    result    = dict(scraped)
    preserved = []
    for field in PRESERVE_IF_SCRAPE_EMPTY:
        if not result.get(field) and existing.get(field):
            result[field] = existing[field]
            preserved.append(field)
    return result, preserved


def merge_meetings(existing: list[dict], scraped: list[dict]) -> tuple[list[dict], dict]:
    stats = {"added": 0, "updated": 0, "unchanged": 0, "preserved": 0}

    def key(m):
        if m.get("event_id") is not None:
            return ("id", m["event_id"])
        return ("fallback", m.get("date"))

    by_key = {key(m): m for m in existing}

    for s in scraped:
        k = key(s)
        if k not in by_key:
            by_key[k] = s
            stats["added"] += 1
            print(f"  + NEW:      {s['date']}")
            continue

        merged, preserved = smart_merge_record(by_key[k], s)
        if preserved:
            stats["preserved"] += len(preserved)
            print(f"  = PROTECTED:{merged['date']}  kept manual {', '.join(preserved)}")

        changed = any(by_key[k].get(f) != merged.get(f)
                      for f in ("date", "url", "cancelled", "minutes_url", "agenda_url", "youtube_id"))
        if changed:
            by_key[k] = merged
            stats["updated"] += 1
            print(f"  ~ UPDATED:  {merged['date']}")
        else:
            stats["unchanged"] += 1

    merged_list = sorted(by_key.values(), key=lambda m: m["date"], reverse=True)
    return merged_list, stats


def merge_recordings(existing: list[dict], new_recs: list[dict]) -> list[dict]:
    """Merge standalone recording entries (unmatched YouTube videos)."""
    by_id = {r["youtube_id"]: r for r in existing}
    added = 0
    for r in new_recs:
        if r["youtube_id"] not in by_id:
            by_id[r["youtube_id"]] = r
            added += 1
            print(f"  + NEW RECORDING: {r['date']} {r['youtube_id']}")
    if added == 0:
        print("  (no new standalone recordings)")
    return sorted(by_id.values(), key=lambda r: r["date"], reverse=True)


# ---------------------------------------------------------------------------
# Argument parsing & date window
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape Kalamazoo Civil Rights Board meetings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python crb_scraper.py                              # Default: last 6 months\n"
            "  python crb_scraper.py --start-date 2020-01-01      # Full backfill\n"
            "  python crb_scraper.py --start-date 2024-01-01 --end-date 2025-01-01\n"
        ),
    )
    parser.add_argument("--start-date", help="Start of window (YYYY-MM-DD).")
    parser.add_argument("--end-date",   help="End of window (YYYY-MM-DD). Defaults to today.")
    return parser.parse_args()


def determine_window(args) -> tuple[str, str, str]:
    now = datetime.now(timezone.utc)
    if args.start_date:
        start_iso = args.start_date
        end_iso   = args.end_date or now.strftime("%Y-%m-%d")
        return start_iso, end_iso, "BACKFILL"

    start_dt  = now - timedelta(days=LOOKBACK_MONTHS * 30)
    cutoff_dt = datetime.fromisoformat(CUTOFF_DATE).replace(tzinfo=timezone.utc)
    if start_dt < cutoff_dt:
        start_dt = cutoff_dt

    start_iso = start_dt.strftime("%Y-%m-%d")
    end_iso   = args.end_date or now.strftime("%Y-%m-%d")
    return start_iso, end_iso, "DEFAULT"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    start_iso, end_iso, mode = determine_window(args)
    api_key = get_youtube_api_key()

    print(f"CRB Scraper [{mode} mode]")
    print(f"Window: {start_iso} → {end_iso}")
    print()

    # ── Step 1: CivicClerk meetings ─────────────────────────────────────────
    print("── Step 1: Fetching CivicClerk events...")
    cc_url    = build_civicclerk_url(start_iso, end_iso)
    all_events = fetch_all_civicclerk_events(cc_url)
    crb_events = [e for e in all_events if is_crb_event(e)]
    print(f"  CRB events found: {len(crb_events)}")
    print()

    scraped_meetings = [transform_civicclerk_event(e) for e in crb_events]
    for m in scraped_meetings:
        ag = "agenda"    if m["agenda_url"]  else "no agenda"
        mn = "minutes"   if m["minutes_url"] else "no minutes"
        ca = " CANCELLED" if m["cancelled"]  else ""
        print(f"  {m['date']}  [{ag}, {mn}]{ca}")
    print()

    # ── Step 2: YouTube recordings ──────────────────────────────────────────
    print("── Step 2: Fetching YouTube streams...")
    yt_items   = fetch_youtube_streams(api_key, start_iso, end_iso)
    recordings = youtube_items_to_recordings(yt_items)
    print()

    # ── Step 3: Match recordings to meetings ────────────────────────────────
    print("── Step 3: Matching recordings to meetings...")
    scraped_meetings, unmatched_recs = match_recordings_to_meetings(
        scraped_meetings, recordings
    )
    print()

    # ── Step 4: Merge with existing JSON ────────────────────────────────────
    print("── Step 4: Merging with existing data/crb.json...")
    existing = load_existing(OUTPUT_PATH)

    merged_meetings, m_stats = merge_meetings(existing.get("meetings", []), scraped_meetings)
    merged_recordings = merge_recordings(existing.get("recordings", []), unmatched_recs)
    print()
    print(f"  Meetings  — added: {m_stats['added']}  updated: {m_stats['updated']}  unchanged: {m_stats['unchanged']}  fields protected: {m_stats['preserved']}")
    print()

    # ── Step 5: Recompute upcoming meetings ─────────────────────────────────
    print("── Step 5: Computing upcoming meetings...")
    upcoming = compute_upcoming_meetings(n=6)
    for u in upcoming:
        print(f"  {u['date']}  {u['display']}")
    print()

    # ── Step 6: Write output ────────────────────────────────────────────────
    output = {
        "last_updated":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "upcoming_meetings": upcoming,
        "meetings":          merged_meetings,
        "recordings":        merged_recordings,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {OUTPUT_PATH}")
    print(f"  {len(merged_meetings)} total meetings")
    print(f"  {len(merged_recordings)} standalone recordings")
    print(f"  {len(upcoming)} upcoming dates")


if __name__ == "__main__":
    main()
