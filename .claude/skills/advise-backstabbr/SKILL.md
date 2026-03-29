---
name: advise-backstabbr
description: Get Diplomacy tactical advice for a power in a backstabbr game. Use when the user wants to analyse a position or get order suggestions.
disable-model-invocation: true
---

Get Diplomacy tactical advice for a power in a backstabbr game.

Arguments provided: $ARGUMENTS

Parse the following from $ARGUMENTS (positional or named):
- `game_id`: numeric game ID (e.g. 5148037665914880). If not provided, list
  available games by checking game_data/*.json filenames and ask the user to
  choose.
- `power`: one of AUSTRIA, ENGLAND, FRANCE, GERMANY, ITALY, RUSSIA, TURKEY.
  If not provided, ask the user.
- `phase`: optional phase string (e.g. S1907M, F1910M, W1905A). If not
  provided, omit the flag to use the current phase.

Once you have game_id and power, run the advisory prompt (append `--phase
<phase>` if a phase was provided):
```bash
python3 advise_backstabbr.py <game_id> <power> [--phase <phase>]
```

Read the output carefully. It contains:
- Supply center trajectory table
- Current board state (all powers)
- Inferred relationships for the power
- Communication frequency table (messages per power per phase — zeros signal silence)
- Diplomatic press (full thread history up to this phase)
- Per-unit BFS context and legal orders
- Recent order history

Next, get the searchbot's ML-recommended orders. First, read the **Inferred
Relationships** section of the advisory output and identify every power listed
with category **Ally**. Add one `--ally <POWER>` flag per ally. Then run
(append `--phase <phase>` if a phase was provided):
```bash
python3 searchbot_recommend.py <game_id> <power> [--phase <phase>] [--ally POWER ...]
```
Example: if the relationships section shows TURKEY and RUSSIA as "Ally":
```bash
python3 searchbot_recommend.py <game_id> <power> --ally TURKEY --ally RUSSIA
```
If no powers are categorised as "Ally" (e.g. early game with no movement history),
omit the `--ally` flags entirely.

If the script exits with an error (model not found), note that searchbot
recommendations are unavailable and continue with Claude-only analysis.

Analyse the position using all three sources:
- Board state + SC trajectory (objective game state)
- Searchbot recommendations (ML signal)
- Diplomatic press + communication frequency (if present in the advisory output)

Where the searchbot's orders and your own analysis agree, that is strong signal.
Where they diverge, explain why you are following or departing from the model's
suggestion.

If press is present, perform deception analysis before proposing orders:
- Cross-reference each active power's press statements against their actual orders
  in the order history. Did they do what they said?
- Note any power whose message frequency has dropped sharply in recent phases.
- Flag over-reassurance ("I will definitely not attack") as a stab warning sign.
- Summarise your trust assessment for each active power before proposing orders.

Propose concrete orders for the current phase.
Then validate each proposed order by running (include `--phase <phase>` if a
phase was provided):
```bash
python3 advise_backstabbr.py <game_id> <power> [--phase <phase>] --validate "ORDER1" "ORDER2" …
```
Present the validated advice to the user.
