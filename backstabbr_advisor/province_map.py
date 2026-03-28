import re
from .exceptions import UnknownProvinceError, CoastAmbiguityError

# Full province name → 3-letter code mapping (case-insensitive keys)
PROVINCE_MAP: dict[str, str] = {
    # Land provinces
    "ankara": "ANK",
    "belgium": "BEL",
    "berlin": "BER",
    "brest": "BRE",
    "budapest": "BUD",
    "bulgaria": "BUL",
    "constantinople": "CON",
    "denmark": "DEN",
    "edinburgh": "EDI",
    "greece": "GRE",
    "holland": "HOL",
    "kiel": "KIE",
    "liverpool": "LVP",
    "livpool": "LVP",  # alternate
    "london": "LON",
    "marseilles": "MAR",
    "marseille": "MAR",
    "moscow": "MOS",
    "munich": "MUN",
    "naples": "NAP",
    "norway": "NWY",
    "paris": "PAR",
    "portugal": "POR",
    "rome": "ROM",
    "rumania": "RUM",
    "romania": "RUM",
    "serbia": "SER",
    "sevastopol": "SEV",
    "smyrna": "SMY",
    "spain": "SPA",
    "st. petersburg": "STP",
    "st petersburg": "STP",
    "saint petersburg": "STP",
    "sweden": "SWE",
    "trieste": "TRI",
    "tunis": "TUN",
    "tunisia": "TUN",
    "venice": "VEN",
    "vienna": "VIE",
    "warsaw": "WAR",
    # Inland/multi-coast
    "albania": "ALB",
    "apulia": "APU",
    "armenia": "ARM",
    "bohemia": "BOH",
    "burgundy": "BUR",
    "clyde": "CLY",
    "finland": "FIN",
    "galicia": "GAL",
    "gascony": "GAS",
    "livonia": "LVN",
    "piedmont": "PIE",
    "picardy": "PIC",
    "prussia": "PRU",
    "ruhr": "RUH",
    "silesia": "SIL",
    "syria": "SYR",
    "tyrolia": "TYR",
    "ukraine": "UKR",
    "wales": "WAL",
    "yorkshire": "YOR",
    # Sea provinces
    "adriatic sea": "ADR",
    "adriatic": "ADR",
    "aegean sea": "AEG",
    "aegean": "AEG",
    "baltic sea": "BAL",
    "baltic": "BAL",
    "barents sea": "BAR",
    "barents": "BAR",
    "black sea": "BLA",
    "black": "BLA",
    "eastern mediterranean": "EAS",
    "eastern med": "EAS",
    "east mediterranean": "EAS",
    "english channel": "ENG",
    "english": "ENG",
    "gulf of bothnia": "BOT",
    "bothnia": "BOT",
    "gulf of lyon": "LYO",
    "gulf of lyons": "LYO",
    "lyon": "LYO",
    "lyons": "LYO",
    "helgoland bight": "HEL",
    "heligoland bight": "HEL",
    "helgoland": "HEL",
    "ionian sea": "ION",
    "ionian": "ION",
    "irish sea": "IRI",
    "irish": "IRI",
    "mid-atlantic ocean": "MAO",
    "mid atlantic ocean": "MAO",
    "mid-atlantic": "MAO",
    "mid atlantic": "MAO",
    "north atlantic ocean": "NAO",
    "north atlantic": "NAO",
    "north sea": "NTH",
    "norwegian sea": "NWG",
    "skagerrak": "SKA",
    "tyrrhenian sea": "TYS",
    "tyrrhenian": "TYS",
    "western mediterranean": "WES",
    "western med": "WES",
    "west mediterranean": "WES",
}

# Provinces that may have coastal variants
COASTAL_PROVINCES = {"STP", "SPA", "BUL"}

# Valid coast suffixes per province
VALID_COASTS: dict[str, list[str]] = {
    "STP": ["NC", "SC"],
    "SPA": ["NC", "SC"],
    "BUL": ["EC", "SC"],
}

# Regex to detect coast hint strings
_COAST_PATTERN = re.compile(r"\b(north|nc|n|south|sc|s|east|ec|e)\b", re.IGNORECASE)

_COAST_NORMALIZE = {
    "north": "NC", "nc": "NC", "n": "NC",
    "south": "SC", "sc": "SC", "s": "SC",
    "east": "EC", "ec": "EC", "e": "EC",
}


def province_to_code(name: str) -> str:
    """Normalize province name and return 3-letter code."""
    normalized = re.sub(r"['\-]", " ", name.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)

    # Strip leading/trailing articles or noise
    normalized = normalized.removeprefix("the ").strip()

    code = PROVINCE_MAP.get(normalized)
    if code is None:
        raise UnknownProvinceError(
            f"Unknown province name: {name!r} (normalized: {normalized!r})"
        )
    return code


def resolve_coast(base_code: str, coast_hint: str | None) -> str:
    """
    Return province code with coast suffix if applicable.
    e.g. resolve_coast("STP", "nc") → "STP/NC"
         resolve_coast("PAR", None) → "PAR"
    Raises CoastAmbiguityError if coast hint is missing for a coastal province
    that requires one, or if the hint is invalid for that province.
    """
    if base_code not in COASTAL_PROVINCES:
        return base_code

    if coast_hint is None:
        raise CoastAmbiguityError(
            f"Province {base_code} requires a coast specifier (NC/SC/EC) "
            f"but none was found. Use --coast-hints to override."
        )

    match = _COAST_PATTERN.search(coast_hint)
    if not match:
        raise CoastAmbiguityError(
            f"Cannot parse coast from hint {coast_hint!r} for province {base_code}"
        )

    coast = _COAST_NORMALIZE[match.group(1).lower()]
    valid = VALID_COASTS[base_code]
    if coast not in valid:
        raise CoastAmbiguityError(
            f"Coast {coast!r} is not valid for {base_code}. Valid coasts: {valid}"
        )

    return f"{base_code}/{coast}"
