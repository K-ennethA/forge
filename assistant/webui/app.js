/* app.js — the Forge web UI.
 *
 * Vanilla, no build step, no framework, no CDN: this is served by a stdlib
 * HTTP server on a machine that may have no internet, and it has to keep
 * working in five years without a toolchain being alive to rebuild it.
 *
 * It talks to exactly one origin — the bridge that served it — through the
 * same API the Blender panel uses (/ask, /job, /cancel, /new) plus the routes
 * that only make sense for a browser (/jobs, /upload, /file, /services/*,
 * /flows).  Both surfaces share one session and one job list, so a question
 * asked here shows up in Blender's panel and vice versa.
 */
(function () {
  "use strict";

  var fmt = window.ForgeFormat;

  // ------------------------------------------------------------------ dom --
  function $(id) { return document.getElementById(id); }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text != null) { node.textContent = text; }
    return node;
  }

  // ----------------------------------------------------------------- http --
  function api(path, options) {
    options = options || {};
    var init = { method: options.method || (options.body ? "POST" : "GET"),
                 headers: { "Accept": "application/json" } };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    return fetch(path, init).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = { error: text }; }
        return { ok: response.ok, status: response.status, data: data || {} };
      });
    }).catch(function (err) {
      // The bridge going away mid-poll is a normal thing on a machine where
      // the artist restarts services; it is a message, not an exception.
      return { ok: false, status: 0, data: {
        error: "The assistant bridge is not answering (" + err + "). Is it running?" } };
    });
  }

  // ---------------------------------------------------------------- state --
  var state = {
    jobs: {},          // job_id -> the last snapshot we saw
    nodes: {},         // job_id -> its <article> in the thread
    order: [],         // job ids, oldest first
    attachment: null,  // {path, url, name} from POST /upload
    polling: null,
    flows: null,
    sessionCost: 0
  };

  //: The part sheet (Phase 11's workbench, now the Studio's right rail): the
  //: part being edited, its parameter controls, and the last picture of it.
  //: Kept apart from `state` because it survives nothing — every one of these
  //: is refetched when the page is opened.
  var wb = {
    projects: null,   // the last /projects answer
    project: null,    // the selected entry from it
    controls: {},     // parameter name -> its control
    preview: null,    // the last /preview answer
    scene: null,      // the last /scene answer
    loading: null     // the in-flight /projects promise, so two callers share one
  };

  //: Auto-follow (Phase 14): the rail keeps up with the conversation by
  //: itself.  `seen` is per job so one finished turn is read exactly once —
  //: the poll returns the same job several times.  `pinned` is the part the
  //: artist chose by hand, which holds until the next detection.
  var follow = {
    seen: {},
    pinned: null
  };

  //: The library (Phase 13): every project as a card, and whatever Blender is
  //: holding beside them.  One fetch, refetched whenever the tab is opened.
  var lib = {
    data: null        // the last /library answer
  };

  function debounce(fn, ms) {
    var timer = null;
    return function () {
      var args = arguments, self = this;
      if (timer) { clearTimeout(timer); }
      timer = setTimeout(function () { timer = null; fn.apply(self, args); }, ms);
    };
  }

  function numberOr(value, fallback) {
    var number = typeof value === "number" ? value : parseFloat(value);
    return (typeof number === "number" && isFinite(number)) ? number : fallback;
  }

  // ---------------------------------------------------------------- banner --
  function banner(kind, text, detail) {
    var box = el("div", "banner " + kind);
    var body = el("div");
    body.appendChild(el("div", null, text));
    if (detail) {
      var pre = el("pre", null, detail);
      body.appendChild(pre);
    }
    box.appendChild(body);
    var close = el("button", "close", "×");
    close.title = "Dismiss";
    close.addEventListener("click", function () { box.remove(); });
    box.appendChild(close);
    $("banners").appendChild(box);
    if (kind === "info") { setTimeout(function () { box.remove(); }, 9000); }
    return box;
  }

  // ---------------------------------------------------------------- health --
  function renderHealth(data) {
    var strip = $("health");
    strip.textContent = "";
    (data.services || []).forEach(function (svc) {
      var cls = svc.ok ? "ok" : (svc.warn ? "warn" : "down");
      if (svc.ok && svc.warn) { cls = "warn"; }
      var node = el("span", "svc " + cls + (svc.optional ? " optional" : ""));
      node.appendChild(el("span", "dot"));
      node.appendChild(el("span", null, svc.label));
      node.title = (svc.label + ": " + (svc.detail || (svc.ok ? "running" : "not running")) +
                    (svc.url ? "\n" + svc.url : "") +
                    (svc.address ? "\n" + svc.address : ""));
      strip.appendChild(node);
    });
    if (typeof data.session_cost_usd === "number") { setCost(data.session_cost_usd); }
  }

  function refreshHealth() {
    return api("/services/health").then(function (res) {
      if (res.ok) { renderHealth(res.data); }
      else { $("health").textContent = "services unknown"; }
    });
  }

  function setCost(value) {
    state.sessionCost = value || 0;
    var cost = $("cost");
    cost.textContent = "$" + (state.sessionCost < 0.005 && state.sessionCost > 0
      ? state.sessionCost.toFixed(4) : state.sessionCost.toFixed(2));
    cost.title = "This conversation has cost $" + state.sessionCost.toFixed(4) +
                 " so far. A new conversation resets it.";
  }

  // ------------------------------------------------------------------ turn --
  function relTime(seconds) {
    if (!seconds) { return ""; }
    var date = new Date(seconds * 1000);
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function activityList(entries, tail) {
    var box = el("div", "activity");
    var shown = tail ? entries.slice(-tail) : entries;
    shown.forEach(function (entry) {
      var line = el("div", "line " + (entry.kind || "status"));
      line.appendChild(el("span", "k", entry.kind === "tool" ? "›" : "·"));
      line.appendChild(el("span", "t", entry.label || ""));
      box.appendChild(line);
    });
    return box;
  }

  //: A mechanism demo (Phase 17): two seconds of the thing working.  Looping
  //: and muted on purpose — the clip is short by construction and silent by
  //: construction (the renderer writes no audio track at all), so a play button
  //: the artist has to press twice to see the stroke would be a worse picture
  //: of the same file.  `controls` stays, because scrubbing a 2 mm press is the
  //: whole point.
  function demoVideo(file) {
    var video = el("video", "demo");
    video.src = file.url;
    video.controls = true;
    video.loop = true;
    video.muted = true;
    video.playsInline = true;
    video.setAttribute("playsinline", "");
    video.preload = "metadata";
    video.title = file.path || file.name || "";
    return video;
  }

  function gallery(files) {
    var box = el("div", "gallery");
    files.forEach(function (file) {
      if (file.kind === "video") {
        var clip = el("figure", "is-video");
        clip.appendChild(demoVideo(file));
        clip.appendChild(el("figcaption", null, file.path));
        box.appendChild(clip);
      } else if (file.kind === "image") {
        var figure = el("figure");
        var img = el("img");
        img.src = file.url;
        img.alt = file.name;
        img.loading = "lazy";
        figure.appendChild(img);
        figure.appendChild(el("figcaption", null, file.path));
        box.appendChild(figure);
      } else {
        var link = el("a", "filechip", file.name);
        link.href = file.url;
        link.title = file.path;
        link.setAttribute("download", file.name);
        box.appendChild(link);
      }
    });
    return box;
  }

  //: The selector says Fast/Smart/Deepest, so the footer does too — an artist
  //: choosing "how much thinking" should not have to remember which alias that
  //: was.  The CLI's own reported model still shows when nothing was asked for.
  var MODEL_LABELS = { haiku: "Fast", sonnet: "Smart", opus: "Deepest" };

  function footer(job) {
    var foot = el("div", "foot");
    function pill(text, cls) {
      if (!text) { return; }
      foot.appendChild(el("span", "pill" + (cls ? " " + cls : ""), text));
    }
    if (job.state === "queued") { pill("waiting its turn", "queued"); }
    pill(MODEL_LABELS[job.requested_model] || job.model || null);
    if (job.duration_ms) { pill((job.duration_ms / 1000).toFixed(1) + " s"); }
    if (typeof job.cost_usd === "number") { pill("$" + job.cost_usd.toFixed(4)); }
    if (job.created_at) { pill(relTime(job.created_at)); }
    return foot;
  }

  function renderTurn(job) {
    var turn = el("article", "turn");
    turn.dataset.job = job.job_id;

    // -- what was asked
    var user = el("div", "bubble user");
    user.appendChild(el("div", null, job.message || ""));
    (job.files || []).forEach(function (file) {
      if (file.source === "attachment" && file.kind === "image") {
        var img = el("img", "attached");
        img.src = file.url;
        img.alt = file.name;
        img.title = file.path;
        user.appendChild(img);
      }
    });
    turn.appendChild(user);

    // -- what came back
    var running = job.state === "running" || job.state === "queued";
    var assistant = el("div", "bubble assistant" +
      (job.state === "error" ? " is-error" : "") + (running ? " is-running" : ""));

    var activity = job.activity || [];
    if (running) {
      if (activity.length) {
        assistant.appendChild(activityList(activity, 4));
      }
      var pending = el("div", "pending");
      pending.appendChild(el("span", "pulse"));
      pending.appendChild(el("span", null,
        job.state === "queued" ? "waiting for the message before it…"
                               : "working…"));
      var stop = el("button", "btn tiny", "Stop");
      stop.addEventListener("click", function () {
        api("/cancel/" + job.job_id, { method: "POST" }).then(poll);
      });
      pending.appendChild(stop);
      assistant.appendChild(pending);
    } else if (activity.length) {
      var details = el("details", "activity-done");
      details.appendChild(el("summary", null,
        activity.length + (activity.length === 1 ? " step" : " steps")));
      details.appendChild(activityList(activity));
      assistant.appendChild(details);
    }

    if (job.reply) {
      var reply = el("div", "reply");
      reply.innerHTML = fmt.formatReply(job.reply);
      assistant.appendChild(reply);
    }
    if (job.error) {
      var error = el("div", "reply");
      error.appendChild(el("div", "err", job.error));
      assistant.appendChild(error);
    }

    var produced = (job.files || []).filter(function (f) {
      return f.source !== "attachment";
    });
    if (produced.length) { assistant.appendChild(gallery(produced)); }

    if (!running || job.state === "queued") { assistant.appendChild(footer(job)); }
    turn.appendChild(assistant);
    return turn;
  }

  function nearBottom() {
    var thread = $("thread");
    return thread.scrollHeight - thread.scrollTop - thread.clientHeight < 160;
  }

  function scrollDown() {
    var thread = $("thread");
    thread.scrollTop = thread.scrollHeight;
  }

  function upsert(job, options) {
    var stick = (options && options.stick) || nearBottom();
    var previous = state.jobs[job.job_id];
    // A turn that has just finished may have made or opened a part; the rail
    // follows it.  `historical` is the page-load redraw, which must not yank
    // the sheet to whatever was being made an hour ago.
    if (options && options.historical) { follow.seen[job.job_id] = true; }
    else { considerFollowing(job); }
    state.jobs[job.job_id] = job;
    var node = renderTurn(job);
    if (state.nodes[job.job_id]) {
      state.nodes[job.job_id].replaceWith(node);
    } else {
      $("thread").appendChild(node);
      state.order.push(job.job_id);
      $("empty").hidden = true;
    }
    state.nodes[job.job_id] = node;
    if (typeof job.session_cost_usd === "number") { setCost(job.session_cost_usd); }
    if (stick) { scrollDown(); }
    // A turn that just landed is worth a fresh look at the services: it may
    // have started Blender's server, or spent the last of a rate limit.
    if (previous && previous.state !== job.state &&
        job.state !== "running" && job.state !== "queued") {
      refreshHealth();
    }
    return node;
  }

  function unsettled() {
    return state.order.filter(function (id) {
      var job = state.jobs[id];
      return job && (job.state === "running" || job.state === "queued");
    });
  }

  function poll() {
    var ids = unsettled();
    if (!ids.length) {
      if (state.polling) { clearInterval(state.polling); state.polling = null; }
      $("composer-status").textContent = "";
      return Promise.resolve();
    }
    return Promise.all(ids.map(function (id) {
      return api("/job/" + id).then(function (res) {
        if (res.ok && res.data && res.data.job_id) { upsert(res.data); }
      });
    }));
  }

  function startPolling() {
    if (state.polling) { return; }
    state.polling = setInterval(poll, 800);
    poll();
  }

  // ------------------------------------------------------------- load all --
  function loadJobs() {
    return api("/jobs").then(function (res) {
      if (!res.ok) {
        banner("error", res.data.error || "Could not read the conversation.");
        return;
      }
      var jobs = (res.data.jobs || []).slice().sort(function (a, b) {
        return (a.created_at || 0) - (b.created_at || 0);
      });
      jobs.forEach(function (job) {
        upsert(job, { stick: true, historical: true });
      });
      if (typeof res.data.session_cost_usd === "number") {
        setCost(res.data.session_cost_usd);
      }
      if (jobs.length) { $("empty").hidden = true; scrollDown(); }
      if (unsettled().length) { startPolling(); }
    });
  }

  // -------------------------------------------------------------- sending --
  function currentModel() { return $("model").value; }

  function send() {
    var box = $("message");
    var text = box.value.trim();
    if (!text) { return; }

    var payload = { message: text, conversation: "continue", model: currentModel() };
    if (state.attachment) {
      // Exactly the road the panel's attachment takes: a path in the context,
      // which the bridge turns into a "Read this image first" block.
      payload.context = { image_path: state.attachment.path };
    }

    $("send").disabled = true;
    api("/ask", { body: payload }).then(function (res) {
      $("send").disabled = false;
      if (res.status === 409) {
        $("composer-status").className = "composer-status warn";
        $("composer-status").textContent =
          "One message is already waiting. It will go as soon as this turn ends.";
        return;
      }
      if (!res.ok) {
        banner("error", res.data.error || ("The bridge answered " + res.status + "."));
        return;
      }
      box.value = "";
      resize();
      clearAttachment();
      if (res.data.state === "queued") {
        $("composer-status").className = "composer-status warn";
        $("composer-status").textContent = "queued — it goes as soon as this turn ends";
      } else {
        $("composer-status").className = "composer-status";
        $("composer-status").textContent = "";
      }
      // Draw it immediately from what we know, so the message appears the
      // instant it is sent rather than after the first poll comes back.
      upsert({
        job_id: res.data.job_id,
        state: res.data.state,
        message: text,
        created_at: Date.now() / 1000,
        requested_model: currentModel(),
        activity: [],
        files: []
      }, { stick: true });
      startPolling();
    });
  }

  function resize() {
    var box = $("message");
    box.style.height = "auto";
    box.style.height = Math.min(box.scrollHeight, window.innerHeight * 0.4) + "px";
  }

  // ------------------------------------------------------------- uploads --
  function clearAttachment() {
    state.attachment = null;
    $("attachment").hidden = true;
    $("file").value = "";
  }

  function attach(file) {
    if (!file) { return; }
    var status = $("composer-status");
    status.className = "composer-status";
    status.textContent = "uploading " + file.name + "…";

    var reader = new FileReader();
    reader.onerror = function () {
      status.className = "composer-status warn";
      status.textContent = "could not read that file";
    };
    reader.onload = function () {
      // A browser hands JavaScript the file's CONTENT, never its path, so the
      // bytes have to be written down on this machine before the assistant can
      // be told where to look.  /upload does that and hands back the path.
      api("/upload", { body: { name: file.name, data: String(reader.result) } })
        .then(function (res) {
          if (!res.ok) {
            status.className = "composer-status warn";
            status.textContent = res.data.error || "that image was refused";
            $("file").value = "";
            return;
          }
          state.attachment = res.data;
          $("attachment-thumb").src = res.data.url;
          $("attachment-name").textContent = res.data.path;
          $("attachment").hidden = false;
          status.textContent = "";
        });
    };
    reader.readAsDataURL(file);
  }

  // ---------------------------------------------------------------- flows --
  function paramInput(name, spec) {
    var wrap = el("div", "param");
    var label = el("label", null, spec.unit ? name + " (" + spec.unit + ")" : name);
    label.setAttribute("for", "p-" + name);
    var input = el("input");
    input.id = "p-" + name;
    input.dataset.param = name;
    var value = spec.value;
    if (typeof value === "boolean") {
      input.type = "checkbox";
      input.checked = value;
      input.dataset.type = "bool";
    } else if (typeof value === "number") {
      input.type = "number";
      input.step = Number.isInteger(value) ? "1" : "any";
      input.value = String(value);
      input.dataset.type = "number";
    } else {
      input.type = "text";
      input.value = value == null ? "" : (typeof value === "object"
        ? JSON.stringify(value) : String(value));
      input.dataset.type = typeof value === "object" ? "json" : "text";
    }
    wrap.appendChild(label);
    wrap.appendChild(input);
    if (spec.description) { wrap.appendChild(el("div", "hint", spec.description)); }
    return wrap;
  }

  function collectParams(card) {
    var out = {};
    card.querySelectorAll("input[data-param]").forEach(function (input) {
      var name = input.dataset.param;
      if (input.dataset.type === "bool") { out[name] = input.checked; return; }
      if (input.dataset.type === "number") {
        var number = parseFloat(input.value);
        out[name] = isNaN(number) ? input.value : number;
        return;
      }
      if (input.dataset.type === "json") {
        try { out[name] = JSON.parse(input.value); return; }
        catch (e) { out[name] = input.value; return; }
      }
      out[name] = input.value;
    });
    return out;
  }

  function renderFlows(data) {
    var host = $("flows");
    host.textContent = "";
    var flows = (data && data.flows) || [];
    if (!flows.length) {
      host.appendChild(el("p", "muted",
        "No flows saved yet. Finish a multi-step job in the chat and ask the " +
        "assistant to save it as a flow."));
      return;
    }
    flows.forEach(function (flow) {
      var card = el("section", "flow" + (flow.error ? " broken" : ""));
      card.appendChild(el("h3", null, flow.name));
      card.appendChild(el("p", "desc", flow.error || flow.description || ""));
      if (flow.error) { host.appendChild(card); return; }

      if (flow.step_labels && flow.step_labels.length) {
        card.appendChild(el("div", "steps",
          flow.step_labels.join("  →  ")));
      }
      var params = flow.params || {};
      var names = Object.keys(params);
      if (names.length) {
        var box = el("div", "params");
        names.forEach(function (name) { box.appendChild(paramInput(name, params[name] || {})); });
        card.appendChild(box);
      }
      var run = el("button", "btn primary", "Run");
      run.addEventListener("click", function () {
        run.disabled = true;
        run.textContent = "Running…";
        var old = card.querySelector(".flow-result");
        if (old) { old.remove(); }
        api("/flows/run", { body: { name: flow.name, params: collectParams(card) } })
          .then(function (res) {
            run.disabled = false;
            run.textContent = "Run";
            var result = el("div", "flow-result");
            if (!res.ok) {
              result.className = "flow-result bad";
              result.textContent = res.data.error || ("The bridge answered " + res.status + ".");
            } else {
              var report = res.data || {};
              result.appendChild(el("div", null,
                "Done — " + (report.count || 0) + " steps in " +
                ((report.duration_ms || 0) / 1000).toFixed(1) + " s."));
              var list = el("ol");
              (report.steps || []).forEach(function (step) {
                list.appendChild(el("li", null,
                  (step.label || step.op) + " — " + (step.brief || "ok")));
              });
              result.appendChild(list);
            }
            card.appendChild(result);
          });
      });
      card.appendChild(run);
      host.appendChild(card);
    });
  }

  function loadFlows() {
    var host = $("flows");
    host.textContent = "";
    host.appendChild(el("p", "muted", "Reading the flows folder…"));
    return api("/flows", { method: "POST", body: {} }).then(function (res) {
      if (!res.ok) {
        host.textContent = "";
        var box = el("div", "flow broken");
        box.appendChild(el("h3", null, "Flows are not available"));
        box.appendChild(el("p", "desc", res.data.error ||
          ("The bridge answered " + res.status + ".")));
        host.appendChild(box);
        return;
      }
      state.flows = res.data;
      renderFlows(res.data);
      // The same flows, again, as one-click buttons in the row at the top.
      renderFlowButtons(res.data);
    });
  }

  // ------------------------------------------------------------ workbench --
  //
  // The second half of the product.  The chat is where a part is *asked* for;
  // this is where it is edited — the dimensions on one side, the picture and
  // the scene's own contents on the other.  Nothing here goes through the
  // model: every button is a route on the bridge, so changing a number costs
  // nothing and answers in the time a rebuild takes.

  function card(title, cls) {
    var box = el("div", "wb-card" + (cls ? " " + cls : ""));
    if (title) { box.appendChild(el("h4", null, title)); }
    return box;
  }

  function bullets(items) {
    var list = el("ul");
    items.forEach(function (item) { list.appendChild(el("li", null, item)); });
    return list;
  }

  function setStatus(text, cls) {
    var node = $("wb-status");
    node.className = "wb-status" + (cls ? " " + cls : "");
    node.textContent = text || "";
  }

  //: spec.json is the artist's file and Phase 11 is still growing it, so this
  //: reads every shape a component list has been written in rather than one:
  //: a list of names, a list of objects, or a map keyed by name.
  function componentList(value, role) {
    var out = [];
    if (!value) { return out; }
    function push(item, key) {
      if (typeof item === "string") {
        out.push({ name: key || item, role: role || "", description: key ? item : "" });
        return;
      }
      if (!item || typeof item !== "object") { return; }
      out.push({
        name: String(item.name || item.object || key || item.script || "component"),
        role: String(item.kind || item.role || item.type || role || ""),
        description: String(item.description || item.note || "")
      });
    }
    if (Object.prototype.toString.call(value) === "[object Array]") {
      value.forEach(function (item) { push(item, null); });
    } else if (typeof value === "object") {
      Object.keys(value).forEach(function (key) {
        if (key.charAt(0) === "_") { return; }
        push(value[key], key);
      });
    }
    return out;
  }

  function specComponents(spec) {
    var out = componentList(spec.components, "");
    out = out.concat(componentList(spec.core, "core"));
    out = out.concat(componentList(spec.proposals, "proposal"));
    if (spec.assembly) { out = out.concat(componentList(spec.assembly.parts, "")); }
    return out;
  }

  function renderSheet(entry) {
    var host = $("wb-sheet");
    host.textContent = "";
    if (!entry) {
      host.appendChild(el("p", "muted small",
        "Pick a part, or ask for a new one in the conversation."));
      return;
    }
    var spec = entry.spec || {};

    var about = card(entry.name);
    if (spec.description) { about.appendChild(el("p", null, spec.description)); }
    about.appendChild(el("p", "muted small",
      (entry.script || "no script") + "  →  object “" + (entry.object || "?") + "”"));
    host.appendChild(about);

    var features = spec.features || [];
    if (features.length) {
      var box = card("What it is made of");
      box.appendChild(bullets(features.map(String)));
      host.appendChild(box);
    }

    var parts = specComponents(spec);
    if (parts.length) {
      var componentBox = card("Components");
      componentBox.appendChild(bullets(parts.map(function (part) {
        return part.name + (part.role ? " (" + part.role + ")" : "") +
               (part.description ? " — " + part.description : "");
      })));
      componentBox.appendChild(el("p", "muted small",
        "Scrap one you do not want from “In the scene”, or ask the assistant " +
        "to redo just that piece."));
      host.appendChild(componentBox);
    }

    var companions = spec.companion_parts || [];
    if (companions.length) {
      var companionBox = card("Prints separately");
      companionBox.appendChild(bullets(companions.map(function (part) {
        return String(part.name || part.script || "companion") +
               (part.script ? " (" + part.script + ")" : "") +
               (part.description ? " — " + part.description : "");
      })));
      host.appendChild(companionBox);
    }
  }

  // -- one parameter ---------------------------------------------------
  function makeControl(name, spec) {
    spec = spec || {};
    var start = spec.value;
    var unit = String(spec.unit || "");
    var row = el("div", "wb-param");

    var label = el("div", "name");
    label.appendChild(el("span", null, name));
    if (unit) { label.appendChild(el("span", "unit", unit)); }
    row.appendChild(label);

    var baseline = start;
    var control = { row: row };
    var isBool = unit === "bool" || typeof start === "boolean";
    var min = numberOr(spec.min, null);
    var max = numberOr(spec.max, null);
    var step = numberOr(spec.step, null);

    // A debounce, because a slider fires on every pixel and the dirty count
    // does not need to be recomputed sixty times a second.
    var settled = debounce(function () { refreshDirty(); }, 180);

    // Enter in a value box IS Apply.  The whole point of the rail is that an
    // edit is typing-speed: click the number, type 1.5, press Enter, watch it
    // rebuild — never reach for a button between every field.
    function applyOnEnter(input) {
      input.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" || event.shiftKey || event.ctrlKey ||
            event.altKey) { return; }
        event.preventDefault();
        refreshDirty();
        applyParams();
      });
    }

    if (isBool) {
      var box = el("input");
      box.type = "checkbox";
      box.checked = !!start;
      box.id = "wbp-" + name;
      label.setAttribute("for", box.id);
      row.appendChild(box);
      box.addEventListener("change", settled);
      control.get = function () { return box.checked; };
      control.set = function (value) { box.checked = !!value; };
    } else if (typeof start === "number") {
      var number = el("input");
      number.type = "number";
      number.value = String(start);
      number.id = "wbp-" + name;
      label.setAttribute("for", number.id);
      if (min !== null) { number.min = String(min); }
      if (max !== null) { number.max = String(max); }
      number.step = (step !== null && step > 0) ? String(step)
        : (Math.floor(start) === start ? "1" : "any");
      row.appendChild(number);

      var range = null;
      if (min !== null && max !== null && max > min) {
        range = el("input");
        range.type = "range";
        range.min = String(min);
        range.max = String(max);
        range.step = (step !== null && step > 0) ? String(step)
                                                 : String((max - min) / 200);
        range.value = String(start);
        range.title = min + " to " + max + (unit ? " " + unit : "");
        row.appendChild(range);
        range.addEventListener("input", function () {
          number.value = range.value;
          settled();
        });
      }
      number.addEventListener("input", function () {
        if (range) { range.value = number.value; }
        settled();
      });
      applyOnEnter(number);
      control.get = function () { return numberOr(number.value, start); };
      control.set = function (value) {
        number.value = String(value);
        if (range) { range.value = String(value); }
      };
    } else {
      var text = el("input");
      text.type = "text";
      text.id = "wbp-" + name;
      label.setAttribute("for", text.id);
      text.value = start == null ? "" : String(start);
      row.appendChild(text);
      text.addEventListener("input", settled);
      applyOnEnter(text);
      control.get = function () { return text.value; };
      control.set = function (value) { text.value = value == null ? "" : String(value); };
    }

    if (spec.description) { row.appendChild(el("div", "hint", spec.description)); }

    control.changed = function () { return control.get() !== baseline; };
    control.reset = function () { control.set(baseline); };
    //: After a successful rebuild the values on screen ARE the part, so they
    //: become the baseline — otherwise every slider stays orange for ever.
    control.commit = function () { baseline = control.get(); };
    return control;
  }

  function refreshDirty() {
    var changed = [];
    Object.keys(wb.controls).forEach(function (name) {
      var control = wb.controls[name];
      var isChanged = control.changed();
      control.row.classList.toggle("is-changed", isChanged);
      if (isChanged) { changed.push(name); }
    });
    if (changed.length) {
      setStatus(changed.length + (changed.length === 1 ? " value" : " values") +
                " changed — press Apply to rebuild", "warn");
    } else {
      setStatus("");
    }
  }

  function renderParams(params) {
    var host = $("wb-params");
    host.textContent = "";
    wb.controls = {};
    var names = Object.keys(params || {});
    if (!names.length) {
      host.appendChild(el("p", "muted small",
        "This part has no PARAMS block, so there is nothing to slide. Ask the " +
        "assistant to add the dimensions you want to be able to change."));
      $("wb-apply").disabled = true;
      $("wb-reset").disabled = true;
      return;
    }
    names.forEach(function (name) {
      var control = makeControl(name, params[name]);
      wb.controls[name] = control;
      host.appendChild(control.row);
    });
    $("wb-apply").disabled = false;
    $("wb-reset").disabled = false;
    setStatus("");
  }

  function collectOverrides() {
    var out = {};
    Object.keys(wb.controls).forEach(function (name) {
      out[name] = wb.controls[name].get();
    });
    return out;
  }

  // -- the parts on disk ------------------------------------------------
  function findProject(name) {
    var list = (wb.projects && wb.projects.projects) || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].name === name) { return list[i]; }
    }
    return null;
  }

  function paramsProblem(res) {
    var host = $("wb-params");
    host.textContent = "";
    var data = res.data || {};
    // The two failures are told apart on purpose: one is a button to press,
    // the other is a message about the artist's own script.
    var box = card(data.service === false ? "The shape service is not running"
                                          : "Could not read the parameters", "bad");
    box.appendChild(el("p", null,
      data.error || ("The bridge answered " + res.status + ".")));
    host.appendChild(box);
    $("wb-apply").disabled = true;
    $("wb-reset").disabled = true;
    setStatus("");
  }

  function selectProject(name) {
    var entry = findProject(name);
    wb.project = entry;
    wb.controls = {};
    renderSheet(entry);
    try { localStorage.setItem("forge.project", name || ""); } catch (e) { /* private mode */ }
    if (!entry) {
      $("wb-params").textContent = "";
      return Promise.resolve();
    }
    var host = $("wb-params");
    host.textContent = "";
    host.appendChild(el("p", "muted small", "Reading the parameters…"));
    return api("/projects/" + encodeURIComponent(name) + "/schema")
      .then(function (res) {
        if (wb.project !== entry) { return; }   // they picked another one
        if (!res.ok) { paramsProblem(res); return; }
        renderParams(res.data.params || {});
      });
  }

  //: `want` is the part the caller would like selected if it is there — the
  //: one auto-follow just detected, or the one the page was left on.  It only
  //: ever *prefers*; a name that is not on disk falls through to the rest.
  function loadProjects(want) {
    var job = api("/projects").then(function (res) {
      var picker = $("project");
      if (!res.ok) {
        picker.textContent = "";
        $("wb-sheet").textContent = "";
        var box = card("Could not read the projects folder", "bad");
        box.appendChild(el("p", null,
          res.data.error || ("The bridge answered " + res.status + ".")));
        $("wb-sheet").appendChild(box);
        return;
      }
      wb.projects = res.data;
      var list = res.data.projects || [];
      var previous = picker.value;
      picker.textContent = "";
      if (!list.length) {
        var none = el("option", null, "no parts yet");
        none.value = "";
        picker.appendChild(none);
        $("wb-params").textContent = "";
        $("wb-sheet").textContent = "";
        var empty = card("Nothing in projects/ yet");
        empty.appendChild(el("p", null, res.data.note ||
          "Ask the assistant for a part and it appears here."));
        $("wb-sheet").appendChild(empty);
        return;
      }
      list.forEach(function (project) {
        var option = el("option", null,
          project.name + (project.has_params ? "" : "  (no parameters)"));
        option.value = project.name;
        picker.appendChild(option);
      });
      var saved = null;
      try { saved = localStorage.getItem("forge.project"); } catch (e) { saved = null; }
      var wanted = [want, previous, saved, list[0].name].filter(function (name) {
        return name && findProject(name);
      })[0];
      picker.value = wanted;
      return selectProject(wanted);
    });
    // Held so a second caller — the library's "Open in Studio" arriving
    // while the tab is still loading — waits for THIS fetch instead of firing
    // a second one and racing it for the picker.
    wb.loading = job;
    return job;
  }

  function whenProjectsLoaded() {
    if (wb.projects !== null) { return Promise.resolve(); }
    return wb.loading || loadProjects();
  }

  // -- auto-follow: the rail keeps up with the conversation ---------------
  //
  // The rules live in follow.js so they can be tested under node with canned
  // /jobs entries.  This half is the wiring: when a turn finishes and names a
  // part, the sheet switches to it and refetches its schema.  A part the
  // assistant has only just written is not in the picker yet, so a name that
  // does not resolve costs one /projects refetch and no more.

  function followNote(text, pinned) {
    var node = $("follow-note");
    node.className = "muted small" + (pinned ? " is-pinned" : "");
    node.textContent = text;
  }

  function pinProject(name) {
    follow.pinned = name || null;
    followNote(name ? "Pinned to " + name + " until the conversation moves on."
                    : "Following the conversation.", !!name);
  }

  //: Switch the rail to `name`, refetching the projects list once if the part
  //: is new.  A detection always wins over a hand-picked pin: the artist asked
  //: for this part in the very message that produced it.
  function followTo(name) {
    if (!name) { return Promise.resolve(); }
    if (wb.project && wb.project.name === name) {
      follow.pinned = null;
      followNote("Following the conversation.");
      return Promise.resolve();
    }
    function land() {
      if (!findProject(name)) { return Promise.resolve(); }
      $("project").value = name;
      follow.pinned = null;
      followNote("Following the conversation — switched to " + name + ".");
      return selectProject(name);
    }
    if (findProject(name)) { return land(); }
    return loadProjects(name).then(land);
  }

  function considerFollowing(job) {
    if (!job || !job.job_id || follow.seen[job.job_id]) { return; }
    if (job.state !== "done") { return; }
    follow.seen[job.job_id] = true;
    var name = "";
    try {
      name = window.ForgeFollow.projectFromJob(job, wb.projects);
    } catch (e) { name = ""; }
    if (name) { followTo(name); }
  }

  function statsLine(data) {
    var stats = data.stats || {};
    var bits = [];
    if (stats.face_count) { bits.push(stats.face_count + " faces"); }
    var box = stats.bounding_box_mm;
    if (box && box.length === 3) {
      bits.push(box.map(function (v) { return Math.round(v * 10) / 10; }).join(" × ") + " mm");
    }
    if (stats.watertight === false) { bits.push("NOT watertight"); }
    else if (stats.watertight === true) { bits.push("watertight"); }
    return "Rebuilt " + (data.object || "the part") +
           (bits.length ? " — " + bits.join(", ") : "") + ".";
  }

  function applyParams() {
    if (!wb.project) { return Promise.resolve(); }
    var button = $("wb-apply");
    if (button.disabled) { return Promise.resolve(); }   // a held-down Enter
    button.disabled = true;
    setStatus("rebuilding…");
    $("wb-timing").textContent = "";
    var started = Date.now();
    return api("/projects/" + encodeURIComponent(wb.project.name) + "/set_params",
               { body: { overrides: collectOverrides() } })
      .then(function (res) {
        button.disabled = false;
        // The number that makes the case for typing it yourself: this is the
        // same edit the assistant would charge a turn and two minutes for.
        $("wb-timing").textContent =
          ((Date.now() - started) / 1000).toFixed(1) + " s";
        var data = res.data || {};
        if (!res.ok) {
          setStatus(data.error || ("The bridge answered " + res.status + "."), "bad");
          banner("error", data.error || ("The bridge answered " + res.status + "."));
          refreshHealth();
          return;
        }
        Object.keys(wb.controls).forEach(function (name) {
          wb.controls[name].commit();
        });
        refreshDirty();
        setStatus(statsLine(data) + (data.loaded ? "" : " Not loaded into Blender."));
        (data.notes || []).forEach(function (note) { banner("info", note); });
        if (data.loaded) {
          renderPreview(data.object ? [data.object] : null);
          loadScene();
        }
      });
  }

  function resetParams() {
    Object.keys(wb.controls).forEach(function (name) { wb.controls[name].reset(); });
    refreshDirty();
  }

  // -- the picture ------------------------------------------------------
  function placeholder(text) {
    var host = $("wb-preview");
    host.textContent = "";
    host.appendChild(el("div", "placeholder", text));
  }

  function renderPreview(objects) {
    placeholder("rendering…");
    var body = { view: $("wb-view").value };
    if (objects && objects.length) { body.objects = objects; }
    return api("/preview", { body: body }).then(function (res) {
      if (!res.ok) {
        placeholder(res.data.error || ("The bridge answered " + res.status + "."));
        return;
      }
      wb.preview = res.data;
      var host = $("wb-preview");
      host.textContent = "";
      var img = el("img");
      // A fresh path per render, so the browser can never show the previous
      // shape from its cache — which would be the most misleading bug here.
      img.src = res.data.url;
      img.alt = (res.data.objects || []).join(", ") || "the scene";
      img.title = res.data.path;
      host.appendChild(img);
    });
  }

  // -- what Blender is holding ------------------------------------------
  function sceneRow(object, active, unitScale) {
    var row = el("div", "wb-object" + (object.name === active ? " is-active" : ""));
    var who = el("div", "who");
    who.appendChild(el("b", null, object.name));
    var bits = [];
    var size = object.dimensions || [];
    // `dimensions` is in Blender units, and one of those is `unit_scale`
    // metres — which get_scene_info reports for exactly this reason. Forge's
    // convention is 1 BU = 1 mm-of-a-metre, i.e. scale 1.0, but a file the
    // artist made elsewhere may not be, and a size printed in the wrong unit is
    // worse than no size at all.
    var toMillimetres = 1000 * numberOr(unitScale, 1) || 1000;
    if (size.length === 3) {
      bits.push(size.map(function (v) {
        return Math.round(v * toMillimetres * 10) / 10;
      }).join(" × ") + " mm");
    }
    if (object.vertex_count) { bits.push(object.vertex_count + " verts"); }
    if (object.type && object.type !== "MESH") { bits.push(String(object.type).toLowerCase()); }
    who.appendChild(el("span", "dims", bits.join("  ·  ")));
    row.appendChild(who);

    var preview = el("button", "btn tiny", "Preview");
    preview.type = "button";
    preview.title = "Render just this one";
    preview.addEventListener("click", function () { renderPreview([object.name]); });
    row.appendChild(preview);

    var scrap = el("button", "btn tiny", "Scrap");
    scrap.type = "button";
    scrap.title = "Delete it from the scene (Ctrl+Z in Blender puts it back)";
    scrap.addEventListener("click", function () { scrapObject(object.name); });
    row.appendChild(scrap);
    return row;
  }

  function renderScene(data) {
    var host = $("scene");
    host.textContent = "";
    var objects = (data && data.objects) || [];
    if (!objects.length) {
      host.appendChild(el("p", "muted small", "Blender's scene is empty."));
      return;
    }
    objects.forEach(function (object) {
      host.appendChild(sceneRow(object, data.active, data.unit_scale));
    });
  }

  function loadScene() {
    var host = $("scene");
    return api("/scene").then(function (res) {
      host.textContent = "";
      if (!res.ok) {
        // Blender closed is the common case, and the bridge's message is the
        // same sentence the Blender panel uses. One line, one thing to do.
        var box = card(res.data.blender === false ? "Blender is not open"
                                                  : "Could not read the scene", "bad");
        box.appendChild(el("p", null,
          res.data.error || ("The bridge answered " + res.status + ".")));
        host.appendChild(box);
        return;
      }
      wb.scene = res.data;
      renderScene(res.data);
    });
  }

  function scrapObject(name) {
    if (!window.confirm("Scrap “" + name + "”?\n\nIt is deleted from the " +
                        "Blender scene. Ctrl+Z in Blender puts it back.")) {
      return;
    }
    api("/scene/delete", { body: { object: name } }).then(function (res) {
      if (!res.ok) {
        banner("error", res.data.error || ("The bridge answered " + res.status + "."));
        return;
      }
      banner("info", res.data.undo || (name + " is gone from the scene."));
      loadScene();
    });
  }

  //: Which part the rail opens on, before anything has been asked: the one it
  //: was left on, else the most recently modified project — which is what
  //: somebody who closed the page mid-part comes back for.  /library is the
  //: only route that carries an mtime, and this is the one call it costs.
  function initialProject() {
    var saved = null;
    try { saved = localStorage.getItem("forge.project"); } catch (e) { saved = null; }
    if (saved) { return Promise.resolve(saved); }
    return api("/library").then(function (res) {
      if (!res.ok) { return null; }
      var newest = null;
      ((res.data && res.data.projects) || []).forEach(function (project) {
        if (!newest || numberOr(project.mtime, 0) > numberOr(newest.mtime, 0)) {
          newest = project;
        }
      });
      return newest ? newest.name : null;
    });
  }

  function loadStudio() {
    var job = initialProject().then(function (name) {
      return loadProjects(name);
    });
    wb.loading = job;
    loadScene();
    return job;
  }

  function reloadStudio() {
    loadProjects();
    loadScene();
  }

  // -------------------------------------------------------------- library --
  //
  // "A view to see all our 3d models."  The Studio edits ONE part; this is
  // the shelf you look along to find it.  Everything on a card is a folder read
  // — description, dimensions, components, exports — so the whole page still
  // draws with Blender closed and the shape service stopped.  The only thing
  // that needs anything running is the picture, and a missing picture is a
  // placeholder rather than an error.

  function bytes(size) {
    var value = numberOr(size, 0);
    if (value < 1024) { return value + " B"; }
    if (value < 1024 * 1024) { return (value / 1024).toFixed(0) + " KB"; }
    return (value / 1048576).toFixed(value < 10 * 1048576 ? 1 : 0) + " MB";
  }

  function initial(name) {
    return String(name || "?").trim().charAt(0).toUpperCase() || "?";
  }

  //: The picture area of a card: the cached PNG, or the project's initial.  The
  //: mtime rides in the query string because the URL is otherwise stable — and
  //: a browser showing yesterday's shape from its cache is the one bug this
  //: feature must not have.
  function thumb(project) {
    var box = el("div", "lib-thumb");
    if (project.has_thumbnail) {
      var img = el("img");
      img.src = project.thumbnail_url + "?t=" + (project.thumbnail_mtime || 0);
      img.alt = project.name;
      img.loading = "lazy";
      box.appendChild(img);
    } else {
      var mark = el("div", "lib-initial", initial(project.name));
      mark.title = "No picture yet — press Preview with the part in the scene.";
      box.appendChild(mark);
    }
    return box;
  }

  function setThumb(box, url, alt) {
    box.textContent = "";
    var img = el("img");
    img.src = url;
    img.alt = alt || "";
    box.appendChild(img);
  }

  function cardStatus(card, text, cls) {
    var node = card.querySelector(".lib-status");
    if (!node) { return; }
    node.className = "lib-status" + (cls ? " " + cls : "");
    node.textContent = text || "";
  }

  function refreshThumbnail(project, card) {
    var button = card.querySelector(".lib-shoot");
    var box = card.querySelector(".lib-thumb");
    if (button) { button.disabled = true; }
    cardStatus(card, "rendering…");
    return api("/projects/" + encodeURIComponent(project.name) + "/thumbnail",
               { method: "POST", body: {} })
      .then(function (res) {
        if (button) { button.disabled = false; }
        if (!res.ok) {
          // 409 is the interesting one: the part is not in the scene, so there
          // is nothing to photograph. Saying so on the card beats a banner —
          // it is about this part, and it names the button that fixes it.
          cardStatus(card, res.data.error ||
                     ("The bridge answered " + res.status + "."), "bad");
          return;
        }
        setThumb(box, res.data.url, project.name);
        project.has_thumbnail = true;
        project.thumbnail_mtime = res.data.thumbnail_mtime || 0;
        cardStatus(card, "");
      });
  }

  //: Phase 15: does this project have a scene of its own?  A .blend is where a
  //: sculpt, a lighting setup and six placed reference empties live — none of
  //: which fits in a part script — so the card says whether there is one and
  //: how big it got.
  function blendFact(project) {
    return project.has_blend ? "scene file " + bytes(project.blend_size)
                             : "no scene file yet";
  }

  //: "Clicking a model should open the associated blender file."  Three answers
  //: come back and the page acts on `route`, never on the status code: a
  //: project with no .blend is the ordinary state of a new project, not a
  //: fault, and the fallback for it is the thing that DOES exist — its
  //: dimensions — plus one sentence naming the button that makes the other.
  function openProject(project, card, confirmed) {
    var button = card.querySelector(".lib-open");
    if (button) { button.disabled = true; }
    cardStatus(card, confirmed ? "opening…" : "asking Blender…");
    return api("/projects/" + encodeURIComponent(project.name) + "/open",
               { method: "POST", body: { confirm: !!confirmed } })
      .then(function (res) {
        if (button) { button.disabled = false; }
        if (!res.ok) {
          cardStatus(card, res.data.error ||
                     ("The bridge answered " + res.status + "."), "bad");
          return;
        }
        var data = res.data || {};
        if (data.route === "no_blend") {
          cardStatus(card, data.hint || "");
          openInStudio(project.name);
          return;
        }
        if (data.route === "spawned") {
          cardStatus(card, data.note || "Blender is starting…");
          return;
        }
        if (data.needs_confirmation) {
          // The last moment this can be asked: a file load throws the running
          // scene away and Ctrl+Z does not cross one. Blender's own words, so
          // the sentence names the file and counts the objects.
          var lose = data.would_lose ||
                     "The scene open in Blender has unsaved changes.";
          if (!window.confirm("Open “" + project.name + "” in Blender?\n\n" +
                              lose + "\n\nOpening replaces the scene that is " +
                              "there now. Undo does not cross a file load.")) {
            cardStatus(card, "Left as it was.");
            return;
          }
          return openProject(project, card, true);
        }
        project.has_blend = true;
        cardStatus(card, "Opened in Blender.");
      });
  }

  function saveProject(project, card) {
    var button = card.querySelector(".lib-save");
    if (button) { button.disabled = true; }
    cardStatus(card, "saving the scene…");
    return api("/projects/" + encodeURIComponent(project.name) + "/save",
               { method: "POST", body: {} })
      .then(function (res) {
        if (button) { button.disabled = false; }
        if (!res.ok) {
          cardStatus(card, res.data.error ||
                     ("The bridge answered " + res.status + "."), "bad");
          return;
        }
        project.has_blend = true;
        project.blend_size = res.data.blend_size || 0;
        project.blend_mtime = res.data.blend_mtime || 0;
        var fact = card.querySelector(".lib-blend");
        if (fact) { fact.textContent = blendFact(project); }
        // Said every time, because it is the one thing an artist would
        // reasonably be afraid of: a copy went to the project, and their own
        // file still saves where it always did.
        cardStatus(card, "Saved " + bytes(project.blend_size) +
                         " — your own file is untouched.");
      });
  }

  function openInStudio(name) {
    showTab("studio");
    return whenProjectsLoaded().then(function () {
      var picker = $("project");
      picker.value = name;
      if (picker.value !== name) {
        banner("error", "The part sheet does not have a part called “" + name +
                        "”. Press Refresh on it and try again.");
        return;
      }
      // Opened by hand from the Library, so it is pinned: the artist went
      // looking for this one and should not lose it to the next turn.
      pinProject(name);
      return selectProject(name);
    });
  }

  // The settings sheet the design phase materialises: every knob for that kind
  // of work, pre-filled at its default, so the artist edits values instead of
  // having to know which fields exist.
  var TASK_CONFIG_FILE = "task-config.json";

  function taskConfigFile(design) {
    var found = null;
    (design || []).forEach(function (file) {
      if (String(file.file).toLowerCase() === TASK_CONFIG_FILE) { found = file; }
    });
    return found;
  }

  function askAbout(text, caret) {
    showTab("studio");
    var box = $("message");
    box.value = text;
    resize();
    box.focus();
    var at = typeof caret === "number" ? caret : text.length;
    try { box.setSelectionRange(at, at); } catch (err) { /* older browsers */ }
  }

  // READ-ONLY, deliberately.  The bridge puts a design file's NAME, size and
  // path on a card and never its contents, and it has no write route onto
  // projects/<slug>/design/ at all — `save_design_doc` and the task_config
  // tools are the only writers and they live in the MCP server.  So a
  // key/value FORM here would be a form drawn over values this page has not
  // got, with a Save button that has nowhere to POST: two lies instead of one
  // honest sentence.  What the card does instead is name the sheet, say who
  // owns it, and hand the artist the one route that does work end to end — a
  // sentence in the composer that the assistant turns into a `task_config_set`
  // call, which validates the value and writes it where every later tool reads.
  function taskConfigNote(project, file) {
    var box = el("div", "lib-config");
    var head = el("div", "lib-config-head", "Settings sheet");
    head.title = file.path;
    box.appendChild(head);
    box.appendChild(el("p", "lib-config-hint",
      "Every setting for this kind of work, already filled in at its default — "
      + "symmetry, budgets, materials, tolerances. The assistant owns this "
      + "file: tell it to change a value and it changes the sheet, so every "
      + "later turn reads the same number instead of remembering one."));

    var row = el("div", "lib-config-actions");

    var show = el("button", "btn tiny lib-config-show", "Show the settings");
    show.type = "button";
    show.title = "Ask the assistant to read " + file.path + " back to you";
    show.addEventListener("click", function () {
      askAbout("Show me the task config sheet for " + project.name
               + " — every setting, and which ones are off their default.");
    });
    row.appendChild(show);

    var change = el("button", "btn tiny lib-config-edit", "Change a setting");
    change.type = "button";
    change.title = "Writes the sentence for you — finish it and press send";
    change.addEventListener("click", function () {
      askAbout("On " + project.name + "'s task config sheet, set ");
    });
    row.appendChild(change);

    box.appendChild(row);
    return box;
  }

  function libraryCard(project) {
    var card = el("section", "lib-card");
    card.dataset.project = project.name;
    card.appendChild(thumb(project));

    var body = el("div", "lib-body");
    body.appendChild(el("h3", null, project.name));
    body.appendChild(el("p", "lib-desc",
      project.description || (project.script
        ? project.script + "  →  object “" + project.object + "”"
        : (project.design_only
            ? "Designed, not built yet — the sheet is waiting for your sign-off."
            : "No description in spec.json."))));

    var facts = el("div", "lib-facts");
    if (typeof project.param_count === "number") {
      facts.appendChild(el("span", "lib-fact",
        project.param_count + (project.param_count === 1 ? " dimension"
                                                          : " dimensions")));
    } else if (project.has_params) {
      facts.appendChild(el("span", "lib-fact", "has dimensions"));
    } else {
      facts.appendChild(el("span", "lib-fact", "no parameters"));
    }
    var exports = project.exports || [];
    var files = el("span", "lib-fact",
      exports.length ? (exports.length + (exports.length === 1 ? " export"
                                                               : " exports"))
                     : "not exported yet");
    facts.appendChild(files);
    var design = project.design || [];
    if (design.length) {
      // Phase 16: the sheet the artist is being asked to sign off on. Before
      // the count, because on a design-only card it is the whole card.
      var sheet = el("span", "lib-fact lib-design-fact",
        design.length + (design.length === 1 ? " design doc" : " design docs") +
        (project.design_only ? " — not built yet" : ""));
      sheet.title = "Requirements, the concept diagram and the components list, "
                  + "in " + project.path;
      facts.appendChild(sheet);
    }
    var demos = project.demos || [];
    if (demos.length) {
      // Phase 17: a film of the mechanism working. Counted like everything
      // else, and then actually played below.
      var reel = el("span", "lib-fact lib-demo-fact",
        demos.length + (demos.length === 1 ? " demo" : " demos"));
      reel.title = "Mechanism demos rendered into " + project.path;
      facts.appendChild(reel);
    }
    var scene = el("span", "lib-fact lib-blend", blendFact(project));
    scene.title = project.has_blend
      ? project.blend_path + " — Open loads this in Blender"
      : "Press Save scene to project with the part in Blender to make one";
    facts.appendChild(scene);
    body.appendChild(facts);

    var parts = project.components || [];
    if (parts.length) {
      var chips = el("div", "lib-chips");
      parts.slice(0, 8).forEach(function (part) {
        var chip = el("span", "lib-chip" +
          (part.role === "proposal" ? " is-proposal" : ""), part.name);
        chip.title = (part.role ? part.role + " — " : "") +
                     (part.description || part.name);
        chips.appendChild(chip);
      });
      if (parts.length > 8) {
        chips.appendChild(el("span", "lib-chip is-more",
                             "+" + (parts.length - 8)));
      }
      body.appendChild(chips);
    }

    if (design.length) {
      var sheetList = el("ul", "lib-exports lib-design");
      design.slice(0, 4).forEach(function (file) {
        var item = el("li");
        item.appendChild(el("span", "f", file.file));
        item.appendChild(el("span", "s", bytes(file.size)));
        item.title = file.path;
        sheetList.appendChild(item);
      });
      if (design.length > 4) {
        sheetList.appendChild(el("li", "more",
          "… and " + (design.length - 4) + " more in " + project.path));
      }
      body.appendChild(sheetList);
    }

    var config = taskConfigFile(design);
    if (config) { body.appendChild(taskConfigNote(project, config)); }

    // The newest take plays on the card; the ones before it are named under it,
    // because a card that autoplays four films is a card nobody can read.
    if (demos.length) {
      var reelBox = el("div", "lib-demos");
      var newest = demos[0];
      if (newest.url) {
        reelBox.appendChild(demoVideo(newest));
      }
      var caption = el("div", "lib-demo-caption", newest.file);
      caption.title = newest.path;
      reelBox.appendChild(caption);
      if (demos.length > 1) {
        var older = el("ul", "lib-exports lib-demo-list");
        demos.slice(1).forEach(function (file) {
          var row = el("li");
          row.appendChild(el("span", "f", file.file));
          row.appendChild(el("span", "s", bytes(file.size)));
          row.title = file.path;
          older.appendChild(row);
        });
        reelBox.appendChild(older);
      }
      body.appendChild(reelBox);
    }

    if (exports.length) {
      var list = el("ul", "lib-exports");
      exports.slice(0, 4).forEach(function (file) {
        var item = el("li");
        item.appendChild(el("span", "f", file.file));
        item.appendChild(el("span", "s", bytes(file.size)));
        // The path, not a link: these are files on this machine and the
        // browser is on this machine. Copy it into Explorer.
        item.title = file.path;
        list.appendChild(item);
      });
      if (exports.length > 4) {
        list.appendChild(el("li", "more",
          "… and " + (exports.length - 4) + " more in " + project.path));
      }
      body.appendChild(list);
    }

    var actions = el("div", "lib-actions");

    // First, and named for what the artist means by "open": the scene itself.
    // Editing dimensions is the other button, and it says so.
    var openBlend = el("button", "btn tiny lib-open", "Open");
    openBlend.type = "button";
    openBlend.title = project.has_blend
      ? "Open " + project.name + ".blend in Blender"
      : "No scene file yet — this opens its dimensions instead";
    openBlend.addEventListener("click", function () {
      openProject(project, card, false);
    });
    actions.appendChild(openBlend);

    var open = el("button", "btn tiny", "Open in Studio");
    open.type = "button";
    open.title = "Edit its dimensions";
    open.addEventListener("click", function () { openInStudio(project.name); });
    actions.appendChild(open);

    var save = el("button", "btn tiny lib-save", "Save scene");
    save.type = "button";
    save.title = "Save Blender's scene into this project as a copy — your own "
               + "file is not moved";
    save.addEventListener("click", function () { saveProject(project, card); });
    actions.appendChild(save);

    var shoot = el("button", "btn tiny lib-shoot", "Preview");
    shoot.type = "button";
    shoot.title = "Render it as it stands in the Blender scene, and keep the picture";
    shoot.addEventListener("click", function () {
      refreshThumbnail(project, card);
    });
    actions.appendChild(shoot);
    body.appendChild(actions);
    body.appendChild(el("div", "lib-status"));

    card.appendChild(body);
    return card;
  }

  function sceneCard(object, unitScale, owner) {
    var card = el("section", "lib-card is-scene");
    card.dataset.object = object.name;
    var box = el("div", "lib-thumb");
    box.appendChild(el("div", "lib-initial", initial(object.name)));
    card.appendChild(box);

    var body = el("div", "lib-body");
    body.appendChild(el("h3", null, object.name));
    var bits = [];
    var size = object.dimensions || [];
    // Blender units, one of which is `unit_scale` metres — the same conversion
    // the rail's scene rows do, for the same reason: a size printed in the
    // wrong unit is worse than no size at all.
    var toMillimetres = 1000 * numberOr(unitScale, 1) || 1000;
    if (size.length === 3) {
      bits.push(size.map(function (v) {
        return Math.round(v * toMillimetres * 10) / 10;
      }).join(" × ") + " mm");
    }
    if (object.vertex_count) { bits.push(object.vertex_count + " verts"); }
    if (object.type && object.type !== "MESH") {
      bits.push(String(object.type).toLowerCase());
    }
    body.appendChild(el("p", "lib-desc", bits.join("  ·  ") || "in the scene"));

    var actions = el("div", "lib-actions");
    var shoot = el("button", "btn tiny", "Preview");
    shoot.type = "button";
    shoot.addEventListener("click", function () {
      shoot.disabled = true;
      cardStatus(card, "rendering…");
      api("/preview", { body: { objects: [object.name], view: "iso" } })
        .then(function (res) {
          shoot.disabled = false;
          if (!res.ok) {
            cardStatus(card, res.data.error ||
                       ("The bridge answered " + res.status + "."), "bad");
            return;
          }
          setThumb(box, res.data.url, object.name);
          cardStatus(card, "");
        });
    });
    actions.appendChild(shoot);

    if (owner) {
      var open = el("button", "btn tiny", "Open in Studio");
      open.type = "button";
      open.title = "This is " + owner.name + "'s part object";
      open.addEventListener("click", function () { openInStudio(owner.name); });
      actions.appendChild(open);
    }
    body.appendChild(actions);
    body.appendChild(el("div", "lib-status"));
    card.appendChild(body);
    return card;
  }

  // --------------------------------------------------------- the models row --
  //
  // "library is still not showing all my actual 3d models."  It was not: a mesh
  // the picture-to-3D service generated has no folder in projects/, no
  // spec.json and therefore no card, so four .glb files of real work were
  // invisible to the one tab that exists to find work.  This row lists the
  // FILES — generated ones and anything in projects/<name>/models/ — and gives
  // each the two things an artist wants to do with one: put it in Blender, and
  // give it a home.
  //
  // No picture on these cards.  A .glb preview means a 3D viewer, which means
  // fetching a library from a CDN, and this page works offline; a badge, a size
  // and a date say what the file is without pretending to show it.

  function stamp(mtime) {
    var seconds = numberOr(mtime, 0);
    if (!seconds) { return "unknown date"; }
    var when = new Date(seconds * 1000);
    if (isNaN(when.getTime())) { return "unknown date"; }
    var today = new Date();
    var sameDay = when.toDateString() === today.toDateString();
    return sameDay ? when.toLocaleTimeString([], { hour: "numeric",
                                                   minute: "2-digit" })
                   : when.toLocaleDateString();
  }

  //: The projects a model can be filed into — the same names the Studio's
  //: picker offers, read off the library answer this row was drawn from rather
  //: than fetched a second time.
  function projectNames() {
    var projects = (lib.data && lib.data.projects) || [];
    return projects.map(function (project) { return project.name; });
  }

  function modelCard(model) {
    var card = el("section", "lib-card is-model is-clickable");
    card.dataset.model = model.path;

    // "Clicking a model should open the associated blender file."  A card is
    // not a link and not a button, so it has to be told to behave like one:
    // the pointer, a role, a tab stop, and Enter/Space — otherwise this is a
    // feature only a mouse can reach.
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.title = "Open " + model.file + " in Blender — into the scene if "
               + "Blender is running, otherwise Blender starts with it";
    card.addEventListener("click", function (event) {
      // The buttons and the picker inside the card are their own actions.
      if (event.target.closest("button, select, option, a")) { return; }
      openModel(model, card);
    });
    card.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") { return; }
      if (event.target !== card) { return; }
      event.preventDefault();
      openModel(model, card);
    });

    var body = el("div", "lib-body");
    body.appendChild(el("h3", null, model.file));

    var facts = el("div", "lib-facts");
    var badge = el("span", "lib-badge" +
      (model.dir_kind === "project" ? " is-project" : " is-generated"),
      model.dir_kind === "project" ? (model.project || "project") : "generated");
    badge.title = model.dir_kind === "project"
      ? "In projects/" + (model.project || "?") + "/models/"
      : "Written by the picture-to-3D service, in " + model.dir;
    facts.appendChild(badge);
    facts.appendChild(el("span", "lib-fact", bytes(model.size)));
    facts.appendChild(el("span", "lib-fact", stamp(model.mtime)));
    facts.appendChild(el("span", "lib-fact", (model.ext || "").replace(".", "")));
    body.appendChild(facts);

    // The path, not a link: the file is on this machine and so is the browser.
    var where = el("p", "lib-desc lib-path", model.dir);
    where.title = model.path;
    body.appendChild(where);

    var actions = el("div", "lib-actions");

    var bring = el("button", "btn tiny lib-open", "Import into Blender");
    bring.type = "button";
    bring.title = "Bring " + model.file + " into the scene, voxel-repaired on "
                + "the way in";
    bring.addEventListener("click", function () { importModel(model, card); });
    actions.appendChild(bring);
    body.appendChild(actions);

    // Filing is a second row: it needs a destination, and a picker crammed in
    // beside the buttons reads as a filter on them rather than an argument.
    var names = projectNames();
    var filing = el("div", "lib-actions lib-file-row");
    if (!names.length) {
      filing.appendChild(el("span", "lib-fact",
        "No projects yet to file it into."));
    } else {
      var picker = el("select", "lib-file-pick");
      picker.title = "Which project this model belongs to";
      names.forEach(function (name) {
        var option = el("option", null, name);
        option.value = name;
        picker.appendChild(option);
      });
      filing.appendChild(picker);
      var file = el("button", "btn tiny lib-file", "File into project…");
      file.type = "button";
      file.title = "Copy it into projects/<name>/models/ — the original stays "
                 + "where it is";
      file.addEventListener("click", function () {
        fileModel(model, picker.value, card);
      });
      filing.appendChild(file);
    }
    body.appendChild(filing);
    body.appendChild(el("div", "lib-status"));

    card.appendChild(body);
    return card;
  }

  // The card click.  One gesture, and what it means does not depend on whether
  // Blender happens to be running — only *how* does, and the bridge decides
  // that (running: into the scene; closed: a Blender starts with it).  So there
  // is nothing to check here first: asking /health before clicking would be a
  // second answer that could already be stale by the time the click lands.
  function openModel(model, card) {
    // One click at a time, and this guard is not cosmetic: with Blender closed
    // a second click would start a SECOND Blender, and two of them fight over
    // port 9876 — the exact thing the bridge refuses to do to itself.  A card
    // has no button to disable, so the card carries the flag.
    if (card.dataset.opening === "1") { return Promise.resolve(); }
    card.dataset.opening = "1";
    cardStatus(card, "opening in Blender…");
    return api("/models/open", { body: { path: model.path } })
      .then(function (res) {
        delete card.dataset.opening;
        if (!res.ok) {
          // 501 is "there is no Blender on this machine", which is a sentence
          // about their machine rather than about this model.
          cardStatus(card, res.data.error ||
                     ("The bridge answered " + res.status + "."), "bad");
          return;
        }
        if (res.data.route === "spawned") {
          cardStatus(card, res.data.note || "Blender is starting with it.");
          return;
        }
        cardStatus(card, "In the scene as “" + (res.data.object || model.file) +
                   "”" + (res.data.repaired ? ", voxel-repaired." : "."));
      });
  }

  function importModel(model, card) {
    var button = card.querySelector(".lib-open");
    if (button) { button.disabled = true; }
    cardStatus(card, "importing — a repair on a big mesh takes a moment…");
    return api("/models/import", { body: { path: model.path } })
      .then(function (res) {
        if (button) { button.disabled = false; }
        if (!res.ok) {
          // 503 is the common one and it is Blender being closed: one sentence
          // with one button in it, on the card rather than in a banner.
          cardStatus(card, res.data.error ||
                     ("The bridge answered " + res.status + "."), "bad");
          return;
        }
        cardStatus(card, "In the scene as “" + (res.data.object || model.file) +
                   "”" + (res.data.repaired ? ", voxel-repaired." : "."));
      });
  }

  function fileModel(model, project, card) {
    var button = card.querySelector(".lib-file");
    if (!project) { return Promise.resolve(); }
    if (button) { button.disabled = true; }
    cardStatus(card, "copying into " + project + "…");
    return api("/models/file", { body: { path: model.path, project: project } })
      .then(function (res) {
        if (button) { button.disabled = false; }
        if (!res.ok) {
          cardStatus(card, res.data.error ||
                     ("The bridge answered " + res.status + "."), "bad");
          return;
        }
        cardStatus(card, res.data.note || ("Filed into " + project + "."));
        // The row now has a second card for the copy, so redraw it.
        loadLibrary();
      });
  }

  function renderModels(data) {
    var host = $("library-models");
    host.textContent = "";
    var section = (data && data.models) || {};
    var models = section.models || [];
    if (!models.length) {
      var empty = card("No 3D model files yet");
      empty.appendChild(el("p", null, section.note ||
        "Generated meshes appear here, and so does anything you put in " +
        "projects/<name>/models/."));
      host.appendChild(empty);
      return;
    }
    models.forEach(function (model) { host.appendChild(modelCard(model)); });
    if (section.total > models.length) {
      host.appendChild(el("p", "muted small",
        "… and " + (section.total - models.length) + " more on disk."));
    }
  }

  function renderLibrary(data) {
    var host = $("library");
    host.textContent = "";
    var projects = (data && data.projects) || [];
    if (!projects.length) {
      var empty = card("Nothing in projects/ yet");
      empty.appendChild(el("p", null, (data && data.note) ||
        "Ask the assistant for a part and it appears here."));
      host.appendChild(empty);
    } else {
      projects.forEach(function (project) {
        host.appendChild(libraryCard(project));
      });
    }

    // The models the artist actually has, between the parts and the scene: a
    // generated mesh is neither a project folder nor something Blender is
    // holding, and before this row it was on neither.
    renderModels(data);

    var sceneHost = $("library-scene");
    sceneHost.textContent = "";
    var scene = (data && data.scene) || {};
    if (!scene.ok) {
      // Blender closed is the common case, and it is one sentence with one
      // thing to do — the same sentence the rail and the panel use.
      var box = card(scene.blender === false ? "Blender is not open"
                                             : "Could not read the scene", "bad");
      box.appendChild(el("p", null, scene.error ||
        "The scene could not be read just now."));
      sceneHost.appendChild(box);
      return;
    }
    var objects = scene.objects || [];
    if (!objects.length) {
      sceneHost.appendChild(el("p", "muted small", "Blender's scene is empty."));
      return;
    }
    var owners = {};
    projects.forEach(function (project) {
      if (project.object) { owners[project.object] = project; }
    });
    objects.forEach(function (object) {
      sceneHost.appendChild(sceneCard(object, scene.unit_scale,
                                      owners[object.name]));
    });
  }

  function loadLibrary() {
    var host = $("library");
    host.textContent = "";
    host.appendChild(el("p", "muted small", "Reading projects/…"));
    var modelHost = $("library-models");
    modelHost.textContent = "";
    modelHost.appendChild(el("p", "muted small", "Reading your model files…"));
    return api("/library").then(function (res) {
      if (!res.ok) {
        host.textContent = "";
        modelHost.textContent = "";
        var box = card("Could not read the library", "bad");
        box.appendChild(el("p", null, res.data.error ||
          ("The bridge answered " + res.status + ".")));
        host.appendChild(box);
        return;
      }
      lib.data = res.data;
      renderLibrary(res.data);
    });
  }

  // -------------------------------------------------------- flow buttons --
  //
  // Always visible, on every tab.  The three canned ones are chat messages —
  // the assistant already knows how to do these jobs and each one is several
  // tools deep, so the button's whole job is to save the artist typing the
  // same paragraph again.  Saved flows are added beside them and run in
  // Blender directly, with no model in the loop.

  function sendCanned(message) {
    showTab("studio");
    $("message").value = message;
    resize();
    send();
  }

  function runSavedFlow(flow, button) {
    var label = button.textContent;
    button.disabled = true;
    button.textContent = "running…";
    api("/flows/run", { body: { name: flow.name } }).then(function (res) {
      button.disabled = false;
      button.textContent = label;
      if (!res.ok) {
        banner("error", res.data.error || ("The bridge answered " + res.status + "."));
        return;
      }
      var report = res.data || {};
      banner("info", flow.name + " — " + (report.count || 0) + " steps in " +
             ((report.duration_ms || 0) / 1000).toFixed(1) + " s.",
             (report.steps || []).map(function (step) {
               return (step.label || step.op) + " — " + (step.brief || "ok");
             }).join("\n"));
      loadScene();
    });
  }

  function renderFlowButtons(data) {
    var host = $("saved-flows");
    host.textContent = "";
    ((data && data.flows) || []).slice(0, 6).forEach(function (flow) {
      if (flow.error) { return; }
      var button = el("button", "btn tiny saved", flow.name);
      button.type = "button";
      button.title = (flow.description || flow.name) +
                     "\nRuns in Blender with its saved defaults.";
      button.addEventListener("click", function () { runSavedFlow(flow, button); });
      host.appendChild(button);
    });
  }

  // ------------------------------------------------------------ workspace --
  //
  // The artist, having used the first version of this screen: "the workspace
  // is too cluttered, we should utilize the pipeline as a stage we're on and
  // can jump back n forth, for activity see the current activity and have
  // past hidden or expandable, the ui should be simpler."
  //
  // So: ONE stage at a time.  The stepper is a row of small chips that says
  // where the build is and gets you to any other stage in one click; the
  // focus panel below it is that stage in full — what its gate measured, what
  // it produced, what it did, and, when it is red, the decision waiting on
  // somebody.  Ten expanded cards became one.
  //
  // Activity is one line — what is happening NOW — with everything before it
  // behind the same line, one click away.  The renders and the saved versions
  // are two closed drawers with a count on them, so the default screen is:
  // where we are, the stage we are on, the model, one status line, and chat.
  //
  // The rule that has not moved: nothing here is invented.  Every number is
  // off design/build-plan.json, the bridge never writes that file, and a
  // panel with nothing in it says so rather than drawing something.

  var ws = {
    project: null,     // the project every panel is about
    projects: [],      // the picker's contents
    pipeline: null,    // the last /pipeline answer
    versions: null,    // the last /versions answer
    deliverables: null,
    snapshot: null,    // the last /snapshot answer
    viewer: null,      // the ForgeGLB viewer, made once
    focus: null,       // the stage id in focus, once the artist has picked one
    timer: null,       // the activity tick, only while the tab is open
    loading: false
  };

  function wsEmpty(host, title, detail) {
    host.textContent = "";
    var box = el("div", "ws-empty");
    box.appendChild(el("strong", null, title));
    if (detail) { box.appendChild(el("p", "muted small", detail)); }
    host.appendChild(box);
  }

  //: Where the eye should land when a plan is opened, before anybody has
  //: clicked anything: the thing that is stopping the build, else the thing
  //: that happens next, else the last thing that happened.  The same three
  //: answers, in the same order, that an artist asking "where are we?" wants.
  function defaultFocus(data) {
    var stages = (data && data.stages) || [];
    if (!stages.length) { return null; }
    if (data.blocked) { return data.blocked; }
    if (data.next) { return data.next; }
    return stages[stages.length - 1].id;
  }

  function focusedStage(data) {
    var stages = (data && data.stages) || [];
    var wanted = ws.focus;
    var found = null;
    stages.forEach(function (stage) {
      if (stage.id === wanted) { found = stage; }
    });
    if (found) { return found; }
    var fallback = defaultFocus(data);
    stages.forEach(function (stage) {
      if (stage.id === fallback) { found = stage; }
    });
    return found;
  }

  // -- the stepper -------------------------------------------------------
  //
  // One chip per stage, in the plan's own order — which IS the pipeline, and
  // is what "the next stage" and "a skip" are measured against.  Colour is
  // the verdict and nothing else; the number is the position, so "we are on
  // 7 of 10" is readable without counting.  Past stages are clickable and
  // view-only: looking back at what the rig gate measured changes nothing.

  function stepChip(stage, index, total, focus, dirty) {
    var chip = el("button", "ws-step is-" + stage.status);
    chip.type = "button";
    chip.dataset.stage = stage.id;
    chip.setAttribute("role", "tab");
    chip.setAttribute("aria-selected", String(stage.id === focus));
    chip.title = (stage.title || stage.id) + " — " + stage.status.replace("_", " ")
               + " (" + (index + 1) + " of " + total + ")";
    chip.appendChild(el("span", "ws-step-num", String(index + 1)));
    chip.appendChild(el("span", "ws-step-name", stage.id));
    if (dirty) {
      // The model has been hand-edited since this gate was measured, so the
      // numbers on its card are about a model that no longer exists. Saying
      // so is the only honest thing available short of re-running the check.
      chip.classList.add("is-dirty");
      chip.appendChild(el("span", "ws-step-dirty", "●"));
      chip.title += " — edited since it was last measured; re-run its check";
    }
    if (stage.id === focus) { chip.classList.add("is-focus"); }
    chip.addEventListener("click", function () {
      ws.focus = stage.id;
      renderPipeline(ws.pipeline);
    });
    return chip;
  }

  function renderPipeline(data) {
    var stepper = $("ws-stepper");
    var focusHost = $("ws-focus");
    stepper.textContent = "";
    focusHost.textContent = "";
    if (!data) {
      focusHost.appendChild(el("p", "muted small", "Reading the build plan…"));
      return;
    }
    if (!data.has_plan) {
      wsEmpty(focusHost, "No build plan yet", data.note ||
        "Ask the assistant to start one and the stages appear here.");
      return;
    }
    var stages = data.stages || [];
    var focus = (focusedStage(data) || {}).id || null;
    var dirty = data.dirty || {};
    stages.forEach(function (stage, index) {
      stepper.appendChild(stepChip(stage, index, stages.length, focus,
                                   !!dirty[stage.id]));
    });
    var stage = focusedStage(data);
    if (stage) { focusHost.appendChild(stagePanel(stage, data)); }
    // The viewer follows the stepper: the controls beside the model are the
    // ones this stage needs, and no others.
    stageToolsFollow(stage ? stage.id : null);
  }

  // -- the stage in focus ------------------------------------------------

  function numberList(stage, full) {
    var numbers = stage.numbers || [];
    if (!numbers.length) { return null; }
    var list = el("dl", "ws-numbers" + (full ? " is-full" : ""));
    numbers.forEach(function (measured) {
      list.appendChild(el("dt", null, measured.name));
      list.appendChild(el("dd", null, measured.text));
    });
    return list;
  }

  function stagePanel(stage, data) {
    var box = el("div", "ws-stage is-" + stage.status);

    var head = el("div", "ws-stage-head");
    head.appendChild(el("h3", null, stage.title || stage.id));
    head.appendChild(el("span", "ws-verdict is-" + stage.status,
                        stage.status.replace("_", " ")));
    box.appendChild(head);

    if (stage.does) { box.appendChild(el("p", "ws-does", stage.does)); }

    if (stage.gate && stage.gate.length) {
      box.appendChild(el("p", "ws-gate", "Gate: " + stage.gate.join(", ")));
    }

    // The decision lives IN the stage it is about, rather than in a panel of
    // its own: a red gate and what to do about it are one thing.  It goes
    // ABOVE the measurements because a gate's numbers can run to several
    // paragraphs of recorded prose, and the one thing an artist looking at a
    // stopped build needs must not be below them.
    if (stage.red) { box.appendChild(decisionBlock(stage, data)); }

    var numbers = numberList(stage, true);
    if (numbers) {
      box.appendChild(numbers);
    } else {
      box.appendChild(el("p", "muted small",
        stage.green ? "This stage passed with nothing recorded against it."
                    : "Nothing measured on this stage yet."));
    }

    var artifacts = stage.artifacts || [];
    if (artifacts.length) {
      var made = el("details", "ws-sub");
      made.appendChild(el("summary", null,
        artifacts.length + (artifacts.length === 1 ? " file" : " files")));
      var files = el("ul", "ws-paths");
      artifacts.forEach(function (path) {
        var item = el("li", null, path);
        item.title = path;   // a path on this machine, to copy into Explorer
        files.appendChild(item);
      });
      made.appendChild(files);
      if (stage.artifact_count > artifacts.length) {
        made.appendChild(el("p", "muted small",
          "… and " + (stage.artifact_count - artifacts.length) + " more."));
      }
      box.appendChild(made);
    }

    var history = stage.history || [];
    if (history.length) {
      var log = el("details", "ws-sub");
      log.appendChild(el("summary", null, "history"));
      var entries = el("ul", "ws-paths");
      history.slice().reverse().forEach(function (entry) {
        var item = el("li", null, entry.date + "  " + entry.action +
                                  (entry.to ? "  → " + entry.to : ""));
        if (entry.override && (entry.override.who || entry.override.why)) {
          item.appendChild(el("div", "ws-signed",
            "signed by " + (entry.override.who || "?") + " — " +
            (entry.override.why || "no reason recorded")));
        }
        entries.appendChild(item);
      });
      log.appendChild(entries);
      box.appendChild(log);
    }
    return box;
  }

  // -- decisions ---------------------------------------------------------
  //
  // The bridge does NOT write build-plan.json, on purpose: pipeline.py owns
  // that file, it refuses a skip, and it will not take an override without a
  // name and a reason recorded on it.  A second writer on a different port
  // would be a way around all three.  So every button here composes the
  // sentence the assistant needs and sends it down the same /ask the composer
  // uses — the plan is still changed by the tool that knows the rules.

  function decisionButton(label, title, message, cls) {
    var button = el("button", "btn tiny" + (cls ? " " + cls : ""), label);
    button.type = "button";
    button.title = title;
    button.addEventListener("click", function () { wsSend(message, label); });
    return button;
  }

  function overrideForm(stage, project) {
    var form = el("form", "ws-override");
    form.appendChild(el("p", "muted small",
      "An override is signed. The stage is marked overridden, never passed, " +
      "and both stages keep who said so and why — so this needs both."));
    var who = el("input", "input tiny");
    who.type = "text";
    who.placeholder = "who is signing this off";
    who.required = true;
    var why = el("input", "input tiny");
    why.type = "text";
    why.placeholder = "why it is acceptable to go on";
    why.required = true;
    form.appendChild(who);
    form.appendChild(why);
    var row = el("div", "ws-actions");
    var go = el("button", "btn tiny warn", "Sign and continue");
    go.type = "submit";
    row.appendChild(go);
    var cancel = el("button", "btn tiny ghost", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", function () { renderPipeline(ws.pipeline); });
    row.appendChild(cancel);
    form.appendChild(row);
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var name = who.value.trim(), reason = why.value.trim();
      if (!name || !reason) { return; }
      wsSend(
        "Override the blocked " + stage.id + " stage on " + project +
        " and advance the build: pipeline_advance with override=true, who=\"" +
        name + "\", why=\"" + reason + "\". Record it exactly as I said it, " +
        "and tell me what you stepped over.",
        "override");
    });
    return form;
  }

  function decisionBlock(stage, data) {
    var project = (data && data.project) || ws.project || "this project";
    var box = el("div", "ws-decision");
    box.appendChild(el("p", "ws-decision-lead",
      "This gate is red, so the build stops here. The ways on:"));
    var actions = el("div", "ws-actions");
    actions.appendChild(decisionButton(
      "Explain",
      "Ask what these numbers mean and what would fix them",
      "The " + stage.id + " stage of " + project + " is failing its gate (" +
      (stage.gate || []).join(", ") + "). Read the build plan, explain in " +
      "plain words what the measurements mean, and give me the two or three " +
      "concrete options with what each one costs.",
      "primary"));
    actions.appendChild(decisionButton(
      "Try the fix",
      "Ask for the fix ladder to be run and the gate re-measured",
      "Fix the " + stage.id + " stage of " + project + ": work the fix ladder " +
      "in order, re-measure the gate, and pipeline_record the real numbers " +
      "whichever way they come out. Do not record a pass you did not measure."));
    actions.appendChild(decisionButton(
      "Re-measure",
      "Ask for the gate to be measured again without changing anything",
      "Re-measure the " + stage.id + " gate on " + project + " without " +
      "changing anything, and pipeline_record what you actually get."));
    var override = el("button", "btn tiny warn", "Override…");
    override.type = "button";
    override.title = "Go on past a red gate. It is recorded as overridden, "
                   + "never as passed, and it needs a name and a reason.";
    override.addEventListener("click", function () {
      var open = box.querySelector(".ws-override");
      if (open) { open.remove(); return; }
      box.appendChild(overrideForm(stage, project));
    });
    actions.appendChild(override);
    box.appendChild(actions);
    return box;
  }

  // -- what is happening, in one line ------------------------------------
  //
  // "For activity see the current activity and have past hidden or
  // expandable."  The summary line is the running turn's newest step, or the
  // word idle; opening it is how the turns before it are reached.  There is
  // no permanent list.

  function liveJob() {
    for (var i = state.order.length - 1; i >= 0; i--) {
      var job = state.jobs[state.order[i]];
      if (job && (job.state === "running" || job.state === "queued")) {
        return job;
      }
    }
    return null;
  }

  function newestStep(job) {
    var steps = (job && job.activity) || [];
    for (var i = steps.length - 1; i >= 0; i--) {
      if (steps[i] && steps[i].label) { return steps[i].label; }
    }
    return "";
  }

  function historyRow(job) {
    var row = el("div", "ws-past is-" + (job.state || "unknown"));
    var head = el("div", "ws-past-head");
    head.appendChild(el("span", "ws-past-state", job.state || "?"));
    head.appendChild(el("span", "ws-past-when", stamp(job.created_at)));
    if (typeof job.cost_usd === "number" && job.cost_usd > 0) {
      head.appendChild(el("span", "ws-past-cost", "$" + job.cost_usd.toFixed(2)));
    }
    row.appendChild(head);
    row.appendChild(el("div", null, String(job.message || "").slice(0, 160)));
    var steps = (job.activity || []).slice(-4);
    if (steps.length) {
      var list = el("ul", "ws-paths");
      steps.forEach(function (step) {
        list.appendChild(el("li", null, step.label || step.kind || ""));
      });
      row.appendChild(list);
    }
    if (job.error) {
      row.appendChild(el("div", "ws-past-error", String(job.error).slice(0, 240)));
    }
    return row;
  }

  function renderActivity() {
    var line = $("ws-now");
    if (!line) { return; }
    var box = $("ws-now-box");
    var job = liveJob();
    if (job) {
      var step = newestStep(job);
      line.className = "ws-now-line is-live";
      line.textContent = job.state === "queued"
        ? "queued — it goes as soon as this turn ends"
        : (step || "working…");
      box.classList.add("is-live");
      // A turn that is running is the one thing on this screen that can be
      // stopped, so the button lives on the line rather than in the drawer.
      if (!box.querySelector(".ws-stop")) {
        var stop = el("button", "btn tiny ghost ws-stop", "Stop");
        stop.type = "button";
        stop.addEventListener("click", function (event) {
          event.preventDefault();
          stop.disabled = true;
          api("/cancel/" + job.job_id, { method: "POST", body: {} })
            .then(renderActivity);
        });
        box.querySelector("summary").appendChild(stop);
      }
    } else {
      line.className = "ws-now-line";
      line.textContent = "idle";
      box.classList.remove("is-live");
      var old = box.querySelector(".ws-stop");
      if (old) { old.remove(); }
    }

    // The drawer is only redrawn while it is open: a list nobody is looking
    // at does not need rebuilding once a second.
    if (!box.open) { return; }
    var host = $("ws-history");
    host.textContent = "";
    var jobs = state.order.map(function (id) { return state.jobs[id]; })
      .filter(Boolean);
    if (!jobs.length) {
      host.appendChild(el("p", "muted small",
        "Nothing has run yet. Ask for something below, or press a button "
        + "above, and the steps appear here."));
      return;
    }
    jobs.slice(-8).reverse().forEach(function (past) {
      host.appendChild(historyRow(past));
    });
  }

  // -- the drawers: renders, and the saved versions ----------------------

  function deliverableCard(file) {
    var card = el("figure", "ws-deliverable is-" + file.kind);
    if (file.url && file.kind === "image") {
      var img = el("img");
      img.src = file.url;
      img.alt = file.file;
      img.loading = "lazy";
      card.appendChild(img);
    } else if (file.url && file.kind === "video") {
      var video = document.createElement("video");
      video.src = file.url;
      video.controls = true;
      video.loop = true;
      video.muted = true;
      video.preload = "metadata";
      card.appendChild(video);
    } else {
      card.appendChild(el("div", "ws-deliverable-none", initial(file.file)));
    }
    var caption = el("figcaption");
    caption.appendChild(el("span", "f", file.file));
    caption.appendChild(el("span", "s", stamp(file.mtime)));
    caption.title = file.path + "  ·  " + bytes(file.size);
    card.appendChild(caption);
    if (file.url) {
      var open = el("a", "ws-deliverable-open", "Open");
      open.href = file.url;
      open.target = "_blank";
      open.rel = "noopener noreferrer";
      card.appendChild(open);
    }
    return card;
  }

  function renderDeliverables(data) {
    var host = $("ws-deliverables");
    var count = $("ws-renders-count");
    host.textContent = "";
    if (!data) { count.textContent = ""; return; }
    var files = data.files || [];
    count.textContent = files.length ? String(data.total) : "none";
    if (!files.length) {
      host.appendChild(el("p", "muted small", data.note ||
        "Renders, turntables and mechanism demos land in this project's "
        + "renders/ folder and show up here."));
      return;
    }
    files.forEach(function (file) { host.appendChild(deliverableCard(file)); });
    if (data.total > files.length) {
      host.appendChild(el("p", "muted small",
        "… and " + (data.total - files.length) + " more in " + data.dir));
    }
  }

  function versionRow(chain, entry, project) {
    var row = el("div", "ws-version" + (entry.current ? " is-current" : ""));

    var picture = el("div", "ws-version-thumb");
    if (entry.thumbnail_url) {
      var img = el("img");
      img.src = entry.thumbnail_url;
      img.alt = entry.file;
      img.loading = "lazy";
      img.title = entry.thumbnail;
      picture.appendChild(img);
    } else {
      picture.appendChild(el("span", "ws-version-number", "v" + entry.version));
    }
    row.appendChild(picture);

    var body = el("div", "ws-version-body");
    var head = el("div", "ws-version-head");
    head.appendChild(el("span", "ws-version-tag", "v" + entry.version));
    head.appendChild(el("span", "ws-version-file", entry.file));
    if (entry.current) {
      head.appendChild(el("span", "ws-version-current", "current"));
    }
    body.appendChild(head);
    var facts = el("div", "ws-version-facts");
    facts.appendChild(el("span", null, stamp(entry.mtime)));
    facts.appendChild(el("span", null, bytes(entry.size)));
    facts.title = entry.path;
    body.appendChild(facts);

    if (!entry.current) {
      var restore = el("button", "btn tiny", "Restore as new version");
      restore.type = "button";
      restore.title = "Copy " + entry.file + " to the end of the chain as v"
                    + (chain.latest + 1) + ". Nothing is overwritten.";
      restore.addEventListener("click", function () {
        var wanted = chain.latest + 1;
        // A confirm step, even though this destroys nothing: it adds a file
        // to the artist's project, and a button that writes into projects/ on
        // one click is a button that gets pressed by accident.
        if (!window.confirm(
              "Copy " + entry.file + " to " + chain.stem + "-" + wanted +
              ".blend?\n\nNothing is overwritten and nothing is deleted — " +
              entry.file + " stays exactly where it is.")) {
          return;
        }
        restore.disabled = true;
        restore.textContent = "copying…";
        api("/projects/" + encodeURIComponent(project) + "/versions/restore",
            { body: { file: entry.file } }).then(function (res) {
          if (!res.ok) {
            restore.disabled = false;
            restore.textContent = "Restore as new version";
            banner("error", res.data.error ||
                   ("The bridge answered " + res.status + "."));
            return;
          }
          banner("info", res.data.note || ("Restored as " + res.data.file));
          loadVersions();
        });
      });
      body.appendChild(restore);
    }
    row.appendChild(body);
    return row;
  }

  function renderVersions(data) {
    var host = $("ws-versions");
    var count = $("ws-saves-count");
    host.textContent = "";
    if (!data) { count.textContent = ""; return; }
    var chains = data.chains || [];
    count.textContent = data.count ? String(data.count) : "none";
    if (!chains.length) {
      host.appendChild(el("p", "muted small", data.note ||
        "Numbered .blend saves in this project's models/ folder appear here, "
        + "newest first. Restoring one copies it forward; nothing is "
        + "overwritten."));
      return;
    }
    var project = data.project || ws.project;
    chains.forEach(function (chain) {
      var group = el("div", "ws-chain");
      group.appendChild(el("div", "ws-chain-head", chain.stem));
      chain.versions.slice().reverse().forEach(function (entry) {
        group.appendChild(versionRow(chain, entry, project));
      });
      if (chain.trimmed) {
        group.appendChild(el("p", "muted small",
          "… and " + chain.trimmed + " older saves in " + data.dir));
      }
      host.appendChild(group);
    });
  }

  // -- the live model view -----------------------------------------------

  //: Looked up through a function so a page served without glbview.js draws
  //: an honest empty state instead of throwing init() away on a ReferenceError.
  function forgeGLB() {
    return (typeof window.ForgeGLB === "object") ? window.ForgeGLB : null;
  }

  function wsViewer() {
    if (ws.viewer) { return ws.viewer; }
    if (!forgeGLB()) { return null; }
    try {
      ws.viewer = forgeGLB().create($("ws-canvas"));
    } catch (err) {
      ws.viewer = { supported: false, error: String(err) };
    }
    return ws.viewer;
  }

  function viewerNote(text, cls) {
    var node = $("ws-viewer-note");
    node.className = "ws-viewer-note" + (cls ? " " + cls : "");
    node.textContent = text;
  }

  function wsSnapshot(weightBone) {
    if (!ws.project) { return Promise.resolve(); }
    var button = $("ws-snapshot");
    var viewer = wsViewer();
    if (!viewer || !viewer.supported) {
      viewerNote((viewer && viewer.error) ||
        "The model view needs WebGL, which this browser did not give us.", "bad");
      return Promise.resolve();
    }
    button.disabled = true;
    viewerNote("Asking Blender for the scene…");
    // A bone here asks Blender to bake that bone's weights into vertex
    // colours before it exports, which is the only way a browser can be
    // shown a vertex group at all.  Only ever a string: this function is one
    // `addEventListener` slip away from being handed a click Event, and an
    // Event serialises into the body as `{"isTrusted": false}`.
    var body = {};
    if (typeof weightBone === "string" && weightBone) {
      body.weight_bone = weightBone;
    }
    return api("/projects/" + encodeURIComponent(ws.project) + "/snapshot",
               { body: body }).then(function (res) {
      button.disabled = false;
      if (!res.ok) {
        viewerNote(res.data.error ||
                   ("The bridge answered " + res.status + "."), "bad");
        return;
      }
      ws.snapshot = res.data;
      if (!res.data.url) {
        viewerNote("Blender exported the scene but the bridge could not mint "
                   + "a link for it.", "bad");
        return;
      }
      viewerNote("Loading " + bytes(res.data.size) + "…");
      return fetch(res.data.url).then(function (response) {
        return response.arrayBuffer();
      }).then(function (buffer) {
        var info;
        try {
          info = viewer.show(buffer);
        } catch (err) {
          viewerNote("That snapshot could not be read: " + err, "bad");
          return;
        }
        $("ws-viewer-empty").hidden = true;
        var play = $("ws-play");
        play.disabled = info.animations.length === 0;
        play.textContent = "Play";
        // A snapshot with a skeleton in it is one whose joints can be placed;
        // one without is honestly nothing to nudge.
        $("ws-nudge").disabled = info.joints === 0;
        viewer.onNudge(renderNudge);
        renderNudge(viewer.readout());
        // The stage's own controls need a model to work on, so they appear
        // once there is one — a panel of buttons over an empty viewport is
        // four ways to get an error message.
        renderStageTools();
        var bits = [info.triangles.toLocaleString() + " tris"];
        if (info.skinned) { bits.push(info.skinned + " skinned"); }
        if (info.joints) { bits.push(info.joints + " joints"); }
        if (info.painted && res.data.weight_bone) {
          bits.push("weights: " + res.data.weight_bone);
        }
        if (info.animations.length) {
          bits.push(info.animations.length + " clip" +
                    (info.animations.length === 1 ? "" : "s"));
        }
        bits.push(stamp(res.data.mtime));
        viewerNote(bits.join("  ·  "));
      });
    });
  }

  // -- joint nudge -------------------------------------------------------
  //
  // "If I am unfamiliar with this it's difficult, we need a simplified
  // version in forge to edit, I don't know how much 10mm is here."
  //
  // The last clause is the feature.  Nothing here tries to teach anybody what
  // a millimetre is; it puts the number NEXT TO the thing it applies to — the
  // handle moves against the model, a grey ghost stays where it started, and
  // the readout says both the millimetres and what share of this character's
  // height that is.  One axis at a time, because "up a bit" is the request and
  // a one-axis drag cannot go sideways by accident.

  function nudgeScaleText(read) {
    if (!read.moved || !(read.height_mm > 0)) { return ""; }
    var height = (read.height_mm / 1000).toFixed(2) + " m tall";
    if (read.share <= 0) { return height; }
    if (read.share < 0.01) {
      return "1/" + Math.round(1 / read.share) + " of him  ·  " + height;
    }
    return (read.share * 100).toFixed(1) + "% of him  ·  " + height;
  }

  function renderNudge(read) {
    var name = $("ws-nudge-name");
    var bone = $("ws-nudge-bone");
    var apply = $("ws-nudge-apply");
    if (!read || !read.selected) {
      name.textContent = "Click a handle to pick a joint.";
      bone.textContent = "";
      $("ws-nudge-mm").textContent = "0.0 mm";
      $("ws-nudge-scale").textContent = "";
      apply.disabled = true;
      return;
    }
    // The plain name leads, and the real bone name sits beside it — a
    // friendly label that hid which bone is about to move would be worse
    // than no label at all.
    name.textContent = read.label || read.bone;
    bone.textContent = read.bone + "  ·  " + read.end;
    // The total distance from where it started, and nothing about which way:
    // a drag can run along two axes one after the other, and "186 mm side to
    // side" when 124 mm of it was vertical would be a lie. Which way is what
    // the line in the viewport is for.
    $("ws-nudge-mm").textContent = read.mm.toFixed(1) + " mm";
    $("ws-nudge-scale").textContent = nudgeScaleText(read);
    apply.disabled = !read.moved;
  }

  function nudgeStatus(text, cls) {
    var node = $("ws-nudge-status");
    node.className = "ws-nudge-status" + (cls ? " " + cls : "");
    node.textContent = text || "";
  }

  function wsToggleNudge() {
    var viewer = ws.viewer;
    if (!viewer || !viewer.supported || !ws.snapshot) { return; }
    var on = !viewer.nudging();
    var joints = viewer.nudge(on);
    $("ws-nudge-bar").hidden = !on;
    $("ws-nudge").classList.toggle("is-on", on);
    renderNudge(viewer.readout());
    if (!on) { nudgeStatus(""); return; }
    if (!joints) {
      nudgeStatus("This snapshot has no skeleton in it, so there are no "
                  + "joints to place. Refresh with the rig in the scene.",
                  "bad");
      return;
    }
    nudgeStatus(joints + " joints. Drag a handle; the grey one stays where it "
                + "started so you can see how far you have moved it.");
  }

  function wsSetAxis(which) {
    var viewer = ws.viewer;
    if (!viewer || !viewer.supported) { return; }
    viewer.axis(which);
    ["up", "forward", "side"].forEach(function (key) {
      $("ws-axis-" + key).classList.toggle("is-on", key === which);
    });
    renderNudge(viewer.readout());
  }

  function wsCancelNudge() {
    var viewer = ws.viewer;
    if (!viewer || !viewer.supported) { return; }
    viewer.cancelNudge();
    renderNudge(viewer.readout());
    nudgeStatus("Put back.");
  }

  function wsApplyNudge() {
    var viewer = ws.viewer;
    if (!viewer || !viewer.supported || !ws.project) { return; }
    var move = viewer.commitNudge();
    if (!move) { return; }
    var body = { bone: move.bone, end: move.end, delta_mm: move.delta_mm };
    var wanted = $("ws-nudge-mirror").value;
    // Left alone, the bridge uses the project's own symmetry setting — which
    // is the right default and the one the artist already chose once.
    if (wanted === "1") { body.mirror = true; }
    if (wanted === "0") { body.mirror = false; }

    var apply = $("ws-nudge-apply");
    apply.disabled = true;
    nudgeStatus("Moving " + (move.label || move.bone) + " in Blender…");
    api("/projects/" + encodeURIComponent(ws.project) + "/joint_move",
        { body: body }).then(function (res) {
      if (!res.ok) {
        apply.disabled = false;
        nudgeStatus(res.data.error ||
                    ("The bridge answered " + res.status + "."), "bad");
        return;
      }
      var said = [];
      said.push("Moved " + move.mm.toFixed(1) + " mm");
      if (res.data.mirror && res.data.mirror_bone) {
        said.push("and " + res.data.mirror_bone + " with it");
      }
      var also = (res.data.moved || []).filter(function (entry) {
        return entry.why && entry.why.indexOf("connected") === 0;
      });
      if (also.length) {
        said.push("(" + also.length + " connected "
                  + (also.length === 1 ? "bone" : "bones") + " followed)");
      }
      nudgeStatus(said.join(" ") + ". " + (res.data.note || ""));
      if (res.data.journal_error) {
        banner("error", res.data.journal_error);
      }
      // What is on screen must be what Blender did, not what was asked for —
      // so the handles come back from a fresh export rather than from the
      // drag that produced them.
      wsSnapshot().then(function () {
        if (viewer.nudging()) {
          $("ws-nudge-bar").hidden = false;
          viewer.nudge(true);
          renderNudge(viewer.readout());
        }
      });
    });
  }

  function wsTogglePlay() {
    var viewer = ws.viewer;
    if (!viewer || !viewer.supported) { return; }
    var button = $("ws-play");
    if (viewer.playing()) {
      viewer.stop();
      button.textContent = "Play";
      return;
    }
    if (viewer.play(0)) { button.textContent = "Pause"; }
  }

  // -- chat, docked ------------------------------------------------------
  //
  // The same /ask the composer uses, through the same code path, so a
  // sentence sent from a decision button is a turn like any other — it lands
  // in the Studio thread, it is billed on the same session, and it shows up
  // on the status line above without any second mechanism.

  function wsSend(text, label) {
    var message = String(text || "").trim();
    if (!message) { return; }
    $("message").value = message;
    resize();
    send();
    var node = $("ws-dock-status");
    node.textContent = label ? ("sent — " + label) : "sent";
    $("ws-message").value = "";
    renderActivity();
  }

  // -- the stage as something to work on ---------------------------------
  //
  // "When we click verify mesh I can click out problem issues and tell it to
  // fix, or in rig it auto selects and shows the rig in the window so I can
  // make small edits, and same for skin, or animate - click on a specific
  // animation and change it."
  //
  // And, on the first draft of that: "i see you say a visible chat turn but
  // I'd also like the option to make changes in a simple version, blender is
  // often overwhelming for people so narrowing the scope could help here."
  //
  // So there are two tiers and one rule for which is which.  If the machine
  // knows exactly what to do — a bounded command over numbers — it is a
  // BUTTON, and pressing it changes the model now, with no model in the loop.
  // If the answer needs judgement — which of three fixes, how to split a tag —
  // it composes a sentence and the assistant works it. Every card says which
  // it is offering, and a card with no deterministic fix offers only the ask.

  var tools = {
    stage: null,       // which stage's controls are showing
    findings: [],      // the last /inspect answer for it
    ran: "",           // when that check ran
    busy: false,
    bone: "",          // skin: which bone's weights are painted
    radius: 40,        // skin: brush radius in millimetres
    op: "smooth",      // skin: which repair the brush does
    point: null,       // skin: where the last click landed, in Blender metres
    action: "",        // animate: which clip
    kind: "",          // animate: which authoring table
    values: {},        // animate: the numbers the sliders hold
    table: null,       // the authoring tables, fetched once
    open: null         // which finding's card is open
  };

  //: Which stages have controls at all.  A stage with no check and no
  //: authoring tool gets no strip rather than an empty one.
  var TOOL_STAGES = {
    verify_mesh: "Mesh",
    rig: "Rig",
    skin: "Skin",
    animate: "Animation"
  };

  function toolsNote(text, cls) {
    var node = $("ws-tools-note");
    node.className = "ws-tools-note" + (cls ? " " + cls : "");
    node.textContent = text || "";
  }

  function severityWord(severity) {
    return { fail: "failing", attention: "needs attention", ok: "passing",
             unknown: "not measured", none: "not applicable" }[severity]
           || severity;
  }

  // -- the findings, as cards --------------------------------------------

  function numbersGrid(numbers) {
    var names = Object.keys(numbers || {});
    if (!names.length) { return null; }
    var list = el("dl", "ws-numbers");
    names.forEach(function (name) {
      list.appendChild(el("dt", null, name));
      list.appendChild(el("dd", null, String(numbers[name])));
    });
    return list;
  }

  function findingCard(finding) {
    var card = el("div", "ws-finding is-" + finding.severity);
    card.dataset.finding = finding.id;
    var head = el("div", "ws-finding-head");
    head.appendChild(el("span", "ws-dot is-" + finding.severity, ""));
    head.appendChild(el("strong", null, finding.label || finding.gate));
    head.appendChild(el("span", "ws-finding-gate",
                        finding.gate + "  ·  " + severityWord(finding.severity)));
    card.appendChild(head);

    var grid = numbersGrid(finding.numbers);
    if (grid) { card.appendChild(grid); }
    if (finding.fix_hint) {
      card.appendChild(el("p", "ws-finding-hint", finding.fix_hint));
    }

    var row = el("div", "ws-actions");
    if (finding.fix && finding.fix.op) {
      // DIRECT: the one kind of repair that is a number in and a count out.
      var apply = el("button", "btn tiny primary", "Apply fix");
      apply.type = "button";
      apply.title = "Runs it now. No chat, no waiting.";
      apply.addEventListener("click", function () {
        applyMeshFix(finding, apply);
      });
      row.appendChild(apply);
    }
    var ask = el("button", "btn tiny", finding.fix ? "Ask instead" : "Ask to fix this");
    ask.type = "button";
    ask.title = "Sends this finding to the assistant, which works the fix ladder";
    ask.addEventListener("click", function () {
      var numbers = Object.keys(finding.numbers || {}).map(function (name) {
        return name + "=" + finding.numbers[name];
      }).join(", ");
      wsSend(
        "On " + (ws.project || "this project") + ", the " + finding.stage +
        " stage's " + finding.gate + " gate reports: " +
        (finding.label || finding.gate) +
        (numbers ? " (" + numbers + ")" : "") +
        ". Work the fix ladder for it, re-measure that gate, and pipeline_record " +
        "the real numbers whichever way they come out.",
        finding.gate);
    });
    row.appendChild(ask);
    if (finding.bone) {
      var show = el("button", "btn tiny ghost", "Show me");
      show.type = "button";
      show.addEventListener("click", function () { focusFinding(finding); });
      row.appendChild(show);
    }
    card.appendChild(row);
    return card;
  }

  function focusFinding(finding) {
    var viewer = ws.viewer;
    tools.open = finding.id;
    if (viewer && viewer.supported && finding.bone) {
      // "In rig it auto selects" — clicking a red joint arms it for nudging.
      if (tools.stage === "rig") {
        if (!viewer.nudging()) { wsToggleNudge(); }
        viewer.pinToHandle(finding.bone);
      }
      if (tools.stage === "skin") {
        tools.bone = finding.bone;
        renderStageTools();
        return;
      }
    }
    renderFindings();
  }

  function renderFindings() {
    var host = $("ws-findings");
    host.textContent = "";
    if (!tools.findings.length) {
      if (tools.ran) {
        host.appendChild(el("p", "muted small",
          "Nothing to report — every gate this check measures came back clean."));
      }
      return;
    }
    tools.findings.forEach(function (finding) {
      var card = findingCard(finding);
      if (finding.id === tools.open) { card.classList.add("is-open"); }
      host.appendChild(card);
    });
  }

  function applyMeshFix(finding, button) {
    if (!ws.project) { return; }
    var body = { finding_id: finding.id, op: finding.fix.op };
    Object.keys(finding.fix).forEach(function (key) {
      if (key !== "op") { body[key] = finding.fix[key]; }
    });
    button.disabled = true;
    toolsNote("Applying…");
    api("/projects/" + encodeURIComponent(ws.project) + "/mesh_fix",
        { body: body }).then(function (res) {
      button.disabled = false;
      if (!res.ok) {
        toolsNote(res.data.error ||
                  ("The bridge answered " + res.status + "."), "bad");
        return;
      }
      var report = res.data.report || {};
      toolsNote("Done" + (report.removed !== undefined
                          ? " — " + report.removed + " vertices merged" : "") +
                ". " + (res.data.note || ""));
      wsSnapshot().then(loadPipeline);
    });
  }

  // -- running a check ---------------------------------------------------

  function wsInspect() {
    if (!ws.project || !tools.stage || tools.busy) { return; }
    var body = { stage: tools.stage };
    if (tools.stage === "animate") {
      if (!tools.action) {
        toolsNote("Pick a clip first.", "bad");
        return;
      }
      body.action = tools.action;
    }
    tools.busy = true;
    $("ws-inspect").disabled = true;
    toolsNote("Measuring in Blender — this is a fresh check, not a cached one…");
    api("/projects/" + encodeURIComponent(ws.project) + "/inspect",
        { body: body }).then(function (res) {
      tools.busy = false;
      $("ws-inspect").disabled = false;
      if (!res.ok) {
        toolsNote(res.data.error ||
                  ("The bridge answered " + res.status + "."), "bad");
        return;
      }
      tools.findings = res.data.findings || [];
      tools.ran = res.data.ran_at || "";
      var counts = res.data.summary || {};
      var bits = [];
      ["fail", "attention", "ok", "unknown"].forEach(function (key) {
        if (counts[key]) { bits.push(counts[key] + " " + severityWord(key)); }
      });
      toolsNote(bits.length ? bits.join(", ") : "nothing to report");
      var viewer = ws.viewer;
      if (viewer && viewer.supported) {
        var placed = viewer.showPins(tools.findings);
        viewer.onPin(function (pin) {
          tools.open = pin.id;
          renderFindings();
          var card = document.querySelector('[data-finding="' +
                                            pin.id.replace(/"/g, '') + '"]');
          if (card) { card.scrollIntoView({ block: "nearest" }); }
        });
        if (placed) {
          toolsNote(toolsNoteText(bits, placed));
        }
      }
      renderFindings();
    });
  }

  function toolsNoteText(bits, placed) {
    return (bits.length ? bits.join(", ") : "nothing to report") +
           "  ·  " + placed + " on the model";
  }

  // -- skin: the brush ----------------------------------------------------

  function brushAt(point) {
    if (!ws.project || !point) { return; }
    var body = { world_pos: point, radius_mm: tools.radius, op: tools.op };
    if (tools.bone) { body.bone = tools.bone; }
    toolsNote("Smoothing…");
    api("/projects/" + encodeURIComponent(ws.project) + "/weights_local",
        { body: body }).then(function (res) {
      if (!res.ok) {
        toolsNote(res.data.error ||
                  ("The bridge answered " + res.status + "."), "bad");
        return;
      }
      toolsNote(res.data.op + " — " + res.data.changed + " vertices. " +
                (res.data.note || ""));
      wsSnapshot(tools.bone).then(loadPipeline);
    });
  }

  // -- animate: the clip, the scrubber, the numbers -----------------------

  function loadAuthoring() {
    if (tools.table) { return Promise.resolve(tools.table); }
    return api("/authoring").then(function (res) {
      tools.table = (res.ok && res.data) || null;
      return tools.table;
    });
  }

  function paramRow(spec) {
    var row = el("label", "ws-param");
    row.appendChild(el("span", "ws-param-name", spec.name.replace(/_/g, " ")));
    var input;
    if (spec.kind === "bool") {
      input = el("input");
      input.type = "checkbox";
      input.checked = tools.values[spec.name] !== undefined
        ? !!tools.values[spec.name] : !!spec.default;
      input.addEventListener("change", function () {
        tools.values[spec.name] = input.checked;
      });
    } else if (spec.kind === "choice") {
      input = el("select", "select tiny");
      (spec.choices || []).forEach(function (choice) {
        var option = el("option", null, choice);
        option.value = choice;
        input.appendChild(option);
      });
      input.value = tools.values[spec.name] || spec.default || "";
      input.addEventListener("change", function () {
        tools.values[spec.name] = input.value;
      });
    } else {
      input = el("input", "input tiny");
      input.type = "number";
      if (spec.min !== null && spec.min !== undefined) { input.min = spec.min; }
      if (spec.max !== null && spec.max !== undefined) { input.max = spec.max; }
      input.step = spec.kind === "int" ? 1 : 0.01;
      input.placeholder = spec.default === null || spec.default === undefined
        ? (spec.note || "from the rig") : String(spec.default);
      if (tools.values[spec.name] !== undefined) {
        input.value = tools.values[spec.name];
      }
      input.addEventListener("input", function () {
        var text = input.value.trim();
        if (!text) { delete tools.values[spec.name]; return; }
        var number = parseFloat(text);
        if (isFinite(number)) { tools.values[spec.name] = number; }
      });
    }
    row.appendChild(input);
    var bounds = [];
    if (spec.min !== null && spec.min !== undefined) { bounds.push("≥ " + spec.min); }
    if (spec.max !== null && spec.max !== undefined) { bounds.push("≤ " + spec.max); }
    row.title = (spec.note || "") + (bounds.length ? "  (" + bounds.join(", ") + ")" : "");
    if (bounds.length) { row.appendChild(el("span", "ws-param-bounds", bounds.join(" "))); }
    return row;
  }

  function reAuthor(button) {
    if (!ws.project || !tools.kind) { return; }
    if (!Object.keys(tools.values).length) {
      toolsNote("Change a number first.", "bad");
      return;
    }
    button.disabled = true;
    toolsNote("Re-authoring " + tools.action + " in Blender…");
    api("/projects/" + encodeURIComponent(ws.project) + "/author", {
      body: { kind: tools.kind, action: tools.action, params: tools.values }
    }).then(function (res) {
      button.disabled = false;
      if (!res.ok) {
        toolsNote(res.data.error ||
                  ("The bridge answered " + res.status + "."), "bad");
        return;
      }
      toolsNote("Re-authored " + (tools.action || tools.kind) + ". " +
                (res.data.note || ""));
      wsSnapshot().then(loadPipeline);
    });
  }

  function renderAnimateTools(host) {
    var viewer = ws.viewer;
    var clips = (viewer && viewer.supported) ? viewer.actions() : [];
    if (!clips.length) {
      host.appendChild(el("p", "muted small",
        "This snapshot carries no animation. Refresh from Blender with the "
        + "rig in the scene."));
      return;
    }
    var list = el("div", "ws-clips");
    clips.forEach(function (clip) {
      var button = el("button", "btn tiny ws-clip" +
                      (clip.name === tools.action ? " is-on" : ""), clip.name);
      button.type = "button";
      button.addEventListener("click", function () {
        tools.action = clip.name;
        tools.kind = "";
        tools.values = {};
        if (viewer.play(clip.index)) { $("ws-play").textContent = "Pause"; }
        renderStageTools();
      });
      list.appendChild(button);
    });
    host.appendChild(list);
    if (!tools.action) {
      host.appendChild(el("p", "muted small", "Pick a clip to play and edit."));
      return;
    }

    // The scrubber: where the clip is, and a way to hold it there.
    var scrub = el("div", "ws-scrub");
    var slider = el("input");
    slider.type = "range";
    slider.min = 0;
    slider.max = 1000;
    slider.step = 1;
    var at = viewer.at() || { fraction: 0, frame: 0, frames: 0 };
    slider.value = Math.round(at.fraction * 1000);
    var readout = el("span", "ws-scrub-at",
                     "frame " + at.frame + " / " + at.frames);
    slider.addEventListener("input", function () {
      var now = viewer.seek(slider.value / 1000);
      if (now) {
        readout.textContent = "frame " + now.frame + " / " + now.frames;
        $("ws-play").textContent = "Play";
      }
    });
    scrub.appendChild(slider);
    scrub.appendChild(readout);
    host.appendChild(scrub);

    var kind = "";
    var lowered = tools.action.toLowerCase();
    [["walk", ["walk", "stride"]], ["punch", ["punch", "jab", "cross"]],
     ["jump", ["jump", "leap", "hop"]]].forEach(function (pair) {
      if (kind) { return; }
      pair[1].forEach(function (word) {
        if (!kind && lowered.indexOf(word) >= 0) { kind = pair[0]; }
      });
    });
    tools.kind = kind;
    if (!kind || !tools.table) {
      host.appendChild(el("p", "muted small",
        "No authoring tool matches this clip's name, so there are no numbers "
        + "to change here. Ask the assistant about it instead."));
      var ask = el("button", "btn tiny", "Ask about this clip");
      ask.type = "button";
      ask.addEventListener("click", function () {
        wsSend("Tell me how " + tools.action + " on " + ws.project +
               " was authored and what I could change about it.", "clip");
      });
      host.appendChild(ask);
      return;
    }

    var spec = (tools.table.kinds || {})[kind];
    if (!spec) { return; }
    host.appendChild(el("p", "muted small",
      spec.label + " — the numbers " + spec.command + " takes. Blank means "
      + "the tool sizes it off the rig."));
    var grid = el("div", "ws-params");
    spec.params.forEach(function (one) { grid.appendChild(paramRow(one)); });
    host.appendChild(grid);

    var row = el("div", "ws-actions");
    var apply = el("button", "btn tiny primary", "Re-author now");
    apply.type = "button";
    apply.title = "Runs " + spec.command + " straight away. No chat.";
    apply.addEventListener("click", function () { reAuthor(apply); });
    row.appendChild(apply);
    var reset = el("button", "btn tiny ghost", "Reset");
    reset.type = "button";
    reset.addEventListener("click", function () {
      tools.values = {};
      renderStageTools();
    });
    row.appendChild(reset);
    var ask2 = el("button", "btn tiny", "Ask instead");
    ask2.type = "button";
    ask2.addEventListener("click", function () {
      var said = Object.keys(tools.values).map(function (name) {
        return name + " " + tools.values[name];
      }).join(", ");
      wsSend("Re-author " + tools.action + " on " + ws.project +
             (said ? " with " + said : "") +
             ", keep everything else, then run animation_check on it.", "clip");
    });
    row.appendChild(ask2);
    host.appendChild(row);
  }

  function renderSkinTools(host) {
    var viewer = ws.viewer;
    var joints = (viewer && viewer.supported) ? viewer.handles() : [];
    if (!joints.length) {
      host.appendChild(el("p", "muted small",
        "This snapshot carries no skeleton, so there are no weights to look "
        + "at. Refresh from Blender with the rig in the scene."));
      return;
    }
    var row = el("div", "ws-tools-row");
    row.appendChild(el("span", "ws-tools-label", "Paint"));
    var picker = el("select", "select tiny");
    var none = el("option", null, "no bone — plain clay");
    none.value = "";
    picker.appendChild(none);
    joints.forEach(function (joint) {
      var option = el("option", null, joint.label + "  (" + joint.bone + ")");
      option.value = joint.bone;
      picker.appendChild(option);
    });
    picker.value = tools.bone;
    picker.addEventListener("change", function () {
      tools.bone = picker.value;
      toolsNote(tools.bone ? ("Painting " + tools.bone + "…") : "Clearing…");
      wsSnapshot(tools.bone).then(function () { renderStageTools(); });
    });
    row.appendChild(picker);
    host.appendChild(row);

    var brush = el("div", "ws-tools-row");
    brush.appendChild(el("span", "ws-tools-label", "Brush"));
    var radius = el("input");
    radius.type = "range";
    radius.min = 5;
    radius.max = 200;
    radius.step = 1;
    radius.value = tools.radius;
    var size = el("span", "ws-tools-value", tools.radius + " mm");
    radius.addEventListener("input", function () {
      tools.radius = parseInt(radius.value, 10);
      size.textContent = tools.radius + " mm";
    });
    brush.appendChild(radius);
    brush.appendChild(size);
    ["smooth", "harden"].forEach(function (op) {
      var button = el("button", "btn tiny ws-op" +
                      (tools.op === op ? " is-on" : ""), op);
      button.type = "button";
      button.title = op === "smooth"
        ? "Fills holes in a bone's region by averaging with the neighbours"
        : "Pulls a vertex towards its strongest bone, for stray influence";
      button.addEventListener("click", function () {
        tools.op = op;
        renderStageTools();
      });
      brush.appendChild(button);
    });
    host.appendChild(brush);
    host.appendChild(el("p", "muted small",
      "Click the model to " + tools.op + " the weights around that point. "
      + "Escape backs out."));
  }

  function renderStageTools() {
    var host = $("ws-tools-body");
    var strip = $("ws-stage-tools");
    var stage = tools.stage;
    if (!stage || !TOOL_STAGES[stage] || !ws.snapshot) {
      strip.hidden = true;
      return;
    }
    strip.hidden = false;
    $("ws-tools-title").textContent = TOOL_STAGES[stage];
    $("ws-inspect").hidden = false;
    host.textContent = "";
    if (stage === "animate") {
      renderAnimateTools(host);
    } else if (stage === "skin") {
      renderSkinTools(host);
    } else if (stage === "rig") {
      host.appendChild(el("p", "muted small",
        "The skeleton is on the model. Drag a joint to move it; press Check "
        + "now to measure where every bone sits."));
    } else {
      host.appendChild(el("p", "muted small",
        "Press Check now to measure the mesh. Anything it can place shows as "
        + "a pin you can click."));
    }
    renderFindings();
  }

  //: Called whenever the stepper's focus moves. The strip follows it, and a
  //: stage's findings never outlive the stage they were measured on.
  function stageToolsFollow(stage) {
    if (stage === tools.stage) { return; }
    tools.stage = stage;
    tools.findings = [];
    tools.ran = "";
    tools.open = null;
    tools.action = "";
    tools.values = {};
    var viewer = ws.viewer;
    if (viewer && viewer.supported) {
      viewer.clearPins();
      // "In rig it auto selects and shows the rig in the window."
      if (stage === "rig" && ws.snapshot && !viewer.nudging()) {
        wsToggleNudge();
      } else if (stage !== "rig" && viewer.nudging()) {
        wsToggleNudge();
      }
    }
    if (stage === "animate" && !tools.table) { loadAuthoring().then(renderStageTools); }
    toolsNote("");
    renderStageTools();
  }

  // -- loading -----------------------------------------------------------

  function summarise() {
    var node = $("ws-summary");
    if (!ws.project) { node.textContent = "Pick a project to follow."; return; }
    var plan = ws.pipeline;
    if (!plan || !plan.has_plan) { node.textContent = "no build plan"; return; }
    var bits = [];
    if (plan.task) { bits.push(plan.task); }
    bits.push(plan.done + " of " + plan.total);
    if (plan.blocked) {
      bits.push("blocked at " + plan.blocked);
    } else if (plan.next) {
      bits.push("next: " + plan.next);
    } else {
      bits.push("every stage green");
    }
    node.textContent = bits.join("  ·  ");
  }

  function loadPipeline() {
    if (!ws.project) { return Promise.resolve(); }
    renderPipeline(null);
    return api("/projects/" + encodeURIComponent(ws.project) + "/pipeline")
      .then(function (res) {
        if (!res.ok) {
          ws.pipeline = null;
          wsEmpty($("ws-focus"), "Could not read the build plan",
                  res.data.error || ("The bridge answered " + res.status + "."));
          summarise();
          return;
        }
        ws.pipeline = res.data;
        // The focus follows the plan unless the artist has pinned a stage
        // that is still on it — so a refresh after a gate turns red lands on
        // the red gate, and a refresh while reading stage 3 stays on stage 3.
        if (!focusedStage(res.data) || ws.focus === null) {
          ws.focus = defaultFocus(res.data);
        }
        renderPipeline(res.data);
        summarise();
      });
  }

  function loadVersions() {
    if (!ws.project) { return Promise.resolve(); }
    renderVersions(null);
    return api("/projects/" + encodeURIComponent(ws.project) + "/versions")
      .then(function (res) {
        if (!res.ok) {
          $("ws-saves-count").textContent = "";
          wsEmpty($("ws-versions"), "Could not read models/",
                  res.data.error || ("The bridge answered " + res.status + "."));
          return;
        }
        ws.versions = res.data;
        renderVersions(res.data);
      });
  }

  function loadDeliverables() {
    if (!ws.project) { return Promise.resolve(); }
    renderDeliverables(null);
    return api("/projects/" + encodeURIComponent(ws.project) + "/deliverables")
      .then(function (res) {
        if (!res.ok) {
          $("ws-renders-count").textContent = "";
          wsEmpty($("ws-deliverables"), "Could not read renders/",
                  res.data.error || ("The bridge answered " + res.status + "."));
          return;
        }
        ws.deliverables = res.data;
        renderDeliverables(res.data);
      });
  }

  function selectWorkspaceProject(name) {
    var changed = name !== ws.project;
    ws.project = name || null;
    if (changed) { ws.focus = null; }
    try { if (name) { localStorage.setItem("forge.project", name); } }
    catch (e) { /* private mode */ }
    if (!name) {
      ws.pipeline = null;
      renderPipeline({ has_plan: false, note: "No project selected." });
      renderVersions({ chains: [], count: 0, note: "No project selected." });
      renderDeliverables({ files: [], total: 0, note: "No project selected." });
      summarise();
      return Promise.resolve();
    }
    summarise();
    return Promise.all([loadPipeline(), loadVersions(), loadDeliverables()]);
  }

  function loadWorkspace(force) {
    if (ws.loading) { return Promise.resolve(); }
    ws.loading = true;
    renderActivity();
    // /library rather than /projects, and with the scene probe off: /projects
    // lists the folders that hold a PartForge script, which leaves out every
    // character and every floorplan — the projects this screen exists to
    // follow. ?scene=0 makes it a folder read that answers with Blender shut.
    return api("/library?scene=0").then(function (res) {
      ws.loading = false;
      var picker = $("ws-project");
      var list = (res.ok && res.data && res.data.projects) || [];
      ws.projects = list;
      var previous = ws.project;
      picker.textContent = "";
      if (!list.length) {
        var none = el("option", null, "no projects yet");
        none.value = "";
        picker.appendChild(none);
        return selectWorkspaceProject(null);
      }
      list.forEach(function (project) {
        var option = el("option", null, project.name);
        option.value = project.name;
        picker.appendChild(option);
      });
      var saved = null;
      try { saved = localStorage.getItem("forge.project"); } catch (e) { saved = null; }
      var names = list.map(function (project) { return project.name; });
      var wanted = [previous, saved, names[0]].filter(function (name) {
        return name && names.indexOf(name) >= 0;
      })[0];
      picker.value = wanted;
      if (!force && previous === wanted && ws.pipeline) {
        renderPipeline(ws.pipeline);
        renderVersions(ws.versions);
        renderDeliverables(ws.deliverables);
        summarise();
        return;
      }
      return selectWorkspaceProject(wanted);
    });
  }

  function startWorkspacePolling() {
    if (ws.timer) { return; }
    // Slower than the Studio's own poll: this is one line of text, and the
    // job snapshots it reads are refreshed by that poll anyway.
    ws.timer = setInterval(renderActivity, 1000);
  }

  function stopWorkspacePolling() {
    if (ws.timer) { clearInterval(ws.timer); ws.timer = null; }
  }

  // ----------------------------------------------------------------- tabs --
  //
  // Four now.  The Studio is the working session — conversation and part
  // sheet on one screen — the Workspace is the build being followed and
  // driven without typing, and the other two are the genuinely separate
  // errands: looking along the shelf, and replaying a saved sequence.
  var TABS = ["studio", "workspace", "library", "flows"];

  //: What a stored or pasted tab name means now.  Somebody with "workbench"
  //: in their localStorage from yesterday, or a #chat bookmark, lands on the
  //: screen that swallowed both rather than on the default by accident.
  var TAB_ALIASES = { chat: "studio", workbench: "studio" };

  function tabName(which) {
    var name = TAB_ALIASES[which] || which;
    return TABS.indexOf(name) >= 0 ? name : "";
  }

  function showTab(which) {
    which = tabName(which) || "studio";
    TABS.forEach(function (name) {
      var on = name === which;
      document.getElementById("panel-" + name).hidden = !on;
      var tab = document.getElementById("tab-" + name);
      tab.classList.toggle("is-active", on);
      tab.setAttribute("aria-selected", String(on));
    });
    if (which === "flows" && state.flows === null) { loadFlows(); }
    // The library is refetched every time it is opened, unlike the flows:
    // a part made in the conversation a minute ago is exactly what somebody
    // opens this tab to look for, and it is one folder read.
    if (which === "library") { loadLibrary(); }
    // The Workspace is refetched on every open for the same reason, and its
    // activity poll runs only while it is the tab being looked at: a status
    // board nobody can see is a timer nobody asked for.
    if (which === "workspace") {
      loadWorkspace();
      startWorkspacePolling();
    } else {
      stopWorkspacePolling();
    }
    if (which === "studio") {
      if (wb.projects === null && !wb.loading) { loadStudio(); }
      scrollDown();
    }
    // Linkable: #library is a URL that opens on the library, which is what a
    // second monitor and a bookmark are for.  Written with replaceState so the
    // back button still leaves the page instead of walking the tabs.
    try {
      if (window.history && window.history.replaceState) {
        window.history.replaceState(null, "", "#" + which);
      }
    } catch (e) { /* file:// and other odd origins */ }
    try { localStorage.setItem("forge.tab", which); } catch (e) { /* private mode */ }
  }

  function tabFromHash() {
    var hash = String(window.location.hash || "").replace(/^#/, "").toLowerCase();
    return tabName(hash) || null;
  }

  //: The tab this page was left on last time, with the pre-Studio names
  //: rewritten — and rewritten in storage too, so the alias is only ever read
  //: once per browser.
  function storedTab() {
    var saved = null;
    try { saved = localStorage.getItem("forge.tab"); } catch (e) { saved = null; }
    var wanted = tabName(saved) || "studio";
    if (saved && saved !== wanted) {
      try { localStorage.setItem("forge.tab", wanted); } catch (e) { /* private */ }
    }
    return wanted;
  }

  // ------------------------------------------------------------------ wire --
  function wire() {
    $("composer").addEventListener("submit", function (event) {
      event.preventDefault();
      send();
    });

    var box = $("message");
    box.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        send();
      }
    });
    box.addEventListener("input", resize);

    $("file").addEventListener("change", function (event) {
      attach(event.target.files && event.target.files[0]);
    });
    $("attachment-clear").addEventListener("click", clearAttachment);

    // Dropping a sketch onto the page is the obvious gesture; it goes through
    // exactly the same upload as the button.
    document.addEventListener("dragover", function (e) { e.preventDefault(); });
    document.addEventListener("drop", function (event) {
      event.preventDefault();
      var files = event.dataTransfer && event.dataTransfer.files;
      if (files && files.length) { attach(files[0]); }
    });
    // …and so is pasting one out of a screenshot tool.
    document.addEventListener("paste", function (event) {
      var items = event.clipboardData && event.clipboardData.items;
      if (!items) { return; }
      for (var i = 0; i < items.length; i++) {
        if (items[i].kind === "file" && items[i].type.indexOf("image/") === 0) {
          attach(items[i].getAsFile());
          event.preventDefault();
          return;
        }
      }
    });

    $("model").addEventListener("change", function () {
      try { localStorage.setItem("forge.model", currentModel()); } catch (e) { /* private mode */ }
    });

    $("new-conversation").addEventListener("click", function () {
      api("/new", { method: "POST" }).then(function (res) {
        if (!res.ok) {
          banner("error", res.data.error || "Could not start a new conversation.");
          return;
        }
        $("thread").appendChild(el("div", "divider", "new conversation"));
        setCost(0);
        scrollDown();
      });
    });

    $("start-services").addEventListener("click", function () {
      var button = $("start-services");
      button.disabled = true;
      button.textContent = "Starting…";
      api("/services/start", { method: "POST" }).then(function (res) {
        button.disabled = false;
        button.textContent = "Start services";
        var lines = (res.data.output || []).join("\n");
        if (!res.ok) {
          banner("error", res.data.error || "Could not start the services.", lines);
        } else {
          banner("info", res.data.ok ? "Services checked and started."
                                     : "The start script reported a problem.", lines);
        }
        refreshHealth();
      });
    });

    $("flows-refresh").addEventListener("click", loadFlows);
    $("tab-studio").addEventListener("click", function () { showTab("studio"); });
    $("tab-workspace").addEventListener("click", function () { showTab("workspace"); });
    $("tab-library").addEventListener("click", function () { showTab("library"); });

    // -- the Workspace's own controls ----------------------------------
    $("ws-project").addEventListener("change", function (event) {
      selectWorkspaceProject(event.target.value);
    });
    $("ws-refresh").addEventListener("click", function () { loadWorkspace(true); });
    // One Refresh for the whole tab. The per-section buttons were three more
    // things to look at for a folder read that takes milliseconds.
    $("ws-now-box").addEventListener("toggle", renderActivity);
    // Wrapped, not passed: wsSnapshot takes a bone name, and a bare listener
    // would hand it the click Event instead.
    $("ws-snapshot").addEventListener("click", function () { wsSnapshot(); });
    $("ws-nudge").addEventListener("click", wsToggleNudge);
    $("ws-inspect").addEventListener("click", wsInspect);

    // Skin mode's brush: a click on the model, not a drag of it. The viewer
    // itself takes pin clicks and joint drags first, so this only ever sees
    // the ones that landed on bare mesh.
    (function () {
      var canvas = $("ws-canvas");
      var down = null;
      canvas.addEventListener("pointerdown", function (event) {
        down = { x: event.clientX, y: event.clientY };
      });
      canvas.addEventListener("pointerup", function (event) {
        var start = down;
        down = null;
        if (!start || tools.stage !== "skin") { return; }
        if (Math.abs(event.clientX - start.x) > 4 ||
            Math.abs(event.clientY - start.y) > 4) { return; }
        var viewer = ws.viewer;
        if (!viewer || !viewer.supported || viewer.nudging()) { return; }
        var box = canvas.getBoundingClientRect();
        var px = event.clientX - box.left, py = event.clientY - box.top;
        // A click that landed on a pin is the pin's, not the brush's.
        var onPin = viewer.pins().some(function (pin) {
          return pin.screen &&
                 Math.hypot(pin.screen[0] - px, pin.screen[1] - py) < 18;
        });
        if (onPin) { return; }
        var found = viewer.surfaceAt(px, py);
        if (!found) { toolsNote("That click missed the mesh.", "bad"); return; }
        tools.point = found.blender;
        brushAt(found.blender);
      });
    }());
    $("ws-axis-up").addEventListener("click", function () { wsSetAxis("up"); });
    $("ws-axis-forward").addEventListener("click", function () {
      wsSetAxis("forward");
    });
    $("ws-axis-side").addEventListener("click", function () {
      wsSetAxis("side");
    });
    $("ws-nudge-cancel").addEventListener("click", wsCancelNudge);
    $("ws-nudge-apply").addEventListener("click", wsApplyNudge);
    // Esc abandons a placement, which is the reflex anybody who has used a 3D
    // tool already has. Only while the strip is open, so it never eats an Esc
    // meant for something else.
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") { return; }
      if ($("ws-nudge-bar").hidden) { return; }
      event.preventDefault();
      wsCancelNudge();
    });
    $("ws-play").addEventListener("click", wsTogglePlay);
    $("ws-view-reset").addEventListener("click", function () {
      if (ws.viewer && ws.viewer.supported) { ws.viewer.recentre(); }
    });
    $("ws-ask").addEventListener("submit", function (event) {
      event.preventDefault();
      wsSend($("ws-message").value);
    });
    $("ws-message").addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        wsSend($("ws-message").value);
      }
    });
    $("tab-flows").addEventListener("click", function () { showTab("flows"); });
    $("library-refresh").addEventListener("click", loadLibrary);
    // The models row rides the same fetch: one folder read draws the whole tab,
    // and a second button that refetched half of it would be two answers that
    // could disagree.
    $("models-refresh").addEventListener("click", loadLibrary);
    // …and a link somebody pasted, or edited in the address bar.
    window.addEventListener("hashchange", function () {
      var wanted = tabFromHash();
      if (wanted) { showTab(wanted); }
    });

    // -- the part rail
    $("projects-refresh").addEventListener("click", reloadStudio);
    $("project").addEventListener("change", function () {
      // Picking by hand pins the rail to that part; the next time the
      // conversation names one, auto-follow takes it back.
      pinProject($("project").value);
      selectProject($("project").value);
    });
    $("wb-apply").addEventListener("click", applyParams);
    $("wb-reset").addEventListener("click", resetParams);
    $("wb-preview-refresh").addEventListener("click", function () {
      renderPreview(wb.project && wb.project.object ? [wb.project.object] : null);
    });
    $("wb-view").addEventListener("change", function () {
      if (wb.preview) { renderPreview(wb.preview.objects); }
    });
    $("scene-refresh").addEventListener("click", loadScene);

    // -- the flow buttons: canned chat messages, verbatim from the markup
    document.querySelectorAll("[data-canned]").forEach(function (button) {
      button.addEventListener("click", function () {
        sendCanned(button.dataset.canned);
      });
    });

    document.querySelectorAll("[data-starter]").forEach(function (chip) {
      chip.addEventListener("click", function () {
        $("message").value = chip.dataset.starter;
        resize();
        $("message").focus();
      });
    });
  }

  function init() {
    try {
      var saved = localStorage.getItem("forge.model");
      if (saved) { $("model").value = saved; }
    } catch (e) { /* private mode: the default is fine */ }

    wire();
    resize();
    placeholder("Press Render to look at what is in the scene.");
    loadJobs();
    refreshHealth();
    // The flow row is on every tab, so its saved-flow buttons are fetched at
    // startup rather than when the Flows tab is first opened.  Blender being
    // closed just means there are none to add.
    loadFlows();
    var tab = storedTab();
    // A link wins over what was open last time: somebody who typed #library
    // meant it.
    var opening = tabFromHash() || tab;
    showTab(opening);
    // The Studio is the working screen, so its rail is loaded at startup even
    // when the page opens on the Library — coming back to the Studio should
    // not be a second wait.
    if (wb.projects === null && !wb.loading) { loadStudio(); }
    setInterval(refreshHealth, 15000);
    if (opening === "studio") { $("message").focus(); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}());
