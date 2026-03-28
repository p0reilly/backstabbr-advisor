"""
Shared analysis helpers for the backstabbr advisor.

- accumulate_relationships: aggregate support/attack counts across all movement phases
- build_sc_trajectory: SC counts per power per year from adjustment phases
"""
from __future__ import annotations

from collections import defaultdict

from .converter import ALL_POWERS


def accumulate_relationships(
    game,
    *,
    up_to_phase: str | None = None,
    recency_decay: float = 0.8,
    recent_n: int = 3,
) -> dict[str, dict[str, dict]]:
    """
    Aggregate support/attack counts across movement phases in state_history,
    weighting recent phases more heavily.

    Args:
        recency_decay: Exponential decay factor per phase (0 < decay <= 1).
            decay=1.0 gives uniform weighting; decay=0.8 means each phase back
            counts 80% as much as the next more-recent one.
        recent_n: Number of most-recent movement phases to include as raw counts
            in the "recent_support" / "recent_attack" return fields.

    Returns:
        {power: {other: {
            "support": float,        # decay-weighted total (used for category)
            "attack": float,         # decay-weighted total (used for category)
            "category": str,         # derived from weighted totals
            "recent_support": int,   # raw count over last recent_n phases
            "recent_attack": int,    # raw count over last recent_n phases
            "total_support": int,    # raw all-time count
            "total_attack": int,     # raw all-time count
        }}}
    """
    all_keys = list(game.state_history.keys())
    if up_to_phase is not None:
        str_keys = [str(k) for k in all_keys]
        if up_to_phase in str_keys:
            all_keys = all_keys[: str_keys.index(up_to_phase) + 1]

    # Collect per-phase raw counts in chronological order.
    phases: list[tuple[dict, dict]] = []  # [(friendly_counts, hostile_counts), ...]
    for key in all_keys:
        if not str(key).endswith("M"):
            continue
        state = game.state_history.get(key)
        orders = game.order_history.get(key) or {}
        phases.append(_phase_counts(state, orders))

    n = len(phases)

    # Weighted totals (floats) for category derivation.
    weighted_f: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    weighted_h: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    # Raw integer totals for display.
    total_f: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    total_h: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # Raw counts for the most-recent window.
    recent_f: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    recent_h: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for i, (f_counts, h_counts) in enumerate(phases):
        weight = recency_decay ** (n - 1 - i)
        is_recent = i >= n - recent_n
        for p, others in f_counts.items():
            for o, cnt in others.items():
                weighted_f[p][o] += cnt * weight
                total_f[p][o] += cnt
                if is_recent:
                    recent_f[p][o] += cnt
        for p, others in h_counts.items():
            for o, cnt in others.items():
                weighted_h[p][o] += cnt * weight
                total_h[p][o] += cnt
                if is_recent:
                    recent_h[p][o] += cnt

    result: dict[str, dict[str, dict]] = {}
    for p in ALL_POWERS:
        result[p] = {}
        for o in ALL_POWERS:
            if o == p:
                continue
            wf = weighted_f[p][o]
            wh = weighted_h[p][o]
            result[p][o] = {
                "support": wf,
                "attack": wh,
                "category": _categorise(wf, wh),
                "recent_support": recent_f[p][o],
                "recent_attack": recent_h[p][o],
                "total_support": total_f[p][o],
                "total_attack": total_h[p][o],
            }
    return result


def _categorise(f: float, h: float) -> str:
    if h == 0 and f == 0:
        return "Neutral"
    if h == 0 and f < 1:
        return "Friendly"
    if h == 0 and f >= 1:
        return "Ally"
    if f == 0 and h < 1:
        return "Unfriendly"
    if f == 0 and h >= 1:
        return "Enemy"
    net = f - h
    if net >= 2:
        return "Ally"
    if net >= 0.5:
        return "Friendly"
    if net >= -0.5:
        return "Neutral"
    if net >= -1:
        return "Unfriendly"
    return "Enemy"


def _phase_counts(
    state_entry: dict,
    orders_entry: dict,
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """Return (friendly, hostile) raw count dicts for one movement phase."""
    friendly: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    hostile: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    prov_to_power: dict[str, str] = {}
    for power, unit_list in state_entry.get("units", {}).items():
        for u in unit_list:
            prov = u.lstrip("*").split()[1].split("/")[0]
            prov_to_power[prov] = power

    for power, order_list in (orders_entry or {}).items():
        for order in order_list:
            tokens = order.split()
            if len(tokens) < 3:
                continue
            verb = tokens[2]
            if verb == "S" and len(tokens) >= 5:
                supported_prov = tokens[4].split("/")[0]
                owner = prov_to_power.get(supported_prov)
                if owner and owner != power:
                    friendly[power][owner] += 1
            elif verb == "C" and len(tokens) >= 5:
                convoyed_prov = tokens[4].split("/")[0]
                owner = prov_to_power.get(convoyed_prov)
                if owner and owner != power:
                    friendly[power][owner] += 1
            elif verb == "-" and len(tokens) >= 4:
                dest_prov = tokens[3].split("/")[0]
                owner = prov_to_power.get(dest_prov)
                if owner and owner != power:
                    hostile[power][owner] += 1

    return dict(friendly), dict(hostile)


def build_sc_trajectory(game, *, up_to_phase: str | None = None) -> dict[int, dict[str, int]]:
    """
    SC counts per power per year from W*A phases in state_history.

    Returns:
        {year: {power: sc_count}}
    """
    trajectory: dict[int, dict[str, int]] = {}

    all_keys = list(game.state_history.keys())
    if up_to_phase is not None:
        str_keys = [str(k) for k in all_keys]
        if up_to_phase in str_keys:
            all_keys = all_keys[: str_keys.index(up_to_phase) + 1]
    for key in all_keys:
        phase_name = str(key)
        if not phase_name.endswith("A"):
            continue
        # Parse year from phase name like "W1905A"
        try:
            year = int(phase_name[1:-1])
        except ValueError:
            continue

        state = game.state_history.get(key)
        centers = state.get("centers", {})
        trajectory[year] = {p: len(centers.get(p, [])) for p in ALL_POWERS}

    return trajectory
