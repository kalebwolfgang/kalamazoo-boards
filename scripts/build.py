"""
build.py — Post-scrape validation and build step
=================================================

Runs after scraper.py in the CI pipeline.

Steps:
  1. Validate boards.json against schemas/boards.schema.json
  2. Validate every data/<board>.json against schemas/meetings.schema.json
  3. On any failure: email exact field path + halt (scraper output is suspect)
  4. On pass: build data/calendar.json and data/ics/*.ics
  5. Write data/changelog.json
  6. Validate changelog.json against schemas/changelog.schema.json
  7. [Task 6] Inject generated HTML into board pages that have marker comments
  8. [Task 6] Write sitemap.xml, search-index.json, api/ endpoints

Usage:
    python scripts/build.py

Both scripts must be run from the repo root so relative paths resolve correctly.
This file imports BOARDS and utility functions from scraper.py; since Python adds
scripts/ to sys.path when you run python scripts/build.py, the import is direct.

Marker system (Task 6):
    Board HTML pages use three HTML comment pairs as injection targets.
    build.py finds each pair with regex and replaces the content between them.

    <!-- GENERATED_HEAD_START --> ... <!-- GENERATED_HEAD_END -->
        In <head>. Receives: title, --board-color CSS variable, build version,
        meta description, OG tags, Twitter card meta, Schema.org JSON-LD.

    <!-- GENERATED_BODY_START --> ... <!-- GENERATED_BODY_END -->
        In <body>, placed after the breadcrumb.
        Receives: .page-header div (abbr, name, description, stat boxes, dot grid,
        legend) and an empty .gov-strip shell (board-template.js fills gov-inner
        at runtime from BOARD.govStrip).
        For bodyType 'appointed': full four-status stat boxes + dot grid.
        For bodyType 'elected':   member count only; no vacancy stats or dots.

    <!-- GENERATED_TABLE_START --> ... <!-- GENERATED_TABLE_END -->
        Inside .members-inner, before .members-table-outer.
        Receives: <thead> + <tbody> rows pre-rendered from boards.json.
        For appointed boards: dot | Name | Type | Residency | Term End | Status
        For elected boards:   dot | Name | Ward/District | Election Year | Next Election | Status

    Fat-format pages (board-*.html files before Task 7 thin conversion) have
    none of these markers. process_board_files() skips them silently.

Seat status logic:
    _seat_status() in this file is the Python equivalent of seatStatus() in
    board-template.js. Both must stay logically identical. If one changes,
    change the other. Statuses: 'vacant' | 'holdover' | 'transitioning' | 'seated'
"""

import calendar as _calendar
import html as _html
import json
import re
import sys
import uuid
from datetime import date as _date
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
from jsonschema import Draft202012Validator

# Utilities live in scraper.py — import them so BOARDS doesn't need to be duplicated
from scraper import (
    BOARDS,
    BOARDS_BY_KEY,
    format_display_date,
    format_display_date_long,
    get_meeting_location,
    send_alert_email,
)


# ---------------------------------------------------------------------------
# Directory / path constants
# ---------------------------------------------------------------------------

SCHEMA_DIR  = Path("schemas")
DATA_DIR    = Path("data")
CONTENT_DIR = Path("content")   # Task 7+ content files live here
API_DIR     = Path("api")

# ---------------------------------------------------------------------------
# Task 6 build constants
# ---------------------------------------------------------------------------

BUILD_TIMESTAMP = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
BASE_URL        = "https://kalebwolfgang.github.io/kalamazoo-boards"
OG_IMAGE        = f"{BASE_URL}/og-boards.png"

_MONTHS_SHORT = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

# Compiled regex patterns for the three marker pairs
_RE_HEAD  = re.compile(
    r"<!-- GENERATED_HEAD_START -->.*?<!-- GENERATED_HEAD_END -->", re.DOTALL
)
_RE_BODY  = re.compile(
    r"<!-- GENERATED_BODY_START -->.*?<!-- GENERATED_BODY_END -->", re.DOTALL
)
_RE_TABLE = re.compile(
    r"<!-- GENERATED_TABLE_START -->.*?<!-- GENERATED_TABLE_END -->", re.DOTALL
)


# ---------------------------------------------------------------------------
# Schema validation  (unchanged from Task 1)
# ---------------------------------------------------------------------------

def load_schema(filename: str) -> dict:
    path = SCHEMA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Schema file not found: {path}\n"
            f"Run from the repo root and ensure schemas/ is committed."
        )
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def format_error(error: jsonschema.ValidationError) -> str:
    """Produce a human-readable single-line error string."""
    path = " > ".join(str(p) for p in error.absolute_path) or "(root)"
    return f"  [{path}]  {error.message}"


def validate_boards_json() -> list[str]:
    """Validate boards.json against boards.schema.json. Returns list of error strings."""
    schema    = load_schema("boards.schema.json")
    data_path = Path("boards.json")

    if not data_path.exists():
        return [f"boards.json not found at {data_path.resolve()}"]

    with data_path.open(encoding="utf-8") as f:
        data = json.load(f)

    validator = Draft202012Validator(schema)
    errors    = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    return [f"boards.json {format_error(e)}" for e in errors]


def validate_meeting_files() -> list[str]:
    """
    Validate each per-board data file against meetings.schema.json.
    Skips meta files: calendar.json, meta.json, state.json, changelog.json.
    Returns list of error strings.
    """
    schema   = load_schema("meetings.schema.json")
    skip     = {"calendar.json", "meta.json", "state.json", "changelog.json"}
    all_errors: list[str] = []

    for path in sorted(DATA_DIR.glob("*.json")):
        if path.name in skip:
            continue

        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        validator = Draft202012Validator(schema)
        errors    = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
        for e in errors:
            all_errors.append(f"{path.name} {format_error(e)}")

    return all_errors


# ---------------------------------------------------------------------------
# Calendar JSON build  (moved here from scraper.py in Task 1)
# ---------------------------------------------------------------------------

def build_calendar_json() -> dict:
    """
    Aggregate all board meetings into data/calendar.json.
    Includes upcoming meetings plus recent past meetings (within 2 months).
    Returns the stats dict for use in changelog.
    """
    print("\nBuilding data/calendar.json...")
    all_meetings: list[dict] = []
    today          = _date.today()
    first_of_month = today.replace(day=1)
    lookback_start = (first_of_month.replace(day=1) - __import__("datetime").timedelta(days=1)).replace(day=1)
    lookback_iso   = lookback_start.strftime("%Y-%m-%d")

    for board in BOARDS:
        path = board["output"]
        if not path.exists():
            continue

        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        key        = board["key"]
        abbr       = board.get("abbr", key.upper())
        name       = board["name"]
        seen_dates: set = set()

        # Upcoming meetings
        for meeting in data.get("upcoming_meetings", []):
            date_iso = meeting.get("date")
            if not date_iso:
                continue
            seen_dates.add(date_iso)
            entry: dict = {
                "date":     date_iso,
                "display":  meeting.get("display", date_iso),
                "time":     meeting.get("time") or None,
                "location": get_meeting_location(board, date_iso, meeting),
                "abbr":     abbr,
                "name":     name,
            }
            if meeting.get("isCancelled"):
                entry["isCancelled"] = True
            if meeting.get("isSpecial"):
                entry["isSpecial"] = True
            if meeting.get("locationChanged"):
                entry["locationChanged"] = True
            if meeting.get("notOnCityCalendar"):
                entry["notOnCityCalendar"] = True
            if meeting.get("rescheduledFrom"):
                entry["rescheduledFrom"] = meeting["rescheduledFrom"]
            if meeting.get("agenda_url"):
                entry["agenda_url"] = meeting["agenda_url"]
            all_meetings.append(entry)

        # Recent past meetings
        for meeting in data.get("meetings", []):
            date_iso = meeting.get("date")
            if not date_iso or date_iso < lookback_iso:
                continue
            if date_iso in seen_dates:
                continue
            seen_dates.add(date_iso)

            entry = {
                "date":     date_iso,
                "display":  format_display_date_long(date_iso),
                "time":     board.get("time") or None,
                "location": get_meeting_location(board, date_iso, meeting),
                "abbr":     abbr,
                "name":     name,
            }
            for url_field in ("youtube_url", "agenda_url", "minutes_url"):
                if meeting.get(url_field):
                    entry[url_field] = meeting[url_field]
            if meeting.get("isCancelled"):
                entry["isCancelled"] = True

            all_meetings.append(entry)

        # Recordings without corresponding document entries
        for rec in data.get("recordings", []):
            date_iso = rec.get("date")
            if not date_iso or date_iso < lookback_iso:
                continue
            if date_iso in seen_dates:
                continue
            seen_dates.add(date_iso)
            all_meetings.append({
                "date":        date_iso,
                "display":     format_display_date_long(date_iso),
                "time":        board.get("time") or None,
                "location":    get_meeting_location(board, date_iso, {}),
                "abbr":        abbr,
                "name":        name,
                "youtube_url": rec["youtube_url"],
            })

    all_meetings.sort(key=lambda m: m["date"])

    out_path = DATA_DIR / "calendar.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "meetings":     all_meetings,
            },
            f, indent=2, ensure_ascii=False,
        )
        f.write("\n")

    print(f"  Wrote {out_path}  ({len(all_meetings)} total meetings)")
    return {"total": len(all_meetings)}


# ---------------------------------------------------------------------------
# ICS generation  (moved here from scraper.py in Task 1)
# ---------------------------------------------------------------------------

def _ics_event(m: dict) -> str:
    date_str = m["date"].replace("-", "")
    time_str = m.get("time", "")
    dtstart  = date_str
    dtend    = date_str
    is_all_day = True

    if time_str and time_str not in ("TBD", "On Call"):
        try:
            parts          = time_str.split("\u2013")
            dt_start       = datetime.strptime(parts[0].strip(), "%I:%M %p")
            dtstart        = f"{date_str}T{dt_start.strftime('%H%M%S')}"
            is_all_day     = False

            if len(parts) >= 2:
                try:
                    dt_end = datetime.strptime(parts[1].strip(), "%I:%M %p")
                    dtend  = f"{date_str}T{dt_end.strftime('%H%M%S')}"
                except ValueError:
                    end_h  = min(dt_start.hour + 2, 23)
                    dtend  = f"{date_str}T{end_h:02d}{dt_start.minute:02d}00"
            else:
                end_h = min(dt_start.hour + 2, 23)
                dtend = f"{date_str}T{end_h:02d}{dt_start.minute:02d}00"
        except ValueError:
            pass

    lines = ["BEGIN:VEVENT", f"UID:{m['abbr']}-{m['date']}@kalamazoocity-boards"]

    if m.get("isCancelled"):
        lines.append("STATUS:CANCELLED")

    if is_all_day:
        lines.append(f"DTSTART;VALUE=DATE:{dtstart}")
    else:
        lines.append(f"DTSTART;TZID=America/Detroit:{dtstart}")
        lines.append(f"DTEND;TZID=America/Detroit:{dtend}")

    lines.append(f"SUMMARY:{m['name']} \u2014 City of Kalamazoo")
    if m.get("location"):
        lines.append(f"LOCATION:{m['location']}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def generate_ics_files(
    calendar_json_path: str = "data/calendar.json",
    output_dir: str = "data/ics",
) -> None:
    print("\nGenerating ICS files...")

    if not Path(calendar_json_path).exists():
        print(f"  WARNING: {calendar_json_path} not found. Skipping ICS generation.")
        return

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    with open(calendar_json_path, encoding="utf-8") as f:
        data = json.load(f)

    today_iso = _date.today().isoformat()
    meetings  = [m for m in data.get("meetings", []) if m.get("date", "") >= today_iso]

    vtimezone = (
        "BEGIN:VTIMEZONE\r\n"
        "TZID:America/Detroit\r\n"
        "BEGIN:DAYLIGHT\r\n"
        "TZOFFSETFROM:-0500\r\nTZOFFSETTO:-0400\r\nTZNAME:EDT\r\n"
        "DTSTART:19700308T020000\r\nRRULE:FREQ=YEARLY;BYDAY=2SU;BYMONTH=3\r\n"
        "END:DAYLIGHT\r\n"
        "BEGIN:STANDARD\r\n"
        "TZOFFSETFROM:-0400\r\nTZOFFSETTO:-0500\r\nTZNAME:EST\r\n"
        "DTSTART:19701101T020000\r\nRRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=11\r\n"
        "END:STANDARD\r\nEND:VTIMEZONE\r\n"
    )
    base_header = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        "PRODID:-//City of Kalamazoo Boards & Commissions//EN\r\n"
        "X-WR-CALNAME:Kalamazoo Boards & Commissions\r\n"
        "X-WR-CALDESC:Public meetings of all City of Kalamazoo boards and commissions\r\n"
        + vtimezone
    )
    footer = "END:VCALENDAR\r\n"

    by_board: dict = {}
    for m in meetings:
        by_board.setdefault(m["abbr"], []).append(m)

    for abbr, board_meetings in by_board.items():
        events     = "\r\n".join(_ics_event(m) for m in board_meetings)
        board_name = board_meetings[0]["name"]
        header     = base_header.replace(
            "X-WR-CALNAME:Kalamazoo Boards & Commissions",
            f"X-WR-CALNAME:{board_name}",
        )
        with open(Path(output_dir) / f"{abbr.lower()}.ics", "w", encoding="utf-8") as f:
            f.write(header + events + "\r\n" + footer)

    if meetings:
        all_events = "\r\n".join(_ics_event(m) for m in meetings)
        with open(Path(output_dir) / "all-boards.ics", "w", encoding="utf-8") as f:
            f.write(base_header + all_events + "\r\n" + footer)

    # Remove ICS files for boards that no longer have upcoming meetings.
    # Without this the file is simply never rewritten and goes stale: it keeps
    # advertising meetings the site itself no longer lists, and anyone who
    # subscribed to that calendar feed keeps seeing them. Only files this
    # function is responsible for are considered.
    expected = {f"{a.lower()}.ics" for a in by_board}
    if meetings:
        expected.add("all-boards.ics")

    removed = 0
    for stale in Path(output_dir).glob("*.ics"):
        if stale.name not in expected:
            stale.unlink()
            removed += 1
            print(f"  Removed stale {stale.name} (no upcoming meetings)")

    print(f"  Wrote {len(by_board) + 1} ICS files to {output_dir}/"
          + (f", removed {removed} stale" if removed else ""))


# ---------------------------------------------------------------------------
# Changelog  (unchanged from Task 1)
# ---------------------------------------------------------------------------

def write_changelog(run_id: str, validation_passed: bool) -> None:
    """Write data/changelog.json and validate it against its own schema."""
    schema = load_schema("changelog.schema.json")

    changes = []
    for board in BOARDS:
        if not board["output"].exists():
            continue
        with board["output"].open(encoding="utf-8") as f:
            data = json.load(f)
        changes.append({
            "board":    board["key"],
            "meetings": len(data.get("meetings", [])),
            "upcoming": len(data.get("upcoming_meetings", [])),
        })

    changelog = {
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "runId":            run_id,
        "validationPassed": validation_passed,
        "changes":          changes,
    }

    errors = list(Draft202012Validator(schema).iter_errors(changelog))
    if errors:
        print(f"  WARNING: changelog.json itself failed schema validation:")
        for e in errors:
            print(f"    {format_error(e)}")

    path = DATA_DIR / "changelog.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(changelog, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  Wrote {path}")


# ===========================================================================
# TASK 6 — HTML generation, sitemap, search index, API endpoints
# ===========================================================================

# ---------------------------------------------------------------------------
# Seat status utilities
# ---------------------------------------------------------------------------

def _six_months_from(d: _date) -> _date:
    """
    Returns the date exactly 6 calendar months from d.
    Matches JavaScript: new Date(); date.setMonth(date.getMonth() + 6)
    Clamps to the last day of the target month when necessary (e.g. Aug 31 → Feb 28).
    """
    month = d.month + 6
    year  = d.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    max_day = _calendar.monthrange(year, month)[1]
    return _date(year, month, min(d.day, max_day))


def _seat_status(member: dict, today: _date, six_mo: _date) -> str:
    """
    Python equivalent of seatStatus() in board-template.js.
    Returns: 'vacant' | 'holdover' | 'transitioning' | 'seated'

    IMPORTANT: Keep this logic in sync with seatStatus() in board-template.js.
    Only call this for bodyType 'appointed' boards.
    """
    if member.get("isVacant"):
        return "vacant"
    term_end_str = member.get("termEnd")
    if not term_end_str or term_end_str == "2100-01-01":
        return "seated"  # permanent / ex-officio
    term_end = _date.fromisoformat(term_end_str)
    if term_end < today:
        return "holdover"
    if term_end < six_mo:
        return "transitioning"
    return "seated"


def _fmt_date_short(iso: str | None) -> str | None:
    """Format ISO date string as 'Jan 1, 2026'. Returns None for null/permanent."""
    if not iso or iso == "2100-01-01":
        return None
    y, m, d = iso.split("-")
    return f"{_MONTHS_SHORT[int(m) - 1]} {int(d)}, {y}"


def _term_text(member: dict, status: str) -> str:
    """Human-readable term description used in tooltip data-term attribute."""
    if status == "vacant":
        return "Open seat"
    if status == "seated" and (
        not member.get("termEnd") or member.get("termEnd") == "2100-01-01"
    ):
        return "Permanent appointment"
    d = _fmt_date_short(member.get("termEnd"))
    if status == "holdover":
        return f"Term expired {d} \u2014 serving as holdover"
    if status == "transitioning":
        return f"Term ends {d}"
    return f"Term ends {d}" if d else "Permanent appointment"


def _member_type_label(member: dict) -> str:
    """Short badge label for the Type column in the appointed member table."""
    role = (member.get("role") or "").lower()
    req  = (member.get("positionRequirement") or "").lower()
    if "ex-officio" in role:
        return "Ex-Officio"
    if "commission representative" in req or "commission representative" in role:
        return "Commission Rep."
    if "city manager" in req or "manager/designee" in req:
        return "City Mgr."
    if req.startswith("at-large"):
        return "At-Large"
    if "youth representative" in req:
        return "Youth Rep."
    if "neighborhood representative" in req:
        return "Neighborhood Rep."
    if "diverse organization" in req:
        return "Org. Rep."
    return "Voting"


def _esc(text: str) -> str:
    """HTML-escape a value for safe use in attribute values and text content."""
    return _html.escape(str(text), quote=True)


def _default_description(board: dict) -> str:
    name = board.get("name", "")
    return (
        f"Meeting records, member roster, and transparency information "
        f"for the {name}, a City of Kalamazoo board or commission."
    )


# ---------------------------------------------------------------------------
# Head generator
# ---------------------------------------------------------------------------

def generate_head(board: dict) -> str:
    """
    Returns the full HTML block to inject between
    <!-- GENERATED_HEAD_START --> and <!-- GENERATED_HEAD_END -->.

    Includes: <title>, --board-color CSS var, build-version meta,
    meta description, OG tags, Twitter card, Schema.org JSON-LD.
    """
    name       = board.get("name", "")
    color      = board.get("color", "#2596be")
    slug       = board.get("slug", board.get("abbr", "").lower())
    raw_desc   = board.get("description") or _default_description(board)
    desc_esc   = _esc(raw_desc)
    page_url   = f"{BASE_URL}/board-{slug}.html"
    full_title = f"{_esc(name)} \u2014 City of Kalamazoo Boards &amp; Commissions"

    schema_ld = {
        "@context": "https://schema.org",
        "@type": "GovernmentOrganization",
        "name": f"{name} \u2014 City of Kalamazoo",
        "url": page_url,
        "description": raw_desc,
        "parentOrganization": {
            "@type": "GovernmentOrganization",
            "name": "City of Kalamazoo",
            "url": "https://www.kalamazoocity.org",
        },
    }

    lines = [
        "<!-- GENERATED_HEAD_START -->",
        f"<title>{full_title}</title>",
        f"<style>:root {{ --board-color: {color}; }}</style>",
        f'<meta name="build-version" content="{BUILD_TIMESTAMP}">',
        f'<meta name="description" content="{desc_esc}">',
        f'<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="City of Kalamazoo \u2014 Boards &amp; Commissions">',
        f'<meta property="og:title" content="{full_title}">',
        f'<meta property="og:description" content="{desc_esc}">',
        f'<meta property="og:url" content="{page_url}">',
        f'<meta property="og:image" content="{OG_IMAGE}">',
        f'<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{full_title}">',
        f'<meta name="twitter:description" content="{desc_esc}">',
        f'<script type="application/ld+json">',
        json.dumps(schema_ld, indent=2, ensure_ascii=False),
        f"</script>",
        "<!-- GENERATED_HEAD_END -->",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Body generator  (page header + empty gov strip)
# ---------------------------------------------------------------------------

def generate_body(board: dict, today: _date, six_mo: _date) -> str:
    """
    Returns the full HTML block to inject between
    <!-- GENERATED_BODY_START --> and <!-- GENERATED_BODY_END -->.

    Covers:
      - .page-header div: abbr, name, description, stat boxes, dot grid, legend
      - .gov-strip shell with empty .gov-inner (board-template.js fills it)

    bodyType 'appointed': all five stat boxes + four-shape dot grid + legend.
    bodyType 'elected':   member count only; no vacancy stats, no dot grid.

    NOTE: board.bodyType defaults to 'appointed' if the field is absent.
          Add bodyType to all boards.json entries before Task 10.
    """
    body_type = board.get("bodyType", "appointed")
    name      = board.get("name", "")
    abbr      = board.get("abbr", "")
    raw_desc  = board.get("description") or ""

    desc_html = (
        f'\n      <p class="page-header-desc">{_esc(raw_desc)}</p>'
        if raw_desc else ""
    )

    if body_type == "elected":
        seat_block_html = _elected_seat_block(board)
    else:
        seat_block_html = _appointed_seat_block(board, today, six_mo)

    page_header = "\n".join([
        '<div class="page-header">',
        '  <div class="page-header-inner">',
        '    <div>',
        f'      <div class="board-badge"><span class="board-abbr-large">{_esc(abbr)}</span></div>',
        f'      <h1>{_esc(name)}</h1>{desc_html}',
        '    </div>',
        f'    {seat_block_html}',
        '  </div>',
        '</div>',
    ])

    gov_strip = "\n".join([
        '<div class="gov-strip">',
        '  <div class="gov-inner">',
        '    <!-- populated at runtime by board-template.js from BOARD.govStrip -->',
        '  </div>',
        '</div>',
    ])

    return "\n".join([
        "<!-- GENERATED_BODY_START -->",
        page_header,
        "",
        gov_strip,
        "<!-- GENERATED_BODY_END -->",
    ])


def _appointed_seat_block(board: dict, today: _date, six_mo: _date) -> str:
    """Stat boxes + dot grid for appointed boards."""
    members  = board.get("members", [])
    max_m    = board.get("maxMembers")

    n_vacant = n_holdover = n_trans = n_seated = 0
    for m in members:
        s = _seat_status(m, today, six_mo)
        if s == "vacant":          n_vacant   += 1
        elif s == "holdover":      n_holdover += 1
        elif s == "transitioning": n_trans    += 1
        else:                      n_seated   += 1

    total       = len(members)
    seats_label = f"{total}\u00a0of up to\u00a0{max_m}" if max_m else str(total)

    stat_row = "\n".join([
        '      <div class="seat-stat-row">',
        f'        <div class="seat-stat"><div class="seat-stat-n" id="stat-total">{seats_label}</div>'
        f'<div class="seat-stat-lbl">Seats</div></div>',
        f'        <div class="seat-stat"><div class="seat-stat-n red" id="stat-open">{n_vacant}</div>'
        f'<div class="seat-stat-lbl">Open</div></div>',
        f'        <div class="seat-stat"><div class="seat-stat-n red" id="stat-holdover">{n_holdover}</div>'
        f'<div class="seat-stat-lbl">Holdover</div></div>',
        f'        <div class="seat-stat"><div class="seat-stat-n yellow" id="stat-trans">{n_trans}</div>'
        f'<div class="seat-stat-lbl">Trans.</div></div>',
        f'        <div class="seat-stat"><div class="seat-stat-n green" id="stat-seated">{n_seated}</div>'
        f'<div class="seat-stat-lbl">Seated</div></div>',
        '      </div>',
    ])

    # Dot grid
    dots = []
    for m in members:
        s        = _seat_status(m, today, six_mo)
        is_vac   = m.get("isVacant", False)
        name_val = (
            f"{m.get('firstName', '')} {m.get('lastName', '')}".strip()
            if not is_vac else "Open Seat"
        )
        role_val = m.get("positionRequirement") or m.get("role") or ""
        term_val = _term_text(m, s)
        res_val  = m.get("residency") or ""
        pos      = m.get("position", "")
        aria     = (
            f"Position {pos}: open seat{f' \u2014 {role_val}' if role_val else ''}"
            if is_vac
            else f"Position {pos}: {name_val}"
        )
        dots.append(
            f'<div class="seat-dot-hdr {s}" role="button" tabindex="0"'
            f' data-name="{_esc(name_val)}"'
            f' data-role="{_esc(role_val)}"'
            f' data-term="{_esc(term_val)}"'
            f' data-termcls="{s}"'
            f' data-res="{_esc(res_val)}"'
            f' aria-label="{_esc(aria)}"></div>'
        )

    # Expandable / available slots
    if max_m and max_m > len(members):
        for _ in range(max_m - len(members)):
            dots.append(
                f'<div class="seat-dot-hdr expandable" role="button" tabindex="0"'
                f' data-name="Available seat"'
                f' data-role=""'
                f' data-term="Board may expand to up to {max_m} members"'
                f' data-termcls="seated"'
                f' data-res=""'
                f' aria-label="Available seat \u2014 board may expand to {max_m} members"></div>'
            )

    dots_html = "\n        ".join(dots)

    legend = "\n".join([
        '      <div class="dot-legend">',
        '        <span class="dl-item"><span class="dl-dot vacant"></span> Open</span>',
        '        <span class="dl-item"><span class="dl-dot holdover"></span> Holdover</span>',
        '        <span class="dl-item"><span class="dl-dot transitioning"></span> Transitioning</span>',
        '        <span class="dl-item"><span class="dl-dot seated"></span> Seated</span>',
        '      </div>',
    ])

    return "\n".join([
        '<div class="header-seat-block">',
        stat_row,
        '      <div class="header-dots" id="header-dots">',
        f'        {dots_html}',
        '      </div>',
        legend,
        '    </div>',
    ])


def _elected_seat_block(board: dict) -> str:
    """Member count stat only for elected bodies (no vacancy stats or dot grid)."""
    total = len(board.get("members", []))
    return "\n".join([
        '<div class="header-seat-block">',
        '      <div class="seat-stat-row">',
        f'        <div class="seat-stat"><div class="seat-stat-n" id="stat-total">{total}</div>'
        f'<div class="seat-stat-lbl">Members</div></div>',
        '      </div>',
        '    </div>',
    ])


# ---------------------------------------------------------------------------
# Table generator
# ---------------------------------------------------------------------------

def generate_table(board: dict, today: _date, six_mo: _date) -> str:
    """
    Returns the full HTML block to inject between
    <!-- GENERATED_TABLE_START --> and <!-- GENERATED_TABLE_END -->.

    Produces a complete <thead> + <tbody> for the members table.
    board-template.js has no member table rendering — this is the sole source.

    Appointed columns: dot | Name (+ role sub) | Type | Residency | Term End | Status
    Elected columns:   dot | Name              | Ward/District | Election Year | Next Election | Status
    """
    body_type = board.get("bodyType", "appointed")
    members   = board.get("members", [])

    if body_type == "elected":
        thead = (
            "<thead><tr>"
            '<th scope="col"><span class="sr-only">Seat status</span></th>'
            '<th scope="col">Name</th>'
            '<th scope="col">Ward / District</th>'
            '<th scope="col">Election Year</th>'
            '<th scope="col">Next Election</th>'
            '<th scope="col">Status</th>'
            "</tr></thead>"
        )
        rows = _elected_rows(members)
    else:
        thead = (
            "<thead><tr>"
            '<th scope="col"><span class="sr-only">Seat status</span></th>'
            '<th scope="col">Name</th>'
            '<th scope="col">Type</th>'
            '<th scope="col">Residency</th>'
            '<th scope="col">Term End</th>'
            '<th scope="col">Status</th>'
            "</tr></thead>"
        )
        rows = _appointed_rows(members, today, six_mo)

    tbody = "<tbody>\n" + "\n".join(rows) + "\n</tbody>"
    return "\n".join([
        "<!-- GENERATED_TABLE_START -->",
        thead,
        tbody,
        "<!-- GENERATED_TABLE_END -->",
    ])


def _appointed_rows(members: list[dict], today: _date, six_mo: _date) -> list[str]:
    rows = []
    _STATUS_LABELS = {
        "vacant":       "Open",
        "holdover":     "Holdover",
        "transitioning":"Transitioning",
        "seated":       "Seated",
    }
    for m in members:
        s         = _seat_status(m, today, six_mo)
        is_vac    = m.get("isVacant", False)
        name_val  = (
            f"{m.get('firstName', '')} {m.get('lastName', '')}".strip()
            if not is_vac else "Open Seat"
        )
        type_lbl  = _member_type_label(m)
        res_val   = _esc(m.get("residency") or "\u2014") if not is_vac else "\u2014"
        term_raw  = m.get("termEnd")
        # A vacant seat has no term, so it must never render as "Permanent".
        if is_vac:
            term_disp = "\u2014"
        elif not term_raw or term_raw == "2100-01-01":
            term_disp = "Permanent"
        else:
            term_disp = _fmt_date_short(term_raw) or "\u2014"
        ds_label  = _STATUS_LABELS.get(s, "Seated")
        role_sub  = (
            m.get("positionRequirement")
            or (m.get("role") if m.get("role") not in ("Member", None, "") else "")
            or ""
        )
        role_html    = f'<div class="member-role-sub">{_esc(role_sub)}</div>' if role_sub else ""
        vacant_class = " vacant" if is_vac else ""

        rows.append(
            "<tr>"
            f'<td style="text-align:center"><span class="member-dot {s}"></span></td>'
            f'<td><div class="member-name{vacant_class}">{_esc(name_val)}</div>{role_html}</td>'
            f'<td><span class="type-badge">{_esc(type_lbl)}</span></td>'
            f"<td>{res_val}</td>"
            f"<td>{_esc(term_disp)}</td>"
            f'<td><span class="status-badge {s}">{ds_label}</span></td>'
            "</tr>"
        )
    return rows


def _elected_rows(members: list[dict]) -> list[str]:
    rows = []
    for m in members:
        is_vac    = m.get("isVacant", False)
        name_val  = (
            f"{m.get('firstName', '')} {m.get('lastName', '')}".strip()
            if not is_vac else "Open Seat"
        )
        ward      = _esc(
            m.get("ward") or m.get("district") or m.get("positionRequirement") or "\u2014"
        )
        elect_yr  = _esc(str(m.get("electionYear") or "\u2014"))
        next_el   = _esc(m.get("nextElection") or "\u2014")
        rows.append(
            "<tr>"
            '<td style="text-align:center"><span class="member-dot seated"></span></td>'
            f'<td><div class="member-name">{_esc(name_val)}</div></td>'
            f"<td>{ward}</td>"
            f"<td>{elect_yr}</td>"
            f"<td>{next_el}</td>"
            '<td><span class="status-badge seated">Seated</span></td>'
            "</tr>"
        )
    return rows


# ---------------------------------------------------------------------------
# Process board HTML files
# ---------------------------------------------------------------------------

def process_board_files(boards_data: list[dict]) -> int:
    """
    Scans all board-*.html files in the repo root.
    For each file that contains at least one of the three marker pairs,
    replaces the marker content with freshly generated HTML.

    Files without any markers (fat-format pages, pre-Task 7) are silently skipped.
    Returns the count of files that were actually modified.
    """
    today  = _date.today()
    six_mo = _six_months_from(today)

    # Build lookup: slug → board dict
    by_slug = {b.get("slug", ""): b for b in boards_data if b.get("slug")}

    updated = 0
    for path in sorted(Path(".").glob("board-*.html")):
        # Derive slug from filename "board-{slug}.html"
        slug  = path.stem[len("board-"):]
        board = by_slug.get(slug)
        if not board:
            continue

        original = path.read_text(encoding="utf-8")

        has_head  = "<!-- GENERATED_HEAD_START -->"  in original
        has_body  = "<!-- GENERATED_BODY_START -->"  in original
        has_table = "<!-- GENERATED_TABLE_START -->" in original

        if not (has_head or has_body or has_table):
            continue  # Fat-format page — skip silently

        content = original
        if has_head:
            content = _RE_HEAD.sub(generate_head(board), content)
        if has_body:
            content = _RE_BODY.sub(generate_body(board, today, six_mo), content)
        if has_table:
            content = _RE_TABLE.sub(generate_table(board, today, six_mo), content)

        if content != original:
            path.write_text(content, encoding="utf-8")
            updated += 1
            print(f"  Updated {path.name}")

    return updated


# ---------------------------------------------------------------------------
# Sitemap
# ---------------------------------------------------------------------------

def generate_sitemap(boards_data: list[dict]) -> None:
    """Writes sitemap.xml to the repo root listing all public pages."""
    print("\nGenerating sitemap.xml...")

    # Core pages
    pages = [
        ("index.html",    "weekly",  "1.0"),
        ("calendar.html", "weekly",  "0.9"),
        ("faq.html",      "monthly", "0.7"),
    ]

    # One entry per board with a slug
    for b in sorted(boards_data, key=lambda x: x.get("name", "")):
        slug = b.get("slug")
        if slug:
            pages.append((f"board-{slug}.html", "monthly", "0.8"))

    today_str = _date.today().isoformat()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for page, freq, pri in pages:
        lines.append(
            f"  <url>\n"
            f"    <loc>{BASE_URL}/{page}</loc>\n"
            f"    <changefreq>{freq}</changefreq>\n"
            f"    <priority>{pri}</priority>\n"
            f"    <lastmod>{today_str}</lastmod>\n"
            f"  </url>"
        )
    lines.append("</urlset>")

    out = Path("sitemap.xml")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Wrote {out}  ({len(pages)} URLs)")


# ---------------------------------------------------------------------------
# Search index
# ---------------------------------------------------------------------------

def generate_search_index(boards_data: list[dict]) -> None:
    """
    Writes search-index.json to the repo root.

    Each entry has:
      id      — unique string identifier
      type    — "board" | "section"
      title   — display label
      url     — page path (with optional #anchor fragment)
      content — plain-text searchable content

    Board-level entries are always generated.
    Section entries (per accordion) are added when content/{abbr}.json
    files exist (Task 7+). Gracefully skips if the content directory
    does not exist yet.
    """
    print("\nGenerating search-index.json...")
    entries: list[dict] = []

    for board in sorted(boards_data, key=lambda x: x.get("name", "")):
        slug = board.get("slug")
        abbr = board.get("abbr", "")
        if not slug:
            continue

        # Board-level entry
        entries.append({
            "id":      f"board-{slug}",
            "type":    "board",
            "title":   board.get("name", abbr),
            "url":     f"board-{slug}.html",
            "content": board.get("description") or _default_description(board),
        })

        # Per-accordion entries from content file (Task 7+)
        if not CONTENT_DIR.exists():
            continue
        content_path = CONTENT_DIR / f"{abbr.lower()}.json"
        if not content_path.exists():
            continue

        try:
            with content_path.open(encoding="utf-8") as f:
                cdata = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        for acc in cdata.get("accordions", []):
            anchor  = acc.get("anchor", "")
            title   = acc.get("title", "")
            summary = acc.get("summary", "")
            body_parts = []
            for item in acc.get("body", []):
                if item.get("type") == "paragraph":
                    body_parts.append(item.get("text", ""))
                elif item.get("type") == "list":
                    body_parts.extend(item.get("items", []))
                elif item.get("type") == "field":
                    body_parts.append(
                        f"{item.get('label', '')} {item.get('value', '')}"
                    )
            content_text = f"{summary} {' '.join(body_parts)}".strip()
            entries.append({
                "id":      f"board-{slug}-{anchor}",
                "type":    "section",
                "title":   f"{board.get('name', abbr)} \u2014 {title}",
                "url":     f"board-{slug}.html#{anchor}",
                "content": content_text,
            })

    out = Path("search-index.json")
    with out.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  Wrote {out}  ({len(entries)} entries)")


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

def generate_api_endpoints(boards_data: list[dict]) -> None:
    """
    Writes static JSON files to api/:

      api/boards.json    — complete boards list (mirrors boards.json)
      api/vacancies.json — appointed boards with ≥1 open seat, sorted by vacancy count
      api/meetings.json  — upcoming meetings extracted from data/calendar.json
      api/changelog.json — copy of data/changelog.json (omitted if not yet present)

    Skips api/meetings.json and api/changelog.json gracefully if their
    source files do not exist yet.
    """
    print("\nGenerating API endpoints...")
    API_DIR.mkdir(exist_ok=True)

    # api/boards.json
    _write_api_file("boards.json", boards_data)

    # api/vacancies.json  (appointed only)
    vacancies = []
    today  = _date.today()
    six_mo = _six_months_from(today)
    for b in boards_data:
        if b.get("bodyType", "appointed") != "appointed":
            continue
        n_vacant = sum(
            1 for m in b.get("members", [])
            if _seat_status(m, today, six_mo) == "vacant"
        )
        if n_vacant:
            vacancies.append({
                "abbr":        b.get("abbr"),
                "name":        b.get("name"),
                "slug":        b.get("slug"),
                "vacantSeats": n_vacant,
                "totalSeats":  len(b.get("members", [])),
                "url":         f"{BASE_URL}/board-{b.get('slug')}.html",
            })
    vacancies.sort(key=lambda x: -x["vacantSeats"])
    _write_api_file("vacancies.json", vacancies)

    # api/meetings.json  (upcoming only, from calendar.json)
    cal_path = DATA_DIR / "calendar.json"
    if cal_path.exists():
        with cal_path.open(encoding="utf-8") as f:
            cal = json.load(f)
        today_iso = _date.today().isoformat()
        upcoming  = [m for m in cal.get("meetings", []) if m.get("date", "") >= today_iso]
        _write_api_file("meetings.json", {
            "lastUpdated": _date.today().isoformat(),
            "meetings":    upcoming,
        })
    else:
        print("  Skipping api/meetings.json — data/calendar.json not found yet")

    # api/changelog.json
    cl_path = DATA_DIR / "changelog.json"
    if cl_path.exists():
        with cl_path.open(encoding="utf-8") as f:
            cl = json.load(f)
        _write_api_file("changelog.json", cl)
    else:
        print("  Skipping api/changelog.json — data/changelog.json not found yet")


def _write_api_file(filename: str, data: object) -> None:
    path = API_DIR / filename
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  Wrote {path}")


# ===========================================================================
# Main
# ===========================================================================

def main() -> None:
    run_id = str(uuid.uuid4())[:8]
    print(f"Build pipeline  |  run: {run_id}")

    # ── Step 1: Schema validation ─────────────────────────────────────────
    print("\nValidating schemas...")
    all_errors: list[str] = []

    print("  boards.json...")
    all_errors.extend(validate_boards_json())

    print(f"  data/*.json ({len(list(DATA_DIR.glob('*.json')))} files)...")
    all_errors.extend(validate_meeting_files())

    if all_errors:
        header = f"Schema validation FAILED — {len(all_errors)} error(s):\n\n"
        body   = header + "\n".join(all_errors)
        print(f"\n{body}")
        send_alert_email("[kalamazoo-boards] CRITICAL: Schema Validation Failed", body)
        write_changelog(run_id, validation_passed=False)
        sys.exit(1)

    print(f"  Validation passed. {len(list(DATA_DIR.glob('*.json')))} file(s) OK.")

    # ── Step 2: Build calendar + ICS ──────────────────────────────────────
    build_calendar_json()
    generate_ics_files()

    # ── Step 3: Load boards.json for HTML generation ───────────────────────
    boards_path = Path("boards.json")
    if not boards_path.exists():
        print("\nWARNING: boards.json not found — skipping HTML generation and API endpoints.")
    else:
        with boards_path.open(encoding="utf-8") as f:
            boards_data = json.load(f)
        # Filter the same boards the site excludes at runtime
        boards_data = [b for b in boards_data if b.get("abbr") not in ("PCIC", "CSAC")]

        # ── Step 4: Board HTML generation ─────────────────────────────────
        print(f"\nProcessing board HTML files ({len(boards_data)} boards in boards.json)...")
        updated = process_board_files(boards_data)
        if updated:
            print(f"  {updated} file(s) updated.")
        else:
            print("  No files with marker comments found yet (expected until Task 7).")

        # ── Step 5: Artifacts ──────────────────────────────────────────────
        generate_sitemap(boards_data)
        generate_search_index(boards_data)
        generate_api_endpoints(boards_data)

    # ── Step 6: Changelog ─────────────────────────────────────────────────
    write_changelog(run_id, validation_passed=True)

    print("\nBuild complete.")


if __name__ == "__main__":
    main()
