"""
Kalamazoo Election Commission Meeting Scraper
=============================================

Pulls EC meetings from the CivicClerk API and writes everything to
data/ec.json. No YouTube step — EC meetings are not streamed.

EC meetings are irregular and election-cycle-driven. Upcoming meetings
are maintained separately (either manually or via the city website scrape
in the unified scraper) and are always preserved by this script.

Default mode (no arguments):
    Fetches the last 6 months. Merges into existing data/ec.json.
    This is what GitHub Actions runs on a weekly cron.

    python ec_scraper.py

Backfill mode:
    Fetches everything from the given date forward.

    python ec_scraper.py --start-date 2023-01-01
"""

import argparse
import json
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

import requests


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CIVICCLERK_TENANT = "kalamazoomi"
EC_CATEGORY_ID    = 37

# EC event names vary widely — filter broadly by category ID alone.
# Keywords list is intentionally broad to catch all event name patterns:
# "City Election Commission", "Public Accuracy Test", "Special Meeting",
# "Precinct Consolidation", "Appoint Election Inspectors", etc.
EC_KEYWORDS = [
    "election commission",
    "election inspector",
    "accuracy test",
    "precinct",
    "election",
]

LOOKBACK_MONTHS = 6
CUTOFF_DATE     = "2023-01-01"

OUTPUT_PATH     = Path("data") / "ec.json"

PRESERVE_IF_SCRAPE_EMPTY = ("agenda_url", "minutes_url")


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


def is_ec_event(event: dict) -> bool:
    if event.get("eventCategoryId") != EC_CATEGORY_ID:
        return False
    name_lower = event.get("eventName", "").lower()
    return any(kw in name_lower for kw in EC_KEYWORDS)


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


def normalize_meeting_type(event_name: str) -> str:
    """Use the CivicClerk event name as the meeting_type sublabel.

    Clean up minor variations but preserve the descriptive content since
    EC meeting names carry meaningful context (e.g. which election they
    relate to).
    """
    name = event_name.strip()
    # Normalize common patterns
    lower = name.lower()
    if "special" in lower and "election commission" not in lower:
        return "Special Meeting"
    return name


def transform_civicclerk_event(event: dict) -> dict | None:
    event_id        = event["id"]
    date_only       = event["startDateTime"].split("T")[0]
    published_files = event.get("publishedFiles", [])
    agenda_file_id  = find_file_id(published_files, "Agenda")
    minutes_file_id = find_file_id(published_files, "Minutes")
    name_lower      = event.get("eventName", "").lower()
    cancelled       = "cancel" in name_lower

    # Skip meetings with no documents
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
        "meeting_type": normalize_meeting_type(event.get("eventName", "")),
        "cancelled":    cancelled,
        "minutes_url":  build_document_url(event_id, minutes_file_id) if minutes_file_id else None,
        "agenda_url":   build_document_url(event_id, agenda_file_id)  if agenda_file_id  else None,
    }


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
                      for f in ("url", "cancelled", "minutes_url", "agenda_url", "meeting_type"))
        if changed:
            by_key[k] = merged
            stats["updated"] += 1
            print(f"  ~ UPDATED:  {merged['date']} {merged['meeting_type']}")
        else:
            stats["unchanged"] += 1

    merged_list = sorted(by_key.values(), key=lambda m: m["date"], reverse=True)
    return merged_list, stats


# ---------------------------------------------------------------------------
# Argument parsing & date window
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape Kalamazoo Election Commission meetings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python ec_scraper.py                              # Default: last 6 months\n"
            "  python ec_scraper.py --start-date 2023-01-01      # Full backfill\n"
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

    print(f"EC Scraper [{mode} mode]")
    print(f"Window: {start_iso} → {end_iso}")
    print()

    print("── Step 1: Fetching CivicClerk events...")
    cc_url     = build_civicclerk_url(start_iso, end_iso)
    all_events = fetch_all_civicclerk_events(cc_url)
    ec_events  = [e for e in all_events if is_ec_event(e)]
    print(f"  EC events found: {len(ec_events)}")
    print()

    scraped = [m for m in (transform_civicclerk_event(e) for e in ec_events) if m is not None]
    for m in scraped:
        ag = "agenda"    if m["agenda_url"]  else "no agenda"
        mn = "minutes"   if m["minutes_url"] else "no minutes"
        ca = " CANCELLED" if m["cancelled"]   else ""
        print(f"  {m['date']}  {m['meeting_type']}  [{ag}, {mn}]{ca}")
    print()

    print("── Step 2: Merging with existing data/ec.json...")
    existing = load_existing(OUTPUT_PATH)
    merged_meetings, stats = merge_meetings(existing.get("meetings", []), scraped)
    print()
    print(f"  Meetings — added: {stats['added']}  updated: {stats['updated']}  unchanged: {stats['unchanged']}  fields protected: {stats['preserved']}")
    print()

    # Always preserve upcoming_meetings — maintained separately via city website
    upcoming = existing.get("upcoming_meetings", [])
    print(f"── Step 3: Preserving {len(upcoming)} upcoming meetings from existing JSON")
    print()

    output = {
        "last_updated":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "upcoming_meetings": upcoming,
        "meetings":          merged_meetings,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {OUTPUT_PATH}")
    print(f"  {len(merged_meetings)} total meetings")
    print(f"  {len(upcoming)} upcoming dates (preserved)")


if __name__ == "__main__":
    main()
