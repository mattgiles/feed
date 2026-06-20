# Data model: canonical artifacts and the build order

The file-based tweet pipeline is organised around **two canonical, committed
artifacts** plus a handful of derived (gitignored) outputs. Every consumer
(viewer, categorizer, reports) reads the one canonical record shape instead of
re-joining CSVs.

## File roles and tracking

| File | Tracked? | Role | Produced by |
| --- | --- | --- | --- |
| `data/tweets.csv` | gitignored | Mining spine: one row per self-emailed link | `just mine` |
| `data/tweets_enriched.jsonl` | gitignored | Enrichment overlay: tweet metadata + extracted link content | `just enrich` |
| `data/categories.json` | **committed** | The locked taxonomy (decision) | human lock |
| `data/tweet_user_data.json` | **committed** | **Source of truth** for per-tweet decisions | `just import-categories` / `just heart` / … |
| `data/tweets_index.jsonl` | **committed** | **Canonical** per-tweet record (a reviewable snapshot) | `just index` |
| `data/tweet_categories.csv` | gitignored | Agent-classification staging CSV (bulk-import format) | agent classify |
| `data/tweets_categorized.csv` | gitignored | Flat per-row category join | `just categorize` |
| `data/tweets_viewer.html` | gitignored | Self-contained static viewer | `just viewer` |

`tweets_index.jsonl` is committed even though its inputs (`tweets.csv`,
`tweets_enriched.jsonl`) are gitignored. Rebuilding it requires re-mining /
re-enriching — accepted for this personal tool.

## Build order

```
mine → enrich → import-categories → index → validate → viewer
                                       ↑
        edit_user_data (set-category / heart / hide / note / needs-review / suggest)
```

- `just mine` → `data/tweets.csv`
- `just enrich` → `data/tweets_enriched.jsonl` (and `just enrich-errors` to retry failures)
- `just import-categories` → merges the staging CSV into `data/tweet_user_data.json`
- `just index` → fully rewrites `data/tweets_index.jsonl`
- `just validate` → the acceptance gate over the index + user-data
- `just viewer` → `data/tweets_viewer.html`

Decision edits (`just set-category`, `just heart`, `just unheart`, `just hide`,
`just unhide`, `just note`, `just needs-review`, `just suggest`) write
`tweet_user_data.json` and then rebuild the (cheap, committed) index. Rerun
`just viewer` to regenerate the HTML — edits do not auto-rebuild the viewer.

## Canonical shapes

### `data/tweet_user_data.json` (committed — source of truth)

```json
{
  "schema_version": 1,
  "tweets": {
    "<message_id>": {
      "category": "<category-name|uncategorized>",
      "favorite": false,
      "note": "",
      "hidden": false,
      "needs_review": false,
      "suggested_category": null,
      "suggested_reason": null,
      "suggested_at": null,
      "updated_at": "<iso8601|null>"
    }
  }
}
```

Any `message_id` absent from the file defaults to `category="uncategorized"`,
booleans `false`, `note=""`, `suggested_*=null`, `updated_at=null`.

### `data/tweets_index.jsonl` (committed — canonical record, one line per `message_id`)

```json
{
  "schema_version": 1,
  "message_id": "...",
  "date": "YYYY-MM-DD",
  "subject": "...",
  "source_urls": ["..."],
  "primary_url": "https://x.com/user/status/123",
  "tweet": {
    "id": "123|null",
    "author": "@user|null",
    "text": "...",
    "permalink": "...|null",
    "fetch_status": "ok|unavailable"
  },
  "links": [
    { "url": "...", "type": "article|youtube|github|pdf|tweet|other",
      "title": "...", "preview": "first ~400 chars", "fetch_status": "ok|error" }
  ],
  "link_error_count": 0,
  "enrichment_status": "ok|partial|missing",
  "user": { "...full user entry merged over defaults..." }
}
```

`enrichment_status`:

- `missing` — no enriched record exists for the `message_id`;
- `partial` — `tweet.fetch_status == "unavailable"` or `link_error_count > 0`;
- `ok` — otherwise.

Records are sorted by `(date, message_id)`. Each `links[].preview` is the first
~400 characters of that link's extracted content, whitespace-collapsed, derived
in `build_index.py` (not stored upstream).

## Idempotence guarantees

Every command is safe to re-run with no duplication, drift, or loss of manual
decisions:

- **`just index`** is a pure function of its inputs and **fully rewrites** the
  index deterministically (sorted keys, sorted by `(date, message_id)`). A
  re-run on unchanged inputs yields a **byte-identical** file — an empty `git
  diff`.
- **`just import-categories`** is a merge/upsert, never a clobber: it updates
  only `category`, preserves all other fields, and bumps `updated_at` **only
  when the category actually changes**. Re-running the same CSV is a no-op.
- **`edit_user_data.py`** writes toggles as explicit booleans (idempotent) and
  bumps `updated_at` only on an actual value change.
- **`save_user_data`** writes via a temp file + `os.replace` with sorted
  `message_id` keys and a trailing newline, so writes are atomic and diff-stable.
- **`just categorize` / `just viewer`** fully rewrite their derived outputs; the
  viewer's only nondeterministic field is its `generated_at` timestamp.

## Enrichment freshness policy

`enrich_tweets.ts` is resumable: a re-run of `just enrich` skips
`message_id`s already present in `tweets_enriched.jsonl`. `just enrich-errors`
(`--refetch-errors`) re-processes only the records that previously failed —
those where `tweet.fetch_status == "unavailable"` or any `ultimate[]` link has
`fetch_status == "error"`. Successful records are never re-fetched. The
downstream `link_error_count` and `enrichment_status` in the index are derived
from these per-record fetch statuses, so retrying errors and rebuilding the
index is the way to clear `partial`/`missing` flags.
