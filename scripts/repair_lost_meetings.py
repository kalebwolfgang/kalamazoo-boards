"""One-time repair for meetings that were dropped, plus the HPC phantoms.

WHERE THIS GOES: scripts/repair_lost_meetings.py
Run it once from the repo root, then delete it:

    python3 scripts/repair_lost_meetings.py --dry-run
    python3 scripts/repair_lost_meetings.py
    python3 scripts/build.py

WHAT IT DOES

1. Walks the git history of every data/<board>.json, finds meetings that were
   once on the site, have since passed, and are in neither the archive nor the
   upcoming list, and puts them back in the archive.

   A date another meeting was rescheduled away from is skipped. That meeting
   did not happen on that day, it moved, and the new date is already recorded.

2. Retires any meeting still sitting in an upcoming list after its date has
   passed. Those are the records the new rule would move on the next scraper
   run; doing it here means the duplicate-date check in build.py passes right
   away. If the archive already holds the date, the two are folded into one.

3. Removes meetings written onto the wrong board: two on HPC from a notice
   that named the Historic Preservation Commission in its heading and the
   Historic District Commission in its body, and one on DDA copied from a
   special session that only DEGA was called to.

Nothing is deleted except those two phantom rows, and the script prints every
change before making it.
"""

import argparse
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

DATA_DIR = Path("data")
SKIP_FILES = {"calendar.json", "meta.json", "state.json", "changelog.json",
              "notices.json"}

# Meetings written onto the wrong board and never held there.
PHANTOMS = {
    # Written onto HPC by the September 2 notice, which named the Historic
    # Preservation Commission in its heading and the Historic District
    # Commission in its body. Both dates are HDC's.
    "hpc": ["2026-08-18", "2026-09-02"],
    # Copied onto DDA from a notice that called a special session of DEGA
    # alone. DDA never had a meeting that day.
    "dda": ["2026-09-01"],
}


def git_show(commit: str, path: str) -> dict | None:
    out = subprocess.run(["git", "show", f"{commit}:{path}"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def commits_for(path: str) -> list[str]:
    return subprocess.run(["git", "log", "--format=%H", "--", path],
                          capture_output=True, text=True).stdout.split()


def display_for(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%A, %B %-d, %Y")


def find_lost(path: Path, today: str) -> dict[str, dict]:
    """Meetings that were once listed, have passed, and are recorded nowhere."""
    commits = commits_for(str(path))
    if not commits:
        return {}

    current = json.loads(path.read_text(encoding="utf-8"))
    archived = {m.get("date") for m in current.get("meetings", [])}
    upcoming = {m.get("date") for m in current.get("upcoming_meetings", [])}

    seen: dict[str, dict] = {}
    vacated: set[str] = set()

    for commit in commits:
        data = git_show(commit, str(path))
        if not data:
            continue
        for m in data.get("upcoming_meetings", []):
            iso = m.get("date")
            if m.get("rescheduledFrom"):
                vacated.add(m["rescheduledFrom"])
            if iso and iso < today:
                # git log runs newest first, so setdefault keeps the LAST
                # state the meeting was in before it dropped off the site.
                # That version carries any cancellation, location or
                # reschedule note added along the way.
                seen.setdefault(iso, m)

    return {
        iso: entry for iso, entry in sorted(seen.items())
        if iso not in archived and iso not in upcoming and iso not in vacated
    }


def restore(data: dict, entry: dict, source_url: str | None) -> dict:
    record = {
        "date":        entry["date"],
        "display":     entry.get("display") or display_for(entry["date"]),
        "minutes_url": None,
        "agenda_url":  None,
        "location":    entry.get("location"),
        "scrapedAt":   datetime.now(timezone.utc).isoformat(),
        "sourceUrl":   source_url,
    }
    if entry.get("isCancelled"):
        record["isCancelled"] = True
        record["link_label"]  = "Cancelled"
    else:
        record["link_label"] = "No documents posted"
    # Keep the note saying this meeting moved, so the date it moved away from
    # is never mistaken for a meeting that went missing.
    if entry.get("rescheduledFrom"):
        record["rescheduledFrom"] = entry["rescheduledFrom"]
    data.setdefault("meetings", []).append(record)
    data["meetings"].sort(key=lambda m: m.get("date", ""), reverse=True)
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would change and write nothing")
    args = parser.parse_args()

    today = date.today().strftime("%Y-%m-%d")
    restored_count = 0
    removed_count = 0
    folded_count = 0

    for path in sorted(DATA_DIR.glob("*.json")):
        if path.name in SKIP_FILES:
            continue

        board_key = path.stem
        data = json.loads(path.read_text(encoding="utf-8"))
        changed = False

        lost = find_lost(path, today)
        # A phantom is in the history too. Without this, running the script a
        # second time would put back the meetings the first run removed.
        for iso in PHANTOMS.get(board_key, []):
            lost.pop(iso, None)
        for iso, entry in lost.items():
            label = " (cancelled)" if entry.get("isCancelled") else ""
            print(f"  restore  {board_key:8s} {iso}{label}")
            restore(data, entry, data.get("metadata", {}).get("web_url"))
            restored_count += 1
            changed = True

        # Phantoms first: a meeting that was never this board's should not
        # be archived on its way out.
        for iso in PHANTOMS.get(board_key, []):
            before = len(data.get("upcoming_meetings", []))
            remaining = [m for m in data.get("upcoming_meetings", [])
                         if m.get("date") != iso]
            if len(remaining) != before:
                print(f"  remove   {board_key:8s} {iso}  (never this board's meeting)")
                removed_count += 1
                changed = True
                data["upcoming_meetings"] = remaining

        # Fold passed meetings out of the upcoming list.
        still_upcoming = []
        archived_by_date = {m.get("date"): m for m in data.get("meetings", [])}
        for entry in data.get("upcoming_meetings", []):
            iso = entry.get("date")
            if not iso or iso >= today:
                still_upcoming.append(entry)
                continue

            archived = archived_by_date.get(iso)
            if archived is not None:
                note = ""
                if entry.get("isCancelled") and not archived.get("isCancelled"):
                    note = " (marking the archived record cancelled)"
                    archived["isCancelled"] = True
                print(f"  fold     {board_key:8s} {iso}  already archived{note}")
            else:
                print(f"  retire   {board_key:8s} {iso}  to archive")
                restore(data, entry, data.get("metadata", {}).get("web_url"))
                archived_by_date[iso] = data["meetings"][0]
            folded_count += 1
            changed = True

        data["upcoming_meetings"] = still_upcoming

        if changed and not args.dry_run:
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

    print()
    print(f"{restored_count} meeting(s) restored, {folded_count} passed meeting(s) "
          f"retired from upcoming lists, {removed_count} phantom(s) removed.")
    if args.dry_run:
        print("Dry run: nothing was written.")
    else:
        print("Run scripts/build.py next to validate and rebuild the calendar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
