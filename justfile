# Onboarding for the gws (Google Workspace) CLI used by the gmail skills.
#
# Order:
#   just install      # install the gws CLI via Homebrew
#   just auth-setup   # one-time Google Cloud + OAuth bootstrap (first time only)
#   just auth          # (re)login, scoped to Gmail
#   just auth-check    # verify the active credential can reach Gmail
#   just mine          # mine self-emailed tweet links into data/tweets.csv

# List available recipes.
default:
    @just --list

# Mine self-emailed tweet/X links from Gmail into data/tweets.csv.
# Pass extra args through, e.g. `just mine --newer-than 30d --out data/recent.csv`.
mine *ARGS:
    uv run python scripts/mine_tweets.py {{ARGS}}

# Install the Node toolchain (tsx + pi-web-access) for the enrich script.
node-install:
    #!/bin/sh
    set -eu
    if ! command -v npm >/dev/null 2>&1; then
        echo "error: npm is not on your PATH. Install Node.js first." >&2
        exit 1
    fi
    npm install

# Enrich mined tweet links to their ultimate content -> data/tweets_enriched.jsonl.
# Pass extra args through, e.g. `just enrich --limit 5 --concurrency 4`.
# Run `just node-install` once first to install the toolchain.
enrich *ARGS:
    #!/bin/sh
    set -eu
    if ! command -v npx >/dev/null 2>&1; then
        echo "error: npx is not on your PATH. Install Node.js and run 'just node-install'." >&2
        exit 1
    fi
    npx tsx scripts/enrich_tweets.ts {{ARGS}}

# Build the canonical per-tweet record stream data/tweets_index.jsonl from
# tweets.csv x tweets_enriched.jsonl x tweet_user_data.json x categories.json.
# Fully rewrites the committed index deterministically (idempotent).
index *ARGS:
    uv run python scripts/build_index.py {{ARGS}}

# Acceptance gate: validate data/tweets_index.jsonl (+ tweet_user_data.json)
# against the schema and the locked taxonomy. Exits non-zero on any problem.
validate *ARGS:
    uv run python scripts/validate_index.py {{ARGS}}

# Merge the agent-classification staging CSV (data/tweet_categories.csv) into
# the committed source of truth data/tweet_user_data.json (merge/upsert; safe to
# re-run). Pass extra args through, e.g. `just import-categories --csv data/x.csv`.
import-categories *ARGS:
    uv run python scripts/import_categories.py {{ARGS}}

# --- Per-tweet decision edits -------------------------------------------------
# Each edits data/tweet_user_data.json then rebuilds the (committed) index.
# Re-run `just viewer` afterwards to regenerate the HTML.

# Set a tweet's category: `just set-category <message_id> <category>`.
set-category MESSAGE_ID CATEGORY:
    uv run python scripts/edit_user_data.py set-category {{MESSAGE_ID}} {{CATEGORY}}
    @just index

# Mark a tweet as a favorite: `just heart <message_id>`.
heart MESSAGE_ID:
    uv run python scripts/edit_user_data.py favorite {{MESSAGE_ID}}
    @just index

# Remove a tweet's favorite: `just unheart <message_id>`.
unheart MESSAGE_ID:
    uv run python scripts/edit_user_data.py unfavorite {{MESSAGE_ID}}
    @just index

# Hide a tweet from the default views: `just hide <message_id>`.
hide MESSAGE_ID:
    uv run python scripts/edit_user_data.py hide {{MESSAGE_ID}}
    @just index

# Unhide a tweet: `just unhide <message_id>`.
unhide MESSAGE_ID:
    uv run python scripts/edit_user_data.py unhide {{MESSAGE_ID}}
    @just index

# Attach a free-text note: `just note <message_id> "some text"`.
note MESSAGE_ID TEXT:
    uv run python scripts/edit_user_data.py note {{MESSAGE_ID}} {{quote(TEXT)}}
    @just index

# Flag a tweet for review: `just needs-review <message_id>`.
needs-review MESSAGE_ID:
    uv run python scripts/edit_user_data.py needs-review {{MESSAGE_ID}}
    @just index

# Record an advisory category suggestion (does not change the category):
# `just suggest <message_id> <category> "optional reason"`.
suggest MESSAGE_ID CATEGORY *REASON:
    uv run python scripts/edit_user_data.py suggest {{MESSAGE_ID}} {{CATEGORY}} {{REASON}}
    @just index

# Print a plain-text status report over data/tweets_index.jsonl.
# Pass extra args through, e.g. `just report --low 3`.
report *ARGS:
    uv run python scripts/report.py {{ARGS}}

# Join the locked taxonomy onto every tweet row -> data/tweets_categorized.csv.
# Reads the category per message_id from data/tweet_user_data.json.
categorize *ARGS:
    uv run python scripts/categorize_tweets.py {{ARGS}}

# Build a self-contained interactive HTML viewer -> data/tweets_viewer.html.
# Reads the canonical data/tweets_index.jsonl, so run `just index` first.
# Pass extra args through, e.g. `just viewer --out data/feed.html`.
viewer *ARGS:
    uv run python scripts/build_viewer.py {{ARGS}}

# Install the gws CLI via Homebrew.
install:
    #!/bin/sh
    set -eu
    if ! command -v brew >/dev/null 2>&1; then
        echo "error: Homebrew (brew) is not on your PATH." >&2
        echo "Install it from https://brew.sh and re-run 'just install'." >&2
        exit 1
    fi
    brew install googleworkspace-cli

# Requires gcloud to be installed and authenticated; gws auth setup uses it to
# create/configure the Cloud project, enable APIs, and perform an initial login.
#
# One-time Google Cloud + OAuth bootstrap (first time only).
auth-setup:
    #!/bin/sh
    set -eu
    if ! command -v gws >/dev/null 2>&1; then
        echo "error: gws is not installed. Run 'just install' first." >&2
        exit 1
    fi
    gws auth setup

# The '-s gmail' scope filter is required: unverified/testing-mode OAuth apps
# (the norm for personal @gmail.com accounts) cap consent at ~25 scopes, and the
# default 'recommended' preset (85+ scopes) fails.
#
# (Re)login, scoped to Gmail.
auth:
    #!/bin/sh
    set -eu
    if ! command -v gws >/dev/null 2>&1; then
        echo "error: gws is not installed. Run 'just install' first." >&2
        exit 1
    fi
    gws auth login -s gmail

# The returned JSON (email address, message/thread totals) is the success signal.
#
# Verify the active credential can reach Gmail.
auth-check:
    #!/bin/sh
    set -eu
    if ! command -v gws >/dev/null 2>&1; then
        echo "error: gws is not installed. Run 'just install' first." >&2
        exit 1
    fi
    gws gmail users getProfile --params '{"userId": "me"}'
