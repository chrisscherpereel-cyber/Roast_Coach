"""
The same folder, every time, and only what has changed in it.

Roast Coach runs in the cloud and has no access to anyone's disk, so roasts have
to be handed to it by the browser. A plain file input would mean navigating to
the same folder every single time — and on macOS that folder is buried inside
``~/Library``, which the *folder* picker refuses to open at all.

Two ways round that, and the app offers both.

:func:`folder_watcher` is the one to reach for. The folder is chosen once; Chrome
holds the permission and the component holds the handle in IndexedDB, so on every
later visit the app lists the folder itself, works out which files are new or have
changed since last time, and reads only those. No path is ever typed, and after
the first visit there is usually nothing to click.

:func:`add_roasts_button` is the fallback for when that folder cannot be watched —
Chrome refuses to share anything inside the macOS Library, and Safari and Firefox
have no folder API at all. It asks for *files*, which is allowed where a folder is
not, and reopens in the folder last used.

Both are handed the list of what has already been imported, and neither opens a
file the app already has.
"""

from __future__ import annotations

from pathlib import Path

import streamlit.components.v1 as components

_upload = components.declare_component(
    "roast_upload", path=str(Path(__file__).parent / "frontend" / "upload")
)

_folder = components.declare_component(
    "roast_folder", path=str(Path(__file__).parent / "frontend" / "folder")
)

_curve = components.declare_component(
    "roast_curve", path=str(Path(__file__).parent / "frontend" / "curve")
)


def curve_editor(handles, ghost=None, start=90.0, gain=1.0, first_crack=None,
                 drop=None, theme="light", key="curve", crack_ibts=None):
    """The rate of rise, as something to pull about rather than read.

    Streamlit can draw a chart; it cannot let anybody drag one. So this is a
    component of its own: the handles go out, the roaster pulls them, and what
    comes back is the curve they drew. Temperature is redrawn in the browser as
    the integral of the rate of rise, so the picture answers under the hand
    instead of after a round trip — and Python does the same arithmetic again
    when it turns the curve into a recipe, so the two never disagree.
    """
    return _curve(handles=handles, ghost=ghost or [], start=start, gain=gain,
                  first_crack=first_crack, crack_ibts=crack_ibts, drop=drop,
                  theme=theme, key=key, default=None)


def add_roasts_button(known: dict, key: str = "uploader"):
    """The Add roasts control. Returns whatever the browser last sent.

    ``known`` maps file name to ``(modified, size)``. The return value is one of:

    ``{"action": "files", "files": [...], "remaining": n, "notRoasts": [...]}``
    ``{"action": "none", "chosen": n, "alreadyHad": n}`` — nothing new was chosen
    """
    return _upload(known=known, key=key, default=None)


def folder_watcher(known: dict, auto: bool = True, key: str = "folder"):
    """Watch one folder and pick up whatever has changed in it.

    The folder is chosen once. Chrome keeps the handle, this component keeps it in
    IndexedDB, and neither the roaster nor the app ever handles a path again. On
    each visit the browser lists the folder, compares every file against ``known``
    (name → ``(modified, size)``) and reads only the ones that are new or whose
    timestamp or size has moved. With ``auto`` set that happens on arrival, with
    no click at all.

    Returns one of:

    ``{"action": "files", "folder", "files", "remaining", "fresh", "changed", "looked"}``
    ``{"action": "scanned", "folder", "looked"}``      — looked, nothing had changed
    ``{"action": "disconnected"}``
    ``{"action": "error", "blocked": bool, "message"}``

    Every message carries a ``seq``; act on each one once.
    """
    return _folder(known=known, auto=auto, key=key, default=None)
