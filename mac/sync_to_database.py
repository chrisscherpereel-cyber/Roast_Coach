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

    parent = roasts_folder.parent
    totals: dict[str, int] = {}
    for name, kind in library.KINDS.items():
        folder = parent / name
        if not folder.is_dir():
            continue
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
    return totals


def sync_once(folder: Path, database: str | None) -> dict:
    url = database or os.environ.get("ROAST_COACH_DATABASE_URL") or stored_database()
    if url:
        os.environ["ROAST_COACH_DATABASE_URL"] = url

    from roastcoach import coach, db, learning, store

    companions = sync_companions(folder)
    if companions:
        print(f"{time.strftime('%H:%M')}  " +
              ", ".join(f"{count} {kind}(s)" for kind, count in sorted(companions.items())))

    known = store.known_sources()
    files = gather(folder, known)
    if not files:
        print(f"{time.strftime('%H:%M')}  nothing new in {folder.name} "
              f"({len(known)} roast file(s) already in {db.describe()})")
        return {"added": 0, "updated": 0, "skipped": 0, "failed": 0, "problems": []}

    print(f"{time.strftime('%H:%M')}  reading {len(files)} file(s) from {folder.name}…")
    report = store.add_roasts(files)

    if report["added"] or report["updated"]:
        frame = store.load_roasts()
        learning.relearn(frame)          # keep the effect sizes current
        coach.auto_evaluate(frame)       # grade any advice this roast tests

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

    if not options.every:
        sync_once(folder, options.database)
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
