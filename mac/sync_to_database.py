"""
Push roasts straight into Roast Coach's database, with no browser involved.

This is the version with nothing to click. It reads RoasTime's folder on this
computer and writes new and changed roasts directly into the same database the
app reads, so roasts simply appear — on every machine signed in, not just this
one. Chrome's rules about which folders it will accept never come into it,
because Chrome is not part of it.

    pip install -r requirements.txt
    export ROAST_COACH_DATABASE_URL="postgresql://…"      # the Session pooler URI
    python3 mac/sync_to_database.py

Then, to have it happen by itself:

    python3 mac/sync_to_database.py --every 15

…or leave it to macOS, which is tidier — see `--install`.

Options
-------
--folder PATH     where the roast files are; defaults to RoasTime's own folder
--database URL    the database; defaults to ROAST_COACH_DATABASE_URL, then to
                  whatever the app itself would use
--every MINUTES   keep running, checking that often
--install         write a launchd agent so macOS runs it every 15 minutes
--uninstall       remove that agent
--once            check once and stop (the default)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

DEFAULT_FOLDERS = [
    Path.home() / "Library/Application Support/roast-time/roasts",   # macOS
    Path(os.environ.get("APPDATA", "")) / "roast-time" / "roasts",    # Windows
    Path.home() / "Documents" / "RoastCoach",                         # a copy
]

LABEL = "com.roastcoach.database-sync"
AGENT = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

# The connection string is a password. It lives in a file only this account can
# read — never in the launch agent, where anything listing processes could see it.
CONFIG = Path.home() / ".roastcoach" / "database"


def remember_database(url: str) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(url.strip() + "\n")
    CONFIG.chmod(0o600)


def stored_database() -> str | None:
    try:
        return CONFIG.read_text().strip() or None
    except OSError:
        return None


def find_folder(given: str | None) -> Path:
    if given:
        folder = Path(given).expanduser()
        if not folder.is_dir():
            raise SystemExit(f"No folder at {folder}")
        return folder
    for candidate in DEFAULT_FOLDERS:
        if candidate.is_dir():
            return candidate
    raise SystemExit(
        "Could not find a roasts folder. Pass one:\n"
        "  python3 mac/sync_to_database.py --folder '~/Library/Application Support/"
        "roast-time/roasts'")


def gather(folder: Path, known: dict) -> list[dict]:
    """Every roast file that is new, or whose size or timestamp has moved."""
    files = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in (".json", ".csv", ""):
            continue
        stat = path.stat()
        previous = known.get(path.name)
        if previous and abs(previous[0] - stat.st_mtime) < 1 and previous[1] == stat.st_size:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as problem:
            print(f"  could not read {path.name}: {problem}")
            continue
        files.append({"name": path.name, "text": text,
                      "modified": stat.st_mtime, "size": stat.st_size})
    return files


def sync_companions(roasts_folder: Path) -> dict:
    """Send the bean, recipe and machine files that sit beside the roasts.

    RoasTime keeps them in sibling folders. They are small and they change
    rarely, so they are simply re-sent each time rather than tracked.
    """
    from roastcoach import library

    # RoasTime keeps them beside the roasts folder. A folder copy — the
    # roast-sync route — may instead have them inside it, or not at all, so look
    # in both places and say plainly when there is nothing to find.
    places = [roasts_folder.parent, roasts_folder]
    # A roasts folder that was *copied* somewhere has no beans/ beside it. The
    # real ones are still where RoasTime keeps them, so look there too rather
    # than reporting nothing and leaving every roast without a coffee.
    for known in (Path.home() / "Library/Application Support/roast-time",
                  Path.home() / "Library/Application Support/RoasTime",
                  Path(os.environ.get("APPDATA", "")) / "roast-time"):
        if known.is_dir() and known not in places:
            places.append(known)
    totals: dict[str, int] = {}
    found_any = False
    for name, kind in library.KINDS.items():
        folder = next((place / name for place in places if (place / name).is_dir()), None)
        if folder is None:
            continue
        found_any = True
        files = []
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in (".json", ""):
                continue
            try:
                files.append({"name": path.name,
                              "text": path.read_text(encoding="utf-8-sig", errors="replace")})
            except OSError:
                continue
        if files:
            stored = library.add_records(kind, files)["stored"]
            totals[kind] = totals.get(kind, 0) + stored

    if not found_any:
        print(f"{time.strftime('%H:%M')}  no beans/ or recipes/ folder beside "
              f"{roasts_folder} — roasts will import, but they will have no bean to be "
              f"grouped by. Point --folder at RoasTime's own roasts folder:")
        print("          ~/Library/Application Support/roast-time/roasts")
    return totals


def audit(folder: Path, progress=None) -> dict:
    """Check every roast file on this Mac against what the database made of it.

    "Is all the data actually syncing?" deserves an answer with numbers in it,
    not a reassurance. This is the only place both halves can be seen at once —
    RoasTime's files are here and nowhere else, and the database is a query away
    — so it reads each roast file, finds what it points at, and asks the same
    three questions of every one:

    1. Is this roast in the database at all?
    2. Does the file name a bean and a recipe?
    3. Did the app end up showing them?

    Every roast that fails any of those is listed by name, with which step it
    failed at, so the answer is never "something is wrong somewhere".
    """
    import json as _json

    from roastcoach import library, store

    frame = store.load_roasts()
    stored = {}
    if not frame.empty:
        for row in frame.to_dict("records"):
            stored[str(row.get("uid"))] = row

    tables = library.tables()
    coffees = library.coffee_lookup(tables)
    recipes = tables.get("recipe") or {}
    aliases = library.id_index(recipes)

    report = {"files": 0, "unreadable": 0, "in_database": 0, "missing": 0,
              "recipe_on_file": 0, "recipe_stored": 0, "recipe_shown": 0,
              "bean_on_file": 0, "bean_stored": 0, "bean_shown": 0,
              "no_recipe_id": [], "recipe_not_stored": [], "recipe_not_shown": [],
              "not_in_database": [], "spellings": {}}

    paths = [path for path in sorted(folder.iterdir())
             if path.is_file() and not path.name.startswith(".")
             and path.suffix.lower() in (".json", ".csv", "")]

    for position, path in enumerate(paths):
        if progress:
            progress(position + 1, len(paths))
        try:
            record = _json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
        except Exception:
            report["unreadable"] += 1
            continue
        if not isinstance(record, dict) or "uid" not in record:
            report["unreadable"] += 1
            continue

        report["files"] += 1
        uid = str(record["uid"])
        name = str(record.get("roastName") or uid)[:40]
        row = stored.get(uid)
        if row is None:
            report["missing"] += 1
            report["not_in_database"].append(name)
            continue
        report["in_database"] += 1

        # Which spelling this file uses for its recipe link, whatever it is.
        for key in record:
            if "recipe" in str(key).lower() and not isinstance(record[key], (list, dict)):
                report["spellings"][key] = report["spellings"].get(key, 0) + 1

        recipe_id = library.link_id(record, "recipe")
        if recipe_id:
            report["recipe_on_file"] += 1
            if recipe_id in aliases:
                report["recipe_stored"] += 1
                if str(row.get("recipe_name") or "").strip():
                    report["recipe_shown"] += 1
                else:
                    report["recipe_not_shown"].append(f"{name} → {recipe_id}")
            else:
                report["recipe_not_stored"].append(f"{name} → {recipe_id}")
        else:
            # RoasTime shows a recipe for these too, so it is holding the link
            # somewhere other than the roast file.
            if not str(row.get("recipe_name") or "").strip():
                report["no_recipe_id"].append(name)

        bean_id = library.link_id(record, "bean")
        if bean_id:
            report["bean_on_file"] += 1
            if bean_id in coffees:
                report["bean_stored"] += 1
                if str(row.get("bean") or "").strip():
                    report["bean_shown"] += 1

    report["roasts_in_database"] = len(stored)
    report["extra_in_database"] = max(0, len(stored) - report["in_database"])
    return report


def sync_once(folder: Path, database: str | None, again: bool = False) -> dict:
    # What was asked for, then what the environment says, then what this Mac has
    # saved. The saved one is a preference, not an instruction: an environment
    # that has already named a database — ROAST_COACH_DB for a file, or the URL
    # itself — is a deliberate choice for this run and must not be overruled by a
    # setting from months ago.
    url = database or os.environ.get("ROAST_COACH_DATABASE_URL")
    if not url and not os.environ.get("ROAST_COACH_DB"):
        url = stored_database()
    if url:
        os.environ["ROAST_COACH_DATABASE_URL"] = url

    from roastcoach import coach, db, learning, store

    companions = sync_companions(folder)
    if companions:
        print(f"{time.strftime('%H:%M')}  " +
              ", ".join(f"{count} {kind}(s)" for kind, count in sorted(companions.items())))

    # What the database holds now, not what this run happened to send. "Did the
    # beans arrive?" is the question every blank Bean column asks, and it should
    # not need a trip to a web page to answer.
    from roastcoach import library

    held = library.counts()
    print(f"{time.strftime('%H:%M')}  the database holds " +
          (", ".join(f"{count} {kind}(s)" for kind, count in sorted(held.items()))
           if held else "no bean, recipe or container records at all — "
                        "every roast will show an empty Bean and Recipe"))

    # Roasts measured by an older version of the app still carry its numbers.
    # Nothing needs re-importing — the curves are stored — so put them right here,
    # where nobody is waiting for it.
    behind = store.outdated()
    if behind:
        print(f"{time.strftime('%H:%M')}  re-measuring {behind} roast(s) with the "
              f"current calculations…")
        store.remeasure()

    # A roast read by an older importer is missing whatever that version threw
    # away — for this roaster, the `recipeID` field, and with it every recipe name
    # in the app. Only the file has it back, so the sync notices and reads them
    # again by itself. Nobody should have to know to ask for that.
    behind_import = 0
    try:
        behind_import = store.unread()
    except Exception:
        behind_import = 0
    if behind_import and not again:
        print(f"{time.strftime('%H:%M')}  {behind_import} roast(s) were read by an "
              f"earlier version of the importer, which kept fewer fields — reading "
              f"their files again so the rest arrives.")
        again = True

    # `--again` reads every roast file over, rather than only what has changed.
    # Nothing you have typed is touched — a roast is matched by its own id and
    # updated in place — but fields the app did not use to keep, such as the link
    # to a recipe, arrive for roasts imported before it knew to look.
    known = {} if again else store.known_sources()
    if again:
        print(f"{time.strftime('%H:%M')}  reading every roast file again, not only "
              f"what has changed — this takes a few minutes.")
    files = gather(folder, known)
    if not files:
        print(f"{time.strftime('%H:%M')}  nothing new in {folder.name} "
              f"({len(known)} roast file(s) already in {db.describe()})")
        return {"added": 0, "updated": 0, "skipped": 0, "failed": 0, "problems": []}

    print(f"{time.strftime('%H:%M')}  reading {len(files)} file(s) from {folder.name}…")
    report = store.add_roasts(files, again=again)

    if report["added"] or report["updated"]:
        frame = store.load_roasts()
        learning.relearn(frame)          # keep the effect sizes current
        coach.auto_evaluate(frame)       # grade any advice this roast tests

    # Everything in the folder has just been read. Anything still stamped by an
    # older importer therefore has no file left here to read — RoasTime deletes
    # roasts it has synced away, and some of these came from a folder this Mac no
    # longer has. Say so once and stop counting them, rather than leaving a
    # warning standing that nobody can act on.
    if again:
        left = store.unread()
        if left:
            sealed = store.seal_unread(
                note=f"no file in {folder} when read again on {time.strftime('%Y-%m-%d')}")
            print(f"{time.strftime('%H:%M')}  {sealed} roast(s) have no file here any "
                  f"more, so they keep what they already had. They will not be asked "
                  f"about again.")

    parts = []
    if report["added"]:
        parts.append(f"{report['added']} new")
    if report["updated"]:
        parts.append(f"{report['updated']} updated")
    if report["skipped"]:
        parts.append(f"{report['skipped']} unchanged")
    if report["failed"]:
        parts.append(f"{report['failed']} not roast files")
    print(f"          {', '.join(parts) or 'nothing to do'} → {db.describe()}")
    for problem in report["problems"][:5]:
        print(f"          {problem}")
    return report


def install(folder: Path, database: str | None) -> None:
    if sys.platform != "darwin":
        raise SystemExit("--install writes a macOS launch agent; this is not macOS.")
    AGENT.parent.mkdir(parents=True, exist_ok=True)
    if database:
        remember_database(database)      # kept out of the agent on purpose
    arguments = [sys.executable, str(Path(__file__).resolve()), "--folder", str(folder)]
    body = "\n".join(f"    <string>{value}</string>" for value in arguments)
    AGENT.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{body}
  </array>
  <key>StartInterval</key>
  <integer>900</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{Path.home()}/Library/Logs/roast-coach-sync.log</string>
  <key>StandardErrorPath</key>
  <string>{Path.home()}/Library/Logs/roast-coach-sync.log</string>
</dict>
</plist>
""")
    import subprocess

    def run(*command) -> tuple[bool, str]:
        finished = subprocess.run(command, capture_output=True, text=True)
        return finished.returncode == 0, (finished.stderr or finished.stdout).strip()

    run("launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}")
    started, complaint = run("launchctl", "bootstrap", f"gui/{os.getuid()}", str(AGENT))
    if not started:
        started, older = run("launchctl", "load", str(AGENT))
        complaint = complaint or older

    if started:
        print("Installed.")
        print(f"  Every 15 minutes and at login, roasts in {folder} go into the database.")
        print("  Record of each run: ~/Library/Logs/roast-coach-sync.log")
        print(f"  To stop: python3 {Path(__file__).name} --uninstall")
        return

    # Say what actually went wrong rather than a shrug, and offer the other
    # scheduler, which needs no approval from macOS.
    print("macOS would not start the schedule.")
    if complaint:
        print(f"  It said: {complaint}")
    if fall_back_to_cron(folder):
        print("Set up the older way instead — same result, every 15 minutes.")
        print("  To stop it: crontab -e, and delete the roast-coach line.")
    else:
        print("  Your roasts did go across. Run this again after roasting, or use")
        print("  --every 15 to leave it running in a Terminal window.")


def fall_back_to_cron(folder: Path) -> bool:
    """launchd sometimes refuses in ways only Apple understands. cron does not."""
    import subprocess

    line = (f"*/15 * * * * {sys.executable} {Path(__file__).resolve()} "
            f'--folder "{folder}" >> {Path.home()}/Library/Logs/roast-coach-sync.log 2>&1'
            "  # roast-coach")
    try:
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        kept = [row for row in existing.splitlines() if "# roast-coach" not in row]
        written = "\n".join(kept + [line]) + "\n"
        return subprocess.run(["crontab", "-"], input=written, text=True,
                              capture_output=True).returncode == 0
    except Exception:
        return False


def uninstall() -> None:
    import subprocess

    os.system(f'launchctl bootout "gui/{os.getuid()}/{LABEL}" 2>/dev/null')
    os.system(f'launchctl unload "{AGENT}" 2>/dev/null')
    AGENT.unlink(missing_ok=True)
    try:                                     # and the cron fallback, if it was used
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        kept = [row for row in existing.splitlines() if "# roast-coach" not in row]
        if len(kept) != len(existing.splitlines()):
            subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n", text=True,
                           capture_output=True)
    except Exception:
        pass
    print("Stopped. Nothing runs on a schedule any more.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--folder")
    parser.add_argument("--database")
    parser.add_argument("--every", type=float, metavar="MINUTES")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--again", action="store_true",
                        help="read every roast file over, not only what changed")
    parser.add_argument("--check", action="store_true",
                        help="compare every file here against what the database made "
                             "of it, and report anything that did not arrive")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    options = parser.parse_args()

    if options.uninstall:
        uninstall()
        return

    folder = find_folder(options.folder)
    if options.install:
        install(folder, options.database)
        return

    if options.check:
        url = options.database or os.environ.get("ROAST_COACH_DATABASE_URL")
        if not url and not os.environ.get("ROAST_COACH_DB"):
            url = stored_database()
        if url:
            os.environ["ROAST_COACH_DATABASE_URL"] = url
        found = audit(folder)
        print(f"\n{found['files']} roast file(s) here · "
              f"{found['roasts_in_database']} roast(s) in the database")
        print(f"  in the database:        {found['in_database']}")
        print(f"  missing from it:        {found['missing']}")
        print(f"  recipe id on the file:  {found['recipe_on_file']}")
        print(f"    that recipe stored:   {found['recipe_stored']}")
        print(f"    showing in the app:   {found['recipe_shown']}")
        print(f"  bean id on the file:    {found['bean_on_file']}")
        print(f"    showing in the app:   {found['bean_shown']}")
        if found["spellings"]:
            print("  recipe field spellings: "
                  + ", ".join(f"{key} ×{count}"
                              for key, count in sorted(found["spellings"].items())))
        for label, key in (("not in the database", "not_in_database"),
                           ("recipe file never arrived", "recipe_not_stored"),
                           ("recipe stored but not shown", "recipe_not_shown"),
                           ("no recipe id on the file at all", "no_recipe_id")):
            items = found[key]
            if items:
                print(f"\n  {len(items)} {label}:")
                for item in items[:10]:
                    print(f"    {item}")
                if len(items) > 10:
                    print(f"    …and {len(items) - 10} more")
        return

    if not options.every:
        sync_once(folder, options.database, again=options.again)
        return

    print(f"Watching {folder} — every {options.every:g} minutes. Ctrl-C to stop.")
    while True:
        try:
            sync_once(folder, options.database)
        except Exception as problem:            # a dropped connection must not end it
            print(f"          {type(problem).__name__}: {problem}")
        time.sleep(max(30.0, options.every * 60))


if __name__ == "__main__":
    main()
