/* format.js — the assistant's reply, as readable HTML.
 *
 * A markdown library is 40 KB of someone else's code fetched from a CDN, and
 * this page has to work offline.  It is also more than the job needs: the
 * assistant writes prose with the occasional exact value, path or command in
 * it, and what has to survive is paragraphs, lists, `code`, fenced blocks and
 * **bold** — the parts that carry a number the artist will type into a slider.
 *
 * Everything is escaped FIRST and marked up afterwards, so no reply can put a
 * tag on this page no matter what it contains.
 */
(function (global) {
  "use strict";

  function escapeHtml(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Inline marks, applied to already-escaped text.  `code` first: what is
  // inside a code span is not bold, italic or a link.
  function inline(escaped) {
    var parts = escaped.split(/(`[^`]+`)/g);
    for (var i = 0; i < parts.length; i++) {
      if (i % 2) {
        parts[i] = "<code>" + parts[i].slice(1, -1) + "</code>";
        continue;
      }
      parts[i] = parts[i]
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
        .replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
        .replace(/(https?:\/\/[^\s<>"')\]]+)/g,
                 '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
    }
    return parts.join("");
  }

  function listItems(lines, strip) {
    var out = "";
    for (var i = 0; i < lines.length; i++) {
      out += "<li>" + inline(escapeHtml(lines[i].replace(strip, ""))) + "</li>";
    }
    return out;
  }

  // One blank-line-separated block: a heading, a bulleted list, a numbered
  // list, or a paragraph whose single newlines are kept as line breaks.
  function block(text) {
    var lines = text.split("\n");
    var heading = /^\s{0,3}#{1,6}\s+(.*)$/.exec(lines[0]);
    if (heading && lines.length === 1) {
      return "<h3>" + inline(escapeHtml(heading[1])) + "</h3>";
    }
    if (lines.every(function (l) { return /^\s*[-*•]\s+/.test(l); })) {
      return "<ul>" + listItems(lines, /^\s*[-*•]\s+/) + "</ul>";
    }
    if (lines.every(function (l) { return /^\s*\d+[.)]\s+/.test(l); })) {
      var start = parseInt(/^\s*(\d+)/.exec(lines[0])[1], 10);
      return '<ol start="' + start + '">' +
             listItems(lines, /^\s*\d+[.)]\s+/) + "</ol>";
    }
    return "<p>" + inline(escapeHtml(text)).replace(/\n/g, "<br>") + "</p>";
  }

  /** Format one reply. Returns an HTML string; never returns unescaped input. */
  function formatReply(text) {
    var source = String(text == null ? "" : text).replace(/\r\n?/g, "\n");
    if (!source.trim()) { return ""; }
    var html = "";
    // Fenced blocks are split out first so their contents are never treated
    // as prose — a code sample full of asterisks stays a code sample.
    var chunks = source.split(/```/g);
    for (var i = 0; i < chunks.length; i++) {
      if (i % 2) {
        var body = chunks[i].replace(/^[^\n]*\n?/, "");   // drop the language
        html += "<pre><code>" + escapeHtml(body.replace(/\n$/, "")) + "</code></pre>";
        continue;
      }
      var blocks = chunks[i].split(/\n\s*\n/);
      for (var b = 0; b < blocks.length; b++) {
        var piece = blocks[b].replace(/^\n+|\s+$/g, "");
        if (piece) { html += block(piece); }
      }
    }
    return html;
  }

  global.ForgeFormat = { formatReply: formatReply, escapeHtml: escapeHtml };
}(window));
