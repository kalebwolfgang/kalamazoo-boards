"""
Kalamazoo Citizens Public Safety Review and Appeal Board Scraper
================================================================

Pulls CPSRAB meeting documents from the City of Kalamazoo website and
recordings from the Kalamazoo City TV YouTube channel.

The city website (kalamazoocity.org/Government/Boards-Commissions/Minutes-Agendas)
lists CPSRAB agendas and minutes as direct PDF links. This scraper parses
that page and groups links by date into agenda_url / minutes_url pairs.

YouTube recordings are searched and matched to meetings by date.

Requires: pip install requests

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
import re
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MINUTES_AGENDAS_URL  = "https://www.kalamazoocity.org/Government/Boards-Commissions/Minutes-Agendas"
CPSRAB_SECTION_NAME  = "Citizens Public Safety Review and Appeal Board"

YOUTUBE_CHANNEL_ID   = "UCIgXSSXLSDxThVaaiRMsR5Q"
YOUTUBE_SEARCH_QUERY = "Citizens Public Safety Review and Appeal Board"
YOUTUBE_TITLE_FILTER = ["citizens public safety", "cpsrab"]
YOUTUBE_TOLERANCE    = 3   # days either side

LOOKBACK_MONTHS  = 6
CUTOFF_DATE      = "2021-01-01"
OUTPUT_PATH      = Path("data") / "cpsrab.json"

PRESERVE_IF_EMPTY = ("agenda_url", "minutes_url", "youtube_id", "youtube_url")


def format_display_date(d) -> str:
    return d.strftime("%B %#d, %Y")


# ---------------------------------------------------------------------------
# Document scraping from Minutes-Agendas page
# ---------------------------------------------------------------------------

def scrape_cpsrab_documents(start_iso: str, end_iso: str) -> list[dict]:
    """
    Fetches the city's Minutes-Agendas page, isolates the CPSRAB section,
    and returns a list of meeting dicts with agenda_url and/or minutes_url.

    Only returns meetings whose date falls within [start_iso, end_iso].
    Pass start_iso='2000-01-01' and end_iso='2099-12-31' for a full backfill.
    """
    print(f"  [Web] Fetching {MINUTES_AGENDAS_URL}...")
    r = requests.get(MINUTES_AGENDAS_URL, timeout=30)
    r.raise_for_status()
    html = r.text

    # ── Isolate the CPSRAB section ────────────────────────────────────────
    # The page uses heading tags to separate each board. Find the CPSRAB
    # heading, then grab everything up to the next heading.
    section_pattern = re.compile(
        r'(?:<h2[^>]*>.*?Citizens Public Safety Review and Appeal Board.*?</h2>)(.*?)(?=<h2|\Z)',
        re.IGNORECASE | re.DOTALL
    )
    section_match = section_pattern.search(html)
    if not section_match:
        print("  WARNING: Could not find CPSRAB section on Minutes-Agendas page.")
        return []

    section_html = section_match.group(1)

    # ── Extract dated PDF links ───────────────────────────────────────────
    # Link text pattern: "Month Day, Year Minutes" or "Month Day, Year Agenda"
    # e.g. "March 10, 2026 Minutes" or "December 10, 2024 Agenda"
    link_pattern = re.compile(
        r'<a\s[^>]*href="([^"]+\.pdf)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL
    )
    date_pattern = re.compile(
        r'(January|February|March|April|May|June|July|August'
        r'|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})',
        re.IGNORECASE
    )

    start_dt = datetime.strptime(start_iso, "%Y-%m-%d").date()
    end_dt   = datetime.strptime(end_iso,   "%Y-%m-%d").date()

    # Collect all dated links keyed by iso date
    by_date = {}   # iso_date -> {"agenda_url": ..., "minutes_url": ...}

    for link_match in link_pattern.finditer(section_html):
        href      = link_match.group(1).strip()
        link_text = re.sub(r'<[^>]+>', '', link_match.group(2)).strip()

        # Make sure it's a full URL
        if href.startswith('/'):
            href = 'https://www.kalamazoocity.org' + href

        date_match = date_pattern.search(link_text)
        if not date_match:
            continue

        month_str = date_match.group(1)
        day_str   = date_match.group(2)
        year_str  = date_match.group(3)

        try:
            doc_date = datetime.strptime(
                f"{month_str} {day_str} {year_str}", "%B %d %Y"
            ).date()
        except ValueError:
            continue

        if doc_date < start_dt or doc_date > end_dt:
            continue

        iso = doc_date.strftime("%Y-%m-%d")
        if iso not in by_date:
            by_date[iso] = {"agenda_url": None, "minutes_url": None}

        text_lower = link_text.lower()
        if "agenda" in text_lower:
            by_date[iso]["agenda_url"] = href
        elif "minutes" in text_lower:
            by_date[iso]["minutes_url"] = href
        # Skip non-meeting links (annual reports, orientation, activity tracker, etc.)

    # ── Build meeting records ─────────────────────────────────────────────
    meetings = []
    for iso, docs in sorted(by_date.items()):
        # Only include entries that have at least one document
        if not docs["agenda_url"] and not docs["minutes_url"]:
            continue

        doc_date = datetime.strptime(iso, "%Y-%m-%d").date()

        # Determine link_label
        if docs["agenda_url"] and docs["minutes_url"]:
            link_label = "Agenda & Minutes"
        elif docs["agenda_url"]:
            link_label = "Agenda"
        else:
            link_label = "Minutes"

        meetings.append({
            "date":        iso,
            "display":     format_display_date(doc_date),
            "agenda_url":  docs["agenda_url"],
            "minutes_url": docs["minutes_url"],
            "link_label":  link_label,
            "cancelled":   False,
        })
        print(f"    {iso}  {link_label}")

    print(f"  {len(meetings)} meetings found with documents")
    return meetings


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
                rec_date = datetime.strptime(match.group().replace(",", ""), "%B %d %Y").date()
            except Exception:
                rec_date = datetime.strptime(pub_date, "%Y-%m-%d").date()
        else:
            rec_date = datetime.strptime(pub_date, "%Y-%m-%d").date()

        recordings.append({
            "date":        rec_date.strftime("%Y-%m-%d"),
            "display":     format_display_date(rec_date),
            "youtube_id":  video_id,
            "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
            "title":       title,
        })
        print(f"    {rec_date}  {video_id}  {title[:55]}")

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
        d = date(year, month, 1)
        days_ahead = 1 - d.weekday()   # 1 = Tuesday
        if days_ahead < 0:
            days_ahead += 7
        first_tue  = d + timedelta(days=days_ahead)
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


def smart_merge_meeting(existing: dict, scraped: dict) -> dict:
    """Merge scraped into existing, preserving fields that scraper returned None for."""
    result = dict(scraped)
    for field in PRESERVE_IF_EMPTY:
        if not result.get(field) and existing.get(field):
            result[field] = existing[field]
    return result


def merge_meetings(existing: list, scraped: list) -> tuple:
    stats = {"added": 0, "updated": 0, "unchanged": 0}
    by_date = {m["date"]: m for m in existing}

    for s in scraped:
        d = s["date"]
        if d not in by_date:
            by_date[d] = s
            stats["added"] += 1
            print(f"    + NEW:     {d}  {s['link_label']}")
            continue

        merged = smart_merge_meeting(by_date[d], s)
        changed = any(
            by_date[d].get(f) != merged.get(f)
            for f in ("agenda_url", "minutes_url", "link_label")
        )
        if changed:
            by_date[d] = merged
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1

    return sorted(by_date.values(), key=lambda m: m["date"], reverse=True), stats


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
    print(f"Window: {start_iso} -> {end_iso}")
    print()

    # Step 1: Scrape meeting documents from Minutes-Agendas page
    print("── Step 1: Scraping meeting documents from city website...")
    scraped_meetings = scrape_cpsrab_documents(start_iso, end_iso)
    print()

    # Step 2: YouTube recordings
    print("── Step 2: Fetching YouTube recordings...")
    recordings = fetch_youtube_recordings(api_key, start_iso, end_iso)
    print(f"  {len(recordings)} recordings found")
    print()

    # Step 3: Merge
    print("── Step 3: Merging with existing data/cpsrab.json...")
    existing = load_existing(OUTPUT_PATH)
    merged_meetings, stats = merge_meetings(existing.get("meetings", []), scraped_meetings)
    print(f"  Meetings  — added: {stats['added']}  updated: {stats['updated']}  unchanged: {stats['unchanged']}")
    merged_recordings = merge_recordings(existing.get("recordings", []), recordings)
    print(f"  Recordings — total: {len(merged_recordings)}")
    print()

    # Step 4: Upcoming
    print("── Step 4: Computing upcoming meetings...")
    upcoming = compute_upcoming(n=6)
    for u in upcoming:
        print(f"  {u['date']}  {u['display']}")
    print()

    # Step 5: Write
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
    print(f"  {len(merged_meetings)} meetings  |  {len(merged_recordings)} recordings")


if __name__ == "__main__":
    main()