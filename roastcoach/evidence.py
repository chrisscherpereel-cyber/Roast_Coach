"""How much each kind of claim is worth, and who says so.

Roasting knowledge comes from three different places and they are not
interchangeable: controlled experiments, the accumulated judgement of respected
roasters, and thresholds somebody chose because a number was needed. Presenting
all three in the same voice is how software ends up asserting that a curve tastes
of something.

So every finding carries a grade, the grade fixes the wording, and the sources
below say where the confidence comes from. Practitioner sources are listed as
practitioners — Rao and Barista Hustle are the origin of most modern rate-of-rise
vocabulary and are cited as such, not as experimental evidence.
"""

from __future__ import annotations

VERSION = 1

GRADES = {
    "A": {
        "name": "Experimental",
        "short": "measured, or experimentally supported",
        "wording": "Research has found…",
        "meaning": "Supported by controlled or peer-reviewed work, or read directly off "
                   "the machine's own probe.",
    },
    "B": {
        "name": "Practitioner consensus",
        "short": "widely recognised in roasting practice",
        "wording": "Roasting practice identifies this as…",
        "meaning": "Recognised by respected roasting authorities, without full "
                   "experimental validation.",
    },
    "C": {
        "name": "Heuristic",
        "short": "this app's threshold",
        "wording": "Flagged by this app's threshold of…",
        "meaning": "A useful diagnostic convention whose numbers are not scientifically "
                   "standardised. Argue with them; they are in one place.",
    },
    "D": {
        "name": "Sensory inference",
        "short": "cannot be established from the curve",
        "wording": "May increase the risk of…",
        "meaning": "A cup outcome. No arrangement of thermocouples has tasted anything: "
                   "cupping is what settles these.",
    },
}

SOURCES = [
    {
        "key": "yang2016",
        "cite": "Yang et al. (2016), Food Chemistry",
        "what": "Volatile markers of five experimentally generated roasting defects — "
                "light, dark, scorched, baked and underdeveloped.",
        "kind": "Peer-reviewed",
        "supports": ["surface_scorching", "surface_charring", "baked", "underdeveloped"],
    },
    {
        "key": "munchow2020",
        "cite": "Münchow et al. (2020), Beverages",
        "what": "Roasting conditions and coffee flavour: separates the effect of roast "
                "colour from roast time, and finds colour carries particular weight.",
        "kind": "Peer-reviewed",
        "supports": ["colour", "colour_gap", "colour_variance"],
    },
    {
        "key": "alstrup2020",
        "cite": "Alstrup et al. (2020), Beverages",
        "what": "Controlled sensory and chemical investigation of development-time "
                "modulation.",
        "kind": "Peer-reviewed",
        "supports": ["development_ratio", "development_band", "development_divergence"],
    },
    {
        "key": "masi2013",
        "cite": "Masi et al. (2013)",
        "what": "Experimental sensory characterisation of under-roasted coffee.",
        "kind": "Peer-reviewed",
        "supports": ["underdeveloped"],
    },
    {
        "key": "hu2020",
        "cite": "Hu et al. (2020)",
        "what": "Bean morphology, chemical composition and sensory scores across degrees "
                "of roast.",
        "kind": "Peer-reviewed",
        "supports": ["colour", "roast_degree"],
    },
    {
        "key": "rao",
        "cite": "Scott Rao",
        "what": "The modern specialty vocabulary: declining rate of rise, crash, flick, "
                "first-crack timing, development-time ratio. Also the demonstration that "
                "graph scaling alone changes how a curve looks.",
        "kind": "Practitioner",
        "supports": ["crash", "flick", "development_ratio", "first_crack_divergence"],
    },
    {
        "key": "baristahustle",
        "cite": "Barista Hustle roasting curriculum",
        "what": "Operational definitions of crash and flick, and the plateau → crash → "
                "flick behaviour of an unmanaged roast.",
        "kind": "Educational",
        "supports": ["crash", "flick", "flick_transient", "oscillation"],
    },
    {
        "key": "hoos",
        "cite": "Rob Hoos",
        "what": "Physical roasting defects: tipping, facing, charring.",
        "kind": "Practitioner",
        "supports": ["surface_tipping", "surface_facing", "surface_charring"],
    },
    {
        "key": "loring",
        "cite": "Loring",
        "what": "Mechanism of scorching: excessive metal-contact heat and drum hot spots.",
        "kind": "Manufacturer",
        "supports": ["surface_scorching"],
    },
    {
        "key": "giesen",
        "cite": "Giesen",
        "what": "Quakers become visible through roasting but are not caused by it.",
        "kind": "Manufacturer",
        "supports": ["quakers"],
    },
]


def source_for(condition_id: str) -> str | None:
    """The shortest honest attribution for one condition."""
    names = [source["cite"] for source in SOURCES if condition_id in source["supports"]]
    return " · ".join(names) if names else None


def grade_note(grade: str) -> str:
    entry = GRADES.get(grade)
    return f"{entry['name']} — {entry['meaning']}" if entry else ""
