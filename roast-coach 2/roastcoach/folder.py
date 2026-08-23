"""
Reading a folder on the roaster's own computer, from a browser.

Roast Coach runs in the cloud, so it has no access to anyone's disk. Chrome and
Edge can bridge that: the roaster picks their RoasTime folder once, the browser
holds the permission, and on every later visit the app can read new roast files
straight out of it -- no uploading, no re-selecting.

The picker is wrapped in a Streamlit component. Everything happens in the
roaster's browser; only the roast files themselves are sent to the app, in
batches, and only the ones it has not seen before.

Safari and Firefox do not implement the API. The component says so plainly and
the upload path covers them.
"""

from __future__ import annotations

from pathlib import Path

import streamlit.components.v1 as components

_component = components.declare_component(
    "roast_folder", path=str(Path(__file__).parent / "frontend")
)


def folder_picker(known: dict, autosync: bool = True, key: str = "folder"):
    """Show the folder control and return whatever the browser sent back.

    ``known`` maps file name to ``(modified, size)`` so the browser can skip
    everything already imported. The return value is one of:

    ``{"action": "files", "folder", "files": [...], "remaining": n}``
    ``{"action": "scanned", ...}``  -- looked, found nothing new
    ``{"action": "disconnected"}`` / ``{"action": "error", "message": ...}``
    """
    return _component(known=known, autosync=autosync, key=key, default=None)
