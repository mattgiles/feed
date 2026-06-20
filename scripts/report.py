# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Print a plain-text status report over the canonical ``tweets_index.jsonl``.

Reads the one canonical record stream and summarises the things worth a human's
attention:

* the **uncategorized** count (the review queue);
* categories **below a threshold** (``--low``, default 5) — candidates for
  merging or reclassification;
* tweets whose advisory ``user.suggested_category`` **differs** from the
  committed ``user.category`` (proposed reclassifications to act on); and
* tweets whose ``enrichment_status`` is not ``ok`` (``missing`` vs ``partial``).

Plain text to stdout, stdlib-only. Run ``just index`` first to refresh input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_index(path: Path) -> list[dict]:
    if not path.exists():
        print(f"error: index not found: {path} (run 'just index' first)", file=sys.stderr)
        sys.exit(1)
    records: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default="data/tweets_index.jsonl")
    parser.add_argument(
        "--low", type=int, default=5, help="Flag categories with fewer than N tweets."
    )
    args = parser.parse_args()

    records = load_index(Path(args.index))
    total = len(records)

    counts: dict[str, int] = {}
    uncategorized = 0
    suggestions: list[tuple[str, str, str]] = []
    enrichment: dict[str, int] = {}
    for rec in records:
        user = rec.get("user") or {}
        category = user.get("category", "uncategorized")
        counts[category] = counts.get(category, 0) + 1
        if category == "uncategorized":
            uncategorized += 1
        suggested = user.get("suggested_category")
        if suggested and suggested != category:
            suggestions.append((rec.get("message_id", ""), category, suggested))
        status = rec.get("enrichment_status", "missing")
        enrichment[status] = enrichment.get(status, 0) + 1

    print(f"Tweet index report  ({total} tweets)")
    print("=" * 40)

    print(f"\nUncategorized: {uncategorized}")

    print(f"\nCategories below {args.low}:")
    low = [(c, n) for c, n in sorted(counts.items()) if c != "uncategorized" and n < args.low]
    if low:
        for c, n in low:
            print(f"  {c}: {n}")
    else:
        print("  (none)")

    print(f"\nSuggestion vs. user category mismatches: {len(suggestions)}")
    for mid, current, suggested in suggestions:
        print(f"  {mid}: {current} -> {suggested}")

    print("\nEnrichment status:")
    for status in ("ok", "partial", "missing"):
        if status in enrichment:
            print(f"  {status}: {enrichment[status]}")

    print("\nPer-category counts:")
    for c, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {c}: {n}")


if __name__ == "__main__":
    main()
