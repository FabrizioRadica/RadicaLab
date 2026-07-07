/* Project editor state + New Project wizard */

window.WVG = window.WVG || {};

(function (WVG) {
  "use strict";

  /* =========================================================
     Resolution presets (shared by wizard + editor)
     ========================================================= */
  /* All presets are multiples of 32 — required by Wan 2.2 pipelines
     (VAE spatial scale x DiT patch size; 32 for TI2V-5B). */
  var RES_PRESETS = [
    { name: "Test Low Landscape", w: 384, h: 224, orientation: "landscape", tag: "test" },
    { name: "Test Low Portrait", w: 224, h: 384, orientation: "portrait", tag: "test" },
    { name: "Test Horizontal Quick", w: 640, h: 352, orientation: "landscape", tag: "test" },
    { name: "Test Vertical Quick", w: 352, h: 640, orientation: "portrait", tag: "test" },
    { name: "SD Landscape", w: 640, h: 352, orientation: "landscape", tag: "standard" },
    { name: "HD Landscape (Wan 704p)", w: 1280, h: 704, orientation: "landscape", tag: "standard" },
    { name: "Full HD Landscape", w: 1920, h: 1088, orientation: "landscape", tag: "standard" },
    { name: "Portrait HD", w: 704, h: 1280, orientation: "portrait", tag: "standard" },
    { name: "Portrait Full HD", w: 1088, h: 1920, orientation: "portrait", tag: "standard" },
    { name: "Square", w: 1024, h: 1024, orientation: "square", tag: "standard" }
  ];
  WVG.RES_PRESETS = RES_PRESETS;

  function validResolution(w, h) {
    return Number.isInteger(w) && Number.isInteger(h) &&
      w >= 16 && h >= 16 && w <= 8192 && h <= 8192 && w % 8 === 0 && h % 8 === 0;
  }

  /* =========================================================
     New Project wizard
     ========================================================= */
  var wz = { step: 1, mode: "text2video", orientation: "landscape", res: null, custom: false };

  WVG.openWizard = function () {
    wz = { step: 1, mode: "text2video", orientation: "landscape", res: null, custom: false };
    renderWizard();
    WVG.openModal("wizard-backdrop");
    var nameInput = document.getElementById("wz-name");
    if (nameInput) setTimeout(function () { nameInput.focus(); }, 100);
  };
  WVG.closeWizard = function () { WVG.closeModal("wizard-backdrop"); };

  function renderWizard() {
    document.querySelectorAll("#wizard-steps .wizard-step-item").forEach(function (item) {
      var s = parseInt(item.dataset.step, 10);
      item.classList.toggle("active", s === wz.step);
      item.classList.toggle("done", s < wz.step);
    });
    document.querySelectorAll(".wizard-page").forEach(function (page) {
      page.style.display = parseInt(page.dataset.page, 10) === wz.step ? "" : "none";
    });
    var back = document.getElementById("wz-back");
    var next = document.getElementById("wz-next");
    if (back) back.disabled = wz.step === 1;
    if (next) next.textContent = wz.step === 5 ? "✦ Create Project" : "Next ›";
    if (wz.step === 4) renderResGrid();
    if (wz.step === 5) renderSummary();
  }

  function renderResGrid() {
    var grid = document.getElementById("wz-res-grid");
    if (!grid) return;
    grid.innerHTML = "";
    var presets = RES_PRESETS.filter(function (p) { return p.orientation === wz.orientation; });
    presets.forEach(function (p) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "preset-btn";
      if (wz.res && !wz.custom && wz.res.w === p.w && wz.res.h === p.h && wz.res.name === p.name) btn.classList.add("selected");
      btn.innerHTML = '<span class="p-tag">' + p.tag + '</span><span class="p-name">' + p.name + '</span><span class="p-res">' + p.w + "×" + p.h + "</span>";
      btn.addEventListener("click", function () {
        wz.res = p; wz.custom = false;
        document.getElementById("wz-custom-res").style.display = "none";
        renderResGrid();
      });
      grid.appendChild(btn);
    });
    var custom = document.createElement("button");
    custom.type = "button";
    custom.className = "preset-btn" + (wz.custom ? " selected" : "");
    custom.innerHTML = '<span class="p-tag">custom</span><span class="p-name">Custom</span><span class="p-res">width × height</span>';
    custom.addEventListener("click", function () {
      wz.custom = true; wz.res = null;
      document.getElementById("wz-custom-res").style.display = "flex";
      renderResGrid();
    });
    grid.appendChild(custom);
    document.getElementById("wz-custom-res").style.display = wz.custom ? "flex" : "none";
    if (!wz.res && !wz.custom && presets.length) {
      wz.res = presets.find(function (p) { return p.tag === "standard"; }) || presets[0];
      renderResGrid();
    }
  }

  function currentResolution() {
    if (wz.custom) {
      return {
        w: parseInt(document.getElementById("wz-width").value, 10),
        h: parseInt(document.getElementById("wz-height").value, 10)
      };
    }
    return wz.res ? { w: wz.res.w, h: wz.res.h } : null;
  }

  function renderSummary() {
    var list = document.getElementById("wz-summary");
    if (!list) return;
    var res = currentResolution();
    var rows = [
      ["Project name", document.getElementById("wz-name").value || "—"],
      ["Description", document.getElementById("wz-desc").value || "—"],
      ["Tags", document.getElementById("wz-tags").value || "—"],
      ["Generation mode", wz.mode === "text2video" ? "Text2Video" : "Image2Video"],
      ["Orientation", wz.orientation.charAt(0).toUpperCase() + wz.orientation.slice(1)],
      ["Resolution", res ? res.w + " × " + res.h : "—"]
    ];
    list.innerHTML = rows.map(function (r) {
      return '<li><span class="k">' + r[0] + '</span><span>' + escapeHtml(String(r[1])) + "</span></li>";
    }).join("");
  }

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  function wizardValidate() {
    if (wz.step === 1) {
      var name = document.getElementById("wz-name").value.trim();
      if (!name) { WVG.toast("Project name is required", "error"); return false; }
    }
    if (wz.step === 4) {
      var res = currentResolution();
      var err = document.getElementById("wz-res-error");
      if (!res || !validResolution(res.w, res.h)) {
        err.textContent = "Enter a valid resolution: 16–8192 px, multiples of 8.";
        err.style.display = "block";
        return false;
      }
      err.style.display = "none";
    }
    return true;
  }

  async function wizardFinish() {
    var res = currentResolution();
    var tags = document.getElementById("wz-tags").value.split(",").map(function (t) { return t.trim(); }).filter(Boolean);
    var next = document.getElementById("wz-next");
    next.disabled = true;
    try {
      var project = await WVG.api("/api/projects", {
        method: "POST",
        body: {
          name: document.getElementById("wz-name").value.trim(),
          description: document.getElementById("wz-desc").value,
          tags: tags,
          generation_mode: wz.mode,
          orientation: wz.orientation,
          width: res.w,
          height: res.h
        }
      });
      WVG.toast('Project "' + project.name + '" created', "success");
      window.location = "/?project=" + project.id;
    } catch (e) {
      WVG.toast("Could not create project", "error", e.message);
      next.disabled = false;
    }
  }

  function initWizard() {
    var next = document.getElementById("wz-next");
    var back = document.getElementById("wz-back");
    if (!next) return;
    next.addEventListener("click", function () {
      if (!wizardValidate()) return;
      if (wz.step === 5) { wizardFinish(); return; }
      wz.step += 1;
      renderWizard();
    });
    back.addEventListener("click", function () {
      if (wz.step > 1) { wz.step -= 1; renderWizard(); }
    });
    document.querySelectorAll("[data-wz-mode]").forEach(function (card) {
      card.addEventListener("click", function () {
        wz.mode = card.dataset.wzMode;
        document.querySelectorAll("[data-wz-mode]").forEach(function (c) { c.classList.toggle("selected", c === card); });
      });
    });
    document.querySelectorAll("[data-wz-orientation]").forEach(function (card) {
      card.addEventListener("click", function () {
        wz.orientation = card.dataset.wzOrientation;
        wz.res = null; wz.custom = false;
        document.querySelectorAll("[data-wz-orientation]").forEach(function (c) { c.classList.toggle("selected", c === card); });
      });
    });
    var nameInput = document.getElementById("wz-name");
    nameInput.addEventListener("input", function () {
      var safe = nameInput.value.replace(/[^A-Za-z0-9 _\-]+/g, "").trim().replace(/\s+/g, "_");
      document.getElementById("wz-folder-preview").textContent = safe ? "projects/" + safe : "—";
    });
  }

  /* =========================================================
     Editor (home page)
     ========================================================= */
  var project = WVG.readJson("project-data");
  var models = WVG.readJson("models-data") || [];
  WVG.project = project;
  WVG.models = models;

  function el(id) { return document.getElementById(id); }

  function populateModelSelect() {
    var select = el("f-model");
    if (!select) return;
    var mode = project.generation_mode;
    select.innerHTML = "";
    models.forEach(function (m) {
      var compatible = m.generation_type === "both" || m.generation_type === mode;
      var opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.display_name + (compatible ? "" : " (not for this mode)") +
        (m.status !== "ok" ? " [" + m.status + "]" : "");
      opt.disabled = !compatible;
      select.appendChild(opt);
    });
    var current = models.find(function (m) { return m.id === project.model_id; });
    var currentOk = current && (current.generation_type === "both" || current.generation_type === mode);
    if (currentOk) {
      select.value = project.model_id;
    } else {
      var def = models.find(function (m) {
        return (mode === "text2video" && m.default_t2v) || (mode === "image2video" && m.default_i2v);
      }) || models.find(function (m) { return m.generation_type === "both" || m.generation_type === mode; });
      if (def) { select.value = def.id; project.model_id = def.id; }
    }
    updateVramHint();
  }

  function updateVramHint() {
    var select = el("f-model");
    var hint = el("model-vram-hint");
    if (!select || !hint) return;
    var m = models.find(function (x) { return x.id === select.value; });
    hint.textContent = m ? "VRAM ~" + m.recommended_vram_gb + " GB" : "";
  }

  function populateResolutionSelect() {
    var select = el("f-resolution");
    if (!select) return;
    select.innerHTML = "";
    var seen = {};
    RES_PRESETS.forEach(function (p) {
      var key = p.w + "x" + p.h;
      if (seen[key]) return;
      seen[key] = true;
      var opt = document.createElement("option");
      opt.value = key;
      opt.textContent = p.name + " — " + p.w + "×" + p.h;
      select.appendChild(opt);
    });
    var customOpt = document.createElement("option");
    customOpt.value = "custom";
    customOpt.textContent = "Custom…";
    select.appendChild(customOpt);

    var key = project.resolution.width + "x" + project.resolution.height;
    if (seen[key]) {
      select.value = key;
    } else {
      select.value = "custom";
      el("custom-res-row").style.display = "flex";
    }
    el("f-width").value = project.resolution.width;
    el("f-height").value = project.resolution.height;
  }

  function applyMode(mode) {
    project.generation_mode = mode;
    document.querySelectorAll(".mode-tab").forEach(function (tab) {
      tab.classList.toggle("active", tab.dataset.mode === mode);
    });
    var isI2V = mode === "image2video";
    el("i2v-source-block").style.display = isI2V ? "" : "none";
    el("i2v-influence-block").style.display = isI2V ? "" : "none";
    populateModelSelect();
  }

  /* =========================================================
     Duration <-> FPS <-> frames synchronization
     frames = round(duration * fps), snapped to Wan's 4k+1 rule
     duration = frames / fps
     ========================================================= */
  var durationMode = "custom"; // "custom" or a preset seconds value (number)

  function snapFrames(n) {
    // Shared Wan 4k+1 frame rule (patchSeq §10) — same implementation the
    // sequence module uses. Local fallback keeps Single Clip working if the
    // shared module failed to load.
    if (window.WVGGenParams && WVGGenParams.snapFrames) return WVGGenParams.snapFrames(n);
    n = Math.max(1, Math.min(1000, Math.round(n)));
    var r = (n - 1) % 4;
    if (r !== 0) n += (r >= 2 ? 4 - r : -r);
    return Math.max(1, Math.min(997, n));
  }

  function updateDurationEstimate() {
    var out = el("duration-estimate");
    if (!out) return;
    var frames = parseInt(el("f-frames").value, 10);
    var fps = parseInt(el("f-fps").value, 10);
    if (frames >= 1 && fps >= 1) {
      var d = frames / fps;
      out.textContent = (Math.round(d * 100) / 100) + "s  (" + frames + " frames @ " + fps + " fps)";
    } else {
      out.textContent = "—";
    }
  }

  function markDurationPreset() {
    document.querySelectorAll("#duration-presets .duration-btn").forEach(function (b) {
      var val = b.dataset.seconds;
      var isActive = (durationMode === "custom" && val === "custom") ||
                     (durationMode !== "custom" && val !== "custom" && parseFloat(val) === durationMode);
      b.classList.toggle("active", isActive);
    });
  }

  function applyDurationPreset(seconds) {
    var fps = parseInt(el("f-fps").value, 10) || 24;
    el("f-frames").value = snapFrames(seconds * fps);
    updateDurationEstimate();
  }

  function initDurationControl() {
    var wrap = el("duration-presets");
    if (!wrap) return;

    // Detect whether the stored frames/fps match a preset duration.
    var frames = parseInt(el("f-frames").value, 10);
    var fps = parseInt(el("f-fps").value, 10);
    durationMode = "custom";
    if (frames >= 1 && fps >= 1) {
      var d = frames / fps;
      [2, 3, 5, 10].forEach(function (p) {
        if (Math.abs(d - p) * fps <= 2) durationMode = p; // within snap tolerance
      });
    }
    markDurationPreset();
    updateDurationEstimate();

    wrap.querySelectorAll(".duration-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (btn.dataset.seconds === "custom") {
          durationMode = "custom";
        } else {
          durationMode = parseFloat(btn.dataset.seconds);
          applyDurationPreset(durationMode);
        }
        markDurationPreset();
      });
    });

    el("f-fps").addEventListener("input", function () {
      if (durationMode !== "custom") applyDurationPreset(durationMode); // duration is primary
      else updateDurationEstimate();                                    // frames stay manual
    });
    el("f-frames").addEventListener("input", function () {
      durationMode = "custom"; // manual frame edits switch duration to Custom
      markDurationPreset();
      updateDurationEstimate();
    });
  }

  /* Select helper: never silently change a stored value — if it is not in
     the option list (e.g. hand-edited project), add it so it stays visible. */
  function setSelectValue(selectEl, value) {
    if (!selectEl) return;
    selectEl.value = value;
    if (selectEl.value !== value) {
      var opt = document.createElement("option");
      opt.value = value;
      opt.textContent = value + " (custom)";
      selectEl.appendChild(opt);
      selectEl.value = value;
    }
  }

  /* Sampler backend status (patch6c §9): show honest direct-vs-ComfyUI
     capability and what will actually happen for the selected sampler. Never
     claims "Wan2.2 does not support euler". */
  function updateSamplingCompat() {
    var warnEl = el("sampling-compat-warning");
    if (!warnEl || !window.WVGGenParams) { if (warnEl) warnEl.style.display = "none"; return; }
    // Shared sampler-support validation (patchSeq §9/§11) — same code path as
    // the sequence Generation Parameters module.
    var r = WVGGenParams.samplerCompat(WVG.backendStatus, el("f-sampler").value,
      parseFloat(el("f-denoise").value));
    if (!r) { warnEl.style.display = "none"; return; }
    warnEl.textContent = r.text;
    warnEl.style.color = (r.level === "warning") ? "var(--warning)" : "var(--muted, #9aa)";
    warnEl.style.display = "";
  }

  /* Wan2.2 5B presets (patch8 §3): apply concrete values to the form and show
     exactly what changed — nothing is applied silently. */
  function setResolutionFields(w, h) {
    el("f-width").value = w;
    el("f-height").value = h;
    var sel = el("f-resolution");
    if (sel) {
      var wanted = w + "x" + h, matched = false;
      for (var i = 0; i < sel.options.length; i++) {
        if (sel.options[i].value === wanted) { sel.value = wanted; matched = true; break; }
      }
      if (!matched) { sel.value = "custom"; el("custom-res-row").style.display = "flex"; }
      else { el("custom-res-row").style.display = "none"; }
    }
  }

  function applyPresetTarget(t) {
    if (!t) return;
    // Turbo/Normal presets carry an orientation — apply it so the resolution,
    // orientation field and preset stay consistent (patchoptimization §4/§5).
    if (t.orientation && el("f-orientation")) el("f-orientation").value = t.orientation;
    setResolutionFields(t.resolution.width, t.resolution.height);
    el("f-frames").value = t.frames;
    el("f-fps").value = t.fps;
    el("f-steps").value = t.steps;
    setSelectValue(el("f-sampler"), t.sampler_name);
    setSelectValue(el("f-scheduler"), t.scheduler);
    el("f-guidance").value = t.guidance_scale;
    el("f-denoise").value = t.denoise;
    if (el("f-model-sampling-enabled")) {
      el("f-model-sampling-enabled").checked = !!t.model_sampling.enabled;
      el("f-model-sampling-shift").value = t.model_sampling.shift;
      el("f-model-sampling-shift-num").value = Number(t.model_sampling.shift).toFixed(2);
    }
    if (el("f-unload-after-gen")) el("f-unload-after-gen").checked = !!t.unload_model_after_generation;
    // Memory optimization / model offload are real advanced fields the preset sets.
    if (el("f-memopt") && typeof t.memory_optimization !== "undefined") el("f-memopt").checked = !!t.memory_optimization;
    if (el("f-offload") && typeof t.model_offload !== "undefined") el("f-offload").checked = !!t.model_offload;
    // Store per-project offload override (not a visible field, but persisted).
    if (project.params && project.params.advanced) project.params.advanced.offload_policy = t.offload_policy || "";
    checkHeavyManual();
    // Refresh dependent UI (slider labels, duration, compat notes).
    ["f-guidance:v-guidance:1", "f-denoise:v-denoise:2"].forEach(function (spec) {
      var parts = spec.split(":"), inp = el(parts[0]), out = el(parts[1]);
      if (inp && out) out.textContent = Number(inp.value).toFixed(parseInt(parts[2], 10));
    });
    updateDurationEstimate();
    updateSamplingCompat();
    updateModelSamplingStatus();
  }

  function showPresetChanges(res) {
    var box = el("wan-preset-changes");
    if (!box) return;
    if (!res || res.preset === "manual") { box.style.display = "none"; return; }
    var html = "<strong>Preset applied:</strong>";
    if (res.changes && res.changes.length) {
      html += "<ul style='margin:4px 0 0;padding-left:16px;'>";
      res.changes.forEach(function (c) { html += "<li>" + escapeHtml(c) + "</li>"; });
      html += "</ul>";
    } else {
      html += " <span class='muted'>no changes (already matching).</span>";
    }
    // Model/preset mismatch + heavy-render warnings (patchoptimization §7).
    (res.warnings || []).forEach(function (w) {
      var color = (w.level === "info") ? "var(--muted, #9aa)" : "var(--warning)";
      var icon = (w.level === "info") ? "ⓘ" : "⚠";
      html += "<div style='color:" + color + ";margin-top:4px;'>" + icon + " " + escapeHtml(w.message) + "</div>";
    });
    if (!(res.warnings && res.warnings.length) && res.warning) {
      html += "<div style='color:var(--warning);margin-top:4px;'>⚠ " + escapeHtml(res.warning) + "</div>";
    }
    box.innerHTML = html;
    box.style.display = "";
  }

  async function applyWanPreset(presetId) {
    var box = el("wan-preset-changes");
    if (presetId === "manual") {
      if (box) box.style.display = "none";
      project.params.wan_preset = "manual";
      return;
    }
    try {
      // Save first so the server computes the preset against current mode/orientation.
      await WVG.saveProject(true);
      // Pass a plain object: WVG.api stringifies once and sets the JSON
      // Content-Type header. Pre-stringifying here bypasses both, so FastAPI
      // received the body as a string ("Input should be a valid dictionary").
      var res = await WVG.api("/api/presets/preview", {
        method: "POST",
        body: { project_id: project.id, preset_id: presetId },
      });
      // Turbo model + normal preset requires explicit confirmation (§7.1).
      if (res.requires_confirmation) {
        var blocking = (res.warnings || [])
          .filter(function (w) { return w.level === "blocking"; })
          .map(function (w) { return w.message; }).join("\n\n");
        if (!window.confirm(blocking + "\n\nApply this preset anyway?")) {
          el("f-wan-preset").value = project.params.wan_preset || "manual";
          return;
        }
      }
      applyPresetTarget(res.target);
      showPresetChanges(res);
      project.params.wan_preset = presetId;
      WVG.toast("Preset applied — review changes and Save", "success");
    } catch (e) {
      WVG.toast("Could not apply preset", "error", e.message);
    }
  }

  /* Very heavy manual render warning (patchoptimization §7.4). */
  function checkHeavyManual() {
    var el0 = el("manual-heavy-warning");
    if (!el0) return;
    var w = parseInt((el("f-width") || {}).value, 10) || 0;
    var h = parseInt((el("f-height") || {}).value, 10) || 0;
    var fr = parseInt((el("f-frames") || {}).value, 10) || 0;
    if (fr >= 121 && Math.max(w, h) >= 1280) {
      el0.textContent = "⚠ Very heavy render: high resolution with " + fr +
        " frames. Expect much longer render time. Consider 81 or 49 frames for testing.";
      el0.style.display = "";
    } else {
      el0.style.display = "none";
    }
  }

  var ACCEL_PROFILES = ["turbo", "lightning", "lightx2v", "fast"];

  function capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

  /* Build the preset dropdown grouped by family, model-appropriate group first
     (patchoptimization §8). Manual stays as the first option. */
  function populatePresetSelect(defs, profile) {
    var sel = el("f-wan-preset");
    if (!sel) return;
    var accel = ACCEL_PROFILES.indexOf(profile) >= 0;
    var manual = [];
    var groups = {}, order = [];
    (defs || []).forEach(function (d) {
      if (d.manual) { manual.push(d); return; }
      var g = d.group || "Presets";
      if (!groups[g]) { groups[g] = []; order.push(g); }
      groups[g].push(d);
    });
    // Turbo group first when an accelerated model is detected, else last.
    order.sort(function (a, b) {
      var ta = /turbo/i.test(a) ? 1 : 0, tb = /turbo/i.test(b) ? 1 : 0;
      return accel ? (tb - ta) : (ta - tb);
    });
    function opt(d) {
      var o = document.createElement("option");
      o.value = d.id; o.textContent = d.label;
      return o;
    }
    sel.innerHTML = "";
    manual.forEach(function (d) { sel.appendChild(opt(d)); });
    order.forEach(function (g) {
      var og = document.createElement("optgroup");
      og.label = g;
      groups[g].forEach(function (d) { og.appendChild(opt(d)); });
      sel.appendChild(og);
    });
    // Restore the project's current selection if it still exists.
    var want = (project.params && project.params.wan_preset) || "manual";
    sel.value = want;
    if (sel.value !== want) sel.value = "manual";
  }

  async function loadPresetCatalog() {
    var recEl = el("wan-preset-recommend");
    var profEl = el("wan-preset-profile");
    try {
      // Pass the currently-selected model so detection reflects an unsaved change.
      var selModel = (el("f-model") || {}).value || project.model_id || "";
      var data = await WVG.api("/api/presets?project_id=" + encodeURIComponent(project.id) +
                               "&model_id=" + encodeURIComponent(selModel));
      var profile = data.detected_model_profile;
      populatePresetSelect(data.presets, profile);
      // "Detected model profile" line (patchoptimization §8).
      if (profEl && profile) {
        var accel = ACCEL_PROFILES.indexOf(profile) >= 0;
        profEl.textContent = "Detected model profile: " + capitalize(profile);
        profEl.style.color = accel ? "var(--warning)" : "var(--muted, #9aa)";
        profEl.style.display = "";
      } else if (profEl) {
        profEl.style.display = "none";
      }
      var r = data.recommendation || {};
      var labelById = {};
      (data.presets || []).forEach(function (p) { labelById[p.id] = p.label; });
      if (recEl && r.recommended) {
        recEl.textContent = "Recommended: " + (labelById[r.recommended] || r.recommended) +
          (r.reason ? " — " + r.reason : "");
      }
    } catch (e) { /* catalogue/recommendation is optional */ }
  }

  /* ModelSamplingSD3 status (patch7 §13): show whether it will be applied and
     by which backend. Never claims applied when it is not. */
  function updateModelSamplingStatus() {
    var statusEl = el("model-sampling-status");
    var enabledEl = el("f-model-sampling-enabled");
    var shiftField = el("model-sampling-shift-field");
    if (!statusEl || !enabledEl) return;
    var enabled = enabledEl.checked;
    if (shiftField) shiftField.style.opacity = enabled ? "1" : "0.5";
    var shift = parseFloat((el("f-model-sampling-shift-num") || {}).value || "8");
    // Shared ModelSamplingSD3 status (patchSeq §9) — same code path as the
    // sequence Generation Parameters module.
    if (!window.WVGGenParams) { statusEl.style.display = "none"; return; }
    var r = WVGGenParams.modelSamplingStatus(WVG.backendStatus, enabled, shift);
    statusEl.textContent = r.text;
    statusEl.style.color = r.warn ? "var(--warning)" : "var(--muted, #9aa)";
    statusEl.style.display = "";
  }

  WVG.updateComposedPrompt = function () {
    var view = el("composed-prompt-view");
    if (!view) return;
    var parts = [el("f-positive").value.trim()];
    var cm = project.camera_motion || {};
    if (cm.enabled && cm.applied_to_prompt && cm.fragment) parts.push(cm.fragment.trim());
    var composed = parts.filter(Boolean).join(", ");
    view.textContent = composed || "— (empty prompt)";
  };

  function hydrateEditor() {
    if (!project || !el("editor")) return;

    applyMode(project.generation_mode);
    el("f-positive").value = project.positive_prompt || "";
    el("f-negative").value = project.negative_prompt || "";
    el("f-orientation").value = project.orientation;
    populateResolutionSelect();

    var p = project.params;
    el("f-frames").value = p.frames;
    el("f-fps").value = p.fps;
    el("f-seed").value = p.seed;
    el("f-random-seed").checked = p.random_seed;
    el("f-control-after").value = p.control_after_generate || "fixed";
    el("f-guidance").value = p.guidance_scale;
    el("f-motion").value = p.motion_strength;
    el("f-influence").value = p.image_influence;
    el("f-denoise").value = (p.denoise == null) ? 1 : p.denoise;
    if (el("f-quality-preset")) el("f-quality-preset").value = p.quality_preset || "none";
    el("f-format").value = p.output_format;
    el("f-steps").value = p.advanced.steps;
    setSelectValue(el("f-sampler"), p.sampler_name || p.advanced.sampler || "euler");
    setSelectValue(el("f-scheduler"), p.scheduler || "simple");
    el("f-precision").value = p.advanced.precision;
    el("f-device").value = p.advanced.device;
    el("f-memopt").checked = p.advanced.memory_optimization;
    el("f-offload").checked = p.advanced.model_offload;
    el("f-save-frames").checked = p.advanced.save_intermediate_frames;
    if (el("f-unload-after-gen")) el("f-unload-after-gen").checked = !!p.advanced.unload_model_after_generation;
    if (el("f-wan-preset")) el("f-wan-preset").value = p.wan_preset || "manual";

    // ModelSamplingSD3 (patch7)
    var ms = p.model_sampling || { enabled: false, shift: 8.0 };
    if (el("f-model-sampling-enabled")) {
      el("f-model-sampling-enabled").checked = !!ms.enabled;
      var shiftVal = (ms.shift == null) ? 8.0 : ms.shift;
      el("f-model-sampling-shift").value = shiftVal;
      el("f-model-sampling-shift-num").value = Number(shiftVal).toFixed(2);
    }

    if (project.source_image) {
      el("source-preview-img").src = "/media/projects/" + project.id + "/source/" + project.source_image + "?t=" + Date.now();
      el("source-preview-wrap").style.display = "";
      el("dropzone").style.display = "none";
    }

    initDurationControl();

    WVG.bindSlider("f-guidance", "v-guidance", function (v) { return v.toFixed(1); });
    WVG.bindSlider("f-motion", "v-motion", function (v) { return v.toFixed(2); });
    WVG.bindSlider("f-influence", "v-influence", function (v) { return v.toFixed(2); });
    WVG.bindSlider("f-denoise", "v-denoise", function (v) { return v.toFixed(2); });

    ["f-sampler", "f-scheduler", "f-denoise"].forEach(function (id) {
      el(id).addEventListener(id === "f-denoise" ? "input" : "change", updateSamplingCompat);
    });
    document.addEventListener("wvg:backend-status", updateSamplingCompat);
    updateSamplingCompat();

    // ModelSamplingSD3 controls: keep slider and number field in sync, and
    // refresh the status/compat note on any change (patch7).
    var msEnabled = el("f-model-sampling-enabled");
    var msSlider = el("f-model-sampling-shift");
    var msNum = el("f-model-sampling-shift-num");
    if (msEnabled && msSlider && msNum) {
      msSlider.addEventListener("input", function () {
        msNum.value = Number(msSlider.value).toFixed(2);
        updateModelSamplingStatus();
      });
      msNum.addEventListener("input", function () {
        var v = parseFloat(msNum.value);
        if (!isNaN(v)) { if (v <= 20) msSlider.value = v; }
        updateModelSamplingStatus();
      });
      msEnabled.addEventListener("change", updateModelSamplingStatus);
      document.addEventListener("wvg:backend-status", updateModelSamplingStatus);
      updateModelSamplingStatus();
    }

    // Wan2.2 5B preset selector (patch8 + patchoptimization): populate the
    // grouped catalogue, apply values + show change summary and warnings.
    var presetSel = el("f-wan-preset");
    if (presetSel) {
      presetSel.addEventListener("change", function () { applyWanPreset(presetSel.value); });
      loadPresetCatalog();
    }
    // Very-heavy manual render warning (§7.4).
    ["f-frames", "f-width", "f-height"].forEach(function (id) {
      if (el(id)) el(id).addEventListener("input", checkHeavyManual);
    });
    if (el("f-resolution")) el("f-resolution").addEventListener("change", checkHeavyManual);
    checkHeavyManual();
    WVG.bindCharCount("f-positive", "count-positive");
    WVG.bindCharCount("f-negative", "count-negative");

    document.querySelectorAll(".mode-tab").forEach(function (tab) {
      tab.addEventListener("click", function () { applyMode(tab.dataset.mode); });
    });
    el("f-resolution").addEventListener("change", function () {
      var custom = this.value === "custom";
      el("custom-res-row").style.display = custom ? "flex" : "none";
      if (!custom) {
        var parts = this.value.split("x");
        el("f-width").value = parseInt(parts[0], 10);
        el("f-height").value = parseInt(parts[1], 10);
      }
    });
    el("f-model").addEventListener("change", function () {
      updateVramHint();
      // Re-detect the selected model's speed profile and re-order presets (§8).
      loadPresetCatalog();
    });
    el("f-positive").addEventListener("input", WVG.updateComposedPrompt);

    if (localStorage.getItem("wvg:ui-advanced-open") === "1") {
      el("advanced-params").setAttribute("open", "");
    }

    var saveBtn = el("btn-save-project");
    if (saveBtn) saveBtn.addEventListener("click", WVG.saveProject);
    var folderBtn = el("btn-open-folder");
    if (folderBtn) folderBtn.addEventListener("click", function () { WVG.openProjectFolder(project.id); });

    var search = el("library-search");
    if (search) {
      search.addEventListener("input", function () {
        var q = search.value.toLowerCase().trim();
        document.querySelectorAll("#library-list .mini-card").forEach(function (card) {
          card.style.display = !q || (card.dataset.name || "").indexOf(q) !== -1 ? "" : "none";
        });
      });
    }

    WVG.updateComposedPrompt();

    window.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        WVG.saveProject();
      }
    });
  }

  WVG.collectProject = function () {
    var w = parseInt(el("f-width").value, 10);
    var h = parseInt(el("f-height").value, 10);
    if (!validResolution(w, h)) {
      throw new Error("Invalid resolution: width and height must be 16–8192 px and multiples of 8.");
    }
    var frames = parseInt(el("f-frames").value, 10);
    var fps = parseInt(el("f-fps").value, 10);
    if (!(frames >= 1 && frames <= 1000)) throw new Error("Frame count must be between 1 and 1000.");
    if (!(fps >= 1 && fps <= 120)) throw new Error("FPS must be between 1 and 120.");

    var cm = window.WVGCam ? WVGCam.getSettings() : project.camera_motion;
    return {
      generation_mode: project.generation_mode,
      orientation: el("f-orientation").value,
      resolution: { width: w, height: h },
      model_id: el("f-model").value,
      positive_prompt: el("f-positive").value,
      negative_prompt: el("f-negative").value,
      params: {
        fps: fps,
        frames: frames,
        seed: parseInt(el("f-seed").value, 10),
        random_seed: el("f-random-seed").checked,
        control_after_generate: el("f-control-after").value,
        guidance_scale: parseFloat(el("f-guidance").value),
        sampler_name: el("f-sampler").value,
        scheduler: el("f-scheduler").value,
        denoise: parseFloat(el("f-denoise").value),
        motion_strength: parseFloat(el("f-motion").value),
        image_influence: parseFloat(el("f-influence").value),
        quality_preset: el("f-quality-preset") ? el("f-quality-preset").value : "none",
        wan_preset: el("f-wan-preset") ? el("f-wan-preset").value : "manual",
        model_sampling: {
          enabled: el("f-model-sampling-enabled") ? el("f-model-sampling-enabled").checked : false,
          type: "sd3",
          shift: el("f-model-sampling-shift-num")
            ? (parseFloat(el("f-model-sampling-shift-num").value) || 8.0)
            : 8.0
        },
        output_format: el("f-format").value,
        advanced: {
          steps: parseInt(el("f-steps").value, 10),
          sampler: el("f-sampler").value,
          precision: el("f-precision").value,
          memory_optimization: el("f-memopt").checked,
          device: el("f-device").value,
          model_offload: el("f-offload").checked,
          cache_latents: project.params.advanced.cache_latents,
          preview_frames: project.params.advanced.preview_frames,
          save_intermediate_frames: el("f-save-frames").checked,
          output_codec: project.params.advanced.output_codec,
          offload_policy: (project.params.advanced && project.params.advanced.offload_policy) || "",
          unload_model_after_generation: el("f-unload-after-gen") ? el("f-unload-after-gen").checked : false
        }
      },
      camera_motion: cm
    };
  };

  WVG.saveProject = async function (silent) {
    if (!project) return null;
    var payload;
    try { payload = WVG.collectProject(); } catch (e) {
      WVG.toast("Cannot save project", "error", e.message);
      return null;
    }
    try {
      var saved = await WVG.api("/api/projects/" + project.id, { method: "PUT", body: payload });
      Object.assign(project, saved);
      if (!silent) WVG.toast("Project saved", "success");
      return saved;
    } catch (e) {
      WVG.toast("Save failed", "error", e.message);
      return null;
    }
  };

  document.addEventListener("DOMContentLoaded", function () {
    initWizard();
    hydrateEditor();
  });
})(window.WVG);
