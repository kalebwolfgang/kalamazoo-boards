"""
Kalamazoo Citizens Public Safety Review and Appeal Board Scraper
================================================================

Pulls CPSRAB meeting documents from the City of Kalamazoo website and
recordings from the Kalamazoo City TV YouTube channel.

The city website (kalamazoocity.org/Government/Boards-Commissions/Minutes-Agendas)
lists CPSRAB agendas and minutes as direct PDF links. This scraper parses
that page and groups links by date into agenda_url / minutes_url pairs.

YouTube recordings are searched and matched to meetings by date.

Requires: pip install requests beautifulsoup4

Default mode (no arguments):
    Checks the last 6 months. Merges into existing data/cpsrab.json.

    python cpsrab_scraper.py

Backfill mode:
    Pulls everything available on the city website + YouTube from that date.

    python cpsrab_scraper.py --start-date 2021-01-01
"""

import argparse
import json
import os
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

import requests


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

YOUTUBE_CHANNEL_ID   = "UCIgXSSXLSDxThVaaiRMsR5Q"
YOUTUBE_SEARCH_QUERY = "Citizens Public Safety Review and Appeal Board"
YOUTUBE_TITLE_FILTER = ["citizens public safety", "cpsrab"]
YOUTUBE_TOLERANCE    = 3   # days either side
def format_display_date(d) -> str:
    return d.strftime("%B %#d, %Y")

LOOKBACK_MONTHS  = 6
CUTOFF_DATE      = "2021-01-01"
OUTPUT_PATH      = Path("data") / "cpsrab.json"




# ---------------------------------------------------------------------------
# YouTube helpers
# ---------------------------------------------------------------------------

def get_youtube_key() -> str:
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "ERROR: YOUTUBE_API_KEY environment variable is not set.\n"
            "  Local:          set YOUTUBE_API_KEY=your-key-here\n"
            "  GitHub Actions: add it as a repository secret"
        )
    return key


def fetch_youtube_recordings(api_key: str, start_date: str, end_date: str) -> list[dict]:
    print(f"  [YouTube] Searching '{YOUTUBE_SEARCH_QUERY}'...")
    base_url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "key":             api_key,
        "channelId":       YOUTUBE_CHANNEL_ID,
        "q":               YOUTUBE_SEARCH_QUERY,
        "type":            "video",
        "eventType":       "completed",
        "publishedAfter":  f"{start_date}T00:00:00Z",
        "publishedBefore": f"{end_date}T23:59:59Z",
        "maxResults":      50,
        "part":            "snippet",
        "order":           "date",
    }

    all_items, page_token, page = [], None, 1
    while True:
        if page_token:
            params["pageToken"] = page_token
        r = requests.get(base_url, params=params, timeout=30)
        r.raise_for_status()
        data       = r.json()
        items      = data.get("items", [])
        all_items.extend(items)
        page_token = data.get("nextPageToken")
        page      += 1
        if not page_token:
            break

    recordings = []
    for item in all_items:
        video_id  = item["id"]["videoId"]
        snippet   = item["snippet"]
        title     = snippet.get("title", "")
        published = snippet.get("publishedAt", "")
        date_only = published[:10] if published else None
        if not date_only:
            continue

        title_lower = title.lower()
        if not any(kw in title_lower for kw in YOUTUBE_TITLE_FILTER):
            print(f"    SKIPPED (wrong board): {title[:60]}")
            continue

        recordings.append({
            "date":        date_only,
            "display":     format_display_date(datetime.strptime(date_only, "%Y-%m-%d").date()),
            "youtube_id":  video_id,
            "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
            "title":       title,
        })
        print(f"    {date_only}  {video_id}  {title[:55]}")

    return recordings


# ---------------------------------------------------------------------------
# Upcoming meetings
# ---------------------------------------------------------------------------

def compute_upcoming(n: int = 6) -> list[dict]:
    """CPSRAB meets the 2nd Tuesday of every month."""
    today   = date.today()
    results = []
    year    = today.year
    month   = today.month
    checked = 0

    while len(results) < n and checked < 24:
        # Find 2nd Tuesday
        d = date(year, month, 1)
        days_ahead = 1 - d.weekday()   # 1 = Tuesday
        if days_ahead < 0:
            days_ahead += 7
        first_tue = d + timedelta(days=days_ahead)
        second_tue = first_tue + timedelta(weeks=1)

        if second_tue >= today:
            results.append({
                "date":    second_tue.strftime("%Y-%m-%d"),
                "display": second_tue.strftime("%A, %B %#d, %Y"),
                "time":    "6:00 PM \u2013 8:00 PM",
            })

        month += 1
        if month > 12:
            month = 1
            year += 1
        checked += 1

    return results


# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------

def load_existing(path: Path) -> dict:
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_updated": None, "upcoming_meetings": [], "meetings": [], "recordings": []}


def merge_recordings(existing: list, new_recs: list) -> list:
    by_id = {r["youtube_id"]: r for r in existing}
    for r in new_recs:
        if r["youtube_id"] not in by_id:
            by_id[r["youtube_id"]] = r
            print(f"    + NEW RECORDING: {r['date']} {r['youtube_id']}")
    return sorted(by_id.values(), key=lambda r: r["date"], reverse=True)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape CPSRAB meetings from the city website + YouTube.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python cpsrab_scraper.py                              # Default: last 6 months\n"
            "  python cpsrab_scraper.py --start-date 2021-01-01      # Full backfill\n"
        ),
    )
    parser.add_argument("--start-date", help="Start of window (YYYY-MM-DD).")
    parser.add_argument("--end-date",   help="End of window (YYYY-MM-DD). Defaults to today.")
    return parser.parse_args()


def determine_window(args):
    now = datetime.now(timezone.utc)
    if args.start_date:
        return args.start_date, args.end_date or now.strftime("%Y-%m-%d"), "BACKFILL"

    start_dt  = now - timedelta(days=LOOKBACK_MONTHS * 30)
    cutoff_dt = datetime.fromisoformat(CUTOFF_DATE).replace(tzinfo=timezone.utc)
    if start_dt < cutoff_dt:
        start_dt = cutoff_dt

    return start_dt.strftime("%Y-%m-%d"), args.end_date or now.strftime("%Y-%m-%d"), "DEFAULT"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    start_iso, end_iso, mode = determine_window(args)
    api_key = get_youtube_key()

    print(f"CPSRAB Scraper [{mode} mode]")
    print(f"Window: {start_iso} → {end_iso}")
    print()
    print("Note: Meeting documents (agendas/minutes) are maintained manually in")
    print("data/cpsrab.json — the city website blocks automated scraping.")
    print()

    # Step 1: YouTube recordings
    print("── Step 1: Fetching YouTube recordings...")
    recordings = fetch_youtube_recordings(api_key, start_iso, end_iso)
    print(f"  {len(recordings)} recordings found")
    print()

    # Step 2: Merge
    print("── Step 2: Merging with existing data/cpsrab.json...")
    existing = load_existing(OUTPUT_PATH)
    merged_recordings = merge_recordings(existing.get("recordings", []), recordings)
    print(f"  Recordings — total: {len(merged_recordings)}")
    print()

    # Step 3: Upcoming
    print("── Step 3: Computing upcoming meetings...")
    upcoming = compute_upcoming(n=6)
    for u in upcoming:
        print(f"  {u['date']}  {u['display']}")
    print()

    # Step 4: Write — preserve existing meetings, only update recordings + upcoming
    output = {
        "last_updated":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "upcoming_meetings": upcoming,
        "meetings":          existing.get("meetings", []),
        "recordings":        merged_recordings,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {OUTPUT_PATH}")
    print(f"  {len(output['meetings'])} meetings (manually maintained)")
    print(f"  {len(merged_recordings)} recordings (auto-updated)")


if __name__ == "__main__":
    main()