import logging
from .scraper import RawGameState, RawUnit
from .province_map import province_to_code, resolve_coast
from .exceptions import UnknownProvinceError, CoastAmbiguityError

logger = logging.getLogger(__name__)

ALL_POWERS = ["AUSTRIA", "ENGLAND", "FRANCE", "GERMANY", "ITALY", "RUSSIA", "TURKEY"]

POWER_NAME_MAP: dict[str, str] = {
    "austria": "AUSTRIA",
    "austro-hungary": "AUSTRIA",
    "austro hungary": "AUSTRIA",
    "england": "ENGLAND",
    "britain": "ENGLAND",
    "great britain": "ENGLAND",
    "france": "FRANCE",
    "germany": "GERMANY",
    "italy": "ITALY",
    "russia": "RUSSIA",
    "turkey": "TURKEY",
    "ottoman": "TURKEY",
    "ottoman empire": "TURKEY",
}

# Phase conversion maps
_SEASON_MAP = {"Spring": "S", "Fall": "F", "Winter": "W"}
_PHASE_MAP = {"Movement": "M", "Retreats": "R", "Adjustments": "A"}


def _is_province_code(s: str) -> bool:
    return len(s) <= 6 and s == s.upper() and s.isalpha()


def convert_phase(season: str, year: int, phase_type: str) -> str:
    """
    Convert human-readable phase to diplomacy package format.
    e.g. ("Spring", 1901, "Movement") → "S1901M"
    """
    season_code = _SEASON_MAP.get(season.capitalize(), season[0].upper())
    phase_code = _PHASE_MAP.get(phase_type.capitalize()) or next(
        (v for k, v in _PHASE_MAP.items() if phase_type.lower().startswith(k.lower())),
        None,
    )
    if not phase_code:
        phase_code = "M"
        logger.warning("Unknown phase type %r; defaulting to 'M'", phase_type)
    return f"{season_code}{year}{phase_code}"


def _normalize_power(raw: str) -> str | None:
    """Return diplomacy-package power name (e.g. 'FRANCE') or None if unrecognized."""
    key = raw.lower().strip()
    return POWER_NAME_MAP.get(key)


def convert_unit(
    raw: RawUnit,
    coast_hints: dict[str, str] | None = None,
) -> str | None:
    """
    Convert a RawUnit to diplomacy package unit string, e.g. "A PAR" or "F STP/NC".
    Returns None if province is unknown (logs warning).
    coast_hints: {province_code: coast_suffix} to resolve ambiguous coasts.
    """
    unit_letter = "F" if raw.unit_type.lower() == "fleet" else "A"

    # province may be a 3-letter code (from JS extraction) or a full name
    prov = raw.province
    if _is_province_code(prov):
        # Looks like an already-resolved code (e.g. "PAR", "STP", "LYO")
        base_code = prov
    else:
        try:
            base_code = province_to_code(prov)
        except UnknownProvinceError as e:
            logger.warning("Skipping unit: %s", e)
            return None

    # Coast resolution only applies to fleets
    if unit_letter == "F":
        coast = raw.coast
        if coast is None and coast_hints:
            coast = coast_hints.get(base_code)
        try:
            code = resolve_coast(base_code, coast)
        except CoastAmbiguityError as e:
            logger.warning("Coast ambiguity for unit in %s: %s", raw.province, e)
            raise
    else:
        code = base_code

    return f"{unit_letter} {code}"


def convert_game_state(
    raw: RawGameState,
    coast_hints: dict[str, str] | None = None,
) -> dict:
    """
    Convert RawGameState to a dict accepted by diplomacy.Game methods:
    {
        "name": "S1901M",
        "units": {
            "FRANCE": ["A PAR", "F BRE", "*A MAR"],   # * = dislodged
            ...  (all 7 powers present)
        },
        "centers": {
            "FRANCE": ["PAR", "BRE", "MAR"],
            ...
        }
    }
    """
    phase_name = convert_phase(raw.season, raw.year, raw.phase_type)

    # Initialize all 7 powers with empty lists
    units_dict: dict[str, list[str]] = {p: [] for p in ALL_POWERS}
    centers_dict: dict[str, list[str]] = {p: [] for p in ALL_POWERS}

    # Active units
    for raw_unit in raw.units:
        power = _normalize_power(raw_unit.power)
        if not power:
            logger.warning("Unknown power %r; skipping unit.", raw_unit.power)
            continue
        unit_str = convert_unit(raw_unit, coast_hints)
        if unit_str:
            units_dict[power].append(unit_str)

    # Dislodged units (prefixed with '*')
    for raw_unit in raw.dislodged:
        power = _normalize_power(raw_unit.power)
        if not power:
            logger.warning("Unknown power %r; skipping dislodged unit.", raw_unit.power)
            continue
        unit_str = convert_unit(raw_unit, coast_hints)
        if unit_str:
            units_dict[power].append(f"*{unit_str}")

    # Supply centers
    for raw_power, provinces in raw.supply_centers.items():
        power = _normalize_power(raw_power)
        if not power:
            logger.warning("Unknown power %r in supply centers; skipping.", raw_power)
            continue
        for prov in provinces:
            if _is_province_code(prov):
                centers_dict[power].append(prov)
            else:
                try:
                    code = province_to_code(prov)
                    centers_dict[power].append(code)
                except UnknownProvinceError as e:
                    logger.warning("Supply center: %s", e)

    return {
        "name": phase_name,
        "units": units_dict,
        "centers": centers_dict,
    }


def _unit_info(province: str, units_by_player: dict) -> tuple[str, str]:
    """
    Look up (unit_letter, coast_suffix) for a province across all powers.
    coast_suffix is e.g. '/SC', '/NC', or '' when no coast applies.
    """
    prov_up = province.upper()
    for power_units in units_by_player.values():
        if not isinstance(power_units, dict):
            continue
        for prov, val in power_units.items():
            if prov.upper() == prov_up:
                if isinstance(val, str):
                    return ("F" if val.upper() == "F" else "A"), ""
                elif isinstance(val, dict):
                    letter = "F" if str(val.get("type", "A")).upper() == "F" else "A"
                    coast = val.get("coast", "")
                    return letter, (f"/{coast.upper()}" if coast else "")
    return "A", ""


def _coast_suffix(raw_coast: str) -> str:
    """Convert a raw coast string (e.g. 'sc', 'nc') to '/SC', or '' if empty."""
    return f"/{raw_coast.upper()}" if raw_coast else ""


def convert_orders(
    raw_orders: dict,
    units_by_player: dict,
) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    """
    Convert backstabbr var orders → (orders, backstabbr_failed).

    orders:             {POWER_UPPER: ['A PAR - BUR', 'F LON H', ...], ...}
    backstabbr_failed:  {POWER_UPPER: {'A MUN S A PRU', ...}}
                        — order strings where backstabbr's own result was "FAILS".
                          These are illegal player orders; validation should ignore them.

    Backstabbr order types:
      MOVE:    {"type":"MOVE","to":"NTH"}                        → "F LON - NTH"
               {"type":"MOVE","to":"Spa","to_coast":"sc"}        → "F POR - SPA/SC"
      HOLD:    {"type":"HOLD"}                                   → "A WAR H"
      SUPPORT: {"type":"SUPPORT","to":"Tri","from":"Ser"}        → "A ALB S A SER - TRI"
               {"type":"SUPPORT","from":"Spa","to_coast":"sc"}   → "F LYO S F SPA/SC"
               (no "to" = support-hold)                         → "A ALB S A SER"
      CONVOY:  {"type":"CONVOY","to":"Nwy","from":"Edi"}         → "F NWG C A EDI - NWY"
      BUILD:   {"type":"BUILD"}                                  → "A PAR B"
      REMOVE:  {"type":"REMOVE"}                                 → "A PAR D"
      WAIVE:   {"type":"WAIVE"}                                  → "WAIVE"

    Coast suffixes are applied to:
      - The ordering unit's source province  (from unitsByPlayer coast field)
      - MOVE destinations                    (from order's to_coast field)
      - SUPPORT from-province                (from unitsByPlayer coast field)
      - SUPPORT move destinations            (from order's to_coast field)
    """
    result: dict[str, list[str]] = {}
    backstabbr_failed: dict[str, set[str]] = {}

    for power_name, power_orders in raw_orders.items():
        power_upper = _normalize_power(power_name)
        if not power_upper:
            logger.warning("convert_orders: unknown power %r, skipping", power_name)
            continue

        order_strings: list[str] = []
        failed_strings: set[str] = set()
        if not isinstance(power_orders, dict):
            result[power_upper] = order_strings
            backstabbr_failed[power_upper] = failed_strings
            continue

        # Find this power's units dict for letter/coast lookup
        power_units: dict = {}
        for k, v in units_by_player.items():
            if k.lower() == power_name.lower() and isinstance(v, dict):
                power_units = v
                break

        for src_prov, order_data in power_orders.items():
            if not isinstance(order_data, dict):
                continue

            order_type = str(order_data.get("type", "")).upper()
            src = src_prov.upper()
            bs_failed = str(order_data.get("result", "")).upper() == "FAILS"

            if order_type == "WAIVE":
                order_strings.append("WAIVE")
                continue

            # Get ordering unit letter and coast from this power's units
            letter = "A"
            src_coast = ""
            for prov, val in power_units.items():
                if prov.upper() == src:
                    if isinstance(val, str):
                        letter = "F" if val.upper() == "F" else "A"
                    elif isinstance(val, dict):
                        letter = "F" if str(val.get("type", "A")).upper() == "F" else "A"
                        src_coast = _coast_suffix(val.get("coast", ""))
                    break

            # Full source province code including coast (e.g. "SPA/SC" or "PAR")
            src_full = f"{src}{src_coast}"

            if order_type == "HOLD":
                order_str = f"{letter} {src_full} H"

            elif order_type == "MOVE":
                dest = str(order_data.get("to", "")).upper()
                to_coast = _coast_suffix(order_data.get("to_coast", ""))
                order_str = f"{letter} {src_full} - {dest}{to_coast}"

            elif order_type == "SUPPORT":
                frm = str(order_data.get("from", "")).upper()
                to = order_data.get("to")
                to_coast = _coast_suffix(order_data.get("to_coast", ""))
                sup_letter, frm_coast = _unit_info(frm, units_by_player)
                frm_full = f"{frm}{frm_coast}"
                if to:
                    order_str = f"{letter} {src_full} S {sup_letter} {frm_full} - {to.upper()}{to_coast}"
                else:
                    order_str = f"{letter} {src_full} S {sup_letter} {frm_full}"

            elif order_type == "CONVOY":
                frm = str(order_data.get("from", "")).upper()
                to = order_data.get("to")
                con_letter, _ = _unit_info(frm, units_by_player)
                if to:
                    order_str = f"{letter} {src_full} C {con_letter} {frm} - {to.upper()}"
                else:
                    order_str = f"{letter} {src_full} H"

            elif order_type == "BUILD":
                order_str = f"{letter} {src_full} B"

            elif order_type == "REMOVE":
                order_str = f"{letter} {src_full} D"

            else:
                logger.debug("convert_orders: unknown order type %r for %s/%s", order_type, power_name, src_prov)
                continue

            order_strings.append(order_str)
            if bs_failed:
                failed_strings.add(order_str)

        result[power_upper] = order_strings
        backstabbr_failed[power_upper] = failed_strings

    return result, backstabbr_failed
