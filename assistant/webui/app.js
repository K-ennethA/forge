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

  //: The workbench (Phase 11): the part being edited, its parameter controls,
  //: and the last picture of it.  Kept apart from `state` because it survives
  //: nothing — every one of these is refetched when the tab is opened.
  var wb = {
    projects: null,   // the last /projects answer
    project: null,    // the selected entry from it
    controls: {},     // parameter name -> its control
    preview: null,    // the last /preview answer
    scene: null       // the last /scene answer
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

  function gallery(files) {
    var box = el("div", "gallery");
    files.forEach(function (file) {
      if (file.kind === "image") {
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
      jobs.forEach(function (job) { upsert(job, { stick: true }); });
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
        "Pick a part, or ask the assistant in the Chat tab for a new one."));
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

  function loadProjects() {
    return api("/projects").then(function (res) {
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
          "Ask the assistant in the Chat tab for a part and it appears here."));
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
      var wanted = [previous, saved, list[0].name].filter(function (name) {
        return name && findProject(name);
      })[0];
      picker.value = wanted;
      return selectProject(wanted);
    });
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
    button.disabled = true;
    setStatus("rebuilding…");
    return api("/projects/" + encodeURIComponent(wb.project.name) + "/set_params",
               { body: { overrides: collectOverrides() } })
      .then(function (res) {
        button.disabled = false;
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

  function loadWorkbench() {
    loadProjects();
    loadScene();
  }

  // -------------------------------------------------------- flow buttons --
  //
  // Always visible, on every tab.  The three canned ones are chat messages —
  // the assistant already knows how to do these jobs and each one is several
  // tools deep, so the button's whole job is to save the artist typing the
  // same paragraph again.  Saved flows are added beside them and run in
  // Blender directly, with no model in the loop.

  function sendCanned(message) {
    showTab("chat");
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

  // ----------------------------------------------------------------- tabs --
  var TABS = ["chat", "workbench", "flows"];

  function showTab(which) {
    if (TABS.indexOf(which) < 0) { which = "chat"; }
    TABS.forEach(function (name) {
      var on = name === which;
      document.getElementById("panel-" + name).hidden = !on;
      var tab = document.getElementById("tab-" + name);
      tab.classList.toggle("is-active", on);
      tab.setAttribute("aria-selected", String(on));
    });
    if (which === "flows" && state.flows === null) { loadFlows(); }
    if (which === "workbench" && wb.projects === null) { loadWorkbench(); }
    if (which === "chat") { scrollDown(); }
    try { localStorage.setItem("forge.tab", which); } catch (e) { /* private mode */ }
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
    $("tab-chat").addEventListener("click", function () { showTab("chat"); });
    $("tab-workbench").addEventListener("click", function () { showTab("workbench"); });
    $("tab-flows").addEventListener("click", function () { showTab("flows"); });

    // -- the workbench
    $("projects-refresh").addEventListener("click", loadWorkbench);
    $("project").addEventListener("change", function () {
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
    var tab = "chat";
    try { tab = localStorage.getItem("forge.tab") || "chat"; } catch (e) { tab = "chat"; }
    showTab(tab);
    setInterval(refreshHealth, 15000);
    if (tab === "chat") { $("message").focus(); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}());
