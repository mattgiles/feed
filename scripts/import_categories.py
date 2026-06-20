# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Merge a ``message_id,category`` CSV into ``data/tweet_user_data.json``.

This is the bulk-import path for agent (re)classification **and** the one-time
seed/migration: the agent writes a staging ``data/tweet_categories.csv`` (the
untracked import format) and runs ``just import-categories`` to fold those
categories into the committed source-of-truth ``data/tweet_user_data.json``.

Merge/upsert semantics (never a clobber):

* loads the existing user-data document (creating it when absent — the seed),
* updates **only** the ``category`` field per CSV row,
* **preserves** ``favorite`` / ``note`` / ``hidden`` / ``needs_review`` /
  ``suggested_*`` for every tweet,
* bumps ``updated_at`` **only when the category actually changes**.

Re-running with the same CSV is therefore a no-op (no ``updated_at`` churn).
Categories are validated fail-closed against ``categories.json`` (plus the
literal ``uncategorized``); an unknown category exits non-zero. Stdlib-only.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from tweet_data import (
    UNCATEGORIZED,
    load_categories,
    load_user_data,
    save_user_data,
    set_entry_fields,
)


def load_csv_categories(path: Path, valid: set[str]) -> dict[str, str]:
    """Read ``message_id,category`` CSV -> mapping, validating names.

    Exits non-zero (listing every offending row) if any category is neither a
    name in ``valid`` nor the literal ``uncategorized`` — the same fail-closed
    behavior as ``categorize_tweets.load_tweet_categories``.
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
    parser.add_argument("--csv", default="data/tweet_categories.csv")
    parser.add_argument("--user-data", default="data/tweet_user_data.json")
    parser.add_argument("--categories", default="data/categories.json")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"error: import CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    _, valid = load_categories(Path(args.categories))
    mapping = load_csv_categories(csv_path, valid)

    user_path = Path(args.user_data)
    seeding = not user_path.exists()
    data = load_user_data(user_path)

    changed = 0
    counts: dict[str, int] = {}
    for message_id, category in mapping.items():
        before = data.get("tweets", {}).get(message_id, {}).get("category")
        set_entry_fields(data, message_id, category=category)
        if before != category:
            changed += 1
        counts[category] = counts.get(category, 0) + 1

    save_user_data(user_path, data)

    action = "seeded" if seeding else "merged"
    print(
        f"{action} {len(mapping)} categor{'y' if len(mapping) == 1 else 'ies'} "
        f"into {user_path} ({changed} changed, "
        f"{len(data['tweets'])} total entries)",
        file=sys.stderr,
    )
    for category in sorted(counts):
        print(f"  {category}: {counts[category]}", file=sys.stderr)


if __name__ == "__main__":
    main()
