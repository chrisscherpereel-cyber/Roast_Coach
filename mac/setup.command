#!/bin/bash
#
# Roast Coach — set up automatic roast syncing on this Mac.
#
#   Double-click this file. Answer one question. Done.
#
# Afterwards, every roast you pull from the Bullet turns up in Roast Coach on its
# own, on every computer signed in — no uploading, no folder picking, nothing to
# remember. This is the only time you run it.
#
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(dirname "$HERE")"

# Files that came out of a download carry a quarantine mark, and unzipping can
# lose the permission that makes them runnable. Fix both, for these and for the
# neighbours, so nothing else in here trips over the same thing.
chmod +x "$HERE"/*.command "$HERE"/*.py 2>/dev/null
xattr -d com.apple.quarantine "$HERE"/*.command "$HERE"/*.py 2>/dev/null
CONFIG="$HOME/.roastcoach/database"
ROASTS="$HOME/Library/Application Support/roast-time/roasts"

say() { printf '%s\n' "$1"; }
stop() { say ""; read -r -p "Press Return to close. "; exit "${1:-1}"; }

clear
say "Roast Coach — automatic roast syncing"
say "====================================="
say ""

# ---------------------------------------------------------------- 1. Python
if ! command -v python3 >/dev/null 2>&1; then
  say "This needs Python, which your Mac does not have yet."
  say ""
  say "A window will open asking to install the developer tools. Say Install,"
  say "wait for it to finish, then double-click this file again."
  xcode-select --install 2>/dev/null
  stop 1
fi
say "✓ Python found — $(python3 --version 2>&1)"

# ------------------------------------------------------------- 2. the roasts
if [ -d "$ROASTS" ]; then
  COUNT=$(find "$ROASTS" -maxdepth 1 -type f \( -name '*.json' -o -name '*.csv' \) | wc -l | tr -d ' ')
  say "✓ Found your roasts — $COUNT file(s) in RoasTime's folder"
else
  say "✗ Cannot find RoasTime's roasts at:"
  say "    $ROASTS"
  say ""
  say "Is RoasTime installed on this Mac, and have you roasted at least once?"
  stop 1
fi

# --------------------------------------------------------- 3. the database
say ""
if [ -f "$CONFIG" ]; then
  say "A database is already saved on this Mac."
  read -r -p "Use it? [Y/n] " keep
  if [ "$keep" = "n" ] || [ "$keep" = "N" ]; then rm -f "$CONFIG"; fi
fi

if [ ! -f "$CONFIG" ]; then
  say "Paste your database connection string."
  say ""
  say "  In Supabase: your project → Connect → Session pooler → copy the URI,"
  say "  and replace [YOUR-PASSWORD] with your database password."
  say ""
  say "  It starts with postgresql:// and is one long line."
  say ""
  read -r -p "Paste it here, then press Return: " URL
  URL="$(printf '%s' "$URL" | tr -d '[:space:]')"
  case "$URL" in
    postgres://*|postgresql://*) ;;
    *) say ""; say "That does not look like a connection string — it should start"
       say "with postgresql://. Nothing was saved; run this again."; stop 1 ;;
  esac
  case "$URL" in
    *YOUR-PASSWORD*) say ""; say "The [YOUR-PASSWORD] placeholder is still in there."
       say "Replace it with your actual database password and run this again."; stop 1 ;;
  esac
  mkdir -p "$HOME/.roastcoach"
  printf '%s\n' "$URL" > "$CONFIG"
  chmod 600 "$CONFIG"
  say ""
  say "✓ Saved, readable only by you"
fi

# ------------------------------------------------------------ 4. the pieces
say ""
say "Installing what it needs (a minute or two the first time)…"
if ! python3 -m pip install --user --quiet --disable-pip-version-check \
     -r "$APP/requirements.txt" 2>/dev/null; then
  say ""
  say "✗ Could not install the Python packages."
  say "  Try it yourself and see what it says:"
  say "    python3 -m pip install --user -r \"$APP/requirements.txt\""
  stop 1
fi
say "✓ Installed"

# -------------------------------------------------------- 5. the first sync
say ""
say "Sending your roasts across for the first time…"
say ""
if ! python3 "$HERE/sync_to_database.py" --folder "$ROASTS" 2>&1 | sed 's/^/    /'; then
  say ""
  say "✗ That did not work. The message above says why."
  say "  Most often it is the connection string — check SETUP.md."
  stop 1
fi

# ----------------------------------------------------------- 6. keep it up
say ""
read -r -p "Keep doing this automatically, every 15 minutes? [Y/n] " repeat
if [ "$repeat" = "n" ] || [ "$repeat" = "N" ]; then
  say ""
  say "Fine. Double-click this file whenever you want to send new roasts."
  stop 0
fi

if python3 "$HERE/sync_to_database.py" --folder "$ROASTS" --install 2>&1 | sed 's/^/    /'; then
  say ""
  say "───────────────────────────────────────────────────────"
  say "Done. Nothing else to do, ever."
  say ""
  say "Roast on the Bullet, and within fifteen minutes the roast is in"
  say "Roast Coach — on this Mac and on everyone else's."
  say ""
  say "  To check on it:  open ~/Library/Logs/roast-coach-sync.log"
  say "  To stop it:      python3 \"$HERE/sync_to_database.py\" --uninstall"
  say "───────────────────────────────────────────────────────"
else
  say ""
  say "The roasts went across, but the schedule would not start."
  say "Double-click this file after roasting and it sends them again."
fi
stop 0
