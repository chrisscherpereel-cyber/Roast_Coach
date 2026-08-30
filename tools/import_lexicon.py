"""
Put World Coffee Research's flavour definitions into your own copy of the app.

    python3 tools/import_lexicon.py ~/Downloads/wcr-sensory-lexicon.pdf

The Sensory Lexicon is free to download and carries no licence to redistribute —
its only rights statement is "Copyright © 2017 World Coffee Research. All rights
reserved." So this repository holds the attribute names, their categories and
their reference intensities, which are facts about a measuring instrument, and
not WCR's own definitions, which are their writing.

This reads the PDF *you* downloaded and writes those definitions somewhere
private, where the app finds them. Nothing leaves your machine and nothing is
added to the repository.

Get the PDF here — free, and they would rather you had it:
https://worldcoffeeresearch.org/resources/sensory-lexicon
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIVATE = HERE.parent / "private" / "wcr-lexicon-definitions.json"
VOCABULARY = HERE.parent / "library" / "sensory" / "wcr-sensory-lexicon-vocabulary.json"


def text_of(pdf: Path) -> str:
    """The PDF as text, by whatever this computer has."""
    try:
        return subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                              capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pass
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit(
            "Neither `pdftotext` nor the `pypdf` package is here.\n"
            "  brew install poppler     — or —     pip3 install pypdf")
    return "\n".join((page.extract_text() or "") for page in PdfReader(str(pdf)).pages)


def known_attributes() -> list[str]:
    """The names to look for, from the vocabulary that does ship."""
    try:
        held = json.loads(VOCABULARY.read_text(encoding="utf-8"))
    except OSError:
        raise SystemExit(f"Cannot read {VOCABULARY} — run this from the repository.")
    return [str(item["name"]) for item in held.get("attributes", []) if item.get("name")]


def definitions_in(text: str, names: list[str]) -> dict:
    """Each attribute's one-sentence definition, as the lexicon prints it.

    The lexicon sets an attribute as a heading on its own line — sometimes in
    capitals, sometimes as printed — with the definition on the line beneath. The
    same name also appears in the contents and the index, where the line beneath
    is another name rather than a sentence, so a candidate only counts if what
    follows reads like a definition: a full sentence with spaces in it. Where
    several candidates survive, the longest wins, which is always the real one.

    Anything that does not match is left out. A missing definition costs a
    tooltip; a wrong one would be worse than none.
    """
    lines = [line.strip() for line in text.splitlines()]
    found = {}

    for name in names:
        # A few attributes are indexed under one name and printed under another:
        # "Body/Fullness" is set as FULLNESS on its own page. Try the whole name
        # first and fall back to its parts, so the printed heading is found
        # without loosening the match for everything else.
        wanted = [name.strip().lower()]
        if "/" in name:
            wanted += [part.strip().lower() for part in name.split("/") if part.strip()]

        best = ""
        for position, line in enumerate(lines):
            if line.strip().lower().rstrip(":") not in wanted:
                continue
            # The sentence beneath, skipping blank lines.
            for step in range(1, 4):
                if position + step >= len(lines):
                    break
                candidate = lines[position + step]
                if not candidate:
                    continue
                if candidate.isupper() or len(candidate.split()) < 4:
                    break
                sentence = " ".join(candidate.split())
                # Definitions wrap, sometimes onto a line of one word — "…
                # characteristic of / molasses." — so keep taking lines until the
                # sentence ends rather than assuming it fits on two.
                step_on = position + step + 1
                while (not sentence.endswith(".") and step_on < len(lines)
                       and len(sentence) < 360):
                    tail = lines[step_on]
                    if not tail or tail.isupper() or tail.startswith("REF"):
                        break
                    sentence = " ".join((sentence + " " + tail).split())
                    step_on += 1
                if 25 < len(sentence) < 400 and " " in sentence:
                    if len(sentence) > len(best):
                        best = sentence
                break
        if best:
            found[name] = best
    return found


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__.strip())
    pdf = Path(sys.argv[1]).expanduser()
    if not pdf.is_file():
        raise SystemExit(f"No file at {pdf}")

    names = known_attributes()
    found = definitions_in(text_of(pdf), names)
    if not found:
        raise SystemExit(
            "No definitions found in that PDF. Is it the Sensory Lexicon?\n"
            "https://worldcoffeeresearch.org/resources/sensory-lexicon")

    PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE.write_text(json.dumps(
        {"source": {"title": "Sensory Lexicon", "publisher": "World Coffee Research",
                    "note": "Copyright © World Coffee Research. All rights reserved. "
                            "Imported from your own copy; not for redistribution."},
         "definitions": found}, indent=1, ensure_ascii=False), encoding="utf-8")

    missing = [name for name in names if name not in found]
    print(f"{len(found)} of {len(names)} definitions → {PRIVATE}")
    if missing:
        print("not matched: " + ", ".join(missing[:12])
              + (f" …and {len(missing) - 12} more" if len(missing) > 12 else ""))
    print("\nThis file is yours and stays out of the repository. The app will show "
          "these definitions wherever a flavour is named.")


if __name__ == "__main__":
    main()
