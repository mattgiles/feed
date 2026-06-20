# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Join the locked tweet taxonomy onto every mined tweet row.

Reads the original ``data/tweets.csv`` (one row per self-emailed link), the
committed taxonomy ``data/categories.json``, and the committed per-tweet user
decisions ``data/tweet_user_data.json``; writes ``data/tweets_categorized.csv``
— the original columns ``date,url,message_id,subject`` plus a ``category``
column.

The category is looked up by ``message_id`` (from the user-decisions file, the
source of truth) so the ``t.co`` and ``x.com`` rows of one email share a
category. Rows whose ``message_id`` is absent from the decisions file get the
literal ``uncategorized`` (the default entry).

Every category in the decisions file is validated against ``categories.json``
(plus the literal ``uncategorized``); an unknown category exits non-zero. This
is the deterministic join/validation stage — it makes no network calls and is
stdlib-only (``uv run``). The output is fully rewritten on every run.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from tweet_data import UNCATEGORIZED, load_categories, load_user_data, merged_entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="in_path", default="data/tweets.csv")
    parser.add_argument("--categories", default="data/categories.json")
    parser.add_argument("--user-data", default="data/tweet_user_data.json")
    parser.add_argument("--out", default="data/tweets_categorized.csv")
    args = parser.parse_args()

    _, valid = load_categories(Path(args.categories))
    user_data = load_user_data(Path(args.user_data))

    # Validate every stored category fail-closed against the locked taxonomy.
    offending: list[tuple[str, str]] = []
    for message_id in user_data.get("tweets", {}):
        category = merged_entry(user_data, message_id)["category"]
        if category not in valid and category != UNCATEGORIZED:
            offending.append((message_id, category))
    if offending:
        print(
            f"error: {len(offending)} entr(y/ies) in {args.user_data} use a "
            f"category not in categories.json (and not '{UNCATEGORIZED}'):",
            file=sys.stderr,
        )
        for message_id, category in offending:
            print(f"  {message_id}: {category!r}", file=sys.stderr)
        sys.exit(1)

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
            category = merged_entry(user_data, row["message_id"])["category"]
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
    expected = len(valid)
    if distinct_locked > expected:
        print(
            f"warning: {distinct_locked} categories used but only {expected} "
            "are locked in categories.json",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
