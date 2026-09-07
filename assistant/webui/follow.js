/* follow.js — which part is the conversation talking about?
 *
 * The Studio screen has the conversation on the left and ONE part's sheet on
 * the right, so the sheet has to keep up on its own: an artist who asks for a
 * lamp and then reaches for the slider should find the lamp's numbers already
 * under their hand, not a picker to hunt through.
 *
 * This file is the whole of that decision and nothing else — no DOM, no fetch,
 * no state.  It is separate from app.js for the same reason format.js is: a
 * rule this load-bearing should be runnable under node in a test, feeding a
 * canned /jobs entry in and asserting the name that comes out.
 *
 * What it is given is exactly what the bridge already publishes on a job:
 *
 *   job.state      "done" — a job still running has not decided anything yet
 *   job.activity   [{"kind": "tool"|"text"|"status", "label": "..."}]
 *   job.reply      the model's prose
 *   job.message    what was asked
 *
 * The labels are `bridge.tool_label()`'s work: a short tool name, a colon, and
 * a ≤60-character sketch of the most identifying argument — with any path
 * reduced to its basename.  So `partforge_new_part` arrives whole ("a small
 * magnet holder" -> the folder `small-magnet-holder`), while the script_path
 * tools arrive as "part.py", which names no folder at all.  That is why this
 * is layered rather than one rule.
 */
(function () {
  "use strict";

  //: The three tools that mean "the artist is now working on this part".
  //: Everything else the assistant can call — a check, an export, a render —
  //: is about a part it is already looking at, and moving the sheet on those
  //: would make the rail flicker through a part's whole history.
  var TOOLS = ["partforge_new_part", "partforge_open_in_panel",
               "partforge_generate"];

  //: The MCP prefixes the bridge already strips; accepted again here so a raw
  //: label from some other source still reads.
  var PREFIXES = ["mcp__forge__", "mcp__partforge__"];

  function slug(text) {
    return String(text == null ? "" : text).toLowerCase()
      .replace(/[^a-z0-9]+/g, "-").replace(/^-+/, "").replace(/-+$/, "");
  }

  //: `projects` may be the /projects answer, its list, or a list of names —
  //: whichever the caller has to hand.
  function entries(projects) {
    var list = projects;
    if (list && !Array.isArray(list) && Array.isArray(list.projects)) {
      list = list.projects;
    }
    if (!Array.isArray(list)) { return []; }
    var out = [];
    list.forEach(function (item) {
      if (typeof item === "string") {
        out.push({ name: item, scripts: [] });
        return;
      }
      if (!item || typeof item !== "object" || !item.name) { return; }
      var scripts = [];
      if (item.script) { scripts.push(String(item.script)); }
      (item.scripts || []).forEach(function (name) {
        if (scripts.indexOf(String(name)) < 0) { scripts.push(String(name)); }
      });
      out.push({ name: String(item.name), scripts: scripts });
    });
    return out;
  }

  function byName(known, wanted) {
    for (var i = 0; i < known.length; i++) {
      if (known[i].name === wanted) { return known[i].name; }
    }
    return "";
  }

  function bySlug(known, wanted) {
    var want = slug(wanted);
    if (!want) { return ""; }
    for (var i = 0; i < known.length; i++) {
      if (slug(known[i].name) === want) { return known[i].name; }
    }
    return "";
  }

  //: A script filename only names a part when exactly one part has it.  Most
  //: projects have a `part.py`, so "part.py" resolves to nothing on purpose —
  //: switching the sheet to whichever one happens to sort first would be worse
  //: than not switching at all.
  function byScript(known, filename) {
    var wanted = String(filename || "").toLowerCase();
    if (!/\.py$/.test(wanted)) { return ""; }
    var found = [];
    known.forEach(function (entry) {
      var hit = entry.scripts.some(function (script) {
        return String(script).toLowerCase() === wanted;
      });
      if (hit) { found.push(entry.name); }
    });
    return found.length === 1 ? found[0] : "";
  }

  function resolve(known, argument) {
    return byName(known, argument) || bySlug(known, argument) ||
           byScript(known, argument);
  }

  function shortName(name) {
    var text = String(name || "").trim();
    for (var i = 0; i < PREFIXES.length; i++) {
      if (text.indexOf(PREFIXES[i]) === 0) {
        text = text.slice(PREFIXES[i].length);
      }
    }
    return text;
  }

  //: "partforge_new_part: a small magnet holder" -> {tool, argument}
  function splitLabel(label) {
    var text = String(label || "").trim();
    var colon = text.indexOf(":");
    var tool = shortName(colon < 0 ? text : text.slice(0, colon));
    var argument = colon < 0 ? "" : text.slice(colon + 1).trim();
    return { tool: tool, argument: argument };
  }

  //: Every `projects/<name>` in some prose, in the order it was written.  Both
  //: slashes, because half of what the model writes about this machine is a
  //: Windows path.
  function pathsIn(text) {
    var out = [];
    var pattern = /projects[\/\\]([A-Za-z0-9][A-Za-z0-9._-]*)/g;
    var match = pattern.exec(String(text || ""));
    while (match) {
      out.push(match[1]);
      match = pattern.exec(String(text || ""));
    }
    return out;
  }

  //: The LAST known project named anywhere in some prose.  Last rather than
  //: first because a reply that mentions two parts ends on the one it just
  //: finished: "…so I took the numbers from the cup and made the lid."
  function fromText(known, text) {
    var body = String(text || "");
    if (!body) { return ""; }
    var best = "";
    var at = -1;
    //: A `projects/<name>` that matches no known project is still a folder
    //: name — the part was written this turn and the picker is one fetch
    //: behind.  It is the weakest signal here, so it is only used when
    //: nothing else resolved.
    var fallback = "";
    pathsIn(body).forEach(function (name) {
      var found = resolve(known, name);
      if (found) { best = found; at = body.lastIndexOf(name); }
      else { fallback = name; }
    });
    known.forEach(function (entry) {
      var where = body.toLowerCase().lastIndexOf(entry.name.toLowerCase());
      if (where < 0) { return; }
      // A whole word, so "cup" does not match inside "cupboard".
      var before = where === 0 ? " " : body.charAt(where - 1);
      var after = body.charAt(where + entry.name.length) || " ";
      if (/[A-Za-z0-9]/.test(before) || /[A-Za-z0-9]/.test(after)) { return; }
      if (where > at) { best = entry.name; at = where; }
    });
    return best || fallback;
  }

  /**
   * The part a finished job was about, or "" when it cannot be told.
   *
   * The rules, in order, and each one cheap:
   *
   *  1. Only a job whose state is "done" is read at all.
   *  2. Its tool activity is read NEWEST FIRST, and only the three part-shaped
   *     tools count.  The argument half of the label is resolved against the
   *     known projects by exact name, then by slug, then as a script filename
   *     that exactly one project has.
   *  3. `partforge_new_part` is special: its argument is the plain-words name
   *     the MCP server slugs into a folder, so when it matches nothing the
   *     slug is returned anyway — the part was just written and the caller's
   *     project list is one fetch out of date.
   *  4. Failing all that, the reply and then the question are read for a
   *     `projects/<name>` path or a known project's name, last mention wins
   *     (a name only, never a bare word inside a longer one).  A path naming
   *     a folder the picker has not heard of is used as a last resort, for
   *     the same reason as rule 3.
   *  5. Otherwise "": the sheet does not move.
   */
  function projectFromJob(job, projects) {
    if (!job || job.state !== "done") { return ""; }
    var known = entries(projects);
    var activity = job.activity || [];

    for (var i = activity.length - 1; i >= 0; i--) {
      var entry = activity[i] || {};
      if (entry.kind !== "tool") { continue; }
      var parsed = splitLabel(entry.label);
      if (TOOLS.indexOf(parsed.tool) < 0) { continue; }
      if (!parsed.argument) { continue; }
      var found = resolve(known, parsed.argument);
      if (found) { return found; }
      if (parsed.tool === "partforge_new_part") {
        var made = slug(parsed.argument);
        if (made) { return made; }
      }
    }

    return fromText(known, job.reply) || fromText(known, job.message);
  }

  window.ForgeFollow = {
    slug: slug,
    projectFromJob: projectFromJob
  };
}());
