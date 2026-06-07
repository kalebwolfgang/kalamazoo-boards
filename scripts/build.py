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

Usage:
    python scripts/build.py

Both scripts must be run from the repo root so relative paths resolve correctly.
This file imports BOARDS and utility functions from scraper.py; since Python adds
scripts/ to sys.path when you run python scripts/build.py, the import is direct.
"""

import json
import sys
import uuid
from datetime import date, datetime, timezone
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


SCHEMA_DIR = Path("schemas")
DATA_DIR   = Path("data")


# ---------------------------------------------------------------------------
# Schema validation
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
    schema = load_schema("boards.schema.json")
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
# Calendar JSON build  (moved here from scraper.py)
# ---------------------------------------------------------------------------

def build_calendar_json() -> dict:
    """
    Aggregate all board meetings into data/calendar.json.
    Includes upcoming meetings plus recent past meetings (within 2 months).
    Returns the stats dict for use in changelog.
    """
    print("\nBuilding data/calendar.json...")
    all_meetings: list[dict] = []
    today          = date.today()
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
# ICS generation  (moved here from scraper.py)
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

    today_iso = date.today().isoformat()
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

    print(f"  Wrote {len(by_board) + 1} ICS files to {output_dir}/")


# ---------------------------------------------------------------------------
# Changelog
# ---------------------------------------------------------------------------

def write_changelog(run_id: str, validation_passed: bool) -> None:
    """Write data/changelog.json and validate it against its own schema."""
    schema = load_schema("changelog.schema.json")

    # Collect per-board change stats by reading the existing data files
    changes = []
    for board in BOARDS:
        if not board["output"].exists():
            continue
        with board["output"].open(encoding="utf-8") as f:
            data = json.load(f)
        changes.append({
            "board":     board["key"],
            "meetings":  len(data.get("meetings", [])),
            "upcoming":  len(data.get("upcoming_meetings", [])),
        })

    changelog = {
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "runId":            run_id,
        "validationPassed": validation_passed,
        "changes":          changes,
    }

    # Validate before writing
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    run_id = str(uuid.uuid4())[:8]
    print(f"Build pipeline  |  run: {run_id}")

    # ---- Step 1: Schema validation ------------------------------------------
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

    # ---- Step 2: Build derived files ----------------------------------------
    build_calendar_json()
    generate_ics_files()

    # ---- Step 3: Changelog --------------------------------------------------
    write_changelog(run_id, validation_passed=True)

    print("\nBuild complete.")


if __name__ == "__main__":
    main()
