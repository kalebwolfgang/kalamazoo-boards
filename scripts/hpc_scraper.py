"""
Kalamazoo Historic Preservation Commission Meeting Scraper
==========================================================

Pulls HPC meetings from the CivicClerk API and past recordings from the
Kalamazoo City TV YouTube channel, then writes everything to data/hpc.json.

CivicClerk category ID: 36
YouTube channel: UCIgXSSXLSDxThVaaiRMsR5Q
Meeting schedule: 2nd Wednesday of each month at 6:00 PM

Requires the YOUTUBE_API_KEY environment variable to be set.
Never put the key in this file or commit it to GitHub.

Default mode (no arguments):
    Fetches the last 6 months. Merges into existing data/hpc.json.
    This is what GitHub Actions runs on a weekly cron.

    python hpc_scraper.py

Backfill mode:
    Fetches everything from the given date forward.

    python hpc_scraper.py --start-date 2020-01-01
"""

import argparse
import json
import os
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

import re
import requests


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CIVICCLERK_TENANT    = "kalamazoomi"
HPC_CATEGORY_ID      = 36
HPC_KEYWORDS         = ["historic preservation commission"]

YOUTUBE_CHANNEL_ID   = "UCIgXSSXLSDxThVaaiRMsR5Q"
YOUTUBE_SEARCH_QUERY = "Historic Preservation Commission"
YOUTUBE_TITLE_FILTER = ["historic preservation commission", "historical preservation commission"]
YOUTUBE_DATE_TOLERANCE = 3

LOOKBACK_MONTHS      = 6
CUTOFF_DATE          = "2020-01-01"

OUTPUT_PATH          = Path("data") / "hpc.json"

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


def is_hpc_event(event: dict) -> bool:
    if event.get("eventCategoryId") != HPC_CATEGORY_ID:
        return False
    name_lower = event.get("eventName", "").lower()
    return any(kw in name_lower for kw in HPC_KEYWORDS)


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


def format_display_date(iso: str) -> str:
    d = datetime.strptime(iso, "%Y-%m-%d")
    return d.strftime("%B %#d, %Y")


def transform_civicclerk_event(event: dict) -> dict | None:
    event_id        = event["id"]
    date_only       = event["startDateTime"].split("T")[0]
    published_files = event.get("publishedFiles", [])
    agenda_file_id  = find_file_id(published_files, "Agenda")
    minutes_file_id = find_file_id(published_files, "Minutes")
    name_lower      = event.get("eventName", "").lower()
    cancelled       = "cancel" in name_lower

    if not agenda_file_id and not minutes_file_id:
        return None

    has_minutes = bool(minutes_file_id)
    link_label  = "Agenda & Minutes" if has_minutes else "Agenda"

    return {
        "date":         date_only,
        "display":      format_display_date(date_only),
        "event_id":     event_id,
        "url":          (
            build_document_url(event_id, agenda_file_id)
            if agenda_file_id
            else f"https://{CIVICCLERK_TENANT}.portal.civicclerk.com/event/{event_id}/overview"
        ),
        "link_label":   link_label,
        "meeting_type": "Historic Preservation Commission",
        "cancelled":    cancelled,
        "minutes_url":  build_document_url(event_id, minutes_file_id) if minutes_file_id else None,
        "agenda_url":   build_document_url(event_id, agenda_file_id)  if agenda_file_id  else None,
        "youtube_id":   None,
        "youtube_url":  None,
    }


# ---------------------------------------------------------------------------
# YouTube helpers
# ---------------------------------------------------------------------------

def get_youtube_api_key() -> str:
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "ERROR: YOUTUBE_API_KEY environment variable is not set.\n"
            "  Local:          set YOUTUBE_API_KEY=your-key-here\n"
            "  GitHub Actions: add it as a repository secret named YOUTUBE_API_KEY"
        )
    return key


def fetch_youtube_streams(api_key: str, start_date: str, end_date: str) -> list[dict]:
    print(f"  [YouTube] Searching for '{YOUTUBE_SEARCH_QUERY}' streams...")
    start_rfc = f"{start_date}T00:00:00Z"
    end_rfc   = f"{end_date}T23:59:59Z"

    base_url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "key":             api_key,
        "channelId":       YOUTUBE_CHANNEL_ID,
        "q":               YOUTUBE_SEARCH_QUERY,
        "type":            "video",
        "eventType":       "completed",
        "publishedAfter":  start_rfc,
        "publishedBefore": end_rfc,
        "maxResults":      50,
        "part":            "snippet",
        "order":           "date",
    }

    all_items  = []
    page_token = None
    page       = 1

    while True:
        if page_token:
            params["pageToken"] = page_token
        print(f"  [YouTube] Fetching page {page}...")
        r = requests.get(base_url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        all_items.extend(data.get("items", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        page += 1

    filtered = [
        item for item in all_items
        if any(kw in item["snippet"]["title"].lower() for kw in YOUTUBE_TITLE_FILTER)
    ]
    print(f"  [YouTube] {len(filtered)} matching videos after title filter")
    return filtered


def youtube_items_to_recordings(items: list[dict]) -> list[dict]:
    recordings = []
    date_pattern = re.compile(
        r'(January|February|March|April|May|June|July|August|September|October|November|December)'
        r'\s+\d{1,2},?\s+\d{4}'
    )
    for item in items:
        vid_id = item["id"]["videoId"]
        title  = item["snippet"]["title"]
        pub    = item["snippet"]["publishedAt"][:10]
        match  = date_pattern.search(title)
        if match:
            try:
                rec_date = datetime.strptime(match.group().replace(",", ""), "%B %d %Y").date()
            except Exception:
                rec_date = datetime.strptime(pub, "%Y-%m-%d").date()
        else:
            rec_date = datetime.strptime(pub, "%Y-%m-%d").date()
        recordings.append({
            "date":        rec_date.strftime("%Y-%m-%d"),
            "display":     format_display_date(rec_date.strftime("%Y-%m-%d")),
            "youtube_id":  vid_id,
            "youtube_url": f"https://www.youtube.com/watch?v={vid_id}",
            "title":       title,
        })
    return recordings

def match_recordings_to_meetings(
    meetings: list[dict], recordings: list[dict]
) -> tuple[list[dict], list[dict]]:
    meeting_by_date = {m["date"]: m for m in meetings}

    for rec in recordings:
        rec_date = datetime.strptime(rec["date"], "%Y-%m-%d").date()
        matched  = False
        for delta in range(YOUTUBE_DATE_TOLERANCE + 1):
            for sign in (0, -1, 1):
                candidate = (rec_date + timedelta(days=delta * sign)).strftime("%Y-%m-%d")
                if candidate in meeting_by_date:
                    m = meeting_by_date[candidate]
                    if not m.get("youtube_id"):
                        m["youtube_id"]  = rec["youtube_id"]
                        m["youtube_url"] = rec["youtube_url"]
                        print(f"  [Match] {rec['date']} -> meeting {candidate}")
                    matched = True
                    break
            if matched:
                break
        if not matched:
            print(f"  [Unmatched] {rec['date']} {rec['youtube_id']}")

    # Return ALL recordings for the master library, not just unmatched
    return meetings, recordings


# ---------------------------------------------------------------------------
# Upcoming meetings: 2nd Wednesday of each month
# ---------------------------------------------------------------------------

def nth_weekday_of_month(year: int, month: int, n: int, weekday: int) -> date:
    """Return the nth occurrence (1-based) of weekday (0=Mon) in the month."""
    d = date(year, month, 1)
    days_ahead = weekday - d.weekday()
    if days_ahead < 0:
        days_ahead += 7
    first = d + timedelta(days=days_ahead)
    return first + timedelta(weeks=n - 1)


def compute_upcoming_meetings(n: int = 6) -> list[dict]:
    today   = date.today()
    results = []
    year    = today.year
    month   = today.month
    checked = 0

    while len(results) < n and checked < 24:
        meeting_date = nth_weekday_of_month(year, month, 2, 2)  # 2nd Wednesday
        if meeting_date >= today:
            results.append({
                "date":    meeting_date.strftime("%Y-%m-%d"),
                "display": meeting_date.strftime("%A, %B %#d, %Y"),
                "time":    "6:00 PM \u2013 8:00 PM",
            })
        month += 1
        if month > 12:
            month = 1
            year += 1
        checked += 1

    return results


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

def load_existing(path: Path) -> dict:
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_updated": None, "upcoming_meetings": [], "meetings": [], "recordings": []}


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
            print(f"  + NEW:      {s['date']} {s['meeting_type']}")
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
            print(f"  ~ UPDATED:  {merged['date']} {merged['meeting_type']}")
        else:
            stats["unchanged"] += 1

    merged_list = sorted(by_key.values(), key=lambda m: m["date"], reverse=True)
    return merged_list, stats


def merge_recordings(existing: list[dict], new_recs: list[dict]) -> list[dict]:
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
        description="Scrape Kalamazoo Historic Preservation Commission meetings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python hpc_scraper.py                              # Default: last 6 months\n"
            "  python hpc_scraper.py --start-date 2020-01-01      # Full backfill\n"
        ),
    )
    parser.add_argument("--start-date", help="Start of window (YYYY-MM-DD).")
    parser.add_argument("--end-date",   help="End of window (YYYY-MM-DD). Defaults to today.")
    return parser.parse_args()


def determine_window(args) -> tuple[str, str, str]:
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

def main() -> None:
    args = parse_args()
    start_iso, end_iso, mode = determine_window(args)
    api_key = get_youtube_api_key()

    print(f"HPC Scraper [{mode} mode]")
    print(f"Window: {start_iso} -> {end_iso}")
    print()

    print("-- Step 1: Fetching CivicClerk events...")
    cc_url     = build_civicclerk_url(start_iso, end_iso)
    all_events = fetch_all_civicclerk_events(cc_url)
    hpc_events = [e for e in all_events if is_hpc_event(e)]
    print(f"  HPC events found: {len(hpc_events)}")
    print()

    scraped_meetings = [m for m in (transform_civicclerk_event(e) for e in hpc_events) if m is not None]
    for m in scraped_meetings:
        ag = "agenda"    if m["agenda_url"]  else "no agenda"
        mn = "minutes"   if m["minutes_url"] else "no minutes"
        ca = " CANCELLED" if m["cancelled"]  else ""
        print(f"  {m['date']}  [{ag}, {mn}]{ca}")
    print()

    print("-- Step 2: Fetching YouTube streams...")
    yt_items   = fetch_youtube_streams(api_key, start_iso, end_iso)
    recordings = youtube_items_to_recordings(yt_items)
    print()

    print("-- Step 3: Matching recordings to meetings...")
    scraped_meetings, unmatched_recs = match_recordings_to_meetings(scraped_meetings, recordings)
    print()

    print("-- Step 4: Merging with existing data/hpc.json...")
    existing          = load_existing(OUTPUT_PATH)
    merged_meetings, m_stats = merge_meetings(existing.get("meetings", []), scraped_meetings)
    merged_recordings = merge_recordings(existing.get("recordings", []), unmatched_recs)
    print()
    print(f"  Meetings -- added: {m_stats['added']}  updated: {m_stats['updated']}  unchanged: {m_stats['unchanged']}  fields protected: {m_stats['preserved']}")
    print()

    print("-- Step 5: Computing upcoming meetings...")
    upcoming = compute_upcoming_meetings(n=6)
    for u in upcoming:
        print(f"  {u['date']}  {u['display']}")
    print()

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