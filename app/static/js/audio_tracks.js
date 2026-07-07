/* Audio Tracks panel — upload, per-track settings, apply mixed audio to a video.
   Post-processing only: audio never influences Wan generation.
   Radica - WanVideoGenerator — Concept & Design: Fabrizio Radica — Project by RadicaDesign */

(function () {
  "use strict";

  var tracks = [];

  function el(id) { return document.getElementById(id); }
  function api(path, opts) { return WVG.api("/api/projects/" + WVG.project.id + path, opts); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* ---------- rendering ---------- */

  function trackCard(t, idx) {
    // Shared card markup (patchReuseColoAudio §8) — the same renderer the
    // sequence audio contexts use. Local fallback keeps Single Clip working if
    // the shared module failed to load.
    var url = "/media/projects/" + WVG.project.id + "/audio/" + encodeURIComponent(t.filename);
    if (window.WVGAudioTracks && WVGAudioTracks.trackCardHTML) {
      return WVGAudioTracks.trackCardHTML(t, idx, url);
    }
    return '' +
      '<div class="audio-track' + (t.enabled ? "" : " disabled") + '" data-id="' + esc(t.id) + '">' +
      '  <div class="audio-track-head">' +
      '    <strong title="' + esc(t.original_filename || t.filename) + '">Track ' + (idx + 1) + ' — ' + esc(t.filename) + '</strong>' +
      '    <audio controls preload="none" src="' + url + '" style="height:24px;max-width:170px;"></audio>' +
      '    <button class="btn btn-ghost btn-sm audio-remove" title="Remove track">✕</button>' +
      '  </div>' +
      '  <div class="audio-track-grid">' +
      '    <label class="toggle"><input type="checkbox" class="a-enabled"' + (t.enabled ? " checked" : "") + '><span class="track"></span><span>Enabled</span></label>' +
      '    <div class="audio-field"><span>Volume</span>' +
      '      <input type="range" class="a-volume" min="0" max="2" step="0.05" value="' + t.volume + '">' +
      '      <span class="slider-value a-volume-out">' + Math.round(t.volume * 100) + '%</span></div>' +
      '    <div class="audio-field"><span>Start (s)</span><input type="number" class="a-start" min="0" step="0.1" value="' + t.start_time + '"></div>' +
      '    <div class="audio-field"><span>Fade in (s)</span><input type="number" class="a-fadein" min="0" step="0.1" value="' + t.fade_in + '"></div>' +
      '    <div class="audio-field"><span>Fade out (s)</span><input type="number" class="a-fadeout" min="0" step="0.1" value="' + t.fade_out + '"></div>' +
      '    <label class="toggle"><input type="checkbox" class="a-loop"' + (t.loop ? " checked" : "") + '><span class="track"></span><span>Loop</span></label>' +
      '    <label class="toggle"><input type="checkbox" class="a-trim"' + (t.trim_to_video ? " checked" : "") + '><span class="track"></span><span>Trim to video</span></label>' +
      '  </div>' +
      '</div>';
  }

  function render() {
    var list = el("audio-track-list");
    list.innerHTML = tracks.map(trackCard).join("");
    el("audio-empty-hint").style.display = tracks.length ? "none" : "";
    list.querySelectorAll(".audio-track").forEach(bindCard);
  }

  function bindCard(card) {
    var id = card.dataset.id;

    function patch(changes) {
      api("/audio/" + id, { method: "PATCH", body: changes }).then(function (updated) {
        var i = tracks.findIndex(function (t) { return t.id === id; });
        if (i !== -1) tracks[i] = updated;
        card.classList.toggle("disabled", !updated.enabled);
      }).catch(function (e) {
        WVG.toast("Could not update track", "error", e.message);
        render(); // restore server state
      });
    }

    card.querySelector(".a-enabled").addEventListener("change", function () { patch({ enabled: this.checked }); });
    card.querySelector(".a-loop").addEventListener("change", function () { patch({ loop: this.checked }); });
    card.querySelector(".a-trim").addEventListener("change", function () { patch({ trim_to_video: this.checked }); });

    var vol = card.querySelector(".a-volume");
    var volOut = card.querySelector(".a-volume-out");
    vol.addEventListener("input", function () { volOut.textContent = Math.round(vol.value * 100) + "%"; });
    vol.addEventListener("change", function () { patch({ volume: parseFloat(vol.value) }); });

    [["a-start", "start_time"], ["a-fadein", "fade_in"], ["a-fadeout", "fade_out"]].forEach(function (pair) {
      var input = card.querySelector("." + pair[0]);
      input.addEventListener("change", function () {
        var v = parseFloat(input.value);
        if (isNaN(v) || v < 0) { input.value = 0; v = 0; }
        var changes = {};
        changes[pair[1]] = v;
        patch(changes);
      });
    });

    card.querySelector(".audio-remove").addEventListener("click", function () {
      if (!window.confirm("Remove this audio track (and its file if unused)?")) return;
      api("/audio/" + id, { method: "DELETE" }).then(function (list) {
        tracks = list;
        render();
        WVG.toast("Audio track removed", "success");
      }).catch(function (e) { WVG.toast("Remove failed", "error", e.message); });
    });
  }

  function populateTargets() {
    var sel = el("audio-apply-target");
    var raw = (WVG.project.generated_videos || []).filter(function (v) { return !v.has_audio; });
    sel.innerHTML = raw.length
      ? raw.slice().reverse().map(function (v) {
          return '<option value="' + esc(v.filename) + '">' + esc(v.filename) + "</option>";
        }).join("")
      : '<option value="">— no generated video yet —</option>';
  }

  /* ---------- actions ---------- */

  async function upload(file) {
    var fd = new FormData();
    fd.append("file", file);
    try {
      tracks = await api("/audio/upload", { method: "POST", body: fd });
      render();
      WVG.toast("Audio track added: " + file.name, "success");
    } catch (e) {
      WVG.toast("Audio upload failed", "error", e.message);
    }
  }

  async function applyAudio() {
    var target = el("audio-apply-target").value;
    if (!target) { WVG.toast("Generate a video first", "error"); return; }
    var btn = el("btn-apply-audio");
    btn.disabled = true;
    btn.textContent = "Mixing…";
    try {
      var res = await api("/audio/apply", { method: "POST", body: { video_filename: target } });
      WVG.toast("Audio applied: " + res.entry.filename, "success");
      if (window.WVGGen) WVGGen.showOutput(res.entry.filename);
      setTimeout(function () { window.location = "/?project=" + WVG.project.id; }, 1200);
    } catch (e) {
      WVG.toast("Audio processing failed", "error", e.message);
    } finally {
      btn.disabled = false;
      btn.innerHTML = "♫ Apply Audio";
    }
  }

  /* ---------- init ---------- */

  document.addEventListener("DOMContentLoaded", function () {
    if (!el("audio-panel") || !WVG.project) return;
    tracks = WVG.project.audio_tracks || [];
    render();
    populateTargets();

    el("btn-add-audio").addEventListener("click", function () { el("audio-file-input").click(); });
    el("audio-file-input").addEventListener("change", function () {
      if (this.files && this.files[0]) upload(this.files[0]);
      this.value = "";
    });
    el("btn-apply-audio").addEventListener("click", applyAudio);
  });
})();
