#!/bin/bash
#
# Roast Coach — the Mac sync, as a page.
#
#   Double-click this file.
#
# It opens a small page in your browser with a Sync now button, what this Mac
# has, what the database holds, and a switch to have macOS do it every fifteen
# minutes. Everything runs on this computer; nothing is uploaded anywhere except
# the database you point it at.
#
# Nothing is ever written back to RoasTime's folder. This only reads.
#
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
cd "$ROOT" || exit 1

PY=""
for candidate in python3 /usr/local/bin/python3 /opt/homebrew/bin/python3 /usr/bin/python3; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done

if [ -z "$PY" ]; then
  echo "Python 3 is not installed."
  echo
  echo "Install it from https://www.python.org/downloads/macos/ — the standard"
  echo "installer is fine — then double-click this file again."
  echo
  read -r -p "Press Return to close. " _
  exit 1
fi

# Everything this page needs, installed into the user's own Python. Quiet unless
# something goes wrong, because a wall of pip output looks like a failure.
if ! "$PY" -c "import streamlit, sqlalchemy, pandas" >/dev/null 2>&1; then
  echo "Setting up (once) — this takes a minute…"
  if ! "$PY" -m pip install --quiet --user -r requirements.txt; then
    echo
    echo "Could not install what it needs. Try running this by hand to see why:"
    echo "  $PY -m pip install -r \"$ROOT/requirements.txt\""
    echo
    read -r -p "Press Return to close. " _
    exit 1
  fi
fi

echo "Opening Roast Coach's Mac sync in your browser…"
echo "Leave this window open while you use it. Close it, or press Control-C,"
echo "when you are done."
echo

exec "$PY" -m streamlit run mac/sync_app.py \
  --server.headless false \
  --browser.gatherUsageStats false
