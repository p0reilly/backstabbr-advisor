import logging

logger = logging.getLogger(__name__)


def load_game(state_dict: dict):
    """
    Construct a diplomacy.Game from the converted state dict.
    Uses set_units / set_centers / set_current_phase rather than
    Game.from_saved_game_format() which requires full phase history.

    Returns a diplomacy.Game instance ready for order submission / analysis.
    """
    try:
        from diplomacy import Game
    except ImportError:
        raise ImportError(
            "The 'diplomacy' package is not installed. "
            "Run: pip install diplomacy"
        )

    game = Game()

    phase_name = state_dict.get("name", "S1901M")
    try:
        game.set_current_phase(phase_name)
        logger.info("Set phase to %s", phase_name)
    except Exception as e:
        logger.warning("set_current_phase(%r) failed: %s. Phase may not be set correctly.", phase_name, e)

    for power, units in state_dict.get("units", {}).items():
        try:
            game.set_units(power, units, reset=True)
            logger.debug("Set units for %s: %s", power, units)
        except Exception as e:
            logger.warning("set_units(%r, %r) failed: %s", power, units, e)

    for power, centers in state_dict.get("centers", {}).items():
        try:
            game.set_centers(power, centers, reset=True)
            logger.debug("Set centers for %s: %s", power, centers)
        except Exception as e:
            logger.warning("set_centers(%r, %r) failed: %s", power, centers, e)

    return game
