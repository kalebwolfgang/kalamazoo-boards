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
import calendar as _cal
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

# How many past months to check the CITY CALENDAR against on a routine run.
# Deliberately a separate name from LOOKBACK_MONTHS above, which controls how
# far back documents and YouTube videos are searched and must stay at 6.
CALENDAR_LOOKBACK_MONTHS = 3
PRESERVE_IF_EMPTY       = ("agenda_url", "minutes_url", "youtube_id", "youtube_url", "scrapedAt")
DETROIT_TZ              = ZoneInfo("America/Detroit")

# Populated by retire_passed_meetings() call sites; reported by run_watchdog so
# a retirement is visible in the run output instead of happening silently.
RETIRED_MEETINGS: dict[str, list[str]] = {}


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
        "keywords":    ["civil rights"],
        "output":      Path("data") / "crb.json",
        "youtube":     True,
        "youtube_search_query": "Civil Rights Board",
        "youtube_title_filter": ["civil rights"],
        "youtube_tolerance":    3,
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Civil-Rights-Board",
    },
    {
        "key":          "bra",
        "name":         "Brownfield Redevelopment Authority Board",
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
        "youtube_title_filter":   ["public safety review", "cpsrab"],
        "youtube_tolerance":      3,
        "schedule":               ("monthly", "tuesday", 2, None),
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Citizens-Public-Safety-Review-and-Appeal-Board-CPSRAB",
    },
    {
        "key":         "dda",
        "name":        "Downtown Development Authority Board",
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
        "name":        "Downtown Economic Growth Authority Board",
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
        "name":         "Economic Development Corporation Board",
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
        "keywords":    ["environmental concerns"],
        "output":      Path("data") / "ecc.json",
        "youtube":     True,
        "youtube_search_query": "Environmental Concerns Committee",
        "youtube_title_filter": ["environmental concerns"],
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
        "youtube_title_filter": ["historic preservation", "historical preservation"],
        "youtube_tolerance":    3,
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Historic-Preservation-Commission",
    },
    {
        "key":         "locc",
        "name":        "Local Officers Compensation Commission",
        "abbr":        "LOCC",
        "time":        "On Call",
        "location":    "City Hall, 241 W South St",
        "meetingScheduleNote": "Meets biennially, typically in December of odd-numbered years",
        "category_id": 31,
        "keywords":    ["local officers compensation commission", "locc"],
        "output":      Path("data") / "locc.json",
        "youtube":     False,
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
        "youtube_title_filter": ["natural features"],
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
        "keywords":    ["zoning board"],
        "output":      Path("data") / "zba.json",
        "youtube":     True,
        "youtube_search_query": "Zoning Board of Appeals Kalamazoo",
        "youtube_title_filter": ["zoning board"],
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
        "web_url": f"{CITY_BASE_URL}/Government/Boards-Commissions/Employee-Retirement-System-Board-of-Trustees-Pension-Board",
    },
    {
        "key":             "prab",
        "name":            "Parks and Recreation Advisory Board",
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
        "name":        "Investment Committee of the Retirement System",
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
    "name":        "Shared Prosperity Kalamazoo Organizing Committee",
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

# Boards that always share meetings — changes to one automatically apply to the other
JOINT_BOARD_MAP: dict = {
    "BRA":  ["EDC"],
    "EDC":  ["BRA"],
    "DDA":  ["DEGA"],
    "DEGA": ["DDA"],
}


def _board_has_date(abbr: str, iso: str) -> bool:
    """True when a board already has a meeting recorded on this date."""
    board = next((b for b in BOARDS if b.get("abbr") == abbr), None)
    if not board or not board["output"].exists():
        return False
    try:
        with board["output"].open(encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    for field in ("upcoming_meetings", "meetings"):
        for record in data.get(field, []):
            if record.get("date") == iso:
                return True
    return False


def expand_to_joint_partners(boards: list[str], actions: list[dict]) -> list[str]:
    """Add a joint partner board only when the notice really covers it.

    A blanket copy put a DEGA-only special session onto DDA's calendar. The
    partner is now included only if the notice names it, or if the partner
    already has a meeting on one of the dates in question.
    """
    dates: list[str] = []
    for action in actions:
        if action["action"] == "rescheduled":
            dates.extend([action["old"], action["new"]])
        elif action.get("date"):
            dates.append(action["date"])

    out = list(boards)
    named = set(boards)

    for abbr in list(boards):
        for partner in JOINT_BOARD_MAP.get(abbr, []):
            if partner in named:
                continue
            shared = next((d for d in dates if _board_has_date(partner, d)), None)
            if shared:
                out.append(partner)
                named.add(partner)
                print(f"  JOINT: applying {abbr} notice to {partner} as well "
                      f"({partner} has a meeting on {shared})")
            else:
                print(f"  JOINT: NOT applying {abbr} notice to {partner}; "
                      f"{partner} has nothing scheduled on "
                      f"{', '.join(dates) or 'these dates'}")

    return out

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
# City OpenCities calendar API
#
# The city's public Meeting Calendar is driven by a JSON endpoint that returns
# every board's scheduled meetings in one request. This is a more reliable
# source for UPCOMING meetings than scraping each board page, because:
#   - it is structured data, not HTML that changes shape
#   - it covers all boards in one call
#   - it reflects reschedules the individual board pages can lag on
#
# It CANNOT report cancellations: the city deletes cancelled meetings from the
# calendar rather than flagging them. Cancellations still come from the
# Special Meeting Notices page.
#
# See city-calendar-guids.md for the full endpoint write-up.
# ---------------------------------------------------------------------------

CITY_CALENDAR_API = f"{CITY_BASE_URL}/ocapi/calendars/getcalendaritems"

# Maps the city's calendar GUID to the board key(s) it covers.
# Some GUIDs cover two boards that meet jointly, so the value is a list.
CALENDAR_GUID_TO_KEYS: dict = {
    "8a94186d-ac1c-45b1-85bc-daa5c52759b6": ["bor"],
    "8bff707f-08ca-47d9-90d3-8d79173a2da9": ["bra", "edc"],
    "5717d235-cb63-409c-98c5-a03cc931045f": ["bba"],
    "4f6c81c4-d50f-468d-a48e-94cbd0da5ee9": ["cpsrab"],
    "33a42050-0578-40da-a879-5c3c5437dc23": ["crb"],
    "1566d40b-f693-4502-925b-988cfa27c8e6": ["cdaac"],
    "242a06a0-01f8-46a5-9b14-900431eeaebc": ["dda", "dega"],
    "37fa9a05-02f4-497b-a7bc-ee573438f8ec": ["ec"],
    "5b0ed74d-906e-49fb-adad-723c1fc6bd37": ["ersb"],
    "2b9cfd53-08b3-4afc-b09e-5d5b4fbaf4f6": ["ecc"],
    "3aa2e0ce-10ac-4fca-bc5c-b413b64a1460": ["hdc"],
    "6dd970e4-2431-4f9a-a28f-f77cebf95915": ["hpc"],
    "1cc139a6-b9ac-4207-9f2e-ea61daace5f8": ["kmga"],
    "963f8951-271e-435d-94c2-2843156b01de": ["nfp"],
    "56cdfeb0-54b1-45eb-8102-dd7cb502791a": ["ncbda"],
    "b2efea5c-beaa-4ac8-bd5f-1afbcb39010f": ["prab"],
    "e82c3360-619a-49db-a77e-7bfbe0ef5fd3": ["pc"],
    "daba9346-1643-4321-b6e7-b7a5a316b3d1": ["ric"],
    "7932cef7-751c-4c20-8103-763d140e7824": ["spk"],
    "0fcb9329-3695-44dc-bf45-fbd335f4da06": ["trb"],
    "7117a554-117f-4e39-a6dd-812bb1806640": ["tre"],
    "2f814ffb-cd8c-4123-b93f-78f057d745ae": ["zba"],
}

# On the city calendar but not covered by this site. Kept for documentation
# and so a future decision to track them is a one-line change.
CALENDAR_GUID_UNTRACKED: dict = {
    "5f43d047-0e0b-46ad-9736-88db3ed55748": "City Commission",
    "eb7c079f-f92e-4d0a-a296-3002065bbcdf": "FFE Committees",
    "3523f276-1b74-488c-86de-e374e8e22ee7": "Utility Policy Committee",
    "4edfa16f-c9d4-4869-a60c-caa6e21ad7ed": "Water System Advisory Council",
}

# LOCC has no calendar GUID. The city does not publish it on the Meeting
# Calendar at all (its schedule is "On Call"), so the API cannot be a
# completeness check for that board. It stays on CivicClerk alone.
CALENDAR_UNCOVERED_KEYS = {"locc"}


def _month_windows(start: date, months: int) -> list[tuple]:
    """Yield (first_day, last_day) date pairs for `months` months from start."""
    windows = []
    y, m = start.year, start.month
    for _ in range(months):
        last_day = _cal.monthrange(y, m)[1]
        windows.append((date(y, m, 1), date(y, m, last_day)))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return windows


def _post_calendar(guids: list[str], start: date, end: date) -> dict | None:
    """
    POST one request to the city calendar API.
    Returns the parsed payload, or None on transport or API-level failure.
    """
    body = {
        "LanguageCode": "en-US",
        "Ids":          guids,
        "StartDate":    start.strftime("%Y-%m-%d"),
        "EndDate":      end.strftime("%Y-%m-%d"),
    }
    try:
        r = requests.post(CITY_CALENDAR_API, json=body, timeout=30)
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:
        print(f"    WARNING: calendar API request failed: {exc}")
        return None

    if not payload.get("success"):
        # The endpoint intermittently returns
        # "Index must be within the bounds of the List" for certain
        # board/date combinations. Caller retries in smaller batches.
        return None
    return payload


def fetch_city_calendar(months: int = LOOKAHEAD_MONTHS,
                        lookback: int = 0) -> dict:
    """
    Fetch upcoming meetings for every mapped board from the city calendar API.

    Queries month by month because the endpoint fails on wide date ranges.
    If a whole-month request fails, retries each board individually so one
    bad board does not cost the entire month.

    Returns a dict with:
      "meetings":     { board_key: { "YYYY-MM-DD": {"time": ..., "name": ...} } }
      "window_start": first date queried (ISO)
      "window_end":   last date queried (ISO)
      "failed":       list of requests that could not be retrieved

    Times are formatted to match the site's existing style (e.g. "5:30 PM").
    """
    print("\nFetching city calendar API...")
    all_guids = list(CALENDAR_GUID_TO_KEYS.keys())
    by_board: dict = {}
    first = date.today().replace(day=1)
    if lookback:
        year, month = first.year, first.month - lookback
        while month < 1:
            month += 12
            year -= 1
        first = date(year, month, 1)
    windows = _month_windows(first, months + lookback)
    failures = []

    for start, end in windows:
        payload = _post_calendar(all_guids, start, end)

        if payload is None:
            # Retry board by board to isolate the failure.
            print(f"    {start:%Y-%m}: batch failed, retrying per board...")
            payload = {"data": []}
            for guid in all_guids:
                single = _post_calendar([guid], start, end)
                if single is None:
                    failures.append(f"{start:%Y-%m} guid {guid}")
                    continue
                payload["data"].extend(single.get("data", []))

        for day in payload.get("data", []):
            for item in day.get("Items", []):
                guid = item.get("CalendarId")
                keys = CALENDAR_GUID_TO_KEYS.get(guid)
                if not keys:
                    continue
                dt_raw = item.get("DateTime", "")
                try:
                    dt = datetime.strptime(dt_raw, "%m/%d/%Y %I:%M:%S %p")
                except ValueError:
                    print(f"    WARNING: unparseable DateTime {dt_raw!r}")
                    continue
                iso = dt.strftime("%Y-%m-%d")
                # "5:30 PM" — strip the leading zero to match site formatting.
                time_str = dt.strftime("%I:%M %p").lstrip("0")
                for key in keys:
                    slot = by_board.setdefault(key, {})
                    # A board can meet twice in one day (e.g. City Commission
                    # 5:00 and 7:00). Keep the earliest as the headline time.
                    if iso not in slot or time_str < slot[iso]["time"]:
                        slot[iso] = {"time": time_str, "name": item.get("Name", "")}

    total = sum(len(v) for v in by_board.values())
    print(f"    Retrieved {total} meeting(s) across {len(by_board)} board(s)")
    if failures:
        print(f"    WARNING: {len(failures)} calendar request(s) failed")

    # The caller must know which window was actually queried. Anything outside
    # it cannot be judged present or absent, so it must not be flagged.
    window_start = windows[0][0].isoformat() if windows else None
    window_end   = windows[-1][1].isoformat() if windows else None
    return {
        "meetings":     by_board,
        "window_start": window_start,
        "window_end":   window_end,
        "failed":       failures,
    }


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
            # Drop map/skip links ENTIRELY before de-linking the rest.
            # The OpenCities template renders "[View Map](...)" inline on the
            # same line as the address whenever a board has one fixed venue.
            # De-linking first turned it into the bare words "View Map", and
            # the line filter below then discarded the whole line, address and
            # all. That silently returned None for the majority of boards, so
            # the hardcoded fallback was never actually refreshed.
            loc_raw = re.sub(r"\[\s*View Map\s*\]\([^)]*\)", "", loc_raw, flags=re.IGNORECASE)
            loc_raw = re.sub(r"\[\s*Skip to[^\]]*\]\([^)]*\)", "", loc_raw, flags=re.IGNORECASE)
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

                # Some boards have no single venue and the city writes prose
                # in this slot instead of an address ("Please see the detailed
                # schedule above for each meeting's location."). That is not a
                # location and must not be stored as one.
                prose_markers = (
                    "see the detailed schedule",
                    "see description",
                    "each meeting's location",
                    "varies",
                    "to be determined",
                )
                low = location.lower()
                if any(p in low for p in prose_markers):
                    result["location_is_prose"] = location
                else:
                    result["location"] = location

        return result

    except Exception as e:
        print(f"    WARNING: Could not scrape board info from {url}: {e}")
        return {}


def refresh_board_metadata(boards_to_run: list, alerts: list | None = None) -> dict:
    """
    Fetch current time and location from the city website for each board.
    Updates the board dict in place and returns a summary of what changed.

    Returns:
        dict mapping board key → {field: new_value} for all fields refreshed.

    skip_time:     boards whose configured time must never be overwritten
    skip_location: boards with dynamic per-meeting locations

    Any board whose location cannot be read from the city page is reported
    through `alerts`. Silence there previously meant the hardcoded fallback
    stayed in place indefinitely with no signal that it was never verified.
    """
    skip_location = {"prab", "kmga"}
    skip_time     = {"locc", "bor"}   # locc is intentional "On Call"; bor has multi-session days

    print("\nRefreshing board metadata from city website...")
    all_updates: dict = {}
    unverified: list = []

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

        if key not in skip_location:
            new_loc = info.get("location")
            old_loc = board.get("location")
            if new_loc:
                if old_loc and new_loc != old_loc:
                    msg = (f"LOCATION CHANGED: {board['abbr']} "
                           f"{old_loc!r} -> {new_loc!r}")
                    print(f"    {msg}")
                    if alerts is not None:
                        alerts.append(msg)
                board["location"] = new_loc
                board["locationVerifiedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                board_updates["location"] = new_loc
                print(f"    {key.upper()}: location \u2192 {new_loc}")
            elif info.get("location_is_prose"):
                # The city says the venue varies. Do not overwrite, but record
                # that we looked and the page genuinely has no single address.
                board["locationNote"] = info["location_is_prose"]
                print(f"    {key.upper()}: location varies per meeting "
                      f"({info['location_is_prose'][:60]})")
            else:
                unverified.append(f"{board['abbr']} ({url})")

        if board_updates:
            all_updates[key] = board_updates

    total = sum(len(v) for v in all_updates.values())
    print(f"    Done. {total} value(s) refreshed across {len(all_updates)} board(s).")

    if unverified:
        msg = ("Could not read a location from the city page for "
               f"{len(unverified)} board(s); the stored value is unverified:\n  "
               + "\n  ".join(unverified))
        print(f"    WARNING: {msg}")
        if alerts is not None:
            alerts.append(msg)

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

    # Previously: meetings with no agenda AND no minutes were discarded entirely.
    # That silently deleted real meetings the city held but never posted documents
    # for (e.g. Election Commission 2026-07-09). A meeting is a public record
    # whether or not a PDF exists, so it is kept and simply carries no doc links.

    source_url = (
        f"https://{CIVICCLERK_TENANT}.portal.civicclerk.com"
        f"/event/{event_id}/overview"
    )

    if minutes_file_id and agenda_file_id:
        link_label = "Agenda & Minutes"
    elif minutes_file_id:
        link_label = "Minutes"
    elif agenda_file_id:
        link_label = "Agenda"
    else:
        link_label = "Meeting Record"

    record = {
        "date":        date_only,
        "display":     format_display_date(date_only),
        "event_id":    event_id,
        "url":         build_doc_url(event_id, agenda_file_id) if agenda_file_id else source_url,
        "link_label":  link_label,
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
# Schedule-generated upcoming: retirement and merging
#
# compute_upcoming_schedule() regenerates the upcoming list from a recurrence
# rule on every run, keeping only dates >= today. Anything stored on the
# previous run for a date that has since passed used to be simply discarded,
# and a stored date the rule did not reproduce was dropped as well.
#
# That lost meetings. A meeting with no agenda and no minutes was archived by
# nothing, so it vanished the day it passed: eighteen of them across eleven
# boards. A special meeting added from a notice survived exactly one run,
# because it is not part of the board's regular schedule.
#
# The rule now is that a meeting is never deleted. retire_passed_meetings()
# moves every passed date into the archive before the regenerated list
# replaces the stored one. merge_upcoming() keeps stored future dates and
# their flags, whether or not the recurrence rule knows about them.
# ---------------------------------------------------------------------------

def retire_passed_meetings(
    board: dict,
    existing_upcoming: list[dict],
    archive: list[dict],
) -> tuple[list[dict], list[str]]:
    """Move every upcoming meeting whose date has passed into the archive.

    Not only cancelled ones. A meeting the city never posted documents for is
    still a meeting that was scheduled, and the record of it belongs in the
    archive rather than nowhere.

    If the archive already holds that date the existing record is updated
    rather than a second one added, because a board can have both a
    CivicClerk event and a schedule-generated entry for the same day.

    Returns the modified archive and the list of dates retired.
    """
    today_iso = date.today().strftime("%Y-%m-%d")
    retired: list[str] = []

    by_date = {m.get("date"): m for m in archive}

    for entry in existing_upcoming or []:
        d = entry.get("date")
        if not d or d >= today_iso:
            continue

        existing_rec = by_date.get(d)
        if existing_rec is not None:
            # Already archived. Carry over anything the archive is missing.
            changed = False
            if entry.get("isCancelled") and not existing_rec.get("isCancelled"):
                existing_rec["isCancelled"] = True
                changed = True
            if entry.get("location") and not existing_rec.get("location"):
                existing_rec["location"] = entry["location"]
                changed = True
            if changed:
                retired.append(d)
            continue

        record = {
            "date":        d,
            "display":     entry.get("display") or format_display_date(d),
            "minutes_url": None,
            "agenda_url":  None,
            "location":    entry.get("location"),
            "scrapedAt":   datetime.now(timezone.utc).isoformat(),
            "sourceUrl":   board.get("web_url"),
        }
        # Keep the reschedule note. Without it, the date this meeting moved
        # away from looks like a missing meeting once both dates are past,
        # and the calendar backfill puts it back.
        if entry.get("rescheduledFrom"):
            record["rescheduledFrom"] = entry["rescheduledFrom"]
        if entry.get("isCancelled"):
            record["isCancelled"] = True
            record["link_label"]  = "Cancelled"
        else:
            record["link_label"] = "No documents posted"
        # An archive record must not carry event_id or url: the schema types
        # event_id as an integer and forbids unknown properties, so a null
        # would fail validation and stop the build.

        archive.append(record)
        by_date[d] = record
        retired.append(d)

    return archive, retired


def merge_upcoming(
    board: dict,
    generated: list[dict],
    existing_upcoming: list[dict],
) -> list[dict]:
    """Combine a freshly generated upcoming list with what was already stored.

    Replaces preserve_flagged_upcoming, which only copied flags onto dates the
    generator happened to produce again. Anything the generator did not know
    about was dropped, so a special meeting added by a notice survived exactly
    one run.

    Three rules:
      1. A stored date the generator repeats keeps its stored version, so
         cancellations, locations and reschedule notes stay put.
      2. A stored future date the generator does not know about is kept
         anyway. It was on the site, so it stays on the site.
      3. A date another meeting was rescheduled away from is dropped. The
         meeting moved, and a recurrence rule would otherwise put the old
         date back every run.
    """
    today_iso = date.today().strftime("%Y-%m-%d")

    stored_by_date = {
        e.get("date"): e for e in (existing_upcoming or []) if e.get("date")
    }

    out: list[dict] = []
    seen: set[str] = set()

    for entry in generated:
        d = entry.get("date")
        if not d or d in seen:
            continue
        out.append(stored_by_date.get(d, entry))
        seen.add(d)

    for d, entry in stored_by_date.items():
        # Past dates are handled by retire_passed_meetings, which runs first.
        if d < today_iso or d in seen:
            continue
        out.append(entry)
        seen.add(d)

    vacated = {
        e.get("rescheduledFrom") for e in out if e.get("rescheduledFrom")
    }
    if vacated:
        out = [e for e in out if e.get("date") not in vacated]

    return sorted(out, key=lambda m: m.get("date", ""))


def merge_meetings(existing: list, scraped: list) -> tuple:
    """Merge scraped meetings into the archive.

    Only the lookup changed. A record archived without documents is keyed by
    its date, and the same meeting arrives later keyed by its CivicClerk
    event_id once the city posts an agenda. Matching on event_id alone created
    a second record for the same day, so a date match is now accepted as a
    fallback and the event_id is written onto the record that already exists.
    """
    stats = {"added": 0, "updated": 0, "unchanged": 0, "preserved": 0}

    def key(m):
        return ("id", m["event_id"]) if m.get("event_id") is not None else ("date", m.get("date"))

    by_key = {key(m): m for m in existing}

    for s in scraped:
        k = key(s)

        if k not in by_key and k[0] == "id":
            # Same day, already archived without an event_id. Adopt it rather
            # than adding a duplicate row for the same meeting.
            date_key = ("date", s.get("date"))
            if date_key in by_key:
                existing_rec = by_key.pop(date_key)
                existing_rec["event_id"] = s["event_id"]
                # A placeholder label no longer applies once documents exist.
                if existing_rec.get("link_label") == "No documents posted":
                    existing_rec.pop("link_label", None)
                by_key[k] = existing_rec

        if k not in by_key:
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



# ---------------------------------------------------------------------------
# Notice text normalisation
#
# The city's OpenCities editor pastes zero-width and non-breaking characters
# between words. They are invisible on the page but sit inside the string and
# break any pattern that expects a plain space.
# ---------------------------------------------------------------------------

_INVISIBLE = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0xFEFF, 0x00AD], None
)

def _normalize_notice_text(text: str) -> str:
    """Strip invisible characters and normalise spacing and punctuation."""
    text = text.translate(_INVISIBLE)
    text = text.replace("\u00a0", " ")          # non-breaking space
    text = text.replace("\u2013", "-").replace("\u2014", "-")  # en/em dash
    text = re.sub(r"\s+", " ", text)
    return text.strip()


_MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2, "febr": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

# "August 25, 2026" / "August 25th, 2026" / "Aug. 25 2026" / "September 2"
_TEXT_DATE = re.compile(
    r"\b(" + _MONTH_ALT + r")\.?\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?(?![\d:])"
    r"(?:\s*,?\s*(\d{4}))?\b",
    re.IGNORECASE,
)

# "September, 8, 2026" - a comma between month and day. Only accepted when a
# full year follows, so that "in August, 20 people attended" is not a date.
_TEXT_DATE_COMMA = re.compile(
    r"\b(" + _MONTH_ALT + r")\.?\s*,\s*"
    r"(\d{1,2})(?:st|nd|rd|th)?(?![\d:])\s*,?\s*(\d{4})\b",
    re.IGNORECASE,
)

# "2026-09-15"
_ISO = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")

# "25 August 2026" / "25th of August, 2026"
_DAY_FIRST = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?(?![\d:])\s+(?:of\s+)?(" + _MONTH_ALT + r")\.?"
    r"(?:\s*,?\s*(\d{4}))?\b",
    re.IGNORECASE,
)

# "8/25/2026" / "08-25-26"
_NUMERIC = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})\b")


def _resolve_year(month: int, day: int, today: date) -> int | None:
    """Pick the most plausible year for a date written without one.

    A notice is about something close to now, so choose the year that puts the
    date nearest today, looking one year back and one year forward.
    """
    best, best_gap = None, None
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        gap = abs((candidate - today).days)
        if best_gap is None or gap < best_gap:
            best, best_gap = year, gap
    return best


def _extract_notice_dates(text: str, today: date | None = None) -> list[str]:
    """Extract every date in a notice, in the order written, as ISO strings.

    Handles the spellings the city actually uses, plus the ones it has not
    used yet: ordinal suffixes, abbreviated months, day-first order, slash
    and dash numerics, missing years, and stray punctuation.
    """
    today = today or date.today()
    text = _normalize_notice_text(text)

    found: list[tuple[int, str]] = []   # (position in text, ISO date)

    def add(pos: int, year: int | None, month: int, day: int) -> None:
        if year is None:
            year = _resolve_year(month, day, today)
        if year is None:
            return
        if year < 100:
            year += 2000
        try:
            iso = date(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return
        found.append((pos, iso))

    for m in _TEXT_DATE.finditer(text):
        month = _MONTHS[m.group(1).lower()]
        add(m.start(), int(m.group(3)) if m.group(3) else None, month, int(m.group(2)))

    for m in _TEXT_DATE_COMMA.finditer(text):
        add(m.start(), int(m.group(3)),
            _MONTHS[m.group(1).lower()], int(m.group(2)))

    for m in _ISO.finditer(text):
        add(m.start(), int(m.group(1)), int(m.group(2)), int(m.group(3)))

    for m in _DAY_FIRST.finditer(text):
        month = _MONTHS[m.group(2).lower()]
        add(m.start(), int(m.group(3)) if m.group(3) else None, month, int(m.group(1)))

    for m in _NUMERIC.finditer(text):
        add(m.start(), int(m.group(3)), int(m.group(1)), int(m.group(2)))

    # Keep document order, drop duplicates. Callers rely on the order:
    # a reschedule notice writes the old date before the new one.
    found.sort(key=lambda pair: pair[0])
    results, seen = [], set()
    for _, iso in found:
        if iso not in seen:
            results.append(iso)
            seen.add(iso)
    return results


# ---------------------------------------------------------------------------
# Which board is the notice about?
# ---------------------------------------------------------------------------

# The sentence that names the meeting's owner. The city writes one of:
#   "The meeting of the <BOARD> scheduled for ..."
#   "The <BOARD> will meet in special session ..."
#   "The <BOARD> has been cancelled ..."
_SUBJECT_PATTERNS = [
    r"meeting of the\s+(.{3,90}?)\s+(?:scheduled|will|has|is)\b",
    r"\bThe\s+(.{3,90}?)\s+will meet\b",
    r"\bThe\s+(.{3,90}?)\s+(?:meeting )?has been\b",
]


# Every abbreviation the site uses, longest first so that a longer one is
# never swallowed by a shorter one sitting inside it.
_KNOWN_ABBRS = sorted(
    {abbr for _, abbr in NOTICE_BOARD_MAP} | {b["abbr"] for b in BOARDS},
    key=len, reverse=True,
)


def _match_boards(text: str) -> list[str]:
    """Every board name appearing in a string, most specific first."""
    lowered = text.lower()
    found, seen = [], set()
    for fragment, abbr in NOTICE_BOARD_MAP:
        if fragment in lowered and abbr not in seen:
            found.append(abbr)
            seen.add(abbr)
    return found


def _match_acronyms(text: str) -> list[str]:
    """Board abbreviations written on their own, e.g. "the CPSRAB meeting".

    Only used when no board name was spelled out. Matching is case sensitive
    and whole-word, so an abbreviation cannot be found inside another word.
    """
    found, seen = [], set()
    for abbr in _KNOWN_ABBRS:
        if abbr in seen:
            continue
        if re.search(r"\b" + re.escape(abbr) + r"\b", text):
            found.append(abbr)
            seen.add(abbr)
    return found


def _boards_in(text: str) -> list[str]:
    """Board names in a string, falling back to abbreviations."""
    return _match_boards(text) or _match_acronyms(text)


def _detect_notice_boards(text: str, warnings: list | None = None) -> list[str]:
    """Return the board(s) a notice is about.

    The heading and the body do not always agree. The city has published a
    notice headed "Historic Preservation Commission" whose body says
    "Historic District Commission". Scanning the whole notice matched both and
    wrote the meeting onto two boards, one of which never had it.

    So the sentence that describes the meeting wins. Only if no board can be
    read from that sentence does this fall back to scanning everything.
    """
    text = _normalize_notice_text(text)
    everywhere = _boards_in(text)

    for pattern in _SUBJECT_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        subject = _boards_in(m.group(1))
        if len(subject) == 1:
            other = [a for a in everywhere if a not in subject]
            if other and warnings is not None:
                warnings.append(
                    f"Notice names {'/'.join(other)} elsewhere in the text but "
                    f"describes a {subject[0]} meeting. Using {subject[0]}. "
                    f"Notice text: {text[:120]}"
                )
            return subject
        if len(subject) > 1:
            # Two boards genuinely named in the same sentence, e.g. a joint
            # meeting. Keep both.
            return subject

    return everywhere


# ---------------------------------------------------------------------------
# What is the notice announcing?
# ---------------------------------------------------------------------------

# Each phrase is tied to what it does to a meeting. Distance from the date
# decides which one applies, so a stray word later in the notice cannot
# override the phrase sitting next to the date.
_ACTION_PHRASES = [
    ("cancelled",       r"\bcancel\w*\b"),
    ("cancelled",       r"\bwill not (?:be held|meet|take place)\b"),
    ("special",         r"\bspecial (?:session|meeting)\b"),
    ("special",         r"\bwill meet\b"),
    ("special",         r"\bwill hold\b"),
    ("location_change", r"\blocation (?:change|has changed)\b"),
    ("location_change", r"\bmoved to meet (?:in|at)\b"),
    ("location_change", r"\bnew location\b"),
]

_RESCHEDULE_MARKER = re.compile(
    r"\breschedul\w*(?:\s+to\s+meet)?|\bmoved to meet on\b|\bwill now meet on\b"
    r"|\bpostponed (?:to|until)\b|\bchanged to\b",
    re.IGNORECASE,
)

def _dates_with_positions(text: str, today: date | None = None) -> list[tuple[int, str]]:
    """Every date in the notice with where it sits in the text."""
    today = today or date.today()
    found: list[tuple[int, str]] = []

    def add(pos, year, month, day):
        if year is None:
            year = _resolve_year(month, day, today)
        if year is None:
            return
        if year < 100:
            year += 2000
        try:
            found.append((pos, date(year, month, day).strftime("%Y-%m-%d")))
        except ValueError:
            return

    for m in _TEXT_DATE.finditer(text):
        add(m.start(), int(m.group(3)) if m.group(3) else None,
            _MONTHS[m.group(1).lower()], int(m.group(2)))
    for m in _TEXT_DATE_COMMA.finditer(text):
        add(m.start(), int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
    for m in _ISO.finditer(text):
        add(m.start(), int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for m in _DAY_FIRST.finditer(text):
        add(m.start(), int(m.group(3)) if m.group(3) else None,
            _MONTHS[m.group(2).lower()], int(m.group(1)))
    for m in _NUMERIC.finditer(text):
        add(m.start(), int(m.group(3)), int(m.group(1)), int(m.group(2)))

    found.sort(key=lambda pair: pair[0])
    out, seen = [], set()
    for pos, iso in found:
        if iso not in seen:
            out.append((pos, iso))
            seen.add(iso)
    return out



# ---------------------------------------------------------------------------
# Sentence splitting
#
# An action only applies to a date sitting in the same sentence. Without that
# rule, "...has been CANCELLED. The board last met on July 14" reads the old
# meeting as cancelled too. Street and time abbreviations carry full stops of
# their own, so they are hidden before splitting and restored afterwards.
# ---------------------------------------------------------------------------

_ABBREVIATIONS = [
    "a.m.", "p.m.", "A.M.", "P.M.",
    "St.", "Ave.", "Rd.", "Blvd.", "Dr.", "Ct.", "Ln.", "Pl.", "Ste.",
    "Mr.", "Mrs.", "Ms.", "Dr.", "No.", "Inc.", "Jr.", "Sr.", "Corp.",
    "Jan.", "Feb.", "Mar.", "Apr.", "Jun.", "Jul.", "Aug.",
    "Sep.", "Sept.", "Oct.", "Nov.", "Dec.",
]

_SINGLE_LETTER_ABBREV = re.compile(r"\b([A-Z])\.")
_PLACEHOLDER = "\x00"


def _split_sentences(text: str) -> list[tuple[int, str]]:
    """Split a notice into sentences, returned as (start position, text)."""
    masked = text
    for abbr in _ABBREVIATIONS:
        masked = masked.replace(abbr, abbr.replace(".", _PLACEHOLDER))
    masked = _SINGLE_LETTER_ABBREV.sub(r"\1" + _PLACEHOLDER, masked)

    # An abbreviation can also end a sentence: "at 5:00 p.m. The purpose ...".
    # Put the full stop back when the next word starts a new sentence.
    masked = re.sub(
        _PLACEHOLDER + r"(\s+(?:The|This|That|Please|All|Questions|Members|An|A|In|"
        r"If|Notice|For|Any|Public|Meeting|Purpose|Anyone|Persons|Board|Its)\b)",
        r".\1", masked,
    )

    sentences, start = [], 0
    for m in re.finditer(r"[.!?]+\s+", masked):
        end = m.end()
        sentences.append((start, text[start:end]))
        start = end
    if start < len(text):
        sentences.append((start, text[start:]))
    return sentences


def _sentence_at(sentences: list[tuple[int, str]], pos: int) -> tuple[int, str]:
    """The sentence containing a position."""
    current = sentences[0] if sentences else (0, "")
    for start, body in sentences:
        if start <= pos:
            current = (start, body)
        else:
            break
    return current


def _nearest_action(sentences: list[tuple[int, str]], pos: int) -> str | None:
    """The action phrase that applies to a date.

    Only phrases in the same sentence as the date count, and of those the
    closest one wins.
    """
    start, body = _sentence_at(sentences, pos)
    local = pos - start
    best, best_gap = None, None
    for action, pattern in _ACTION_PHRASES:
        for m in re.finditer(pattern, body, re.IGNORECASE):
            gap = 0 if m.start() <= local <= m.end() else min(
                abs(m.start() - local), abs(m.end() - local)
            )
            if best_gap is None or gap < best_gap:
                best, best_gap = action, gap
    return best


def _extract_time(text: str) -> str | None:
    m = re.search(r"\bat\s+(\d{1,2}(?::\d{2})?\s*[aApP]\.?[mM]\.?)", text)
    if not m:
        return None
    t = m.group(1).strip().rstrip(".")
    t = re.sub(r"a\.?m\.?", "AM", t, flags=re.IGNORECASE)
    t = re.sub(r"p\.?m\.?", "PM", t, flags=re.IGNORECASE)
    return t


# A location runs to the end of the sentence. Street abbreviations such as
# "241 W. South Street" contain full stops of their own, so a sentence only
# ends where a full stop is followed by a word that starts a new one.
_SENTENCE_END = (
    r"(?=\.\s+(?:The|This|That|Please|All|Questions|Members|An|A|In|If|Notice|"
    r"For|Any|Public|Meeting|Purpose)\b|\.\s*$|\Z)"
)


def _extract_location(text: str) -> str | None:
    patterns = [
        r"(?:take place|be held)\s+(?:at|in)\s+(?:the\s+)?(.+?)" + _SENTENCE_END,
        r"moved to meet (?:in|at)\s+(?:the\s+)?(.+?)" + _SENTENCE_END,
        r"reschedul\w*\s+to\s+meet\s+on\s+.*?\bin\s+(?:the\s+)?(.+?)" + _SENTENCE_END,
        r"reschedul\w*\s+to\s+meet\s+(?:in|at)\s+(?:the\s+)?(.+?)" + _SENTENCE_END,
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            loc = m.group(1).strip().rstrip(".,")
            loc = re.sub(r"^the\s+", "", loc, flags=re.IGNORECASE)
            if loc and len(loc) < 200:
                return loc
    return None


def parse_notice(text: str, today: date | None = None,
                 warnings: list | None = None) -> dict:
    """Read one notice into the list of changes it asks for.

    A notice can say more than one thing. The September 2 special meeting
    notice also records that the August 18 meeting was cancelled, and both
    facts belong on the site.

    Returns {"boards": [...], "actions": [...]} where each action is one of:
      {"action": "rescheduled", "old": iso, "new": iso, "time":, "location":}
      {"action": "cancelled", "date": iso}
      {"action": "special", "date": iso, "time":, "location":}
      {"action": "location_change", "date": iso, "location":}
    """
    today = today or date.today()
    text = _normalize_notice_text(text)

    boards = _detect_notice_boards(text, warnings)
    dated = _dates_with_positions(text, today)
    sentences = _split_sentences(text)
    actions: list[dict] = []
    used: set[str] = set()

    time_str = _extract_time(text)
    location = _extract_location(text)

    # A reschedule is the one action that ties two dates together, so it is
    # resolved first and its two dates are taken out of play.
    marker_seen = bool(_RESCHEDULE_MARKER.search(text))

    # The heading repeats the word "Rescheduled" before either date is
    # written, so take the marker that has a date on both sides of it rather
    # than the first one in the text.
    for marker in _RESCHEDULE_MARKER.finditer(text):
        before = [iso for pos, iso in dated if pos < marker.start()]
        after = [iso for pos, iso in dated if pos > marker.end()]
        if before and after and before[-1] != after[0]:
            actions.append({
                "action": "rescheduled",
                "old": before[-1],
                "new": after[0],
                "time": time_str,
                "location": location,
            })
            used.update({before[-1], after[0]})
            break
        if not before and len(after) >= 2:
            # "will now meet on <new> instead of <old>" puts both dates after
            # the marker, in the opposite order.
            swap = re.search(r"instead of|rather than|previously scheduled (?:for|on)",
                             text, re.IGNORECASE)
            if swap:
                old = next((iso for pos, iso in dated if pos > swap.end()), None)
                new = next((iso for pos, iso in dated
                            if pos > marker.end() and iso != old), None)
                if old and new and old != new:
                    actions.append({
                        "action": "rescheduled", "old": old, "new": new,
                        "time": time_str, "location": location,
                    })
                    used.update({old, new})
                    break

    for pos, iso in dated:
        if iso in used:
            continue
        action = _nearest_action(sentences, pos)
        if action == "cancelled":
            actions.append({"action": "cancelled", "date": iso})
        elif action == "special":
            actions.append({
                "action": "special", "date": iso,
                "time": time_str, "location": location,
            })
        elif action == "location_change" and location:
            actions.append({
                "action": "location_change", "date": iso, "location": location,
            })
        elif marker_seen and location:
            actions.append({
                "action": "location_change", "date": iso, "location": location,
            })
        else:
            if warnings is not None:
                warnings.append(
                    f"Date {iso} in a notice with no clear action nearby; "
                    f"ignored. Notice text: {text[:120]}"
                )
        used.add(iso)

    return {"boards": boards, "actions": actions}


# ---------------------------------------------------------------------------
# Applying a notice to the stored data
# ---------------------------------------------------------------------------

def _find(records: list, iso: str) -> dict | None:
    return next((m for m in records if m.get("date") == iso), None)


def _record_cancellation(data: dict, iso: str, board: dict) -> bool:
    """Mark a meeting cancelled, wherever it lives, adding it if missing.

    A cancellation can name a date that has already passed. Those belong in
    the archive, not in the upcoming list, and they are never dropped: the
    city told us the meeting did not happen and that is worth keeping.
    """
    upcoming = data.setdefault("upcoming_meetings", [])
    archive  = data.setdefault("meetings", [])

    existing = _find(upcoming, iso) or _find(archive, iso)
    if existing:
        if existing.get("isCancelled"):
            return False
        existing["isCancelled"] = True
        return True

    if iso >= date.today().strftime("%Y-%m-%d"):
        upcoming.append({
            "date":        iso,
            "display":     format_display_date_long(iso),
            "time":        board.get("time", "TBD"),
            "isCancelled": True,
        })
        upcoming.sort(key=lambda m: m["date"])
    else:
        # Archive records must not carry event_id or url: the schema types
        # event_id as an integer and forbids unknown properties, so a null
        # would fail validation and halt the build.
        archive.append({
            "date":        iso,
            "display":     format_display_date_long(iso),
            "isCancelled": True,
        })
        archive.sort(key=lambda m: m["date"], reverse=True)
    return True


def _record_special(data: dict, iso: str, board: dict,
                    time_str: str | None, location: str | None) -> bool:
    upcoming = data.setdefault("upcoming_meetings", [])
    archive  = data.setdefault("meetings", [])
    changed  = False

    existing = _find(upcoming, iso)
    if existing:
        if time_str and existing.get("time") != time_str:
            existing["time"] = time_str
            changed = True
        if location and existing.get("location") != location:
            existing["location"] = location
            changed = True
        if not existing.get("isSpecial"):
            existing["isSpecial"] = True
            changed = True
        return changed

    if _find(archive, iso):
        return False        # already happened and already recorded

    if iso >= date.today().strftime("%Y-%m-%d"):
        entry = {
            "date":      iso,
            "display":   format_display_date_long(iso),
            "time":      time_str or board.get("time", "TBD"),
            "isSpecial": True,
        }
        if location:
            entry["location"] = location
        upcoming.append(entry)
        upcoming.sort(key=lambda m: m["date"])
    else:
        entry = {"date": iso, "display": format_display_date_long(iso)}
        if location:
            entry["location"] = location
        archive.append(entry)
        archive.sort(key=lambda m: m["date"], reverse=True)
    return True


def _record_reschedule(data: dict, old_iso: str, new_iso: str, board: dict,
                       time_str: str | None, location: str | None) -> bool:
    """Move a meeting to a new date.

    A reschedule is not a cancellation. The meeting is still happening, so the
    old date comes off the calendar and the new date carries a note saying
    where it moved from.
    """
    upcoming = data.setdefault("upcoming_meetings", [])
    changed  = False

    before = len(upcoming)
    upcoming[:] = [m for m in upcoming if m.get("date") != old_iso]
    if len(upcoming) != before:
        changed = True

    entry = _find(upcoming, new_iso)
    if entry:
        if time_str and entry.get("time") != time_str:
            entry["time"] = time_str
            changed = True
        if location and entry.get("location") != location:
            entry["location"] = location
            changed = True
        if entry.get("rescheduledFrom") != old_iso:
            entry["rescheduledFrom"] = old_iso
            changed = True
        # It is the board's regular meeting on a new date, not an extra
        # session, and its absence from the old date is explained now.
        if entry.pop("isSpecial", None) is not None:
            changed = True
        if entry.pop("notOnCityCalendar", None) is not None:
            changed = True
    else:
        entry = {
            "date":            new_iso,
            "display":         format_display_date_long(new_iso),
            "time":            time_str or board.get("time", "TBD"),
            "rescheduledFrom": old_iso,
        }
        if location:
            entry["location"] = location
        upcoming.append(entry)
        changed = True

    upcoming.sort(key=lambda m: m["date"])
    return changed


def _record_location_change(data: dict, iso: str, location: str) -> bool:
    entry = _find(data.setdefault("upcoming_meetings", []), iso)
    if not entry or entry.get("location") == location:
        return False
    entry["location"] = location
    entry["locationChanged"] = True
    return True


def apply_notice_actions(board: dict, parsed: dict) -> int:
    """Write one notice's actions onto one board's data file."""
    if not board["output"].exists():
        return 0

    with board["output"].open("r", encoding="utf-8") as f:
        data = json.load(f)

    abbr = board.get("abbr")
    changed = False

    for action in parsed["actions"]:
        kind = action["action"]
        if kind == "rescheduled":
            if _record_reschedule(data, action["old"], action["new"], board,
                                  action.get("time"), action.get("location")):
                changed = True
                print(f"  RESCHEDULED: {abbr} {action['old']} -> {action['new']}")
        elif kind == "cancelled":
            if _record_cancellation(data, action["date"], board):
                changed = True
                print(f"  CANCELLED: {abbr} {action['date']}")
        elif kind == "special":
            if _record_special(data, action["date"], board,
                               action.get("time"), action.get("location")):
                changed = True
                print(f"  SPECIAL: {abbr} {action['date']}")
        elif kind == "location_change":
            if _record_location_change(data, action["date"], action["location"]):
                changed = True
                print(f"  LOCATION CHANGE: {abbr} {action['date']} -> {action['location']}")

    if changed:
        _write_output(board, data)
        return 1
    return 0


# ---------------------------------------------------------------------------
# Notice log
#
# The city deletes a notice once it is no longer current, so the wording is
# gone the moment it stops being visible. Keeping a copy means the exact text
# can still be read months later, and a notice that quietly disappears can be
# spotted.
# ---------------------------------------------------------------------------

NOTICE_LOG_PATH = Path("data/notices.json")


def record_notice_log(entries: list[dict], dom_alerts: list) -> None:
    """Save every notice seen this run, keeping ones that have come down."""
    today = date.today().strftime("%Y-%m-%d")

    stored: dict = {}
    if NOTICE_LOG_PATH.exists():
        try:
            with NOTICE_LOG_PATH.open(encoding="utf-8") as f:
                for item in json.load(f).get("notices", []):
                    stored[item["url"]] = item
        except Exception as exc:
            print(f"  WARNING: could not read {NOTICE_LOG_PATH}: {exc}")

    seen_now = set()
    for entry in entries:
        seen_now.add(entry["url"])
        existing = stored.get(entry["url"])
        if existing:
            existing["last_seen"] = today
            existing["stillPosted"] = True
            if existing.get("text") != entry["text"]:
                # The city edited a notice after posting it. Keep both.
                existing.setdefault("previousText", []).append(existing["text"])
                existing["text"] = entry["text"]
                msg = f"Notice text was edited by the city: {entry['url']}"
                print(f"  NOTE: {msg}")
                dom_alerts.append(msg)
            existing["boards"] = entry["boards"]
            existing["actions"] = entry["actions"]
        else:
            stored[entry["url"]] = {
                "url":          entry["url"],
                "first_seen":   today,
                "last_seen":    today,
                "stillPosted":  True,
                "text":         entry["text"],
                "boards":       entry["boards"],
                "actions":      entry["actions"],
            }

    for url, item in stored.items():
        if url not in seen_now and item.get("stillPosted"):
            item["stillPosted"] = False
            print(f"  NOTE: notice no longer posted: {url}")

    NOTICE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_updated": today,
        "notices": sorted(stored.values(), key=lambda i: i["first_seen"], reverse=True),
    }
    with NOTICE_LOG_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def scrape_and_apply_special_notices(boards_to_run: list, dom_alerts: list) -> None:
    """Fetch the Special Meeting Notices page and apply what it says.

    Each notice is read into a list of actions, then written onto the board
    the notice is actually about. A notice can carry more than one action:
    a special meeting called to replace one that was cancelled records both.
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

    html = r.text

    # The page prints its own count, e.g. "6 Result(s) Found". If we read a
    # different number of notices than the page says it has, something on the
    # page changed or a notice was missed. This page has served a partial copy
    # before, so the count is worth checking every run.
    stated_count = None
    count_match = re.search(r"(\d+)\s*Result\(s\)\s*Found", html, re.IGNORECASE)
    if count_match:
        stated_count = int(count_match.group(1))

    notice_pattern = re.compile(
        r'<a\s[^>]*href="(?:https?://[^/]+)?(/Government/Boards-Commissions/Special-Meeting-Notices/[^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )

    notices_seen = 0
    notices_applied = 0
    log_entries: list[dict] = []

    for match in notice_pattern.finditer(html):
        raw_text = re.sub(r"<[^>]+>", " ", match.group(2))
        raw_text = _normalize_notice_text(raw_text)
        if not raw_text or len(raw_text) < 10:
            continue

        notices_seen += 1
        notice_url = match.group(1)
        warnings: list[str] = []
        parsed = parse_notice(raw_text, warnings=warnings)
        log_entries.append({
            "url":     notice_url,
            "text":    raw_text,
            "boards":  parsed["boards"],
            "actions": parsed["actions"],
        })

        for warning in warnings:
            print(f"  WARNING: {warning}")
            dom_alerts.append(warning)

        if not parsed["boards"]:
            msg = f"Notice matched no board: {raw_text[:120]}"
            print(f"  WARNING: {msg}")
            dom_alerts.append(msg)
            continue

        if not parsed["actions"]:
            msg = f"Notice produced no readable change: {raw_text[:120]}"
            print(f"  WARNING: {msg}")
            dom_alerts.append(msg)
            continue

        boards = list(parsed["boards"])

        # Joint boards. Two boards that always meet together share one notice.
        # NOTE: this is the behaviour being reviewed in the DDA and DEGA fix;
        # today it copies every notice across, including a special session
        # that named only one of them.
        boards = expand_to_joint_partners(boards, parsed["actions"])

        for abbr in boards:
            board = next((b for b in BOARDS if b.get("abbr") == abbr), None)
            if not board:
                continue
            notices_applied += apply_notice_actions(board, parsed)

    if stated_count is not None and stated_count != notices_seen:
        msg = (f"Special Meeting Notices page says {stated_count} notice(s) but "
               f"{notices_seen} were read. The page may have changed shape or "
               f"served a partial copy.")
        print(f"  WARNING: {msg}")
        dom_alerts.append(msg)
    elif stated_count is None:
        msg = "Could not read the notice count from the Special Meeting Notices page."
        print(f"  WARNING: {msg}")
        dom_alerts.append(msg)

    record_notice_log(log_entries, dom_alerts)

    print(f"  Read {notices_seen} notice(s), changed {notices_applied} board file(s).")















def backfill_archive_from_calendar(boards_to_run: list, calendar: dict) -> list:
    """Add past meetings the city calendar has and the site does not.

    Runs after reconciliation. Only touches dates before today; everything
    from today forward is reconciliation's job.

    Returns a list of human-readable strings for the watchdog.
    """
    print(f"\n{'='*60}\n  Backfilling archive from city calendar\n{'='*60}")

    api_by_board = calendar.get("meetings", {})
    win_start    = calendar.get("window_start")
    today_iso    = date.today().isoformat()

    if not win_start or win_start >= today_iso:
        print("  No past months were queried. Nothing to backfill.")
        return []

    notes: list[str] = []
    added_total = 0

    for board in boards_to_run:
        key = board["key"]
        if key in CALENDAR_UNCOVERED_KEYS:
            continue

        api_meetings = api_by_board.get(key)
        if not api_meetings:
            continue

        if not board["output"].exists():
            continue
        with board["output"].open("r", encoding="utf-8") as f:
            data = json.load(f)

        archive  = data.get("meetings", [])
        upcoming = data.get("upcoming_meetings", [])

        known = {m.get("date") for m in archive} | {m.get("date") for m in upcoming}

        # A date another meeting moved away from is not a missing meeting.
        vacated = {
            m.get("rescheduledFrom")
            for m in list(archive) + list(upcoming)
            if m.get("rescheduledFrom")
        }

        added = 0
        for iso in sorted(api_meetings):
            if iso >= today_iso or iso < win_start:
                continue
            if iso in known or iso in vacated:
                continue

            archive.append({
                "date":        iso,
                "display":     format_display_date_long(iso),
                "link_label":  "No documents posted",
                "minutes_url": None,
                "agenda_url":  None,
                "scrapedAt":   datetime.now(timezone.utc).isoformat(),
                "sourceUrl":   CITY_CALENDAR_API,
            })
            known.add(iso)
            added += 1
            msg = f"BACKFILLED from city calendar: {board['abbr']} {iso}"
            print(f"  {msg}")
            notes.append(msg)

        if added:
            archive.sort(key=lambda m: m.get("date", ""), reverse=True)
            data["meetings"] = archive
            _write_output(board, data)
            added_total += added

    if not added_total:
        print("  Archive already matches the city calendar for the past window.")
    else:
        print(f"  Added {added_total} past meeting(s) across the boards.")

    return notes


def reconcile_with_city_calendar(boards_to_run: list, calendar: dict) -> list:
    """
    Compare each board's upcoming_meetings against the city calendar API and
    correct the differences. Runs AFTER all per-board scraping and AFTER
    special notices have been applied.

    Three cases:

      1. In the API, not in our data
         -> ADD it. This is how meetings the board-page scrapers miss get in
            (e.g. ERSB 2026-07-22, which the city announced only in an inline
            box on its own board page and never posted to the notices page).

      2. In our data, gone from the API, already marked cancelled
         -> KEEP as-is. The city deletes cancelled meetings from its calendar;
            we deliberately keep and mark them. Absence is expected here.

      3. In our data, gone from the API, NOT marked cancelled
         -> KEEP and flag. Never delete. Something changed that no notice
            explained: a silent cancellation, a reschedule, or a city error.
            The meeting stays visible and the discrepancy is reported.

    Only meetings INSIDE the window the API was actually queried for are
    judged. A meeting scheduled beyond that window is not missing, merely
    out of range, and must never be flagged.

    If any calendar request failed, absence is not trustworthy, so case 3
    is skipped entirely for that run. Additions are still safe.

    Returns a list of human-readable discrepancy strings for the watchdog.
    """
    print(f"\n{'='*60}\n  Reconciling against city calendar\n{'='*60}")

    api_by_board = calendar.get("meetings", {})
    win_start    = calendar.get("window_start")
    win_end      = calendar.get("window_end")
    had_failures = bool(calendar.get("failed"))

    if had_failures:
        print("  NOTE: some calendar requests failed. Additions will still be "
              "applied, but absences will not be flagged this run.")

    today_iso = date.today().isoformat()
    # Never judge absence before today or outside the queried window.
    lower = max(today_iso, win_start) if win_start else today_iso
    discrepancies: list[str] = []
    added = updated = flagged = 0

    for board in boards_to_run:
        key = board["key"]
        if key in CALENDAR_UNCOVERED_KEYS:
            continue

        api_meetings = api_by_board.get(key)
        if api_meetings is None:
            # No API data for this board at all. Could be legitimate (a board
            # with nothing scheduled) or a failed request. Do not touch data.
            continue

        if not board["output"].exists():
            continue
        with board["output"].open("r", encoding="utf-8") as f:
            data = json.load(f)

        upcoming = data.get("upcoming_meetings", [])
        ours = {m["date"]: m for m in upcoming}
        changed = False

        # --- Case 1: API has it, we do not -> add ---------------------------
        for iso, info in sorted(api_meetings.items()):
            if iso < today_iso:
                continue
            if iso in ours:
                continue
            entry = {
                "date":    iso,
                "display": format_display_date_long(iso),
                "time":    info["time"],
            }
            upcoming.append(entry)
            ours[iso] = entry
            changed = True
            added += 1
            msg = f"ADDED from city calendar: {board['abbr']} {iso} {info['time']}"
            print(f"  {msg}")
            discrepancies.append(msg)

        # --- Time corrections ----------------------------------------------
        for iso, info in api_meetings.items():
            if iso < today_iso or iso not in ours:
                continue
            existing = ours[iso]
            # Only correct when our stored time is a plain start time that
            # disagrees. Ranges like "5:30 PM - 7:30 PM" carry an end time the
            # API does not provide, so do not overwrite those with a bare time.
            current = (existing.get("time") or "").strip()
            if current and "\u2013" not in current and "-" not in current:
                if current != info["time"]:
                    existing["time"] = info["time"]
                    changed = True
                    updated += 1
                    msg = (f"TIME CORRECTED from city calendar: {board['abbr']} "
                           f"{iso} {current} -> {info['time']}")
                    print(f"  {msg}")
                    discrepancies.append(msg)

        # --- Cases 2 and 3: we have it, API does not ------------------------
        if not had_failures:
            for iso, meeting in sorted(ours.items()):
                if iso < lower:
                    continue
                if win_end and iso > win_end:
                    continue  # beyond what we asked for; not evidence of absence
                if iso in api_meetings:
                    continue
                if meeting.get("isCancelled"):
                    continue  # Case 2: expected, already marked
                # Case 3: unexplained disappearance. Keep it, flag it.
                if not meeting.get("notOnCityCalendar"):
                    meeting["notOnCityCalendar"] = True
                    changed = True
                flagged += 1
                msg = (f"NOT ON CITY CALENDAR: {board['abbr']} {iso} "
                       f"(kept and flagged; no notice explains its removal)")
                print(f"  {msg}")
                discrepancies.append(msg)

        # Clear the flag if a previously-missing meeting reappears.
        for iso, meeting in ours.items():
            if iso in api_meetings and meeting.get("notOnCityCalendar"):
                del meeting["notOnCityCalendar"]
                changed = True

        if changed:
            upcoming.sort(key=lambda m: m["date"])
            data["upcoming_meetings"] = upcoming
            _write_output(board, data)

    print(f"  Added {added}, corrected {updated}, flagged {flagged}.")
    return discrepancies


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

    stored_upcoming = existing.get("upcoming_meetings", [])

    # Retire cancelled entries that have passed into the archive BEFORE the
    # regenerated list replaces them, otherwise the record is lost forever.
    merged_meetings, retired = retire_passed_meetings(
        board, stored_upcoming, merged_meetings
    )
    if retired:
        merged_meetings.sort(key=lambda m: m.get("date", ""), reverse=True)
        print(f"  Retired {len(retired)} passed meeting(s) to archive: "
              + ", ".join(retired))
        RETIRED_MEETINGS.setdefault(board["abbr"], []).extend(retired)

    upcoming = compute_upcoming_schedule(board)
    upcoming = merge_upcoming(board, upcoming, stored_upcoming)
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

    archive = existing.get("meetings", [])

    if board.get("schedule"):
        stored_upcoming = existing.get("upcoming_meetings", [])
        # Same retirement pass as the web_docs path: this branch discards the
        # stored upcoming list wholesale, so cancelled past dates must be moved
        # into the archive first.
        archive, retired = retire_passed_meetings(board, stored_upcoming, archive)
        if retired:
            archive.sort(key=lambda m: m.get("date", ""), reverse=True)
            print(f"  Retired {len(retired)} passed meeting(s) to archive: "
                  + ", ".join(retired))
            RETIRED_MEETINGS.setdefault(board["abbr"], []).extend(retired)

        upcoming = compute_upcoming_schedule(board)
        upcoming = merge_upcoming(board, upcoming, stored_upcoming)
        print(f"  Upcoming: computed {len(upcoming)} dates from schedule rule")
    else:
        stored_upcoming = existing.get("upcoming_meetings", [])
        archive, retired = retire_passed_meetings(board, stored_upcoming, archive)
        if retired:
            archive.sort(key=lambda m: m.get("date", ""), reverse=True)
            RETIRED_MEETINGS.setdefault(board["abbr"], []).extend(retired)
        upcoming = merge_upcoming(board, [], stored_upcoming)
        print(f"  Upcoming: preserved {len(upcoming)} dates from existing JSON")

    output = {
        "last_updated":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "metadata":          build_metadata(board),
        "upcoming_meetings": upcoming,
        "meetings":          archive,
        "recordings":        merged_recordings,
    }
    _write_output(board, output)
    preserved = len(archive)
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

    stored_upcoming = existing.get("upcoming_meetings", [])
    existing_meetings, retired = retire_passed_meetings(
        board, stored_upcoming, existing_meetings
    )
    if retired:
        print(f"  Retired {len(retired)} passed meeting(s) to archive: "
              + ", ".join(retired))
        RETIRED_MEETINGS.setdefault(board["abbr"], []).extend(retired)
    upcoming = merge_upcoming(board, upcoming, stored_upcoming)
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

    # A meeting is never dropped. Anything stored whose date has passed moves
    # into the archive, and anything stored for a future date the generator
    # does not know about is kept.
    stored_upcoming = existing.get("upcoming_meetings", [])
    merged_meetings, retired = retire_passed_meetings(
        board, stored_upcoming, merged_meetings
    )
    if retired:
        merged_meetings.sort(key=lambda m: m.get("date", ""), reverse=True)
        print(f"  Retired {len(retired)} passed meeting(s) to archive: "
              + ", ".join(retired))
        RETIRED_MEETINGS.setdefault(board["abbr"], []).extend(retired)
    upcoming = merge_upcoming(board, upcoming, stored_upcoming)

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
        for abbr, dates in sorted(RETIRED_MEETINGS.items()):
            if dates:
                print(f"  RETIRED: {abbr} → {', '.join(dates)}")
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

    # Informational, not a warning: record any cancelled meetings that moved
    # from upcoming into the archive on this run.
    for abbr, dates in sorted(RETIRED_MEETINGS.items()):
        if dates:
            alerts.append(
                f"{abbr}: retired {len(dates)} cancelled meeting(s) to archive "
                f"({', '.join(dates)})"
            )

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
    parser.add_argument(
        "--backfill-months", type=int, default=CALENDAR_LOOKBACK_MONTHS,
        help="How many past months to check the city calendar against. "
             "Use a larger number once to fill older gaps, e.g. 24.",
    )
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

    dom_alerts: list = []

    # Refresh dynamic metadata from city website
    refresh_board_metadata(boards_to_run, dom_alerts)

    needs_youtube = any(b.get("youtube") for b in boards_to_run)
    api_key       = get_youtube_key() if needs_youtube else None

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

    # Cross-check every board against the city's own calendar API. This runs
    # last so it sees the result of both scraping and notice application.
    calendar_discrepancies: list = []
    try:
        city_calendar = fetch_city_calendar(lookback=max(0, args.backfill_months))
        if city_calendar.get("meetings"):
            calendar_discrepancies = reconcile_with_city_calendar(
                boards_to_run, city_calendar
            )
            calendar_discrepancies.extend(
                backfill_archive_from_calendar(boards_to_run, city_calendar)
            )
        else:
            msg = "City calendar API returned no data; reconciliation skipped."
            print(f"  WARNING: {msg}")
            dom_alerts.append(msg)
    except Exception:
        tb = traceback.format_exc()
        print(f"  WARNING: calendar reconciliation failed:\n{tb}")
        dom_alerts.append(f"Calendar reconciliation raised an exception:\n{tb}")

    # Watchdog + state snapshot (full runs only)
    if not single_board:
        run_watchdog(boards_to_run, prev_state, dom_alerts + calendar_discrepancies)
        save_state(boards_to_run)

    write_meta_json()
    print("\nDone. Run scripts/build.py to validate schemas and build calendar.json / ICS files.")


if __name__ == "__main__":
    main()
