# backstabbr_advisor/order_context.py
#
# Adapted from AI_Diplomacy/ai_diplomacy/possible_order_context.py
# Changes: module docstring replaced; France debug block removed.

from collections import deque
from typing import Dict, List, Callable, Optional, Any, Set, Tuple
from diplomacy.engine.map import Map as GameMap
from diplomacy.engine.game import Game as BoardState
import logging
import re

logger = logging.getLogger(__name__)


def build_diplomacy_graph(game_map: GameMap) -> Dict[str, Dict[str, List[str]]]:
    """
    Return graph[PROV]['ARMY'|'FLEET'] = list of 3-letter neighbour provinces.
    Works for dual-coast provinces by interrogating `abuts()` directly instead
    of relying on loc_abut.
    """
    provs: Set[str] = {
        loc.split("/")[0][:3].upper()
        for loc in game_map.locs
        if len(loc.split("/")[0]) == 3
    }

    graph: Dict[str, Dict[str, List[str]]] = {p: {"ARMY": [], "FLEET": []} for p in provs}

    def variants(code: str) -> List[str]:
        lst = list(game_map.loc_coasts.get(code, []))
        if code not in lst:
            lst.append(code)
        return lst

    for src in provs:
        src_vers = variants(src)

        for dest in provs:
            if dest == src:
                continue
            dest_vers = variants(dest)

            if any(
                game_map.abuts("A", src, "-", dv)
                for dv in dest_vers
            ):
                graph[src]["ARMY"].append(dest)

            if any(game_map.abuts("F", sv, "-", dv) for sv in src_vers for dv in dest_vers):
                graph[src]["FLEET"].append(dest)

    for p in graph:
        graph[p]["ARMY"] = sorted(set(graph[p]["ARMY"]))
        graph[p]["FLEET"] = sorted(set(graph[p]["FLEET"]))

    return graph


def bfs_shortest_path(
    graph: Dict[str, Dict[str, List[str]]],
    board_state: BoardState,
    game_map: GameMap,
    start_loc_full: str,
    unit_type: str,
    is_target_func: Callable[[str, BoardState], bool],
) -> Optional[List[str]]:
    """Performs BFS to find the shortest path from start_loc to a target satisfying is_target_func."""

    start_loc_short = game_map.loc_name.get(start_loc_full, start_loc_full)
    if "/" in start_loc_short:
        start_loc_short = start_loc_short[:3]
    if "/" not in start_loc_full:
        start_loc_short = start_loc_full[:3]
    else:
        start_loc_short = start_loc_full[:3]

    if start_loc_short not in graph:
        logger.warning(f"BFS: Start province {start_loc_short} (from {start_loc_full}) not in graph. Pathfinding may fail.")
        return None

    queue: deque[Tuple[str, List[str]]] = deque([(start_loc_short, [start_loc_short])])
    visited_nodes: Set[str] = {start_loc_short}

    while queue:
        current_loc_short, path = queue.popleft()

        if is_target_func(current_loc_short, board_state):
            return path

        possible_neighbors_short = graph.get(current_loc_short, {}).get(unit_type, [])

        for next_loc_short in possible_neighbors_short:
            if next_loc_short not in visited_nodes:
                if next_loc_short not in graph:
                    logger.warning(f"BFS: Neighbor {next_loc_short} of {current_loc_short} not in graph. Skipping.")
                    continue
                visited_nodes.add(next_loc_short)
                new_path = path + [next_loc_short]
                queue.append((next_loc_short, new_path))
    return None


def get_unit_at_location(board_state: BoardState, location: str) -> Optional[str]:
    """Returns the full unit string (e.g., 'A PAR (FRA)') if a unit is at the location, else None."""
    for power, unit_list in board_state.get("units", {}).items():
        for unit_str in unit_list:
            parts = unit_str.split(" ")
            if len(parts) == 2:
                unit_map_loc = parts[1]
                if unit_map_loc == location:
                    return f"{parts[0]} {location} ({power})"
    return None


def get_sc_controller(game_map: GameMap, board_state: BoardState, location: str) -> Optional[str]:
    """Returns the controlling power's name if the location is an SC, else None."""
    loc_province_name = game_map.loc_name.get(location, location).upper()[:3]
    if loc_province_name not in game_map.scs:
        return None
    for power, sc_list in board_state.get("centers", {}).items():
        if loc_province_name in sc_list:
            return power
    return None


def get_nearest_enemy_units(
    board_state: BoardState,
    graph: Dict[str, Dict[str, List[str]]],
    game_map: GameMap,
    power_name: str,
    start_unit_loc_full: str,
    start_unit_type: str,
    n: int = 3,
) -> List[Tuple[str, List[str]]]:
    """Finds up to N nearest enemy units, sorted by path length."""
    enemy_paths: List[Tuple[str, List[str]]] = []

    all_enemy_unit_locations_full: List[Tuple[str, str]] = []
    for p_name, unit_list_for_power in board_state.get("units", {}).items():
        if p_name != power_name:
            for unit_repr_from_state in unit_list_for_power:
                parts = unit_repr_from_state.split(" ")
                if len(parts) == 2:
                    loc_full = parts[1]
                    full_unit_str_with_power = get_unit_at_location(board_state, loc_full)
                    if full_unit_str_with_power:
                        all_enemy_unit_locations_full.append((loc_full, full_unit_str_with_power))

    for target_enemy_loc_full, enemy_unit_str in all_enemy_unit_locations_full:
        target_enemy_loc_short = game_map.loc_name.get(target_enemy_loc_full, target_enemy_loc_full)
        if "/" in target_enemy_loc_short:
            target_enemy_loc_short = target_enemy_loc_short[:3]
        if "/" not in target_enemy_loc_full:
            target_enemy_loc_short = target_enemy_loc_full[:3]
        else:
            target_enemy_loc_short = target_enemy_loc_full[:3]

        def is_specific_enemy_loc(loc_short: str, current_board_state: BoardState) -> bool:
            return loc_short == target_enemy_loc_short

        path_short_names = bfs_shortest_path(graph, board_state, game_map, start_unit_loc_full, start_unit_type, is_specific_enemy_loc)
        if path_short_names:
            enemy_paths.append((enemy_unit_str, path_short_names))

    enemy_paths.sort(key=lambda x: len(x[1]))
    return enemy_paths[:n]


def get_nearest_uncontrolled_scs(
    game_map: GameMap,
    board_state: BoardState,
    graph: Dict[str, Dict[str, List[str]]],
    power_name: str,
    start_unit_loc_full: str,
    start_unit_type: str,
    n: int = 3,
) -> List[Tuple[str, int, List[str]]]:
    """
    Return up to N nearest supply centres not controlled by `power_name`,
    excluding centres that are the unit's own province (distance 0) or
    adjacent in one move (distance 1).
    """
    results: List[Tuple[str, int, List[str]]] = []

    for sc_short in game_map.scs:
        controller = get_sc_controller(game_map, board_state, sc_short)
        if controller == power_name:
            continue

        def is_target(loc_short: str, _state: BoardState) -> bool:
            return loc_short == sc_short

        path = bfs_shortest_path(
            graph,
            board_state,
            game_map,
            start_unit_loc_full,
            start_unit_type,
            is_target,
        )
        if not path:
            continue

        distance = len(path) - 1

        if distance <= 1 or distance > 3:
            continue

        tag = f"{sc_short} (Ctrl: {controller or 'None'})"
        results.append((tag, distance, path))

    results.sort(key=lambda x: (x[1], x[0]))
    return results[:n]


# ---------------------------------------------------------------------------
# Regex and tiny helpers
# ---------------------------------------------------------------------------

_SIMPLE_MOVE_RE = re.compile(r"^[AF] [A-Z]{3}(?:/[A-Z]{2})? - [A-Z]{3}(?:/[A-Z]{2})?$")
_HOLD_RE = re.compile(r"^[AF] [A-Z]{3}(?:/[A-Z]{2})? H$")


def _is_hold_order(order: str) -> bool:
    return bool(_HOLD_RE.match(order.strip()))


def _norm_power(name: str) -> str:
    return name.strip().upper()


def _is_simple_move(order: str) -> bool:
    return bool(_SIMPLE_MOVE_RE.match(order.strip()))


def _split_move(order: str) -> Tuple[str, str]:
    """Return ('A BUD', 'TRI') from 'A BUD - TRI' (validated move only)."""
    unit_part, dest = order.split(" - ")
    return unit_part.strip(), dest.strip()


# ---------------------------------------------------------------------------
# Gather *all* friendly support orders for a given move
# ---------------------------------------------------------------------------


def _all_support_examples(
    mover: str,
    dest: str,
    all_orders: Dict[str, List[str]],
) -> List[str]:
    target = f"{mover} - {dest}"
    supports: List[str] = []

    for loc, orders in all_orders.items():
        if mover.endswith(loc):
            continue
        for o in orders:
            if " S " in o and target in o:
                supports.append(o.strip())

    return supports


def _all_support_hold_examples(
    holder: str,
    all_orders: Dict[str, List[str]],
) -> List[str]:
    target = f" S {holder}"
    supports: List[str] = []

    for loc, orders in all_orders.items():
        if holder.endswith(loc):
            continue
        for o in orders:
            if o.strip().endswith(target):
                supports.append(o.strip())
    return supports


# ---------------------------------------------------------------------------
# Province-type resolver
# ---------------------------------------------------------------------------


def _province_type_display(game_map, prov_short: str) -> str:
    for full in game_map.loc_coasts.get(prov_short, [prov_short]):
        t = game_map.loc_type.get(full)
        if not t:
            continue
        t = t.upper()
        if t in ("LAND", "L"):
            return "LAND"
        if t in ("COAST", "C"):
            return "COAST"
        if t in ("WATER", "SEA", "W"):
            return "WATER"
    return "UNKNOWN"


def _dest_occupancy_desc(
    dest_short: str,
    game_map,
    board_state,
    our_power: str,
) -> str:
    occupant: Optional[str] = None
    for full in game_map.loc_coasts.get(dest_short, [dest_short]):
        u = get_unit_at_location(board_state, full)
        if u:
            occupant = u.split(" ")[-1].strip("()")
            break
    if occupant is None:
        return "(unoccupied)"
    if occupant == our_power:
        return f"(occupied by {occupant} — you!)"
    return f"(occupied by {occupant})"


# ---------------------------------------------------------------------------
# Adjacent-territory lines (used by movement-phase builder)
# ---------------------------------------------------------------------------


def _adjacent_territory_lines(
    graph,
    game_map,
    board_state,
    unit_loc_full: str,
    mover_descr: str,
    our_power: str,
) -> List[str]:
    lines: List[str] = []
    indent1 = "  "
    indent2 = "    "

    unit_loc_short = game_map.loc_name.get(unit_loc_full, unit_loc_full)[:3]
    mover_type_key = "ARMY" if mover_descr.startswith("A") else "FLEET"
    adjacents = graph.get(unit_loc_short, {}).get(mover_type_key, [])

    for adj in adjacents:
        typ_display = _province_type_display(game_map, adj)

        base_parts = [f"{indent1}{adj} ({typ_display})"]

        sc_ctrl = get_sc_controller(game_map, board_state, adj)
        if sc_ctrl:
            base_parts.append(f"SC Control: {sc_ctrl}")

        unit_here = None
        for full in game_map.loc_coasts.get(adj, [adj]):
            unit_here = get_unit_at_location(board_state, full)
            if unit_here:
                break
        if unit_here:
            base_parts.append(f"Units: {unit_here}")

        lines.append(" ".join(base_parts))

        if unit_here:
            pwr = unit_here.split(" ")[-1].strip("()")
            if pwr == our_power:
                friend_descr = unit_here.split(" (")[0]
                lines.append(f"{indent2}Support hold: {mover_descr} S {friend_descr}")
            else:
                lines.append(f"{indent2}-> {unit_here} can support or contest {mover_descr}'s moves and vice-versa")

    return lines


# ---------------------------------------------------------------------------
# Movement-phase generator
# ---------------------------------------------------------------------------


def _generate_rich_order_context_movement(
    game: Any,
    power_name: str,
    possible_orders_for_power: Dict[str, List[str]],
) -> str:
    board_state = game.get_state()
    game_map = game.map
    graph = build_diplomacy_graph(game_map)

    blocks: List[str] = []
    me = _norm_power(power_name)

    for unit_loc_full, orders in possible_orders_for_power.items():
        unit_full_str = get_unit_at_location(board_state, unit_loc_full)
        if not unit_full_str:
            continue

        unit_power = unit_full_str.split(" ")[-1].strip("()")
        if _norm_power(unit_power) != me:
            continue

        mover_descr, _ = _split_move(f"{unit_full_str.split(' ')[0]} {unit_loc_full} - {unit_loc_full}")

        prov_short = game_map.loc_name.get(unit_loc_full, unit_loc_full)[:3]
        prov_type_disp = _province_type_display(game_map, prov_short)
        sc_tag = " (SC)" if prov_short in game_map.scs else ""

        owner = get_sc_controller(game_map, board_state, unit_loc_full) or "None"
        owner_line = f"Held by {owner} (You)" if owner == power_name else f"Held by {owner}"

        ind = "  "
        block: List[str] = [f"<Territory {prov_short}>"]
        block.append(f"{ind}({prov_type_disp}){sc_tag}")
        block.append(f"{ind}{owner_line}")
        block.append(f"{ind}Units present: {unit_full_str}")

        block.append("# Adjacent territories:")
        block.extend(_adjacent_territory_lines(graph, game_map, board_state, unit_loc_full, mover_descr, power_name))

        block.append("# Nearest units (not ours):")
        enemies = get_nearest_enemy_units(
            board_state,
            graph,
            game_map,
            power_name,
            unit_loc_full,
            "ARMY" if mover_descr.startswith("A") else "FLEET",
            n=3,
        )
        for u, path in enemies:
            path_disp = "→".join([unit_loc_full] + path[1:])
            block.append(f"{ind}{u}, path [{path_disp}]")

        scs = get_nearest_uncontrolled_scs(
            game_map,
            board_state,
            graph,
            power_name,
            unit_loc_full,
            "ARMY" if mover_descr.startswith("A") else "FLEET",
            n=3,
        )
        if scs:
            block.append("# Nearest supply centers (not controlled by us):")
            for sc_str, dist, sc_path in scs:
                path_disp = "→".join([unit_loc_full] + sc_path[1:])
                sc_fmt = sc_str.replace("Ctrl:", "Controlled by")
                block.append(f"{ind}{sc_fmt}, path [{path_disp}]")

        block.append(f"# Possible {mover_descr} unit movements & supports:")

        simple_moves = [o for o in orders if _is_simple_move(o)]
        hold_orders = [o for o in orders if _is_hold_order(o)]

        if not simple_moves and not hold_orders:
            block.append(f"{ind}None")
        else:
            for mv in simple_moves:
                mover, dest = _split_move(mv)
                occ = _dest_occupancy_desc(dest.split("/")[0][:3], game_map, board_state, power_name)
                # Skip moves into own occupied territories — they're rarely intentional
                # and generate a lot of noise (bounce-only scenarios).
                if "you!" in occ:
                    continue
                block.append(f"{ind}{mv} {occ}")

                for s in _all_support_examples(mover, dest, possible_orders_for_power):
                    block.append(f"{ind * 2}Available Support: {s}")

            for hd in hold_orders:
                holder = hd.split(" H")[0]
                block.append(f"{ind}{hd}")

                for s in _all_support_hold_examples(holder, possible_orders_for_power):
                    block.append(f"{ind * 2}Available Support: {s}")

        block.append(f"</Territory {prov_short}>")
        blocks.append("\n".join(block))

    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Retreat-phase builder
# ---------------------------------------------------------------------------


def _generate_rich_order_context_retreat(
    game: Any,
    power_name: str,
    possible_orders_for_power: Dict[str, List[str]],
) -> str:
    lines: List[str] = []
    for orders in possible_orders_for_power.values():
        for o in orders:
            lines.append(o.strip())

    return "\n".join(lines) if lines else "(No dislodged units)"


# ---------------------------------------------------------------------------
# Adjustment-phase builder
# ---------------------------------------------------------------------------


def _generate_rich_order_context_adjustment(
    game: Any,
    power_name: str,
    possible_orders_for_power: Dict[str, List[str]],
) -> str:
    """
    * First line states how many builds are allowed or disbands required.
    * Echo every B/D order exactly as supplied, skipping WAIVE.
    * No wrapper tags.
    """
    board_state = game.get_state()
    sc_owned = len(board_state.get("centers", {}).get(power_name, []))
    units_num = len(board_state.get("units", {}).get(power_name, []))
    delta = sc_owned - units_num

    if delta > 0:
        summary = f"Builds available: {delta}"
    elif delta < 0:
        summary = f"Disbands required: {-delta}"
    else:
        summary = "No builds or disbands required"

    lines: List[str] = [summary]
    for orders in possible_orders_for_power.values():
        for o in orders:
            if "WAIVE" in o.upper():
                continue
            lines.append(o.strip())

    return "\n".join(lines) if len(lines) > 1 else summary


# ---------------------------------------------------------------------------
# Condensed summary builder
# ---------------------------------------------------------------------------


def _generate_condensed_move_summary(
    game: Any,
    power_name: str,
    possible_orders_for_power: Dict[str, List[str]],
) -> str:
    board_state = game.get_state()

    our_unit_descs: Set[str] = set()
    for u in board_state.get("units", {}).get(power_name, []):
        kind, loc_full = u.split(" ")
        base = loc_full.split("/")[0]
        our_unit_descs.update({f"{kind} {loc_full}", f"{kind} {base}"})

    lines: List[str] = [
        "# Summary of possible orders (not including supports of other powers' units):"
    ]

    for loc in sorted(possible_orders_for_power.keys()):
        unit_full = get_unit_at_location(board_state, loc)
        if not unit_full:
            continue
        unit_desc = unit_full.split(" (")[0].strip()
        if unit_desc not in our_unit_descs:
            continue

        orders = possible_orders_for_power[loc]
        simple_moves = [o for o in orders if _is_simple_move(o)]
        hold_orders  = [o for o in orders if _is_hold_order(o)]

        lines.append(f"## {unit_desc} possible orders:")

        def _friendly_supports_for(target_order: str) -> List[str]:
            if " - " in target_order:
                mover, dest = _split_move(target_order)
                supps = _all_support_examples(mover, dest, possible_orders_for_power)
            else:
                holder = target_order.split(" H")[0]
                supps = _all_support_hold_examples(holder, possible_orders_for_power)

            friendly: List[str] = []
            for s in supps:
                tgt = (
                    s.split(" S ", 1)[1]
                      .split(" - ")[0]
                      .split(" H")[0]
                      .strip()
                )
                if tgt in our_unit_descs:
                    friendly.append(s)
            return friendly

        for mv in simple_moves:
            lines.append(mv)
            for s in _friendly_supports_for(mv):
                lines.append(f"    {s}")

        for hd in hold_orders:
            lines.append(hd)
            for s in _friendly_supports_for(hd):
                lines.append(f"    {s}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


def generate_rich_order_context(
    game: Any,
    power_name: str,
    possible_orders_for_power: Dict[str, List[str]],
    *,
    include_full: bool = True,
    include_summary: bool = False,
) -> str:
    """
    Dispatch to phase-specific builders and (optionally) append the condensed
    move summary.

    Args:
        include_full    – emit the full rich context (default True)
        include_summary – emit the condensed per-unit order list (default False)
    """
    phase_type = game.current_short_phase[-1]
    sections: List[str] = []

    if include_full:
        if phase_type == "M":
            sections.append(
                _generate_rich_order_context_movement(
                    game, power_name, possible_orders_for_power
                )
            )
        elif phase_type == "R":
            sections.append(
                _generate_rich_order_context_retreat(
                    game, power_name, possible_orders_for_power
                )
            )
        elif phase_type == "A":
            sections.append(
                _generate_rich_order_context_adjustment(
                    game, power_name, possible_orders_for_power
                )
            )
        else:
            sections.append(
                _generate_rich_order_context_movement(
                    game, power_name, possible_orders_for_power
                )
            )

    if include_summary and phase_type == "M":
        sections.append(
            _generate_condensed_move_summary(
                game, power_name, possible_orders_for_power
            )
        )

    return "\n\n".join(sections).strip()
