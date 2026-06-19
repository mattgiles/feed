# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Join the locked tweet taxonomy onto every mined tweet row.

Reads the original ``data/tweets.csv`` (one row per self-emailed link), the
committed taxonomy ``data/categories.json``, and the committed per-tweet
mapping ``data/tweet_categories.csv``; writes ``data/tweets_categorized.csv`` —
the original columns ``date,url,message_id,subject`` plus a ``category`` column.

The category is looked up by ``message_id`` so the ``t.co`` and ``x.com`` rows
of one email share a category. Rows whose ``message_id`` is missing from the
mapping get the literal ``uncategorized``.

Every category in the mapping is validated against ``categories.json`` (plus the
literal ``uncategorized``); an unknown category exits non-zero. This is the
deterministic join/validation stage — it makes no network calls and mirrors
``scripts/mine_tweets.py`` (stdlib-only, ``uv run``).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

UNCATEGORIZED = "uncategorized"


def load_categories(path: Path) -> set[str]:
    """Read categories.json -> set of valid category names."""
    data = json.loads(path.read_text(encoding="utf-8"))
    names = {c["name"] for c in data.get("categories", [])}
    if not names:
        print(f"error: no categories found in {path}", file=sys.stderr)
        sys.exit(1)
    return names


def load_tweet_categories(path: Path, valid: set[str]) -> dict[str, str]:
    """Read tweet_categories.csv -> {message_id: category}, validating names.

    Exits non-zero (listing the offending rows) if any category is neither a
    name in ``valid`` nor the literal ``uncategorized``.
    """
    mapping: dict[str, str] = {}
    offending: list[tuple[str, str]] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            message_id = (row.get("message_id") or "").strip()
            category = (row.get("category") or "").strip()
            if not message_id:
                continue
            if category not in valid and category != UNCATEGORIZED:
                offending.append((message_id, category))
                continue
            mapping[message_id] = category
    if offending:
        print(
            f"error: {len(offending)} row(s) in {path} use a category not in "
            f"categories.json (and not '{UNCATEGORIZED}'):",
            file=sys.stderr,
        )
        for message_id, category in offending:
            print(f"  {message_id}: {category!r}", file=sys.stderr)
        sys.exit(1)
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="in_path", default="data/tweets.csv")
    parser.add_argument("--categories", default="data/categories.json")
    parser.add_argument("--mapping", default="data/tweet_categories.csv")
    parser.add_argument("--out", default="data/tweets_categorized.csv")
    args = parser.parse_args()

    valid = load_categories(Path(args.categories))
    mapping = load_tweet_categories(Path(args.mapping), valid)

    in_path = Path(args.in_path)
    out_path = Path(args.out)

    counts: dict[str, int] = {}
    total = 0
    uncategorized_rows = 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with in_path.open(newline="", encoding="utf-8") as fin, out_path.open(
        "w", newline="", encoding="utf-8"
    ) as fout:
        reader = csv.DictReader(fin)
        writer = csv.writer(fout)
        writer.writerow(["date", "url", "message_id", "subject", "category"])
        for row in reader:
            category = mapping.get(row["message_id"], UNCATEGORIZED)
            if category == UNCATEGORIZED:
                uncategorized_rows += 1
            counts[category] = counts.get(category, 0) + 1
            total += 1
            writer.writerow(
                [row["date"], row["url"], row["message_id"], row["subject"], category]
            )

    distinct_locked = sum(1 for c in counts if c != UNCATEGORIZED)
    print(f"Wrote {total} rows -> {out_path}", file=sys.stderr)
    for category in sorted(counts):
        print(f"  {category}: {counts[category]}", file=sys.stderr)
    print(
        f"distinct categories: {distinct_locked} locked"
        + (f" (+ {UNCATEGORIZED})" if uncategorized_rows else ""),
        file=sys.stderr,
    )
    if not 5 <= distinct_locked <= 6:
        print(
            f"warning: expected 5-6 locked categories, found {distinct_locked}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
