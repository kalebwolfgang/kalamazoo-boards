"""Tests for the Special Meeting Notices reader.

WHERE THIS GOES: tests/test_notices.py

Run it from the repo root:

    python3 tests/test_notices.py

It prints one line per case and exits with a non-zero code if anything fails,
so it can be wired into CI later. No test framework needed.

Every case below is either a notice the city has actually published or a
wording it plausibly could. The TRAPS section matters most: those are strings
that must NOT be read as meetings. Each one is a mistake the code made at some
point during development.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scraper  # noqa: E402

# Every notice is read as if today were this date, so results never drift.
TODAY = date(2026, 9, 1)

Z = "\u200b"   # the invisible character the city pastes between words


def notice(text: str) -> dict:
    return scraper.parse_notice(text, today=TODAY)


def summary(parsed: dict) -> tuple:
    """Boil a parse down to something comparable."""
    actions = []
    for a in parsed["actions"]:
        if a["action"] == "rescheduled":
            actions.append(("rescheduled", a["old"], a["new"]))
        else:
            actions.append((a["action"], a["date"]))
    return tuple(parsed["boards"]), tuple(actions)


# ---------------------------------------------------------------------------
# Notices published by the City of Kalamazoo
# ---------------------------------------------------------------------------

LIVE = [
    (
        "PRAB reschedule",
        f"Parks and Recreation Advisory Board September Meeting Rescheduled The "
        f"meeting of the {Z}Parks and Recreation Advisory Board{Z} scheduled for "
        f"{Z}Tuesday{Z}, {Z}September 8, 2026{Z} has been RESCHEDULED to meet on "
        f"{Z}Wednesday{Z}, {Z}September 9, 2026{Z}, at {Z}5:30 p.m.{Z} in {Z}Parks "
        f"and Recreation Community Room at Mayors' Riverfront Park, 251 Mills Street{Z}",
        (("PRAB",), (("rescheduled", "2026-09-08", "2026-09-09"),)),
    ),
    (
        "NFP cancellation with an ordinal date",
        f"NFP Review Board August 25 Meeting Cancelled The meeting of the "
        f"{Z}Natural Features Protection Review Board{Z} scheduled for {Z}Tuesday{Z}, "
        f"{Z}August 25th, 2026{Z}, at {Z}4:00 p.m.{Z} in {Z}City Commission Chambers "
        f"at City Hall, 241 W. South Street{Z}, has been CANCELLED due to {Z}the lack "
        f"of agenda items{Z}.",
        (("NFP",), (("cancelled", "2026-08-25"),)),
    ),
    (
        "HDC special meeting, heading names the wrong board",
        "Historic Preservation Commission Special Meeting on September 2 The "
        "Historic District Commission will meet in special session on Wednesday, "
        "September 2, 2026, at 5:00 p.m. This meeting will take place in the City "
        "Commission Chambers at City Hall, 241 W. South Street. The purpose of the "
        "meeting is to conduct business originally scheduled for the August 18, 2026 "
        "meeting which was cancelled.",
        (("HDC",), (("special", "2026-09-02"), ("cancelled", "2026-08-18"))),
    ),
    (
        "CPSRAB cancellation",
        f"CPSRAB September Meeting Cancelled The meeting of the {Z}Citizen Public "
        f"Safety Review and Appeal Board{Z} scheduled for {Z}Tuesday{Z}, "
        f"{Z}September 8, 2026{Z}, at {Z}6:00 p.m.{Z} in {Z}City Commission Chambers "
        f"at City Hall, 241 W. South Street{Z}, has been CANCELLED.",
        (("CPSRAB",), (("cancelled", "2026-09-08"),)),
    ),
    (
        "DEGA special session",
        "Downtown Economic Growth Authority Board Meeting on September 1 The "
        "Downtown Economic Growth Authority Board will meet in special session on "
        "Tuesday, September 1, 2026, at 8:00 a.m. This meeting will take place in "
        "the Community Room at City Hall, 241 W. South Street. The purpose of the "
        "meeting is a grant discussion.",
        (("DEGA",), (("special", "2026-09-01"),)),
    ),
    (
        "CRB special session",
        "Civil Rights Board Special Meeting on September 2 The Civil Rights Board "
        "will meet in special session on Wednesday, September 2, 2026, at 5:00 p.m. "
        "This meeting will take place in Community Room at City Hall, 241 W. South "
        "Street. The purpose of the meeting is to vote on new board member and to "
        "announce open board position.",
        (("CRB",), (("special", "2026-09-02"),)),
    ),
]


# ---------------------------------------------------------------------------
# Wordings the city has not used yet
# ---------------------------------------------------------------------------

PLAUSIBLE = [
    (
        "cancellation phrased as will not be held",
        "Traffic Board October Meeting Cancelled The meeting of the Traffic Board "
        "scheduled for Monday, October 5, 2026, at 3:00 p.m. will not be held.",
        (("TRB",), (("cancelled", "2026-10-05"),)),
    ),
    (
        "location change only",
        "Tree Committee Location Change The meeting of the Tree Committee scheduled "
        "for Wednesday, October 7, 2026 has been MOVED to meet in the Community Room "
        "at City Hall, 241 W. South Street.",
        (("TRE",), (("location_change", "2026-10-07"),)),
    ),
    (
        "postponed until",
        "Planning Commission Meeting Postponed The meeting of the Planning Commission "
        "scheduled for Thursday, October 1, 2026 has been postponed until Thursday, "
        "October 15, 2026, at 7:00 p.m.",
        (("PC",), (("rescheduled", "2026-10-01", "2026-10-15"),)),
    ),
    (
        "new date written before the old one",
        "Environmental Concerns Committee The Environmental Concerns Committee will "
        "now meet on Wednesday, November 4, 2026 instead of Tuesday, November 3, "
        "2026, at 6:00 p.m.",
        (("ECC",), (("rescheduled", "2026-11-03", "2026-11-04"),)),
    ),
    (
        "joint boards named in one sentence",
        "BRA and EDC Special Meeting The meeting of the Brownfield Redevelopment "
        "Authority and Economic Development Corporation will meet in special session "
        "on Tuesday, October 20, 2026, at 8:00 a.m. in the Community Room.",
        (("BRA", "EDC"), (("special", "2026-10-20"),)),
    ),
    (
        "special meeting replacing a cancelled one",
        "Pension Board Special Meeting The Pension Board will meet in special session "
        "on October 8, 2026 at 9:00 a.m. to conduct business from the September 10, "
        "2026 meeting, which was cancelled.",
        (("ERSB",), (("special", "2026-10-08"), ("cancelled", "2026-09-10"))),
    ),
    (
        "board named only by its acronym",
        "CPSRAB Meeting Cancelled The CPSRAB meeting scheduled for Tuesday, "
        "October 13, 2026 has been CANCELLED.",
        (("CPSRAB",), (("cancelled", "2026-10-13"),)),
    ),
]


# ---------------------------------------------------------------------------
# Traps. Every one of these was a real mistake at some point.
# ---------------------------------------------------------------------------

TRAPS = [
    (
        "a date in a trailing fact is not cancelled",
        "Board of Review Meeting Cancelled The meeting of the Board of Review "
        "scheduled for December 8, 2026 has been CANCELLED. The board last met on "
        "July 14, 2026.",
        (("BOR",), (("cancelled", "2026-12-08"),)),
    ),
    (
        "a date in the purpose clause is not a meeting",
        "Zoning Board of Appeals Special Meeting on October 6 The Zoning Board of "
        "Appeals will meet in special session on Tuesday, October 6, 2026, at "
        "5:00 p.m. The purpose of the meeting is to review the ordinance adopted on "
        "January 5, 2026.",
        (("ZBA",), (("special", "2026-10-06"),)),
    ),
]


# ---------------------------------------------------------------------------
# Dates on their own
# ---------------------------------------------------------------------------

DATES_READ = [
    ("August 25, 2026", "2026-08-25"),
    ("August 25th, 2026", "2026-08-25"),
    ("AUGUST 25TH, 2026", "2026-08-25"),
    ("Aug. 25 2026", "2026-08-25"),
    ("Sept 2nd, 2026", "2026-09-02"),
    ("September, 8, 2026", "2026-09-08"),
    ("the 25th of August, 2026", "2026-08-25"),
    ("Monday the 14th of September", "2026-09-14"),
    ("9/8/26", "2026-09-08"),
    ("08/25/2026", "2026-08-25"),
    ("9.15.2026", "2026-09-15"),
    ("2026-09-15", "2026-09-15"),
]

DATES_IGNORED = [
    "Sept 2026 budget hearing",
    "in June 2026 the board voted",
    "the May 5:30 p.m. session",
    "call 269-337-8000 for details",
    "241 W. South Street",
    "Room 25 at City Hall",
    "at 4:00 p.m.",
    "chapter 8/9 of the code",
    "in August, 20 people attended",
    "ordinance 5.2.1 was adopted",
    "February 30, 2026",
]


def main() -> int:
    failures = 0

    for group, cases in (("live", LIVE), ("plausible", PLAUSIBLE), ("trap", TRAPS)):
        for name, text, expected in cases:
            got = summary(notice(text))
            if got == expected:
                print(f"  pass  [{group}] {name}")
            else:
                failures += 1
                print(f"  FAIL  [{group}] {name}")
                print(f"        expected {expected}")
                print(f"        got      {got}")

    for text, expected in DATES_READ:
        got = scraper._extract_notice_dates(text, today=TODAY)
        if expected in got:
            print(f"  pass  [date] {text}")
        else:
            failures += 1
            print(f"  FAIL  [date] {text}: expected {expected}, got {got}")

    for text in DATES_IGNORED:
        got = scraper._extract_notice_dates(text, today=TODAY)
        if not got:
            print(f"  pass  [not a date] {text}")
        else:
            failures += 1
            print(f"  FAIL  [not a date] {text}: read {got}")

    print()
    if failures:
        print(f"{failures} failure(s).")
        return 1
    print("All notice tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
