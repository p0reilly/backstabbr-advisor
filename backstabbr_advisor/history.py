"""
Multi-phase history scraping and persistence for backstabbr games.

Fetches every historical phase from backstabbr, builds a full diplomacy.Game
history, and persists it to a JSON file. On subsequent runs, only fetches
phases that are not already saved.
"""
from __future__ import annotations

import json
import logging
import os
import time

from .scraper import fetch_game_page, extract_game_state, RawGameState
from .converter import convert_game_state, convert_orders, _normalize_power, ALL_POWERS
from .exceptions import ParseError

logger = logging.getLogger(__name__)

_SEASONS = ["spring", "fall", "winter"]

# Map season string → diplomacy season letter
_SEASON_LETTER = {"spring": "S", "fall": "F", "winter": "W"}


def _phase_name(year: int, season: str) -> str:
    """e.g. (1901, 'spring') → 'S1901M', (1901, 'fall') → 'F1901M', (1901, 'winter') → 'W1901A'"""
    letter = _SEASON_LETTER[season.lower()]
    phase_type = "A" if season.lower() == "winter" else "M"
    return f"{letter}{year}{phase_type}"


def enumerate_phase_urls(
    base_url: str,
    current_season: str,
    current_year: int,
) -> list[tuple[int, str]]:
    """
    Return [(year, season_str), ...] from Spring 1901 up to and including the current phase.
    season_str is one of 'spring', 'fall', 'winter'.
    """
    current_season_lower = current_season.lower()
    current_idx = _SEASONS.index(current_season_lower)

    phases: list[tuple[int, str]] = []
    for year in range(1901, current_year + 1):
        for idx, season in enumerate(_SEASONS):
            if year == current_year and idx > current_idx:
                break
            phases.append((year, season))
    return phases


def scrape_phase(game_url: str, year: int, season_str: str, cookie: str) -> RawGameState:
    """
    Fetch /YEAR/SEASON and return RawGameState (including raw_orders).
    game_url should be the canonical game URL (no trailing slash).
    """
    url = f"{game_url.rstrip('/')}/{year}/{season_str.lower()}"
    logger.info("Fetching phase %s %d: %s", season_str, year, url)
    soup = fetch_game_page(url, cookie)
    return extract_game_state(soup)


def load_history(save_path: str):
    """Load game from JSON file using Game.from_dict(). Returns None if file missing."""
    if not os.path.exists(save_path):
        return None
    try:
        from diplomacy import Game
    except ImportError:
        raise ImportError("The 'diplomacy' package is not installed. Run: pip install diplomacy")

    with open(save_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    logger.info("Loaded existing game from %s", save_path)
    return Game.from_dict(data)


def save_history(game, save_path: str) -> None:
    """Write game.to_dict() as JSON to save_path (creates parent dir if needed)."""
    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(game.to_dict(), f, indent=2)
    logger.info("Saved game to %s", save_path)


def _inject_phase(game, phase_name: str, converted: dict, orders: dict[str, list[str]]) -> None:
    """Directly insert a phase into game.state_history and game.order_history."""
    # state_history and order_history use a typed SortedDict whose keys must be
    # StringComparator instances (a subclass wrapping str), not plain strings.
    key_type = game.state_history.key_type
    key = key_type(phase_name)

    game.state_history.put(key, {
        "name": phase_name,
        "units": converted["units"],
        "centers": converted["centers"],
        "retreats": {},
        "builds": {},
        "homes": {},
        "influence": {},
        "civil_disorder": {},
    })
    game.order_history.put(key, orders)


def validate_phase_history(
    game,
    known_failed: dict[str, dict[str, set[str]]],
) -> dict[str, list[str]]:
    """
    Validate orders against board state for each phase listed in known_failed.

    Only phases present in known_failed are checked — previously saved phases
    are assumed to have been validated on their first scrape run.

    For each order, backstabbr already told us whether it FAILS or SUCCEEDS.
    Orders in known_failed[phase][power] were marked FAILS by backstabbr (illegal
    player orders). Those are excluded from validation so they don't produce false
    positives. Only orders backstabbr marked SUCCEEDS are validated; any diplomacy
    engine error on those indicates a conversion bug and is raised as a RuntimeError.

    Returns {phase_name: [error_string, ...]} — non-empty only if a conversion bug
    is detected (should be empty after correct implementation).
    """
    try:
        from diplomacy import Game
    except ImportError:
        raise ImportError("The 'diplomacy' package is not installed. Run: pip install diplomacy")

    issues: dict[str, list[str]] = {}

    for phase_name, phase_failed in known_failed.items():
        state = game.state_history.get(phase_name)
        orders = game.order_history.get(phase_name)
        if not state or not orders:
            continue

        # Create a fresh game and set it to this phase
        tmp = Game()
        try:
            tmp.set_current_phase(str(phase_name))
        except Exception as e:
            logger.debug("validate: set_current_phase(%s) failed: %s", phase_name, e)

        # Clear ALL powers first — Game() initialises with starting units, which
        # would pollute build-site checks and adjacency lookups.
        for power_name in ALL_POWERS:
            tmp.set_units(power_name, [], reset=True)
            tmp.set_centers(power_name, [], reset=True)

        # Restore board state for all powers from state_history
        for power_name, unit_list in state.get("units", {}).items():
            try:
                tmp.set_units(power_name, unit_list, reset=True)
            except Exception as e:
                logger.debug("validate: set_units(%s) in %s failed: %s", power_name, phase_name, e)

        for power_name, center_list in state.get("centers", {}).items():
            try:
                tmp.set_centers(power_name, center_list, reset=True)
            except Exception as e:
                logger.debug("validate: set_centers(%s) in %s failed: %s", power_name, phase_name, e)

        # Validate only orders backstabbr marked as SUCCEEDS
        phase_errors: list[str] = []
        for power_name, power_orders in orders.items():
            if not power_orders:
                continue
            failed_set = phase_failed.get(power_name, set())
            valid_orders = [o for o in power_orders if o not in failed_set]
            skipped = [o for o in power_orders if o in failed_set]
            for o in skipped:
                logger.debug("Validation [%s] %s: skipping backstabbr-FAILS order: %s", phase_name, power_name, o)
            if not valid_orders:
                continue
            tmp.error.clear()
            try:
                tmp.set_orders(power_name, valid_orders)
            except Exception as e:
                phase_errors.append(f"{power_name}: exception during set_orders: {e}")
                continue
            for err in tmp.error:
                phase_errors.append(f"{power_name}: {err}")

        if phase_errors:
            issues[str(phase_name)] = phase_errors

    total = len(known_failed)
    bad = len(issues)
    if bad:
        logger.info("Validation complete: %d/%d phases have conversion errors", bad, total)
        for phase_name, errs in issues.items():
            for msg in errs:
                logger.error("Conversion error [%s] %s", phase_name, msg)
        raise RuntimeError(
            f"Order conversion produced {bad} phase(s) with invalid SUCCEEDS orders — "
            f"see logs for details: {list(issues.keys())}"
        )
    else:
        logger.info("Validation complete: all %d phases OK", total)

    return issues


def scrape_and_persist(game_url: str, cookie: str, save_dir: str = "game_data"):
    """
    Main entry point for incremental history scraping.

    1. Fetch current page → determine current phase.
    2. Load existing save if present; determine which phases are missing.
    3. Scrape missing phases oldest-first.
    4. Inject each into game.state_history / game.order_history.
    5. Set current board state (units/centers/phase).
    6. Save and return Game.
    """
    try:
        from diplomacy import Game
    except ImportError:
        raise ImportError("The 'diplomacy' package is not installed. Run: pip install diplomacy")

    # Step 1: Fetch current page
    logger.info("Fetching current game state from %s", game_url)
    soup = fetch_game_page(game_url, cookie)
    current_raw = extract_game_state(soup)

    if not current_raw.season or not current_raw.year:
        raise ParseError("Could not determine current season/year from game page.")

    current_season = current_raw.season.lower()   # "spring" / "fall" / "winter"
    current_year = current_raw.year

    # Derive phase name for current state
    current_phase_name = _phase_name(current_year, current_season)
    logger.info("Current phase: %s", current_phase_name)

    # Derive game ID and save path from URL
    game_id = game_url.rstrip("/").split("/")[-1]
    save_path = os.path.join(save_dir, f"{game_id}.json")

    # Step 2: Load existing save
    game = load_history(save_path)

    if game is not None:
        saved_phases = set(game.state_history.keys())
        # Check if already up to date
        if current_phase_name in saved_phases:
            logger.info("Game already up to date at %s — no new phases to fetch.", current_phase_name)
            return game
    else:
        game = Game()
        saved_phases: set[str] = set()

    # Step 3: Enumerate all phases to scrape
    all_phases = enumerate_phase_urls(game_url, current_season, current_year)
    missing = [(yr, sea) for yr, sea in all_phases if _phase_name(yr, sea) not in saved_phases]

    logger.info(
        "%d total phases, %d already saved, %d to fetch",
        len(all_phases), len(all_phases) - len(missing), len(missing),
    )

    # Step 4: Scrape missing phases oldest-first
    known_failed: dict[str, dict[str, set[str]]] = {}
    for i, (year, season_str) in enumerate(missing):
        phase_name = _phase_name(year, season_str)
        try:
            raw = scrape_phase(game_url, year, season_str, cookie)
        except Exception as e:
            logger.warning("Failed to scrape %s %d (%s): %s — skipping", season_str, year, phase_name, e)
            continue

        try:
            converted = convert_game_state(raw)
        except Exception as e:
            logger.warning("Failed to convert %s: %s — skipping", phase_name, e)
            continue

        orders, failed = convert_orders(raw.raw_orders, raw.units_by_player_raw)
        known_failed[phase_name] = failed

        _inject_phase(game, phase_name, converted, orders)
        logger.debug("Injected phase %s", phase_name)

        # Rate limiting — avoid hammering backstabbr
        if i < len(missing) - 1:
            time.sleep(0.5)

    # Step 5: Set current board state
    try:
        game.set_current_phase(current_phase_name)
    except Exception as e:
        logger.warning("set_current_phase(%r) failed: %s", current_phase_name, e)

    current_converted = convert_game_state(current_raw)
    for power, units in current_converted.get("units", {}).items():
        try:
            game.set_units(power, units, reset=True)
        except Exception as e:
            logger.warning("set_units(%r, %r) failed: %s", power, units, e)

    for power, centers in current_converted.get("centers", {}).items():
        try:
            game.set_centers(power, centers, reset=True)
        except Exception as e:
            logger.warning("set_centers(%r, %r) failed: %s", power, centers, e)

    # Step 6: Validate injected phases (raises RuntimeError on conversion bugs)
    if known_failed:
        validate_phase_history(game, known_failed)

    # Step 7: Save and return
    save_history(game, save_path)
    return game
