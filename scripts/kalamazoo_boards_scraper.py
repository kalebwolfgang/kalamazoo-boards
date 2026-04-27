"""
Kalamazoo Boards & Commissions — Unified Ongoing Scraper
=========================================================

Runs all configured boards, checking the last 6 months for new meetings.
This is the script GitHub Actions runs on a weekly cron.

To add a new board, add one entry to BOARDS below. That's it.

Usage:
    python scraper.py               # All boards, last 6 months
    python scraper.py --board crb   # One board only, last 6 months

YouTube API key is only required for boards that have youtube: true.
Set it as an environment variable before running:
    set YOUTUBE_API_KEY=your-key-here
"""

import argparse
import json
import os
import re
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

import requests


# ---------------------------------------------------------------------------
# Board configuration
# ---------------------------------------------------------------------------
# Add a new board here when you're ready to automate it.
#
# Required fields:
#   key             - short slug, matches data/{key}.json
#   name            - display name for logging
#   category_id     - CivicClerk eventCategoryId
#   keywords        - list of strings to match against eventName (lowercase)
#   output          - path to the JSON file
#   youtube         - True if this board has YouTube recordings
#
# YouTube-only fields (only needed when youtube: True):
#   youtube_channel_id    - channel to search
#   youtube_search_query  - search term
#   youtube_title_filter  - list of strings; video title must contain at least one
#   youtube_tolerance     - days either side of a meeting to accept a match

BOARDS = [
    {
        "key":         "crb",
        "name":        "Civil Rights Board",
        "category_id": 32,
        "keywords":    ["civil rights board", "civil rights"],
        "output":      Path("data") / "crb.json",
        "youtube":     True,
        "youtube_channel_id":   "UCIgXSSXLSDxThVaaiRMsR5Q",
        "youtube_search_query": "Civil Rights Board",
        "youtube_title_filter": ["civil rights board", "civil rights"],
        "youtube_tolerance":    3,
        "schedule":    ("bimonthly", "wednesday", 1, [2, 4, 6, 8, 10, 12]),
    },
    {
        "key":         "bra",
        "name":        "Brownfield Redevelopment Authority",
        "category_id": 34,
        "keywords":    ["brownfield redevelopment authority"],
        "output":      Path("data") / "bra.json",
        "youtube":     False,
        "schedule":    ("monthly", "thursday", 3, None),
    },
    {
        "key":         "cpsrab",
        "name":        "Citizens Public Safety Review and Appeal Board",
        "scraper_type": "youtube_only",   # city website blocks scraping; meetings maintained manually
        "category_id": None,
        "keywords":    [],
        "output":      Path("data") / "cpsrab.json",
        "youtube":     True,
        "youtube_channel_id":   "UCIgXSSXLSDxThVaaiRMsR5Q",
        "youtube_search_query": "Citizens Public Safety Review and Appeal Board",
        "youtube_title_filter": ["citizens public safety", "cpsrab"],
        "youtube_tolerance":    3,
        "schedule":    ("monthly", "tuesday", 2, None),
    },
    {
        "key":         "dda",
        "name":        "Downtown Development Authority",
        "category_id": 38,
        "keywords":    ["downtown development authority", "dda"],
        "output":      Path("data") / "dda.json",
        "youtube":     False,
        "schedule":    ("monthly", "monday", 3, None),
    },
    {
        "key":         "dega",
        "name":        "Downtown Economic Growth Authority",
        "category_id": 39,
        "keywords":    ["downtown economic growth authority", "dega"],
        "output":      Path("data") / "dega.json",
        "youtube":     False,
        "schedule":    ("monthly", "monday", 3, None),
    },
]


# ---------------------------------------------------------------------------
# Global config
# ---------------------------------------------------------------------------

CIVICCLERK_TENANT    = "kalamazoomi"
LOOKBACK_MONTHS      = 6
PRESERVE_IF_EMPTY    = ("agenda_url", "minutes_url")


# ---------------------------------------------------------------------------
# CivicClerk helpers
# ---------------------------------------------------------------------------

def build_cc_url(start_date: str, end_date: str) -> str:
    base  = f"https://{CIVICCLERK_TENANT}.api.civicclerk.com/v1/Events"
    query = (
        f"?$filter=startDateTime ge {start_date} and startDateTime lt {end_date}"
        f"&$orderby=startDateTime desc, eventName asc"
    )
    return base + query


def fetch_all_cc_events(url: str) -> list[dict]:
    all_events, page = [], 1
    while url:
        print(f"    [CivicClerk] page {page}...")
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        events = data.get("value", [])
        all_events.extend(events)
        url  = data.get("@odata.nextLink")
        page += 1
    return all_events


def find_file_id(published_files: list, file_type: str):
    for f in published_files or []:
        if f.get("type") == file_type:
            return f.get("fileId")
    return None


def build_doc_url(event_id: int, file_id: int) -> str:
    return (
        f"https://{CIVICCLERK_TENANT}.portal.civicclerk.com"
        f"/event/{event_id}/files/agenda/{file_id}"
    )


def format_display_date(iso: str) -> str:
    d = datetime.strptime(iso, "%Y-%m-%d")
    return d.strftime("%B %#d, %Y")   # %#d = no leading zero on Windows


def normalize_meeting_type(event_name: str) -> str:
    name_lower = event_name.lower()
    if "special" in name_lower:
        return "Special Meeting"
    return event_name


def transform_event(event: dict, board: dict):
    """Transform a raw CivicClerk event into our JSON shape. Returns None if no docs."""
    event_id        = event["id"]
    date_only       = event["startDateTime"].split("T")[0]
    published_files = event.get("publishedFiles", [])
    agenda_file_id  = find_file_id(published_files, "Agenda")
    minutes_file_id = find_file_id(published_files, "Minutes")
    name_lower      = event.get("eventName", "").lower()
    cancelled       = "cancel" in name_lower

    if not agenda_file_id and not minutes_file_id:
        return None

    link_label = "Agenda & Minutes" if minutes_file_id else "Agenda"

    record = {
        "date":        date_only,
        "display":     format_display_date(date_only),
        "event_id":    event_id,
        "url":         build_doc_url(event_id, agenda_file_id) if agenda_file_id
                       else f"https://{CIVICCLERK_TENANT}.portal.civicclerk.com/event/{event_id}/overview",
        "link_label":  link_label,
        "cancelled":   cancelled,
        "minutes_url": build_doc_url(event_id, minutes_file_id) if minutes_file_id else None,
        "agenda_url":  build_doc_url(event_id, agenda_file_id)  if agenda_file_id  else None,
    }

    # Add meeting_type for boards that use it (non-CRB)
    if board.get("key") != "crb":
        record["meeting_type"] = normalize_meeting_type(event.get("eventName", ""))

    return record


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


def fetch_youtube_streams(api_key: str, board: dict, start_date: str, end_date: str) -> list[dict]:
    print(f"    [YouTube] Searching '{board['youtube_search_query']}'...")
    base_url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "key":            api_key,
        "channelId":      board["youtube_channel_id"],
        "q":              board["youtube_search_query"],
        "type":           "video",
        "eventType":      "completed",
        "publishedAfter": f"{start_date}T00:00:00Z",
        "publishedBefore":f"{end_date}T23:59:59Z",
        "maxResults":     50,
        "part":           "snippet",
        "order":          "date",
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
    title_filter = board.get("youtube_title_filter", [])
    for item in all_items:
        video_id  = item["id"]["videoId"]
        snippet   = item["snippet"]
        title     = snippet.get("title", "")
        published = snippet.get("publishedAt", "")
        date_only = published[:10] if published else None
        if not date_only:
            continue
        title_lower = title.lower()
        if title_filter and not any(kw in title_lower for kw in title_filter):
            print(f"    SKIPPED (wrong board): {title[:60]}")
            continue
        recordings.append({
            "date":        date_only,
            "display":     format_display_date(date_only),
            "youtube_id":  video_id,
            "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
            "title":       title,
        })
        print(f"    {date_only}  {video_id}  {title[:55]}")

    return recordings


def match_recordings_to_meetings(meetings: list, recordings: list, tolerance: int) -> tuple:
    meetings  = [dict(m) for m in meetings]
    unmatched = []

    for rec in recordings:
        rec_date     = datetime.strptime(rec["date"], "%Y-%m-%d").date()
        best_meeting = None
        best_delta   = timedelta(days=tolerance + 1)

        for m in meetings:
            m_date = datetime.strptime(m["date"], "%Y-%m-%d").date()
            delta  = abs(rec_date - m_date)
            if delta <= timedelta(days=tolerance) and delta < best_delta:
                best_delta   = delta
                best_meeting = m

        if best_meeting:
            if not best_meeting.get("youtube_id"):
                best_meeting["youtube_id"]  = rec["youtube_id"]
                best_meeting["youtube_url"] = rec["youtube_url"]
                print(f"    MATCHED:  {rec['date']} → {best_meeting['date']}")
            else:
                print(f"    KEPT:     {best_meeting['date']} already has youtube_id")
        else:
            print(f"    UNMATCHED:{rec['date']} {rec['youtube_id']}")
            unmatched.append(rec)

    return meetings, unmatched


# ---------------------------------------------------------------------------
# Upcoming meetings
# ---------------------------------------------------------------------------

def nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """Return the nth occurrence (1-based) of weekday (0=Mon...6=Sun) in month."""
    d = date(year, month, 1)
    days_ahead = weekday - d.weekday()
    if days_ahead < 0:
        days_ahead += 7
    first = d + timedelta(days=days_ahead)
    return first + timedelta(weeks=n - 1)


def compute_upcoming(board: dict, n: int = 6) -> list[dict]:
    schedule = board.get("schedule")
    if not schedule:
        return []

    freq, weekday_name, nth, months = schedule
    weekday_map = {"monday": 0, "tuesday": 1, "wednesday": 2,
                   "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
    weekday = weekday_map[weekday_name.lower()]

    today   = date.today()
    results = []
    year    = today.year
    month   = today.month
    checked = 0

    while len(results) < n and checked < 36:
        if months is None or month in months:
            d = nth_weekday_of_month(year, month, weekday, nth)
            if d >= today:
                results.append({
                    "date":    d.strftime("%Y-%m-%d"),
                    "display": d.strftime("%A, %B %#d, %Y"),
                    "time":    board.get("time", "TBD"),
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


def smart_merge(existing: dict, scraped: dict) -> tuple:
    result, preserved = dict(scraped), []
    for field in PRESERVE_IF_EMPTY:
        if not result.get(field) and existing.get(field):
            result[field] = existing[field]
            preserved.append(field)
    return result, preserved


def merge_meetings(existing: list, scraped: list) -> tuple:
    stats = {"added": 0, "updated": 0, "unchanged": 0, "preserved": 0}

    def key(m):
        return ("id", m["event_id"]) if m.get("event_id") is not None else ("date", m.get("date"))

    by_key = {key(m): m for m in existing}

    for s in scraped:
        k = key(s)
        if k not in by_key:
            by_key[k] = s
            stats["added"] += 1
            print(f"    + NEW:     {s['date']}")
            continue

        merged, preserved = smart_merge(by_key[k], s)
        if preserved:
            stats["preserved"] += len(preserved)
        changed = any(by_key[k].get(f) != merged.get(f)
                      for f in ("url", "cancelled", "minutes_url", "agenda_url"))
        if changed:
            by_key[k] = merged
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1

    return sorted(by_key.values(), key=lambda m: m["date"], reverse=True), stats


def merge_recordings(existing: list, new_recs: list) -> list:
    by_id = {r["youtube_id"]: r for r in existing}
    for r in new_recs:
        if r["youtube_id"] not in by_id:
            by_id[r["youtube_id"]] = r
            print(f"    + NEW RECORDING: {r['date']} {r['youtube_id']}")
    return sorted(by_id.values(), key=lambda r: r["date"], reverse=True)


# ---------------------------------------------------------------------------
# Per-board run
# ---------------------------------------------------------------------------

def run_youtube_only_board(board: dict, start_iso: str, end_iso: str, api_key: str) -> None:
    """For boards where meetings are manually maintained and only YouTube is scraped."""
    name = board["name"]
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    print("  (meetings manually maintained — scraping YouTube only)")

    print("  Step 1: Fetching YouTube recordings...")
    recordings = fetch_youtube_streams(api_key, board, start_iso, end_iso)
    print(f"    Found {len(recordings)} recordings in window")

    print("  Step 2: Merging...")
    existing          = load_existing(board["output"])
    merged_recordings = merge_recordings(existing.get("recordings", []), recordings)

    upcoming = compute_upcoming(board)

    output = {
        "last_updated":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "upcoming_meetings": upcoming,
        "meetings":          existing.get("meetings", []),
        "recordings":        merged_recordings,
    }

    board["output"].parent.mkdir(parents=True, exist_ok=True)
    with board["output"].open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"  Wrote {board['output']}  ({len(existing.get('meetings', []))} meetings preserved, {len(merged_recordings)} recordings)")


def run_board(board: dict, start_iso: str, end_iso: str, api_key: str | None) -> None:
    if board.get("scraper_type") == "youtube_only":
        run_youtube_only_board(board, start_iso, end_iso, api_key)
        return

    name = board["name"]
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")

    print("  Step 1: Fetching CivicClerk events...")
    cc_url     = build_cc_url(start_iso, end_iso)
    all_events = fetch_all_cc_events(cc_url)
    board_events = [
        e for e in all_events
        if e.get("eventCategoryId") == board["category_id"]
        and any(kw in e.get("eventName", "").lower() for kw in board["keywords"])
    ]
    print(f"    Found {len(board_events)} events")

    scraped = [m for m in (transform_event(e, board) for e in board_events) if m is not None]
    print(f"    {len(scraped)} with documents")

    unmatched_recs = []
    if board.get("youtube") and api_key:
        print("  Step 2: Fetching YouTube streams...")
        recordings = fetch_youtube_streams(api_key, board, start_iso, end_iso)
        unmatched_recs = recordings

    print("  Step 3: Merging...")
    existing   = load_existing(board["output"])
    merged_meetings, stats = merge_meetings(existing.get("meetings", []), scraped)
    print(f"    added: {stats['added']}  updated: {stats['updated']}  unchanged: {stats['unchanged']}")

    merged_recordings = []
    if board.get("youtube"):
        merged_recordings = merge_recordings(existing.get("recordings", []), unmatched_recs)

    upcoming = compute_upcoming(board)

    output = {
        "last_updated":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "upcoming_meetings": upcoming,
        "meetings":          merged_meetings,
    }
    if board.get("youtube"):
        output["recordings"] = merged_recordings

    board["output"].parent.mkdir(parents=True, exist_ok=True)
    with board["output"].open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"  Wrote {board['output']}  ({len(merged_meetings)} meetings)")


# ---------------------------------------------------------------------------
# Time / meeting times per board
# ---------------------------------------------------------------------------

BOARD_TIMES = {
    "crb":    "5:00 PM",
    "bra":    "7:45 AM \u2013 9:30 AM",
    "cpsrab": "6:00 PM \u2013 8:00 PM",
    "dda":    "3:00 PM \u2013 5:00 PM",
    "dega":   "3:00 PM \u2013 5:00 PM",
}

for b in BOARDS:
    b["time"] = BOARD_TIMES.get(b["key"], "TBD")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Scrape all Kalamazoo boards (last 6 months).")
    parser.add_argument("--board", help="Run only this board key (e.g. crb, bra).")
    return parser.parse_args()


def main():
    args    = parse_args()
    now     = datetime.now(timezone.utc)
    start_dt = now - timedelta(days=LOOKBACK_MONTHS * 30)
    start_iso = start_dt.strftime("%Y-%m-%d")
    end_iso   = now.strftime("%Y-%m-%d")

    print(f"Unified Scraper — window: {start_iso} → {end_iso}")

    boards_to_run = BOARDS
    if args.board:
        boards_to_run = [b for b in BOARDS if b["key"] == args.board]
        if not boards_to_run:
            raise SystemExit(f"Unknown board key: {args.board}. Available: {[b['key'] for b in BOARDS]}")

    needs_youtube = any(b.get("youtube") for b in boards_to_run)
    api_key       = get_youtube_key() if needs_youtube else None

    for board in boards_to_run:
        run_board(board, start_iso, end_iso, api_key)

    print("\nDone.")


if __name__ == "__main__":
    main()
