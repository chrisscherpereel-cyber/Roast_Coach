"""
Read the coffee's origin and roast number out of a roast name.

RoasTime's roast name is free text, so this is how a roast gets a first guess at
which coffee it is. The roaster can always correct it -- what they type wins.

``Panama Geisha`` resolves to Panama because Geisha is a variety, not a place;
region names (Yirgacheffe, Tarrazu, Huehuetenango …) resolve to their country.
"""

from __future__ import annotations

import re

# country -> the names, regions, farms and abbreviations that imply it.
# Longer aliases are matched first so "Costa Rica" never resolves as "Rica".
ORIGIN_ALIASES: dict[str, tuple[str, ...]] = {
    "Ethiopia": ("ethiopia", "ethiopian", "eth", "ethi", "yirgacheffe", "yirgachefe", "yirg",
                 "sidamo", "sidama", "guji", "harrar", "harar", "limu", "djimmah", "jimma",
                 "kochere", "aricha", "gedeb", "gedeo", "shakiso", "hambela", "bombe", "worka"),
    "Kenya": ("kenya", "kenyan", "aa", "nyeri", "kirinyaga", "kiambu", "embu", "thika", "gichathaini"),
    "Colombia": ("colombia", "colombian", "col", "colo", "huila", "narino", "nariño", "tolima",
                 "cauca", "antioquia", "quindio", "supremo", "excelso"),
    "Brazil": ("brazil", "brazilian", "brasil", "braz", "cerrado", "mogiana", "minas gerais",
               "sul de minas", "bourbon santos", "santos"),
    "Guatemala": ("guatemala", "guatemalan", "guat", "huehuetenango", "huehue", "antigua",
                  "atitlan", "atitlán", "coban", "cobán", "acatenango", "fraijanes"),
    "Costa Rica": ("costa rica", "costarica", "tarrazu", "tarrazú", "la minita", "naranjo",
                   "dota", "tres rios", "west valley", "brunca"),
    "Panama": ("panama", "panamanian", "boquete", "volcan", "volcán", "chiriqui", "chiriquí", "hartmann"),
    "Honduras": ("honduras", "honduran", "hond", "marcala", "copan", "copán", "santa barbara",
                 "santa bárbara", "ocotepeque", "intibuca"),
    "Nicaragua": ("nicaragua", "nicaraguan", "nica", "nic", "jinotega", "matagalpa", "nueva segovia", "dipilto"),
    "El Salvador": ("el salvador", "salvador", "salvadoran", "el sal", "ahuachapan", "santa ana",
                    "apaneca", "chalatenango"),
    "Mexico": ("mexico", "méxico", "mexican", "chiapas", "oaxaca", "veracruz", "nayarit", "altura"),
    "Peru": ("peru", "perú", "peruvian", "cajamarca", "amazonas", "cusco", "chanchamayo", "puno"),
    "Bolivia": ("bolivia", "bolivian", "caranavi", "yungas"),
    "Ecuador": ("ecuador", "ecuadorian", "loja", "galapagos", "galápagos"),
    "Rwanda": ("rwanda", "rwandan", "rwa", "nyamasheke", "huye", "gakenke", "kivu"),
    "Burundi": ("burundi", "burundian", "kayanza", "ngozi", "bwayi"),
    "Tanzania": ("tanzania", "tanzanian", "tanz", "kilimanjaro", "mbeya", "ruvuma", "peaberry ta"),
    "Uganda": ("uganda", "ugandan", "bugisu", "sipi", "rwenzori"),
    "DR Congo": ("dr congo", "drc", "democratic republic of congo", "kivu congo", "congo"),
    "Zambia": ("zambia", "zambian"),
    "Malawi": ("malawi", "malawian"),
    "Zimbabwe": ("zimbabwe",),
    "Cameroon": ("cameroon", "cameroun"),
    "Ivory Coast": ("ivory coast", "cote d'ivoire", "côte d'ivoire"),
    "Yemen": ("yemen", "yemeni", "mocha", "matari", "haraz"),
    "India": ("india", "indian", "monsooned malabar", "malabar", "mysore", "karnataka", "kerala"),
    "Indonesia": ("indonesia", "indonesian", "indo", "sumatra", "sumatran", "mandheling",
                  "mandailing", "gayo", "aceh", "lintong", "java", "sulawesi", "toraja",
                  "bali", "kintamani", "flores", "sunda"),
    "Papua New Guinea": ("papua new guinea", "png", "papua", "sigri", "wahgi", "eastern highlands"),
    "Timor-Leste": ("timor", "timor-leste", "east timor"),
    "Vietnam": ("vietnam", "vietnamese", "da lat", "dalat"),
    "Thailand": ("thailand", "thai", "chiang mai", "doi chaang", "doi chang"),
    "Laos": ("laos", "bolaven"),
    "Myanmar": ("myanmar", "burma", "ywangan"),
    "Philippines": ("philippines", "filipino", "benguet", "sagada"),
    "China": ("china", "chinese", "yunnan", "pu'er", "puer"),
    "Jamaica": ("jamaica", "jamaican", "blue mountain", "jbm"),
    "United States": ("hawaii", "hawaiian", "kona", "kauai", "maui", "ka'u", "kau coffee", "puerto rico"),
    "Dominican Republic": ("dominican republic", "dominican", "barahona"),
    "Haiti": ("haiti", "haitian"),
    "Cuba": ("cuba", "cuban", "crystal mountain"),
}

# Ambiguous short forms only count as whole words: "CR" in "CR Tarrazu" is Costa
# Rica, but "cr" inside "crack" is not.
_SHORT_ALIASES = {
    "cr": "Costa Rica",
    "es": "El Salvador",
    "png": "Papua New Guinea",
    "drc": "DR Congo",
    "jbm": "Jamaica",
    "aa": "Kenya",
}

_ALIAS_PATTERNS = sorted(
    ((alias, country) for country, aliases in ORIGIN_ALIASES.items() for alias in aliases),
    key=lambda pair: -len(pair[0]),
)

_ROAST_NUMBER = re.compile(r"#\s*(\d{1,5})")


def roast_number_from_name(roast_name: str):
    """The ``#48`` in ``#48 Eth Yirgacheffe 3rd``."""
    if not roast_name:
        return None
    match = _ROAST_NUMBER.search(str(roast_name))
    return int(match.group(1)) if match else None


def origin_from_name(roast_name: str):
    """Best-guess country for a roast name, or None."""
    if not roast_name:
        return None
    text = re.sub(r"[^a-z0-9'\s-]", " ", str(roast_name).lower())
    text = " " + re.sub(r"\s+", " ", text).strip() + " "

    for alias, country in _ALIAS_PATTERNS:
        if len(alias) <= 3 and alias not in _SHORT_ALIASES:
            continue
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", text):
            return country
    for alias, country in _SHORT_ALIASES.items():
        if re.search(rf"(?<![a-z]){alias}(?![a-z])", text):
            return country
    return None


def annotate(roast_names) -> list[tuple[str | None, int | None]]:
    """(origin, roast number) for a list of roast names, offline."""
    return [(origin_from_name(name), roast_number_from_name(name)) for name in roast_names]
