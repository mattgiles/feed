# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Build a self-contained interactive HTML viewer of categorized tweets.

Joins the mined ``data/tweets.csv`` (one row per self-emailed link) with the
committed per-tweet mapping ``data/tweet_categories.csv`` by ``message_id``
(mirroring ``scripts/categorize_tweets.py``), orders categories by the locked
taxonomy ``data/categories.json``, and — when present — overlays the richer
``data/tweets_enriched.jsonl`` (author, full tweet text, extracted link titles)
by ``message_id``. The assembled feed is embedded inline as a JSON island in one
HTML file that opens by double-click (``file://``, no server, no network).

Stdlib-only and deterministic, mirroring ``scripts/categorize_tweets.py`` and
``scripts/mine_tweets.py`` (``uv run``, no third-party deps). The enriched
overlay is optional: absent jsonl ⇒ the viewer renders cleanly from the two
CSVs alone.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

UNCATEGORIZED = "uncategorized"

# Match an x.com / twitter.com status permalink and capture the tweet id.
STATUS_RE = re.compile(
    r"(?:x\.com|twitter\.com)/(?:i/web/status|[^/]+/status)/(\d+)", re.IGNORECASE
)
TWITTER_HOST_RE = re.compile(r"(^|\.)(x\.com|twitter\.com)$", re.IGNORECASE)
TCO_RE = re.compile(r"\bt\.co/", re.IGNORECASE)


def load_categories(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    """Read categories.json -> (ordered [{name, description}], name set).

    Preserves file order and appends a synthetic trailing ``uncategorized``
    entry so the UI can render it last.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    ordered: list[dict[str, str]] = []
    names: set[str] = set()
    for c in data.get("categories", []):
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


def load_tweet_categories(path: Path, valid: set[str]) -> dict[str, str]:
    """Read tweet_categories.csv -> {message_id: category}, validating names.

    Exits non-zero (listing the offending rows) if any category is neither a
    name in ``valid`` nor the literal ``uncategorized`` — the same fail-closed
    behavior as ``scripts/categorize_tweets.py``.
    """
    mapping: dict[str, str] = {}
    offending: list[tuple[str, str]] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            message_id = (row.get("message_id") or "").strip()
            category = (row.get("category") or "").strip()
            if not message_id:
                continue
            if category not in valid and category != UNCATEGORIZED:
                offending.append((message_id, category))
                continue
            mapping[message_id] = category
    if offending:
        print(
            f"error: {len(offending)} row(s) in {path} use a category not in "
            f"categories.json (and not '{UNCATEGORIZED}'):",
            file=sys.stderr,
        )
        for message_id, category in offending:
            print(f"  {message_id}: {category!r}", file=sys.stderr)
        sys.exit(1)
    return mapping


def _is_twitter_host(url: str) -> bool:
    m = re.match(r"^[a-z]+://([^/]+)", url, re.IGNORECASE)
    host = m.group(1) if m else ""
    # Strip any userinfo/port.
    host = host.split("@")[-1].split(":")[0]
    return bool(TWITTER_HOST_RE.search(host))


def _primary_url(urls: list[str]) -> str:
    """Pick a display URL: prefer an x.com/twitter status permalink, else the
    first non-t.co URL, else the first URL."""
    for url in urls:
        if STATUS_RE.search(url) and _is_twitter_host(url):
            return url
    for url in urls:
        if not TCO_RE.search(url):
            return url
    return urls[0] if urls else ""


def load_tweets(path: Path) -> dict[str, dict]:
    """Read tweets.csv and group rows by message_id.

    Keeps the first ``date`` and ``subject`` seen, collects all ``url``s deduped
    in order (mirroring ``enrich_tweets.ts::groupByMessage``), and derives a
    primary display URL per group.
    """
    groups: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            message_id = (row.get("message_id") or "").strip()
            if not message_id:
                continue
            url = (row.get("url") or "").strip()
            group = groups.get(message_id)
            if group is None:
                group = {
                    "message_id": message_id,
                    "date": (row.get("date") or "").strip(),
                    "subject": (row.get("subject") or ""),
                    "urls": [],
                }
                groups[message_id] = group
            if url and url not in group["urls"]:
                group["urls"].append(url)
    for group in groups.values():
        group["primary_url"] = _primary_url(group["urls"])
    return groups


def load_enriched(path: Path) -> dict[str, dict]:
    """Parse tweets_enriched.jsonl -> {message_id: record}, skipping malformed
    lines. Returns ``{}`` when the file is absent."""
    if not path.exists():
        return {}
    records: dict[str, dict] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            mid = rec.get("message_id")
            if isinstance(mid, str) and mid:
                records[mid] = rec
    return records


def _enriched_links(rec: dict) -> list[dict[str, str]]:
    """Map an enriched record's ``ultimate[]`` to {url, title, type}, omitting
    entries with an empty title."""
    links: list[dict[str, str]] = []
    for entry in rec.get("ultimate", []) or []:
        if not isinstance(entry, dict):
            continue
        title = (entry.get("title") or "").strip()
        url = (entry.get("url") or "").strip()
        if not title or not url:
            continue
        links.append({"url": url, "title": title, "type": entry.get("type") or ""})
    return links


def assemble(
    tweets: dict[str, dict],
    mapping: dict[str, str],
    enriched: dict[str, dict],
) -> list[dict]:
    """Join groups × category mapping × enriched overlay into per-tweet dicts,
    sorted by date ascending then message_id."""
    out: list[dict] = []
    for mid, group in tweets.items():
        category = mapping.get(mid, UNCATEGORIZED)
        rec = enriched.get(mid)
        tweet_meta = (rec or {}).get("tweet") or {}

        enriched_text = (tweet_meta.get("text") or "").strip()
        text = enriched_text if enriched_text else group["subject"]
        author = tweet_meta.get("author") or None
        permalink = tweet_meta.get("permalink") or group["primary_url"]
        links = _enriched_links(rec) if rec else []

        out.append(
            {
                "message_id": mid,
                "date": group["date"],
                "category": category,
                "text": text,
                "author": author,
                "permalink": permalink,
                "urls": group["urls"],
                "links": links,
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
    parser.add_argument("--tweets", default="data/tweets.csv")
    parser.add_argument("--mapping", default="data/tweet_categories.csv")
    parser.add_argument("--categories", default="data/categories.json")
    parser.add_argument("--enriched", default="data/tweets_enriched.jsonl")
    parser.add_argument("--out", default="data/tweets_viewer.html")
    parser.add_argument("--title", default="Tweet feed")
    args = parser.parse_args()

    ordered_categories, valid = load_categories(Path(args.categories))
    mapping = load_tweet_categories(Path(args.mapping), valid)
    tweets = load_tweets(Path(args.tweets))
    enriched = load_enriched(Path(args.enriched))
    enriched_applied = bool(enriched)

    assembled = assemble(tweets, mapping, enriched)

    counts: dict[str, int] = {}
    for t in assembled:
        counts[t["category"]] = counts.get(t["category"], 0) + 1
    total = len(assembled)

    categories_payload = [
        {
            "name": c["name"],
            "description": c["description"],
            "count": counts.get(c["name"], 0),
        }
        for c in ordered_categories
    ]

    payload = {
        "title": args.title,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "categories": categories_payload,
        "tweets": assembled,
        "total": total,
        "enriched": enriched_applied,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(payload, args.title), encoding="utf-8")

    print(f"Wrote viewer with {total} tweets -> {out_path}", file=sys.stderr)
    for c in categories_payload:
        print(f"  {c['name']}: {c['count']}", file=sys.stderr)
    print(
        "enriched overlay: "
        + ("applied" if enriched_applied else "absent (CSV-only)"),
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
