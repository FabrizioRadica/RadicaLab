/* Global generation progress strip — recovers active jobs on every page load
   and keeps polling while jobs run, so navigation/refresh never loses progress.
   Radica - WanVideoGenerator — Concept & Design: Fabrizio Radica — Project by RadicaDesign */

(function () {
  "use strict";

  var strip = null;
  var pollMs = 1000;
  var idleMs = 5000;          // reduced polling when nothing is running
  var dismissed = {};          // job_id -> true (user closed the entry)
  var timer = null;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var ACTIVE = ["running", "pending", "cancel_requested"];

  function jobRow(job) {
    var cls = "gen-job " + job.status;
    var left = "";
    var isActive = ACTIVE.indexOf(job.status) !== -1;
    if (isActive) {
      var stopping = job.status === "cancel_requested" || job.cancel_requested;
      left = "<span class='gen-label'>" + (stopping ? "Stopping:" : "Generating:") + "</span> <strong>" + esc(job.project_name) + "</strong>" +
             " <span class='gen-meta'>" + esc(job.mode) + " · " + esc(job.backend) + " backend" +
             (job.is_mock ? " (MOCK)" : "") + "</span>" +
             "<span class='gen-pct'>" + (job.progress || 0) + "%</span>" +
             "<span class='gen-stage'>" + esc(stopping ? "Stopping…" : (job.stage || job.status)) + "</span>" +
             "<button class='gen-stop' data-stop='" + esc(job.id) + "'" + (stopping ? " disabled" : "") +
             " title='Stop this generation'>" + (stopping ? "⏳" : "⏹ Stop") + "</button>";
    } else if (job.status === "completed") {
      left = "<span class='gen-label ok'>✔ Completed:</span> <strong>" + esc(job.project_name) + "</strong>" +
             (job.output_url ? " <a class='gen-link' href='/?project=" + esc(job.project_id) + "'>view result</a>" : "") +
             (job.output_file ? " <span class='gen-meta'>" + esc(job.output_file) + "</span>" : "");
    } else { // failed / cancelled
      left = "<span class='gen-label err'>✖ " + esc(job.status) + ":</span> <strong>" + esc(job.project_name) + "</strong>" +
             " <span class='gen-error' title='" + esc(job.error) + "'>" + esc((job.error || "").slice(0, 160)) + "</span>" +
             " <a class='gen-link' href='/?project=" + esc(job.project_id) + "'>open project</a>";
    }
    var bar = isActive
      ? "<div class='gen-bar'><div class='gen-bar-fill' style='width:" + (job.progress || 0) + "%'></div></div>"
      : "";
    return "<div class='" + cls + "' data-job='" + esc(job.id) + "'>" +
           "<div class='gen-row'>" + left +
           "<button class='gen-dismiss' data-dismiss='" + esc(job.id) + "' title='Dismiss'>×</button></div>" +
           bar + "</div>";
  }

  function render(active, recent) {
    var jobs = active.concat(recent).filter(function (j) { return !dismissed[j.id]; });
    if (!jobs.length) {
      strip.style.display = "none";
      strip.innerHTML = "";
      return;
    }
    strip.innerHTML = jobs.map(jobRow).join("");
    strip.style.display = "";
    strip.querySelectorAll("[data-dismiss]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        dismissed[btn.dataset.dismiss] = true;
        render(active, recent);
      });
    });
    strip.querySelectorAll("[data-stop]").forEach(function (btn) {
      btn.addEventListener("click", function () { stopJob(btn.dataset.stop, btn); });
    });
  }

  async function stopJob(jobId, btn) {
    if (!window.confirm("Stop the current generation?\nThe partial output may be deleted.")) return;
    if (btn) { btn.disabled = true; btn.textContent = "⏳"; }
    try {
      await fetch("/api/generation/jobs/" + jobId + "/cancel", { method: "POST" })
        .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); });
      if (window.WVG && WVG.toast) WVG.toast("Stopping generation…", "warning");
    } catch (e) {
      if (btn) { btn.disabled = false; btn.textContent = "⏹ Stop"; }
      if (window.WVG && WVG.toast) WVG.toast("Could not stop generation", "error", e.message);
    }
    tick();  // refresh strip state promptly
  }

  async function tick() {
    var hasActive = false;
    try {
      var data = await fetch("/api/generation/active").then(function (r) { return r.json(); });
      var active = data.active || [];
      var recent = data.recent || [];
      hasActive = active.length > 0;
      render(active, recent);
      // Let page-local UIs (project editor) sync with the running job too.
      document.dispatchEvent(new CustomEvent("wvg:jobs", { detail: { active: active, recent: recent } }));
    } catch (e) { /* backend unreachable — keep quiet, retry on next tick */ }
    schedule(hasActive ? pollMs : idleMs);
  }

  function schedule(ms) {
    clearTimeout(timer);
    timer = setTimeout(function () {
      if (document.hidden) { schedule(idleMs); return; }
      tick();
    }, ms);
  }

  document.addEventListener("DOMContentLoaded", function () {
    strip = document.getElementById("gen-progress-strip");
    if (!strip) return;
    pollMs = Math.max(parseInt(strip.dataset.pollMs, 10) || 1000, 250);
    tick(); // recover any active job immediately on page load
  });
})();
