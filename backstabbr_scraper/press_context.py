"""
Press context helpers for the advisory prompt.

Loads, normalises, and formats diplomatic press for inclusion in advisory prompts.
Provides a communication-frequency table as an objective stab-signal indicator.
"""
from __future__ import annotations

import os
from collections import defaultdict

from .press import PressMessage, PressThread, load_press

# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------

_PRESS_SEASON_TO_CODE   = {"Spring": "S", "Fall": "F", "Winter": "W"}
_PRESS_SEASON_TO_SUFFIX = {"Spring": "M", "Fall": "M", "Winter": "A"}

# NOTE: never compare short phase codes lexicographically — "F" < "S" alphabetically
# but Fall comes AFTER Spring within a year. Always use _phase_key() for ordering.
_SEASON_ORDER = {"S": 0, "F": 1, "W": 2}


def _press_phase_to_short(press_phase: str) -> str | None:
    """Convert a press phase string to a short game phase code.

    'Spring 1905' -> 'S1905M'
    'Fall 1918'   -> 'F1918M'
    'Winter 1901' -> 'W1901A'

    Returns None if the string is empty, malformed, or uses an unknown season,
    so callers can safely skip those messages without raising.
    """
    parts = press_phase.strip().split()
    if len(parts) != 2:
        return None
    season, year = parts
    code   = _PRESS_SEASON_TO_CODE.get(season)
    suffix = _PRESS_SEASON_TO_SUFFIX.get(season)
    if code is None or not year.isdigit():
        return None
    return f"{code}{year}{suffix}"


def _phase_key(short: str) -> tuple[int, int]:
    """Return an orderable key for a short phase code.

    'S1905M' -> (1905, 0)
    'F1905M' -> (1905, 1)
    'W1905A' -> (1905, 2)

    Use this instead of lexicographic comparison — 'F' < 'S' alphabetically
    but Fall comes after Spring within a year.
    """
    return (int(short[1:-1]), _SEASON_ORDER[short[0]])


# ---------------------------------------------------------------------------
# Loading and filtering
# ---------------------------------------------------------------------------

def load_press_context(
    game_id: str,
    power: str,
    game_data_dir: str,
    cutoff_phase: str,
) -> list[PressThread]:
    """Load and filter press threads relevant to `power` at or before `cutoff_phase`.

    Args:
        game_id:       numeric game ID string, e.g. "5148037665914880"
        power:         uppercase power name, e.g. "AUSTRIA"
        game_data_dir: directory containing game JSON files
        cutoff_phase:  short phase code, e.g. "F1918M" — messages after this are excluded

    Returns a list of PressThread objects with:
      - "You" replaced by power.title() in recipients and message authors
      - Only threads where `power` is a participant
      - Messages filtered to those at or before cutoff_phase
      - Threads with no remaining messages omitted

    Returns [] if the press file is missing (press is optional).
    """
    path = os.path.join(game_data_dir, f"{game_id}_press.json")
    raw_threads = load_press(path)  # returns {} if missing
    if not raw_threads:
        return []

    cutoff_key = _phase_key(cutoff_phase)
    power_title = power.title()
    power_upper = power.upper()
    result: list[PressThread] = []

    for thread in raw_threads.values():
        # Normalise recipients: "You" -> power.title()
        norm_recipients = [
            power_title if r.upper() == "YOU" else r
            for r in thread.recipients
        ]

        # Filter: only threads where our power is a participant
        if power_upper not in {r.upper() for r in norm_recipients}:
            continue

        # Filter and normalise messages
        filtered_messages: list[PressMessage] = []
        for msg in thread.messages:
            short = _press_phase_to_short(msg.phase)
            if short is None:
                continue  # unparseable phase stamp — skip
            if _phase_key(short) > cutoff_key:
                continue  # after cutoff
            norm_author = power_title if msg.author.upper() == "YOU" else msg.author
            filtered_messages.append(PressMessage(
                author=norm_author,
                phase=msg.phase,
                body=msg.body,
            ))

        if not filtered_messages:
            continue

        result.append(PressThread(
            thread_id=thread.thread_id,
            subject=thread.subject,
            recipients=norm_recipients,
            messages=filtered_messages,
        ))

    return result


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_press_section(threads: list[PressThread], power: str) -> str | None:
    """Format filtered press threads as a markdown section body.

    Returns None when threads is empty — the caller should also suppress the heading.

    Format:
        ### Thread: "subject" — Recipient1, Recipient2, ...

        **Author** (Spring 1901): message body

        **Author** (Fall 1901): message body

        ---

        ### Thread: ...
    """
    if not threads:
        return None

    parts: list[str] = []
    for i, thread in enumerate(threads):
        recipients_str = ", ".join(thread.recipients)
        header = f'### Thread: "{thread.subject}" — {recipients_str}'
        msg_lines: list[str] = []
        for msg in thread.messages:
            msg_lines.append(f"**{msg.author}** ({msg.phase}): {msg.body}")
        thread_block = header + "\n\n" + "\n\n".join(msg_lines)
        parts.append(thread_block)
        if i < len(threads) - 1:
            parts.append("---")

    return "\n\n".join(parts)


def format_press_frequency_table(threads: list[PressThread], power: str) -> str | None:
    """Build a markdown table of messages-per-phase per other power.

    Rows = phases (chronological), columns = other powers, cells = message count.
    Zeros are shown explicitly so silence is visible.

    Returns None when there are no messages from other powers.
    """
    power_upper = power.upper()

    # Collect (author, phase_string) counts for messages from other powers.
    # Skip messages with empty author strings (malformed press data).
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for thread in threads:
        for msg in thread.messages:
            if not msg.author or msg.author.upper() == power_upper:
                continue
            counts[(msg.author, msg.phase)] += 1

    if not counts:
        return None

    # Unique phases — sort chronologically; skip phases that don't parse
    raw_phases = {phase for (_, phase) in counts}
    sortable: list[tuple[tuple[int, int], str]] = []
    for p in raw_phases:
        short = _press_phase_to_short(p)
        if short is not None:
            sortable.append((_phase_key(short), p))
    sorted_phases = [p for (_, p) in sorted(sortable)]

    # Unique authors — sort alphabetically for stable output
    authors = sorted({author for (author, _) in counts})

    # Build table
    col_widths = [max(len(a), 7) for a in authors]
    phase_col_width = max((len(p) for p in sorted_phases), default=5)

    header = (
        "| " + "Phase".ljust(phase_col_width) + " | "
        + " | ".join(a.ljust(w) for a, w in zip(authors, col_widths))
        + " |"
    )
    sep = (
        "|-" + "-" * phase_col_width + "-|-"
        + "-|-".join("-" * w for w in col_widths)
        + "-|"
    )
    rows = [header, sep]
    for phase in sorted_phases:
        cells = [str(counts.get((author, phase), 0)).ljust(w) for author, w in zip(authors, col_widths)]
        rows.append(
            "| " + phase.ljust(phase_col_width) + " | "
            + " | ".join(cells)
            + " |"
        )

    return "\n".join(rows)
