/* AudioFeedbackModule (patch §10) — reusable UI sound feedback.
   Radica - WanVideoGenerator — Concept & Design: Fabrizio Radica — Project by RadicaDesign

   One module, called from multiple parts of the app (never scattered sound
   calls). Sounds are synthesized with the Web Audio API so there are no asset
   files to ship and nothing violates a strict CSP. Settings come from the AI
   assistant config (enabled / volume / per-event toggles) and can be refreshed
   live via WVGAudioFeedback.setConfig(). */

window.WVGAudioFeedback = (function () {
  "use strict";

  var cfg = {
    enabled: true, volume: 0.7,
    on_single_clip_complete: true, on_sequence_complete: true,
    on_ai_sequence_created: true, on_warning: false, on_error: true,
  };
  var ctx = null;
  var seenCompleted = {};   // job_id -> true (single-clip completion de-dupe)
  var seenFailed = {};

  function audioCtx() {
    if (ctx) return ctx;
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
    } catch (e) { ctx = null; }
    return ctx;
  }

  /* Play a small sequence of notes. notes = [{f, t, d}] (freq Hz, start s, dur s). */
  function tones(notes, type) {
    if (!cfg.enabled) return;
    var ac = audioCtx();
    if (!ac) return;
    if (ac.state === "suspended" && ac.resume) { try { ac.resume(); } catch (e) {} }
    var vol = Math.max(0, Math.min(1, cfg.volume));
    if (vol <= 0) return;
    var now = ac.currentTime + 0.01;
    notes.forEach(function (n) {
      var osc = ac.createOscillator();
      var gain = ac.createGain();
      osc.type = type || "sine";
      osc.frequency.value = n.f;
      var start = now + n.t, end = start + n.d;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(vol * 0.5, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, end);
      osc.connect(gain).connect(ac.destination);
      osc.start(start);
      osc.stop(end + 0.02);
    });
  }

  var API = {
    setConfig: function (audioFeedback) {
      if (audioFeedback && typeof audioFeedback === "object") {
        Object.keys(cfg).forEach(function (k) {
          if (k in audioFeedback) cfg[k] = audioFeedback[k];
        });
      }
    },
    getConfig: function () { return JSON.parse(JSON.stringify(cfg)); },

    /* --- Required event methods (patch §10.2) --- */
    play_clip_completed: function () {
      if (cfg.on_single_clip_complete) tones([{ f: 660, t: 0, d: 0.12 }, { f: 880, t: 0.12, d: 0.16 }], "sine");
    },
    play_sequence_completed: function () {
      if (cfg.on_sequence_complete) tones([{ f: 523, t: 0, d: 0.12 }, { f: 659, t: 0.12, d: 0.12 }, { f: 784, t: 0.24, d: 0.2 }], "sine");
    },
    play_ai_sequence_created: function () {
      if (cfg.on_ai_sequence_created) tones([{ f: 587, t: 0, d: 0.1 }, { f: 784, t: 0.1, d: 0.1 }, { f: 988, t: 0.2, d: 0.16 }], "triangle");
    },
    play_success: function () { tones([{ f: 784, t: 0, d: 0.14 }], "sine"); },
    play_warning: function () {
      if (cfg.on_warning) tones([{ f: 440, t: 0, d: 0.14 }, { f: 392, t: 0.14, d: 0.18 }], "triangle");
    },
    play_error: function () {
      if (cfg.on_error) tones([{ f: 330, t: 0, d: 0.16 }, { f: 247, t: 0.16, d: 0.24 }], "sawtooth");
    },
  };

  /* Single-clip generation completion is detected from the global job strip
     events (patch §10.3.1) — decoupled from the generation code itself. */
  document.addEventListener("wvg:jobs", function (e) {
    var d = (e && e.detail) || {};
    (d.recent || []).forEach(function (j) {
      if (j.status === "completed" && !seenCompleted[j.id]) {
        seenCompleted[j.id] = true;
        API.play_clip_completed();
      } else if ((j.status === "failed" || j.status === "cancelled") && !seenFailed[j.id]) {
        seenFailed[j.id] = true;
        if (j.status === "failed") API.play_error();
      }
    });
  });

  /* Load audio-feedback settings once on any page. Silent if the assistant
     config endpoint is unavailable (defaults keep sound on). */
  document.addEventListener("DOMContentLoaded", function () {
    fetch("/api/ai-assistant/config")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (data && data.config && data.config.audio_feedback) API.setConfig(data.config.audio_feedback);
      })
      .catch(function () { /* keep defaults */ });
  });

  return API;
})();
