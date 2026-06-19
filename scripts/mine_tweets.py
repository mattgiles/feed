# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Mine self-emailed tweet/X links from Gmail into a tidy long-format CSV.

Finds every email the user sent to themselves
(``from:matt.s.giles@gmail.com`` -> ``to:matt.s.giles@gmail.com``) within a
relative window (default: last 12 months), extracts every tweet/X link from
each message, and writes one row per link to a CSV.

This is the *mining* stage only: URLs are captured verbatim (including ``t.co``
shorteners and mirror domains) and are **not** followed or resolved. A later
plan will follow the links.

All Gmail access goes through the already-authenticated ``gws`` CLI; this script
never makes raw HTTP calls.
"""

from __future__ import annotations

import argparse
import csv
import html as html_lib
import json
import re
import subprocess
import sys
from email.utils import parsedate_to_datetime
from pathlib import Path

SELF = "matt.s.giles@gmail.com"

# Tweet/X hosts to keep. Verbose, case-insensitive. Matches optional
# www./mobile./m. subdomains and any nitter.* mirror.
TWEET_HOST_RE = re.compile(
    r"""^https?://
        (?:www\.|mobile\.|m\.)?
        (?:
            x\.com
          | twitter\.com
          | t\.co
          | vxtwitter\.com
          | fxtwitter\.com
          | fixupx\.com
          | nitter\.[^/\s]+
        )
        (?:/|$)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Bare URLs in any text. Stops at whitespace and common delimiters.
URL_RE = re.compile(r"""https?://[^\s"'<>)\]]+""")

# href="..." / href='...' attribute values.
HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)

# Trailing punctuation that commonly clings to pasted URLs.
TRAILING_PUNCT = ".,;)>]\"'"


def build_query(newer_than: str) -> str:
    """Gmail search query for self-emails within the window.

    Filters only by from/to/date -- not by link domain. Gmail's full-text
    tokenizer handles dotted tokens like ``t.co`` poorly, so domain filtering
    happens in-script against the raw body to avoid missing links.
    """
    return f"from:{SELF} to:{SELF} newer_than:{newer_than}"


def _run_gws(args: list[str]) -> str:
    """Run a gws command and return its stdout. Raises on non-zero exit."""
    result = subprocess.run(
        ["gws", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def list_message_ids(query: str) -> list[str]:
    """Return all Gmail message ids matching ``query`` (deduped, in order)."""
    params = json.dumps({"userId": "me", "q": query, "maxResults": 500})
    stdout = _run_gws(
        [
            "gmail",
            "users",
            "messages",
            "list",
            "--params",
            params,
            "--page-all",
            "--format",
            "json",
        ]
    )
    ids: list[str] = []
    seen: set[str] = set()
    # --page-all emits NDJSON: one page object per line.
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        page = json.loads(line)
        for msg in page.get("messages") or []:
            msg_id = msg.get("id")
            if msg_id and msg_id not in seen:
                seen.add(msg_id)
                ids.append(msg_id)
    return ids


def read_message(msg_id: str) -> dict[str, str]:
    """Read one message, returning ``{date, subject, text, html}``.

    A single ``gws gmail +read --headers --format json`` call returns the
    headers plus both the plain-text (``body_text``) and HTML (``body_html``)
    bodies, so one call suffices.
    """
    stdout = _run_gws(
        ["gmail", "+read", "--id", msg_id, "--headers", "--format", "json"]
    )
    data = json.loads(stdout)
    return {
        "date": data.get("date") or "",
        "subject": data.get("subject") or "",
        "text": data.get("body_text") or "",
        "html": data.get("body_html") or "",
    }


def extract_urls(text: str, html: str) -> list[str]:
    """Return tweet/X URLs found in ``text`` and ``html`` (deduped, in order).

    HTML entities (e.g. ``&amp;``) are unescaped first so a URL pasted as raw
    text and the same URL inside an ``href`` dedupe to one row.
    """
    html = html_lib.unescape(html)
    candidates: list[str] = []
    candidates.extend(URL_RE.findall(f"{text}\n{html}"))
    candidates.extend(HREF_RE.findall(html))

    kept: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        url = raw.rstrip(TRAILING_PUNCT)
        if not TWEET_HOST_RE.match(url):
            continue
        if url not in seen:
            seen.add(url)
            kept.append(url)
    return kept


def parse_date(date_header: str) -> str:
    """Parse an RFC 2822 Date header to ``YYYY-MM-DD``; ``""`` on failure."""
    try:
        return parsedate_to_datetime(date_header).date().isoformat()
    except (TypeError, ValueError) as exc:
        print(f"warning: could not parse date {date_header!r}: {exc}", file=sys.stderr)
        return ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--newer-than",
        default="12m",
        help="Gmail relative window (e.g. 12m, 30d, 1y). Default: 12m.",
    )
    parser.add_argument(
        "--out",
        default="data/tweets.csv",
        help="Output CSV path. Default: data/tweets.csv.",
    )
    args = parser.parse_args()

    query = build_query(args.newer_than)
    message_ids = list_message_ids(query)

    rows: list[tuple[str, str, str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    emails_with_links = 0

    for msg_id in message_ids:
        msg = read_message(msg_id)
        urls = extract_urls(msg["text"], msg["html"])
        if urls:
            emails_with_links += 1
        date = parse_date(msg["date"])
        for url in urls:
            pair = (msg_id, url)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            rows.append((date, url, msg_id, msg["subject"]))

    # Sort by date ascending, then message_id for stable ordering.
    rows.sort(key=lambda r: (r[0], r[2]))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "url", "message_id", "subject"])
        writer.writerows(rows)

    print(
        f"Scanned {len(message_ids)} emails, "
        f"{emails_with_links} with links, "
        f"{len(rows)} total link rows -> {out_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
