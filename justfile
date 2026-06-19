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
