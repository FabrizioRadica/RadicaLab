/* Camera Motion Assistant: movement grid, sliders, prompt fragment, canvas diagram */

window.WVGCam = (function () {
  "use strict";

  var ICONS = {
    static: "⊙", push_in: "⇥", pull_back: "⇤", pan_left: "⟵", pan_right: "⟶",
    tilt_up: "⤒", tilt_down: "⤓", orbit_left: "↺", orbit_right: "↻",
    truck_left: "⇇", truck_right: "⇉", crane_up: "⇈", crane_down: "⇊",
    handheld: "〰", follow: "◎", parallax: "⧉"
  };

  var state = {
    enabled: false,
    movement_type: "static",
    intensity: 0.65,
    speed: 0.55,
    smoothness: 0.8,
    orbit_angle: 360,
    distance: 3.5,
    height: 1.5,
    path_type: "arc",
    framing: "stable",
    preset: null,
    fragment: "",
    applied_to_prompt: false
  };

  var options = null;
  var fragmentTimer = null;
  var animStart = performance.now();

  function el(id) { return document.getElementById(id); }

  function getSettings() { return Object.assign({}, state); }

  /* ---------- Fragment preview (server-side composition) ---------- */
  function refreshFragment() {
    clearTimeout(fragmentTimer);
    fragmentTimer = setTimeout(async function () {
      try {
        var res = await WVG.api("/api/camera-motion/fragment", { method: "POST", body: state });
        state.fragment = res.fragment;
        var view = el("cm-fragment");
        if (view) view.textContent = res.fragment || "—";
        syncProject();
        WVG.updateComposedPrompt();
      } catch (e) { /* keep last fragment on transient errors */ }
    }, 200);
  }

  function syncProject() {
    if (WVG.project) WVG.project.camera_motion = getSettings();
  }

  function updateApplyButtons() {
    var apply = el("cm-apply");
    var remove = el("cm-remove");
    var frag = el("cm-fragment");
    if (!apply) return;
    apply.style.display = state.applied_to_prompt ? "none" : "";
    remove.style.display = state.applied_to_prompt ? "" : "none";
    frag.classList.toggle("applied", state.applied_to_prompt);
  }

  /* ---------- Movement grid ---------- */
  function buildGrid() {
    var grid = el("movement-grid");
    if (!grid || !options) return;
    grid.innerHTML = "";
    options.movement_types.forEach(function (mt) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "movement-btn" + (state.movement_type === mt.id ? " selected" : "");
      btn.dataset.movement = mt.id;
      btn.innerHTML = '<span class="m-icon">' + (ICONS[mt.id] || "•") + '</span><span class="m-label">' + mt.label + "</span>";
      btn.addEventListener("click", function () {
        state.movement_type = mt.id;
        state.preset = null;
        el("cm-preset").value = "";
        grid.querySelectorAll(".movement-btn").forEach(function (b) { b.classList.toggle("selected", b === btn); });
        updateOrbitVisibility();
        refreshFragment();
      });
      grid.appendChild(btn);
    });
  }

  function updateOrbitVisibility() {
    var orbit = el("orbit-params");
    if (orbit) orbit.classList.toggle("visible", /^orbit_/.test(state.movement_type));
  }

  /* ---------- Presets ---------- */
  function buildPresets() {
    var select = el("cm-preset");
    if (!select || !options) return;
    options.presets.forEach(function (p) {
      var opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.name;
      select.appendChild(opt);
    });
    select.addEventListener("change", function () {
      var preset = options.presets.find(function (p) { return p.id === select.value; });
      if (!preset) { state.preset = null; return; }
      Object.assign(state, preset.settings);
      state.preset = preset.id;
      hydrateControls();
      refreshFragment();
    });
  }

  /* ---------- Controls ---------- */
  function bindControl(id, valueId, key, format) {
    var input = el(id);
    if (!input) return;
    WVG.bindSlider(id, valueId, format);
    input.addEventListener("input", function () {
      state[key] = parseFloat(input.value);
      state.preset = null;
      refreshFragment();
    });
  }

  function hydrateControls() {
    el("cm-enabled").checked = state.enabled;
    el("cm-intensity").value = state.intensity;
    el("cm-speed").value = state.speed;
    el("cm-smoothness").value = state.smoothness;
    el("cm-angle").value = state.orbit_angle;
    el("cm-distance").value = state.distance;
    el("cm-height").value = state.height;
    el("cm-framing").value = state.framing;
    var pathRadio = document.querySelector('input[name="cm-path"][value="' + state.path_type + '"]');
    if (pathRadio) pathRadio.checked = true;
    ["cm-intensity", "cm-speed", "cm-smoothness", "cm-angle", "cm-distance", "cm-height"].forEach(function (id) {
      var input = el(id);
      input.dispatchEvent(new Event("input", { bubbles: false }));
    });
    buildGrid();
    updateOrbitVisibility();
    updateApplyButtons();
    var view = el("cm-fragment");
    if (view) view.textContent = state.fragment || "—";
  }

  /* ---------- Canvas diagram ---------- */
  function drawDiagram(now) {
    var canvas = el("motion-canvas");
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    var W = canvas.width, H = canvas.height;
    var cx = W / 2, cy = H / 2 + 14;
    ctx.clearRect(0, 0, W, H);

    var speed = 0.2 + state.speed * 1.1;
    var t = ((now - animStart) / 1000 * speed * 0.25) % 1;
    var accent = "#7c5cfa", accentSoft = "rgba(124,92,250,0.45)", white = "#cfc9e8";

    // ground grid (2.5D)
    ctx.strokeStyle = "rgba(124,92,250,0.10)";
    ctx.lineWidth = 1;
    for (var gy = 0; gy < 4; gy++) {
      var ry = 44 + gy * 16, rx = ry * 2.4;
      ctx.beginPath();
      ctx.ellipse(cx, cy + 22, rx, ry * 0.42, 0, 0, Math.PI * 2);
      ctx.stroke();
    }

    // subject (stylized bust on pedestal)
    ctx.fillStyle = "#3b3757";
    ctx.beginPath(); ctx.ellipse(cx, cy + 26, 34, 10, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#57517d";
    ctx.beginPath(); ctx.ellipse(cx, cy + 8, 20, 14, 0, 0, Math.PI); ctx.fill();
    ctx.beginPath(); ctx.arc(cx, cy - 10, 13, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#6d668f";
    ctx.beginPath(); ctx.arc(cx - 4, cy - 13, 4, 0, Math.PI * 2); ctx.fill();

    var mv = state.movement_type;
    var amp = 30 + state.intensity * 60;

    function camera(x, y, angleToSubject) {
      ctx.fillStyle = accent;
      ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 1.5;
      ctx.stroke();
      var a = angleToSubject !== undefined ? angleToSubject : Math.atan2(cy - y, cx - x);
      ctx.strokeStyle = accentSoft;
      ctx.beginPath();
      ctx.moveTo(x + Math.cos(a - 0.3) * 16, y + Math.sin(a - 0.3) * 16);
      ctx.lineTo(x, y);
      ctx.lineTo(x + Math.cos(a + 0.3) * 16, y + Math.sin(a + 0.3) * 16);
      ctx.stroke();
    }

    function arrow(x1, y1, x2, y2, color) {
      ctx.strokeStyle = color || accentSoft;
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      var a = Math.atan2(y2 - y1, x2 - x1);
      ctx.beginPath();
      ctx.moveTo(x2, y2);
      ctx.lineTo(x2 - 9 * Math.cos(a - 0.42), y2 - 9 * Math.sin(a - 0.42));
      ctx.moveTo(x2, y2);
      ctx.lineTo(x2 - 9 * Math.cos(a + 0.42), y2 - 9 * Math.sin(a + 0.42));
      ctx.stroke();
    }

    ctx.setLineDash([]);

    if (mv === "static") {
      camera(cx, cy + 92);
      ctx.fillStyle = white; ctx.font = "11px sans-serif"; ctx.textAlign = "center";
      ctx.fillText("locked-off shot", cx, cy + 116);
    } else if (mv === "orbit_left" || mv === "orbit_right") {
      var rx = 92 + state.distance * 6, ry = (92 + state.distance * 6) * 0.42;
      var sweep = (state.path_type === "full_circle" ? 360 : state.orbit_angle) * Math.PI / 180;
      var dir = mv === "orbit_left" ? -1 : 1;
      ctx.setLineDash([6, 6]);
      ctx.strokeStyle = accentSoft;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.ellipse(cx, cy + 4, rx, ry, 0, Math.PI / 2 - sweep / 2 * dir * (dir === 1 ? 1 : -1) - (dir === 1 ? 0 : 0), Math.PI / 2 + sweep / 2, dir === -1);
      ctx.stroke();
      ctx.setLineDash([]);
      // numbered steps
      ctx.fillStyle = accent; ctx.font = "bold 10px sans-serif"; ctx.textAlign = "center";
      for (var s = 0; s < 4; s++) {
        var sa = Math.PI / 2 + dir * sweep * (s / 3 - 0.5);
        var sx = cx + Math.cos(sa) * rx, sy = cy + 4 + Math.sin(sa) * ry;
        ctx.beginPath(); ctx.arc(sx, sy, 8, 0, Math.PI * 2);
        ctx.fillStyle = "#241f3a"; ctx.fill();
        ctx.strokeStyle = accent; ctx.stroke();
        ctx.fillStyle = white; ctx.fillText(String(s + 1), sx, sy + 3.5);
      }
      var ca = Math.PI / 2 + dir * sweep * (t - 0.5);
      camera(cx + Math.cos(ca) * rx, cy + 4 + Math.sin(ca) * ry);
    } else if (mv === "push_in" || mv === "pull_back") {
      var d0 = 118, d1 = 48;
      var f = mv === "push_in" ? t : 1 - t;
      var y = cy + d0 - (d0 - d1) * f;
      ctx.setLineDash([6, 6]);
      ctx.strokeStyle = accentSoft;
      ctx.beginPath(); ctx.moveTo(cx, cy + d0 + 4); ctx.lineTo(cx, cy + d1); ctx.stroke();
      ctx.setLineDash([]);
      arrow(cx + 22, cy + d0, cx + 22, mv === "push_in" ? cy + d1 + 10 : cy + d0 + 2);
      if (mv === "pull_back") arrow(cx + 22, cy + d1 + 10, cx + 22, cy + d0);
      camera(cx, y, -Math.PI / 2);
    } else if (mv === "pan_left" || mv === "pan_right" || mv === "truck_left" || mv === "truck_right" || mv === "parallax" || mv === "follow") {
      var dirX = /left/.test(mv) ? -1 : 1;
      var x0 = cx - dirX * amp, x1 = cx + dirX * amp;
      var xNow = x0 + (x1 - x0) * t;
      ctx.setLineDash([6, 6]);
      ctx.strokeStyle = accentSoft;
      ctx.beginPath(); ctx.moveTo(cx - amp, cy + 92); ctx.lineTo(cx + amp, cy + 92); ctx.stroke();
      ctx.setLineDash([]);
      arrow(cx, cy + 110, cx + dirX * (amp + 14), cy + 110);
      camera(xNow, cy + 92);
      if (mv === "parallax") {
        ctx.strokeStyle = "rgba(207,201,232,0.25)";
        for (var L = 0; L < 3; L++) {
          var off = ((t * (L + 1) * 40) % 80) - 40;
          ctx.beginPath();
          ctx.moveTo(cx - 110 + off, cy - 46 + L * 14);
          ctx.lineTo(cx - 60 + off, cy - 46 + L * 14);
          ctx.stroke();
        }
      }
    } else if (mv === "tilt_up" || mv === "tilt_down" || mv === "crane_up" || mv === "crane_down") {
      var dirY = /up$/.test(mv) ? -1 : 1;
      var y0 = cy + 92 - dirY * -amp * 0.6;
      var yNow = (cy + 92) + dirY * (t - 0.5) * amp * 1.2;
      ctx.setLineDash([6, 6]);
      ctx.strokeStyle = accentSoft;
      ctx.beginPath(); ctx.moveTo(cx - 78, cy + 92 - amp * 0.6); ctx.lineTo(cx - 78, cy + 92 + amp * 0.6); ctx.stroke();
      ctx.setLineDash([]);
      arrow(cx - 96, cy + 92 + dirY * amp * 0.5 * -1 * -1, cx - 96, cy + 92 + dirY * amp * 0.6);
      camera(cx - 78, /^crane/.test(mv) ? yNow : cy + 92, Math.atan2((cy - (/^crane/.test(mv) ? yNow : cy + 92 + dirY * (t - 0.5) * -amp)), cx - (cx - 78)));
    } else if (mv === "handheld") {
      var jx = Math.sin(now / 90) * 4 * state.intensity + Math.sin(now / 41) * 2.4 * state.intensity;
      var jy = Math.cos(now / 71) * 3.4 * state.intensity;
      camera(cx + jx, cy + 92 + jy);
      ctx.fillStyle = white; ctx.font = "11px sans-serif"; ctx.textAlign = "center";
      ctx.fillText("organic micro-movement", cx, cy + 118);
    }

    requestAnimationFrame(drawDiagram);
  }

  /* ---------- Init ---------- */
  document.addEventListener("DOMContentLoaded", function () {
    if (!el("camera-panel")) return;
    options = WVG.readJson("camera-options");
    if (WVG.project && WVG.project.camera_motion) {
      Object.assign(state, WVG.project.camera_motion);
    }

    buildPresets();
    bindControl("cm-intensity", "v-cm-intensity", "intensity", function (v) { return v.toFixed(2); });
    bindControl("cm-speed", "v-cm-speed", "speed", function (v) { return v.toFixed(2); });
    bindControl("cm-smoothness", "v-cm-smoothness", "smoothness", function (v) { return v.toFixed(2); });
    bindControl("cm-angle", "v-cm-angle", "orbit_angle", function (v) { return v.toFixed(0) + "°"; });
    bindControl("cm-distance", "v-cm-distance", "distance", function (v) { return v.toFixed(1) + "m"; });
    bindControl("cm-height", "v-cm-height", "height", function (v) { return v.toFixed(1) + "m"; });

    el("cm-framing").addEventListener("change", function () {
      state.framing = this.value; state.preset = null; refreshFragment();
    });
    document.querySelectorAll('input[name="cm-path"]').forEach(function (radio) {
      radio.addEventListener("change", function () {
        state.path_type = this.value; state.preset = null; refreshFragment();
      });
    });
    el("cm-enabled").addEventListener("change", function () {
      state.enabled = this.checked;
      if (!state.enabled && state.applied_to_prompt) {
        state.applied_to_prompt = false;
        updateApplyButtons();
      }
      syncProject();
      WVG.updateComposedPrompt();
    });

    el("cm-copy").addEventListener("click", async function () {
      try {
        await navigator.clipboard.writeText(state.fragment || "");
        WVG.toast("Camera text copied", "success");
      } catch (e) { WVG.toast("Copy failed", "error"); }
    });
    el("cm-apply").addEventListener("click", function () {
      if (!state.fragment) { WVG.toast("No camera text to apply yet", "error"); return; }
      state.enabled = true;
      state.applied_to_prompt = true;
      el("cm-enabled").checked = true;
      updateApplyButtons();
      syncProject();
      WVG.updateComposedPrompt();
      WVG.toast("Camera motion appended to the final prompt", "success");
    });
    el("cm-remove").addEventListener("click", function () {
      state.applied_to_prompt = false;
      updateApplyButtons();
      syncProject();
      WVG.updateComposedPrompt();
      WVG.toast("Camera motion removed from the final prompt");
    });

    hydrateControls();
    if (state.fragment) {
      el("cm-fragment").textContent = state.fragment;
    } else {
      refreshFragment();
    }
    requestAnimationFrame(drawDiagram);
  });

  return { getSettings: getSettings };
})();
