"""
Kalamazoo Boards & Commissions — Unified Ongoing Scraper
=========================================================

Runs all configured boards, checking the last 6 months for new meetings
and the next 6 months for upcoming meeting dates.

Four scraper types:
  (default)              CivicClerk for past meetings + future events for upcoming
  youtube_only           Meetings manually maintained; YouTube scraped; upcoming from schedule rule
  web_scrape             No CivicClerk; upcoming meetings scraped from city website
  web_docs_and_youtube   Documents scraped from Minutes-Agendas page; YouTube scraped; upcoming from schedule rule

Flags:
  upcoming_from_web        CivicClerk for past meetings; city website for upcoming
  upcoming_web_override_cc CivicClerk for past; try web first, fall back to CivicClerk for upcoming
  preserve_upcoming        CivicClerk for past meetings; preserve existing upcoming from JSON

Usage:
    python scripts/scraper.py                # All boards
    python scripts/scraper.py --board crb    # One board only

Output:
  data/<key>.json    Per-board data files
  data/state.json    Watchdog snapshot (full runs only)
  data/meta.json     Pipeline timestamp { "lastUpdated": "..." }
"""

import argparse
import json
import os
import re
import smtplib
import traceback
from datetime import datetime, date, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

CITY_BASE_URL           = "https://www.kalamazoocity.org"
CITY_YOUTUBE_CHANNEL_ID = "UCIgXSSXLSDxThVaaiRMsR5Q"
CIVICCLERK_TENANT       = "kalamazoomi"
MINUTES_AGENDAS_URL     = f"{CITY_BASE_URL}/Government/Boards-Commissions/Minutes-Agendas"
SPECIAL_NOTICES_URL     = f"{CITY_BASE_URL}/Government/Boards-Commissions/Special-Meeting-Notices"
LOOKBACK_MONTHS         = 6
LOOKAHEAD_MONTHS        = 6
PRESERVE_IF_EMPTY       = ("agenda_url", "minutes_url", "youtube_id", "youtube_url", "scrapedAt")
DETROIT_TZ              = ZoneInfo("America/Detroit")


# ---------------------------------------------------------------------------
# Board configuration
#
# All per-board metadata lives here — adding a board means touching ONE place.
#
#   abbr               Uppercase display label used in calendar.json / ICS
#   time               Meeting time string shown to the public
#   location           Static default location (None = resolved per-meeting)
#   session_note       Cross-board joint-session note (optional)
#   meetingScheduleNote  Explains irregular schedule patterns (optional)
# ---------------------------------------------------------------------------

BOARDS = [
    {
        "key":         "crb",
        "name":        "Civil Rights Board",
        "abbr":        "CRB",
        "time":        "5:00 PM",
        "location":    "City Commission Chambers, City Hall Second Floor, 241 W South St",
        "category_id": 32,
        "keywords":    ["civil rights board", "civil rights"],
        "output":      Path("data") / "crb.json",
        "youtube":     True,
        "youtube_search_query": "Civil Rights Board",
        "youtube_title_filter": ["civil rights board", "civil rights"],
        "youtube_tolerance":    3,
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Civil-Rights-Board",
    },
    {
        "key":          "bra",
        "name":         "Brownfield Redevelopment Authority",
        "abbr":         "BRA",
        "time":         "7:45 AM \u2013 9:30 AM",
        "location":     "CPED Main Conference Room, 245 N Rose St, Suite 100",
        "session_note": "Meets immediately following EDC",
        "category_id":  34,
        "keywords":     ["brownfield redevelopment authority"],
        "output":       Path("data") / "bra.json",
        "youtube":      False,
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Brownfield-Redevelopment-Authority-Economic-Development-Corporation",
    },
    {
        "key":                    "cpsrab",
        "name":                   "Citizens Public Safety Review and Appeal Board",
        "abbr":                   "CPSRAB",
        "time":                   "6:00 PM \u2013 8:00 PM",
        "location":               "City Commission Chambers, City Hall Second Floor, 241 W South St",
        "scraper_type":           "web_docs_and_youtube",
        "minutes_agendas_section":"Citizens Public Safety Review and Appeal Board",
        "category_id":            None,
        "keywords":               [],
        "output":                 Path("data") / "cpsrab.json",
        "youtube":                True,
        "youtube_search_query":   "Citizens Public Safety Review and Appeal Board",
        "youtube_title_filter":   ["citizens public safety", "cpsrab"],
        "youtube_tolerance":      3,
        "schedule":               ("monthly", "tuesday", 2, None),
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Citizens-Public-Safety-Review-and-Appeal-Board-CPSRAB",
    },
    {
        "key":         "dda",
        "name":        "Downtown Development Authority",
        "abbr":        "DDA",
        "time":        "3:00 PM \u2013 5:00 PM",
        "location":    "City Hall, 241 W South St",
        "category_id": 38,
        "keywords":    ["downtown development authority", "dda"],
        "output":      Path("data") / "dda.json",
        "youtube":     False,
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Downtown-Development-Authority-Downtown-Economic-Growth-Authority",
    },
    {
        "key":         "dega",
        "name":        "Downtown Economic Growth Authority",
        "abbr":        "DEGA",
        "time":        "3:00 PM \u2013 5:00 PM",
        "location":    "City Hall, 241 W South St",
        "category_id": 39,
        "keywords":    ["downtown economic growth authority", "dega"],
        "output":      Path("data") / "dega.json",
        "youtube":     False,
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Downtown-Development-Authority-Downtown-Economic-Growth-Authority",
    },
    {
        "key":          "edc",
        "name":         "Economic Development Corporation",
        "abbr":         "EDC",
        "time":         "7:45 AM \u2013 9:30 AM",
        "location":     "CPED Main Conference Room, 245 N Rose St, Suite 100",
        "session_note": "BRA meets immediately following",
        "category_id":  33,
        "keywords":     ["economic development corporation", "edc"],
        "output":       Path("data") / "edc.json",
        "youtube":      False,
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Brownfield-Redevelopment-Authority-Economic-Development-Corporation",
    },
    {
        "key":            "ec",
        "name":           "Election Commission",
        "abbr":           "EC",
        "time":           "9:00 AM",
        "location":       None,
        "category_id":    37,
        "keywords":       ["election commission", "election inspector", "accuracy test", "precinct", "election"],
        "output":         Path("data") / "ec.json",
        "youtube":        False,
        "upcoming_from_web": True,
        "web_url":        f"{CITY_BASE_URL}/Government/Boards-Commissions/Election-Commission",
        "parse_locations": True,
    },
    {
        "key":         "ecc",
        "name":        "Environmental Concerns Committee",
        "abbr":        "ECC",
        "time":        "4:30 PM \u2013 6:30 PM",
        "location":    "City Commission Chambers, City Hall Second Floor, 241 W South St",
        "category_id": 46,
        "keywords":    ["environmental concerns committee", "environmental concerns"],
        "output":      Path("data") / "ecc.json",
        "youtube":     True,
        "youtube_search_query": "Environmental Concerns Committee",
        "youtube_title_filter": ["environmental concerns committee", "environmental concerns"],
        "youtube_tolerance":    3,
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Environmental-Concerns-Committee",
    },
    {
        "key":         "hdc",
        "name":        "Historic District Commission",
        "abbr":        "HDC",
        "time":        "5:00 PM \u2013 7:00 PM",
        "location":    "City Commission Chambers, City Hall Second Floor, 241 W South St",
        "category_id": 35,
        "keywords":    ["historic district commission", "historic district", "hdc"],
        "output":      Path("data") / "hdc.json",
        "youtube":     True,
        "youtube_search_query": "Historic District Commission",
        "youtube_title_filter": ["historic district"],
        "youtube_tolerance":    3,
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Historic-District-Commission",
    },
    {
        "key":         "hpc",
        "name":        "Historic Preservation Commission",
        "abbr":        "HPC",
        "time":        "6:00 PM \u2013 8:00 PM",
        "location":    "City Hall, 241 W South St",
        "category_id": 36,
        "keywords":    ["historic preservation commission"],
        "output":      Path("data") / "hpc.json",
        "youtube":     True,
        "youtube_search_query": "Historic Preservation Commission",
        "youtube_title_filter": ["historic preservation commission", "historical preservation commission"],
        "youtube_tolerance":    3,
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Historic-Preservation-Commission",
    },
    {
        "key":         "locc",
        "name":        "Local Officers Compensation Commission",
        "abbr":        "LOCC",
        "time":        "On Call",
        "location":    "City Hall, 241 W South St",
        "category_id": 31,
        "keywords":    ["local officers compensation commission", "locc"],
        "output":      Path("data") / "locc.json",
        "youtube":     False,
        "upcoming_web_override_cc": True,
        "web_url":     f"{CITY_BASE_URL}/Government/Boards-Commissions/Local-Officers-Compensation-Commission",
    },
    {
        "key":         "nfp",
        "name":        "Natural Features Protection Review Board",
        "abbr":        "NFP",
        "time":        "4:00 PM \u2013 6:00 PM",
        "location":    "City Commission Chambers, City Hall Second Floor, 241 W South St",
        "category_id": 41,
        "keywords":    ["natural features protection", "nfp"],
        "output":      Path("data") / "nfp.json",
        "youtube":     True,
        "youtube_search_query": "Natural Features Protection Review Board",
        "youtube_title_filter": ["natural features protection"],
        "youtube_tolerance":    3,
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Natural-Features-Protection-Review-Board",
    },
    {
        "key":         "pc",
        "name":        "Planning Commission",
        "abbr":        "PC",
        "time":        "7:00 PM \u2013 9:00 PM",
        "location":    "City Commission Chambers, City Hall Second Floor, 241 W South St",
        "category_id": 29,
        "keywords":    ["planning commission"],
        "output":      Path("data") / "pc.json",
        "youtube":     True,
        "youtube_search_query": "Planning Commission Kalamazoo",
        "youtube_title_filter": ["planning commission"],
        "youtube_tolerance":    3,
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Planning-Commission",
    },
    {
        "key":         "zba",
        "name":        "Zoning Board of Appeals",
        "abbr":        "ZBA",
        "time":        "7:00 PM \u2013 9:00 PM",
        "location":    "City Commission Chambers, City Hall Second Floor, 241 W South St",
        "category_id": 30,
        "keywords":    ["zoning board of appeals", "zoning board"],
        "output":      Path("data") / "zba.json",
        "youtube":     True,
        "youtube_search_query": "Zoning Board of Appeals Kalamazoo",
        "youtube_title_filter": ["zoning board of appeals", "zoning board"],
        "youtube_tolerance":    3,
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Zoning-Board-of-Appeals",
    },
    {
        "key":         "ersb",
        "name":        "Employee Retirement System Board of Trustees",
        "abbr":        "ERSB",
        "time":        "8:00 AM \u2013 9:00 AM",
        "location":    "City Hall, 241 W South St",
        "category_id": 42,
        "keywords":    ["employees retirement system", "retirement system", "pension"],
        "output":      Path("data") / "ersb.json",
        "youtube":     False,
        "preserve_upcoming": True,
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Employee-Retirement-System-Board-of-Trustees-Pension-Board",
    },
    {
        "key":             "prab",
        "name":            "Parks & Recreation Advisory Board",
        "abbr":            "PRAB",
        "time":            "5:30 PM \u2013 7:30 PM",
        "location":        None,
        "scraper_type":    "web_scrape",
        "web_url":         f"{CITY_BASE_URL}/Government/Boards-Commissions/Parks-Recreation-Advisory-Board-PRAB",
        "category_id":     None,
        "keywords":        [],
        "output":          Path("data") / "prab.json",
        "youtube":         False,
        "parse_locations": True,
    },
    {
        "key":                 "trb",
        "name":                "Traffic Board",
        "abbr":                "TRB",
        "time":                "",
        "location":            "Kalamazoo Public Services, 415 E Stockbridge Ave",
        "meetingScheduleNote": "No regular schedule \u2014 special meetings called as needed",
        "scraper_type":        "web_scrape",
        "web_url":             f"{CITY_BASE_URL}/Government/Boards-Commissions/Traffic-Board",
        "category_id":         None,
        "keywords":            [],
        "output":              Path("data") / "trb.json",
        "youtube":             False,
    },
    {
        "key":                 "bor",
        "name":                "Board of Review for Assessments",
        "abbr":                "BOR",
        "time":                "TBD",
        "location":            "Third Floor Conference Room, City Hall, 241 W South St",
        "meetingScheduleNote": "Seasonal \u2014 March hearings, July and December corrections",
        "scraper_type":        "web_scrape",
        "web_url":             f"{CITY_BASE_URL}/Government/Boards-Commissions/Board-of-Review-for-Assessments",
        "category_id":         None,
        "keywords":            [],
        "output":              Path("data") / "bor.json",
        "youtube":             False,
    },
    {
        "key":         "ric",
        "name":        "Retirement Investment Committee / Perpetual Care Investment Committee",
        "abbr":        "RIC",
        "time":        "11:00 AM \u2013 12:00 PM",
        "location":    "W.E. Upjohn Institute, 300 S Westnedge Ave",
        "scraper_type": "web_scrape",
        "web_url":     f"{CITY_BASE_URL}/Government/Boards-Commissions/Retirement-Investment-Committee-Perpetual-Care-Investment-Committee",
        "category_id": None,
        "keywords":    [],
        "output":      Path("data") / "ric.json",
        "youtube":     False,
    },
    {
        "key":         "kmga",
        "name":        "Kalamazoo Municipal Golf Association",
        "abbr":        "KMGA",
        "time":        "12:00 PM \u2013 2:00 PM",
        "location":    None,
        "scraper_type": "web_scrape",
        "web_url":     f"{CITY_BASE_URL}/Government/Boards-Commissions/Kalamazoo-Municipal-Golf-Association",
        "category_id": None,
        "keywords":    [],
        "output":      Path("data") / "kmga.json",
        "youtube":     False,
    },
    {
        "key":         "tre",
        "name":        "Tree Committee",
        "abbr":        "TRE",
        "time":        "2:00 PM \u2013 4:00 PM",
        "location":    "Kalamazoo Stockbridge Facility, 415 E Stockbridge Ave",
        "scraper_type": "web_scrape",
        "web_url":     f"{CITY_BASE_URL}/Government/Boards-Commissions/Tree-Committee",
        "category_id": None,
        "keywords":    [],
        "output":      Path("data") / "tre.json",
        "youtube":     False,
    },
    {
        "key":         "bba",
        "name":        "Building Board of Appeals",
        "abbr":        "BBA",
        "time":        "4:00 PM \u2013 6:00 PM",
        "location":    "City Commission Chambers, City Hall Second Floor, 241 W South St",
        "scraper_type": "web_scrape",
        "web_url":     f"{CITY_BASE_URL}/Government/Boards-Commissions/Building-Board-of-Appeals",
        "category_id": None,
        "keywords":    [],
        "output":      Path("data") / "bba.json",
        "youtube":     False,
    },
    {
        "key":         "cdaac",
        "name":        "Community Development Act Advisory Committee",
        "abbr":        "CDAAC",
        "time":        "5:30 PM \u2013 7:30 PM",
        "location":    "Community Room, City Hall Second Floor, 241 W South St",
        "scraper_type": "web_scrape",
        "web_url":     f"{CITY_BASE_URL}/Government/Boards-Commissions/Community-Development-Act-Advisory-Committee-CDAAC",
        "category_id": None,
        "keywords":    [],
        "output":      Path("data") / "cdaac.json",
        "youtube":     False,
    },
    {
        "key":         "ncbda",
        "name":        "Northside Cultural Business District Authority Board",
        "abbr":        "NCBDA",
        "time":        "6:00 PM \u2013 7:00 PM",
        "location":    "Northside Association for Community Development, 612 N Park St",
        "category_id": 43,
        "keywords":    ["northside cultural business district", "northside cultural", "ncbda"],
        "output":      Path("data") / "ncbda.json",
        "youtube":     False,
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Northside-Cultural-Business-District-Authority-NCBDA",
    },
    {
    "key":         "spk",
    "name":        "SPK Organizing Committee",
    "abbr":        "SPK",
    "time":        "12:30 PM \u2013 2:00 PM",
    "location":    "City Hall, 241 W South St",
    "meetingScheduleNote": "Meets the second Monday of every other month \u2014 confirmed dates posted when available",
    "category_id": 44,
    "keywords":    ["shared prosperity kalamazoo", "spk organizing committee", "spk"],
    "output":      Path("data") / "spk.json",
    "youtube":     False,
    "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Shared-Prosperity-Kalamazoo-SPK-Organizing-Committee",
},
]

# Fast key lookup — used by build.py and watchdog
BOARDS_BY_KEY: dict = {b["key"]: b for b in BOARDS}

# Maps text fragments from Special Meeting Notices to board abbrs.
# Order matters — more specific fragments listed first.
NOTICE_BOARD_MAP = [
    ("citizens public safety review",          "CPSRAB"),
    ("citizen public safety review",           "CPSRAB"),
    ("csprab",                                 "CPSRAB"),
    ("zoning board of appeals",                "ZBA"),
    ("planning commission",                    "PC"),
    ("civil rights board",                     "CRB"),
    ("brownfield redevelopment authority",     "BRA"),
    ("economic development corporation",       "EDC"),
    ("downtown economic growth authority",     "DEGA"),
    ("downtown development authority",         "DDA"),
    ("environmental concerns committee",       "ECC"),
    ("historic district commission",           "HDC"),
    ("historic preservation commission",       "HPC"),
    ("natural features protection",            "NFP"),
    ("parks and recreation advisory",          "PRAB"),
    ("parks & recreation advisory",            "PRAB"),
    ("traffic board",                          "TRB"),
    ("tree committee",                         "TRE"),
    ("building board of appeals",              "BBA"),
    ("community development act",              "CDAAC"),
    ("northside cultural business",            "NCBDA"),
    ("shared prosperity kalamazoo",            "SPK"),
    ("spk organizing committee",               "SPK"),
    ("employee retirement system",             "ERSB"),
    ("pension board",                          "ERSB"),
    ("board of review",                        "BOR"),
    ("election commission",                    "EC"),
    ("local officers compensation",            "LOCC"),
    ("retirement investment committee",        "RIC"),
    ("kalamazoo municipal golf",               "KMGA"),
]

# ---------------------------------------------------------------------------
# Alert email
# ---------------------------------------------------------------------------

def send_alert_email(subject: str, body: str) -> None:
    """Send email via SMTP. Credentials from environment variables.

    Required env vars: SMTP_HOST, SMTP_USER, SMTP_PASS, NOTIFY_EMAIL
    Optional:          SMTP_PORT (default 587)
    """
    host      = os.environ.get("SMTP_HOST", "").strip()
    port_str  = os.environ.get("SMTP_PORT", "").strip()
    port      = int(port_str) if port_str else 587
    user      = os.environ.get("SMTP_USER", "").strip()
    password  = os.environ.get("SMTP_PASS", "").strip()
    recipient = os.environ.get("NOTIFY_EMAIL", "").strip()

    if not all([host, user, password, recipient]):
        print(f"  [email] Not configured — alert not sent: {subject}")
        return

    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = user
        msg["To"]      = recipient
        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            server.starttls()
            server.login(user, password)
            server.sendmail(user, [recipient], msg.as_string())
        print(f"  [email] Sent: {subject}")
    except Exception:
        print(f"  [email] Failed to send '{subject}':\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# City website metadata scraping
# ---------------------------------------------------------------------------

def scrape_city_web_info(url: str) -> dict:
    """
    Scrape meeting time and location from a city board page.
    Returns dict with 'time' and/or 'location' if found, empty dict on failure.

    Normalizations applied to scraped location:
      - "City Commission Chambers" + "City Hall" without "Second Floor"
        → "Second Floor" inserted
      - "415 Stockbridge" → "415 E Stockbridge"
    """
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        text   = r.text
        result = {}

        # Time: "Next date: Wednesday, June 03, 2026 | 05:00 PM\n to 07:00 PM"
        time_m = re.search(
            r"Next date:[^|]+\|\s*(\d{1,2}:\d{2}\s+[AP]M)\s*\n?\s*to\s*(\d{1,2}:\d{2}\s+[AP]M)",
            text, re.IGNORECASE,
        )
        if time_m:
            result["time"] = f"{time_m.group(1).strip()} \u2013 {time_m.group(2).strip()}"

        # Location: section after "## Location"
        loc_m = re.search(
            r"##\s*Location\s*\n+(.*?)(?=\n##|\Z)",
            text, re.IGNORECASE | re.DOTALL,
        )
        if loc_m:
            loc_raw = loc_m.group(1)
            loc_raw = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", loc_raw)
            loc_raw = re.sub(r",?\s*Kalamazoo,?\s*\d{5}[^,\n]*", "", loc_raw)
            lines = [
                ln.strip()
                for ln in loc_raw.split("\n")
                if ln.strip()
                and not re.match(r"^[\d.,\s-]+$", ln.strip())
                and "View Map"  not in ln
                and "Skip to"   not in ln
                and not ln.strip().startswith("#")
            ]
            if lines:
                location = ", ".join(lines[:2])
                location = location.replace("Kalamazoo City Hall, Second Floor", "City Hall Second Floor")
                location = location.replace("Kalamazoo City Hall",               "City Hall")
                location = re.sub(r"\bStreet\b", "St",  location)
                location = re.sub(r"\bAvenue\b", "Ave", location)
                location = re.sub(r",\s*,",     ",",    location)
                location = re.sub(r"\s+",       " ",    location).strip().rstrip(",")

                # Normalize: City Commission Chambers in City Hall always on Second Floor
                if (
                    "City Commission Chambers" in location
                    and "City Hall" in location
                    and "Second Floor" not in location
                ):
                    location = location.replace("City Hall", "City Hall Second Floor")

                # Normalize: 415 Stockbridge → 415 E Stockbridge
                location = re.sub(r"\b415 Stockbridge\b", "415 E Stockbridge", location)

                result["location"] = location

        return result

    except Exception as e:
        print(f"    WARNING: Could not scrape board info from {url}: {e}")
        return {}


def refresh_board_metadata(boards_to_run: list) -> dict:
    """
    Fetch current time and location from the city website for each board.
    Updates the board dict in place and returns a summary of what changed.

    Returns:
        dict mapping board key → {field: new_value} for all fields refreshed.

    skip_time:     boards whose configured time must never be overwritten
    skip_location: boards with dynamic per-meeting locations
    """
    skip_location = {"prab", "kmga"}
    skip_time     = {"locc", "bor"}   # locc is intentional "On Call"; bor has multi-session days

    print("\nRefreshing board metadata from city website...")
    all_updates: dict = {}

    for board in boards_to_run:
        url = board.get("web_url")
        if not url:
            continue

        key  = board["key"]
        info = scrape_city_web_info(url)
        board_updates: dict = {}

        if info.get("time") and key not in skip_time:
            board["time"] = info["time"]
            board_updates["time"] = info["time"]
            print(f"    {key.upper()}: time \u2192 {info['time']}")

        if info.get("location") and key not in skip_location:
            board["location"] = info["location"]
            board_updates["location"] = info["location"]
            print(f"    {key.upper()}: location \u2192 {info['location']}")

        if board_updates:
            all_updates[key] = board_updates

    total = sum(len(v) for v in all_updates.values())
    print(f"    Done. {total} value(s) refreshed across {len(all_updates)} board(s).")
    return all_updates


# ---------------------------------------------------------------------------
# Meeting location resolver (used by build.py via import)
# ---------------------------------------------------------------------------

def extract_cc_location(event: dict) -> str | None:
    """Extract and normalize a location string from a CivicClerk event object."""
    loc = event.get("eventLocation")
    if not loc:
        return None
    parts = [loc.get("address1") or "", loc.get("address2") or ""]
    parts = [p.strip() for p in parts if p and p.strip()]
    if not parts:
        return None
    location = ", ".join(parts)
    location = re.sub(r"\bStreet\b", "St",  location)
    location = re.sub(r"\bAvenue\b", "Ave", location)
    location = re.sub(r"\b415 Stockbridge\b", "415 E Stockbridge", location)
    location = re.sub(r"\s+", " ", location).strip()
    return location or None


def get_cc_location_override(event: dict, board: dict) -> str | None:
    """
    Returns a CivicClerk location only when it represents a genuinely different
    venue from the board's static default — detected by comparing street numbers.

    Same street number = formatting variation only, use static default.
    Different street number = genuine location change, use CivicClerk address.
    """
    cc_loc = extract_cc_location(event)
    if not cc_loc:
        return None

    static_loc = board.get("location") or ""
    cc_num     = re.search(r"\b(\d{2,5})\b", cc_loc)
    static_num = re.search(r"\b(\d{2,5})\b", static_loc)

    if cc_num and static_num and cc_num.group(1) == static_num.group(1):
        return None

    return cc_loc if cc_num else None


def get_meeting_location(board: dict, date_iso: str, meeting: dict) -> str | None:
    """Resolve the display location for a single meeting."""
    # Per-meeting override (e.g. EC, PRAB web-scraped locations)
    if meeting.get("location"):
        return meeting["location"]

    key = board["key"]

    if key == "prab":
        return "Community Room, Mayors' Riverfront Park"

    if key == "kmga":
        month = int(date_iso[5:7])
        return (
            "Eastern Hills Golf Club, Kalamazoo"
            if month in (1, 2, 3, 10, 11, 12)
            else "Milham Park Golf Club, Kalamazoo"
        )

    return board.get("location")


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
        all_events.extend(data.get("value", []))
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
    """Cross-platform equivalent of '%B %#d, %Y' (avoids Windows-only %#d)."""
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def format_display_date_long(iso: str) -> str:
    """Cross-platform equivalent of '%A, %B %#d, %Y'."""
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d.strftime('%A')}, {d.strftime('%B')} {d.day}, {d.year}"


def normalize_meeting_type(event_name: str) -> str:
    if "special" in event_name.lower():
        return "Special Meeting"
    return event_name


def transform_event(event: dict, board: dict) -> dict | None:
    """Convert a raw CivicClerk event to a meeting record.

    scrapedAt is intentionally left None here; it is stamped in
    merge_meetings when the record is first inserted.
    """
    event_id        = event["id"]
    date_only       = event["startDateTime"].split("T")[0]
    published_files = event.get("publishedFiles", [])
    agenda_file_id  = find_file_id(published_files, "Agenda")
    minutes_file_id = find_file_id(published_files, "Minutes")
    name_lower      = event.get("eventName", "").lower()
    cancelled       = "cancel" in name_lower

    if not agenda_file_id and not minutes_file_id:
        return None

    source_url = (
        f"https://{CIVICCLERK_TENANT}.portal.civicclerk.com"
        f"/event/{event_id}/overview"
    )

    record = {
        "date":        date_only,
        "display":     format_display_date(date_only),
        "event_id":    event_id,
        "url":         build_doc_url(event_id, agenda_file_id) if agenda_file_id else source_url,
        "link_label":  "Agenda & Minutes" if minutes_file_id else "Agenda",
        "isCancelled": cancelled,
        "minutes_url": build_doc_url(event_id, minutes_file_id) if minutes_file_id else None,
        "agenda_url":  build_doc_url(event_id, agenda_file_id) if agenda_file_id else None,
        "location":    get_cc_location_override(event, board),
        "sourceUrl":   source_url,
        "scrapedAt":   None,
    }
    if board.get("key") != "crb":
        record["meeting_type"] = normalize_meeting_type(event.get("eventName", ""))

    return record


def events_to_upcoming(events: list[dict], board: dict) -> list[dict]:
    upcoming = []
    for event in events:
        date_only       = event["startDateTime"].split("T")[0]
        published_files = event.get("publishedFiles", [])
        agenda_file_id  = find_file_id(published_files, "Agenda")
        item: dict = {
            "date":    date_only,
            "display": format_display_date_long(date_only),
            "time":    board.get("time", "TBD"),
        }
        loc = get_cc_location_override(event, board)
        if loc:
            item["location"] = loc
        if agenda_file_id:
            item["agenda_url"] = build_doc_url(event["id"], agenda_file_id)
        upcoming.append(item)
    upcoming.sort(key=lambda m: m["date"])
    return upcoming


# ---------------------------------------------------------------------------
# Schedule-based upcoming
# ---------------------------------------------------------------------------

def nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """Return the nth occurrence (1-based) of weekday (0=Mon…6=Sun) in month."""
    d = date(year, month, 1)
    days_ahead = weekday - d.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return d + timedelta(days=days_ahead) + timedelta(weeks=n - 1)


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
# Minutes-Agendas page scraping  (web_docs_and_youtube boards)
# ---------------------------------------------------------------------------

def scrape_minutes_agendas_docs(board: dict, start_iso: str, end_iso: str) -> list[dict]:
    """
    Fetch the city Minutes-Agendas page, isolate the board's section, and
    return meeting dicts with agenda_url / minutes_url.

    sourceUrl is set to MINUTES_AGENDAS_URL; scrapedAt is left None (set in
    merge_meetings on first insert).
    """
    section_name = board["minutes_agendas_section"]
    print(f"    [Web] Fetching Minutes-Agendas page for {section_name}...")
    r = requests.get(MINUTES_AGENDAS_URL, timeout=30)
    r.raise_for_status()
    html = r.text

    section_pattern = re.compile(
        r"(?:<h2[^>]*>.*?" + re.escape(section_name) + r".*?</h2>)(.*?)(?=<h2|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    section_match = section_pattern.search(html)
    if not section_match:
        print(f"    WARNING: Could not find '{section_name}' section on Minutes-Agendas page.")
        return []

    section_html  = section_match.group(1)
    link_pattern  = re.compile(r'<a\s[^>]*href="([^"]+\.pdf)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    date_pattern  = re.compile(
        r"(January|February|March|April|May|June|July|August"
        r"|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
        re.IGNORECASE,
    )
    start_dt = datetime.strptime(start_iso, "%Y-%m-%d").date()
    end_dt   = datetime.strptime(end_iso,   "%Y-%m-%d").date()
    by_date: dict = {}

    for link_match in link_pattern.finditer(section_html):
        href      = link_match.group(1).strip()
        link_text = re.sub(r"<[^>]+>", "", link_match.group(2)).strip()
        if href.startswith("/"):
            href = f"{CITY_BASE_URL}{href}"
        date_match = date_pattern.search(link_text)
        if not date_match:
            continue
        try:
            doc_date = datetime.strptime(
                f"{date_match.group(1)} {date_match.group(2)} {date_match.group(3)}", "%B %d %Y"
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

    meetings = []
    for iso, docs in sorted(by_date.items()):
        if not docs["agenda_url"] and not docs["minutes_url"]:
            continue
        if docs["agenda_url"] and docs["minutes_url"]:
            link_label = "Agenda & Minutes"
        elif docs["agenda_url"]:
            link_label = "Agenda"
        else:
            link_label = "Minutes"
        meetings.append({
            "date":        iso,
            "display":     format_display_date(iso),
            "agenda_url":  docs["agenda_url"],
            "minutes_url": docs["minutes_url"],
            "link_label":  link_label,
            "isCancelled": False,
            "sourceUrl":   MINUTES_AGENDAS_URL,
            "scrapedAt":   None,
        })
        print(f"    {iso}  {link_label}")

    print(f"    Found {len(meetings)} meetings with documents in window")
    return meetings


# ---------------------------------------------------------------------------
# Web scrape upcoming  (web_scrape boards + EC / LOCC fallback)
# ---------------------------------------------------------------------------

def check_dom_integrity(html: str) -> bool:
    """
    Returns True when the page contains the expected date|pipe structure.
    Returns False when the pattern is completely absent — signals a potential
    DOM change that may require scraper maintenance.
    """
    return bool(re.search(r"\w+day,\s+\w+\s+\d{1,2},\s+\d{4}\s*\|", html))


def scrape_location_overrides(text: str) -> dict:
    """Parse per-meeting location overrides from board page HTML.
    Only called for boards with parse_locations: True.
    Returns dict of iso_date -> location_string.
    """
    overrides: dict = {}
    today = date.today()

    # PRAB format: "<li>June 12 at Spring Valley Park</li>"
    li_pattern   = re.compile(r"<li>(.*?)</li>", re.IGNORECASE | re.DOTALL)
    prab_pattern = re.compile(
        r"(January|February|March|April|May|June|July|August"
        r"|September|October|November|December)"
        r"\s+(\d{1,2})\s+at\s+(.+)",
        re.IGNORECASE,
    )
    for li_match in li_pattern.finditer(text):
        li_text = re.sub(r"<[^>]+>", " ", li_match.group(1)).strip().replace("&nbsp;", " ")
        loc_match = prab_pattern.search(li_text)
        if loc_match:
            month_str, day_str, location = loc_match.groups()
            location = location.strip().rstrip("., ")
            for year in (today.year, today.year + 1):
                try:
                    d = datetime.strptime(f"{month_str} {day_str} {year}", "%B %d %Y").date()
                    if d >= today:
                        overrides[d.strftime("%Y-%m-%d")] = location
                        break
                except ValueError:
                    continue

    # EC format: "…July 9, 2026, at 9:00 a.m. at the City Records Center"
    ec_pattern = re.compile(
        r"(January|February|March|April|May|June|July|August"
        r"|September|October|November|December)"
        r"\s+(\d{1,2}),?\s+(\d{4}),?\s+at\s+[\d:apm.\s]+(?:at|in)\s+(.*?)(?=<|\n|$)",
        re.IGNORECASE,
    )
    for match in ec_pattern.finditer(text):
        month_str, day_str, year_str, location = match.groups()
        location = location.strip().rstrip("., ")
        if "City Record" in location:
            location = "City Records Center, 3001 S Burdick St"
        elif "Community Room" in location:
            location = "Community Room, City Hall Second Floor, 241 W South St"
        try:
            d = datetime.strptime(f"{month_str} {day_str} {year_str}", "%B %d %Y").date()
            if d >= today:
                overrides[d.strftime("%Y-%m-%d")] = location
        except ValueError:
            continue

    return overrides


def scrape_web_upcoming(board: dict, dom_alerts: list, html: str | None = None) -> list[dict]:
    """Scrape upcoming meeting dates from a city website board page."""
    url = board["web_url"]
    if html is None:
        print(f"    [Web] Fetching {url}...")
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        html = r.text

    if not check_dom_integrity(html):
        msg = f"{board['name']} ({board['key']}) — no date|pipe pattern at {url}"
        dom_alerts.append(msg)
        print(f"    WARNING: DOM structure check failed for {board['key'].upper()}")

    today    = date.today()
    upcoming = []
    seen: set = set()

    location_overrides: dict = {}
    if board.get("parse_locations"):
        location_overrides = scrape_location_overrides(html)
        if location_overrides:
            print(f"    Found {len(location_overrides)} location override(s): "
                  f"{list(location_overrides.values())}")

    pattern = r"(\w+day,\s+\w+\s+\d{1,2},\s+\d{4})\s*\|"
    for match in re.findall(pattern, html):
        match_clean = re.sub(r"\s+", " ", match.strip())
        if match_clean in seen:
            continue
        seen.add(match_clean)
        try:
            d = datetime.strptime(match_clean, "%A, %B %d, %Y").date()
            if d >= today:
                item: dict = {
                    "date":    d.strftime("%Y-%m-%d"),
                    "display": format_display_date_long(d.strftime("%Y-%m-%d")),
                    "time":    board.get("time", "TBD"),
                }
                loc = location_overrides.get(d.strftime("%Y-%m-%d"))
                if loc:
                    item["location"] = loc
                upcoming.append(item)
        except ValueError:
            continue

    upcoming.sort(key=lambda m: m["date"])
    print(f"    Found {len(upcoming)} upcoming meetings")
    return upcoming


def scrape_web_past_meetings(board: dict, html: str | None = None) -> list[dict]:
    """
    Scrape recent past meeting dates from a city website board page.
    Returns minimal meeting records for dates within the last 2 months.
    Used by web_scrape boards that have no CivicClerk history.
    """
    url = board.get("web_url")
    if not url:
        return []
    if html is None:
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            html = r.text
        except Exception as exc:
            print(f"    WARNING: Could not fetch past meetings from {url}: {exc}")
            return []

    today        = date.today()
    lookback_iso = (today - timedelta(days=60)).strftime("%Y-%m-%d")
    past         = []
    seen: set    = set()

    pattern = r"(\w+day,\s+\w+\s+\d{1,2},\s+\d{4})\s*\|"
    for match in re.findall(pattern, html):
        match_clean = re.sub(r"\s+", " ", match.strip())
        if match_clean in seen:
            continue
        seen.add(match_clean)
        try:
            d        = datetime.strptime(match_clean, "%A, %B %d, %Y").date()
            date_iso = d.strftime("%Y-%m-%d")
            if lookback_iso <= date_iso and d < today:
                past.append({
                    "date":    date_iso,
                    "display": format_display_date(date_iso),
                })
        except ValueError:
            continue

    past.sort(key=lambda m: m["date"], reverse=True)
    if past:
        print(f"    Found {len(past)} past meeting(s) in lookback window")
    return past
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
    channel_id = board.get("youtube_channel_id", CITY_YOUTUBE_CHANNEL_ID)
    params = {
        "key":             api_key,
        "channelId":       channel_id,
        "q":               board["youtube_search_query"],
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
        r = requests.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=30)
        r.raise_for_status()
        data       = r.json()
        all_items.extend(data.get("items", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    recordings   = []
    title_filter = board.get("youtube_title_filter", [])
    date_pattern = re.compile(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2},?\s+\d{4}"
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
            data = json.load(f)
        # One-time migration: renamed field cancelled → isCancelled
        for m in data.get("meetings", []):
            if "cancelled" in m and "isCancelled" not in m:
                m["isCancelled"] = m.pop("cancelled")
        return data
    return {"last_updated": None, "upcoming_meetings": [], "meetings": [], "recordings": []}


def smart_merge(existing: dict, scraped: dict) -> tuple:
    """Merge scraped record into existing, preserving designated fields when empty."""
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
            # Stamp provenance on first insertion
            s["scrapedAt"] = datetime.now(timezone.utc).isoformat()
            by_key[k]      = s
            stats["added"] += 1
            print(f"    + NEW:     {s['date']}")
            continue

        merged, preserved = smart_merge(by_key[k], s)
        if preserved:
            stats["preserved"] += len(preserved)
        changed = any(
            by_key[k].get(f) != merged.get(f)
            for f in ("url", "isCancelled", "minutes_url", "agenda_url", "youtube_id", "youtube_url")
        )
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
# Shared metadata builder
# ---------------------------------------------------------------------------

def build_metadata(board: dict) -> dict:
    """Build the metadata block written to every board output file."""
    meta: dict = {
        "time":     board.get("time", "TBD"),
        "location": board.get("location") or "TBD",
    }
    for optional in ("session_note", "meetingScheduleNote"):
        if board.get(optional):
            meta[optional] = board[optional]
    return meta


# ---------------------------------------------------------------------------
# Special Meeting Notices helpers
# ---------------------------------------------------------------------------

def _detect_notice_boards(text: str) -> list[str]:
    """Return list of board abbrs mentioned in notice text."""
    text_lower = text.lower()
    found, seen = [], set()
    for fragment, abbr in NOTICE_BOARD_MAP:
        if fragment in text_lower and abbr not in seen:
            found.append(abbr)
            seen.add(abbr)
    return found


def _extract_notice_dates(text: str) -> list[str]:
    """Extract all ISO date strings from notice text."""
    pattern = re.compile(
        r"(January|February|March|April|May|June|July|August"
        r"|September|October|November|December)"
        r"\s+(\d{1,2}),?\s+(\d{4})",
        re.IGNORECASE,
    )
    results, seen = [], set()
    for m in pattern.finditer(text):
        try:
            d = datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y"
            ).date()
            iso = d.strftime("%Y-%m-%d")
            if iso not in seen:
                results.append(iso)
                seen.add(iso)
        except ValueError:
            continue
    return results


def _extract_moved_location(text: str) -> str | None:
    """Pull new location from a location-change notice."""
    m = re.search(r"moved to meet in\s+(.+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip().rstrip(".,")
    return None
  
def _extract_special_meeting_info(text: str) -> tuple[str | None, str | None]:
    """Return (time_str, location_str) from a special-meeting notice."""
    time_m = re.search(r"\bat\s+(\d{1,2}:\d{2}\s*[aApP]\.?[mM]\.?)", text)
    time_str = None
    if time_m:
        t = time_m.group(1).strip().rstrip(".")
        t = re.sub(r"a\.m\.?", "AM", t, flags=re.IGNORECASE)
        t = re.sub(r"p\.m\.?", "PM", t, flags=re.IGNORECASE)
        time_str = t

    loc_m = re.search(
        r"(?:take place (?:at|in)|meet (?:at|in))\s+(.+?)(?:\.\s+The purpose|\.?\s*$)",
        text, re.IGNORECASE,
    )
    loc_str = loc_m.group(1).strip().rstrip(".,") if loc_m else None
    return time_str, loc_str


def scrape_and_apply_special_notices(boards_to_run: list, dom_alerts: list) -> None:
    """
    Fetch the Special Meeting Notices page and apply changes to per-board
    data/*.json files.

    Handles three notice types:
      cancelled       — sets isCancelled: True on the matching upcoming date
      location_change — updates location on the matching upcoming date
      special_meeting — adds or updates an entry in upcoming_meetings
    """
    print(f"\n{'='*60}\n  Special Meeting Notices\n{'='*60}")
    print(f"  Fetching {SPECIAL_NOTICES_URL}...")

    try:
        r = requests.get(SPECIAL_NOTICES_URL, timeout=30)
        r.raise_for_status()
    except Exception as exc:
        msg = f"Could not fetch Special Meeting Notices page: {exc}"
        print(f"  WARNING: {msg}")
        dom_alerts.append(msg)
        return

    html = r.text.replace("\u200b", "")

    notice_pattern = re.compile(
        r'<a\s[^>]*href="(?:https?://[^/]+)?(/Government/Boards-Commissions/Special-Meeting-Notices/[^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )

    notices_applied = 0

    for match in notice_pattern.finditer(html):
        raw_text = re.sub(r"<[^>]+>", " ", match.group(2))
        raw_text = re.sub(r"\s+", " ", raw_text).strip()
        if not raw_text or len(raw_text) < 10:
            continue

        boards = _detect_notice_boards(raw_text)
        dates  = _extract_notice_dates(raw_text)

        if not boards or not dates:
            continue

        text_lc = raw_text.lower()
        if "cancel" in text_lc:
            notice_type = "cancelled"
        elif "moved" in text_lc or "location change" in text_lc:
            notice_type = "location_change"
        else:
            notice_type = "special_meeting"

        for abbr in boards:
            board = next((b for b in BOARDS if b.get("abbr") == abbr), None)
            if not board or not board["output"].exists():
                continue

            with board["output"].open("r", encoding="utf-8") as f:
                data = json.load(f)

            upcoming = data.get("upcoming_meetings", [])
            changed  = False

            if notice_type == "cancelled":
                for date_iso in dates:
                    existing = next((m for m in upcoming if m["date"] == date_iso), None)
                    if existing:
                        if not existing.get("isCancelled"):
                            existing["isCancelled"] = True
                            changed = True
                            print(f"  CANCELLED: {abbr} {date_iso}")
                    else:
                        upcoming.append({
                            "date":        date_iso,
                            "display":     format_display_date_long(date_iso),
                            "time":        board.get("time", "TBD"),
                            "isCancelled": True,
                        })
                        upcoming.sort(key=lambda m: m["date"])
                        changed = True
                        print(f"  CANCELLED (added): {abbr} {date_iso}")

            elif notice_type == "location_change":
                new_loc = _extract_moved_location(raw_text)
                if new_loc:
                    for date_iso in dates:
                        existing = next((m for m in upcoming if m["date"] == date_iso), None)
                        if existing and existing.get("location") != new_loc:
                            existing["location"] = new_loc
                            existing["locationChanged"] = True
                            changed = True
                            print(f"  LOCATION CHANGE: {abbr} {date_iso} → {new_loc}")

            elif notice_type == "special_meeting":
                time_str, loc_str = _extract_special_meeting_info(raw_text)
                for date_iso in dates:
                    existing = next((m for m in upcoming if m["date"] == date_iso), None)
                    if existing:
                        if time_str and existing.get("time") != time_str:
                            existing["time"] = time_str
                            changed = True
                        if loc_str and existing.get("location") != loc_str:
                            existing["location"] = loc_str
                            changed = True
                        if not existing.get("isSpecial"):
                            existing["isSpecial"] = True
                            changed = True
                        if changed:
                            print(f"  SPECIAL (updated): {abbr} {date_iso}")
                    else:
                        new_entry: dict = {
                            "date":    date_iso,
                            "display": format_display_date_long(date_iso),
                            "time":    time_str or board.get("time", "TBD"),
                            "isSpecial": True,
                        }
                        if loc_str:
                            new_entry["location"] = loc_str
                        upcoming.append(new_entry)
                        upcoming.sort(key=lambda m: m["date"])
                        changed = True
                        print(f"  SPECIAL (added): {abbr} {date_iso}")

            if changed:
                data["upcoming_meetings"] = upcoming
                _write_output(board, data)
                notices_applied += 1

    print(f"  Applied {notices_applied} notice change(s) across boards.")

# ---------------------------------------------------------------------------
# Per-board runners
# ---------------------------------------------------------------------------
def run_web_docs_and_youtube_board(
    board: dict, start_iso: str, end_iso: str, api_key: str
) -> None:
    name = board["name"]
    print(f"\n{'='*60}\n  {name}\n{'='*60}")

    print("  Step 1: Scraping meeting documents from Minutes-Agendas page...")
    scraped_meetings = scrape_minutes_agendas_docs(board, start_iso, end_iso)

    print("  Step 2: Fetching YouTube recordings...")
    recordings = fetch_youtube_streams(api_key, board, start_iso, end_iso)
    print(f"    Found {len(recordings)} recordings in window")

    print("  Step 3: Merging...")
    existing = load_existing(board["output"])
    merged_meetings, stats = merge_meetings(existing.get("meetings", []), scraped_meetings)
    print(f"    added: {stats['added']}  updated: {stats['updated']}  unchanged: {stats['unchanged']}")
    merged_recordings = merge_recordings(existing.get("recordings", []), recordings)

    upcoming = compute_upcoming_schedule(board)
    print(f"  Upcoming: computed {len(upcoming)} dates from schedule rule")

    output = {
        "last_updated":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "metadata":          build_metadata(board),
        "upcoming_meetings": upcoming,
        "meetings":          merged_meetings,
        "recordings":        merged_recordings,
    }
    _write_output(board, output)
    print(f"  Wrote {board['output']}  ({len(merged_meetings)} meetings, {len(merged_recordings)} recordings)")


def run_youtube_only_board(
    board: dict, start_iso: str, end_iso: str, api_key: str
) -> None:
    name = board["name"]
    print(f"\n{'='*60}\n  {name}\n{'='*60}")
    print("  (meetings manually maintained — scraping YouTube only)")

    print("  Step 1: Fetching YouTube recordings...")
    recordings = fetch_youtube_streams(api_key, board, start_iso, end_iso)
    print(f"    Found {len(recordings)} recordings in window")

    print("  Step 2: Merging...")
    existing          = load_existing(board["output"])
    merged_recordings = merge_recordings(existing.get("recordings", []), recordings)

    if board.get("schedule"):
        upcoming = compute_upcoming_schedule(board)
        print(f"  Upcoming: computed {len(upcoming)} dates from schedule rule")
    else:
        upcoming = existing.get("upcoming_meetings", [])
        print(f"  Upcoming: preserved {len(upcoming)} dates from existing JSON")

    output = {
        "last_updated":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "metadata":          build_metadata(board),
        "upcoming_meetings": upcoming,
        "meetings":          existing.get("meetings", []),
        "recordings":        merged_recordings,
    }
    _write_output(board, output)
    preserved = len(existing.get("meetings", []))
    print(f"  Wrote {board['output']}  ({preserved} meetings preserved, {len(merged_recordings)} recordings)")


def run_web_scrape_board(board: dict, dom_alerts: list) -> None:
    name = board["name"]
    print(f"\n{'='*60}\n  {name}\n{'='*60}")

    url = board["web_url"]
    print(f"  Step 1: Fetching board page from city website...")
    print(f"    [Web] Fetching {url}...")
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        html = r.text
    except Exception as exc:
        print(f"    WARNING: Could not fetch {url}: {exc}")
        return

    upcoming     = scrape_web_upcoming(board, dom_alerts, html=html)
    past_scraped = scrape_web_past_meetings(board, html=html)

    print("  Step 2: Writing...")
    existing      = load_existing(board["output"])
    existing_meetings = existing.get("meetings", [])
    existing_dates    = {m.get("date") for m in existing_meetings}
    new_past          = [m for m in past_scraped if m["date"] not in existing_dates]
    if new_past:
        print(f"    Adding {len(new_past)} new past meeting(s) to archive")
    merged_meetings = sorted(
        existing_meetings + new_past,
        key=lambda m: m["date"],
        reverse=True,
    )

    output: dict = {
        "last_updated":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "metadata":          build_metadata(board),
        "upcoming_meetings": upcoming,
    }
    if merged_meetings:
        output["meetings"] = merged_meetings

    _write_output(board, output)
    print(f"  Wrote {board['output']}  ({len(upcoming)} upcoming, {len(merged_meetings)} meetings)")


def run_board(
    board: dict,
    start_iso: str,
    end_iso: str,
    api_key: str | None,
    dom_alerts: list,
) -> None:
    """Dispatch to the correct runner based on scraper_type."""
    scraper_type = board.get("scraper_type")

    if scraper_type == "web_docs_and_youtube":
        run_web_docs_and_youtube_board(board, start_iso, end_iso, api_key)
        return

    if scraper_type == "youtube_only":
        run_youtube_only_board(board, start_iso, end_iso, api_key)
        return

    if scraper_type == "web_scrape":
        run_web_scrape_board(board, dom_alerts)
        return

    # ---- Default: CivicClerk ------------------------------------------------
    name = board["name"]
    print(f"\n{'='*60}\n  {name}\n{'='*60}")

    now        = datetime.now(timezone.utc)
    future_iso = (now + timedelta(days=LOOKAHEAD_MONTHS * 30)).strftime("%Y-%m-%d")
    today_iso  = date.today().isoformat()

    print("  Step 1: Fetching CivicClerk events (past + upcoming)...")
    cc_url       = build_cc_url(start_iso, future_iso)
    all_events   = fetch_all_cc_events(cc_url)
    board_events = filter_board_events(all_events, board)

    past_events   = [e for e in board_events if e["startDateTime"].split("T")[0] <= today_iso]
    future_events = [e for e in board_events if e["startDateTime"].split("T")[0] >  today_iso]
    print(f"    Found {len(past_events)} past events, {len(future_events)} upcoming events")

    scraped = [m for m in (transform_event(e, board) for e in past_events) if m is not None]
    print(f"    {len(scraped)} past events with documents")

    # Upcoming strategy
    if board.get("upcoming_web_override_cc"):
        print("  Step 2: Checking web and CivicClerk for upcoming...")
        web_upcoming = scrape_web_upcoming(board, dom_alerts)
        if web_upcoming:
            print(f"    Website override active: {len(web_upcoming)} meetings found.")
            upcoming = web_upcoming
        else:
            upcoming = events_to_upcoming(future_events, board)
            print(f"    Website had 0. Fallback: {len(upcoming)} from CivicClerk.")
    elif board.get("upcoming_from_web"):
        print("  Step 2: Scraping upcoming meetings from city website...")
        upcoming = scrape_web_upcoming(board, dom_alerts)
    elif board.get("preserve_upcoming"):
        existing_check = load_existing(board["output"])
        upcoming = existing_check.get("upcoming_meetings", [])
        print(f"    Preserving {len(upcoming)} upcoming meetings from existing JSON")
    else:
        upcoming = events_to_upcoming(future_events, board)
        print(f"    {len(upcoming)} upcoming meetings on CivicClerk")

    # YouTube
    all_recs: list = []
    if board.get("youtube") and api_key:
        print("  Step 2: Fetching YouTube streams...")
        all_recs = fetch_youtube_streams(api_key, board, start_iso, end_iso)
        tolerance = board.get("youtube_tolerance", 3)
        for rec in all_recs:
            rec_date     = datetime.strptime(rec["date"], "%Y-%m-%d").date()
            best_meeting = None
            best_delta   = timedelta(days=tolerance + 1)
            for m in scraped:
                m_date = datetime.strptime(m["date"], "%Y-%m-%d").date()
                delta  = abs(rec_date - m_date)
                if delta <= timedelta(days=tolerance) and delta < best_delta:
                    best_delta   = delta
                    best_meeting = m
            if best_meeting and not best_meeting.get("youtube_id"):
                best_meeting["youtube_id"]  = rec["youtube_id"]
                best_meeting["youtube_url"] = rec["youtube_url"]

    print("  Step 3: Merging...")
    existing = load_existing(board["output"])
    merged_meetings, stats = merge_meetings(existing.get("meetings", []), scraped)
    print(f"    added: {stats['added']}  updated: {stats['updated']}  unchanged: {stats['unchanged']}")

    merged_recordings: list = []
    if board.get("youtube"):
        merged_recordings = merge_recordings(existing.get("recordings", []), all_recs)

    output: dict = {
        "last_updated":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "metadata":          build_metadata(board),
        "upcoming_meetings": upcoming,
        "meetings":          merged_meetings,
    }
    if board.get("youtube"):
        output["recordings"] = merged_recordings

    _write_output(board, output)
    print(f"  Wrote {board['output']}  ({len(merged_meetings)} meetings, {len(upcoming)} upcoming)")


def _write_output(board: dict, payload: dict) -> None:
    board["output"].parent.mkdir(parents=True, exist_ok=True)
    with board["output"].open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ---------------------------------------------------------------------------
# Watchdog suite
# ---------------------------------------------------------------------------

def load_state() -> dict:
    path = Path("data") / "state.json"
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(boards_run: list) -> None:
    state: dict = {
        "timestamp":     datetime.now(DETROIT_TZ).isoformat(),
        "totalMeetings": 0,
        "byBoard":       {},
    }
    for board in boards_run:
        if not board["output"].exists():
            continue
        with board["output"].open("r", encoding="utf-8") as f:
            data = json.load(f)
        n = len(data.get("meetings", []))
        state["byBoard"][board["key"]] = {"meetings": n}
        state["totalMeetings"] += n

    path = Path("data") / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  Wrote data/state.json  (total meetings: {state['totalMeetings']})")


def run_watchdog(boards_run: list, prev_state: dict, dom_alerts: list) -> None:
    """
    Three checks:
      1. Empty-board: any board goes from N→0 meetings (critical)
      2. Mass-deletion: >15% drop in total meeting count (critical)
      3. DOM alerts: structural change suspected during web scraping (warning)

    Critical failures email full detail and raise SystemExit so build.py
    never runs on potentially corrupted data.
    """
    if not prev_state:
        print("  Watchdog: no previous state snapshot — skipping comparison.")
        for alert in dom_alerts:
            print(f"  DOM ALERT: {alert}")
        if dom_alerts:
            send_alert_email(
                "[kalamazoo-boards] DOM Structure Alerts",
                "Possible DOM changes detected during web scraping:\n\n"
                + "\n".join(dom_alerts),
            )
        return

    print("\nRunning watchdog checks...")
    errors:  list[str] = []
    alerts:  list[str] = list(dom_alerts)  # dom alerts are warnings, not hard stops

    prev_by_board = prev_state.get("byBoard", {})

    # --- 1. Empty board check ------------------------------------------------
    for board in boards_run:
        key    = board["key"]
        prev_n = prev_by_board.get(key, {}).get("meetings", 0)
        if prev_n == 0:
            continue
        if not board["output"].exists():
            continue
        with board["output"].open("r", encoding="utf-8") as f:
            data = json.load(f)
        current_n = len(data.get("meetings", []))
        if current_n == 0:
            errors.append(
                f"EMPTY BOARD: {board['name']} ({key}) — was {prev_n}, now 0 meetings"
            )

    # --- 2. Mass-deletion tripwire (>15%) ------------------------------------
    prev_total = prev_state.get("totalMeetings", 0)
    if prev_total > 0:
        current_total = 0
        for board in boards_run:
            if board["output"].exists():
                with board["output"].open("r", encoding="utf-8") as f:
                    current_total += len(json.load(f).get("meetings", []))
        drop_pct = (prev_total - current_total) / prev_total
        if drop_pct > 0.15:
            errors.append(
                f"MASS DELETION: total meetings {prev_total} \u2192 {current_total} "
                f"({drop_pct:.1%} drop; threshold 15%)"
            )

    # --- Non-critical DOM alerts ---------------------------------------------
    if alerts:
        body = (
            "Non-critical watchdog alerts (no pipeline halt):\n\n"
            + "\n".join(alerts)
            + "\n\nReview the affected board pages for structural changes."
        )
        send_alert_email("[kalamazoo-boards] Watchdog Alerts", body)
        for a in alerts:
            print(f"  ALERT: {a}")

    # --- Critical errors: halt -----------------------------------------------
    if errors:
        body = (
            "CRITICAL: Scraper watchdog detected potential data corruption.\n\n"
            + "\n".join(errors)
            + "\n\nbuild.py was not run. Review data/*.json before the next deploy.\n"
            + "Roll back the affected file(s) if necessary."
        )
        send_alert_email("[kalamazoo-boards] CRITICAL Watchdog Failure", body)
        for e in errors:
            print(f"  CRITICAL: {e}")
        raise SystemExit(
            f"Watchdog halted — {len(errors)} critical error(s). "
            f"build.py will not run."
        )

    print(f"  Watchdog OK. {len(alerts)} alert(s), 0 critical errors.")


# ---------------------------------------------------------------------------
# Pipeline outputs
# ---------------------------------------------------------------------------

def write_meta_json() -> None:
    """Write data/meta.json with the current Detroit-timezone timestamp."""
    now  = datetime.now(DETROIT_TZ)
    meta = {"lastUpdated": now.isoformat()}
    path = Path("data") / "meta.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  Wrote data/meta.json  (lastUpdated: {now.isoformat()})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape all Kalamazoo boards.")
    parser.add_argument("--board", help="Run only this board key (e.g. crb, bba).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    now       = datetime.now(timezone.utc)
    start_iso = (now - timedelta(days=LOOKBACK_MONTHS * 30)).strftime("%Y-%m-%d")
    end_iso   = now.strftime("%Y-%m-%d")
    single_board = bool(args.board)

    print(
        f"Unified Scraper  |  lookback: {start_iso} \u2192 {end_iso}"
        f"  |  lookahead: +{LOOKAHEAD_MONTHS} months"
    )

    boards_to_run = BOARDS
    if single_board:
        boards_to_run = [b for b in BOARDS if b["key"] == args.board]
        if not boards_to_run:
            raise SystemExit(
                f"Unknown board key: {args.board}. "
                f"Available: {[b['key'] for b in BOARDS]}"
            )

    # Load previous state before scraping (for watchdog comparison)
    prev_state = load_state() if not single_board else {}

    # Refresh dynamic metadata from city website
    refresh_board_metadata(boards_to_run)

    needs_youtube = any(b.get("youtube") for b in boards_to_run)
    api_key       = get_youtube_key() if needs_youtube else None

    dom_alerts: list = []
    for board in boards_to_run:
        try:
            run_board(board, start_iso, end_iso, api_key, dom_alerts)
        except Exception:
            tb = traceback.format_exc()
            msg = f"Unhandled exception for board '{board['key']}':\n\n{tb}"
            print(f"\nERROR: {msg}")
            send_alert_email(
                f"[kalamazoo-boards] Scraper exception: {board['key']}",
                msg,
            )
            raise

    # Apply special meeting notices (cancellations, location changes, special meetings)
    scrape_and_apply_special_notices(boards_to_run, dom_alerts)

    # Watchdog + state snapshot (full runs only)
    if not single_board:
        run_watchdog(boards_to_run, prev_state, dom_alerts)
        save_state(boards_to_run)

    write_meta_json()
    print("\nDone. Run scripts/build.py to validate schemas and build calendar.json / ICS files.")


if __name__ == "__main__":
    main()
