# Classifying enriched tweets into a locked taxonomy

This is the **hybrid, human-gated** classification stage that runs *after*
`just enrich` has populated `data/tweets_enriched.jsonl`. The agent proposes a
taxonomy from real cached content, a human locks it, and the agent then
classifies every tweet. The deterministic join (`just categorize`) fans the
per-tweet category back out to all rows.

## Inputs and outputs

| Artifact | Tracked? | Produced by |
| --- | --- | --- |
| `data/tweets_enriched.jsonl` | gitignored (derived) | `just enrich` |
| `data/categories.json` | **committed** (decision) | human lock (step 2) |
| `data/tweet_categories.csv` | gitignored (import format) | agent classify (step 3) |
| `data/tweet_user_data.json` | **committed** (source of truth) | `just import-categories` |
| `data/tweets_index.jsonl` | **committed** (canonical record) | `just index` |
| `data/tweets_categorized.csv` | gitignored (derived) | `just categorize` |
| `data/tweets_viewer.html` | gitignored (derived) | `just viewer` |

See [`docs/data-model.md`](data-model.md) for the full canonical shapes, the
build order, and the idempotence guarantees.

Each enriched record (one per `message_id`) carries the tweet text
(`tweet.text`) plus the extracted ultimate content for every outbound link
(`ultimate[].title`, `ultimate[].type`, `ultimate[].content`). Classify from
the **tweet text + ultimate content together**.

## Procedure

### 1. Propose (agent)

- Read every line of `data/tweets_enriched.jsonl`.
- Cluster on `tweet.text` plus each `ultimate[].title` / `ultimate[].type`
  (e.g. AI/LLM tooling, infra/devops, hardware/gadgets, career/meta, science,
  humor/personal …). Let the *real* corpus shape the clusters — do not
  pre-impose categories.
- Draft **5–6 mutually-exclusive** categories. Each is a
  `{name, description}` with 2–3 example `message_id`s justifying the cluster.
- Present the draft to the user. **Stop here.**

### 2. Lock (human gate)

- The user edits and approves the category set. **No classification of the
  full corpus happens before this gate.**
- Write the approved set to committed `data/categories.json`:

  ```json
  {
    "schema_version": 1,
    "locked_at": "2026-06-19T00:00:00Z",
    "categories": [
      { "name": "ai-llm-tooling", "description": "…" },
      { "name": "infra-devops", "description": "…" }
    ]
  }
  ```

  `name` values must be stable slugs (used verbatim in the mapping CSV).

### 3. Classify (agent)

- Assign **every** `message_id` to exactly one locked category, judged from the
  tweet text + ultimate content.
- Write the staging `data/tweet_categories.csv` (untracked) with header
  `message_id,category`, one row per unique tweet. Every `category` must be a
  `name` from `categories.json`, **or** the literal `uncategorized` for tweets
  that genuinely fit none (allowed without being listed in `categories.json`).
- Run `just import-categories`. `scripts/import_categories.py` validates every
  category fail-closed, then **merges** the categories into the committed
  `data/tweet_user_data.json` — updating only `category` and preserving
  `favorite`/`note`/`hidden`/`needs_review`/`suggested_*`. It is a safe upsert:
  re-running the same CSV is a no-op.

### 4. Build the canonical index (deterministic)

- Run `just index`. `scripts/build_index.py` joins `tweets.csv` ×
  `tweets_enriched.jsonl` × `tweet_user_data.json` × `categories.json` and
  fully rewrites the committed `data/tweets_index.jsonl` (the canonical record
  every consumer reads). Then `just validate` gates it.
- The flat per-row join `just categorize` (→ `data/tweets_categorized.csv`)
  remains available; it now reads the category per `message_id` from
  `tweet_user_data.json`. Both rows of a single email share the category; tweets
  with no entry get `uncategorized`.
