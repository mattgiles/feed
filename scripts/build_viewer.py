# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Build a self-contained interactive HTML viewer of categorized tweets.

Reads the single canonical record stream ``data/tweets_index.jsonl`` (built by
``scripts/build_index.py``) — no more re-joining CSVs — and orders categories by
the locked taxonomy ``data/categories.json``. Every per-tweet record already
carries the tweet metadata, extracted link content (incl. ``preview``), the
enrichment status, and the user's decisions (``category``/``favorite``/``note``/
``hidden``/``needs_review``/``suggested_*``). The assembled feed is embedded
inline as a JSON island in one HTML file that opens by double-click
(``file://``, no server, no network).

Stdlib-only and deterministic (``uv run``, no third-party deps). The only
nondeterministic field is the ``generated_at`` timestamp. Run ``just index``
first to refresh the canonical input.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from tweet_data import UNCATEGORIZED, load_categories, merged_entry


def load_index(path: Path) -> list[dict]:
    """Read tweets_index.jsonl -> per-tweet payload dicts for the viewer.

    Flattens each canonical index record into the shape the inline viewer
    script consumes: the tweet metadata hoisted to the top level plus the full
    ``user`` decisions, ``links`` (with ``preview``), ``enrichment_status``,
    ``link_error_count``, ``source_urls``, and ``primary_url``. Sorted by
    ``(date, message_id)`` (the index is already sorted; we re-sort defensively).
    """
    if not path.exists():
        print(
            f"error: index not found: {path} (run 'just index' first)",
            file=sys.stderr,
        )
        sys.exit(1)
    out: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            tweet = rec.get("tweet") or {}
            user = rec.get("user") or {}
            out.append(
                {
                    "message_id": rec.get("message_id", ""),
                    "date": rec.get("date", ""),
                    "category": user.get("category", UNCATEGORIZED),
                    "text": tweet.get("text", ""),
                    "author": tweet.get("author"),
                    "permalink": tweet.get("permalink") or rec.get("primary_url", ""),
                    "primary_url": rec.get("primary_url", ""),
                    "source_urls": rec.get("source_urls", []),
                    "links": rec.get("links", []),
                    "enrichment_status": rec.get("enrichment_status", "missing"),
                    "link_error_count": rec.get("link_error_count", 0),
                    "tweet_fetch_status": tweet.get("fetch_status", "unavailable"),
                    "user": user,
                }
            )
    out.sort(key=lambda t: (t["date"], t["message_id"]))
    return out


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  --bg: #0f1419; --panel: #16202a; --panel-2: #1c2732; --border: #2f3b47;
  --text: #e7e9ea; --muted: #8b98a5; --accent: #1d9bf0; --chip: #243340;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
header {
  padding: 20px 24px; border-bottom: 1px solid var(--border);
  position: sticky; top: 0; background: var(--bg); z-index: 5;
}
header h1 { margin: 0 0 4px; font-size: 20px; }
header .meta { color: var(--muted); font-size: 13px; }
header .search { margin-top: 12px; }
header .search input {
  width: 100%; max-width: 480px; padding: 8px 12px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--panel); color: var(--text);
  font-size: 14px;
}
.layout { display: flex; align-items: flex-start; }
nav {
  width: 260px; flex: 0 0 260px; padding: 16px; position: sticky; top: 121px;
  max-height: calc(100vh - 121px); overflow-y: auto;
}
nav button {
  display: block; width: 100%; text-align: left; margin-bottom: 6px;
  padding: 8px 10px; border-radius: 8px; border: 1px solid transparent;
  background: var(--panel); color: var(--text); cursor: pointer; font-size: 14px;
}
nav button:hover:not(:disabled) { border-color: var(--border); }
nav button.active { background: var(--panel-2); border-color: var(--accent); }
nav button:disabled { opacity: 0.4; cursor: default; }
nav button .count { float: right; color: var(--muted); }
nav button .desc { display: block; color: var(--muted); font-size: 12px; margin-top: 3px; }
main { flex: 1 1 auto; padding: 16px 24px; min-width: 0; }
.card {
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
  padding: 14px 16px; margin-bottom: 14px;
}
.card .top { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
.chip {
  font-size: 12px; padding: 2px 9px; border-radius: 999px;
  background: var(--chip); color: var(--text); border: 1px solid var(--border);
}
.card .date, .card .author { color: var(--muted); font-size: 13px; }
.card .author { font-weight: 600; color: var(--text); }
.card .text { white-space: pre-wrap; word-wrap: break-word; margin: 6px 0; }
.card .links { margin: 8px 0 2px; padding: 0; list-style: none; }
.card .links li { margin: 3px 0; font-size: 13px; }
.card .links .type { color: var(--muted); font-size: 11px; text-transform: uppercase; margin-left: 6px; }
.card .actions { margin-top: 8px; font-size: 13px; }
.empty { color: var(--muted); padding: 40px 0; text-align: center; }
</style>
</head>
<body>
<header>
  <h1 id="hd-title"></h1>
  <div class="meta" id="hd-meta"></div>
  <div class="search"><input id="search" type="search" placeholder="Search text or author…" autocomplete="off"></div>
</header>
<div class="layout">
  <nav id="nav"></nav>
  <main id="feed"></main>
</div>
<script type="application/json" id="feed-data">__PAYLOAD__</script>
<script>
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("feed-data").textContent);
  var descByName = {};
  DATA.categories.forEach(function (c) { descByName[c.name] = c.description; });

  var state = { activeCategory: "__all__", searchTerm: "" };

  document.getElementById("hd-title").textContent = DATA.title;
  document.getElementById("hd-meta").textContent =
    DATA.total + " tweets · generated " + DATA.generated_at +
    " · " + (DATA.enriched ? "enriched overlay applied" : "CSV-only (no enriched overlay)");

  function matchesSearch(t, term) {
    if (!term) return true;
    var hay = ((t.text || "") + " " + (t.author || "")).toLowerCase();
    return hay.indexOf(term) !== -1;
  }

  function visibleTweets() {
    var term = state.searchTerm.toLowerCase().trim();
    return DATA.tweets.filter(function (t) {
      if (state.activeCategory !== "__all__" && t.category !== state.activeCategory) return false;
      return matchesSearch(t, term);
    });
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function renderNav() {
    var nav = document.getElementById("nav");
    nav.textContent = "";
    var allBtn = el("button");
    allBtn.appendChild(el("span", null, "All"));
    var allCount = el("span", "count", String(DATA.total));
    allBtn.appendChild(allCount);
    if (state.activeCategory === "__all__") allBtn.className = "active";
    allBtn.addEventListener("click", function () { state.activeCategory = "__all__"; render(); });
    nav.appendChild(allBtn);

    DATA.categories.forEach(function (c) {
      var btn = el("button");
      var label = el("span", null, c.name);
      btn.appendChild(label);
      btn.appendChild(el("span", "count", String(c.count)));
      if (c.description) btn.appendChild(el("span", "desc", c.description));
      btn.title = c.description || "";
      if (c.count === 0) {
        btn.disabled = true;
      } else {
        btn.addEventListener("click", function () { state.activeCategory = c.name; render(); });
      }
      if (state.activeCategory === c.name) btn.className = "active";
      nav.appendChild(btn);
    });
  }

  function renderCard(t) {
    var card = el("div", "card");
    var top = el("div", "top");
    top.appendChild(el("span", "chip", t.category));
    if (t.date) top.appendChild(el("span", "date", t.date));
    if (t.author) top.appendChild(el("span", "author", t.author));
    card.appendChild(top);

    if (t.text) card.appendChild(el("div", "text", t.text));

    if (t.links && t.links.length) {
      var ul = el("ul", "links");
      t.links.forEach(function (lk) {
        var li = el("li");
        var a = el("a", null, lk.title);
        a.href = lk.url; a.target = "_blank"; a.rel = "noopener noreferrer";
        li.appendChild(a);
        if (lk.type) li.appendChild(el("span", "type", lk.type));
        ul.appendChild(li);
      });
      card.appendChild(ul);
    }

    if (t.permalink) {
      var actions = el("div", "actions");
      var a = el("a", null, "View on X");
      a.href = t.permalink; a.target = "_blank"; a.rel = "noopener noreferrer";
      actions.appendChild(a);
      card.appendChild(actions);
    }
    return card;
  }

  function renderFeed() {
    var feed = document.getElementById("feed");
    feed.textContent = "";
    var tweets = visibleTweets();
    if (!tweets.length) {
      feed.appendChild(el("div", "empty", "No tweets match this filter."));
      return;
    }
    tweets.forEach(function (t) { feed.appendChild(renderCard(t)); });
  }

  function render() { renderNav(); renderFeed(); }

  document.getElementById("search").addEventListener("input", function (e) {
    state.searchTerm = e.target.value;
    renderFeed();
  });

  render();
})();
</script>
</body>
</html>
"""


def render_html(payload: dict, title: str) -> str:
    """Serialize the payload as a safe JSON island and fill the HTML template."""
    blob = json.dumps(payload, ensure_ascii=False)
    # Prevent premature </script> termination from any embedded text.
    blob = blob.replace("</", "<\\/")
    html = HTML_TEMPLATE.replace("__PAYLOAD__", blob)
    # Title is rendered into <title> only; escape the HTML-significant chars.
    safe_title = (
        title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    html = html.replace("__TITLE__", safe_title)
    return html


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default="data/tweets_index.jsonl")
    parser.add_argument("--categories", default="data/categories.json")
    parser.add_argument("--out", default="data/tweets_viewer.html")
    parser.add_argument("--title", default="Tweet feed")
    args = parser.parse_args()

    ordered_categories, _valid = load_categories(Path(args.categories))
    tweets = load_index(Path(args.index))

    counts: dict[str, int] = {}
    fav_counts: dict[str, int] = {}
    enriched_applied = False
    for t in tweets:
        cat = t["category"]
        counts[cat] = counts.get(cat, 0) + 1
        if t["user"].get("favorite"):
            fav_counts[cat] = fav_counts.get(cat, 0) + 1
        if t["enrichment_status"] != "missing":
            enriched_applied = True
    total = len(tweets)
    total_favorites = sum(fav_counts.values())

    categories_payload = [
        {
            "name": c["name"],
            "description": c["description"],
            "count": counts.get(c["name"], 0),
            "favorites": fav_counts.get(c["name"], 0),
        }
        for c in ordered_categories
    ]

    payload = {
        "title": args.title,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "categories": categories_payload,
        "tweets": tweets,
        "total": total,
        "total_favorites": total_favorites,
        "enriched": enriched_applied,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(payload, args.title), encoding="utf-8")

    print(f"Wrote viewer with {total} tweets -> {out_path}", file=sys.stderr)
    for c in categories_payload:
        print(f"  {c['name']}: {c['count']} ({c['favorites']} \u2665)", file=sys.stderr)
    print(
        "enrichment overlay: "
        + ("present" if enriched_applied else "absent (index has no enriched records)"),
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
