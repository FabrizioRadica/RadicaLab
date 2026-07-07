/* Model Manager: Model Bundles — list, sectioned editor, structured validation, defaults */

window.WVGModels = (function () {
  "use strict";

  var models = [];
  var selectedId = null;
  var editingId = null;

  var STATUS_LABELS = { ok: "OK", missing: "MISSING", invalid: "INVALID", experimental: "EXPERIMENTAL", partial: "PARTIALLY CONFIGURED" };

  var CORE_COMPONENTS = [
    ["diffusion_model_path", "Diffusion model"],
    ["checkpoint_path", "Main checkpoint"],
    ["vae_path", "VAE"],
    ["text_encoder_path", "Text encoder"],
    ["clip_path", "CLIP encoder"],
    ["t5_encoder_path", "T5 encoder"],
    ["vision_encoder_path", "Vision encoder (I2V)"],
    ["tokenizer_path", "Tokenizer"],
    ["config_path", "Config"],
    ["scheduler_config_path", "Scheduler config"]
  ];

  function el(id) { return document.getElementById(id); }

  function badge(status) {
    return '<span class="badge badge-' + status + '">' + (STATUS_LABELS[status] || status.toUpperCase()) + "</span>";
  }

  function typeLabel(t) {
    return t === "text2video" ? "Text2Video" : t === "image2video" ? "Image2Video" : "Both";
  }

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function corePath(m) {
    return m.diffusion_model_path || m.checkpoint_path || "";
  }

  function componentCount(m) {
    var n = 0;
    CORE_COMPONENTS.forEach(function (c) { if (m[c[0]]) n++; });
    n += (m.lora_paths || []).length + (m.control_model_paths || []).length + (m.auxiliary_model_paths || []).length;
    if (m.upscaler_model_path) n++;
    n += Object.keys(m.custom_component_paths || {}).length;
    return n;
  }

  function render() {
    var tbody = el("models-tbody");
    tbody.innerHTML = "";
    models.forEach(function (m) {
      var tr = document.createElement("tr");
      tr.className = m.id === selectedId ? "selected" : "";
      var core = corePath(m);
      tr.innerHTML =
        "<td><strong>" + esc(m.display_name) + "</strong><br><span class='muted small mono'>" + esc(m.id) + "</span></td>" +
        "<td>" + typeLabel(m.generation_type) + "</td>" +
        "<td class='muted'>" + esc(m.family) + " " + esc(m.version) + "</td>" +
        "<td class='mono small' style='max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;' title='" + esc(core) + "'>" +
        esc(core || "—") + "<br><span class='muted'>" + componentCount(m) + " component(s)</span></td>" +
        "<td>" + badge(m.status) + "</td>" +
        "<td class='muted'>" + m.recommended_vram_gb + " GB</td>" +
        "<td class='muted small'>" + (m.default_t2v ? "T2V " : "") + (m.default_i2v ? "I2V" : "") + "</td>" +
        "<td>" +
        "<button class='btn btn-sm' data-act='details'>Details</button> " +
        "<button class='btn btn-sm' data-act='edit'>Edit</button> " +
        "<button class='btn btn-sm btn-danger' data-act='remove'>✕</button>" +
        "</td>";
      tr.querySelector("[data-act='details']").addEventListener("click", function () { select(m.id); });
      tr.querySelector("[data-act='edit']").addEventListener("click", function () { openEditor(m.id); });
      tr.querySelector("[data-act='remove']").addEventListener("click", function () { removeModel(m.id, m.display_name); });
      tbody.appendChild(tr);
    });
  }

  function kvRow(k, v) {
    return "<li><span class='k'>" + esc(k) + "</span><span class='v'>" + esc(v) + "</span></li>";
  }

  function select(id) {
    selectedId = id;
    var m = models.find(function (x) { return x.id === id; });
    var panel = el("model-detail-panel");
    if (!m) { panel.style.display = "none"; render(); return; }
    panel.style.display = "";
    el("detail-name").textContent = m.display_name;

    var rows = [
      kvRow("Model ID", m.id),
      kvRow("Family / version", m.family + " " + m.version),
      kvRow("Generation type", typeLabel(m.generation_type)),
      kvRow("Status", STATUS_LABELS[m.status] || m.status),
      kvRow("Experimental", m.experimental ? "Yes" : "No"),
      kvRow("Backends", [
        m.supports_direct_python_backend ? "Direct Python" : null,
        m.supports_comfyui_export ? "ComfyUI export" : null,
        m.supports_mock_backend ? "Mock" : null
      ].filter(Boolean).join(", ") || "None enabled")
    ];
    if (m.backend_notes) rows.push(kvRow("Backend notes", m.backend_notes));

    rows.push("<li><span class='k' style='color:var(--accent-text);font-weight:700;'>Bundle components</span><span></span></li>");
    CORE_COMPONENTS.forEach(function (c) {
      rows.push(kvRow(c[1], m[c[0]] || "— not configured"));
    });
    (m.lora_paths || []).forEach(function (p, i) { rows.push(kvRow("LoRA #" + (i + 1), p)); });
    (m.control_model_paths || []).forEach(function (p, i) { rows.push(kvRow("Control model #" + (i + 1), p)); });
    if (m.upscaler_model_path) rows.push(kvRow("Upscaler model", m.upscaler_model_path));
    (m.auxiliary_model_paths || []).forEach(function (p, i) { rows.push(kvRow("Auxiliary #" + (i + 1), p)); });
    Object.keys(m.custom_component_paths || {}).forEach(function (name) {
      rows.push(kvRow("Custom: " + name, m.custom_component_paths[name]));
    });

    rows.push(kvRow("Recommended VRAM", m.recommended_vram_gb + " GB"));
    rows.push(kvRow("Recommended resolutions", (m.recommended_resolutions || []).join(", ") || "—"));
    rows.push(kvRow("Supported resolutions", (m.supported_resolutions || []).join(", ") || "—"));
    if (m.notes) rows.push(kvRow("Notes", m.notes));
    rows.push(kvRow("Created", (m.created_at || "").slice(0, 16).replace("T", " ")));
    rows.push(kvRow("Updated", (m.updated_at || "").slice(0, 16).replace("T", " ")));

    el("detail-kv").innerHTML = rows.join("");
    el("detail-validation").innerHTML = "";
    el("detail-default-t2v").style.display = (m.generation_type === "text2video" || m.generation_type === "both") ? "" : "none";
    el("detail-default-i2v").style.display = (m.generation_type === "image2video" || m.generation_type === "both") ? "" : "none";
    render();
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function reload() {
    try {
      models = await WVG.api("/api/models");
      render();
      if (selectedId) select(selectedId);
    } catch (e) { WVG.toast("Could not load models", "error", e.message); }
  }

  function lines(id) {
    return el(id).value.split("\n").map(function (s) { return s.trim(); }).filter(Boolean);
  }

  function openEditor(id) {
    editingId = id || null;
    var m = id ? models.find(function (x) { return x.id === id; }) : null;
    el("model-editor-title").textContent = m ? "Edit Model Bundle" : "Add Model Bundle";
    el("me-name").value = m ? m.display_name : "";
    el("me-id").value = m ? m.id : "";
    el("me-id").disabled = !!m;
    el("me-family").value = m ? m.family : "Wan2.2";
    el("me-version").value = m ? m.version : "2.2";
    el("me-type").value = m ? m.generation_type : "text2video";
    el("me-notes").value = m ? m.notes : "";
    el("me-experimental").checked = m ? m.experimental : false;

    el("me-b-direct").checked = m ? m.supports_direct_python_backend : false;
    el("me-b-comfy").checked = m ? m.supports_comfyui_export : true;
    el("me-b-mock").checked = m ? m.supports_mock_backend : true;
    el("me-backend-notes").value = m ? m.backend_notes : "";

    el("me-diffusion").value = m ? m.diffusion_model_path : "";
    el("me-checkpoint").value = m ? m.checkpoint_path : "";
    el("me-vae").value = m ? m.vae_path : "";
    el("me-text-encoder").value = m ? m.text_encoder_path : "";
    el("me-clip").value = m ? m.clip_path : "";
    el("me-t5").value = m ? m.t5_encoder_path : "";
    el("me-vision").value = m ? m.vision_encoder_path : "";
    el("me-tokenizer").value = m ? m.tokenizer_path : "";
    el("me-config").value = m ? m.config_path : "";
    el("me-scheduler").value = m ? m.scheduler_config_path : "";

    el("me-loras").value = m ? (m.lora_paths || []).join("\n") : "";
    el("me-controls").value = m ? (m.control_model_paths || []).join("\n") : "";
    el("me-upscaler").value = m ? m.upscaler_model_path : "";
    el("me-aux").value = m ? (m.auxiliary_model_paths || []).join("\n") : "";
    el("me-custom").value = m ? Object.keys(m.custom_component_paths || {}).map(function (k) {
      return k + " = " + m.custom_component_paths[k];
    }).join("\n") : "";

    el("me-vram").value = m ? m.recommended_vram_gb : 12;
    el("me-resolutions").value = m ? (m.recommended_resolutions || []).join(", ") : "1280x720";
    el("me-supported-res").value = m ? (m.supported_resolutions || []).join(", ") : "";
    WVG.openModal("model-editor-backdrop");
  }

  async function saveEditor() {
    var custom = {};
    lines("me-custom").forEach(function (line) {
      var idx = line.indexOf("=");
      if (idx > 0) custom[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
    });
    var payload = {
      display_name: el("me-name").value.trim(),
      family: el("me-family").value.trim() || "Wan2.2",
      version: el("me-version").value.trim() || "2.2",
      generation_type: el("me-type").value,
      notes: el("me-notes").value,
      experimental: el("me-experimental").checked,

      supports_direct_python_backend: el("me-b-direct").checked,
      supports_comfyui_export: el("me-b-comfy").checked,
      supports_mock_backend: el("me-b-mock").checked,
      backend_notes: el("me-backend-notes").value.trim(),

      diffusion_model_path: el("me-diffusion").value.trim(),
      checkpoint_path: el("me-checkpoint").value.trim(),
      vae_path: el("me-vae").value.trim(),
      text_encoder_path: el("me-text-encoder").value.trim(),
      clip_path: el("me-clip").value.trim(),
      t5_encoder_path: el("me-t5").value.trim(),
      vision_encoder_path: el("me-vision").value.trim(),
      tokenizer_path: el("me-tokenizer").value.trim(),
      config_path: el("me-config").value.trim(),
      scheduler_config_path: el("me-scheduler").value.trim(),

      lora_paths: lines("me-loras"),
      control_model_paths: lines("me-controls"),
      upscaler_model_path: el("me-upscaler").value.trim(),
      auxiliary_model_paths: lines("me-aux"),
      custom_component_paths: custom,

      recommended_vram_gb: parseInt(el("me-vram").value, 10) || 12,
      recommended_resolutions: el("me-resolutions").value.split(",").map(function (r) { return r.trim(); }).filter(Boolean),
      supported_resolutions: el("me-supported-res").value.split(",").map(function (r) { return r.trim(); }).filter(Boolean)
    };
    if (!payload.display_name) { WVG.toast("Display name is required", "error"); return; }
    if (!payload.diffusion_model_path && !payload.checkpoint_path) {
      WVG.toast("Configure at least a diffusion model path or a main checkpoint path", "error");
      return;
    }
    try {
      if (editingId) {
        await WVG.api("/api/models/" + editingId, { method: "PUT", body: payload });
        WVG.toast("Model bundle updated", "success");
      } else {
        var customId = el("me-id").value.trim();
        if (customId) payload.id = customId;
        await WVG.api("/api/models", { method: "POST", body: payload });
        WVG.toast("Model bundle added", "success");
      }
      WVG.closeModal("model-editor-backdrop");
      reload();
    } catch (e) { WVG.toast("Could not save model bundle", "error", e.message); }
  }

  async function removeModel(id, name) {
    if (!window.confirm('Remove model bundle "' + name + '" from the registry?\n(The model files on disk are NOT deleted.)')) return;
    try {
      await WVG.api("/api/models/" + id, { method: "DELETE" });
      WVG.toast("Model bundle removed", "success");
      if (selectedId === id) { selectedId = null; el("model-detail-panel").style.display = "none"; }
      reload();
    } catch (e) { WVG.toast("Could not remove model", "error", e.message); }
  }

  function listBlock(title, items, color) {
    if (!items || !items.length) return "";
    return "<div style='margin-top:8px;'><strong style='color:" + color + ";font-size:12px;'>" + esc(title) + "</strong>" +
      "<ul style='margin:4px 0 0 18px;padding:0;font-size:12px;color:var(--muted);'>" +
      items.map(function (i) { return "<li>" + esc(i) + "</li>"; }).join("") + "</ul></div>";
  }

  async function validateSelected() {
    if (!selectedId) return;
    var target = el("detail-validation");
    target.innerHTML = "<span class='muted small'>Validating bundle…</span>";
    try {
      var res = await WVG.api("/api/models/" + selectedId + "/validate", { method: "POST" });
      var html = "<div class='panel' style='background:var(--bg-2);'>" +
        "<strong>" + badge(res.status) + " " + esc(res.message) + "</strong>" +
        listBlock("Missing required components", res.missing_required_components, "var(--danger)") +
        listBlock("Missing optional components", res.missing_optional_components, "var(--warning)") +
        listBlock("Warnings", res.warnings, "var(--warning)") +
        listBlock("Errors", res.errors, "var(--danger)") +
        "<ul class='kv-list' style='margin-top:10px;'>" +
        res.checks.map(function (c) {
          var icon = c.ok ? (c.level === "warning" ? "⚠️" : "✅") : (c.level === "warning" ? "⚠️" : "❌");
          return "<li><span class='k'>" + icon + " " + esc(c.name) + "</span><span class='v small muted'>" + esc(c.detail) + "</span></li>";
        }).join("") + "</ul></div>";
      target.innerHTML = html;
      reload();
    } catch (e) {
      target.innerHTML = "";
      WVG.toast("Validation failed", "error", e.message);
    }
  }

  async function setDefault(mode) {
    if (!selectedId) return;
    try {
      var res = await WVG.api("/api/models/" + selectedId + "/set-default", { method: "POST", body: { mode: mode } });
      WVG.toast(res.message, "success");
      reload();
    } catch (e) { WVG.toast("Could not set default", "error", e.message); }
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!el("models-tbody")) return;
    models = WVG.readJson("models-data") || [];
    render();
    el("me-save").addEventListener("click", saveEditor);
    el("detail-validate").addEventListener("click", validateSelected);
    el("detail-default-t2v").addEventListener("click", function () { setDefault("text2video"); });
    el("detail-default-i2v").addEventListener("click", function () { setDefault("image2video"); });
  });

  return { openEditor: openEditor };
})();
