"""
Press scraping for backstabbr games.

Fetches press (diplomacy messages) from:
  GET {game_url}/pressthread[?cursor=...]  — paginated thread header list
  GET {game_url}/pressthread/{thread_id}  — single thread detail

Both endpoints return HTML fragments and require the session cookie.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

from .exceptions import AuthenticationError, PressUnavailableError
from .scraper import _BROWSER_UA

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PressMessage:
    author: str    # power name as returned by backstabbr, e.g. "France"
    phase: str     # phase string as shown by backstabbr, e.g. "Spring 1902"
    body: str      # message text


@dataclass
class PressThread:
    thread_id: str              # numeric string, e.g. "12345"
    subject: str                # thread subject line
    recipients: list[str]       # power names, empty list if not parseable
    messages: list[PressMessage] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def thread_to_dict(t: PressThread) -> dict:
    return {
        "thread_id": t.thread_id,
        "subject": t.subject,
        "recipients": t.recipients,
        "messages": [
            {"author": m.author, "phase": m.phase, "body": m.body}
            for m in t.messages
        ],
    }


def thread_from_dict(d: dict) -> PressThread:
    return PressThread(
        thread_id=d["thread_id"],
        subject=d.get("subject", ""),
        recipients=d.get("recipients", []),
        messages=[
            PressMessage(
                author=m["author"],
                phase=m.get("phase", ""),
                body=m.get("body", ""),
            )
            for m in d.get("messages", [])
        ],
    )


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _press_base(game_url: str) -> str:
    return game_url.rstrip("/") + "/pressthread"


# ---------------------------------------------------------------------------
# HTTP fetch (mirrors fetch_game_page but handles 404)
# ---------------------------------------------------------------------------

def _fetch_press_fragment(url: str, cookie: str) -> BeautifulSoup:
    """
    GET a backstabbr press endpoint (returns an HTML fragment).
    Raises PressUnavailableError on 404, AuthenticationError on redirect to /signin.
    """
    headers = {
        "User-Agent": _BROWSER_UA,
        "Cookie": cookie if "=" in cookie else f"session={cookie}",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://www.backstabbr.com/",
        "X-Requested-With": "XMLHttpRequest",
    }
    resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)

    if "/signin" in resp.url or resp.status_code == 401:
        raise AuthenticationError(
            f"Redirected to {resp.url!r}. Check your session cookie."
        )
    if resp.status_code == 404:
        raise PressUnavailableError(
            f"Press unavailable at {url} (404 — gunboat game or press disabled)."
        )
    if resp.status_code != 200:
        from .exceptions import ParseError
        raise ParseError(f"HTTP {resp.status_code} fetching {url}")

    return BeautifulSoup(resp.text, "lxml")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_thread_ids(soup: BeautifulSoup) -> list[str]:
    """
    Extract thread IDs from a thread-list HTML fragment.
    Looks for <a class="press-thread-header" id="thread_{id}"> elements.
    """
    thread_ids: list[str] = []
    for a in soup.find_all("a", class_="press-thread-header"):
        elem_id = a.get("id", "")
        if elem_id.startswith("thread_"):
            thread_ids.append(elem_id[len("thread_"):])
        elif elem_id:
            thread_ids.append(elem_id)
    return thread_ids


# Matches: load_message_headers('BASE64CURSOR', null)
_CURSOR_RE = re.compile(r"load_message_headers\('([^']+)'")


def _parse_cursor(soup: BeautifulSoup) -> str | None:
    """
    Look for a pagination cursor in the thread-list HTML fragment.
    Backstabbr embeds it in an onclick: load_message_headers('BASE64CURSOR', null).
    Returns the cursor string or None if there are no more pages.
    """
    for btn in soup.find_all(onclick=True):
        m = _CURSOR_RE.search(btn["onclick"])
        if m:
            return m.group(1)
    return None


def _parse_thread_detail(soup: BeautifulSoup, thread_id: str) -> PressThread:
    """
    Parse a thread-detail HTML fragment into a PressThread.

    Actual HTML structure (from --dump-press-html probe):

    <div class="thread" data-thread-id="..." id="press-thread">
      <div class="subject">
        <h4>Status</h4>          ← subject
        <p class="from m-0">
          <em>Italy</em>, <em>You</em>, ...   ← recipients
        </p>
      </div>
      <div id="press-thread-body">
        <div class="season-year-header ..."><span class="mx-3">fall 1918</span></div>
        <div class="d-flex position-relative flex-row mb-2">   ← others' messages
          <div class="messages-new ... yours italy">
            <div class="sender-name ..."><sub><em>Italy</em></sub></div>
            <div class="message-new ..."><p class="body ...">text</p></div>
          </div>
        </div>
        <div class="d-flex position-relative flex-row-reverse mb-2">  ← own messages
          <div class="messages-new ... mine austria">
            <div class="sender-name ..."><sub><em>Austria</em></sub></div>
            <div class="message-new ..."><p class="body ...">text</p></div>
          </div>
        </div>
        ...
      </div>
    </div>
    """
    # --- Subject ---
    subject = ""
    h4 = soup.select_one("div.subject h4")
    if h4:
        subject = h4.get_text(strip=True)

    # --- Recipients ---
    recipients: list[str] = []
    subject_div = soup.select_one("div.subject")
    if subject_div:
        for em in subject_div.find_all("em"):
            text = em.get_text(strip=True)
            if text:
                recipients.append(text)

    # --- Messages (with phase tracking via season-year-header divs) ---
    messages: list[PressMessage] = []
    body_div = soup.select_one("#press-thread-body")
    if body_div is None:
        return PressThread(thread_id=thread_id, subject=subject, recipients=recipients)

    current_phase = ""
    for child in body_div.children:
        if not hasattr(child, "get"):
            continue  # skip NavigableString

        classes = child.get("class") or []
        classes_str = " ".join(classes)

        # Phase section header
        if "season-year-header" in classes_str:
            phase_span = child.select_one("span.mx-3")
            if phase_span:
                current_phase = phase_span.get_text(strip=True).title()
            continue

        # Message row
        if "d-flex" in classes_str and "position-relative" in classes_str:
            msg_div = child.select_one(".messages-new")
            if msg_div is None:
                continue

            sender_em = msg_div.select_one(".sender-name sub em")
            author = sender_em.get_text(strip=True) if sender_em else ""

            body_p = msg_div.select_one("p.body")
            body = body_p.get_text(strip=True) if body_p else ""

            if author or body:
                messages.append(PressMessage(author=author, phase=current_phase, body=body))

    return PressThread(
        thread_id=thread_id,
        subject=subject,
        recipients=recipients,
        messages=messages,
    )


# ---------------------------------------------------------------------------
# Public fetch functions
# ---------------------------------------------------------------------------

def fetch_thread_ids(game_url: str, cookie: str) -> list[str]:
    """
    GET /pressthread (with cursor pagination).
    Parse all <a class="press-thread-header" id="thread_{id}"> elements.
    Returns list of thread ID strings.
    Raises PressUnavailableError on 404.
    """
    base = _press_base(game_url)
    thread_ids: list[str] = []
    url: str | None = base

    while url is not None:
        logger.debug("Fetching thread list: %s", url)
        soup = _fetch_press_fragment(url, cookie)
        ids = _parse_thread_ids(soup)
        logger.debug("Found %d thread IDs on this page", len(ids))
        thread_ids.extend(ids)

        cursor = _parse_cursor(soup)
        url = f"{base}?cursor={cursor}" if cursor else None

    logger.info("Total press threads found: %d", len(thread_ids))
    return thread_ids


def fetch_thread(game_url: str, thread_id: str, cookie: str) -> PressThread:
    """
    GET /pressthread/{thread_id}.
    Parse subject, recipients, and messages.
    """
    url = f"{_press_base(game_url)}/{thread_id}"
    logger.debug("Fetching thread detail: %s", url)
    soup = _fetch_press_fragment(url, cookie)
    return _parse_thread_detail(soup, thread_id)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_press(save_path: str) -> dict[str, PressThread]:
    """Load game_data/<id>_press.json → {thread_id: PressThread}. Returns {} if missing."""
    if not os.path.exists(save_path):
        return {}
    with open(save_path, encoding="utf-8") as f:
        raw = json.load(f)
    threads = {}
    for tid, d in raw.get("threads", {}).items():
        threads[tid] = thread_from_dict(d)
    return threads


def save_press(threads: dict[str, PressThread], save_path: str) -> None:
    """Write {thread_id: thread_to_dict(t)} to JSON."""
    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    payload = {"threads": {tid: thread_to_dict(t) for tid, t in threads.items()}}
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    logger.info("Press saved to %s (%d threads)", save_path, len(threads))


# ---------------------------------------------------------------------------
# Incremental scrape
# ---------------------------------------------------------------------------

def scrape_and_persist_press(
    game_url: str,
    cookie: str,
    save_dir: str = "game_data",
    rate_limit_s: float = 0.5,
    refetch_recent: int = 5,
) -> dict[str, PressThread]:
    """
    Incremental press scrape:
    1. Extract game_id from game_url.
    2. Load existing _press.json (or {}).
    3. Fetch thread ID list from /pressthread (all pages).
    4. For each thread_id not already in saved data → fetch full thread.
    5. Re-fetch the most recent `refetch_recent` threads to pick up new replies.
    6. Save and return.

    Handles PressUnavailableError gracefully (logs info, returns {}).
    """
    game_id = game_url.rstrip("/").split("/")[-1]
    save_path = os.path.join(save_dir, f"{game_id}_press.json")

    os.makedirs(save_dir, exist_ok=True)
    threads = load_press(save_path)
    logger.info("Loaded %d existing press threads from %s", len(threads), save_path)

    try:
        all_ids = fetch_thread_ids(game_url, cookie)
    except PressUnavailableError:
        logger.info("No press available for game %s (gunboat game or press disabled).", game_id)
        return threads

    new_ids = [tid for tid in all_ids if tid not in threads]
    # Always re-fetch the most recent N threads to catch new replies
    recent_ids = [tid for tid in all_ids[:refetch_recent] if tid not in new_ids]

    fetch_ids = new_ids + recent_ids
    logger.info(
        "Fetching %d new + %d recent threads (total %d).",
        len(new_ids), len(recent_ids), len(fetch_ids),
    )

    for i, tid in enumerate(fetch_ids):
        if i > 0:
            time.sleep(rate_limit_s)
        try:
            thread = fetch_thread(game_url, tid, cookie)
            threads[tid] = thread
            logger.debug("Fetched thread %s: %r (%d messages)", tid, thread.subject, len(thread.messages))
        except Exception as e:
            logger.warning("Failed to fetch thread %s: %s", tid, e)

    save_press(threads, save_path)
    return threads
