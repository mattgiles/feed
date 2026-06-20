# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Edit per-tweet decisions in ``data/tweet_user_data.json`` from the CLI.

The first positional argument selects an action; the rest are the
``message_id`` (and, where relevant, a value):

    set-category <mid> <category>
    favorite <mid>            unfavorite <mid>
    hide <mid>                unhide <mid>
    note <mid> <text...>
    needs-review <mid> [on|off]     (default: on)
    suggest <mid> <category> [reason...]

Toggles are written as explicit booleans (never flip-flop) so every action is
idempotent: re-running with the same value is a no-op and ``updated_at`` is
stamped only on an actual change (via the shared upsert helper). The category is
validated fail-closed for ``set-category``/``suggest``; an unknown ``message_id``
(absent from ``data/tweets.csv``) is a **non-fatal warning**, not an error.

This does **not** rebuild the index — the ``just`` recipes chain ``just index``
after the edit. Stdlib-only. Prints the resulting entry as JSON.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from tweet_data import (
    UNCATEGORIZED,
    load_categories,
    load_user_data,
    merged_entry,
    now_iso,
    save_user_data,
    set_entry_fields,
)


def known_message_ids(path: Path) -> set[str] | None:
    """Return the set of message_ids in tweets.csv, or None when it's absent."""
    if not path.exists():
        return None
    ids: set[str] = set()
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mid = (row.get("message_id") or "").strip()
            if mid:
                ids.add(mid)
    return ids


def parse_bool(token: str) -> bool:
    t = token.strip().lower()
    if t in {"on", "true", "yes", "1"}:
        return True
    if t in {"off", "false", "no", "0"}:
        return False
    print(f"error: expected on/off, got {token!r}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action")
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    parser.add_argument("--user-data", default="data/tweet_user_data.json")
    parser.add_argument("--categories", default="data/categories.json")
    parser.add_argument("--tweets", default="data/tweets.csv")
    args = parser.parse_args()

    action = args.action
    rest = args.rest
    if not rest:
        print(f"error: action {action!r} requires a <message_id>", file=sys.stderr)
        sys.exit(2)
    message_id = rest[0]

    _, valid = load_categories(Path(args.categories))
    data = load_user_data(Path(args.user_data))

    def require_category(cat: str) -> str:
        if cat not in valid and cat != UNCATEGORIZED:
            print(
                f"error: category {cat!r} not in categories.json "
                f"(and not '{UNCATEGORIZED}')",
                file=sys.stderr,
            )
            sys.exit(1)
        return cat

    if action == "set-category":
        if len(rest) < 2:
            print("error: set-category <mid> <category>", file=sys.stderr)
            sys.exit(2)
        set_entry_fields(data, message_id, category=require_category(rest[1]))
    elif action == "favorite":
        set_entry_fields(data, message_id, favorite=True)
    elif action == "unfavorite":
        set_entry_fields(data, message_id, favorite=False)
    elif action == "hide":
        set_entry_fields(data, message_id, hidden=True)
    elif action == "unhide":
        set_entry_fields(data, message_id, hidden=False)
    elif action == "note":
        text = " ".join(rest[1:])
        set_entry_fields(data, message_id, note=text)
    elif action == "needs-review":
        value = parse_bool(rest[1]) if len(rest) > 1 else True
        set_entry_fields(data, message_id, needs_review=value)
    elif action == "suggest":
        if len(rest) < 2:
            print("error: suggest <mid> <category> [reason...]", file=sys.stderr)
            sys.exit(2)
        category = require_category(rest[1])
        reason = " ".join(rest[2:]) or None
        # Stamp suggested_at only when the suggestion content actually changes.
        current = merged_entry(data, message_id)
        changed = (
            current.get("suggested_category") != category
            or current.get("suggested_reason") != reason
        )
        set_entry_fields(
            data,
            message_id,
            suggested_category=category,
            suggested_reason=reason,
            suggested_at=now_iso() if changed else current.get("suggested_at"),
        )
    else:
        print(f"error: unknown action {action!r}", file=sys.stderr)
        sys.exit(2)

    # Non-fatal warning when the message_id isn't a mined tweet.
    ids = known_message_ids(Path(args.tweets))
    if ids is not None and message_id not in ids:
        print(
            f"warning: {message_id} is not in {args.tweets} (editing anyway)",
            file=sys.stderr,
        )

    save_user_data(Path(args.user_data), data)
    print(json.dumps({message_id: merged_entry(data, message_id)}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
