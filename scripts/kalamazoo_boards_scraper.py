"""
Kalamazoo Boards & Commissions - Unified Ongoing Scraper
=========================================================

Runs all configured boards, checking the last 6 months for new meetings
and the next 6 months for upcoming meeting dates.

Three scraper types:
  (default)      CivicClerk for past meetings + future events for upcoming
  youtube_only   Meetings manually maintained; YouTube scraped; upcoming from schedule rule
  web_scrape     No CivicClerk; upcoming meetings scraped from city website

Flags:
  upcoming_from_web  CivicClerk for past meetings; city website for upcoming
  preserve_upcoming  CivicClerk for past meetings; preserve existing upcoming from JSON

Usage:
    python scraper.py               # All boards
    python scraper.py --board crb   # One board only

YouTube API key required for boards with youtube: true.
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
    },
    {
        "key":         "bra",
        "name":        "Brownfield Redevelopment Authority",
        "category_id": 34,
        "keywords":    ["brownfield redevelopment authority"],
        "output":      Path("data") / "bra.json",
        "youtube":     False,
    },
    {
        "key":         "cpsrab",
        "name":        "Citizens Public Safety Review and Appeal Board",
        "scraper_type": "youtube_only",
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
    },
    {
        "key":         "dega",
        "name":        "Downtown Economic Growth Authority",
        "category_id": 39,
        "keywords":    ["downtown economic growth authority", "dega"],
        "output":      Path("data") / "dega.json",
        "youtube":     False,
    },
    {
        "key":         "edc",
        "name":        "Economic Development Corporation",
        "category_id": 33,
        "keywords":    ["economic development corporation", "edc"],
        "output":      Path("data") / "edc.json",
        "youtube":     False,
    },
    {
        "key":         "ec",
        "name":        "Election Commission",
        "category_id": 37,
        "keywords":    ["election commission", "election inspector", "accuracy test", "precinct", "election"],
        "output":      Path("data") / "ec.json",
        "youtube":     False,
        "upcoming_from_web": True,
        "web_url":     "https://www.kalamazoocity.org/Government/Boards-Commissions/Election-Commission",
    },
    {
        "key":         "ecc",
        "name":        "Environmental Concerns Committee",
        "category_id": 46,
        "keywords":    ["environmental concerns committee", "environmental concerns"],
        "output":      Path("data") / "ecc.json",
        "youtube":     True,
        "youtube_channel_id":   "UCIgXSSXLSDxThVaaiRMsR5Q",
        "youtube_search_query": "Environmental Concerns Committee",
        "youtube_title_filter": ["environmental concerns committee", "environmental concerns"],
        "youtube_tolerance":    3,
    },
    {
        "key":         "hdc",
        "name":        "Historic District Commission",
        "category_id": 35,
        "keywords":    ["historic district commission", "historic district", "hdc"],
        "output":      Path("data") / "hdc.json",
        "youtube":     True,
        "youtube_channel_id":   "UCIgXSSXLSDxThVaaiRMsR5Q",
        "youtube_search_query": "Historic District Commission",
        "youtube_title_filter": ["historic district"],
        "youtube_tolerance":    3,
        "schedule":    ("monthly", "tuesday", 3, None),
    },
   {
        "key":         "hpc",
        "name":        "Historic Preservation Commission",
        "category_id": 36,
        "keywords":    ["historic preservation commission"],
        "output":      Path("data") / "hpc.json",
        "youtube":     True,
        "youtube_channel_id":   "UCIgXSSXLSDxThVaaiRMsR5Q",
        "youtube_search_query": "Historic Preservation Commission",
        "youtube_title_filter": ["historic preservation commission", "historical preservation commission"],
        "youtube_tolerance":    3,
        "schedule":    ("monthly", "wednesday", 2, None),
    },
    {
        "key":         "pension-board",
        "name":        "Employee Retirement System Board of Trustees",
        "category_id": 42,
        "keywords":    ["employees retirement system", "retirement system", "pension"],
        "output":      Path("data") / "pension-board.json",
        "youtube":     False,
        "preserve_upcoming": True,
    },
  {
        "key":         "ric",
        "name":        "Retirement Investment Committee / Perpetual Care Investment Committee",
        "scraper_type": "web_scrape",
        "web_url":     "https://www.kalamazoocity.org/Government/Boards-Commissions/Retirement-Investment-Committee-Perpetual-Care-Investment-Committee",
        "category_id": None,
        "keywords":    [],
        "output":      Path("data") / "ric.json",
        "youtube":     False,
    },
  {
        "key":         "kmga",
        "name":        "Kalamazoo Municipal Golf Association",
        "scraper_type": "web_scrape",
        "web_url":     "https://www.kalamazoocity.org/Government/Boards-Commissions/Kalamazoo-Municipal-Golf-Association",
        "category_id": None,
        "keywords":    [],
        "output":      Path("data") / "kmga.json",
        "youtube":     False,
    },
    {
        "key":         "tre",
        "name":        "Tree Committee",
        "scraper_type": "web_scrape",
        "web_url":     "https://www.kalamazoocity.org/Government/Boards-Commissions/Tree-Committee",
        "category_id": None,
        "keywords":    [],
        "output":      Path("data") / "tre.json",
        "youtube":     False,
    },
    {
        "key":         "bba",
        "name":        "Building Board of Appeals",
        "scraper_type": "web_scrape",
        "web_url":     "https://www.kalamazoocity.org/Government/Boards-Commissions/Building-Board-of-Appeals",
        "category_id": None,
        "keywords":    [],
        "output":      Path("data") / "bba.json",
        "youtube":     False,
    },
    {
        "key":         "cdaac",
        "name":        "Community Development Act Advisory Committee",
        "scraper_type": "web_scrape",
        "web_url":     "https://www.kalamazoocity.org/Government/Boards-Commissions/Community-Development-Act-Advisory-Committee-CDAAC",
        "category_id": None,
        "keywords":    [],
        "output":      Path("data") / "cdaac.json",
        "youtube":     False,
    },
]


# ---------------------------------------------------------------------------
# Global config
# ---------------------------------------------------------------------------

CIVICCLERK_TENANT    = "kalamazoomi"
LOOKBACK_MONTHS      = 6
LOOKAHEAD_MONTHS     = 6
PRESERVE_IF_EMPTY    = ("agenda_url", "minutes_url", "youtube_id", "youtube_url")


# ---------------------------------------------------------------------------
# Meeting times per board
# ---------------------------------------------------------------------------

BOARD_TIMES = {
    "crb":          "5:00 PM",
    "bra":          "7:45 AM \u2013 9:30 AM",
    "cpsrab":       "6:00 PM \u2013 8:00 PM",
    "dda":          "3:00 PM \u2013 5:00 PM",
    "dega":         "3:00 PM \u2013 5:00 PM",
    "edc":          "7:45 AM",
    "ec":           "9:00 AM",
    "ecc":          "4:30 PM \u2013 6:30 PM",
    "hdc":          "5:00 PM \u2013 7:00 PM",
    "hpc":          "6:00 PM \u2013 8:00 PM",
    "pension-board":"8:00 AM \u2013 9:00 AM",
    "ric":          "11:00 AM \u2013 12:00 PM",
    "kmga":         "12:00 PM \u2013 2:00 PM",
    "tre":          "2:00 PM \u2013 4:00 PM",
    "bba":          "4:00 PM \u2013 6:00 PM",
    "cdaac":        "5:30 PM \u2013 7:30 PM",
}
for b in BOARDS:
    b["time"] = BOARD_TIMES.get(b["key"], "TBD")


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


def filter_board_events(all_events: list[dict], board: dict) -> list[dict]:
    return [
        e for e in all_events
        if e.get("eventCategoryId") == board["category_id"]
        and any(kw in e.get("eventName", "").lower() for kw in board["keywords"])
    ]


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
    return d.strftime("%B %#d, %Y")


def format_display_date_long(iso: str) -> str:
    d = datetime.strptime(iso, "%Y-%m-%d")
    return d.strftime("%A, %B %#d, %Y")


def normalize_meeting_type(event_name: str) -> str:
    if "special" in event_name.lower():
        return "Special Meeting"
    return event_name


def transform_event(event: dict, board: dict):
    event_id        = event["id"]
    date_only       = event["startDateTime"].split("T")[0]
    published_files = event.get("publishedFiles", [])
    agenda_file_id  = find_file_id(published_files, "Agenda")
    minutes_file_id = find_file_id(published_files, "Minutes")
    name_lower      = event.get("eventName", "").lower()
    cancelled       = "cancel" in name_lower

    if not agenda_file_id and not minutes_file_id:
        return None

    record = {
        "date":        date_only,
        "display":     format_display_date(date_only),
        "event_id":    event_id,
        "url":         build_doc_url(event_id, agenda_file_id) if agenda_file_id
                       else f"https://{CIVICCLERK_TENANT}.portal.civicclerk.com/event/{event_id}/overview",
        "link_label":  "Agenda & Minutes" if minutes_file_id else "Agenda",
        "cancelled":   cancelled,
        "minutes_url": build_doc_url(event_id, minutes_file_id) if minutes_file_id else None,
        "agenda_url":  build_doc_url(event_id, agenda_file_id)  if agenda_file_id  else None,
    }

    if board.get("key") != "crb":
        record["meeting_type"] = normalize_meeting_type(event.get("eventName", ""))

    return record


def events_to_upcoming(events: list[dict], board: dict) -> list[dict]:
    upcoming = []
    for event in events:
        date_only = event["startDateTime"].split("T")[0]
        upcoming.append({
            "date":    date_only,
            "display": format_display_date_long(date_only),
            "time":    board.get("time", "TBD"),
        })
    upcoming.sort(key=lambda m: m["date"])
    return upcoming


# ---------------------------------------------------------------------------
# Schedule-based upcoming (used for boards without CivicClerk future events)
# ---------------------------------------------------------------------------

def nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """Return the nth occurrence (1-based) of weekday (0=Mon...6=Sun) in month."""
    d = date(year, month, 1)
    days_ahead = weekday - d.weekday()
    if days_ahead < 0:
        days_ahead += 7
    first = d + timedelta(days=days_ahead)
    return first + timedelta(weeks=n - 1)


def compute_upcoming_schedule(board: dict, n: int = 6) -> list[dict]:
    schedule = board.get("schedule")
    if not schedule:
        return []

    _, weekday_name, nth, months = schedule
    weekday_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    weekday = weekday_map[weekday_name.lower()]
    today   = date.today()
    results = []
    year, month = today.year, today.month
    checked = 0

    while len(results) < n and checked < 36:
        if months is None or month in months:
            d = nth_weekday_of_month(year, month, weekday, nth)
            if d >= today:
                results.append({
                    "date":    d.strftime("%Y-%m-%d"),
                    "display": format_display_date_long(d.strftime("%Y-%m-%d")),
                    "time":    board.get("time", "TBD"),
                })
        month += 1
        if month > 12:
            month = 1
            year += 1
        checked += 1

    return results


# ---------------------------------------------------------------------------
# Web scrape upcoming (for boards on kalamazoocity.org without CivicClerk)
# ---------------------------------------------------------------------------

def scrape_web_upcoming(board: dict) -> list[dict]:
    """Scrape upcoming meeting dates from a city website board page."""
    url = board["web_url"]
    print(f"    [Web] Fetching {url}...")
    r = requests.get(url, timeout=30)
    r.raise_for_status()

    today    = date.today()
    upcoming = []
    seen     = set()

    # Match date patterns like "Thursday, May 21, 2026 | 04:00 PM"
    pattern = r'(\w+day,\s+\w+\s+\d{1,2},\s+\d{4})\s*\|'
    matches = re.findall(pattern, r.text)

    for match in matches:
        match_clean = re.sub(r'\s+', ' ', match.strip())
        if match_clean in seen:
            continue
        seen.add(match_clean)
        try:
            d = datetime.strptime(match_clean, "%A, %B %d, %Y").date()
            if d >= today:
                upcoming.append({
                    "date":    d.strftime("%Y-%m-%d"),
                    "display": format_display_date_long(d.strftime("%Y-%m-%d")),
                    "time":    board.get("time", "TBD"),
                })
        except ValueError:
            continue

    upcoming.sort(key=lambda m: m["date"])
    print(f"    Found {len(upcoming)} upcoming meetings")
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
    title_filter = board.get("youtube_title_filter", [])
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
        if title_filter and not any(kw in title.lower() for kw in title_filter):
            print(f"    SKIPPED (wrong board): {title[:60]}")
            continue
        match = date_pattern.search(title)
        if match:
            try:
                date_only = datetime.strptime(match.group().replace(",", ""), "%B %d %Y").strftime("%Y-%m-%d")
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
    name = board["name"]
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    print("  (meetings manually maintained - scraping YouTube only)")

    print("  Step 1: Fetching YouTube recordings...")
    recordings = fetch_youtube_streams(api_key, board, start_iso, end_iso)
    print(f"    Found {len(recordings)} recordings in window")

    print("  Step 2: Merging...")
    existing          = load_existing(board["output"])
    merged_recordings = merge_recordings(existing.get("recordings", []), recordings)

    # Upcoming: use schedule rule if configured, otherwise preserve from JSON
    if board.get("schedule"):
        upcoming = compute_upcoming_schedule(board)
        print(f"  Upcoming: computed {len(upcoming)} dates from schedule rule")
    else:
        upcoming = existing.get("upcoming_meetings", [])
        print(f"  Upcoming: preserved {len(upcoming)} dates from existing JSON")

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


def run_web_scrape_board(board: dict) -> None:
    name = board["name"]
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")

    print("  Step 1: Scraping upcoming meetings from city website...")
    upcoming = scrape_web_upcoming(board)

    print("  Step 2: Writing...")
    existing = load_existing(board["output"])

    output = {
        "last_updated":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "upcoming_meetings": upcoming,
    }

    # Preserve meetings array if it exists (future-proof)
    if existing.get("meetings"):
        output["meetings"] = existing["meetings"]

    board["output"].parent.mkdir(parents=True, exist_ok=True)
    with board["output"].open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"  Wrote {board['output']}  ({len(upcoming)} upcoming)")


def run_board(board: dict, start_iso: str, end_iso: str, api_key: str | None) -> None:
    if board.get("scraper_type") == "youtube_only":
        run_youtube_only_board(board, start_iso, end_iso, api_key)
        return

    if board.get("scraper_type") == "web_scrape":
        run_web_scrape_board(board)
        return

    name = board["name"]
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")

    now        = datetime.now(timezone.utc)
    future_iso = (now + timedelta(days=LOOKAHEAD_MONTHS * 30)).strftime("%Y-%m-%d")
    today_iso  = date.today().isoformat()

    print("  Step 1: Fetching CivicClerk events (past + upcoming)...")
    cc_url       = build_cc_url(start_iso, future_iso)
    all_events   = fetch_all_cc_events(cc_url)
    board_events = filter_board_events(all_events, board)

    past_events   = [e for e in board_events if e["startDateTime"].split("T")[0] <= today_iso]
    future_events = [e for e in board_events if e["startDateTime"].split("T")[0] > today_iso]

    print(f"    Found {len(past_events)} past events, {len(future_events)} upcoming events")

    scraped = [m for m in (transform_event(e, board) for e in past_events) if m is not None]
    print(f"    {len(scraped)} past events with documents")

    # Upcoming: use city website if flagged, otherwise use CivicClerk future events
    if board.get("upcoming_from_web"):
        print("  Step 2: Scraping upcoming meetings from city website...")
        upcoming = scrape_web_upcoming(board)
    elif board.get("schedule"):
        print("  Step 2: Computing upcoming meetings from schedule rule...")
        upcoming = compute_upcoming_schedule(board)
    elif board.get("preserve_upcoming"):
        existing_check = load_existing(board["output"])
        upcoming = existing_check.get("upcoming_meetings", [])
        print(f"    Preserving {len(upcoming)} upcoming meetings from existing JSON")
    else:
        upcoming = events_to_upcoming(future_events, board)
        print(f"    {len(upcoming)} upcoming meetings on CivicClerk")

    all_recs = []
    if board.get("youtube") and api_key:
        print("  Step 2: Fetching YouTube streams...")
        all_recs = fetch_youtube_streams(api_key, board, start_iso, end_iso)
        
        # Embed videos into meetings
        tolerance = board.get("youtube_tolerance", 3)
        for rec in all_recs:
            rec_date = datetime.strptime(rec["date"], "%Y-%m-%d").date()
            best_meeting = None
            best_delta = timedelta(days=tolerance + 1)
            
            for m in scraped:
                m_date = datetime.strptime(m["date"], "%Y-%m-%d").date()
                delta = abs(rec_date - m_date)
                if delta <= timedelta(days=tolerance) and delta < best_delta:
                    best_delta = delta
                    best_meeting = m
                    
            if best_meeting and not best_meeting.get("youtube_id"):
                best_meeting["youtube_id"] = rec["youtube_id"]
                best_meeting["youtube_url"] = rec["youtube_url"]

    print("  Step 3: Merging...")
    existing        = load_existing(board["output"])
    merged_meetings, stats = merge_meetings(existing.get("meetings", []), scraped)
    print(f"    added: {stats['added']}  updated: {stats['updated']}  unchanged: {stats['unchanged']}")

    merged_recordings = []
    if board.get("youtube"):
        merged_recordings = merge_recordings(existing.get("recordings", []), all_recs)

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

    print(f"  Wrote {board['output']}  ({len(merged_meetings)} meetings, {len(upcoming)} upcoming)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Scrape all Kalamazoo boards.")
    parser.add_argument("--board", help="Run only this board key (e.g. crb, bba).")
    return parser.parse_args()


def main():
    args      = parse_args()
    now       = datetime.now(timezone.utc)
    start_dt  = now - timedelta(days=LOOKBACK_MONTHS * 30)
    start_iso = start_dt.strftime("%Y-%m-%d")
    end_iso   = now.strftime("%Y-%m-%d")

    print(f"Unified Scraper - lookback: {start_iso} -> {end_iso}  |  lookahead: +{LOOKAHEAD_MONTHS} months")

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
