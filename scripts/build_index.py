# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Build the canonical per-tweet record stream ``data/tweets_index.jsonl``.

This is the one canonical join every downstream consumer (viewer, categorizer,
reports) reads instead of re-joining CSVs. It **fully rewrites** the committed
``data/tweets_index.jsonl`` deterministically from four inputs:

* ``data/tweets.csv`` — the mining spine (one row per self-emailed link),
  grouped by ``message_id`` (same logic + primary-URL pick as the old
  ``build_viewer.load_tweets``);
* ``data/tweets_enriched.jsonl`` — the enrichment overlay (tweet metadata +
  extracted ultimate content per outbound link);
* ``data/tweet_user_data.json`` — the committed user decisions (merged over
  defaults); and
* ``data/categories.json`` — the locked taxonomy (categories validated
  fail-closed).

Idempotent: a re-run with unchanged inputs yields a **byte-identical** file
(deterministic key order, one line per ``message_id`` sorted by
``(date, message_id)``, stable JSON serialization) so a no-op rebuild leaves an
empty ``git diff``.

When ``data/tweets.csv`` is absent (it is gitignored and requires re-mining),
the spine falls back to the union of ``message_id``s known to the user-decisions
file so a committed snapshot can still be produced — those records carry empty
``date``/``subject``/``source_urls`` and ``enrichment_status == "missing"``.
Stdlib-only.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from tweet_data import (
    SCHEMA_VERSION,
    UNCATEGORIZED,
    load_categories,
    load_user_data,
    merged_entry,
)

PREVIEW_CHARS = 400

# Match an x.com / twitter.com status permalink and capture the tweet id.
STATUS_RE = re.compile(
    r"(?:x\.com|twitter\.com)/(?:i/web/status|[^/]+/status)/(\d+)", re.IGNORECASE
)
TWITTER_HOST_RE = re.compile(r"(^|\.)(x\.com|twitter\.com)$", re.IGNORECASE)
TCO_RE = re.compile(r"\bt\.co/", re.IGNORECASE)
WS_RE = re.compile(r"\s+")


def _is_twitter_host(url: str) -> bool:
    m = re.match(r"^[a-z]+://([^/]+)", url, re.IGNORECASE)
    host = m.group(1) if m else ""
    host = host.split("@")[-1].split(":")[0]
    return bool(TWITTER_HOST_RE.search(host))


def _primary_url(urls: list[str]) -> str:
    """Pick a display URL: prefer an x.com/twitter status permalink, else the
    first non-t.co URL, else the first URL."""
    for url in urls:
        if STATUS_RE.search(url) and _is_twitter_host(url):
            return url
    for url in urls:
        if not TCO_RE.search(url):
            return url
    return urls[0] if urls else ""


def load_tweets(path: Path) -> dict[str, dict]:
    """Read tweets.csv and group rows by message_id.

    Keeps the first ``date``/``subject`` seen, collects all ``url``s deduped in
    order (mirroring ``enrich_tweets.ts::groupByMessage``), and derives a
    primary display URL per group. Returns ``{}`` when the file is absent.
    """
    groups: dict[str, dict] = {}
    if not path.exists():
        return groups
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            message_id = (row.get("message_id") or "").strip()
            if not message_id:
                continue
            url = (row.get("url") or "").strip()
            group = groups.get(message_id)
            if group is None:
                group = {
                    "message_id": message_id,
                    "date": (row.get("date") or "").strip(),
                    "subject": (row.get("subject") or ""),
                    "urls": [],
                }
                groups[message_id] = group
            if url and url not in group["urls"]:
                group["urls"].append(url)
    for group in groups.values():
        group["primary_url"] = _primary_url(group["urls"])
    return groups


def load_enriched(path: Path) -> dict[str, dict]:
    """Parse tweets_enriched.jsonl -> {message_id: record}, skipping malformed
    lines. Returns ``{}`` when the file is absent."""
    if not path.exists():
        return {}
    records: dict[str, dict] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            mid = rec.get("message_id")
            if isinstance(mid, str) and mid:
                records[mid] = rec
    return records


def _preview(content: str) -> str:
    """First ~400 chars of an ultimate link's content, whitespace-collapsed."""
    collapsed = WS_RE.sub(" ", (content or "").strip())
    if len(collapsed) <= PREVIEW_CHARS:
        return collapsed
    return collapsed[:PREVIEW_CHARS].rstrip() + "…"


def _index_links(rec: dict) -> tuple[list[dict], int]:
    """Map an enriched record's ``ultimate[]`` to index ``links[]``.

    Returns ``(links, link_error_count)``. Every ultimate entry is kept (even
    error/empty-title ones) so the per-link ``fetch_status`` and the
    fetch-error filter remain accurate.
    """
    links: list[dict] = []
    error_count = 0
    for entry in rec.get("ultimate", []) or []:
        if not isinstance(entry, dict):
            continue
        url = (entry.get("url") or "").strip()
        if not url:
            continue
        fetch_status = entry.get("fetch_status") or "ok"
        if fetch_status == "error":
            error_count += 1
        links.append(
            {
                "url": url,
                "type": entry.get("type") or "other",
                "title": (entry.get("title") or "").strip(),
                "preview": _preview(entry.get("content") or ""),
                "fetch_status": fetch_status,
            }
        )
    return links, error_count


def build_record(
    message_id: str,
    group: dict | None,
    rec: dict | None,
    user_data: dict,
) -> dict:
    """Assemble one canonical index record for ``message_id``."""
    group = group or {"date": "", "subject": "", "urls": [], "primary_url": ""}
    tweet_meta = (rec or {}).get("tweet") or {}

    enriched_text = (tweet_meta.get("text") or "").strip()
    text = enriched_text if enriched_text else group.get("subject", "")
    tweet_fetch_status = tweet_meta.get("fetch_status") or (
        "unavailable" if rec is None else "ok"
    )

    if rec is not None:
        links, link_error_count = _index_links(rec)
    else:
        links, link_error_count = [], 0

    if rec is None:
        enrichment_status = "missing"
    elif tweet_fetch_status == "unavailable" or link_error_count > 0:
        enrichment_status = "partial"
    else:
        enrichment_status = "ok"

    return {
        "schema_version": SCHEMA_VERSION,
        "message_id": message_id,
        "date": group.get("date", ""),
        "subject": group.get("subject", ""),
        "source_urls": list(group.get("urls", [])),
        "primary_url": group.get("primary_url", ""),
        "tweet": {
            "id": tweet_meta.get("tweet_id"),
            "author": tweet_meta.get("author"),
            "text": text,
            "permalink": tweet_meta.get("permalink") or group.get("primary_url", ""),
            "fetch_status": tweet_fetch_status,
        },
        "links": links,
        "link_error_count": link_error_count,
        "enrichment_status": enrichment_status,
        "user": merged_entry(user_data, message_id),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tweets", default="data/tweets.csv")
    parser.add_argument("--enriched", default="data/tweets_enriched.jsonl")
    parser.add_argument("--user-data", default="data/tweet_user_data.json")
    parser.add_argument("--categories", default="data/categories.json")
    parser.add_argument("--out", default="data/tweets_index.jsonl")
    args = parser.parse_args()

    _, valid = load_categories(Path(args.categories))
    tweets = load_tweets(Path(args.tweets))
    enriched = load_enriched(Path(args.enriched))
    user_data = load_user_data(Path(args.user_data))

    # Spine = tweets.csv message_ids, unioned with any user-data ids so committed
    # decisions are never silently dropped (and so a snapshot is producible when
    # tweets.csv is absent).
    message_ids = set(tweets) | set(user_data.get("tweets", {}))

    # Fail-closed category validation against the locked taxonomy.
    offending: list[tuple[str, str]] = []
    for mid in message_ids:
        category = merged_entry(user_data, mid)["category"]
        if category not in valid and category != UNCATEGORIZED:
            offending.append((mid, category))
    if offending:
        print(
            f"error: {len(offending)} tweet(s) have a category not in "
            f"categories.json (and not '{UNCATEGORIZED}'):",
            file=sys.stderr,
        )
        for mid, category in offending:
            print(f"  {mid}: {category!r}", file=sys.stderr)
        sys.exit(1)

    records = [
        build_record(mid, tweets.get(mid), enriched.get(mid), user_data)
        for mid in message_ids
    ]
    records.sort(key=lambda r: (r["date"], r["message_id"]))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n" for rec in records
    ]
    out_path.write_text("".join(lines), encoding="utf-8")

    status_counts: dict[str, int] = {}
    for rec in records:
        s = rec["enrichment_status"]
        status_counts[s] = status_counts.get(s, 0) + 1
    if not tweets:
        print(
            "warning: data/tweets.csv absent — built index spine from "
            "tweet_user_data.json (enrichment_status=missing).",
            file=sys.stderr,
        )
    print(f"Wrote {len(records)} records -> {out_path}", file=sys.stderr)
    for s in sorted(status_counts):
        print(f"  enrichment {s}: {status_counts[s]}", file=sys.stderr)


if __name__ == "__main__":
    main()
