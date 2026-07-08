/* AI Prompt Assistant — Settings page controller (patch §3/§12).
   Radica - WanVideoGenerator — Concept & Design: Fabrizio Radica — Project by RadicaDesign

   Loads the persisted assistant config into the editable form, saves changes
   back through /api/ai-assistant/config, and drives the provider/sound tests. */

(function () {
  "use strict";

  var root = document.getElementById("s-ai-assistant");
  if (!root) return;

  var providerDefaults = {};

  function fields() { return root.querySelectorAll("[data-ai-field]"); }

  function setStatus(msg) {
    var s = document.getElementById("ai-cfg-status");
    if (s) s.textContent = msg || "";
  }

  function hydrate(config) {
    fields().forEach(function (node) {
      var section = node.dataset.aiSection, field = node.dataset.aiField;
      var val = (config[section] || {})[field];
      if (node.type === "checkbox") node.checked = !!val;
      else if (val == null) node.value = "";
      else node.value = val;
    });
    var keyNote = document.getElementById("ai-key-set-note");
    if (keyNote) keyNote.textContent = (config.provider && config.provider.api_key_set) ? "(a key is saved)" : "(not set)";
    var vol = document.getElementById("ai-vol"), volVal = document.getElementById("ai-vol-val");
    if (vol && volVal) volVal.textContent = Number(vol.value).toFixed(2);
  }

  function collect() {
    var payload = { prompt_assistant: {}, provider: {}, resources: {}, audio_feedback: {} };
    fields().forEach(function (node) {
      var section = node.dataset.aiSection, field = node.dataset.aiField;
      if (node.type === "checkbox") {
        payload[section][field] = node.checked;
      } else if (node.type === "number" || node.type === "range") {
        var raw = node.value.trim();
        if (raw === "") payload[section][field] = null;   // optional numeric -> null
        else payload[section][field] = parseFloat(raw);
      } else {
        payload[section][field] = node.value;
      }
    });
    // Never send an empty api_key (the backend then preserves the stored one).
    if (payload.provider.api_key === "") delete payload.provider.api_key;
    return payload;
  }

  async function load() {
    try {
      var data = await WVG.api("/api/ai-assistant/config");
      providerDefaults = data.provider_defaults || {};
      hydrate(data.config);
      setStatus(data.config.prompt_assistant.enabled ? "Enabled" : "Disabled");
    } catch (e) { setStatus("Could not load config"); }
  }

  async function save() {
    var btn = document.getElementById("ai-save-btn");
    btn.disabled = true; btn.textContent = "Saving…";
    try {
      var data = await WVG.api("/api/ai-assistant/config", { method: "PUT", body: collect() });
      hydrate(data.config);
      if (window.WVGAudioFeedback && data.config.audio_feedback) WVGAudioFeedback.setConfig(data.config.audio_feedback);
      if (window.WVGAIAssistant && WVGAIAssistant.reloadConfig) WVGAIAssistant.reloadConfig();
      setStatus(data.config.prompt_assistant.enabled ? "Enabled" : "Disabled");
      WVG.toast("AI Assistant settings saved", "success");
    } catch (e) {
      WVG.toast("Could not save settings", "error", e.message);
    } finally {
      btn.disabled = false; btn.textContent = "💾 Save AI Assistant settings";
    }
  }

  async function testConnection() {
    var out = document.getElementById("ai-test-result");
    var btn = document.getElementById("ai-test-btn");
    out.textContent = "Testing… (save first if you changed provider fields)";
    out.style.color = "";
    btn.disabled = true;
    try {
      var r = await WVG.api("/api/ai-assistant/test", { method: "POST" });
      out.textContent = "✓ Connected to " + r.provider + " (" + r.model + ")";
      out.style.color = "var(--success, #4ade80)";
    } catch (e) {
      out.textContent = "✕ " + e.message;
      out.style.color = "var(--danger, #f87171)";
    } finally { btn.disabled = false; }
  }

  function bind() {
    document.getElementById("ai-save-btn").addEventListener("click", save);
    document.getElementById("ai-test-btn").addEventListener("click", testConnection);
    document.getElementById("ai-test-sound").addEventListener("click", function () {
      if (window.WVGAudioFeedback) {
        WVGAudioFeedback.setConfig(collect().audio_feedback);
        WVGAudioFeedback.play_success();
      }
    });
    var vol = document.getElementById("ai-vol"), volVal = document.getElementById("ai-vol-val");
    if (vol) vol.addEventListener("input", function () { if (volVal) volVal.textContent = Number(vol.value).toFixed(2); });

    // Fill Base URL / model defaults when the provider changes and the fields
    // are still empty (never clobber a user's custom value).
    var provSel = document.getElementById("ai-provider-select");
    if (provSel) provSel.addEventListener("change", function () {
      var d = providerDefaults[provSel.value];
      if (!d) return;
      var base = document.getElementById("ai-base-url"), model = document.getElementById("ai-model-name");
      if (base && !base.value.trim()) base.value = d.base_url || "";
      if (model && !model.value.trim()) model.value = d.default_model || "";
    });
  }

  bind();
  load();
})();
