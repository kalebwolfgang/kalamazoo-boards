#!/usr/bin/env python3
"""
Planning Commission scraper
===========================
CivicClerk category 29 + YouTube
Upcoming meetings pulled from CivicClerk future events (reliable).

Default mode (no arguments):
    Fetches the last 6 months + next 6 months for upcoming.
    python pc_scraper.py

Backfill mode:
    python pc_scraper.py --start-date 2020-01-01
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

import requests


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CIVICCLERK_TENANT = "kalamazoomi"
PC_CATEGORY_ID    = 29
PC_KEYWORDS       = ["planning commission"]
BOARD_NAME        = "Planning Commission"
OUTPUT_PATH       = Path("data") / "pc.json"
CUTOFF_DATE       = "2020-01-01"
LOOKBACK_MONTHS   = 6
LOOKAHEAD_MONTHS  = 6

YOUTUBE_CHANNEL_ID   = "UCIgXSSXLSDxThVaaiRMsR5Q"
YOUTUBE_SEARCH_QUERY = "Planning Commission Kalamazoo"
YOUTUBE_TITLE_FILTER = ["planning commission"]
YOUTUBE_TOLERANCE    = 3

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


def fetch_all_civicclerk_events(url: str) -> list:
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


def is_pc_event(event: dict) -> bool:
    if event.get("eventCategoryId") != PC_CATEGORY_ID:
        return False
    name_lower = event.get("eventName", "").lower()
    return any(kw in name_lower for kw in PC_KEYWORDS)


def find_file_id(published_files: list, file_type: str):
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


def normalize_meeting_type(event_name: str) -> str:
    if "special" in event_name.lower():
        return "Special Meeting"
    return BOARD_NAME


def transform_civicclerk_event(event: dict):
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
    if cancelled:
        link_label = "View notice"
    elif has_minutes:
        link_label = "Agenda & Minutes"
    else:
        link_label = "Agenda"

    agenda_url  = build_document_url(event_id, agenda_file_id)  if agenda_file_id  else None
    minutes_url = build_document_url(event_id, minutes_file_id) if minutes_file_id else None

    url = agenda_url or (
        f"https://{CIVICCLERK_TENANT}.portal.civicclerk.com/event/{event_id}/overview"
    )

    return {
        "date":         date_only,
        "display":      format_display_date(date_only),
        "event_id":     event_id,
        "url":          url,
        "link_label":   link_label,
        "meeting_type": normalize_meeting_type(event.get("eventName", "")),
        "cancelled":    cancelled,
        "minutes_url":  minutes_url,
        "agenda_url":   agenda_url,
    }


def events_to_upcoming(events: list, time_str: str) -> list:
    upcoming = []
    for event in events:
        date_only = event["startDateTime"].split("T")[0]
        upcoming.append({
            "date":    date_only,
            "display": format_display_date_long(date_only),
            "time":    time_str,
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


def fetch_youtube_recordings(api_key: str, start_date: str, end_date: str) -> list:
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

    recordings   = []
    date_pattern = re.compile(
        r'(January|February|March|April|May|June|July|August|September|October|November|December)'
        r'\s+\d{1,2},?\s+\d{4}'
    )
    for item in all_items:
        video_id  = item["id"]["videoId"]
        snippet   = item["snippet"]
        title     = snippet.get("title", "")
        published = snippet.get("publishedAt", "")
        pub_date  = published[:10] if published else None
        if not pub_date:
            continue
        if not any(kw in title.lower() for kw in YOUTUBE_TITLE_FILTER):
            print(f"    SKIPPED (wrong board): {title[:60]}")
            continue
        match = date_pattern.search(title)
        if match:
            try:
                date_only = datetime.strptime(
                    match.group().replace(",", ""), "%B %d %Y"
                ).strftime("%Y-%m-%d")
            except Exception:
                date_only = pub_date
        else:
            date_only = pub_date
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
# Merge helpers
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


def smart_merge_record(existing: dict, scraped: dict) -> tuple:
    result, preserved = dict(scraped), []
    for field in PRESERVE_IF_SCRAPE_EMPTY:
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
            print(f"  + NEW:      {s['date']} {s['meeting_type']}")
            continue

        merged, preserved = smart_merge_record(by_key[k], s)
        if preserved:
            stats["preserved"] += len(preserved)
            print(f"  = PROTECTED:{merged['date']}  kept {', '.join(preserved)}")

        changed = any(
            by_key[k].get(f) != merged.get(f)
            for f in ("url", "cancelled", "minutes_url", "agenda_url")
        )
        if changed:
            by_key[k] = merged
            stats["updated"] += 1
            print(f"  ~ UPDATED:  {merged['date']}")
        else:
            stats["unchanged"] += 1

    merged_list = sorted(by_key.values(), key=lambda m: m["date"], reverse=True)
    return merged_list, stats


def merge_recordings(existing: list, new_recs: list) -> list:
    by_id = {r["youtube_id"]: r for r in existing}
    for r in new_recs:
        if r["youtube_id"] not in by_id:
            by_id[r["youtube_id"]] = r
            print(f"  + NEW RECORDING: {r['date']} {r['youtube_id']}")
    return sorted(by_id.values(), key=lambda r: r["date"], reverse=True)


# ---------------------------------------------------------------------------
# Argument parsing & date window
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape Kalamazoo Planning Commission meetings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python pc_scraper.py                             # Default: last 6 months\n"
            "  python pc_scraper.py --start-date 2020-01-01    # Full backfill\n"
        ),
    )
    parser.add_argument("--start-date", help="Start of window (YYYY-MM-DD).")
    parser.add_argument("--end-date",   help="End of window (YYYY-MM-DD). Defaults to today.")
    return parser.parse_args()


def determine_window(args) -> tuple:
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

    print(f"PC Scraper [{mode} mode]")
    print(f"Window: {start_iso} → {end_iso}")
    print()

    # ── Step 1: CivicClerk past meetings + upcoming
    print("── Step 1: Fetching CivicClerk events...")
    now        = datetime.now(timezone.utc)
    future_iso = (now + timedelta(days=LOOKAHEAD_MONTHS * 30)).strftime("%Y-%m-%d")
    today_iso  = date.today().isoformat()

    cc_url     = build_civicclerk_url(start_iso, future_iso)
    all_events = fetch_all_civicclerk_events(cc_url)
    pc_events  = [e for e in all_events if is_pc_event(e)]

    past_events   = [e for e in pc_events if e["startDateTime"].split("T")[0] <= today_iso]
    future_events = [e for e in pc_events if e["startDateTime"].split("T")[0] > today_iso]

    print(f"  PC events found: {len(pc_events)} ({len(past_events)} past, {len(future_events)} upcoming)")
    print()

    scraped = [m for m in (transform_civicclerk_event(e) for e in past_events) if m is not None]
    for m in scraped:
        ag = "agenda"   if m["agenda_url"]  else "no agenda"
        mn = "minutes"  if m["minutes_url"] else "no minutes"
        ca = " CANCELLED" if m["cancelled"]  else ""
        print(f"  {m['date']}  [{ag}, {mn}]{ca}  {m['meeting_type']}")
    print()

    # ── Step 2: YouTube recordings
    print("── Step 2: Fetching YouTube recordings...")
    recordings = fetch_youtube_recordings(api_key, start_iso, end_iso)
    print(f"  {len(recordings)} recordings found in window")
    print()

    # ── Step 3: Merge
    print("── Step 3: Merging with existing data/pc.json...")
    existing = load_existing(OUTPUT_PATH)
    merged_meetings, stats = merge_meetings(existing.get("meetings", []), scraped)
    merged_recordings = merge_recordings(existing.get("recordings", []), recordings)
    print()
    print(f"  Meetings   — added: {stats['added']}  updated: {stats['updated']}  unchanged: {stats['unchanged']}  fields protected: {stats['preserved']}")
    print(f"  Recordings — total: {len(merged_recordings)}")
    print()

    # ── Step 4: Upcoming from CivicClerk future events
    upcoming = events_to_upcoming(future_events, "7:00 PM \u2013 9:00 PM")
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
