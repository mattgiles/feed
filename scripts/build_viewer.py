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
  --heart: #f91880; --warn: #ffd166; --err: #f4655f;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
header {
  padding: 16px 24px; border-bottom: 1px solid var(--border);
  position: sticky; top: 0; background: var(--bg); z-index: 5;
}
header h1 { margin: 0 0 4px; font-size: 20px; }
header .meta { color: var(--muted); font-size: 13px; }
.controls { margin-top: 12px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.controls input[type=search] {
  flex: 1 1 320px; min-width: 200px; padding: 8px 12px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--panel); color: var(--text);
  font-size: 14px;
}
.filters { display: flex; flex-wrap: wrap; gap: 6px; }
.filters button, .toolbtn {
  padding: 6px 11px; border-radius: 999px; border: 1px solid var(--border);
  background: var(--panel); color: var(--text); cursor: pointer; font-size: 13px;
}
.filters button:hover, .toolbtn:hover { border-color: var(--accent); }
.filters button.active { background: var(--accent); border-color: var(--accent); color: #fff; }
.controls select {
  padding: 7px 10px; border-radius: 8px; border: 1px solid var(--border);
  background: var(--panel); color: var(--text); font-size: 13px;
}
.toolbtn.on { background: var(--panel-2); border-color: var(--accent); }
.layout { display: flex; align-items: flex-start; }
nav {
  width: 260px; flex: 0 0 260px; padding: 16px; position: sticky; top: 150px;
  max-height: calc(100vh - 150px); overflow-y: auto;
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
nav button .favc { float: right; color: var(--heart); margin-left: 8px; font-size: 12px; }
nav button .desc { display: block; color: var(--muted); font-size: 12px; margin-top: 3px; }
main { flex: 1 1 auto; padding: 16px 24px; min-width: 0; }
.feedhead { color: var(--muted); font-size: 13px; margin: 0 0 12px; }
.card {
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
  padding: 14px 16px; margin-bottom: 14px;
}
.card.selected { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
.card .top { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
.chip {
  font-size: 12px; padding: 2px 9px; border-radius: 999px;
  background: var(--chip); color: var(--text); border: 1px solid var(--border);
}
.chip.review { border-color: var(--warn); color: var(--warn); }
.chip.err { border-color: var(--err); color: var(--err); }
.chip.status { color: var(--muted); }
.card .date, .card .author { color: var(--muted); font-size: 13px; }
.card .author { font-weight: 600; color: var(--text); }
.card .spacer { flex: 1 1 auto; }
.heart {
  border: none; background: none; cursor: pointer; font-size: 18px; line-height: 1;
  color: var(--muted); padding: 2px 4px;
}
.heart.on { color: var(--heart); }
.card .text { white-space: pre-wrap; word-wrap: break-word; margin: 6px 0; }
.card .links { margin: 8px 0 2px; padding: 0; list-style: none; }
.card .links li { margin: 3px 0; font-size: 13px; }
.card .links .type { color: var(--muted); font-size: 11px; text-transform: uppercase; margin-left: 6px; }
.card .links .lerr { color: var(--err); font-size: 11px; margin-left: 6px; }
.card .editrow { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 8px; }
.card .editrow select {
  padding: 5px 8px; border-radius: 7px; border: 1px solid var(--border);
  background: var(--panel-2); color: var(--text); font-size: 13px;
}
.card .editrow.changed select { border-color: var(--warn); }
.mid { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: var(--muted); }
.smallbtn {
  padding: 3px 8px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--panel-2); color: var(--text); cursor: pointer; font-size: 12px;
}
.smallbtn:hover { border-color: var(--accent); }
.card details { margin-top: 8px; }
.card details summary { cursor: pointer; color: var(--muted); font-size: 13px; }
.card .detailbody { margin-top: 8px; font-size: 13px; }
.card .detailbody .kv { margin: 4px 0; }
.card .detailbody .kv .k { color: var(--muted); margin-right: 6px; }
.card .detailbody .preview { color: var(--muted); font-style: italic; margin: 2px 0 6px; }
.card .detailbody .note { white-space: pre-wrap; }
.card .detailbody .suggest { color: var(--warn); }
.empty { color: var(--muted); padding: 40px 0; text-align: center; }
/* Compact mode */
body.compact .card { padding: 8px 12px; margin-bottom: 8px; border-radius: 8px; }
body.compact .card .text { margin: 3px 0; font-size: 14px; }
body.compact .card .links, body.compact .card details { display: none; }
body.compact .card .editrow { margin-top: 4px; }
/* Export panel */
#export-panel {
  display: none; margin-top: 12px; padding: 12px; border: 1px solid var(--border);
  border-radius: 10px; background: var(--panel);
}
#export-panel.open { display: block; }
#export-panel textarea {
  width: 100%; height: 160px; background: var(--bg); color: var(--text);
  border: 1px solid var(--border); border-radius: 8px; padding: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;
}
#export-panel .hint { color: var(--muted); font-size: 12px; margin: 0 0 6px; }
</style>
</head>
<body>
<header>
  <h1 id="hd-title"></h1>
  <div class="meta" id="hd-meta"></div>
  <div class="controls">
    <input id="search" type="search" placeholder="Search text, author, links, notes…" autocomplete="off">
    <div class="filters" id="filters"></div>
    <select id="sort" title="Sort order">
      <option value="newest">Newest first</option>
      <option value="oldest">Oldest first</option>
      <option value="favorites">Favorites first</option>
    </select>
    <button class="toolbtn" id="compact-btn" title="Toggle compact mode">Compact</button>
    <button class="toolbtn" id="export-btn" title="Export in-browser edits as a JSON delta">Export changes <span id="edit-count"></span></button>
  </div>
  <div id="export-panel">
    <p class="hint">Paste this <code>tweets</code> delta into <code>data/tweet_user_data.json</code> (merge by message_id), then re-run <code>just index</code> &amp; <code>just viewer</code>. Edits live only in this page until exported.</p>
    <textarea id="export-text" readonly></textarea>
    <div style="margin-top:8px; display:flex; gap:8px;">
      <button class="smallbtn" id="copy-export">Copy to clipboard</button>
      <button class="smallbtn" id="clear-export">Discard edits</button>
    </div>
  </div>
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

  var FILTERS = [
    { key: "all", label: "All" },
    { key: "favorites", label: "Favorites" },
    { key: "uncategorized", label: "Uncategorized" },
    { key: "needs-review", label: "Needs review" },
    { key: "fetch-errors", label: "Fetch errors" },
    { key: "hidden", label: "Hidden" }
  ];

  var state = {
    activeCategory: "__all__",
    searchTerm: "",
    filter: "all",
    sort: "newest",
    selectedIndex: -1
  };
  // In-memory edit set: message_id -> { category?, favorite? }.
  var edits = {};
  var lastVisible = [];

  // ---- effective (edited) values -----------------------------------------
  function effCat(t) {
    var e = edits[t.message_id];
    return (e && e.category != null) ? e.category : t.category;
  }
  function effFav(t) {
    var e = edits[t.message_id];
    return (e && e.favorite != null) ? e.favorite : !!(t.user && t.user.favorite);
  }
  function isHidden(t) { return !!(t.user && t.user.hidden); }
  function noteOf(t) { return (t.user && t.user.note) || ""; }

  function setEdit(mid, field, value, original) {
    var e = edits[mid] || {};
    if (value === original) { delete e[field]; }
    else { e[field] = value; }
    if (Object.keys(e).length === 0) delete edits[mid];
    else edits[mid] = e;
  }
  function editCount() { return Object.keys(edits).length; }

  // ---- header -------------------------------------------------------------
  document.getElementById("hd-title").textContent = DATA.title;
  document.getElementById("hd-meta").textContent =
    DATA.total + " tweets · " + DATA.total_favorites + " ♥ · generated " +
    DATA.generated_at + " · " +
    (DATA.enriched ? "enrichment present" : "no enrichment yet");

  // ---- search -------------------------------------------------------------
  function matchesSearch(t, term) {
    if (!term) return true;
    var parts = [t.text || "", t.author || "", noteOf(t)];
    (t.links || []).forEach(function (lk) { parts.push(lk.title || ""); });
    return parts.join(" ").toLowerCase().indexOf(term) !== -1;
  }

  function matchesFilter(t) {
    switch (state.filter) {
      case "favorites": return effFav(t);
      case "uncategorized": return effCat(t) === "uncategorized";
      case "needs-review": return !!(t.user && t.user.needs_review);
      case "fetch-errors":
        return (t.link_error_count > 0) || (t.tweet_fetch_status === "unavailable");
      case "hidden": return isHidden(t);
      default: return true;
    }
  }

  function visibleTweets() {
    var term = state.searchTerm.toLowerCase().trim();
    var list = DATA.tweets.filter(function (t) {
      // Hidden tweets only appear under the Hidden filter.
      if (state.filter !== "hidden" && isHidden(t)) return false;
      if (state.activeCategory !== "__all__" && effCat(t) !== state.activeCategory) return false;
      if (!matchesFilter(t)) return false;
      return matchesSearch(t, term);
    });
    list = list.slice();
    if (state.sort === "oldest") {
      list.sort(function (a, b) { return cmp(a, b); });
    } else if (state.sort === "favorites") {
      list.sort(function (a, b) {
        var fa = effFav(a) ? 0 : 1, fb = effFav(b) ? 0 : 1;
        if (fa !== fb) return fa - fb;
        return -cmp(a, b);
      });
    } else { // newest
      list.sort(function (a, b) { return -cmp(a, b); });
    }
    return list;
  }
  function cmp(a, b) {
    if (a.date !== b.date) return a.date < b.date ? -1 : 1;
    return a.message_id < b.message_id ? -1 : (a.message_id > b.message_id ? 1 : 0);
  }

  // ---- DOM helpers --------------------------------------------------------
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // ---- nav ----------------------------------------------------------------
  function renderNav() {
    var nav = document.getElementById("nav");
    nav.textContent = "";
    var allBtn = el("button");
    allBtn.appendChild(el("span", null, "All"));
    allBtn.appendChild(el("span", "count", String(DATA.total)));
    if (DATA.total_favorites) allBtn.appendChild(el("span", "favc", "♥" + DATA.total_favorites));
    if (state.activeCategory === "__all__") allBtn.className = "active";
    allBtn.addEventListener("click", function () { state.activeCategory = "__all__"; render(); });
    nav.appendChild(allBtn);

    DATA.categories.forEach(function (c) {
      var btn = el("button");
      btn.appendChild(el("span", null, c.name));
      btn.appendChild(el("span", "count", String(c.count)));
      if (c.favorites) btn.appendChild(el("span", "favc", "♥" + c.favorites));
      if (c.description) btn.appendChild(el("span", "desc", c.description));
      btn.title = c.description || "";
      if (c.count === 0) { btn.disabled = true; }
      else { btn.addEventListener("click", function () { state.activeCategory = c.name; render(); }); }
      if (state.activeCategory === c.name) btn.className = "active";
      nav.appendChild(btn);
    });
  }

  // ---- filters / toolbar --------------------------------------------------
  function renderFilters() {
    var box = document.getElementById("filters");
    box.textContent = "";
    FILTERS.forEach(function (f) {
      var b = el("button", state.filter === f.key ? "active" : null, f.label);
      b.addEventListener("click", function () {
        state.filter = f.key; state.selectedIndex = -1; render();
      });
      box.appendChild(b);
    });
  }

  function updateEditCount() {
    var n = editCount();
    document.getElementById("edit-count").textContent = n ? "(" + n + ")" : "";
  }

  // ---- card ---------------------------------------------------------------
  function renderCard(t, index) {
    var card = el("div", "card");
    card.dataset.index = String(index);
    if (index === state.selectedIndex) card.className = "card selected";

    var top = el("div", "top");
    var catChip = el("span", "chip", effCat(t));
    top.appendChild(catChip);
    if (t.date) top.appendChild(el("span", "date", t.date));
    if (t.author) top.appendChild(el("span", "author", t.author));
    if (t.user && t.user.needs_review) top.appendChild(el("span", "chip review", "needs review"));
    if (t.link_error_count > 0 || t.tweet_fetch_status === "unavailable")
      top.appendChild(el("span", "chip err", "fetch error"));
    if (t.enrichment_status && t.enrichment_status !== "ok")
      top.appendChild(el("span", "chip status", t.enrichment_status));
    if (t.user && t.user.suggested_category && t.user.suggested_category !== effCat(t))
      top.appendChild(el("span", "chip review", "suggests " + t.user.suggested_category));
    top.appendChild(el("span", "spacer"));
    var heart = el("button", "heart" + (effFav(t) ? " on" : ""), effFav(t) ? "♥" : "♡");
    heart.title = "Toggle favorite";
    heart.addEventListener("click", function (ev) {
      ev.stopPropagation();
      var orig = !!(t.user && t.user.favorite);
      setEdit(t.message_id, "favorite", !effFav(t), orig);
      updateEditCount(); renderFeed();
    });
    top.appendChild(heart);
    card.appendChild(top);

    if (t.text) card.appendChild(el("div", "text", t.text));

    if (t.links && t.links.length) {
      var ul = el("ul", "links");
      t.links.forEach(function (lk) {
        var li = el("li");
        if (lk.title) {
          var a = el("a", null, lk.title);
          a.href = lk.url; a.target = "_blank"; a.rel = "noopener noreferrer";
          li.appendChild(a);
        } else {
          var a2 = el("a", null, lk.url);
          a2.href = lk.url; a2.target = "_blank"; a2.rel = "noopener noreferrer";
          li.appendChild(a2);
        }
        if (lk.type) li.appendChild(el("span", "type", lk.type));
        if (lk.fetch_status === "error") li.appendChild(el("span", "lerr", "✕ fetch error"));
        ul.appendChild(li);
      });
      card.appendChild(ul);
    }

    // edit row: category select + message id + copy + view-on-x
    var er = el("div", "editrow");
    var orig = t.category;
    if (effCat(t) !== orig) er.className = "editrow changed";
    var sel = document.createElement("select");
    DATA.categories.forEach(function (c) {
      var o = document.createElement("option");
      o.value = c.name; o.textContent = c.name;
      if (c.name === effCat(t)) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener("click", function (ev) { ev.stopPropagation(); });
    sel.addEventListener("change", function () {
      setEdit(t.message_id, "category", sel.value, orig);
      updateEditCount(); renderFeed();
    });
    er.appendChild(sel);

    var midSpan = el("span", "mid", t.message_id);
    er.appendChild(midSpan);
    var copyBtn = el("button", "smallbtn", "copy id");
    copyBtn.addEventListener("click", function (ev) {
      ev.stopPropagation(); copyText(t.message_id, copyBtn);
    });
    er.appendChild(copyBtn);
    if (t.permalink) {
      var ax = el("a", "smallbtn", "View on X");
      ax.href = t.permalink; ax.target = "_blank"; ax.rel = "noopener noreferrer";
      ax.addEventListener("click", function (ev) { ev.stopPropagation(); });
      er.appendChild(ax);
    }
    card.appendChild(er);

    // details
    var det = document.createElement("details");
    var sum = document.createElement("summary");
    sum.textContent = "Details";
    det.appendChild(sum);
    var body = el("div", "detailbody");
    body.appendChild(kv("enrichment", t.enrichment_status));
    if (t.primary_url) {
      var pk = el("div", "kv");
      pk.appendChild(el("span", "k", "primary"));
      var pa = el("a", null, t.primary_url);
      pa.href = t.primary_url; pa.target = "_blank"; pa.rel = "noopener noreferrer";
      pk.appendChild(pa); body.appendChild(pk);
    }
    if (t.source_urls && t.source_urls.length)
      body.appendChild(kv("source urls", t.source_urls.join("  ")));
    if (t.links && t.links.length) {
      t.links.forEach(function (lk) {
        if (lk.preview) {
          var p = el("div", "preview", "“" + lk.preview + "”");
          body.appendChild(p);
        }
      });
    }
    if (noteOf(t)) {
      var nk = el("div", "kv");
      nk.appendChild(el("span", "k", "note"));
      nk.appendChild(el("span", "note", noteOf(t)));
      body.appendChild(nk);
    }
    if (t.user && t.user.suggested_category) {
      var s = "suggested: " + t.user.suggested_category +
        (t.user.suggested_reason ? " — " + t.user.suggested_reason : "");
      body.appendChild(el("div", "suggest", s));
    }
    det.appendChild(body);
    card.appendChild(det);

    card.addEventListener("click", function () {
      state.selectedIndex = index; highlightSelected();
    });
    return card;
  }

  function kv(k, v) {
    var d = el("div", "kv");
    d.appendChild(el("span", "k", k));
    d.appendChild(el("span", null, String(v)));
    return d;
  }

  function copyText(text, btn) {
    var done = function () { var o = btn.textContent; btn.textContent = "copied"; setTimeout(function () { btn.textContent = o; }, 900); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
    } else { fallbackCopy(text); done(); }
  }
  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(ta);
  }

  // ---- feed ---------------------------------------------------------------
  function renderFeed() {
    var feed = document.getElementById("feed");
    feed.textContent = "";
    var tweets = visibleTweets();
    lastVisible = tweets;
    if (state.selectedIndex >= tweets.length) state.selectedIndex = tweets.length - 1;

    var head = el("p", "feedhead",
      tweets.length + " shown" +
      (state.filter !== "all" ? " · filter: " + state.filter : "") +
      (state.activeCategory !== "__all__" ? " · " + state.activeCategory : ""));
    feed.appendChild(head);

    if (!tweets.length) {
      feed.appendChild(el("div", "empty", "No tweets match this filter."));
      return;
    }
    tweets.forEach(function (t, i) { feed.appendChild(renderCard(t, i)); });
  }

  function highlightSelected() {
    var cards = document.querySelectorAll(".card");
    cards.forEach(function (c) {
      var idx = Number(c.dataset.index);
      c.className = (idx === state.selectedIndex) ? "card selected" : "card";
    });
    var sel = document.querySelector('.card[data-index="' + state.selectedIndex + '"]');
    if (sel) sel.scrollIntoView({ block: "nearest" });
  }

  function moveSelection(delta) {
    if (!lastVisible.length) return;
    var i = state.selectedIndex;
    if (i < 0) { i = 0; }
    else { i = Math.max(0, Math.min(lastVisible.length - 1, i + delta)); }
    state.selectedIndex = i; highlightSelected();
  }

  function render() {
    renderNav(); renderFilters(); updateEditCount(); renderFeed();
  }

  // ---- export -------------------------------------------------------------
  function buildExport() {
    var out = { schema_version: 1, tweets: {} };
    Object.keys(edits).sort().forEach(function (mid) {
      out.tweets[mid] = edits[mid];
    });
    return JSON.stringify(out, null, 2);
  }

  document.getElementById("export-btn").addEventListener("click", function () {
    var panel = document.getElementById("export-panel");
    panel.classList.toggle("open");
    if (panel.classList.contains("open"))
      document.getElementById("export-text").value = buildExport();
  });
  document.getElementById("copy-export").addEventListener("click", function (e) {
    document.getElementById("export-text").value = buildExport();
    copyText(buildExport(), e.target);
  });
  document.getElementById("clear-export").addEventListener("click", function () {
    edits = {};
    document.getElementById("export-text").value = buildExport();
    updateEditCount(); renderFeed();
  });

  // ---- search / sort / compact -------------------------------------------
  document.getElementById("search").addEventListener("input", function (e) {
    state.searchTerm = e.target.value; state.selectedIndex = -1; renderFeed();
  });
  document.getElementById("sort").addEventListener("change", function (e) {
    state.sort = e.target.value; state.selectedIndex = -1; renderFeed();
  });

  var COMPACT_KEY = "tweetviewer.compact";
  function applyCompact(on) {
    document.body.classList.toggle("compact", on);
    document.getElementById("compact-btn").classList.toggle("on", on);
  }
  var compactBtn = document.getElementById("compact-btn");
  var savedCompact = false;
  try { savedCompact = localStorage.getItem(COMPACT_KEY) === "1"; } catch (e) {}
  applyCompact(savedCompact);
  compactBtn.addEventListener("click", function () {
    var on = !document.body.classList.contains("compact");
    applyCompact(on);
    try { localStorage.setItem(COMPACT_KEY, on ? "1" : "0"); } catch (e) {}
  });

  // ---- keyboard nav -------------------------------------------------------
  document.addEventListener("keydown", function (e) {
    var tag = (e.target && e.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); moveSelection(1); }
    else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); moveSelection(-1); }
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
