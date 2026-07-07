/* Shared, context-aware Generation Parameters module (patchSeq §3/§7/§9).

   Two responsibilities:

   1. Pure, DOM-agnostic helpers (snapFrames, samplerCompat, modelSamplingStatus)
      that Single Clip (project.js) and the sequence contexts both call, so the
      duration/Wan-frame rule, sampler-support validation and ModelSamplingSD3
      status are computed by ONE implementation (patchSeq §5/§9/§10/§11).

   2. WVGGenParams.mount(root, opts) — binds the partials/generation_parameters.html
      markup (data-generation-parameters + data-field, no global ids) to a state
      object for the VideoSequenceQueue Global and Clip-override contexts. It uses
      the same shared preset endpoint as Single Clip, so preset logic, model-profile
      detection and Turbo warnings are shared (patchSeq §6/§9). */

window.WVGGenParams = (function () {
  "use strict";

  var ACCEL_PROFILES = ["turbo", "lightning", "lightx2v", "fast"];

  // Fallback resolution list if project.js has not defined WVG.RES_PRESETS yet.
  var RES_FALLBACK = [
    { name: "SD Landscape", w: 640, h: 352 },
    { name: "HD Landscape (Wan 704p)", w: 1280, h: 704 },
    { name: "Full HD Landscape", w: 1920, h: 1088 },
    { name: "Portrait HD", w: 704, h: 1280 },
    { name: "Portrait Full HD", w: 1088, h: 1920 },
    { name: "Square", w: 1024, h: 1024 }
  ];

  function resPresets() {
    return (window.WVG && WVG.RES_PRESETS) || RES_FALLBACK;
  }

  function capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = s == null ? "" : s;
    return div.innerHTML;
  }

  /* ---- shared pure helpers (also used by Single Clip project.js) ---- */

  // Wan requires a frame count of 4k+1 — snap to the nearest valid value.
  function snapFrames(n) {
    n = Math.max(1, Math.min(1000, Math.round(n)));
    var r = (n - 1) % 4;
    if (r !== 0) n += (r >= 2 ? 4 - r : -r);
    return Math.max(1, Math.min(997, n));
  }

  // Honest per-backend sampler support (patch6c §9). Returns {level, text}.
  function samplerCompat(backendStatus, sampler, denoise) {
    var sup = backendStatus && backendStatus.sampling_support;
    if (!sup) return null;
    var direct = sup.direct_backend_samplers || sup.sampler_names || [];
    var comfy = sup.comfyui_samplers || [];
    var policy = sup.fallback_policy || "block";
    var comfyAvail = !!sup.comfyui_available;
    var directOk = direct.indexOf(sampler) !== -1;
    var comfyOk = comfy.indexOf(sampler) !== -1;
    var lines = [];
    var level = "muted";
    if (directOk) {
      lines.push('Sampler "' + sampler + '" — Direct backend: supported. Effective sampler: ' + sampler + ".");
    } else {
      var action;
      if (policy === "route_to_comfyui" && comfyAvail && comfyOk) {
        action = "will route to ComfyUI backend (sampler used exactly).";
      } else if (policy === "route_to_comfyui") {
        action = "route to ComfyUI requested, but ComfyUI is not available — generation will be BLOCKED."; level = "warning";
      } else if (policy === "allow_with_warning") {
        action = 'debug fallback: will render with "uni_pc" (visible in diagnostics).'; level = "warning";
      } else if (policy === "ask") {
        action = "you will be asked to confirm a fallback before rendering."; level = "warning";
      } else {
        action = "generation will be BLOCKED (no silent fallback). Pick a supported direct sampler or enable ComfyUI routing."; level = "warning";
      }
      lines.push('Sampler "' + sampler + '" — Direct backend: unsupported. ComfyUI backend: ' +
        (comfyOk ? "supported" : "unsupported") + ". Action: " + action);
    }
    if (denoise != null && Math.abs(denoise - 1) > 1e-6) {
      lines.push("Denoise " + denoise.toFixed(2) + " is not applied by the direct backend (full denoise 1.00) — preserved for ComfyUI export.");
    }
    return { level: level, text: (level === "warning" ? "⚠ " : "ⓘ ") + lines.join(" ") };
  }

  // ModelSamplingSD3 status (patch7 §13). Returns {warn, text}.
  function modelSamplingStatus(backendStatus, enabled, shift) {
    if (!enabled) {
      return { warn: false, text: "ⓘ ModelSamplingSD3 disabled — the model's default flow shift is used." };
    }
    var sup = backendStatus && backendStatus.model_sampling_support;
    var s = (shift == null ? 8 : shift).toFixed(2);
    if (!sup) return { warn: false, text: "ⓘ ModelSamplingSD3 enabled (shift " + s + ")." };
    if (sup.direct_backend_supported) {
      return { warn: false, text: "ⓘ ModelSamplingSD3 enabled (shift " + s + ") — applied by the direct backend as the flow-matching shift." };
    }
    if (sup.fallback_policy === "route_to_comfyui" && sup.comfyui_available) {
      return { warn: false, text: "ⓘ ModelSamplingSD3 enabled (shift " + s + ") — will route to ComfyUI backend." };
    }
    if (sup.fallback_policy === "block") {
      return { warn: true, text: "⚠ ModelSamplingSD3 enabled but the direct backend cannot apply it — generation will be BLOCKED. Enable ComfyUI routing or disable it." };
    }
    return { warn: false, text: "ⓘ ModelSamplingSD3 enabled (shift " + s + ")." };
  }

  /* ======================================================================
     Context-aware component
     ====================================================================== */
  function mount(root, opts) {
    if (!root) return null;
    opts = opts || {};
    var models = opts.models || [];
    var getMode = opts.getMode || function () { return "text2video"; };
    var onChange = opts.onChange || function () {};
    var suspended = true;  // don't emit changes while hydrating

    function q(sel) { return root.querySelector(sel); }
    function field(name) { return root.querySelector('[data-field="' + name + '"]'); }
    function roleEl(name) { return root.querySelector('[data-role="' + name + '"]'); }

    var durationMode = "custom";

    /* ---- populate static option lists ---- */
    function populateModels() {
      var sel = field("model_id");
      if (!sel) return;
      sel.innerHTML = "";
      models.forEach(function (m) {
        var o = document.createElement("option");
        o.value = m.id;
        o.textContent = m.display_name + (m.status && m.status !== "ok" ? " [" + m.status + "]" : "");
        sel.appendChild(o);
      });
    }

    function populateResolution() {
      var sel = field("resolution");
      if (!sel) return;
      sel.innerHTML = "";
      var seen = {};
      resPresets().forEach(function (p) {
        var key = p.w + "x" + p.h;
        if (seen[key]) return;
        seen[key] = true;
        var o = document.createElement("option");
        o.value = key;
        o.textContent = p.name + " — " + p.w + "×" + p.h;
        sel.appendChild(o);
      });
      var custom = document.createElement("option");
      custom.value = "custom"; custom.textContent = "Custom…";
      sel.appendChild(custom);
    }

    function updateVramHint() {
      var hint = roleEl("vram-hint");
      var sel = field("model_id");
      if (!hint || !sel) return;
      var m = models.find(function (x) { return x.id === sel.value; });
      hint.textContent = m && m.recommended_vram_gb ? "VRAM ~" + m.recommended_vram_gb + " GB" : "";
    }

    /* ---- resolution select <-> width/height ---- */
    function syncResolutionSelect() {
      var sel = field("resolution");
      var w = parseInt((field("width") || {}).value, 10);
      var h = parseInt((field("height") || {}).value, 10);
      var key = w + "x" + h, matched = false;
      if (sel) {
        for (var i = 0; i < sel.options.length; i++) {
          if (sel.options[i].value === key) { sel.value = key; matched = true; break; }
        }
        if (!matched) sel.value = "custom";
      }
      var row = roleEl("custom-res-row");
      if (row) row.style.display = matched ? "none" : "flex";
    }

    /* ---- sliders ---- */
    function slidersInit() {
      root.querySelectorAll('.slider-row input[type="range"][data-field]').forEach(function (input) {
        input.addEventListener("input", function () { updateSliderFill(input); });
      });
    }
    function updateSliderFill(input) {
      var min = parseFloat(input.min || 0), max = parseFloat(input.max || 100);
      var pct = ((parseFloat(input.value) - min) / (max - min)) * 100;
      input.style.setProperty("--fill", pct + "%");
      var lbl = root.querySelector('[data-value-for="' + input.dataset.field + '"]');
      if (lbl) {
        var dec = input.dataset.field === "guidance_scale" ? 1 : 2;
        lbl.textContent = Number(input.value).toFixed(dec);
      }
    }
    function refreshSliders() {
      root.querySelectorAll('.slider-row input[type="range"][data-field]').forEach(updateSliderFill);
    }

    /* ---- duration <-> frames <-> fps ---- */
    function updateDurationEstimate() {
      var out = roleEl("duration-estimate");
      if (!out) return;
      var frames = parseInt((field("frames") || {}).value, 10);
      var fps = parseInt((field("fps") || {}).value, 10);
      if (frames >= 1 && fps >= 1) {
        var d = frames / fps;
        out.textContent = (Math.round(d * 100) / 100) + "s  (" + frames + " frames @ " + fps + " fps)";
      } else out.textContent = "—";
    }
    function markDurationPreset() {
      root.querySelectorAll('[data-role="duration-presets"] .duration-btn').forEach(function (b) {
        var val = b.dataset.seconds;
        var active = (durationMode === "custom" && val === "custom") ||
          (durationMode !== "custom" && val !== "custom" && parseFloat(val) === durationMode);
        b.classList.toggle("active", active);
      });
    }
    function applyDurationPreset(seconds) {
      var fps = parseInt((field("fps") || {}).value, 10) || 24;
      field("frames").value = snapFrames(seconds * fps);
      updateDurationEstimate();
    }
    function detectDurationMode() {
      var frames = parseInt((field("frames") || {}).value, 10);
      var fps = parseInt((field("fps") || {}).value, 10);
      durationMode = "custom";
      if (frames >= 1 && fps >= 1) {
        var d = frames / fps;
        [2, 3, 5, 10].forEach(function (p) { if (Math.abs(d - p) * fps <= 2) durationMode = p; });
      }
      markDurationPreset();
    }

    /* ---- heavy-render warning ---- */
    function checkHeavy() {
      var el0 = roleEl("heavy-warning");
      if (!el0) return;
      var w = parseInt((field("width") || {}).value, 10) || 0;
      var h = parseInt((field("height") || {}).value, 10) || 0;
      var fr = parseInt((field("frames") || {}).value, 10) || 0;
      if (fr >= 121 && Math.max(w, h) >= 1280) {
        el0.textContent = "⚠ Very heavy render: high resolution with " + fr +
          " frames. Expect much longer render time. Consider 81 or 49 frames for testing.";
        el0.style.display = "";
      } else el0.style.display = "none";
    }

    /* ---- sampler compat + model sampling status ---- */
    function refreshSamplerCompat() {
      var el0 = roleEl("sampling-compat-warning");
      if (!el0) return;
      var r = samplerCompat(window.WVG && WVG.backendStatus,
        (field("sampler_name") || {}).value, parseFloat((field("denoise") || {}).value));
      if (!r) { el0.style.display = "none"; return; }
      el0.textContent = r.text;
      el0.style.color = r.level === "warning" ? "var(--warning)" : "var(--muted, #9aa)";
      el0.style.display = "";
    }
    function refreshModelSamplingStatus() {
      var el0 = roleEl("model-sampling-status");
      if (!el0) return;
      var enabled = (field("model_sampling_enabled") || {}).checked;
      var shift = parseFloat((field("model_sampling_shift_num") || {}).value || "8");
      var r = modelSamplingStatus(window.WVG && WVG.backendStatus, enabled, shift);
      el0.textContent = r.text;
      el0.style.color = r.warn ? "var(--warning)" : "var(--muted, #9aa)";
      el0.style.display = "";
    }

    /* ---- preset catalogue + apply (shared endpoint) ---- */
    function populatePresetSelect(defs, profile) {
      var sel = field("wan_preset");
      if (!sel) return;
      var accel = ACCEL_PROFILES.indexOf(profile) >= 0;
      var manual = [], groups = {}, order = [];
      (defs || []).forEach(function (d) {
        if (d.manual) { manual.push(d); return; }
        var g = d.group || "Presets";
        if (!groups[g]) { groups[g] = []; order.push(g); }
        groups[g].push(d);
      });
      order.sort(function (a, b) {
        var ta = /turbo/i.test(a) ? 1 : 0, tb = /turbo/i.test(b) ? 1 : 0;
        return accel ? (tb - ta) : (ta - tb);
      });
      function opt(d) { var o = document.createElement("option"); o.value = d.id; o.textContent = d.label; return o; }
      var want = sel.value || "manual";
      sel.innerHTML = "";
      manual.forEach(function (d) { sel.appendChild(opt(d)); });
      order.forEach(function (g) {
        var og = document.createElement("optgroup"); og.label = g;
        groups[g].forEach(function (d) { og.appendChild(opt(d)); });
        sel.appendChild(og);
      });
      sel.value = want;
      if (sel.value !== want) sel.value = "manual";
    }

    async function loadPresetCatalog() {
      var recEl = roleEl("preset-recommend");
      var profEl = roleEl("preset-profile");
      try {
        var modelId = (field("model_id") || {}).value || "";
        var data = await WVG.api("/api/presets?model_id=" + encodeURIComponent(modelId));
        var profile = data.detected_model_profile;
        populatePresetSelect(data.presets, profile);
        if (profEl && profile) {
          var accel = ACCEL_PROFILES.indexOf(profile) >= 0;
          profEl.textContent = "Detected model profile: " + capitalize(profile);
          profEl.style.color = accel ? "var(--warning)" : "var(--muted, #9aa)";
          profEl.style.display = "";
        } else if (profEl) profEl.style.display = "none";
        var r = data.recommendation || {};
        var labelById = {};
        (data.presets || []).forEach(function (p) { labelById[p.id] = p.label; });
        if (recEl && r.recommended) {
          recEl.textContent = "Recommended: " + (labelById[r.recommended] || r.recommended) +
            (r.reason ? " — " + r.reason : "");
        }
      } catch (e) { /* catalogue is optional */ }
    }

    function applyTarget(t) {
      if (!t) return;
      if (t.orientation && field("orientation")) field("orientation").value = t.orientation;
      field("width").value = t.resolution.width;
      field("height").value = t.resolution.height;
      syncResolutionSelect();
      field("frames").value = t.frames;
      field("fps").value = t.fps;
      field("steps").value = t.steps;
      setSelectValue(field("sampler_name"), t.sampler_name);
      setSelectValue(field("scheduler"), t.scheduler);
      field("guidance_scale").value = t.guidance_scale;
      field("denoise").value = t.denoise;
      if (field("model_sampling_enabled")) {
        field("model_sampling_enabled").checked = !!t.model_sampling.enabled;
        field("model_sampling_shift").value = t.model_sampling.shift;
        field("model_sampling_shift_num").value = Number(t.model_sampling.shift).toFixed(2);
      }
      if (field("memory_optimization") && typeof t.memory_optimization !== "undefined")
        field("memory_optimization").checked = !!t.memory_optimization;
      if (field("model_offload") && typeof t.model_offload !== "undefined")
        field("model_offload").checked = !!t.model_offload;
      if (field("unload_model_after_generation"))
        field("unload_model_after_generation").checked = !!t.unload_model_after_generation;
      detectDurationMode();
      updateDurationEstimate();
      refreshSliders();
      checkHeavy();
      refreshSamplerCompat();
      refreshModelSamplingStatus();
    }

    function showPresetChanges(res) {
      var box = roleEl("preset-changes");
      if (!box) return;
      if (!res || res.preset === "manual") { box.style.display = "none"; return; }
      var html = "<strong>Preset applied:</strong>";
      if (res.changes && res.changes.length) {
        html += "<ul style='margin:4px 0 0;padding-left:16px;'>";
        res.changes.forEach(function (c) { html += "<li>" + escapeHtml(c) + "</li>"; });
        html += "</ul>";
      } else html += " <span class='muted'>no changes (already matching).</span>";
      (res.warnings || []).forEach(function (w) {
        var color = w.level === "info" ? "var(--muted, #9aa)" : "var(--warning)";
        var icon = w.level === "info" ? "ⓘ" : "⚠";
        html += "<div style='color:" + color + ";margin-top:4px;'>" + icon + " " + escapeHtml(w.message) + "</div>";
      });
      box.innerHTML = html;
      box.style.display = "";
    }

    async function applyWanPreset(presetId) {
      var box = roleEl("preset-changes");
      if (presetId === "manual") { if (box) box.style.display = "none"; emit(); return; }
      try {
        var res = await WVG.api("/api/presets/compute", {
          method: "POST",
          body: {
            preset_id: presetId,
            mode: getMode(),
            orientation: (field("orientation") || {}).value || "landscape",
            model_id: (field("model_id") || {}).value || "",
            current: collect()
          }
        });
        if (res.requires_confirmation) {
          var blocking = (res.warnings || []).filter(function (w) { return w.level === "blocking"; })
            .map(function (w) { return w.message; }).join("\n\n");
          if (!window.confirm(blocking + "\n\nApply this preset anyway?")) {
            field("wan_preset").value = "manual";
            return;
          }
        }
        applyTarget(res.target);
        showPresetChanges(res);
        emit();
        WVG.toast("Preset applied — review changes", "success");
      } catch (e) {
        WVG.toast("Could not apply preset", "error", e.message);
      }
    }

    function setSelectValue(selectEl, value) {
      if (!selectEl) return;
      selectEl.value = value;
      if (selectEl.value !== value) {
        var opt = document.createElement("option");
        opt.value = value; opt.textContent = value + " (custom)";
        selectEl.appendChild(opt);
        selectEl.value = value;
      }
    }

    /* ---- collect / hydrate ---- */
    function collect() {
      var randomSeed = (field("random_seed") || {}).checked;
      return {
        model_id: (field("model_id") || {}).value || "",
        wan_preset: (field("wan_preset") || {}).value || "manual",
        orientation: (field("orientation") || {}).value || "landscape",
        width: parseInt((field("width") || {}).value, 10),
        height: parseInt((field("height") || {}).value, 10),
        frames: parseInt((field("frames") || {}).value, 10),
        fps: parseInt((field("fps") || {}).value, 10),
        seed: parseInt((field("seed") || {}).value, 10),
        seed_mode: randomSeed ? "random" : "fixed",
        control_after_generate: (field("control_after_generate") || {}).value || "fixed",
        guidance_scale: parseFloat((field("guidance_scale") || {}).value),
        steps: parseInt((field("steps") || {}).value, 10),
        sampler_name: (field("sampler_name") || {}).value || "euler",
        scheduler: (field("scheduler") || {}).value || "simple",
        denoise: parseFloat((field("denoise") || {}).value),
        model_sampling_enabled: (field("model_sampling_enabled") || {}).checked,
        model_sampling_shift: parseFloat((field("model_sampling_shift_num") || {}).value) || 8.0,
        precision: (field("precision") || {}).value || "bf16",
        device: (field("device") || {}).value || "cuda",
        memory_optimization: (field("memory_optimization") || {}).checked,
        model_offload: (field("model_offload") || {}).checked,
        save_intermediate_frames: (field("save_intermediate_frames") || {}).checked,
        unload_model_after_generation: (field("unload_model_after_generation") || {}).checked
      };
    }

    function hydrate(state) {
      suspended = true;
      state = state || {};
      function setF(name, val) { var e = field(name); if (e != null && val != null) e.value = val; }
      function setC(name, val) { var e = field(name); if (e) e.checked = !!val; }
      setF("model_id", state.model_id);
      setF("orientation", state.orientation);
      setF("width", state.width);
      setF("height", state.height);
      setF("frames", state.frames);
      setF("fps", state.fps);
      setF("seed", state.seed);
      setC("random_seed", state.seed_mode ? state.seed_mode !== "fixed" : state.random_seed !== false);
      setF("control_after_generate", state.control_after_generate || "fixed");
      setF("guidance_scale", state.guidance_scale);
      setF("steps", state.steps);
      setSelectValue(field("sampler_name"), state.sampler_name || "euler");
      setSelectValue(field("scheduler"), state.scheduler || "simple");
      setF("denoise", state.denoise == null ? 1 : state.denoise);
      setC("model_sampling_enabled", state.model_sampling_enabled);
      var shift = state.model_sampling_shift == null ? 8.0 : state.model_sampling_shift;
      setF("model_sampling_shift", shift);
      setF("model_sampling_shift_num", Number(shift).toFixed(2));
      setF("precision", state.precision || "bf16");
      setF("device", state.device || "cuda");
      setC("memory_optimization", state.memory_optimization);
      setC("model_offload", state.model_offload);
      setC("save_intermediate_frames", state.save_intermediate_frames);
      setC("unload_model_after_generation", state.unload_model_after_generation);
      if (field("wan_preset")) field("wan_preset").value = state.wan_preset || "manual";

      syncResolutionSelect();
      refreshSliders();
      detectDurationMode();
      updateDurationEstimate();
      updateVramHint();
      checkHeavy();
      refreshSamplerCompat();
      refreshModelSamplingStatus();
      suspended = false;
    }

    function emit() { if (!suspended) onChange(collect()); }

    /* ---- wiring ---- */
    function bindEvents() {
      // Preset select
      var presetSel = field("wan_preset");
      if (presetSel) presetSel.addEventListener("change", function () { applyWanPreset(presetSel.value); });

      // Model change -> vram hint + reload preset catalogue
      var modelSel = field("model_id");
      if (modelSel) modelSel.addEventListener("change", function () {
        updateVramHint(); loadPresetCatalog(); emit();
      });

      // Resolution select -> width/height
      var resSel = field("resolution");
      if (resSel) resSel.addEventListener("change", function () {
        var row = roleEl("custom-res-row");
        if (resSel.value === "custom") { if (row) row.style.display = "flex"; }
        else {
          var parts = resSel.value.split("x");
          field("width").value = parseInt(parts[0], 10);
          field("height").value = parseInt(parts[1], 10);
          if (row) row.style.display = "none";
          checkHeavy(); emit();
        }
      });
      ["width", "height"].forEach(function (name) {
        var e = field(name);
        if (e) e.addEventListener("input", function () { syncResolutionSelect(); checkHeavy(); emit(); });
      });

      // Duration presets
      root.querySelectorAll('[data-role="duration-presets"] .duration-btn').forEach(function (btn) {
        btn.addEventListener("click", function () {
          if (btn.dataset.seconds === "custom") durationMode = "custom";
          else { durationMode = parseFloat(btn.dataset.seconds); applyDurationPreset(durationMode); }
          markDurationPreset(); checkHeavy(); emit();
        });
      });
      var fpsEl = field("fps");
      if (fpsEl) fpsEl.addEventListener("input", function () {
        if (durationMode !== "custom") applyDurationPreset(durationMode);
        else updateDurationEstimate();
        emit();
      });
      var framesEl = field("frames");
      if (framesEl) framesEl.addEventListener("input", function () {
        durationMode = "custom"; markDurationPreset(); updateDurationEstimate(); checkHeavy(); emit();
      });

      // Sliders (value display handled by slidersInit) -> emit + dependent UI
      var guid = field("guidance_scale");
      if (guid) guid.addEventListener("input", emit);
      var den = field("denoise");
      if (den) den.addEventListener("input", function () { refreshSamplerCompat(); emit(); });

      // Sampler / scheduler
      ["sampler_name", "scheduler"].forEach(function (name) {
        var e = field(name);
        if (e) e.addEventListener("change", function () { refreshSamplerCompat(); emit(); });
      });

      // Model sampling slider <-> number
      var msSlider = field("model_sampling_shift");
      var msNum = field("model_sampling_shift_num");
      var msEnabled = field("model_sampling_enabled");
      if (msSlider && msNum) {
        msSlider.addEventListener("input", function () {
          msNum.value = Number(msSlider.value).toFixed(2); refreshModelSamplingStatus(); emit();
        });
        msNum.addEventListener("input", function () {
          var v = parseFloat(msNum.value);
          if (!isNaN(v) && v <= 20) msSlider.value = v;
          refreshModelSamplingStatus(); emit();
        });
      }
      if (msEnabled) msEnabled.addEventListener("change", function () { refreshModelSamplingStatus(); emit(); });

      // Generic emit for the remaining simple fields
      ["orientation", "seed", "random_seed", "control_after_generate", "steps",
       "precision", "device", "memory_optimization", "model_offload",
       "save_intermediate_frames", "unload_model_after_generation"].forEach(function (name) {
        var e = field(name);
        if (e) e.addEventListener("change", emit);
      });

      document.addEventListener("wvg:backend-status", function () {
        refreshSamplerCompat(); refreshModelSamplingStatus();
      });
    }

    // init
    populateModels();
    populateResolution();
    slidersInit();
    bindEvents();
    loadPresetCatalog();

    return { hydrate: hydrate, collect: collect, root: root, context: opts.context };
  }

  return {
    mount: mount,
    snapFrames: snapFrames,
    samplerCompat: samplerCompat,
    modelSamplingStatus: modelSamplingStatus
  };
})();
