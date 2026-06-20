# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Shared helpers for the file-based tweet pipeline.

Stdlib-only module imported by the sibling Python scripts (the sibling-dir
import works because ``uv run python scripts/<x>.py`` puts ``scripts/`` on
``sys.path``). It centralises the two canonical concerns that several scripts
share:

* the per-tweet **user-decisions** store ``data/tweet_user_data.json`` — load,
  upsert (stamping ``updated_at`` only on an actual change), and a diff-stable
  save (sorted keys, trailing newline, temp-file-then-``os.replace``); and
* the locked **taxonomy** ``data/categories.json`` — the ordered list + name-set
  loader (with a synthetic trailing ``uncategorized``) previously duplicated in
  ``build_viewer.py`` and ``categorize_tweets.py``.

Nothing here makes a network call. Every write is deterministic so committed
diffs stay empty on a no-op re-run.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

UNCATEGORIZED = "uncategorized"

SCHEMA_VERSION = 1

# The full default shape of one ``tweets[<message_id>]`` entry. Any field absent
# from the stored file falls back to this; ``merged_entry`` overlays the stored
# values on top of a fresh copy so every consumer sees a complete record.
DEFAULT_USER_ENTRY: dict = {
    "category": UNCATEGORIZED,
    "favorite": False,
    "note": "",
    "hidden": False,
    "needs_review": False,
    "suggested_category": None,
    "suggested_reason": None,
    "suggested_at": None,
    "updated_at": None,
}


def now_iso() -> str:
    """Current UTC time as a second-precision ISO-8601 string.

    Mirrors the existing convention in ``build_viewer.py`` so timestamps are
    consistent across the pipeline.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_user_data(path: Path) -> dict:
    """Read ``tweet_user_data.json`` -> the full document.

    Returns a fresh ``{"schema_version": 1, "tweets": {}}`` when the file is
    absent so callers never special-case the first run.
    """
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "tweets": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object at the top level")
    data.setdefault("schema_version", SCHEMA_VERSION)
    tweets = data.setdefault("tweets", {})
    if not isinstance(tweets, dict):
        raise ValueError(f"{path}: 'tweets' must be a JSON object")
    return data


def save_user_data(path: Path, data: dict) -> None:
    """Write the user-decisions document deterministically.

    Pretty (2-space) JSON, ``message_id`` keys sorted, a trailing newline, and
    an atomic temp-file-then-``os.replace`` swap so concurrent readers never see
    a half-written file and a no-op rebuild yields a byte-identical file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": data.get("schema_version", SCHEMA_VERSION),
        "tweets": {
            mid: data["tweets"][mid] for mid in sorted(data.get("tweets", {}))
        },
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def merged_entry(data: dict, message_id: str) -> dict:
    """Return the full entry for ``message_id`` (stored values over defaults)."""
    entry = dict(DEFAULT_USER_ENTRY)
    stored = data.get("tweets", {}).get(message_id)
    if isinstance(stored, dict):
        entry.update(stored)
    return entry


def set_entry_fields(data: dict, message_id: str, **fields) -> dict:
    """Upsert ``fields`` into ``tweets[message_id]`` (defaults filled in).

    Stamps ``updated_at`` **only when a value actually changes** so re-running an
    edit with the same value is a no-op (no churn). Returns the resulting entry.
    """
    tweets = data.setdefault("tweets", {})
    current = merged_entry(data, message_id)
    changed = False
    for key, value in fields.items():
        if key not in DEFAULT_USER_ENTRY:
            raise KeyError(f"unknown user-data field: {key!r}")
        if current.get(key) != value:
            current[key] = value
            changed = True
    if changed:
        current["updated_at"] = now_iso()
    tweets[message_id] = current
    return current


def load_categories(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    """Read ``categories.json`` -> (ordered ``[{name, description}]``, name set).

    Preserves file order and appends a synthetic trailing ``uncategorized``
    entry so the UI can render it last. The returned name set does **not**
    include ``uncategorized`` (it is always valid as a literal). Exits non-zero
    when the file lists no categories.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    ordered: list[dict[str, str]] = []
    names: set[str] = set()
    for c in doc.get("categories", []):
        name = c["name"]
        ordered.append({"name": name, "description": c.get("description", "")})
        names.add(name)
    if not names:
        print(f"error: no categories found in {path}", file=sys.stderr)
        sys.exit(1)
    ordered.append(
        {"name": UNCATEGORIZED, "description": "Tweets with no category mapping."}
    )
    return ordered, names
