#!/usr/bin/env python3
"""
NFP Review Board scraper — Natural Features Protection Review Board
CivicClerk category 41 + YouTube
Schedule: 4th Tuesday monthly
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

# ── Constants ────────────────────────────────────────────────────────────────
CIVICCLERK_TENANT = "kalamazoomi"
CATEGORY_ID       = 41
KEYWORDS          = ["natural features protection", "nfp"]
BOARD_NAME        = "Natural Features Protection Review Board"
OUTPUT_PATH       = Path("data") / "nfp.json"
CUTOFF_DATE       = "2020-01-01"
LOOKBACK_MONTHS   = 6

YOUTUBE_CHANNEL_ID   = "UCIgXSSXLSDxThVaaiRMsR5Q"
YOUTUBE_SEARCH_QUERY = "Natural Features Protection Review Board"
YOUTUBE_TITLE_FILTER = ["natural features protection"]
YOUTUBE_TOLERANCE    = 3

PRESERVE_IF_SCRAPE_EMPTY = ["youtube_id", "agenda_url", "minutes_url"]


# ── CLI / Window ─────────────────────────────────────────────────────────────
def determine_window(args):
    now = datetime.now(timezone.utc)
    if args.start_date:
        mode      = "BACKFILL"
        start_iso = args.start_date
        end_iso   = args.end_date or now.strftime("%Y-%m-%d")
    else:
        mode      = "DEFAULT"
        start_dt  = now - timedelta(days=LOOKBACK_MONTHS * 30)
        cutoff_dt = datetime.strptime(CUTOFF_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start_dt  = max(start_dt, cutoff_dt)
        start_iso = start_dt.strftime("%Y-%m-%d")
        end_iso   = now.strftime("%Y-%m-%d")
    return start_iso, end_iso, mode


# ── CivicClerk ───────────────────────────────────────────────────────────────
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


def is_nfp_event(event: dict) -> bool:
    if event.get("eventCategoryId") != CATEGORY_ID:
        return False
    name = (event.get("eventName") or "").lower()
    return any(kw in name for kw in KEYWORDS)


def format_display_date(iso: str) -> str:
    d = datetime.strptime(iso, "%Y-%m-%d").date()
    return d.strftime("%B %#d, %Y")


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


def transform_civicclerk_event(event: dict):
    raw_dt          = event.get("startDateTime", "")[:10]
    event_id        = event["id"]
    published_files = event.get("publishedFiles", [])
    agenda_file_id  = find_file_id(published_files, "Agenda")
    minutes_file_id = find_file_id(published_files, "Minutes")
    name_lower      = event.get("eventName", "").lower()
    cancelled       = "cancel" in name_lower

    if not agenda_file_id and not minutes_file_id:
        return None

    has_minutes = bool(minutes_file_id)
    link_label  = "Agenda & Minutes" if has_minutes else "Agenda"
    if cancelled:
        link_label = "View notice"

    agenda_url  = build_document_url(event_id, agenda_file_id)  if agenda_file_id  else None
    minutes_url = build_document_url(event_id, minutes_file_id) if minutes_file_id else None

    url = agenda_url or (
        f"https://{CIVICCLERK_TENANT}.portal.civicclerk.com/event/{event_id}/overview"
    )

    return {
        "date":         raw_dt,
        "display":      format_display_date(raw_dt),
        "event_id":     event_id,
        "url":          url,
        "link_label":   link_label,
        "meeting_type": BOARD_NAME,
        "cancelled":    cancelled,
        "minutes_url":  minutes_url,
        "agenda_url":   agenda_url,
    }


# ── YouTube ──────────────────────────────────────────────────────────────────
def get_youtube_api_key() -> str:
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        sys.exit("ERROR: YOUTUBE_API_KEY environment variable is not set.")
    return key


def fetch_youtube_videos(api_key: str, start_date: str, end_date: str) -> list:
    url    = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "key":           api_key,
        "channelId":     YOUTUBE_CHANNEL_ID,
        "part":          "snippet",
        "type":          "video",
        "q":             YOUTUBE_SEARCH_QUERY,
        "publishedAfter":  f"{start_date}T00:00:00Z",
        "publishedBefore": f"{end_date}T23:59:59Z",
        "maxResults":    50,
        "order":         "date",
    }
    items = []
    while True:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get("items", []))
        token = data.get("nextPageToken")
        if not token:
            break
        params["pageToken"] = token
    return items


def youtube_items_to_recordings(items: list) -> list:
    recordings   = []
    date_pattern = re.compile(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2},?\s+\d{4}"
    )
    for item in items:
        vid_id = item["id"]["videoId"]
        title  = item["snippet"]["title"]
        pub    = item["snippet"]["publishedAt"][:10]

        if not any(kw in title.lower() for kw in YOUTUBE_TITLE_FILTER):
            continue

        match = date_pattern.search(title)
        if match:
            try:
                rec_date = datetime.strptime(
                    match.group().replace(",", ""), "%B %d %Y"
                ).date()
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
    meetings: list, recordings: list, tolerance: int = 3
):
    matched   = []
    for rec in recordings:
        rec_date  = datetime.strptime(rec["date"], "%Y-%m-%d").date()
        best      = None
        best_delta = timedelta(days=tolerance + 1)
        for mtg in meetings:
            mtg_date = datetime.strptime(mtg["date"], "%Y-%m-%d").date()
            delta    = abs(rec_date - mtg_date)
            if delta <= timedelta(days=tolerance) and delta < best_delta:
                best_delta = delta
                best       = mtg
        if best:
            matched.append((best, rec))

    # Master library rule — return ALL recordings regardless of match
    return matched, recordings


# ── Upcoming meetings ─────────────────────────────────────────────────────────
def nth_weekday_of_month(year: int, month: int, n: int, weekday: int) -> date:
    """Return the nth occurrence of weekday (Mon=0, Tue=1, …) in the given month."""
    first      = date(year, month, 1)
    days_ahead = (weekday - first.weekday()) % 7
    return first + timedelta(days=days_ahead) + timedelta(weeks=n - 1)


def compute_upcoming_meetings(n: int = 6) -> list:
    today  = date.today()
    result = []
    year, month = today.year, today.month

    while len(result) < n:
        meeting_date = nth_weekday_of_month(year, month, 4, 1)  # 4th Tuesday
        if meeting_date >= today:
            result.append({
                "date":    meeting_date.strftime("%Y-%m-%d"),
                "display": meeting_date.strftime("%A, ") + format_display_date(
                    meeting_date.strftime("%Y-%m-%d")
                ),
                "time": "4:00 PM \u2013 6:00 PM",
            })
        month += 1
        if month > 12:
            month = 1
            year += 1

    return result


# ── Merge helpers ─────────────────────────────────────────────────────────────
def load_existing(path: Path) -> dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"meetings": [], "recordings": []}


def smart_merge_record(existing: dict, scraped: dict) -> dict:
    merged = {**scraped}
    for field in PRESERVE_IF_SCRAPE_EMPTY:
        if not merged.get(field) and existing.get(field):
            merged[field] = existing[field]
    # Prefer minutes_url in the display url when available
    if merged.get("minutes_url"):
        merged["url"]        = merged["minutes_url"]
        merged["link_label"] = "Agenda & Minutes"
    elif merged.get("agenda_url"):
        merged["url"] = merged["agenda_url"]
    return merged


def merge_meetings(existing_list: list, scraped_list: list) -> list:
    by_id = {m["event_id"]: m for m in existing_list}
    for s in scraped_list:
        eid = s["event_id"]
        if eid in by_id:
            by_id[eid] = smart_merge_record(by_id[eid], s)
        else:
            by_id[eid] = s
    return sorted(by_id.values(), key=lambda m: m["date"], reverse=True)


def merge_recordings(existing_list: list, new_list: list) -> list:
    by_id = {r["youtube_id"]: r for r in existing_list}
    for rec in new_list:
        vid = rec["youtube_id"]
        if vid not in by_id:
            by_id[vid] = rec
        else:
            if rec.get("title") and not by_id[vid].get("title"):
                by_id[vid]["title"] = rec["title"]
    return sorted(by_id.values(), key=lambda r: r["date"], reverse=True)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="NFP Review Board scraper")
    parser.add_argument("--start-date", help="Start date YYYY-MM-DD (backfill mode)")
    parser.add_argument("--end-date",   help="End date YYYY-MM-DD (backfill mode)")
    args = parser.parse_args()

    start_iso, end_iso, mode = determine_window(args)
    print(f"[NFP] Mode: {mode}  Window: {start_iso} → {end_iso}")

    # ── CivicClerk ──
    cc_url     = build_civicclerk_url(start_iso, end_iso)
    print(f"[NFP] Fetching CivicClerk events...")
    all_events = fetch_all_civicclerk_events(cc_url)
    nfp_events = [e for e in all_events if is_nfp_event(e)]
    print(f"[NFP] Found {len(nfp_events)} NFP events (of {len(all_events)} total)")

    scraped_meetings = [r for r in (transform_civicclerk_event(e) for e in nfp_events) if r]
    print(f"[NFP] Transformed {len(scraped_meetings)} meetings")

    # ── YouTube ──
    api_key    = get_youtube_api_key()
    print(f"[NFP] Fetching YouTube videos...")
    yt_items   = fetch_youtube_videos(api_key, start_iso, end_iso)
    recordings = youtube_items_to_recordings(yt_items)
    print(f"[NFP] Found {len(recordings)} NFP recordings")

    _, all_recordings = match_recordings_to_meetings(
        scraped_meetings, recordings, YOUTUBE_TOLERANCE
    )

    # ── Merge with existing ──
    existing    = load_existing(OUTPUT_PATH)
    merged_mtg  = merge_meetings(existing.get("meetings", []), scraped_meetings)
    merged_rec  = merge_recordings(existing.get("recordings", []), all_recordings)

    existing_ids = {m["event_id"] for m in existing.get("meetings", [])}
    scraped_ids  = {m["event_id"] for m in scraped_meetings}
    added        = len(scraped_ids - existing_ids)
    updated      = len(scraped_ids & existing_ids)
    unchanged    = len(existing_ids - scraped_ids)
    print(f"[NFP] Meetings — added: {added}  updated: {updated}  unchanged: {unchanged}")

    # ── Upcoming (algorithmic — schedule flag) ──
    upcoming = compute_upcoming_meetings(n=6)
    print(f"[NFP] Computed {len(upcoming)} upcoming meetings")

    # ── Write output ──
    output = {
        "last_updated":      date.today().strftime("%Y-%m-%d"),
        "upcoming_meetings": upcoming,
        "meetings":          merged_mtg,
        "recordings":        merged_rec,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(
        f"[NFP] Written → {OUTPUT_PATH}"
        f"  ({len(merged_mtg)} meetings, {len(merged_rec)} recordings)"
    )


if __name__ == "__main__":
    main()