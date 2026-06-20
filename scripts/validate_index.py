# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Acceptance gate for the canonical ``data/tweets_index.jsonl``.

Reads the committed index and asserts the invariants every downstream consumer
relies on:

* unique ``message_id``s (no duplicate lines);
* every required field present and correctly typed;
* ``schema_version == 1`` on every record;
* ``user.category`` is a name in ``categories.json`` or the literal
  ``uncategorized``.

Also validates that every ``data/tweet_user_data.json`` category is likewise
valid. Exits non-zero listing **every** problem found; prints an OK summary
otherwise. Stdlib-only — this is the project's standing ``just validate`` check
in lieu of a test framework.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tweet_data import UNCATEGORIZED, load_categories, load_user_data, merged_entry

REQUIRED_TOP = {
    "schema_version": int,
    "message_id": str,
    "date": str,
    "subject": str,
    "source_urls": list,
    "primary_url": str,
    "tweet": dict,
    "links": list,
    "link_error_count": int,
    "enrichment_status": str,
    "user": dict,
}
REQUIRED_TWEET = {"id", "author", "text", "permalink", "fetch_status"}
REQUIRED_USER = {
    "category",
    "favorite",
    "note",
    "hidden",
    "needs_review",
    "suggested_category",
    "suggested_reason",
    "suggested_at",
    "updated_at",
}
VALID_ENRICHMENT = {"ok", "partial", "missing"}


def validate_index(path: Path, valid: set[str]) -> list[str]:
    """Return a list of human-readable problems (empty == valid)."""
    problems: list[str] = []
    if not path.exists():
        return [f"{path}: file not found"]

    seen: set[str] = set()
    count = 0
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            count += 1
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as exc:
                problems.append(f"line {lineno}: invalid JSON ({exc})")
                continue
            if not isinstance(rec, dict):
                problems.append(f"line {lineno}: not a JSON object")
                continue

            mid = rec.get("message_id")
            tag = mid if isinstance(mid, str) and mid else f"line {lineno}"

            for field, typ in REQUIRED_TOP.items():
                if field not in rec:
                    problems.append(f"{tag}: missing field '{field}'")
                elif not isinstance(rec[field], typ):
                    problems.append(
                        f"{tag}: field '{field}' should be {typ.__name__}, "
                        f"got {type(rec[field]).__name__}"
                    )

            if rec.get("schema_version") != 1:
                problems.append(f"{tag}: schema_version != 1")

            if isinstance(mid, str) and mid:
                if mid in seen:
                    problems.append(f"{tag}: duplicate message_id")
                seen.add(mid)

            if isinstance(rec.get("enrichment_status"), str):
                if rec["enrichment_status"] not in VALID_ENRICHMENT:
                    problems.append(
                        f"{tag}: enrichment_status {rec['enrichment_status']!r} "
                        f"not in {sorted(VALID_ENRICHMENT)}"
                    )

            tweet = rec.get("tweet")
            if isinstance(tweet, dict):
                for field in REQUIRED_TWEET - set(tweet):
                    problems.append(f"{tag}: tweet missing field '{field}'")

            user = rec.get("user")
            if isinstance(user, dict):
                for field in REQUIRED_USER - set(user):
                    problems.append(f"{tag}: user missing field '{field}'")
                category = user.get("category")
                if category not in valid and category != UNCATEGORIZED:
                    problems.append(
                        f"{tag}: user.category {category!r} not in categories.json "
                        f"(and not '{UNCATEGORIZED}')"
                    )

    if count == 0:
        problems.append(f"{path}: no records found")
    return problems


def validate_user_data(path: Path, valid: set[str]) -> list[str]:
    """Validate every category in tweet_user_data.json."""
    problems: list[str] = []
    if not path.exists():
        return problems
    data = load_user_data(path)
    for mid in data.get("tweets", {}):
        category = merged_entry(data, mid)["category"]
        if category not in valid and category != UNCATEGORIZED:
            problems.append(
                f"tweet_user_data {mid}: category {category!r} not in "
                f"categories.json (and not '{UNCATEGORIZED}')"
            )
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default="data/tweets_index.jsonl")
    parser.add_argument("--user-data", default="data/tweet_user_data.json")
    parser.add_argument("--categories", default="data/categories.json")
    args = parser.parse_args()

    _, valid = load_categories(Path(args.categories))
    problems = validate_index(Path(args.index), valid)
    problems += validate_user_data(Path(args.user_data), valid)

    if problems:
        print(f"FAIL: {len(problems)} problem(s) found:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        sys.exit(1)

    index_count = sum(
        1 for line in Path(args.index).read_text(encoding="utf-8").splitlines() if line.strip()
    )
    print(f"OK: {index_count} index records valid; categories consistent.")


if __name__ == "__main__":
    main()
