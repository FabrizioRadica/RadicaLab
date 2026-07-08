/* AI Prompt Assistant UI controller (patch §6).
   Radica - WanVideoGenerator — Concept & Design: Fabrizio Radica — Project by RadicaDesign

   Talks to the real /api/ai-assistant endpoints. Never renders — it only
   produces prompts/plans and applies them to the existing Single Clip prompt
   fields or the existing SequenceQueue. */

window.WVGAIAssistant = (function () {
  "use strict";

  function el(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var S = {
    config: null,
    mode: "single_clip",
    plan: null,            // last generated SequencePlan (with edits applied)
    scResult: null,        // last SingleClipPromptResult
    preselectSequence: null,
  };

  /* ---------------- config + entry points ---------------- */
  function loadConfig() {
    return WVG.api("/api/ai-assistant/config").then(function (data) {
      S.config = data.config;
      if (window.WVGAudioFeedback && S.config.audio_feedback) {
        WVGAudioFeedback.setConfig(S.config.audio_feedback);
      }
      applyEntryVisibility();
      return S.config;
    }).catch(function () { S.config = null; });
  }

  function applyEntryVisibility() {
    var pa = (S.config && S.config.prompt_assistant) || {};
    var enabled = !!(pa.enabled);
    var single = el("ai-open-single-clip");
    var seq = el("ai-open-sequence");
    if (single) single.style.display = (enabled && pa.show_button_single_clip) ? "" : "none";
    if (seq) seq.style.display = (enabled && pa.show_button_sequence) ? "" : "none";
  }

  function setStatus(text) {
    var line = el("ai-status-line");
    if (line && text) line.textContent = text;
  }
  function showWarnings(list) {
    var box = el("ai-warn");
    if (!box) return;
    if (list && list.length) { box.innerHTML = list.map(esc).join("<br>"); box.style.display = ""; }
    else { box.innerHTML = ""; box.style.display = "none"; }
  }

  /* Report a successful generation, downgrading a resource-cleanup failure to a
     non-blocking warning — the generated prompts/plan are always kept (patch
     §10/§11). Cleanup warnings never play the error sound. */
  function reportSuccess(successMsg, release) {
    var warn = release && release.warning;
    if (warn) {
      setStatus(warn);
      WVG.toast(successMsg + " — AI resource cleanup reported a warning", "warning", warn);
      if (window.WVGAudioFeedback) WVGAudioFeedback.play_warning();
    } else {
      WVG.toast(successMsg, "success");
    }
  }

  /* ---------------- mode switching ---------------- */
  function setMode(mode) {
    S.mode = mode === "sequence" ? "sequence" : "single_clip";
    document.querySelectorAll("#ai-mode-tabs .mode-tab").forEach(function (t) {
      t.classList.toggle("active", t.dataset.aiMode === S.mode);
    });
    el("ai-pane-single").style.display = S.mode === "single_clip" ? "" : "none";
    el("ai-pane-sequence").style.display = S.mode === "sequence" ? "" : "none";
    el("ai-sc-actions").style.display = (S.mode === "single_clip" && S.scResult) ? "" : "none";
    el("ai-seq-actions").style.display = (S.mode === "sequence" && S.plan) ? "" : "none";
  }

  /* ---------------- open / close ---------------- */
  function open(mode, opts) {
    opts = opts || {};
    S.preselectSequence = opts.sequenceId || null;
    if (!S.config) {
      loadConfig().then(function () { openNow(mode); });
    } else { openNow(mode); }
  }
  function openNow(mode) {
    if (!S.config || !S.config.prompt_assistant.enabled) {
      WVG.toast("Enable the Prompt Assistant in Settings first.", "warning");
      return;
    }
    setStatus("AI Assistant idle");
    showWarnings(null);
    setMode(mode || S.config.prompt_assistant.default_mode || "single_clip");
    WVG.openModal("ai-assistant-backdrop");
    if (S.mode === "sequence") loadTargetSequences();
  }
  function close() { WVG.closeModal("ai-assistant-backdrop"); }

  /* ---------------- Single Clip ---------------- */
  function collectSingleBrief(only) {
    return {
      scene: el("ai-sc-scene").value,
      subject: el("ai-sc-subject").value,
      style: el("ai-sc-style").value,
      camera: el("ai-sc-camera").value,
      mood: el("ai-sc-mood").value,
      environment: el("ai-sc-env").value,
      only: only || null,
    };
  }

  async function generateSingle(only) {
    if (!el("ai-sc-scene").value.trim()) { WVG.toast("Describe the scene first.", "warning"); return; }
    var btn = el("ai-sc-generate");
    setStatus("AI Assistant generating prompt...");
    btn.disabled = true; btn.textContent = "Generating…";
    try {
      var data = await WVG.api("/api/ai-assistant/single-clip", { method: "POST", body: collectSingleBrief(only) });
      S.scResult = data.result;
      showWarnings(data.warnings);
      setStatus(data.status || "AI Assistant idle");
      renderSingleResult(only);
      reportSuccess("Prompts generated", data.release);
    } catch (e) {
      setStatus("AI Assistant idle");
      if (window.WVGAudioFeedback) WVGAudioFeedback.play_error();
      WVG.toast("Prompt generation failed", "error", e.message);
    } finally {
      btn.disabled = false; btn.textContent = "✨ Generate prompts";
    }
  }

  function renderSingleResult(only) {
    var r = S.scResult || {};
    el("ai-sc-result").style.display = "";
    el("ai-sc-actions").style.display = "";
    el("ai-sc-regen-pos").disabled = false;
    el("ai-sc-regen-neg").disabled = false;
    if (only !== "negative") el("ai-sc-positive").value = r.positive_prompt || "";
    if (only !== "positive") el("ai-sc-negative").value = r.negative_prompt || "";
    el("ai-sc-notes").textContent = r.notes || "";
    var rawDet = el("ai-sc-raw-details");
    if (r.parsed_json === false && r.raw) { el("ai-sc-raw").textContent = r.raw; rawDet.style.display = ""; }
    else { rawDet.style.display = "none"; }

    // Auto-apply on generation when enabled (patch §3.1 / §6.3).
    if (S.config && S.config.prompt_assistant.auto_apply_prompts) applyToClip("both", true);
  }

  function targetFields() {
    return { pos: document.getElementById("f-positive"), neg: document.getElementById("f-negative") };
  }

  function applyToClip(which, silent) {
    var f = targetFields();
    if (!f.pos || !f.neg) {
      WVG.toast("Open a project in Single Clip to apply prompts.", "warning");
      return;
    }
    var r = S.scResult || {};
    var auto = S.config && S.config.prompt_assistant.auto_apply_prompts;
    function setField(field, value) {
      if (value == null) return;
      if (!auto && !silent && field.value.trim() &&
          !window.confirm("Overwrite the existing prompt in this field?")) return;
      field.value = value;
      field.dispatchEvent(new Event("input", { bubbles: true }));
    }
    if (which === "positive" || which === "both") setField(f.pos, el("ai-sc-positive").value);
    if (which === "negative" || which === "both") setField(f.neg, el("ai-sc-negative").value);
    if (window.WVG && WVG.updateComposedPrompt) WVG.updateComposedPrompt();
    if (!silent) WVG.toast("Applied to Single Clip (remember to Save the project)", "success");
  }

  function copySingle() {
    var text = "Positive:\n" + el("ai-sc-positive").value + "\n\nNegative:\n" + el("ai-sc-negative").value;
    navigator.clipboard.writeText(text).then(
      function () { WVG.toast("Prompts copied", "success"); },
      function () { WVG.toast("Could not copy", "error"); });
  }

  /* ---------------- Sequence ---------------- */
  async function loadTargetSequences() {
    var sel = el("ai-seq-target");
    if (!sel) return;
    try {
      var data = await WVG.api("/api/sequences");
      var seqs = data.sequences || [];
      sel.innerHTML = "";
      if (!seqs.length) {
        var o = document.createElement("option");
        o.value = ""; o.textContent = "— no sequences (create one first) —"; sel.appendChild(o);
      }
      seqs.forEach(function (s) {
        var o = document.createElement("option");
        o.value = s.sequence_id;
        o.textContent = s.name + " (" + s.clips_total + " clips)";
        sel.appendChild(o);
      });
      if (S.preselectSequence) sel.value = S.preselectSequence;
    } catch (e) { WVG.toast("Could not load sequences", "error", e.message); }
  }

  function collectSequenceBrief() {
    return {
      description: el("ai-seq-desc").value,
      num_clips: parseInt(el("ai-seq-clips").value, 10) || null,
      clip_duration: parseFloat(el("ai-seq-dur").value) || null,
      total_duration: parseFloat(el("ai-seq-total").value) || null,
      clip_type: el("ai-seq-type").value,
      continuity: el("ai-seq-continuity").value,
      subject_identity: el("ai-seq-subject").value,
    };
  }

  async function generateSequence() {
    if (!el("ai-seq-desc").value.trim()) { WVG.toast("Describe the sequence first.", "warning"); return; }
    var btn = el("ai-seq-generate");
    setStatus("AI Assistant generating sequence plan...");
    btn.disabled = true; btn.textContent = "Generating…";
    try {
      var data = await WVG.api("/api/ai-assistant/sequence-plan", { method: "POST", body: collectSequenceBrief() });
      showWarnings(data.warnings);
      setStatus(data.status || "AI Assistant idle");
      S.plan = data.plan;
      if (data.parse_error) {
        el("ai-seq-preview").style.display = "";
        el("ai-seq-actions").style.display = "none";
        el("ai-seq-clip-list").innerHTML = "<p class='muted small'>The AI response was not machine-readable. See the raw response and retry.</p>";
        el("ai-seq-raw").textContent = data.plan.raw || "";
        el("ai-seq-raw-details").style.display = "";
        if (window.WVGAudioFeedback) WVGAudioFeedback.play_warning();
        WVG.toast("Could not parse the sequence plan — see raw response", "warning");
      } else {
        renderPlan();
        reportSuccess("Sequence plan generated (" + (S.plan.clips || []).length + " clips)", data.release);
      }
    } catch (e) {
      setStatus("AI Assistant idle");
      if (window.WVGAudioFeedback) WVGAudioFeedback.play_error();
      WVG.toast("Sequence planning failed", "error", e.message);
    } finally {
      btn.disabled = false; btn.textContent = "✨ Generate sequence plan";
    }
  }

  function renderPlan() {
    var p = S.plan || { clips: [] };
    el("ai-seq-preview").style.display = "";
    el("ai-seq-actions").style.display = "";
    el("ai-seq-title").textContent = p.sequence_title ? ("Generated: " + p.sequence_title) : "Generated sequence";
    var gbits = [];
    if (p.global_style) gbits.push("Style: " + p.global_style);
    if (p.global_negative_prompt) gbits.push("Global negative: " + p.global_negative_prompt);
    el("ai-seq-global").textContent = gbits.join("  •  ");
    var rawDet = el("ai-seq-raw-details");
    if (p.raw) { el("ai-seq-raw").textContent = p.raw; rawDet.style.display = ""; } else { rawDet.style.display = "none"; }

    var list = el("ai-seq-clip-list");
    list.innerHTML = (p.clips || []).map(function (c, i) {
      var typeBadge = "<span class='badge badge-soft'>" + (c.clip_type === "Image2Video" ? "I2V" : "T2V") + "</span>";
      var notes = [c.camera_notes && ("Camera: " + c.camera_notes), c.motion_notes && ("Motion: " + c.motion_notes),
                   c.continuity_notes && ("Continuity: " + c.continuity_notes)].filter(Boolean).join(" · ");
      return "<div class='ai-seq-clip' data-idx='" + i + "'>" +
        "<div class='ai-seq-clip-head'>#" + (i + 1) + " " + typeBadge +
          " <input type='text' class='ai-c-name' value='" + esc(c.clip_name) + "'>" +
          " <span class='small muted'>" + esc(c.duration_seconds) + "s</span>" +
          " <button class='btn btn-icon btn-sm ai-c-del' title='Remove clip'>🗑</button></div>" +
        "<textarea class='ai-c-pos' rows='2' placeholder='Positive prompt'>" + esc(c.positive_prompt) + "</textarea>" +
        "<textarea class='ai-c-neg' rows='1' placeholder='Negative prompt'>" + esc(c.negative_prompt) + "</textarea>" +
        (notes ? "<div class='small muted'>" + esc(notes) + "</div>" : "") +
        "</div>";
    }).join("");
    if (!(p.clips || []).length) list.innerHTML = "<p class='muted small'>No clips in the plan.</p>";

    list.querySelectorAll(".ai-c-del").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var idx = parseInt(btn.closest(".ai-seq-clip").dataset.idx, 10);
        syncPlanFromDom();
        S.plan.clips.splice(idx, 1);
        renderPlan();
      });
    });
  }

  function syncPlanFromDom() {
    if (!S.plan) return;
    document.querySelectorAll("#ai-seq-clip-list .ai-seq-clip").forEach(function (row) {
      var idx = parseInt(row.dataset.idx, 10);
      var c = S.plan.clips[idx];
      if (!c) return;
      c.clip_name = row.querySelector(".ai-c-name").value;
      c.positive_prompt = row.querySelector(".ai-c-pos").value;
      c.negative_prompt = row.querySelector(".ai-c-neg").value;
    });
  }

  async function addToQueue() {
    syncPlanFromDom();
    var sel = el("ai-seq-target");
    var seqId = sel && sel.value;
    if (!seqId) { WVG.toast("Select (or create) a target sequence first.", "warning"); return; }
    var mode = el("ai-seq-queue-mode").value;
    if (S.config && S.config.prompt_assistant.require_confirmation_sequence) {
      var verb = mode === "replace" ? "REPLACE all clips in" : "add " + (S.plan.clips || []).length + " clip(s) to";
      if (!window.confirm("Confirm: " + verb + " the selected SequenceQueue?")) return;
    }
    var btn = el("ai-seq-add");
    btn.disabled = true; btn.textContent = "Adding…";
    setStatus("AI Assistant adding clips to SequenceQueue...");
    try {
      var res = await WVG.api("/api/ai-assistant/sequences/" + seqId + "/populate", {
        method: "POST", body: { plan: S.plan, mode: mode },
      });
      setStatus("AI resources released");
      if (window.WVGAudioFeedback) WVGAudioFeedback.play_ai_sequence_created();
      WVG.toast("Added " + res.added + " clip(s) to the SequenceQueue", "success",
                (res.notes && res.notes.length) ? res.notes.join(" ") : null);
      // Refresh the sequence UI if it is loaded and showing this sequence.
      document.dispatchEvent(new CustomEvent("wvg:ai-sequence-populated", { detail: { sequence_id: seqId } }));
      close();
    } catch (e) {
      setStatus("AI Assistant idle");
      if (window.WVGAudioFeedback) WVGAudioFeedback.play_error();
      WVG.toast("Could not add clips", "error", e.message);
    } finally {
      btn.disabled = false; btn.textContent = "＋ Add clips to SequenceQueue";
    }
  }

  /* ---------------- init ---------------- */
  function bind() {
    document.querySelectorAll("#ai-mode-tabs .mode-tab").forEach(function (t) {
      t.addEventListener("click", function () { setMode(t.dataset.aiMode); });
    });
    var map = {
      "ai-sc-generate": function () { generateSingle(null); },
      "ai-sc-regen-pos": function () { generateSingle("positive"); },
      "ai-sc-regen-neg": function () { generateSingle("negative"); },
      "ai-sc-apply-pos": function () { applyToClip("positive"); },
      "ai-sc-apply-neg": function () { applyToClip("negative"); },
      "ai-sc-apply-both": function () { applyToClip("both"); },
      "ai-sc-copy": copySingle,
      "ai-seq-generate": generateSequence,
      "ai-seq-add": addToQueue,
    };
    Object.keys(map).forEach(function (id) {
      var node = el(id);
      if (node) node.addEventListener("click", map[id]);
    });
    // Entry buttons (may be present on the editor page).
    var single = el("ai-open-single-clip");
    if (single) single.addEventListener("click", function () { open("single_clip"); });
    var seq = el("ai-open-sequence");
    if (seq) seq.addEventListener("click", function () {
      var id = (window.WVGSequence && WVGSequence.currentId && WVGSequence.currentId()) || null;
      open("sequence", { sequenceId: id });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!el("ai-assistant-backdrop")) { loadConfig(); return; }  // settings page has no modal
    bind();
    loadConfig();
  });

  return { open: open, close: close, reloadConfig: loadConfig };
})();
