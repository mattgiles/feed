# feed — a personal tweet-link archive

A small, file-based personal tool for archiving the tweet/X links you email to
yourself. It mines self-emailed tweet/X links from Gmail, enriches them with
tweet metadata and the content of their outbound links, builds a canonical,
reviewable per-tweet index, and produces a self-contained static HTML viewer
plus plain-text reports and CSV exports. Everything is driven through `just`
recipes, and the canonical state lives in committed files in `data/`.

## How it works

The pipeline is organised around **two canonical, committed artifacts** plus a
handful of derived (gitignored) outputs. The build order is:

```
mine → enrich → import-categories → index → validate → viewer
                                       ↑
        decision edits (set-category / heart / hide / note / needs-review / suggest)
```

Decision edits write `data/tweet_user_data.json` and then feed back into
`index`. The two canonical files are:

- **`data/tweet_user_data.json`** — the committed source of truth for human
  decisions (category, favorite, note, hidden, review flag, suggestions).
- **`data/tweets_index.jsonl`** — the committed canonical per-tweet snapshot
  that every downstream command (reports, exports, viewer) reads.

Because the decision-edit commands auto-run `just index` but do **not** rebuild
`data/tweets_viewer.html`, re-run `just viewer` whenever you want the static
HTML to reflect the latest edits.

See [`docs/data-model.md`](docs/data-model.md) for the full canonical shapes,
build order, and idempotence guarantees, and
[`docs/classify-tweets.md`](docs/classify-tweets.md) for the human-gated
classification stage.

## Prerequisites

- **`just`** — the command runner that drives every recipe in this repo.
- **`uv`** — runs the Python scripts (`uv run python …`); the mining script
  declares `requires-python = ">=3.10"`.
- **Node.js + npm/npx** — runs the TypeScript enrichment script via `tsx`
  (installed by `just node-install`).
- **`gws`** — the Google Workspace CLI used for Gmail access (installed by
  `just install`).
- **`gcloud`** — required only for the one-time `just auth-setup` bootstrap.

## Setup (first-time)

Run these once per machine/checkout, in order:

1. `just install` — installs the `gws` Google Workspace CLI via Homebrew.
2. `just auth-setup` — the one-time `gws auth setup` Google Cloud/OAuth
   bootstrap (requires `gcloud` installed and authenticated).
3. `just auth` — logs in through `gws auth login -s gmail`.
4. `just auth-check` — verifies the active credential can reach Gmail.
5. `just node-install` — installs the Node toolchain used by `just enrich`.

## Full refresh workflow

To mine new links and rebuild the canonical index and viewer:

1. `just mine`
2. `just enrich`
3. `just import-categories` — when `data/tweet_categories.csv` has new bulk
   classifications to merge.
4. `just index`
5. `just validate`
6. `just viewer`

## Review & maintenance workflow

Day-to-day review goes through the decision-edit commands, which each edit
`data/tweet_user_data.json` and then re-run `just index`:

1. `just report`
2. `just viewer` (open `data/tweets_viewer.html`)
3. `just set-category <message_id> <category>` when a category needs changing
4. `just heart <message_id>` / `just unheart <message_id>`
5. `just hide <message_id>` / `just unhide <message_id>`
6. `just note <message_id> "text"`
7. `just needs-review <message_id>`
8. `just suggest <message_id> <category> "reason"`
9. `just viewer`

A typical review loop:

```sh
just report
open data/tweets_viewer.html
just set-category <message_id> <category>
just heart <message_id>
just note <message_id> "useful reference"
just viewer
```

The decision-edit commands automatically run `just index` after changing
`data/tweet_user_data.json`. They do **not** rebuild `data/tweets_viewer.html`,
so run `just viewer` when you want the static HTML to reflect the latest edits.

## Command reference

### Setup commands

| Command | Purpose | When to run |
| --- | --- | --- |
| `just` | Lists available recipes. | Anytime you want the command menu. |
| `just install` | Installs the `gws` Google Workspace CLI with Homebrew. | Once per machine, before Gmail access. |
| `just auth-setup` | Runs the one-time `gws auth setup` Google Cloud/OAuth bootstrap. | First setup only, after `just install`. Requires `gcloud` installed and authenticated. |
| `just auth` | Logs in through `gws auth login -s gmail`. | First setup, after auth resets, or when credentials expire. |
| `just auth-check` | Calls Gmail `users.getProfile` for `me` and prints the active profile JSON. | After auth, or when debugging Gmail access. |
| `just node-install` | Installs the Node dependencies used by the enrichment script. | Once per checkout, before `just enrich`. |

### Build pipeline commands

| Command | Purpose | Main output |
| --- | --- | --- |
| `just mine [args...]` | Searches self-sent Gmail messages and extracts tweet/X links into a tidy CSV. Passes args to `scripts/mine_tweets.py`, such as `--newer-than 30d` or `--out data/recent.csv`. | `data/tweets.csv` |
| `just enrich [args...]` | Resolves tweet links, fetches tweet metadata, extracts outbound-link content, and appends resumable JSONL records. Useful args include `--limit`, `--concurrency`, `--delay-ms`, `--in`, and `--out`. | `data/tweets_enriched.jsonl` |
| `just enrich-errors [args...]` | Re-fetches only enrichment records that previously had unavailable tweets or errored outbound links. | Updates `data/tweets_enriched.jsonl` |
| `just import-categories [args...]` | Merges an agent-produced staging CSV into the committed user-decision file. It validates categories and preserves notes, favorites, hidden state, review flags, and suggestions. | `data/tweet_user_data.json` |
| `just index [args...]` | Joins mined links, enrichment data, user decisions, and the locked taxonomy into the canonical per-tweet stream. | `data/tweets_index.jsonl` |
| `just validate [args...]` | Acceptance gate for the canonical index and user-decision file. Run after `just index` and before treating the snapshot as good. | Validation status |
| `just viewer [args...]` | Builds the self-contained static HTML browser from the canonical index. Common args: `--out data/feed.html`, `--title "My feed"`. | `data/tweets_viewer.html` |
| `just categorize [args...]` | Produces a flat CSV export that joins each mined row to its category. This is mainly for spreadsheet-style export, not the primary viewer path. | `data/tweets_categorized.csv` |

### Review & decision commands

These commands edit `data/tweet_user_data.json` and then run `just index`. Use
the `message_id` shown in reports, the index, or the viewer.

| Command | Purpose |
| --- | --- |
| `just set-category <message_id> <category>` | Changes the committed category. The category must exist in `data/categories.json`, or be `uncategorized`. |
| `just heart <message_id>` | Marks a tweet as a favorite. |
| `just unheart <message_id>` | Removes the favorite mark. |
| `just hide <message_id>` | Hides a tweet from default views. |
| `just unhide <message_id>` | Restores a hidden tweet. |
| `just note <message_id> "text"` | Stores a free-text note. |
| `just needs-review <message_id>` | Flags a tweet for later review. |
| `just suggest <message_id> <category> "reason"` | Records an advisory category suggestion without changing the current category. |
| `just report [args...]` | Prints counts, uncategorized items, low-count categories, suggestion mismatches, and enrichment status. Use `--low N` to change the low-count threshold. |

## Canonical files

The important committed files are:

| File | Role |
| --- | --- |
| `data/categories.json` | Locked taxonomy. Category names used in commands must match this file. |
| `data/tweet_user_data.json` | Source of truth for human decisions: category, favorite, note, hidden, review flag, and suggestions. |
| `data/tweets_index.jsonl` | Canonical per-tweet snapshot consumed by reports, exports, and the viewer. |

The main derived or local (gitignored) files are:

| File | Role |
| --- | --- |
| `data/tweets.csv` | Gmail-mined link spine. |
| `data/tweets_enriched.jsonl` | Resumable enrichment cache. |
| `data/tweet_categories.csv` | Staging CSV for bulk category imports. |
| `data/tweets_categorized.csv` | Flat category export. |
| `data/tweets_viewer.html` | Static browser output. |

See [`docs/data-model.md`](docs/data-model.md) for the full schema, build order,
and idempotence guarantees.

## Further reading

- [`docs/data-model.md`](docs/data-model.md) — the canonical artifact shapes,
  build order, and idempotence guarantees.
- [`docs/classify-tweets.md`](docs/classify-tweets.md) — the hybrid,
  human-gated classification stage that proposes, locks, and applies the
  taxonomy.
