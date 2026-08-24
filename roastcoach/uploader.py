"""
One click, the same folder, only what is new.

Roast Coach runs in the cloud and has no access to anyone's disk, so roasts have
to be handed to it by the browser. A plain file input would mean navigating to
the same folder every single time — and on macOS that folder is buried inside
``~/Library``, which the *folder* picker refuses to open at all.

This does better. Chrome and Edge let a file picker be given an ``id``, and they
reopen it wherever it was last used; the component also stores a handle to the
last file chosen and passes it as the picker's starting point. After the first
visit, adding roasts is: click, select all, done — and the dialog is already in
the right folder, Library or not, because picking *files* is allowed where
picking a folder is not.

Before anything is read, the browser is given the list of files already imported
— name, size and timestamp — and skips them. Only genuinely new or changed files
are opened and sent, ten at a time.
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


def add_roasts_button(known: dict, key: str = "uploader"):
    """The Add roasts control. Returns whatever the browser last sent.

    ``known`` maps file name to ``(modified, size)``. The return value is one of:

    ``{"action": "files", "files": [...], "remaining": n, "notRoasts": [...]}``
    ``{"action": "none", "chosen": n, "alreadyHad": n}`` — nothing new was chosen
    """
    return _upload(known=known, key=key, default=None)


def folder_picker(known: dict, autosync: bool = True, key: str = "folder"):
    """Read a whole folder on every visit, where the browser allows it.

    ``known`` is the same map as above. Returns ``{"action": "files"|"scanned"|
    "disconnected"|"error", …}``.
    """
    return _folder(known=known, autosync=autosync, key=key, default=None)
