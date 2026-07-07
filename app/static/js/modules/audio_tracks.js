/* Shared, context-aware Audio Tracks module (patchReuseColoAudio §8/§9/§15).

   One implementation of the audio track card + per-track controls, reused by:
     - Single Clip (audio_tracks.js delegates its card HTML to WVGAudioTracks.trackCardHTML)
     - VideoSequenceQueue Sequence Audio (context "sequence_master", after final merge)
     - VideoSequenceQueue per-clip audio (context "sequence_clip", before merge)

   Reads/writes the canonical AudioTrack schema. Root-scoped via
   data-audio-tracks-context + data-role/data-action — no global IDs. Persistence
   is delegated to an adapter so each context talks to its own endpoints. */

window.WVGAudioTracks = (function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // The one audio track card markup (ported from audio_tracks.js so Single Clip
  // and the sequence render identical controls). `mediaUrl` may be "" to omit
  // the preview player.
  function trackCardHTML(t, idx, mediaUrl) {
    var player = mediaUrl
      ? '<audio controls preload="none" src="' + esc(mediaUrl) + '" style="height:24px;max-width:170px;"></audio>'
      : "";
    return '' +
      '<div class="audio-track' + (t.enabled ? "" : " disabled") + '" data-id="' + esc(t.id) + '">' +
      '  <div class="audio-track-head">' +
      '    <strong title="' + esc(t.original_filename || t.filename) + '">Track ' + (idx + 1) + ' — ' + esc(t.original_filename || t.filename) + '</strong>' +
      player +
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

  /* ===================================================================
     Context-aware component (sequence contexts)
     =================================================================== */
  function mount(root, opts) {
    if (!root) return null;
    opts = opts || {};
    var getMediaUrl = opts.getMediaUrl || function () { return ""; };
    var onUpdate = opts.onUpdate || function () { return Promise.resolve(); };
    var onRemove = opts.onRemove || function () { return Promise.resolve(); };
    var onUpload = opts.onUpload || function () { return Promise.resolve(); };
    var reload = opts.reload || function () { return Promise.resolve([]); };
    var tracks = [];

    var listEl = root.querySelector('[data-role="list"]');
    var emptyEl = root.querySelector('[data-role="empty"]');
    var addBtn = root.querySelector('[data-action="add-track"]');
    var fileInput = root.querySelector('[data-role="file"]');

    function render() {
      if (!listEl) return;
      listEl.innerHTML = tracks.map(function (t, i) { return trackCardHTML(t, i, getMediaUrl(t)); }).join("");
      if (emptyEl) emptyEl.style.display = tracks.length ? "none" : "";
      listEl.querySelectorAll(".audio-track").forEach(bindCard);
    }

    async function refresh() {
      try { tracks = (await reload()) || []; } catch (e) { /* keep current */ }
      render();
    }

    function bindCard(card) {
      var id = card.dataset.id;
      function patch(changes) {
        Promise.resolve(onUpdate(id, changes)).then(function (updated) {
          if (updated && updated.id) {
            var i = tracks.findIndex(function (t) { return t.id === id; });
            if (i !== -1) tracks[i] = updated;
            card.classList.toggle("disabled", !updated.enabled);
          }
        }).catch(function (e) { WVG.toast("Could not update track", "error", e.message); render(); });
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
          var changes = {}; changes[pair[1]] = v; patch(changes);
        });
      });

      card.querySelector(".audio-remove").addEventListener("click", function () {
        if (!window.confirm("Remove this audio track?")) return;
        Promise.resolve(onRemove(id)).then(refresh).then(function () {
          WVG.toast("Audio track removed", "success");
        }).catch(function (e) { WVG.toast("Remove failed", "error", e.message); });
      });
    }

    if (addBtn && fileInput) {
      addBtn.addEventListener("click", function () { fileInput.click(); });
      fileInput.addEventListener("change", function () {
        var f = this.files && this.files[0];
        this.value = "";
        if (!f) return;
        Promise.resolve(onUpload(f)).then(refresh).then(function () {
          WVG.toast("Audio track added: " + f.name, "success");
        }).catch(function (e) { WVG.toast("Audio upload failed", "error", e.message); });
      });
    }

    function hydrate(list) { tracks = (list || []).slice(); render(); }

    return { hydrate: hydrate, refresh: refresh, root: root };
  }

  return { mount: mount, trackCardHTML: trackCardHTML };
})();
