/* VideoSequenceQueue UI controller (patchRC2).
   Talks to the real /api/sequences endpoints; never fakes queue behavior. */

window.WVG = window.WVG || {};

(function (WVG) {
  "use strict";

  function el(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var S = { seq: null, models: [], poll: null, editing: null, gpGlobal: null, gpClip: null,
            clGlobal: null, clClip: null, audMaster: null, audClip: null };

  function audioMediaUrl(t) {
    return "/media/sequences/" + S.seq.sequence_id + "/asset/audio/" + encodeURIComponent(Path_basename(t.filename));
  }
  function Path_basename(p) { return String(p || "").split(/[\\/]/).pop(); }

  var STATUS_LABEL = {
    ready: "Ready", queued: "Queued", rendering: "Rendering…", completed: "Completed",
    failed: "Failed", cancel_requested: "Cancelling…", cancelled: "Cancelled",
    stopped: "Stopped", needs_regeneration: "Needs regeneration", skipped: "Skipped",
  };

  /* ---------------- mode switch ---------------- */
  function initModeSwitch() {
    var buttons = document.querySelectorAll(".mode-switch-bar .mode-switch-btn");
    if (!buttons.length) return;
    function setMode(mode) {
      buttons.forEach(function (b) { b.classList.toggle("active", b.dataset.mode === mode); });
      var single = el("mode-single"), sequence = el("mode-sequence");
      if (single) single.style.display = mode === "single" ? "" : "none";
      if (sequence) sequence.style.display = mode === "sequence" ? "" : "none";
      localStorage.setItem("wvg:mode", mode);
      if (mode === "sequence" && !S.loaded) { S.loaded = true; loadList(true); }
    }
    buttons.forEach(function (b) {
      b.addEventListener("click", function () { setMode(b.dataset.mode); });
    });
    setMode(localStorage.getItem("wvg:mode") || "single");
  }

  /* ---------------- sequence list / selection ---------------- */
  async function loadList(selectFirst) {
    try {
      var data = await WVG.api("/api/sequences");
      var seqs = data.sequences || [];
      var sel = el("seq-select");
      sel.innerHTML = "";
      if (!seqs.length) {
        var opt = document.createElement("option");
        opt.value = ""; opt.textContent = "— no sequences —"; sel.appendChild(opt);
      }
      seqs.forEach(function (s) {
        var o = document.createElement("option");
        o.value = s.sequence_id;
        o.textContent = s.name + " (" + s.clips_completed + "/" + s.clips_total + ")";
        sel.appendChild(o);
      });
      renderLibrary(seqs);
      var wanted = S.seq ? S.seq.sequence_id : (selectFirst && seqs.length ? seqs[0].sequence_id : "");
      if (wanted) { sel.value = wanted; await selectSequence(wanted); }
      else { S.seq = null; renderAll(); }
    } catch (e) { WVG.toast("Could not load sequences", "error", e.message); }
  }

  function renderLibrary(seqs) {
    var box = el("seq-library-list");
    if (!box) return;
    if (!seqs.length) { box.innerHTML = "<p class='muted small' style='padding:8px;'>No sequences yet.</p>"; return; }
    box.innerHTML = seqs.map(function (s) {
      var badge = s.final_output ? "<span class='badge badge-accent'>final ready</span>" : "";
      return "<div class='seq-lib-card' data-id='" + s.sequence_id + "'>" +
        "<div class='seq-lib-title'>" + esc(s.name) + " " + badge + "</div>" +
        "<div class='small muted'>" + s.clips_completed + "/" + s.clips_total + " clips · " +
        esc(s.status) + "</div></div>";
    }).join("");
    box.querySelectorAll(".seq-lib-card").forEach(function (c) {
      c.addEventListener("click", function () {
        el("seq-select").value = c.dataset.id;
        selectSequence(c.dataset.id);
      });
    });
  }

  async function selectSequence(id) {
    stopPolling();
    if (!id) { S.seq = null; renderAll(); return; }
    try {
      S.seq = await WVG.api("/api/sequences/" + id);
      renderAll();
      if (S.seq.render_state && S.seq.render_state.status === "rendering") startPolling();
    } catch (e) { WVG.toast("Could not load sequence", "error", e.message); }
  }

  /* ---------------- render whole UI from S.seq ---------------- */
  function renderAll() {
    var has = !!S.seq;
    el("seq-body").style.display = has ? "" : "none";
    el("seq-queue-empty").style.display = has ? "none" : "";
    el("seq-queue-body").style.display = has ? "" : "none";
    if (!has) return;
    var s = S.seq;
    el("seq-name").value = s.name || "";
    el("seq-output-mode").value = s.output_mode;
    el("seq-vram-mode").value = s.vram_mode;
    el("seq-continue-on-error").checked = !!s.continue_on_error;
    renderGlobalGen();
    var look = ensureGlobalLook();
    if (look) look.hydrate(s.global_color_look);
    var mAudio = ensureMasterAudio();
    if (mAudio) mAudio.hydrate(s.sequence_audio_tracks || []);
    renderClips();
    renderStatus(s.render_state, s);
  }

  /* Global Color & Look: the SAME shared module used by Single Clip, mounted in
     the "sequence_global" context (patchReuseColoAudio §5/§6). */
  function ensureGlobalLook() {
    if (S.clGlobal) return S.clGlobal;
    var root = document.querySelector('[data-color-look-context="sequence_global"]');
    if (!root || !window.WVGColorLook) return null;
    S.clGlobal = WVGColorLook.mount(root, {
      context: "sequence_global",
      state: S.seq.global_color_look,
      onChange: function (fx) { S.seq.global_color_look = fx; saveSettings(); },
      previewSource: firstClipPreviewSource,
    });
    return S.clGlobal;
  }

  function firstClipPreviewSource() {
    var clip = (S.seq.clips || []).find(function (c) { return c.outputs && c.outputs.preview; });
    if (!clip) return null;
    return { url: "/media/sequences/" + S.seq.sequence_id + "/clip/" + clip.clip_id + "/preview",
             label: "Frame from " + clip.name };
  }

  /* Sequence Audio Tracks: the SAME shared module, mounted in "sequence_master"
     context — applied after final merge (patchReuseColoAudio §8/§9). */
  function ensureMasterAudio() {
    if (S.audMaster) return S.audMaster;
    var root = document.querySelector('[data-audio-tracks-context="sequence_master"]');
    if (!root || !window.WVGAudioTracks) return null;
    S.audMaster = WVGAudioTracks.mount(root, {
      context: "sequence_master",
      getMediaUrl: audioMediaUrl,
      onUpdate: function (id, patch) {
        return WVG.api("/api/sequences/" + S.seq.sequence_id + "/audio/" + id, { method: "PUT", body: patch });
      },
      onRemove: function (id) {
        return WVG.api("/api/sequences/" + S.seq.sequence_id + "/audio/" + id, { method: "DELETE" });
      },
      onUpload: function (file) {
        var fd = new FormData(); fd.append("file", file);
        return WVG.api("/api/sequences/" + S.seq.sequence_id + "/assets/audio", { method: "POST", body: fd });
      },
      reload: async function () {
        S.seq = await WVG.api("/api/sequences/" + S.seq.sequence_id);
        return S.seq.sequence_audio_tracks || [];
      },
    });
    return S.audMaster;
  }

  /* Global Generation Parameters: the SAME shared module used by Single Clip,
     mounted in the "sequence_global" context (patchSeq §3/§5/§7). */
  function ensureGlobalModule() {
    if (S.gpGlobal) return S.gpGlobal;
    var root = document.querySelector('[data-generation-parameters="sequence_global"]');
    if (!root || !window.WVGGenParams) return null;
    S.gpGlobal = WVGGenParams.mount(root, {
      context: "sequence_global",
      models: S.models,
      getMode: function () { return "text2video"; },
      onChange: function () { saveSettings(); },
    });
    return S.gpGlobal;
  }

  function renderGlobalGen() {
    var mod = ensureGlobalModule();
    if (mod) mod.hydrate(S.seq.global_generation_settings);
    var neg = el("seq-g-negative");
    if (neg) neg.value = S.seq.global_generation_settings.negative_prompt || "";
  }

  function collectGlobalGen() {
    var g = S.gpGlobal ? S.gpGlobal.collect() : {};
    g.negative_prompt = (el("seq-g-negative") || {}).value || "";
    return g;
  }

  var saveTimer = null;
  function saveSettings() {
    if (!S.seq) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async function () {
      try {
        var body = {
          name: el("seq-name").value.trim() || S.seq.name,
          output_mode: el("seq-output-mode").value,
          vram_mode: el("seq-vram-mode").value,
          continue_on_error: el("seq-continue-on-error").checked,
          global_generation_settings: collectGlobalGen(),
          global_color_look: S.seq.global_color_look,
        };
        S.seq = await WVG.api("/api/sequences/" + S.seq.sequence_id, { method: "PUT", body: body });
      } catch (e) { WVG.toast("Could not save sequence", "error", e.message); }
    }, 400);
  }

  /* ---------------- clip cards ---------------- */
  function renderClips() {
    var box = el("seq-clip-list");
    var clips = S.seq.clips || [];
    if (!clips.length) { box.innerHTML = "<p class='muted small' style='padding:10px;'>No clips yet — add an Image Reference or Prompt Only clip.</p>"; return; }
    box.innerHTML = clips.map(function (c, i) { return clipCard(c, i, clips.length); }).join("");
    box.querySelectorAll("[data-act]").forEach(function (btn) {
      btn.addEventListener("click", function () { clipAction(btn.dataset.act, btn.dataset.clip); });
    });
  }

  function clipCard(c, i, n) {
    var look = c.color_look_mode === "off" ? "Look: Off" :
      (c.color_look_mode === "custom" ? "Look: Custom" : "Look: Global");
    var audio = (c.clip_audio_tracks && c.clip_audio_tracks.filter(function (t) { return t.enabled; }).length)
      ? "Clip Audio: Custom" : "Clip Audio: Off";
    var thumb = c.outputs && c.outputs.preview
      ? "<img src='/media/sequences/" + S.seq.sequence_id + "/clip/" + c.clip_id + "/preview' class='seq-clip-thumb'>"
      : (c.type === "image_reference" && c.source_image
        ? "<img src='/media/sequences/" + S.seq.sequence_id + "/asset/image/" + encodeURIComponent(c.source_image) + "' class='seq-clip-thumb'>"
        : "<div class='seq-clip-thumb placeholder'>" + (i + 1) + "</div>");
    var prog = c.status === "rendering"
      ? "<div class='progress-bar' style='margin-top:6px;'><div class='fill' style='width:" + (c.progress || 0) + "%;'></div></div>" : "";
    var badge = "<span class='badge status-" + c.status + "'>" + (STATUS_LABEL[c.status] || c.status) + "</span>";
    var typeBadge = "<span class='badge badge-soft'>" + (c.type === "image_reference" ? "I2V" : "T2V") + "</span>";
    var err = c.last_error ? "<div class='small' style='color:var(--danger);'>" + esc(c.last_error) + "</div>" : "";
    var playBtn = (c.outputs && c.outputs.final)
      ? "<button class='btn btn-xs' data-act='play' data-clip='" + c.clip_id + "'>▶ Final</button>" : "";
    return "<div class='seq-clip-card'>" +
      "<div class='seq-clip-head'>" + thumb +
        "<div class='seq-clip-meta'>" +
          "<div class='seq-clip-title'>#" + (i + 1) + " " + esc(c.name) + " " + typeBadge + " " + badge + "</div>" +
          "<div class='small muted seq-clip-prompt'>" + esc((c.prompt || "").slice(0, 90) || "(no prompt)") + "</div>" +
          "<div class='small muted'>" + esc(look) + " · " + esc(audio) + "</div>" +
          err + prog +
        "</div>" +
      "</div>" +
      "<div class='seq-clip-actions'>" +
        "<button class='btn btn-xs' data-act='edit' data-clip='" + c.clip_id + "'>Edit</button>" +
        "<button class='btn btn-xs' data-act='look' data-clip='" + c.clip_id + "'>Look</button>" +
        "<button class='btn btn-xs' data-act='audio' data-clip='" + c.clip_id + "'>Audio</button>" +
        "<button class='btn btn-xs' data-act='dup' data-clip='" + c.clip_id + "'>Duplicate</button>" +
        "<button class='btn btn-xs' data-act='up' data-clip='" + c.clip_id + "'" + (i === 0 ? " disabled" : "") + ">↑</button>" +
        "<button class='btn btn-xs' data-act='down' data-clip='" + c.clip_id + "'" + (i === n - 1 ? " disabled" : "") + ">↓</button>" +
        "<button class='btn btn-xs' data-act='regen' data-clip='" + c.clip_id + "'>Regenerate</button>" +
        "<button class='btn btn-xs' data-act='resume' data-clip='" + c.clip_id + "'>Resume here</button>" +
        "<button class='btn btn-xs' data-act='skip' data-clip='" + c.clip_id + "'>Skip</button>" +
        playBtn +
        "<button class='btn btn-xs btn-danger' data-act='del' data-clip='" + c.clip_id + "'>Delete</button>" +
      "</div></div>";
  }

  async function clipAction(act, clipId) {
    var sid = S.seq.sequence_id;
    var clip = (S.seq.clips || []).find(function (c) { return c.clip_id === clipId; });
    try {
      if (act === "edit") return openClipModal(clip, null);
      if (act === "look") return openClipModal(clip, "clip-look");
      if (act === "audio") return openClipModal(clip, "clip-audio");
      if (act === "play") return playClipFinal(clip);
      if (act === "del") {
        if (!confirm("Delete clip '" + clip.name + "'?")) return;
        await WVG.api("/api/sequences/" + sid + "/clips/" + clipId, { method: "DELETE" });
      } else if (act === "dup") {
        await WVG.api("/api/sequences/" + sid + "/clips/" + clipId + "/duplicate", { method: "POST" });
      } else if (act === "up" || act === "down") {
        var ids = S.seq.clips.map(function (c) { return c.clip_id; });
        var idx = ids.indexOf(clipId), swap = act === "up" ? idx - 1 : idx + 1;
        if (swap < 0 || swap >= ids.length) return;
        ids[idx] = ids[swap]; ids[swap] = clipId;
        await WVG.api("/api/sequences/" + sid + "/clips/reorder", { method: "POST", body: { clip_ids: ids } });
      } else if (act === "regen") {
        await WVG.api("/api/sequences/" + sid + "/clips/" + clipId + "/regenerate", { method: "POST" });
        WVG.toast("Regenerating clip…", "success"); startPolling();
      } else if (act === "resume") {
        await WVG.api("/api/sequences/" + sid + "/resume-from/" + clipId, { method: "POST" });
        WVG.toast("Resuming from clip…", "success"); startPolling();
      } else if (act === "skip") {
        await WVG.api("/api/sequences/" + sid + "/clips/" + clipId + "/skip", { method: "POST" });
      }
      await selectSequence(sid);
    } catch (e) { WVG.toast("Action failed", "error", e.message); }
  }

  function playClipFinal(clip) {
    var url = "/media/sequences/" + S.seq.sequence_id + "/clip/" + clip.clip_id + "/final";
    el("seq-final-output").style.display = "";
    el("seq-final-video").src = url;
    el("seq-final-video").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  /* ---------------- clip modal ---------------- */
  async function addClip(type) {
    if (!S.seq) return;
    try {
      var clip = await WVG.api("/api/sequences/" + S.seq.sequence_id + "/clips",
        { method: "POST", body: { type: type, prompt: "" } });
      await selectSequence(S.seq.sequence_id);
      var fresh = S.seq.clips.find(function (c) { return c.clip_id === clip.clip_id; });
      openClipModal(fresh, null);
    } catch (e) { WVG.toast("Could not add clip", "error", e.message); }
  }

  function openClipModal(clip, openSection) {
    S.editing = JSON.parse(JSON.stringify(clip));
    el("clip-modal-title").textContent = "Edit Clip #" + (clip.index + 1);
    el("clip-edit-id").value = clip.clip_id;
    el("clip-name").value = clip.name || "";
    el("clip-prompt").value = clip.prompt || "";
    el("clip-negative").value = clip.negative_prompt || "";
    el("clip-use-global").checked = !!clip.use_global_generation_settings;
    el("clip-look-mode").value = clip.color_look_mode;
    setClipType(clip.type);
    // Generation overrides use the SAME shared module in the
    // "sequence_clip_override" context (patchSeq §12). Seed it with the global
    // settings overlaid by any existing overrides so it shows real values.
    var mod = ensureClipModule();
    if (mod) {
      var o = clip.generation_overrides || {};
      var merged = Object.assign({}, S.seq.global_generation_settings);
      Object.keys(o).forEach(function (k) { if (o[k] != null) merged[k] = o[k]; });
      mod.hydrate(merged);
    }
    updateClipOverrideVisibility(!!clip.use_global_generation_settings);
    // image
    if (clip.source_image) {
      el("clip-image-preview").src = "/media/sequences/" + S.seq.sequence_id + "/asset/image/" + encodeURIComponent(clip.source_image);
      el("clip-image-preview").style.display = "";
      el("clip-image-name").textContent = clip.source_image;
    } else { el("clip-image-preview").style.display = "none"; el("clip-image-name").textContent = ""; }
    el("clip-image-fit").value = clip.image_fit || "contain";
    // Custom Color & Look: the SAME shared module in "sequence_clip_override".
    var look = ensureClipLook();
    if (look) look.hydrate(clip.custom_color_look || WVGColorLook.defaults());
    updateClipLookVisibility(el("clip-look-mode").value);
    // Clip Audio Tracks: the SAME shared module in "sequence_clip".
    var caud = ensureClipAudio();
    if (caud) caud.hydrate(clip.clip_audio_tracks || []);
    // open requested section
    ["clip-overrides", "clip-look", "clip-audio"].forEach(function (d) {
      if (el(d)) el(d).open = (d === openSection);
    });
    WVG.openModal("clip-modal-backdrop");
  }

  function updateClipLookVisibility(mode) {
    var wrap = el("clip-look-custom-wrap");
    if (wrap) wrap.style.display = (mode === "custom") ? "" : "none";
  }

  /* Custom Color & Look editor for one clip (sequence_clip_override). */
  function ensureClipLook() {
    if (S.clClip) return S.clClip;
    var root = document.querySelector('[data-color-look-context="sequence_clip_override"]');
    if (!root || !window.WVGColorLook) return null;
    S.clClip = WVGColorLook.mount(root, {
      context: "sequence_clip_override",
      state: WVGColorLook.defaults(),
      onChange: function (fx) { S.editing.custom_color_look = fx; },  // collected on Save
      previewSource: function () {
        var cid = el("clip-edit-id").value;
        var clip = (S.seq.clips || []).find(function (c) { return c.clip_id === cid; });
        if (!clip || !clip.outputs || !clip.outputs.preview) return null;
        return { url: "/media/sequences/" + S.seq.sequence_id + "/clip/" + clip.clip_id + "/preview",
                 label: "Frame from " + clip.name };
      },
    });
    return S.clClip;
  }

  /* Per-clip Audio Tracks editor (sequence_clip). */
  function ensureClipAudio() {
    if (S.audClip) return S.audClip;
    var root = document.querySelector('[data-audio-tracks-context="sequence_clip"]');
    if (!root || !window.WVGAudioTracks) return null;
    S.audClip = WVGAudioTracks.mount(root, {
      context: "sequence_clip",
      getMediaUrl: audioMediaUrl,
      onUpdate: function (id, patch) {
        var body = Object.assign({ clip_id: el("clip-edit-id").value }, patch);
        return WVG.api("/api/sequences/" + S.seq.sequence_id + "/audio/" + id, { method: "PUT", body: body });
      },
      onRemove: function (id) {
        return WVG.api("/api/sequences/" + S.seq.sequence_id + "/audio/" + id +
          "?clip_id=" + encodeURIComponent(el("clip-edit-id").value), { method: "DELETE" });
      },
      onUpload: function (file) {
        var fd = new FormData(); fd.append("file", file); fd.append("clip_id", el("clip-edit-id").value);
        return WVG.api("/api/sequences/" + S.seq.sequence_id + "/assets/audio", { method: "POST", body: fd });
      },
      reload: async function () {
        var cid = el("clip-edit-id").value;
        S.seq = await WVG.api("/api/sequences/" + S.seq.sequence_id);
        var clip = S.seq.clips.find(function (c) { return c.clip_id === cid; });
        return clip ? (clip.clip_audio_tracks || []) : [];
      },
    });
    return S.audClip;
  }

  function setClipType(type) {
    document.querySelectorAll("#clip-modal-backdrop [data-clip-type]").forEach(function (b) {
      b.classList.toggle("active", b.dataset.clipType === type);
    });
    el("clip-image-field").style.display = type === "image_reference" ? "" : "none";
    S.editing.type = type;
  }

  function ensureClipModule() {
    if (S.gpClip) return S.gpClip;
    var root = document.querySelector('[data-generation-parameters="sequence_clip_override"]');
    if (!root || !window.WVGGenParams) return null;
    S.gpClip = WVGGenParams.mount(root, {
      context: "sequence_clip_override",
      models: S.models,
      getMode: function () {
        return S.editing && S.editing.type === "image_reference" ? "image2video" : "text2video";
      },
      onChange: function () {},  // collected on Save
    });
    return S.gpClip;
  }

  function updateClipOverrideVisibility(useGlobal) {
    var det = el("clip-overrides");
    if (det) det.open = !useGlobal;
  }

  async function saveClip() {
    var sid = S.seq.sequence_id, cid = el("clip-edit-id").value;
    var useGlobal = el("clip-use-global").checked;
    // When overriding, collect the shared module and map to the per-clip
    // override fields (a subset — model/precision/device always come from
    // global). When using global, send an empty override object.
    var overrides = {};
    if (!useGlobal && S.gpClip) {
      var c = S.gpClip.collect();
      overrides = {
        width: c.width, height: c.height, frames: c.frames, fps: c.fps,
        steps: c.steps, guidance_scale: c.guidance_scale,
        sampler_name: c.sampler_name, scheduler: c.scheduler, denoise: c.denoise,
        seed_mode: c.seed_mode, seed: c.seed,
        model_sampling_shift: c.model_sampling_shift,
      };
    }
    var body = {
      name: el("clip-name").value.trim(),
      type: S.editing.type,
      prompt: el("clip-prompt").value,
      negative_prompt: el("clip-negative").value,
      image_fit: el("clip-image-fit").value,
      use_global_generation_settings: useGlobal,
      color_look_mode: el("clip-look-mode").value,
      custom_color_look: (S.clClip ? S.clClip.getState() : S.editing.custom_color_look),
      source_image: S.editing.source_image || null,
      generation_overrides: overrides,
    };
    try {
      await WVG.api("/api/sequences/" + sid + "/clips/" + cid, { method: "PUT", body: body });
      WVG.closeModal("clip-modal-backdrop");
      await selectSequence(sid);
    } catch (e) { WVG.toast("Could not save clip", "error", e.message); }
  }

  /* Color & Look and Audio Tracks are now the shared context-aware modules
     (WVGColorLook / WVGAudioTracks) mounted above — no sequence-only clones. */

  /* ---------------- render controls + polling ---------------- */
  function renderStatus(rs, s) {
    rs = rs || {};
    el("seq-progress-fill").style.width = (rs.overall_progress || 0) + "%";
    el("seq-progress-pct").textContent = (rs.overall_progress || 0) + "%";
    el("seq-progress-stage").textContent = rs.current_stage || STATUS_LABEL[rs.status] || rs.status || "Idle";
    var running = rs.status === "rendering" || rs.status === "stopping";
    el("seq-stop").disabled = !running;
    el("seq-render").disabled = running;
    var badge = el("seq-status-badge");
    if (badge) badge.textContent = rs.status ? STATUS_LABEL[rs.status] || rs.status : "";
    // final output
    var out = (s && s.outputs) || {};
    var box = el("seq-final-output");
    if (out.final || out.merged) {
      box.style.display = "";
      var kind = out.final ? "final" : "merged";
      el("seq-final-video").src = "/media/sequences/" + S.seq.sequence_id + "/export/" + kind;
      el("seq-final-download").href = "/media/sequences/" + S.seq.sequence_id + "/export/" + (out.final ? "final" : "merged") + "?download=1";
      el("seq-merged-download").href = "/media/sequences/" + S.seq.sequence_id + "/export/merged?download=1";
      el("seq-merged-download").style.display = out.merged ? "" : "none";
    }
  }

  function applyStatus(st) {
    // st is the lightweight status payload from polling; patch into S.seq clips
    if (!S.seq) return;
    (st.clips || []).forEach(function (cs) {
      var c = S.seq.clips.find(function (x) { return x.clip_id === cs.clip_id; });
      if (c) { c.status = cs.status; c.progress = cs.progress; c.stage = cs.stage; c.last_error = cs.last_error; }
    });
    S.seq.render_state = {
      status: st.status, overall_progress: st.overall_progress,
      current_stage: st.current_stage, current_clip_id: st.current_clip_id,
      can_resume: st.can_resume, last_error: st.last_error,
    };
    if (st.outputs) S.seq.outputs = st.outputs;
    renderClips();
    renderStatus(S.seq.render_state, S.seq);
  }

  function startPolling() {
    stopPolling();
    S.wasRendering = true;
    S.poll = setInterval(async function () {
      if (!S.seq) return stopPolling();
      try {
        var st = await WVG.api("/api/sequences/" + S.seq.sequence_id + "/status");
        applyStatus(st);
        if (!st.running && st.status !== "rendering" && st.status !== "stopping") {
          stopPolling();
          var finalStatus = st.status;
          await selectSequence(S.seq.sequence_id);  // full refresh with outputs
          // Reusable audio feedback on whole-sequence completion (patch §10.3.2).
          if (S.wasRendering && finalStatus === "completed" && window.WVGAudioFeedback) {
            WVGAudioFeedback.play_sequence_completed();
          } else if (finalStatus === "failed" && window.WVGAudioFeedback) {
            WVGAudioFeedback.play_error();
          }
          S.wasRendering = false;
        }
      } catch (e) { /* keep polling */ }
    }, 1200);
  }
  function stopPolling() { if (S.poll) { clearInterval(S.poll); S.poll = null; } }

  async function renderQueue() {
    try {
      var only = null;
      if (S.seq.output_mode === "selected_only") {
        var sel = prompt("Selected clips mode: enter clip numbers to render (e.g. 1,3):", "");
        if (sel == null) return;
        var nums = sel.split(",").map(function (x) { return parseInt(x.trim(), 10); }).filter(function (x) { return x > 0; });
        only = S.seq.clips.filter(function (c, i) { return nums.indexOf(i + 1) >= 0; }).map(function (c) { return c.clip_id; });
      }
      await WVG.api("/api/sequences/" + S.seq.sequence_id + "/render", { method: "POST", body: only ? { clip_ids: only } : {} });
      WVG.toast("Render started", "success");
      startPolling();
    } catch (e) { WVG.toast("Could not start render", "error", e.message); }
  }

  /* ---------------- init ---------------- */
  function bind() {
    initModeSwitch();
    try { S.models = WVG.readJson ? (WVG.readJson("seq-models-data") || []) : JSON.parse((el("seq-models-data") || {}).textContent || "[]"); }
    catch (e) { S.models = []; }

    el("seq-new").addEventListener("click", async function () {
      var name = prompt("New sequence name:", "Beach Short 001");
      if (!name) return;
      try {
        var s = await WVG.api("/api/sequences", { method: "POST", body: { name: name } });
        S.seq = null; await loadList(false);
        el("seq-select").value = s.sequence_id; await selectSequence(s.sequence_id);
      } catch (e) { WVG.toast("Could not create sequence", "error", e.message); }
    });
    el("seq-delete").addEventListener("click", async function () {
      if (!S.seq) return;
      if (!confirm("Delete sequence '" + S.seq.name + "' and all its clips/outputs?")) return;
      try { await WVG.api("/api/sequences/" + S.seq.sequence_id, { method: "DELETE" }); S.seq = null; await loadList(true); }
      catch (e) { WVG.toast("Delete failed", "error", e.message); }
    });
    el("seq-select").addEventListener("change", function () { selectSequence(el("seq-select").value); });

    // Sequence orchestration fields + the global default negative prompt. The
    // Global Generation Parameters module saves itself via its onChange handler.
    ["seq-name", "seq-output-mode", "seq-vram-mode", "seq-continue-on-error",
     "seq-g-negative"].forEach(function (id) {
      var e = el(id); if (e) e.addEventListener("change", saveSettings);
    });

    var useGlobal = el("clip-use-global");
    if (useGlobal) useGlobal.addEventListener("change", function () {
      updateClipOverrideVisibility(this.checked);
    });
    var lookMode = el("clip-look-mode");
    if (lookMode) lookMode.addEventListener("change", function () {
      updateClipLookVisibility(this.value);
    });

    el("seq-add-image").addEventListener("click", function () { addClip("image_reference"); });
    el("seq-add-prompt").addEventListener("click", function () { addClip("prompt_only"); });
    el("seq-render").addEventListener("click", renderQueue);
    el("seq-stop").addEventListener("click", async function () {
      try { await WVG.api("/api/sequences/" + S.seq.sequence_id + "/stop", { method: "POST" }); WVG.toast("Stopping…", "success"); }
      catch (e) { WVG.toast("Stop failed", "error", e.message); }
    });
    el("seq-resume").addEventListener("click", async function () {
      try { await WVG.api("/api/sequences/" + S.seq.sequence_id + "/resume", { method: "POST" }); WVG.toast("Resuming…", "success"); startPolling(); }
      catch (e) { WVG.toast("Resume failed", "error", e.message); }
    });
    el("seq-merge").addEventListener("click", async function () {
      try { await WVG.api("/api/sequences/" + S.seq.sequence_id + "/merge", { method: "POST" }); await selectSequence(S.seq.sequence_id); WVG.toast("Merged", "success"); }
      catch (e) { WVG.toast("Merge failed", "error", e.message); }
    });
    el("seq-seq-audio").addEventListener("click", async function () {
      try { await WVG.api("/api/sequences/" + S.seq.sequence_id + "/apply-sequence-audio", { method: "POST" }); await selectSequence(S.seq.sequence_id); WVG.toast("Sequence audio applied", "success"); }
      catch (e) { WVG.toast("Sequence audio failed", "error", e.message); }
    });

    // Sequence-master and clip audio uploads are handled inside the reused
    // WVGAudioTracks modules (add-track button + scoped file input).
    el("clip-save").addEventListener("click", saveClip);
    document.querySelectorAll("#clip-modal-backdrop [data-clip-type]").forEach(function (b) {
      b.addEventListener("click", function () { setClipType(b.dataset.clipType); });
    });
    el("clip-image-file").addEventListener("change", async function () {
      var f = this.files[0]; if (!f) return;
      var fd = new FormData(); fd.append("file", f);
      try {
        var r = await WVG.api("/api/sequences/" + S.seq.sequence_id + "/assets/image", { method: "POST", body: fd });
        S.editing.source_image = r.filename;
        el("clip-image-preview").src = r.url; el("clip-image-preview").style.display = "";
        el("clip-image-name").textContent = r.filename;
        if (S.editing.type !== "image_reference") setClipType("image_reference");
      } catch (e) { WVG.toast("Image upload failed", "error", e.message); }
      this.value = "";
    });
  }

  document.addEventListener("DOMContentLoaded", bind);

  // Refresh the queue when the AI Assistant populates the currently-open
  // sequence (patch §9 — SequenceQueue stays the source of truth).
  document.addEventListener("wvg:ai-sequence-populated", function (e) {
    var id = e && e.detail && e.detail.sequence_id;
    if (id && S.seq && S.seq.sequence_id === id) selectSequence(id);
    else loadList(false);
  });

  // Let the AI Assistant target the open sequence when launched from this panel.
  window.WVGSequence = window.WVGSequence || {};
  window.WVGSequence.currentId = function () { return S.seq ? S.seq.sequence_id : null; };
})(window.WVG);
