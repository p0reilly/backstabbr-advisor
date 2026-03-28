# backstabbr-advisor

Scrapes a backstabbr.com game page and builds a full phase history into a `diplomacy.Game`, persisted as JSON. On subsequent runs only new phases are fetched.

## Architecture

```
backstabbr_scraper/
    exceptions.py     # custom error hierarchy
    province_map.py   # province name → 3-letter code + coast resolution
    scraper.py        # HTTP fetch + parsing → RawGameState (includes raw_orders, units_by_player_raw)
    converter.py      # RawGameState → diplomacy dicts; convert_orders() for order conversion
    loader.py         # construct diplomacy.Game from a single state dict (single-phase path)
    history.py        # multi-phase scraping, persistence, and validation
    press.py          # press thread scraping → PressThread/PressMessage; saved to <id>_press.json
    order_context.py  # BFS-based per-unit context for advisory prompts (movement phases only)
    analysis.py       # accumulate_relationships() with recency decay, build_sc_trajectory()
    advisor.py        # build_advisory_prompt() — assembles full advisory markdown
scrape_backstabbr.py  # scraper CLI entry point
advise_backstabbr.py  # advisory CLI; --validate flag for order validation
searchbot_recommend.py  # ML move recommendations via diplomacy_searchbot ModelSampledAgent
.claude/skills/
    scrape-backstabbr/SKILL.md  # skill: scrape a game
    advise-backstabbr/SKILL.md  # skill: analyse position, run searchbot, propose orders, validate
game_data/            # auto-created; one <game_id>.json per game, one <game_id>_press.json per game
```

## How backstabbr serves game state

No API. Game state is embedded as inline JS variables in the HTML (static — no Selenium needed):

```js
var stage = "NEEDS_BUILDS";
var unitsByPlayer = {
    "Austria": {"Ber": "A", "LYO": "F", "Spa": {"type": "F", "coast": "sc"}},
    ...
};
var territories = {"Lon": "England", "Par": "Italy", ...};
var retreatOptions = {"Ber": ["Mun", "Sil"]};  // only present in retreat phase
var orders = {
    "England": {
        "Por": {"type": "MOVE", "to": "Spa", "to_coast": "sc", "result": "SUCCEEDS"},
        "Mun": {"type": "SUPPORT", "from": "Pru", "result": "FAILS", "result_reason": "..."},
        ...
    },
    ...
};
```

- Province keys are backstabbr abbreviated codes (title-case for land, uppercase for seas) — uppercased to get standard 3-letter diplomacy codes.
- Unit value is `"A"` / `"F"` (string) or `{"type": "F", "coast": "sc"}` (dict, for coastal fleets).
- Season/year comes from `<meta property="og:title">`, e.g. `"Diplomacy 101 (Winter 1919)"`.
- `stage` values: `NEEDS_BUILDS` → Adjustments, `MOVEMENT`/`NEEDS_ORDERS` → Movement, `NEEDS_RETREATS` → Retreats.
- Historical phase URL pattern: `/game/{TITLE}/{ID}/{YEAR}/{SEASON}` where SEASON ∈ `spring` | `fall` | `winter`.

## Order conversion (`converter.py`)

`convert_orders(raw_orders, units_by_player)` returns `(orders, backstabbr_failed)`:

- `orders`: `{POWER_UPPER: ['A PAR - BUR', 'F POR - SPA/SC', ...]}` — diplomacy-format order strings
- `backstabbr_failed`: `{POWER_UPPER: {order_str, ...}}` — orders backstabbr marked `result: FAILS` (illegal player orders)

Coast suffixes are resolved from:
- **Source unit**: `unitsByPlayer[power][prov]` coast field → e.g. `F SPA/SC H`
- **MOVE destination**: order's `to_coast` field → e.g. `F POR - SPA/SC`
- **SUPPORT from-unit**: `unitsByPlayer` lookup across all powers → e.g. `F LYO S F SPA/SC`
- **SUPPORT move destination**: order's `to_coast` field

## History scraping and persistence (`history.py`)

`scrape_and_persist(game_url, cookie, save_dir="game_data")`:

1. Fetches current page to determine the current phase (e.g. `W1919A`).
2. Loads `game_data/<id>.json` if it exists; returns early if already up to date.
3. Enumerates all phases from `S1901M` up to the current phase.
4. Scrapes only missing phases (oldest-first, 0.5 s between requests).
5. Injects each into `game.state_history` and `game.order_history` via `.put()` with the correct `StringComparator` key type.
6. Validates newly-scraped phases (see below).
7. Sets current board state via `set_units` / `set_centers` / `set_current_phase`.
8. Saves via `game.to_dict()` → JSON.

## Validation (`history.py::validate_phase_history`)

Called after injection with `known_failed` (the `backstabbr_failed` dicts accumulated during scraping).

For each newly-scraped phase:
- Sets up a fresh `Game`, clears all powers' units/centers (to avoid phantom starting units polluting build-site checks), then restores board state from `state_history`.
- Calls `set_orders()` with only the orders backstabbr marked **SUCCEEDS** — orders marked **FAILS** are silently skipped (they are illegal player orders that backstabbr accepted but adjudicated away).
- Any diplomacy engine error on a SUCCEEDS order indicates a conversion bug → `RuntimeError` is raised (save is not written).

Previously-saved phases are not re-validated (assumed clean from their first scrape run).

## Press scraping (`press.py`)

Press threads are fetched from two XHR endpoints (no Selenium needed):

- `GET {game_url}/pressthread` — paginated thread header list; HTML fragment with `<a class="press-thread-header" id="thread_{id}">` elements. Pagination cursor is embedded in a "Load more" button `onclick="load_message_headers('BASE64CURSOR', null)"` — the cursor is passed as a query param `?cursor=BASE64CURSOR` on the next request.
- `GET {game_url}/pressthread/{thread_id}` — single thread detail; HTML fragment with messages.

Thread detail HTML structure (confirmed from probe):
- Subject: `div.subject h4`
- Recipients: `<em>` elements inside `div.subject`
- Phase headers: `div.season-year-header span.mx-3` — section dividers between message groups; each message is stamped with the most recent header above it
- Messages: `.messages-new` divs; author in `.sender-name sub em`; body in `p.body`

`scrape_and_persist_press(game_url, cookie)`:
1. Loads existing `game_data/<id>_press.json` (or `{}`).
2. Fetches all thread IDs (following cursor pagination).
3. Fetches new threads + re-fetches the most recent 5 to pick up new replies.
4. Saves to `game_data/<id>_press.json` as `{"threads": {thread_id: {...}}}`.

`PressUnavailableError` is raised on 404 (gunboat games or press-disabled games); `scrape_and_persist_press` catches this and returns the existing data.

## Authentication

Cookie name is `session`, value is a Firebase JWT. Pass as `--cookie "session=<jwt>"`.
Unauthenticated requests redirect to `/signin` (detected and raised as `AuthenticationError`).

## Running

```bash
pip install -r requirements.txt

# Full history scrape (default) — saves to game_data/<id>.json
python scrape_backstabbr.py <url> --cookie "session=<jwt>"

# Second run — loads from file, reports "already up to date"
python scrape_backstabbr.py <url> --cookie "session=<jwt>"

# Single-phase path (skips history)
python scrape_backstabbr.py <url> --cookie "session=<jwt>" --no-history

# Inspect raw HTML
python scrape_backstabbr.py <url> --cookie "session=<jwt>" --dump-html debug.html

# Print parsed RawGameState as JSON
python scrape_backstabbr.py <url> --cookie "session=<jwt>" --dump-state

# Convert and print state dict without constructing diplomacy.Game
python scrape_backstabbr.py <url> --cookie "session=<jwt>" --dry-run

# Scrape game history + press threads (both on by default)
python scrape_backstabbr.py <url> --cookie "session=<jwt>"

# Suppress press scraping
python scrape_backstabbr.py <url> --cookie "session=<jwt>" --no-press

# Probe press HTML (saves raw fragments to a directory for selector debugging)
python scrape_backstabbr.py <url> --cookie "session=<jwt>" --dump-press-html press_debug/

# Advisory prompt for a power (prints markdown to stdout)
python advise_backstabbr.py <game_id> <power>
python advise_backstabbr.py <game_id> <power> --game-data-dir <dir> --recent 5

# Historical phase advisory
python advise_backstabbr.py <game_id> <power> --phase S1907M

# Validate proposed orders (outputs JSON {valid, invalid, errors})
python advise_backstabbr.py <game_id> <power> --validate "A PAR - BUR" "F BRE H"

# ML move recommendations (requires diplomacy_searchbot repo with downloaded models)
python searchbot_recommend.py <game_id> <power>
python searchbot_recommend.py <game_id> <power> --phase S1907M
```

## Searchbot recommendations (`searchbot_recommend.py`)

Loads a backstabbr game JSON, converts the current (or a historical `--phase`) board state to pydipcc format, and runs `ModelSampledAgent` from the `diplomacy_searchbot` repo to produce ML-recommended orders.

```bash
# Recommend moves for current phase (uses default model and temperature)
python searchbot_recommend.py <game_id> <power>

# Historical phase
python searchbot_recommend.py <game_id> <power> --phase S1907M

# All powers
python searchbot_recommend.py <game_id> <power> --all-powers

# Override model or searchbot location
python searchbot_recommend.py <game_id> <power> --model <ckpt> --searchbot-dir <path>
```

Key defaults:
- **Model**: `neurips21_human_dnvi_npu_epoch000500.ckpt` — best for human-compatible play (NeurIPS '21 paper; beats prior SOTA SearchBot 36.3% vs 0.5% in 1v6 games). DORA is superhuman in pure self-play but incompatible with human conventions — do not use it for advice.
- **Temperature**: `0.1` — near-greedy, matches production `model_sampled.prototxt`. The 0.75 value seen in searchbot configs is for internal CFR rollouts, not final move generation.
- **Searchbot dir**: `~/IdeaProjects/diplomacy_searchbot` (loaded via `sys.path` injection; no install needed).

The pydipcc JSON is built as a single-phase snapshot. Builds/disbands are computed from `len(centers) - len(units)` for adjustment phases; standard homes are hardcoded.

## Relationships section (`analysis.py`)

`accumulate_relationships()` uses **exponential decay** (`recency_decay=0.8` default) so recent phases outweigh early-game history. The weighted totals drive the category label (Enemy, Friendly, etc.); the prompt also shows raw last-3 and all-time counts so the LLM can judge trajectory.

Eliminated powers (0 units and 0 centers) are omitted from the board state and relationships sections.

## Advisory prompt design notes

- **Per-unit BFS context** (`order_context.py`): generated for **movement phases only**. Suppressed for adjustment (A) and retreat (R) phases where it adds noise without strategic value.
- **Nearest SCs**: capped at 3 hops (distance > 3 omitted). The header is hidden if no SCs qualify.
- **Moves into own territory**: filtered from the legal orders list (bounce-only scenarios, rarely intentional).
- **Eliminated powers**: omitted from units, supply centers, and relationships sections.

## Claude Code skills

Two skills in `.claude/skills/` wrap the CLIs for interactive use:

- `/scrape-backstabbr [url] [cookie]` — prompts for any missing args, runs the scraper, reports the saved phase.
- `/advise-backstabbr [game_id] [power]` — prompts for any missing args, runs the advisory prompt AND searchbot recommendations, proposes orders comparing both, then self-validates via `--validate`.

## Key design notes

- `state_history` / `order_history` use `SortedDict` with `StringComparator` keys — plain `str` is rejected. Always use `.put(key_type(phase_name), value)`.
- `diplomacy.Game()` initialises with all 7 powers at their 1901 starting positions. Validation clears all powers before restoring history state, otherwise build-site checks see phantom occupying units.
- `RawUnit.province` stores the already-uppercased 3-letter code when extracted from JS vars. `converter.py` detects this and skips `province_to_code()` lookup.
- Eliminated powers have `[]` for units and centers; only powers present in the scraped data are overwritten.
- Winter phases map to `W{year}A`; spring/fall to `S/F{year}M`. Backstabbr has no separate retreat-phase URLs; retreat data is embedded in the movement page via `var retreatOptions`.
