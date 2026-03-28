#!/usr/bin/env python3
"""
CLI wrapper for build_advisory_prompt() and order validation.

Usage:
    python advise_backstabbr.py <game_id> <power> [--game-data-dir DIR] [--recent N]
    python advise_backstabbr.py <game_id> <power> --validate "A PAR - BUR" "F BRE H"
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diplomacy advisory tool for backstabbr games."
    )
    parser.add_argument("game_id", help="Numeric game ID (e.g. 5148037665914880)")
    parser.add_argument("power", help="Power name (e.g. ENGLAND)")
    parser.add_argument(
        "--game-data-dir",
        default="game_data",
        help="Directory containing game JSON files (default: game_data)",
    )
    parser.add_argument(
        "--recent",
        type=int,
        default=3,
        metavar="N",
        help="Number of recent movement phases to include (default: 3)",
    )
    parser.add_argument(
        "--phase",
        default=None,
        metavar="PHASE",
        help="Historical phase to advise on, e.g. F1919M (default: current phase)",
    )
    parser.add_argument(
        "--no-press",
        action="store_true",
        default=False,
        help="Suppress diplomatic press and communication frequency sections",
    )
    parser.add_argument(
        "--validate",
        nargs="+",
        metavar="ORDER",
        help="Validate order strings; outputs JSON {valid, invalid, errors}",
    )
    args = parser.parse_args()

    if args.validate:
        _run_validate(args.game_id, args.power, args.validate, args.game_data_dir, args.phase)
    else:
        _run_advisory(args.game_id, args.power, args.game_data_dir, args.recent, args.phase,
                      not args.no_press)


def _run_advisory(
    game_id: str, power: str, game_data_dir: str, n_recent: int,
    phase: str | None, include_press: bool = True,
) -> None:
    from backstabbr_advisor.advisor import build_advisory_prompt

    try:
        prompt = build_advisory_prompt(
            game_id,
            power,
            game_data_dir=game_data_dir,
            n_recent_phases=n_recent,
            phase=phase,
            include_press=include_press,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(prompt)


def _run_validate(
    game_id: str, power: str, orders: list[str], game_data_dir: str, phase: str | None = None
) -> None:
    import json as _json

    from backstabbr_advisor.converter import ALL_POWERS, POWER_NAME_MAP

    # Normalize power
    upper = power.strip().upper()
    if upper not in ALL_POWERS:
        mapped = POWER_NAME_MAP.get(power.strip().lower())
        if mapped:
            upper = mapped
        else:
            print(
                json.dumps(
                    {"valid": [], "invalid": [], "errors": [f"Unknown power: {power!r}"]}
                )
            )
            sys.exit(1)

    # Load game (reconstructed at the historical phase if given)
    try:
        from backstabbr_advisor.advisor import _load_game_at_phase
        game = _load_game_at_phase(game_id, game_data_dir, phase=phase)
    except (FileNotFoundError, ValueError, ImportError) as e:
        print(json.dumps({"valid": [], "invalid": [], "errors": [str(e)]}))
        sys.exit(1)

    valid: list[str] = []
    invalid: list[str] = []
    errors: list[str] = []

    for order in orders:
        game.error.clear()
        game.set_orders(upper, [order])
        if game.error:
            invalid.append(order)
            errors.append(f"{order}: {game.error[0]}")
        else:
            valid.append(order)

    print(json.dumps({"valid": valid, "invalid": invalid, "errors": errors}))


if __name__ == "__main__":
    main()
