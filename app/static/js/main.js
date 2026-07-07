/* Radica - WanVideoGenerator — shared UI helpers
   Concept & Design: Fabrizio Radica — Project by RadicaDesign */

window.WVG = window.WVG || {};

(function (WVG) {
  "use strict";

  /* ---------- API helper ---------- */
  WVG.api = async function (url, options) {
    options = options || {};
    if (options.body && typeof options.body !== "string" && !(options.body instanceof FormData)) {
      options.body = JSON.stringify(options.body);
      options.headers = Object.assign({ "Content-Type": "application/json" }, options.headers);
    }
    var res = await fetch(url, options);
    var data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON response */ }
    if (!res.ok) {
      var detail = (data && data.detail) ? data.detail : (res.status + " " + res.statusText);
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  };

  /* ---------- Toasts ---------- */
  WVG.toast = function (message, type, detail) {
    var container = document.getElementById("toast-container");
    if (!container) return;
    var el = document.createElement("div");
    el.className = "toast" + (type ? " toast-" + type : "");
    el.textContent = message;
    if (detail) {
      var d = document.createElement("div");
      d.className = "toast-detail";
      d.textContent = detail;
      el.appendChild(d);
    }
    container.appendChild(el);
    setTimeout(function () {
      el.style.transition = "opacity 0.3s";
      el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 350);
    }, type === "error" ? 7000 : 3500);
  };

  /* ---------- Modals ---------- */
  WVG.openModal = function (id) {
    var el = document.getElementById(id);
    if (el) el.classList.add("open");
  };
  WVG.closeModal = function (id) {
    var el = document.getElementById(id);
    if (el) el.classList.remove("open");
  };
  document.addEventListener("click", function (e) {
    if (e.target.classList && e.target.classList.contains("modal-backdrop")) {
      e.target.classList.remove("open");
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      document.querySelectorAll(".modal-backdrop.open").forEach(function (m) { m.classList.remove("open"); });
    }
  });

  /* ---------- Range slider fill + value binding ---------- */
  WVG.bindSlider = function (inputId, valueId, format) {
    var input = document.getElementById(inputId);
    var out = valueId ? document.getElementById(valueId) : null;
    if (!input) return;
    var update = function () {
      var min = parseFloat(input.min || 0), max = parseFloat(input.max || 100);
      var pct = ((parseFloat(input.value) - min) / (max - min)) * 100;
      input.style.setProperty("--fill", pct + "%");
      if (out) out.textContent = format ? format(parseFloat(input.value)) : input.value;
    };
    input.addEventListener("input", update);
    update();
  };

  /* ---------- Character counters ---------- */
  WVG.bindCharCount = function (textareaId, counterId) {
    var ta = document.getElementById(textareaId);
    var counter = document.getElementById(counterId);
    if (!ta || !counter) return;
    var update = function () { counter.textContent = ta.value.length; };
    ta.addEventListener("input", update);
    update();
  };

  /* ---------- Card filtering (search) ---------- */
  WVG.filterCards = function (gridId, query) {
    query = (query || "").toLowerCase().trim();
    document.querySelectorAll("#" + gridId + " > [data-name]").forEach(function (card) {
      card.style.display = !query || card.dataset.name.indexOf(query) !== -1 ? "" : "none";
    });
  };
  WVG.filterCardsByMode = function (gridId, mode) {
    document.querySelectorAll("#" + gridId + " > [data-mode]").forEach(function (card) {
      card.style.display = !mode || card.dataset.mode === mode ? "" : "none";
    });
  };

  /* ---------- Shared project actions ---------- */
  WVG.openProjectFolder = async function (projectId) {
    try {
      var res = await WVG.api("/api/projects/" + projectId + "/open-folder", { method: "POST" });
      WVG.toast("Folder opened: " + res.path, "success");
    } catch (e) { WVG.toast("Could not open folder", "error", e.message); }
  };

  WVG.duplicateProject = async function (projectId) {
    try {
      var p = await WVG.api("/api/projects/" + projectId + "/duplicate", { method: "POST" });
      WVG.toast("Project duplicated: " + p.name, "success");
      setTimeout(function () { window.location = "/?project=" + p.id; }, 600);
    } catch (e) { WVG.toast("Duplicate failed", "error", e.message); }
  };

  WVG.deleteProject = async function (projectId, name) {
    if (!window.confirm('Delete project "' + name + '" and ALL its files (videos, workflows, metadata)?\nThis cannot be undone.')) return;
    try {
      await WVG.api("/api/projects/" + projectId, { method: "DELETE" });
      WVG.toast("Project deleted", "success");
      setTimeout(function () { window.location.reload(); }, 600);
    } catch (e) { WVG.toast("Delete failed", "error", e.message); }
  };

  WVG.projectDetails = async function (projectId) {
    try {
      var p = await WVG.api("/api/projects/" + projectId);
      var pre = document.getElementById("details-json");
      if (pre) {
        pre.textContent = JSON.stringify(p, null, 2);
        WVG.openModal("details-backdrop");
      }
    } catch (e) { WVG.toast("Could not load project", "error", e.message); }
  };

  /* ---------- Video preview modal (library page) ---------- */
  WVG.previewVideo = function (url, filename) {
    var body = document.getElementById("video-modal-body");
    var title = document.getElementById("video-modal-title");
    if (!body) return;
    title.textContent = filename;
    body.innerHTML = "";
    if (/\.webp$/i.test(url)) {
      var img = document.createElement("img");
      img.src = url;
      img.style.maxWidth = "100%";
      img.style.maxHeight = "70vh";
      body.appendChild(img);
    } else {
      var video = document.createElement("video");
      video.src = url;
      video.controls = true;
      video.autoplay = true;
      video.style.maxWidth = "100%";
      video.style.maxHeight = "70vh";
      body.appendChild(video);
    }
    WVG.openModal("video-modal");
  };

  WVG.copyMetadata = async function (metadataUrl) {
    try {
      var res = await fetch(metadataUrl);
      var text = await res.text();
      await navigator.clipboard.writeText(text);
      WVG.toast("Metadata copied to clipboard", "success");
    } catch (e) { WVG.toast("Could not copy metadata", "error", e.message); }
  };

  /* ---------- Backend readiness indicator (topbar, all pages) ---------- */
  document.addEventListener("DOMContentLoaded", async function () {
    var slot = document.getElementById("topbar-status");
    if (!slot) return;
    try {
      var s = await WVG.api("/api/backend/status");
      if (s.is_mock) {
        slot.innerHTML = '<span class="badge badge-accent" title="Simulated output for UI testing — NOT real Wan generation">MOCK BACKEND (developer mode)</span>';
      } else if (s.ready) {
        var dev = s.device_name ? " · " + s.device_name : (s.device ? " · " + s.device : "");
        slot.innerHTML = '<span class="badge" style="color:var(--success,#4ade80);border-color:currentColor;" title="Real Wan generation backend is ready">● wan backend ready' + dev + "</span>";
      } else {
        var why = s.error || s.device_error || ((s.missing_dependencies || []).length ? "Missing packages: " + s.missing_dependencies.join(", ") : "not ready");
        slot.innerHTML = '<span class="badge" style="color:var(--danger,#f87171);border-color:currentColor;" title="' + String(why).replace(/"/g, "&quot;") + '">● wan backend NOT ready — hover for details</span>';
      }
      WVG.backendStatus = s;
      document.dispatchEvent(new CustomEvent("wvg:backend-status"));
    } catch (e) { /* status endpoint unavailable — leave topbar empty */ }
  });

  /* ---------- Generated video deletion (library + editor) ---------- */
  WVG.deleteVideo = async function (projectId, filename, onDeleted) {
    if (!window.confirm("Are you sure you want to delete this generated video?\n" +
                        filename + "\nThis action cannot be undone.")) return;
    try {
      await WVG.api("/api/projects/" + projectId + "/videos", {
        method: "DELETE",
        body: { filename: filename }
      });
      WVG.toast("Video deleted successfully.", "success");
      if (typeof onDeleted === "function") onDeleted();
    } catch (e) {
      // Deletion failed — the item stays in the UI untouched.
      WVG.toast("Could not delete generated video.", "error", e.message);
    }
  };

  /* ---------- JSON data blocks ---------- */
  WVG.readJson = function (id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  };
})(window.WVG);
