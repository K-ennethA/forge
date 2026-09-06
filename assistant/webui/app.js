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
    });
  }

  // ----------------------------------------------------------------- tabs --
  function showTab(which) {
    var chat = which === "chat";
    $("panel-chat").hidden = !chat;
    $("panel-flows").hidden = chat;
    $("tab-chat").classList.toggle("is-active", chat);
    $("tab-flows").classList.toggle("is-active", !chat);
    $("tab-chat").setAttribute("aria-selected", String(chat));
    $("tab-flows").setAttribute("aria-selected", String(!chat));
    if (!chat && state.flows === null) { loadFlows(); }
    if (chat) { scrollDown(); }
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
    $("tab-flows").addEventListener("click", function () { showTab("flows"); });

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
    loadJobs();
    refreshHealth();
    setInterval(refreshHealth, 15000);
    $("message").focus();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}());
