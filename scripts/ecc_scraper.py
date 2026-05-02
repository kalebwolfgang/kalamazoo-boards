"""
Kalamazoo Environmental Concerns Committee Scraper
===================================================

Pulls ECC meetings from the CivicClerk API and past recordings from the
Kalamazoo City TV YouTube channel, then writes everything to data/ecc.json.

Upcoming meetings are generated from CivicClerk future events (ECC meets
the 3rd Wednesday of every month).

Default mode (no arguments):
    Fetches the last 6 months. Merges into existing data/ecc.json.
    This is what GitHub Actions runs on a weekly cron.

    python ecc_scraper.py

Backfill mode:
    Fetches everything from the given date forward.

    python ecc_scraper.py --start-date 2023-01-01
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

CIVICCLERK_TENANT  = "kalamazoomi"
ECC_CATEGORY_ID    = 46
ECC_KEYWORDS       = ["environmental concerns committee", "environmental concerns"]

YOUTUBE_CHANNEL_ID   = "UCIgXSSXLSDxThVaaiRMsR5Q"
YOUTUBE_SEARCH_QUERY = "Environmental Concerns Committee"
YOUTUBE_TITLE_FILTER = ["environmental concerns committee", "environmental concerns"]
YOUTUBE_TOLERANCE    = 3

LOOKBACK_MONTHS    = 6
CUTOFF_DATE        = "2023-01-01"

OUTPUT_PATH        = Path("data") / "ecc.json"

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


def is_ecc_event(event: dict) -> bool:
    if event.get("eventCategoryId") != ECC_CATEGORY_ID:
        return False
    name_lower = event.get("eventName", "").lower()
    return any(kw in name_lower for kw in ECC_KEYWORDS)


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


def format_display_date_long(iso: str) -> str:
    d = datetime.strptime(iso, "%Y-%m-%d")
    return d.strftime("%A, %B %#d, %Y")


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
        "url":          build_document_url(event_id, agenda_file_id) if agenda_file_id
                        else f"https://{CIVICCLERK_TENANT}.portal.civicclerk.com/event/{event_id}/overview",
        "link_label":   link_label,
        "meeting_type": "Environmental Concerns Committee",
        "cancelled":    cancelled,
        "minutes_url":  build_document_url(event_id, minutes_file_id) if minutes_file_id else None,
        "agenda_url":   build_document_url(event_id, agenda_file_id)  if agenda_file_id  else None,
    }


def events_to_upcoming(events: list[dict]) -> list[dict]:
    upcoming = []
    for event in events:
        date_only = event["startDateTime"].split("T")[0]
        upcoming.append({
            "date":    date_only,
            "display": format_display_date_long(date_only),
            "time":    "4:30 PM \u2013 6:30 PM",
        })
    upcoming.sort(key=lambda m: m["date"])
    return upcoming


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

    all_items, page_token = [], None
    while True:
        if page_token:
            params["pageToken"] = page_token
        r = requests.get(base_url, params=params, timeout=30)
        r.raise_for_status()
        data       = r.json()
        all_items.extend(data.get("items", []))
        page_token = data.get("nextPageToken")
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
        if not any(kw in title.lower() for kw in YOUTUBE_TITLE_FILTER):
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


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

def load_existing(path: Path) -> dict:
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "last_updated":      None,
        "upcoming_meetings": [],
        "meetings":          [],
        "recordings":        [],
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
            print(f"  + NEW:      {s['date']} {s['meeting_type']}")
            continue

        merged, preserved = smart_merge_record(by_key[k], s)
        if preserved:
            stats["preserved"] += len(preserved)
            print(f"  = PROTECTED:{merged['date']}  kept manual {', '.join(preserved)}")

        changed = any(by_key[k].get(f) != merged.get(f)
                      for f in ("url", "cancelled", "minutes_url", "agenda_url"))
        if changed:
            by_key[k] = merged
            stats["updated"] += 1
            print(f"  ~ UPDATED:  {merged['date']}")
        else:
            stats["unchanged"] += 1

    merged_list = sorted(by_key.values(), key=lambda m: m["date"], reverse=True)
    return merged_list, stats


def merge_recordings(existing: list[dict], new_recs: list[dict]) -> list[dict]:
    by_id = {r["youtube_id"]: r for r in existing}
    for r in new_recs:
        if r["youtube_id"] not in by_id:
            by_id[r["youtube_id"]] = r
            print(f"  + NEW RECORDING: {r['date']} {r['youtube_id']}")
    return sorted(by_id.values(), key=lambda r: r["date"], reverse=True)


# ---------------------------------------------------------------------------
# Argument parsing & date window
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape Kalamazoo Environmental Concerns Committee meetings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python ecc_scraper.py                              # Default: last 6 months\n"
            "  python ecc_scraper.py --start-date 2023-01-01      # Full backfill\n"
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
    api_key = get_youtube_key()

    print(f"ECC Scraper [{mode} mode]")
    print(f"Window: {start_iso} → {end_iso}")
    print()

    # ── Step 1: CivicClerk past meetings
    print("── Step 1: Fetching CivicClerk events...")
    now        = datetime.now(timezone.utc)
    future_iso = (now + timedelta(days=6 * 30)).strftime("%Y-%m-%d")
    today_iso  = date.today().isoformat()

    cc_url     = build_civicclerk_url(start_iso, future_iso)
    all_events = fetch_all_civicclerk_events(cc_url)
    ecc_events = [e for e in all_events if is_ecc_event(e)]

    past_events   = [e for e in ecc_events if e["startDateTime"].split("T")[0] <= today_iso]
    future_events = [e for e in ecc_events if e["startDateTime"].split("T")[0] > today_iso]

    print(f"  ECC events found: {len(ecc_events)} ({len(past_events)} past, {len(future_events)} upcoming)")
    print()

    scraped = [m for m in (transform_civicclerk_event(e) for e in past_events) if m is not None]
    for m in scraped:
        ag = "agenda"    if m["agenda_url"]  else "no agenda"
        mn = "minutes"   if m["minutes_url"] else "no minutes"
        ca = " CANCELLED" if m["cancelled"]   else ""
        print(f"  {m['date']}  [{ag}, {mn}]{ca}")
    print()

    # ── Step 2: YouTube recordings
    print("── Step 2: Fetching YouTube recordings...")
    recordings = fetch_youtube_recordings(api_key, start_iso, end_iso)
    print(f"  {len(recordings)} recordings found in window")
    print()

    # ── Step 3: Merge
    print("── Step 3: Merging with existing data/ecc.json...")
    existing = load_existing(OUTPUT_PATH)
    merged_meetings, stats = merge_meetings(existing.get("meetings", []), scraped)
    merged_recordings = merge_recordings(existing.get("recordings", []), recordings)
    print()
    print(f"  Meetings   — added: {stats['added']}  updated: {stats['updated']}  unchanged: {stats['unchanged']}  fields protected: {stats['preserved']}")
    print(f"  Recordings — total: {len(merged_recordings)}")
    print()

    # ── Step 4: Upcoming from CivicClerk future events
    upcoming = events_to_upcoming(future_events)
    print(f"── Step 4: {len(upcoming)} upcoming meetings from CivicClerk")
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
    print(f"  {len(merged_recordings)} total recordings")
    print(f"  {len(upcoming)} upcoming dates")


if __name__ == "__main__":
    main()
