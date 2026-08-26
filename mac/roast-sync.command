#!/bin/bash
#
# Roast Coach — keep a copy of RoasTime's roasts somewhere the browser will take.
#
#   Double-click this file.
#
# It copies every roast out of ~/Library/Application Support/roast-time/roasts
# into ~/Documents/RoastCoach, and offers to repeat that by itself every fifteen
# minutes. After that, "Choose folder…" in Roast Coach always works and is always
# current, because the folder it points at is an ordinary one.
#
# Nothing is ever written back to RoasTime's folder. This only reads.
#
set -u

SOURCE="$HOME/Library/Application Support/roast-time/roasts"
TARGET="$HOME/Documents/RoastCoach"
LABEL="com.roastcoach.sync"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

copy_them() {
  if [ ! -d "$SOURCE" ]; then
    echo "Cannot find RoasTime's roasts folder at:"
    echo "  $SOURCE"
    echo
    echo "If RoasTime keeps them somewhere else, edit SOURCE at the top of this file."
    return 1
  fi
  if ! mkdir -p "$TARGET"; then
    echo "Could not create $TARGET."
    echo "If macOS asked whether Terminal may use your Documents folder, say yes."
    return 1
  fi

  # Timestamps have to survive the copy: they are how the app tells a roast it
  # has already read from one that has changed. -a and -p both keep them.
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --include='*.json' --include='*.csv' --exclude='*' "$SOURCE/" "$TARGET/" || return 1
  else
    copied=0
    for file in "$SOURCE"/*.json "$SOURCE"/*.csv; do
      [ -e "$file" ] || continue
      cp -p "$file" "$TARGET/" || return 1
      copied=$((copied + 1))
    done
    [ "$copied" -gt 0 ] || { echo "No .json or .csv roast files in $SOURCE."; return 1; }
  fi

  count=$(find "$TARGET" -maxdepth 1 -type f \( -name '*.json' -o -name '*.csv' \) | wc -l)
  echo "$(date '+%Y-%m-%d %H:%M')  $(echo "$count" | tr -d ' ') roast file(s) in $TARGET"
}

# Called by the scheduler with --quiet: just copy, say one line, leave.
if [ "${1:-}" = "--quiet" ]; then
  copy_them
  exit $?
fi

echo
echo "Roast Coach — roasts folder sync"
echo "--------------------------------"
echo
copy_them || { echo; read -r -p "Press Return to close. "; exit 1; }
echo
echo "Copied. In Roast Coach, press Choose folder… and pick:"
echo "  Documents → RoastCoach"
echo

if [ -f "$AGENT" ]; then
  echo "This is already set to repeat every 15 minutes."
  echo
  read -r -p "Stop it repeating? [y/N] " stop
  if [ "$stop" = "y" ] || [ "$stop" = "Y" ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$AGENT" 2>/dev/null
    rm -f "$AGENT"
    echo "Stopped. Double-click this file whenever you want to copy by hand."
  fi
  echo
  read -r -p "Press Return to close. "
  exit 0
fi

read -r -p "Repeat this automatically every 15 minutes? [Y/n] " repeat
if [ "$repeat" = "n" ] || [ "$repeat" = "N" ]; then
  echo "Fine — double-click this file after roasting and it copies again."
  echo
  read -r -p "Press Return to close. "
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$SCRIPT</string>
    <string>--quiet</string>
  </array>
  <key>StartInterval</key>
  <integer>900</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$HOME/Library/Logs/roast-coach-sync.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/Library/Logs/roast-coach-sync.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
if launchctl bootstrap "gui/$(id -u)" "$AGENT" 2>/dev/null \
   || launchctl load "$AGENT" 2>/dev/null; then
  echo
  echo "Done. Every 15 minutes, and at login, your roasts are copied to"
  echo "  $TARGET"
  echo
  echo "You never have to run this again. In Roast Coach just press Choose folder…"
  echo "and pick Documents → RoastCoach."
  echo
  echo "A record of each run is in ~/Library/Logs/roast-coach-sync.log"
  echo "To stop it, double-click this file again."
else
  echo
  echo "Could not start the scheduler. The copy above still worked — double-click"
  echo "this file after roasting and it will copy again."
fi

echo
if [ ! -w "$TARGET" ]; then
  echo "If macOS asked whether Terminal may use your Documents folder, say yes —"
  echo "that is what lets the copy land there."
  echo
fi
read -r -p "Press Return to close. "
