import json
import re
import logging
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

from .exceptions import AuthenticationError, ParseError

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# Regex for phase heading: "Spring 1901 - Movement" or "Fall 1902 – Retreats"
_PHASE_RE = re.compile(
    r"(Spring|Fall|Winter)\s+(\d{4})\s*[-\u2013]\s*(Movement|Retreat[s]?|Adjustment[s]?)",
    re.IGNORECASE,
)

# Backstabbr stage → diplomacy phase type
_STAGE_TO_PHASE: dict[str, str] = {
    "movement":      "Movement",
    "needs_orders":  "Movement",
    "retreat":       "Retreats",
    "needs_retreats":"Retreats",
    "adjustment":    "Adjustments",
    "needs_builds":  "Adjustments",
    "builds":        "Adjustments",
    "complete":      "Adjustments",  # game over — last phase was adjustments
}

# Regex to pull JS variable assignments from inline <script> blocks
_JS_VAR_RE = re.compile(
    r"var\s+(stage|unitsByPlayer|territories|retreatOptions|orders)\s*=\s*"
    r"(\"[^\"]*\"|'[^']*'|\{.*?\})\s*;",
    re.DOTALL,
)


@dataclass
class RawUnit:
    power: str         # "France"
    unit_type: str     # "Army" or "Fleet"
    province: str      # full name, e.g. "Paris"
    coast: str | None  # "nc", "sc", "ec", or None


@dataclass
class RawGameState:
    units: list[RawUnit] = field(default_factory=list)
    dislodged: list[RawUnit] = field(default_factory=list)
    supply_centers: dict[str, list[str]] = field(default_factory=dict)
    season: str = ""
    year: int = 0
    phase_type: str = ""
    raw_orders: dict = field(default_factory=dict)           # var orders verbatim
    units_by_player_raw: dict = field(default_factory=dict)  # var unitsByPlayer verbatim


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------

def fetch_game_page(url: str, session_cookie: str) -> BeautifulSoup:
    """GET the backstabbr game page with the provided session cookie."""
    headers = {
        "User-Agent": _BROWSER_UA,
        "Cookie": session_cookie if "=" in session_cookie else f"session={session_cookie}",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://www.backstabbr.com/",
    }
    resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)

    # Backstabbr redirects unauthenticated users to /signin
    if "/signin" in resp.url or resp.status_code == 401:
        raise AuthenticationError(
            f"Redirected to {resp.url!r}. Check your session cookie."
        )
    if resp.status_code != 200:
        raise ParseError(f"HTTP {resp.status_code} fetching {url}")

    return BeautifulSoup(resp.text, "lxml")


def fetch_game_page_selenium(url: str, session_cookie: str) -> BeautifulSoup:
    """Use headless Chrome via Selenium to get JS-rendered DOM."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.common.by import By
    except ImportError:
        raise ImportError(
            "selenium is not installed. Run: pip install selenium"
        )

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"user-agent={_BROWSER_UA}")

    driver = webdriver.Chrome(options=options)
    try:
        # Set cookie on the domain first
        driver.get("https://www.backstabbr.com")
        name, _, value = session_cookie.partition("=")
        driver.add_cookie({"name": name.strip(), "value": value.strip(), "domain": ".backstabbr.com"})
        driver.get(url)

        # Wait for SVG or game board to render
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "svg, .game-map, #map"))
            )
        except Exception:
            logger.warning("Timed out waiting for game map element; proceeding anyway.")

        html = driver.page_source
    finally:
        driver.quit()

    return BeautifulSoup(html, "lxml")


# ---------------------------------------------------------------------------
# Primary: backstabbr inline JS variable extraction
# ---------------------------------------------------------------------------

def _extract_from_js_vars(soup: BeautifulSoup) -> RawGameState | None:
    """
    Parse the inline JS block that backstabbr embeds in every game page:

        var stage = "NEEDS_BUILDS";
        var unitsByPlayer = {"Austria": {"Ber": "A", "LYO": "F",
                                         "Spa": {"type": "F", "coast": "sc"}}, ...};
        var territories = {"Lon": "England", "Par": "Italy", ...};
        var retreatOptions = {"Ber": ["Mun", "Sil"], ...};   (retreat phase only)

    Province keys are backstabbr abbreviated codes (e.g. "Ber", "BAL") — we
    upper-case them to get standard 3-letter codes.
    """
    for script in soup.find_all("script"):
        text = script.string or ""
        if "unitsByPlayer" not in text:
            continue

        # Pull all var assignments from this block
        vars_: dict[str, object] = {}
        for m in _JS_VAR_RE.finditer(text):
            name = m.group(1)
            raw_val = m.group(2).strip()
            if raw_val.startswith(('"', "'")):
                vars_[name] = raw_val[1:-1]
            else:
                try:
                    vars_[name] = json.loads(raw_val)
                except json.JSONDecodeError as e:
                    logger.debug("Could not parse JS var %s: %s", name, e)

        units_by_player = vars_.get("unitsByPlayer")
        territories = vars_.get("territories")
        stage = vars_.get("stage", "")
        retreat_options = vars_.get("retreatOptions", {})
        raw_orders = vars_.get("orders", {})

        if not isinstance(units_by_player, dict):
            continue

        state = RawGameState()
        if isinstance(raw_orders, dict):
            state.raw_orders = raw_orders
        if isinstance(units_by_player, dict):
            state.units_by_player_raw = units_by_player

        # Phase type from stage
        stage_key = str(stage).lower()
        state.phase_type = _STAGE_TO_PHASE.get(stage_key, "Movement")
        logger.debug("stage=%r → phase_type=%r", stage, state.phase_type)

        # Season + year from <meta property="og:title"> e.g. "Diplomacy 101 (Winter 1919)"
        meta_title = soup.find("meta", property="og:title")
        og_title = meta_title["content"] if meta_title else ""
        m_phase = re.search(r"(Spring|Fall|Winter)\s+(\d{4})", og_title, re.IGNORECASE)
        if not m_phase:
            # fallback: scan page text
            m_phase = re.search(r"(Spring|Fall|Winter)\s+(\d{4})", soup.get_text(), re.IGNORECASE)
        if m_phase:
            state.season = m_phase.group(1).capitalize()
            state.year = int(m_phase.group(2))
        else:
            logger.warning("Could not determine season/year from page")

        # Units from unitsByPlayer
        dislodged_provinces = set(
            p.upper() for p in (retreat_options if isinstance(retreat_options, dict) else {})
        )
        for power_name, unit_map in units_by_player.items():
            if not isinstance(unit_map, dict):
                continue
            for prov_code, unit_val in unit_map.items():
                province = prov_code.upper()

                if isinstance(unit_val, str):
                    utype = _normalize_unit_type(unit_val)
                    coast = None
                elif isinstance(unit_val, dict):
                    utype = _normalize_unit_type(unit_val.get("type", ""))
                    coast = unit_val.get("coast")  # e.g. "sc", "nc", "ec"
                else:
                    logger.debug("Unexpected unit value %r for %s in %s", unit_val, prov_code, power_name)
                    continue

                raw = RawUnit(
                    power=power_name,
                    unit_type=utype,
                    province=province,   # already a 3-letter code (uppercased)
                    coast=coast,
                )
                if province in dislodged_provinces:
                    state.dislodged.append(raw)
                else:
                    state.units.append(raw)

        # Supply centers from territories: {"Lon": "England", ...}
        if isinstance(territories, dict):
            for prov_code, power_name in territories.items():
                if isinstance(power_name, str) and power_name:
                    state.supply_centers.setdefault(power_name, []).append(prov_code.upper())

        logger.info(
            "JS vars: %d units, %d dislodged, %d powers with SCs",
            len(state.units), len(state.dislodged), len(state.supply_centers),
        )
        return state

    return None


# ---------------------------------------------------------------------------
# Phase extraction (fallback)
# ---------------------------------------------------------------------------

def _extract_phase(soup: BeautifulSoup) -> tuple[str, int, str]:
    """Return (season, year, phase_type) from the page heading."""
    # Try dedicated heading elements
    for selector in ("h1", "h2", "h3", ".phase", ".game-phase", "#phase", ".turn-header"):
        el = soup.select_one(selector)
        if el:
            m = _PHASE_RE.search(el.get_text())
            if m:
                return m.group(1).capitalize(), int(m.group(2)), _normalize_phase_type(m.group(3))

    # Fall back to full-page text scan
    m = _PHASE_RE.search(soup.get_text())
    if m:
        return m.group(1).capitalize(), int(m.group(2)), _normalize_phase_type(m.group(3))

    raise ParseError(
        "Could not find phase heading (e.g. 'Spring 1901 - Movement') in page. "
        "Try --dump-html to inspect the raw HTML."
    )


def _normalize_unit_type(raw: str) -> str:
    return "Fleet" if raw.strip().upper() in ("F", "FLEET") else "Army"


def _normalize_phase_type(raw: str) -> str:
    raw = raw.lower()
    if raw.startswith("movement"):
        return "Movement"
    if raw.startswith("retreat"):
        return "Retreats"
    if raw.startswith("adjustment"):
        return "Adjustments"
    return raw.capitalize()


# ---------------------------------------------------------------------------
# JSON extraction (tries embedded JS state first)
# ---------------------------------------------------------------------------

def _try_extract_json(soup: BeautifulSoup) -> RawGameState | None:
    """
    Scan <script> tags for embedded JSON game state.
    Backstabbr may embed state as window.__gameState or similar.
    Returns None if no recognized JSON blob found.
    """
    patterns = [
        re.compile(r"window\.__gameState\s*=\s*(\{.*?\});", re.DOTALL),
        re.compile(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});", re.DOTALL),
        re.compile(r"window\.gameData\s*=\s*(\{.*?\});", re.DOTALL),
    ]

    for script in soup.find_all("script"):
        text = script.string or ""
        for pat in patterns:
            m = pat.search(text)
            if m:
                try:
                    data = json.loads(m.group(1))
                    return _parse_json_state(data)
                except (json.JSONDecodeError, KeyError) as e:
                    logger.debug("JSON parse attempt failed: %s", e)

    # Also check <script type="application/json">
    for script in soup.find_all("script", attrs={"type": "application/json"}):
        try:
            data = json.loads(script.string or "")
            result = _parse_json_state(data)
            if result:
                return result
        except (json.JSONDecodeError, KeyError):
            pass

    return None


def _parse_json_state(data: dict) -> RawGameState | None:
    """
    Attempt to extract RawGameState from an arbitrary JSON blob.
    Returns None if the structure is not recognizable.
    """
    # This is speculative — actual field names depend on backstabbr's internals.
    # We try common patterns; the user should inspect --dump-state output to refine.
    game = data.get("game") or data.get("gameState") or data
    if not isinstance(game, dict):
        return None

    units_data = game.get("units") or game.get("pieces")
    if not units_data:
        return None

    state = RawGameState()

    phase_str = game.get("phase") or game.get("currentPhase") or ""
    if phase_str:
        m = _PHASE_RE.search(phase_str)
        if m:
            state.season = m.group(1).capitalize()
            state.year = int(m.group(2))
            state.phase_type = _normalize_phase_type(m.group(3))

    for unit in (units_data if isinstance(units_data, list) else []):
        power = unit.get("power") or unit.get("country") or ""
        utype = unit.get("type") or unit.get("unitType") or ""
        province = unit.get("province") or unit.get("location") or ""
        coast = unit.get("coast") or unit.get("coastalVariant")
        dislodged = unit.get("dislodged") or unit.get("retreating") or False

        raw = RawUnit(
            power=power.capitalize(),
            unit_type=_normalize_unit_type(utype),
            province=province,
            coast=coast,
        )
        if dislodged:
            state.dislodged.append(raw)
        else:
            state.units.append(raw)

    sc_data = game.get("supplyCenters") or game.get("centers") or {}
    if isinstance(sc_data, dict):
        state.supply_centers = {
            k.capitalize(): v for k, v in sc_data.items()
        }

    return state if (state.units or state.supply_centers) else None


# ---------------------------------------------------------------------------
# SVG extraction
# ---------------------------------------------------------------------------

# Known fill colors → power names (backstabbr palette — adjust after inspecting debug.html)
_COLOR_TO_POWER: dict[str, str] = {
    # These are approximate; backstabbr may use slightly different hex values
    "#ff0000": "Austria", "#c00000": "Austria", "#aa0000": "Austria",
    "#0000ff": "France",  "#003399": "France",  "#3366cc": "France",
    "#000000": "Germany", "#333333": "Germany", "#555555": "Germany",
    "#00aa00": "Italy",   "#009900": "Italy",   "#006600": "Italy",
    "#ffffff": "England", "#dddddd": "England", "#eeeeee": "England",
    "#ffff00": "Russia",  "#cccc00": "Russia",  "#aaaa00": "Russia",
    "#ff8800": "Turkey",  "#cc6600": "Turkey",  "#ff9900": "Turkey",
}


def _color_to_power(fill: str) -> str | None:
    if not fill:
        return None
    fill = fill.lower().strip()
    return _COLOR_TO_POWER.get(fill)


def _extract_units_from_svg(soup: BeautifulSoup) -> tuple[list[RawUnit], list[RawUnit]]:
    """
    Strategy A: look for elements with data-power + data-province + data-type attributes.
    Strategy B: infer from shape (circle=army, polygon/path=fleet) + fill color + position.
    Returns (active_units, dislodged_units).
    """
    units: list[RawUnit] = []
    dislodged: list[RawUnit] = []

    svg = soup.find("svg")
    if not svg:
        logger.debug("No <svg> element found in page.")
        return units, dislodged

    # --- Strategy A: data attributes ---
    candidates = svg.find_all(
        lambda tag: tag.get("data-power") or tag.get("data-country")
    )
    if candidates:
        logger.debug("Strategy A: found %d data-attribute elements", len(candidates))
        for el in candidates:
            power = (el.get("data-power") or el.get("data-country") or "").strip()
            province = (
                el.get("data-province")
                or el.get("data-location")
                or el.get("data-territory")
                or ""
            ).strip()
            utype_raw = (
                el.get("data-type")
                or el.get("data-unit-type")
                or el.get("data-piece-type")
                or ""
            ).strip().lower()
            coast = (el.get("data-coast") or el.get("data-coastal-variant") or "").strip() or None
            is_dislodged = bool(el.get("data-dislodged") or el.get("data-retreating"))

            if not power or not province:
                continue

            utype = _normalize_unit_type(utype_raw)
            raw = RawUnit(
                power=power.capitalize(),
                unit_type=utype,
                province=province,
                coast=coast or None,
            )
            (dislodged if is_dislodged else units).append(raw)

        if units or dislodged:
            return units, dislodged

    # --- Strategy B: shape + color heuristic ---
    logger.debug("Strategy A found no units; falling back to Strategy B (color/shape heuristic)")

    # Circles → armies; polygons or paths with multiple points → fleets
    for el in svg.find_all(["circle", "ellipse", "polygon", "rect", "path", "g"]):
        classes = " ".join(el.get("class") or []).lower()
        # Skip map background elements
        if any(skip in classes for skip in ("border", "background", "sea", "land", "label")):
            continue

        fill = el.get("fill") or el.get("style", "")
        # Extract fill from style if needed
        if "fill:" in fill:
            m = re.search(r"fill:\s*(#[0-9a-fA-F]{3,6}|[a-z]+)", fill)
            fill = m.group(1) if m else ""

        power = _color_to_power(fill)
        if not power:
            continue

        tag = el.name
        if tag in ("circle", "ellipse"):
            utype = "Army"
        elif tag in ("polygon",):
            utype = "Fleet"
        else:
            utype = "Army"  # ambiguous; default to Army

        # Province must be inferred from parent group or nearby text — log for now
        parent = el.find_parent(attrs={"data-province": True}) or \
                 el.find_parent(attrs={"data-location": True})
        province = ""
        if parent:
            province = parent.get("data-province") or parent.get("data-location") or ""

        if not province:
            logger.debug(
                "Strategy B: cannot determine province for %s unit; skipping. "
                "Inspect debug.html to find province attribute names.",
                utype,
            )
            continue

        units.append(RawUnit(power=power, unit_type=utype, province=province, coast=None))

    return units, dislodged


def _extract_supply_centers_from_svg(soup: BeautifulSoup) -> dict[str, list[str]]:
    """
    Parse supply center markers from SVG.
    Returns dict of {power: [province_name, ...]}
    """
    centers: dict[str, list[str]] = {}
    svg = soup.find("svg")
    if not svg:
        return centers

    # Strategy A: elements with data-sc or data-supply-center attributes
    sc_elements = svg.find_all(
        lambda tag: tag.get("data-sc") or tag.get("data-supply-center") or
                    "supply" in " ".join(tag.get("class") or []).lower()
    )

    for el in sc_elements:
        province = (
            el.get("data-province")
            or el.get("data-sc")
            or el.get("data-location")
            or ""
        ).strip()
        power_raw = (
            el.get("data-power")
            or el.get("data-owner")
            or el.get("data-country")
            or ""
        ).strip()

        if not province:
            continue

        if power_raw:
            power = power_raw.capitalize()
        else:
            # Try inferring from fill color
            fill = el.get("fill") or ""
            power = _color_to_power(fill) or "Neutral"

        if power != "Neutral":
            centers.setdefault(power, []).append(province)

    if centers:
        return centers

    # Strategy B: look for elements with class containing "sc" or "dot" with fill color
    logger.debug("SC Strategy A found nothing; trying class-based SC search")
    for el in svg.find_all(lambda tag: any(
        "sc" in c.lower() or "supply" in c.lower() or "center" in c.lower()
        for c in (tag.get("class") or [])
    )):
        fill = el.get("fill") or ""
        power = _color_to_power(fill)
        if not power:
            continue
        province = el.get("data-province") or el.get("id") or ""
        if province:
            centers.setdefault(power, []).append(province)

    return centers


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_game_state(soup: BeautifulSoup) -> RawGameState:
    """
    Extract RawGameState from a parsed backstabbr game page.

    Extraction priority:
      1. Backstabbr inline JS vars (unitsByPlayer / territories / stage)
      2. Generic embedded JSON (window.__gameState etc.)
      3. SVG attribute parsing
    """
    # 1. Backstabbr-specific JS vars (confirmed format)
    state = _extract_from_js_vars(soup)
    if state and (state.units or state.supply_centers):
        logger.info("Extracted game state from backstabbr JS vars")
        return state

    # 2. Generic embedded JSON state
    state = _try_extract_json(soup)
    if state and (state.units or state.supply_centers):
        logger.info("Extracted game state from embedded JSON")
        if not state.season:
            try:
                state.season, state.year, state.phase_type = _extract_phase(soup)
            except ParseError:
                pass
        return state

    logger.info("No JS/JSON state found; parsing SVG")

    # 2. Fall back to SVG
    state = RawGameState()
    try:
        state.season, state.year, state.phase_type = _extract_phase(soup)
    except ParseError as e:
        logger.warning("Phase extraction failed: %s", e)

    state.units, state.dislodged = _extract_units_from_svg(soup)
    state.supply_centers = _extract_supply_centers_from_svg(soup)

    if not state.units and not state.supply_centers:
        raise ParseError(
            "Could not extract any units or supply centers from the page. "
            "The game board may be rendered by JavaScript. "
            "Try running with --selenium flag, or use --dump-html to inspect the HTML."
        )

    return state
