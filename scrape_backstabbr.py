#!/usr/bin/env python3
"""
CLI entry point: scrape a backstabbr.com game page and load into diplomacy engine.

Usage:
    python scrape_backstabbr.py <game_url> --cookie "<session_cookie>"
    python scrape_backstabbr.py <url> --cookie "<session>" --dump-html debug.html
    python scrape_backstabbr.py <url> --cookie "<session>" --output game.json
    python scrape_backstabbr.py <url> --cookie "<session>" --dry-run
"""

import argparse
import json
import logging
import os
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scrape a backstabbr.com game page and load into diplomacy engine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("game_url", help="Full URL to the backstabbr game page")
    p.add_argument(
        "--cookie", "-c",
        required=True,
        help="Session cookie string (e.g. 'connect.sid=...' or just the value)",
    )
    p.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="Write converted state dict to FILE as JSON",
    )
    p.add_argument(
        "--dump-html",
        metavar="FILE",
        help="Write raw fetched HTML to FILE for inspection (without parsing)",
    )
    p.add_argument(
        "--dump-state",
        action="store_true",
        help="Print RawGameState as JSON to stdout",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Convert state and print dict but do not construct diplomacy.Game",
    )
    p.add_argument(
        "--coast-hints",
        metavar="JSON",
        help=(
            'JSON dict of province-code → coast to resolve ambiguities. '
            'e.g. \'{"STP": "NC", "BUL": "SC"}\''
        ),
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose debug logging",
    )
    p.add_argument(
        "--history",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Scrape full phase history and persist to game_data/<id>.json (default: on)",
    )
    p.add_argument(
        "--press",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Scrape press message threads (saved to game_data/<id>_press.json) (default: on).",
    )
    p.add_argument(
        "--dump-press-html",
        metavar="DIR",
        help="Debug: save raw HTML from /pressthread and first thread detail to DIR/.",
    )
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    # Parse coast hints
    coast_hints: dict[str, str] | None = None
    if args.coast_hints:
        try:
            coast_hints = json.loads(args.coast_hints)
        except json.JSONDecodeError as e:
            logger.error("Invalid --coast-hints JSON: %s", e)
            return 1

    # Lazy imports (allow --help without installed deps)
    try:
        from backstabbr_advisor import (
            fetch_game_page,
            extract_game_state,
            convert_game_state,
            load_game,
            scrape_and_persist,
        )
        from backstabbr_advisor.exceptions import (
            AuthenticationError,
            ParseError,
            CoastAmbiguityError,
        )
        from backstabbr_advisor.converter import ALL_POWERS
    except ImportError as e:
        logger.error("Missing dependency: %s\nRun: pip install -r requirements.txt", e)
        return 1

    # --- Press HTML probe (debug) ---
    if args.dump_press_html:
        try:
            from backstabbr_advisor import fetch_game_page
            from backstabbr_advisor.press import _press_base, _parse_thread_ids
            from backstabbr_advisor.exceptions import AuthenticationError, PressUnavailableError
        except ImportError as e:
            logger.error("Missing dependency: %s\nRun: pip install -r requirements.txt", e)
            return 1

        press_base = _press_base(args.game_url)
        os.makedirs(args.dump_press_html, exist_ok=True)

        try:
            soup = fetch_game_page(press_base, args.cookie)
            list_path = os.path.join(args.dump_press_html, "thread_list.html")
            with open(list_path, "w", encoding="utf-8") as f:
                f.write(str(soup))
            logger.info("Thread list HTML written to %s", list_path)

            thread_ids = _parse_thread_ids(soup)
            if thread_ids:
                first_id = thread_ids[0]
                detail_url = f"{press_base}/{first_id}"
                soup2 = fetch_game_page(detail_url, args.cookie)
                detail_path = os.path.join(args.dump_press_html, f"thread_{first_id}.html")
                with open(detail_path, "w", encoding="utf-8") as f:
                    f.write(str(soup2))
                logger.info("Thread detail HTML written to %s", detail_path)
            else:
                logger.info("No thread IDs found in thread list; skipping detail fetch.")
        except AuthenticationError as e:
            logger.error("Authentication failed: %s", e)
            return 1
        except Exception as e:
            logger.error("Press HTML dump failed: %s", e)
            if args.verbose:
                import traceback
                traceback.print_exc()
            return 1

        if not args.history:
            return 0

    # --- History mode: scrape all phases and persist ---
    if args.history and not args.dump_html and not args.dump_state:
        logger.info("History mode: scraping all phases for %s", args.game_url)
        try:
            game = scrape_and_persist(args.game_url, args.cookie, save_dir="game_data")
        except AuthenticationError as e:
            logger.error("Authentication failed: %s", e)
            return 1
        except Exception as e:
            logger.error("History scrape failed: %s", e)
            if args.verbose:
                import traceback
                traceback.print_exc()
            return 1

        print("\n--- Game summary ---")
        print(f"Phase: {game.get_current_phase()}")
        print(f"Phases in history: {len(game.state_history)}")
        for power_name in ALL_POWERS:
            try:
                power = game.get_power(power_name)
                print(f"  {power_name:8s}: {len(power.units)} units, {len(power.centers)} SCs")
            except Exception:
                pass

        if args.press:
            from backstabbr_advisor.press import scrape_and_persist_press
            from backstabbr_advisor.exceptions import PressUnavailableError
            try:
                threads = scrape_and_persist_press(args.game_url, args.cookie)
                print(f"Press: {len(threads)} thread(s) saved.")
            except PressUnavailableError:
                logger.info("No press available (gunboat game or press disabled).")
            except Exception as e:
                logger.warning("Press scrape failed: %s", e)
                if args.verbose:
                    import traceback
                    traceback.print_exc()

        return 0

    # --- Step 1: Fetch page ---
    logger.info("Fetching %s", args.game_url)
    try:
        soup = fetch_game_page(args.game_url, args.cookie)
    except AuthenticationError as e:
        logger.error("Authentication failed: %s", e)
        return 1
    except Exception as e:
        logger.error("Failed to fetch page: %s", e)
        return 1

    # --- Step 2: Optionally dump raw HTML ---
    if args.dump_html:
        html = str(soup)
        with open(args.dump_html, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Raw HTML written to %s (%d bytes)", args.dump_html, len(html))
        if not args.dump_state and not args.dry_run and not args.output:
            return 0

    # --- Step 3: Extract raw game state ---
    try:
        raw_state = extract_game_state(soup)
    except ParseError as e:
        logger.error("Parse error: %s", e)
        return 1
    except Exception as e:
        logger.error("Unexpected error during extraction: %s", e)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    logger.info(
        "Extracted: %s %d %s — %d units, %d dislodged, %d powers with SCs",
        raw_state.season,
        raw_state.year,
        raw_state.phase_type,
        len(raw_state.units),
        len(raw_state.dislodged),
        len(raw_state.supply_centers),
    )

    if args.dump_state:
        # Serialize RawGameState to JSON-like dict
        dump = {
            "season": raw_state.season,
            "year": raw_state.year,
            "phase_type": raw_state.phase_type,
            "units": [
                {
                    "power": u.power,
                    "unit_type": u.unit_type,
                    "province": u.province,
                    "coast": u.coast,
                }
                for u in raw_state.units
            ],
            "dislodged": [
                {
                    "power": u.power,
                    "unit_type": u.unit_type,
                    "province": u.province,
                    "coast": u.coast,
                }
                for u in raw_state.dislodged
            ],
            "supply_centers": raw_state.supply_centers,
        }
        print(json.dumps(dump, indent=2))
        if not args.dry_run and not args.output:
            return 0

    # --- Step 4: Convert to diplomacy dict ---
    try:
        state_dict = convert_game_state(raw_state, coast_hints=coast_hints)
    except CoastAmbiguityError as e:
        logger.error(
            "Coast ambiguity: %s\n"
            "Use --coast-hints '{\"STP\": \"NC\"}' to resolve.", e
        )
        return 1
    except Exception as e:
        logger.error("Conversion error: %s", e)
        return 1

    logger.info("Converted to phase %s", state_dict["name"])

    if args.dry_run or args.verbose:
        print("\n--- Converted state dict ---")
        print(json.dumps(state_dict, indent=2))

    # --- Step 5: Write JSON output ---
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(state_dict, f, indent=2)
        logger.info("State dict written to %s", args.output)

    if args.dry_run:
        logger.info("Dry run complete; skipping diplomacy.Game construction.")
        return 0

    # --- Step 6: Load into diplomacy.Game ---
    try:
        game = load_game(state_dict)
    except ImportError as e:
        logger.error("%s", e)
        return 1
    except Exception as e:
        logger.error("Failed to load game: %s", e)
        return 1

    logger.info("diplomacy.Game loaded successfully.")

    # Quick sanity check
    print("\n--- Game summary ---")
    print(f"Phase: {game.get_current_phase()}")
    for power_name in ALL_POWERS:
        try:
            power = game.get_power(power_name)
            n_units = len(power.units)
            n_centers = len(power.centers)
            print(f"  {power_name:8s}: {n_units} units, {n_centers} SCs")
        except Exception:
            pass

    print("\nDone. Use game.get_orderable_locations('FRANCE') etc. to issue orders.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
