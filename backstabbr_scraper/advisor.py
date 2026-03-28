"""
Build a structured advisory prompt for a given power in a backstabbr game.
Loads the game fresh from disk on every call.
"""
from __future__ import annotations

import json
import os

_SEASON_LONG = {"S": "SPRING", "F": "FALL", "W": "WINTER"}
_SUFFIX_LONG = {"M": "MOVEMENT", "R": "RETREATS", "A": "ADJUSTMENTS"}


def _short_to_long_phase(short: str) -> str:
    return f"{_SEASON_LONG[short[0]]} {short[1:-1]} {_SUFFIX_LONG[short[-1]]}"

from .converter import ALL_POWERS, POWER_NAME_MAP
from .analysis import accumulate_relationships, build_sc_trajectory
from .order_context import generate_rich_order_context
from .press_context import load_press_context, format_press_section, format_press_frequency_table


def _normalize_power(raw: str) -> str:
    """Return uppercase diplomacy power name or raise ValueError."""
    upper = raw.strip().upper()
    if upper in ALL_POWERS:
        return upper
    lower = raw.strip().lower()
    mapped = POWER_NAME_MAP.get(lower)
    if mapped:
        return mapped
    raise ValueError(f"Unknown power: {raw!r}. Must be one of {ALL_POWERS}")


def _load_game_at_phase(game_id: str, game_data_dir: str, phase: str | None = None):
    """Load game fresh from disk, optionally reconstructed at a historical phase.

    When phase is given, the full game (with complete state_history/order_history)
    is loaded so trajectory and relationship analysis work correctly, then the
    current board state is overwritten to match the historical phase using
    set_current_phase + set_units/set_centers — the same pattern used in
    validate_phase_history.
    """
    try:
        from diplomacy import Game
    except ImportError:
        raise ImportError("The 'diplomacy' package is not installed. Run: pip install diplomacy")

    path = os.path.join(game_data_dir, f"{game_id}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"No game data for {game_id!r}. Run the scraper first.")
    with open(path) as f:
        d = json.load(f)

    game = Game.from_dict(d)

    if phase is None:
        return game

    if phase not in d["state_history"]:
        available = list(d["state_history"].keys())
        raise ValueError(f"Phase {phase!r} not in state_history. Available: {available}")

    state = d["state_history"][phase]

    try:
        game.set_current_phase(phase)
    except Exception:
        pass

    # Clear all powers first — Game.from_dict restores the latest board state,
    # which would pollute unit/adjacency lookups for the historical phase.
    for power_name in ALL_POWERS:
        game.set_units(power_name, [], reset=True)
        game.set_centers(power_name, [], reset=True)

    for power_name, unit_list in state.get("units", {}).items():
        try:
            game.set_units(power_name, unit_list, reset=True)
        except Exception:
            pass

    for power_name, center_list in state.get("centers", {}).items():
        try:
            game.set_centers(power_name, center_list, reset=True)
        except Exception:
            pass

    return game


def _sc_trajectory_table(trajectory: dict[int, dict[str, int]]) -> str:
    """Render SC trajectory as a markdown table."""
    if not trajectory:
        return "(no adjustment phases recorded yet)"

    years = sorted(trajectory.keys())
    header = "| Year | " + " | ".join(ALL_POWERS) + " |"
    sep = "|------|" + "|".join(["------"] * len(ALL_POWERS)) + "|"
    rows = [header, sep]
    for year in years:
        counts = trajectory[year]
        row = f"| {year} | " + " | ".join(str(counts.get(p, 0)) for p in ALL_POWERS) + " |"
        rows.append(row)
    return "\n".join(rows)


def _relationships_section(
    rels: dict[str, dict],
    power: str,
    recent_n: int = 3,
    active_powers: set[str] | None = None,
) -> str:
    """Render relationship summary for the given power, skipping eliminated powers."""
    our_rels = rels.get(power, {})
    if not our_rels:
        return "(no movement phases recorded yet)"
    lines = []
    for other, data in sorted(our_rels.items()):
        if active_powers is not None and other not in active_powers:
            continue
        cat = data["category"]
        rs = data["recent_support"]
        ra = data["recent_attack"]
        ts = data["total_support"]
        ta = data["total_attack"]
        lines.append(
            f"- **{other}**: {cat} — last {recent_n}: {rs} sup / {ra} atk"
            f" (all-time: {ts} sup / {ta} atk)"
        )
    return "\n".join(lines) if lines else "(no active opponents)"


def build_advisory_prompt(
    game_id: str,
    power: str,
    game_data_dir: str = "game_data",
    n_recent_phases: int = 3,
    phase: str | None = None,
    include_press: bool = True,
) -> str:
    """
    Build a full advisory markdown prompt for `power` in game `game_id`.
    Loads the game fresh from disk. If `phase` is given (e.g. 'F1919M'),
    reconstructs the board at that historical phase; otherwise uses current phase.
    """
    power_upper = _normalize_power(power)
    game = _load_game_at_phase(game_id, game_data_dir, phase=phase)

    target_phase = game.current_short_phase
    phase_type = target_phase[-1]  # M, A, or R

    # --- SC trajectory ---
    trajectory = build_sc_trajectory(game, up_to_phase=target_phase)
    traj_table = _sc_trajectory_table(trajectory)

    # --- Current board state ---
    state = game.get_state()
    units_by_power = state.get("units", {})
    centers_by_power = state.get("centers", {})

    active_powers = {p for p in ALL_POWERS if units_by_power.get(p) or centers_by_power.get(p)}

    units_lines = []
    for p in ALL_POWERS:
        if p not in active_powers:
            continue
        unit_list = units_by_power.get(p, [])
        units_lines.append(f"- **{p}**: {', '.join(unit_list)}")

    sc_lines = []
    for p in ALL_POWERS:
        if p not in active_powers:
            continue
        sc_list = centers_by_power.get(p, [])
        sc_lines.append(f"- **{p}**: {len(sc_list)} SCs — {', '.join(sorted(sc_list)) if sc_list else '(none)'}")

    # --- Relationships ---
    rels = accumulate_relationships(game, up_to_phase=target_phase)
    rels_section = _relationships_section(rels, power_upper, active_powers=active_powers)

    # --- Diplomatic press (optional) ---
    if include_press:
        press_threads = load_press_context(
            game_id, power_upper, game_data_dir, cutoff_phase=target_phase
        )
        press_body = format_press_section(press_threads, power_upper)
        freq_body  = format_press_frequency_table(press_threads, power_upper)
    else:
        press_body = None
        freq_body  = None

    # --- Per-unit order context (movement phases only) ---
    if phase_type == "M":
        orderable_locs = game.get_orderable_locations(power_upper)
        all_poss = game.get_all_possible_orders()
        power_poss = {loc: all_poss.get(loc, []) for loc in orderable_locs}
        order_context = generate_rich_order_context(game, power_upper, power_poss)
    else:
        order_context = None

    # --- Recent order history (capped at target_phase) ---
    all_order_keys = list(game.order_history.keys())
    str_order_keys = [str(k) for k in all_order_keys]
    if target_phase in str_order_keys:
        all_order_keys = all_order_keys[: str_order_keys.index(target_phase) + 1]
    recent_phases: list[tuple[str, list[str]]] = []
    for key in all_order_keys:
        pn = str(key)
        if not pn.endswith("M"):
            continue
        orders = game.order_history.get(key) or {}
        power_orders = orders.get(power_upper, [])
        if power_orders:
            recent_phases.append((pn, power_orders))

    recent_phases = recent_phases[-n_recent_phases:]
    if recent_phases:
        history_lines = []
        for pn, order_list in recent_phases:
            history_lines.append(f"**{pn}**: {', '.join(order_list)}")
        history_section = "\n".join(history_lines)
    else:
        history_section = "(no movement order history yet)"

    # --- Phase-type note ---
    if phase_type == "A":
        sc_count = len(centers_by_power.get(power_upper, []))
        unit_count = len(units_by_power.get(power_upper, []))
        delta = sc_count - unit_count
        if delta > 0:
            phase_note = f"{delta} build(s) available. Choose home centers to build in."
        elif delta < 0:
            phase_note = f"{-delta} disband(s) required. Choose units to remove."
        else:
            phase_note = "No builds or disbands required this adjustment phase."
    elif phase_type == "R":
        dislodged = [u for u in units_by_power.get(power_upper, []) if u.startswith("*")]
        phase_note = f"{len(dislodged)} unit(s) must retreat or disband."
    else:
        phase_note = "Submit movement orders for all units."

    # --- Assemble prompt ---
    prompt = f"""# Diplomacy Advisory: {power_upper} — {target_phase}

## Game Arc (Supply Center History)

{traj_table}

## Current Board State

### Units (all powers)
{chr(10).join(units_lines)}

### Supply Centers (all powers)
{chr(10).join(sc_lines)}

## Inferred Relationships for {power_upper}

{rels_section}

{"## Communication Frequency" + chr(10) + chr(10) + freq_body + chr(10) + chr(10) if freq_body is not None else ""}{"## Diplomatic Press" + chr(10) + chr(10) + press_body + chr(10) + chr(10) if press_body is not None else ""}{"## Per-Unit Context and Legal Orders" + chr(10) + chr(10) + order_context + chr(10) + chr(10) if order_context is not None else ""}## Recent Order History ({power_upper}, last {n_recent_phases} movement phases)

{history_section}

## Advisory Request

Analyse {power_upper}'s position in {target_phase} and suggest orders.
{phase_note}
{"If diplomatic press is shown above: cross-reference press commitments against the order history. Note any powers whose stated intentions diverged from their actual orders. Flag communication gaps (sudden silence after active correspondence) and unusual over-reassurance as potential stab signals." + chr(10) if include_press else ""}Validate all proposed orders before finalising.
"""
    return prompt.strip()
