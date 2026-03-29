#!/usr/bin/env python3
"""
Get searchbot move recommendations for a backstabbr game.

Loads the backstabbr game JSON, converts the current board state to pydipcc
format, and runs ModelSampledAgent to produce neural-network-recommended orders.

Usage:
    python searchbot_recommend.py <game_id> <power>
    python searchbot_recommend.py <game_id> <power> --model <ckpt>
    python searchbot_recommend.py <game_id> <power> --phase S1910M
    python searchbot_recommend.py <game_id> <power> --all-powers
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEARCHBOT_DEFAULT = os.path.expanduser("~/IdeaProjects/diplomacy_searchbot")
_MODEL_DEFAULT_REL = "models/neurips21_human_dnvi_npu_epoch000500.ckpt"

_SEASON_TO_CODE = {"SPRING": "S", "FALL": "F", "WINTER": "W"}
_PHASE_TO_CODE = {"MOVEMENT": "M", "RETREATS": "R", "ADJUSTMENTS": "A"}

ALL_POWERS = ["AUSTRIA", "ENGLAND", "FRANCE", "GERMANY", "ITALY", "RUSSIA", "TURKEY"]

# Standard home supply centers per power (for builds computation).
_HOMES: dict[str, list[str]] = {
    "AUSTRIA": ["BUD", "TRI", "VIE"],
    "ENGLAND": ["EDI", "LON", "LVP"],
    "FRANCE":  ["BRE", "MAR", "PAR"],
    "GERMANY": ["BER", "KIE", "MUN"],
    "ITALY":   ["NAP", "ROM", "VEN"],
    "RUSSIA":  ["MOS", "SEV", "STP", "WAR"],
    "TURKEY":  ["ANK", "CON", "SMY"],
}

# ---------------------------------------------------------------------------
# Phase name helpers
# ---------------------------------------------------------------------------

def _long_to_short_phase(long_phase: str) -> str:
    """Convert python-diplomacy long phase name to pydipcc short form.

    e.g. "SPRING 1901 MOVEMENT" → "S1901M"
         "FALL 1903 RETREATS"   → "F1903R"
         "WINTER 1905 ADJUSTMENTS" → "W1905A"
    """
    parts = long_phase.upper().split()
    season = _SEASON_TO_CODE[parts[0]]
    year = parts[1]
    phase_type = _PHASE_TO_CODE[parts[2]]
    return f"{season}{year}{phase_type}"


# ---------------------------------------------------------------------------
# Builds computation
# ---------------------------------------------------------------------------

def _compute_builds(
    power: str,
    units: list[str],
    centers: list[str],
    short_phase: str,
) -> dict:
    """Return a pydipcc Builds object for the given power and phase."""
    if not short_phase.endswith("A"):
        return {"count": 0, "homes": []}

    non_dislodged = [u for u in units if not u.startswith("*")]
    diff = len(centers) - len(non_dislodged)
    if diff <= 0:
        return {"count": diff, "homes": []}

    # Builds available — find home SCs that are owned and unoccupied.
    occupied = {u.split()[-1] for u in non_dislodged}
    available = [h for h in _HOMES.get(power, []) if h in centers and h not in occupied]
    return {"count": min(diff, len(available)), "homes": available}


# ---------------------------------------------------------------------------
# pydipcc JSON construction
# ---------------------------------------------------------------------------

def _build_pydipcc_json(
    game_id: str,
    short_phase: str,
    units: dict[str, list[str]],
    centers: dict[str, list[str]],
    retreats: dict | None = None,
) -> str:
    """Construct a minimal single-phase pydipcc-format JSON string."""
    if retreats is None:
        retreats = {}

    builds = {
        power: _compute_builds(power, units.get(power, []), centers.get(power, []), short_phase)
        for power in ALL_POWERS
    }

    phase_obj = {
        "name": short_phase,
        "messages": {},
        "orders": {},
        "state": {
            "name": short_phase,
            "units": units,
            "centers": centers,
            "retreats": retreats,
            "builds": builds,
        },
    }

    game_obj = {
        "version": "1.0",
        "id": f"backstabbr_{game_id}",
        "is_full_press": False,
        "map": "standard",
        "scoring_system": "sum_of_squares",
        "phases": [phase_obj],
    }

    return json.dumps(game_obj)


# ---------------------------------------------------------------------------
# Game data loading
# ---------------------------------------------------------------------------

def _load_state(
    game_id: str,
    game_data_dir: str,
    phase: str | None,
) -> tuple[str, dict[str, list[str]], dict[str, list[str]], dict]:
    """Return (short_phase, units, centers, retreats) for the given game/phase."""
    path = os.path.join(game_data_dir, f"{game_id}.json")
    if not os.path.exists(path):
        print(f"Error: no game data for {game_id!r}. Run the scraper first.", file=sys.stderr)
        sys.exit(1)

    with open(path) as f:
        d = json.load(f)

    if phase is not None:
        # Historical phase from state_history.
        history = d.get("state_history", {})
        if phase not in history:
            available = list(history.keys())
            print(
                f"Error: phase {phase!r} not in state_history.\nAvailable: {available}",
                file=sys.stderr,
            )
            sys.exit(1)
        state = history[phase]
        return phase, state["units"], state["centers"], state.get("retreats", {})

    # Current phase — python-diplomacy Game.to_dict() stores current units/centers
    # per power under d["powers"][POWER]["units"] / ["centers"], not at the top level.
    long_phase = d.get("phase", "")
    if not long_phase:
        print("Error: game JSON has no 'phase' field.", file=sys.stderr)
        sys.exit(1)
    short_phase = _long_to_short_phase(long_phase)
    powers_data = d.get("powers", {})
    units = {p: powers_data[p]["units"] for p in ALL_POWERS if p in powers_data}
    centers = {p: powers_data[p]["centers"] for p in ALL_POWERS if p in powers_data}
    retreats = {p: powers_data[p].get("retreats", {}) for p in ALL_POWERS if p in powers_data}
    return short_phase, units, centers, retreats


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

import re as _re

_DEST_RE = _re.compile(r"- (\w+)")


def _order_attacks_ally(order_str: str, ally_provinces: set) -> bool:
    """True if the order targets an ally-occupied province.

    Covers all offensive order types via the same "- DEST" pattern:
      - Direct move:       "A PAR - BUR"
      - Support of move:   "A CON S F BLA - ANK"
      - Move via convoy:   "A BRE - LON VIA"
      - Fleet convoy:      "F MID C A BRE - LON"  (fleet + army both masked)
    Hold, retreat, disband, and build orders have no "- DEST" and are never masked.
    """
    m = _DEST_RE.search(order_str)
    return m is not None and m.group(1) in ally_provinces


def _mask_ally_attacks(inputs, power: str, ally_provinces: set) -> None:
    """Zero out x_possible_actions entries that attack an ally-occupied province."""
    if not ally_provinces:
        return
    from fairdiplomacy.models.consts import POWERS  # type: ignore
    from fairdiplomacy.utils.order_idxs import ORDER_VOCABULARY, EOS_IDX  # type: ignore

    power_idx = POWERS.index(power)
    pa = inputs["x_possible_actions"]  # shape [B, 7, S, 469]
    pa_cpu = pa[0, power_idx].cpu()    # [S, 469]
    for s in range(pa_cpu.shape[0]):
        for j in range(pa_cpu.shape[1]):
            idx = pa_cpu[s, j].item()
            if idx == EOS_IDX:
                break  # rest of row is padding
            if 0 <= idx < len(ORDER_VOCABULARY):
                if _order_attacks_ally(ORDER_VOCABULARY[idx], ally_provinces):
                    pa_cpu[s, j] = EOS_IDX
    pa[0, power_idx] = pa_cpu.to(pa.device)


def _run_inference(
    game_json: str,
    powers: list[str],
    model_path: str,
    searchbot_dir: str,
    temperature: float,
    top_p: float,
    allies: list[str] | None = None,
) -> dict[str, list[str]]:
    sys.path.insert(0, searchbot_dir)

    try:
        from conf import agents_cfgs  # type: ignore
        from fairdiplomacy.agents.model_sampled_agent import ModelSampledAgent  # type: ignore
        from fairdiplomacy import pydipcc  # type: ignore
        from fairdiplomacy.models.consts import POWERS as ALL_POWERS_ORDERED  # type: ignore
    except ImportError as e:
        print(f"Error: failed to import diplomacy_searchbot from {searchbot_dir!r}: {e}", file=sys.stderr)
        sys.exit(1)

    game = pydipcc.Game.from_json(game_json)

    # temperature=0.1 is the production value from conf/common/agents/model_sampled.prototxt
    # (near-greedy — picks highest-probability orders consistently).
    # temperature=1.0 is the rollout/exploration value used inside SearchBot, not for final moves.
    cfg = agents_cfgs.ModelSampledAgent(
        model_path=model_path,
        temperature=temperature,
        top_p=top_p,
    )
    agent = ModelSampledAgent(cfg)

    if not allies:
        results: dict[str, list[str]] = {}
        for power in powers:
            results[power] = agent.get_orders(game, power)
        return results

    # Build set of provinces currently occupied by allied units.
    ally_provinces: set[str] = set()
    state = game.get_state()
    for ally in allies:
        for unit in state["units"].get(ally, []):
            # unit strings: "A ANK", "F CON", "* A SMY" (dislodged — skip)
            parts = unit.split()
            if parts[0] == "*":
                continue
            loc = parts[-1].split("/")[0]  # strip coast suffix e.g. STP/NC -> STP
            ally_provinces.add(loc)

    if ally_provinces:
        print(f"Ally filter active — masking attacks on: {sorted(ally_provinces)}", file=sys.stderr)

    # Replicate get_orders_many_powers with masking injected before the forward pass.
    encode_fn = (
        agent.input_encoder.encode_inputs_all_powers
        if agent.model.is_all_powers()
        else agent.input_encoder.encode_inputs
    )
    inputs = encode_fn([game]).to(agent.device)

    for power in powers:
        if len(game.get_orderable_locations().get(power, [])) > 0:
            _mask_ally_attacks(inputs, power, ally_provinces)

    actions, _, _ = agent.model.do_model_request(
        inputs, temperature=agent.temperature, top_p=agent.top_p
    )
    actions = actions[0]  # remove batch dim
    return {p: a for p, a in zip(ALL_POWERS_ORDERED, actions) if p in powers}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Get searchbot ML move recommendations for a backstabbr game."
    )
    parser.add_argument("game_id", help="Numeric game ID (e.g. 5148037665914880)")
    parser.add_argument(
        "power",
        help="Power name (e.g. FRANCE). Use ALL for all powers.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Path to model checkpoint (.ckpt). "
            f"Defaults to <searchbot-dir>/{_MODEL_DEFAULT_REL}"
        ),
    )
    parser.add_argument(
        "--searchbot-dir",
        default=_SEARCHBOT_DEFAULT,
        help=f"Path to diplomacy_searchbot repo root (default: {_SEARCHBOT_DEFAULT})",
    )
    parser.add_argument(
        "--game-data-dir",
        default="game_data",
        help="Directory containing backstabbr game JSON files (default: game_data)",
    )
    parser.add_argument(
        "--phase",
        default=None,
        metavar="PHASE",
        help="Short phase name to advise on, e.g. S1910M (default: current phase)",
    )
    parser.add_argument(
        "--all-powers",
        action="store_true",
        help="Run inference for all seven powers instead of just one",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="Sampling temperature (default: 0.1 — near-greedy, matches production model_sampled config)",
    )
    parser.add_argument(
        "--top-p",
        type=float,
        default=1.0,
        help="Top-p (nucleus) sampling (default: 1.0)",
    )
    parser.add_argument(
        "--ally",
        dest="allies",
        action="append",
        default=[],
        metavar="POWER",
        help=(
            "Treat POWER as an ally — moves attacking their units are masked before inference. "
            "May be repeated: --ally TURKEY --ally RUSSIA"
        ),
    )
    args = parser.parse_args()

    # Resolve model path.
    searchbot_dir = os.path.expanduser(args.searchbot_dir)
    model_path = args.model or os.path.join(searchbot_dir, _MODEL_DEFAULT_REL)
    if not os.path.exists(model_path):
        print(
            f"Error: model checkpoint not found at {model_path!r}.\n"
            f"Run bin/download_dora_models.sh inside {searchbot_dir!r}, "
            f"or pass --model <path>.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Determine which powers to run.
    power_upper = args.power.upper()
    if args.all_powers or power_upper == "ALL":
        powers = ALL_POWERS
    else:
        if power_upper not in ALL_POWERS:
            print(f"Error: unknown power {args.power!r}. Must be one of {ALL_POWERS}.", file=sys.stderr)
            sys.exit(1)
        powers = [power_upper]

    # Normalise and validate ally list.
    allies = [a.strip().upper() for a in args.allies]
    for ally in allies:
        if ally not in ALL_POWERS:
            print(f"Error: unknown ally {ally!r}. Must be one of {ALL_POWERS}.", file=sys.stderr)
            sys.exit(1)

    # Load game state.
    short_phase, units, centers, retreats = _load_state(
        args.game_id, args.game_data_dir, args.phase
    )

    # Build pydipcc JSON.
    game_json = _build_pydipcc_json(args.game_id, short_phase, units, centers, retreats)

    # Run inference.
    print(f"Loading model: {model_path}", file=sys.stderr)
    recommendations = _run_inference(
        game_json, powers, model_path, searchbot_dir, args.temperature, args.top_p,
        allies=allies,
    )

    # Print results.
    for power, orders in recommendations.items():
        print(f"Phase: {short_phase}  Power: {power}")
        for order in orders:
            print(f"  {order}")


if __name__ == "__main__":
    main()
