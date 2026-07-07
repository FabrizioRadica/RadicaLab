/* Generation: submit job, poll status, update preview, export workflow, save video */

window.WVGGen = (function () {
  "use strict";

  var pollTimer = null;
  var latestOutput = null;
  var currentJobId = null;

  // Statuses during which the Stop button is shown (patch6b §2).
  var ACTIVE_STATUSES = ["pending", "running", "cancel_requested"];

  function el(id) { return document.getElementById(id); }

  function setStopVisible(job) {
    var row = el("stop-generation-row");
    if (!row) return;
    var active = ACTIVE_STATUSES.indexOf(job.status) !== -1;
    row.style.display = active ? "" : "none";
    var btn = el("btn-stop-generation");
    if (btn) {
      // Once cancellation is requested, keep the button visible but disabled
      // and relabelled so the user sees it is being processed.
      if (job.status === "cancel_requested" || job.cancel_requested) {
        btn.disabled = true;
        btn.innerHTML = "⏳ Stopping…";
      } else {
        btn.disabled = false;
        btn.innerHTML = "⏹ Stop Generation";
      }
    }
  }

  function setProgress(job) {
    currentJobId = job.id;
    el("progress-wrap").classList.add("active");
    el("progress-fill").style.width = (job.progress || 0) + "%";
    el("progress-stage").textContent = job.stage || job.status;
    el("progress-pct").textContent = (job.progress || 0) + "%";
    var log = el("job-log");
    log.textContent = (job.log || []).join("\n");
    log.scrollTop = log.scrollHeight;
    setStopVisible(job);
    if (job.diagnostics) renderDiagnostics(job.diagnostics);
  }

  async function cancelGeneration() {
    if (!currentJobId) return;
    if (!window.confirm("Stop the current generation?\nThe partial output may be deleted.")) return;
    var btn = el("btn-stop-generation");
    if (btn) { btn.disabled = true; btn.innerHTML = "⏳ Stopping…"; }
    el("progress-stage").textContent = "Stopping…";
    try {
      await WVG.api("/api/generation/jobs/" + currentJobId + "/cancel", { method: "POST" });
      WVG.toast("Stopping generation…", "warning");
    } catch (e) {
      if (btn) { btn.disabled = false; btn.innerHTML = "⏹ Stop Generation"; }
      WVG.toast("Could not stop generation", "error", e.message);
    }
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  /* Backend Diagnostics panel: honest requested-vs-effective view of what the
     backend actually did — device/dtype placement, timings, VRAM, warnings. */
  function renderDiagnostics(d) {
    var panel = el("backend-diagnostics");
    if (!panel || !d) return;
    panel.style.display = "";

    var warnEl = el("diagnostics-warnings");
    var warnings = d.warnings || [];
    var presetNotes = d.preset_notes || [];
    if (warnings.length || presetNotes.length) {
      var items = presetNotes.map(function (n) { return "<li class='diag-note'>ⓘ " + esc(n) + "</li>"; })
        .concat(warnings.map(function (w) { return "<li>⚠ " + esc(w) + "</li>"; }));
      warnEl.innerHTML = "<ul>" + items.join("") + "</ul>";
      warnEl.style.display = "";
    } else {
      warnEl.innerHTML = "<div class='diag-ok'>✓ No fallbacks — all requested settings were applied.</div>";
      warnEl.style.display = "";
    }

    var req = d.requested_settings || {};
    var eff = d.effective_settings || {};
    var html = "";

    // Sampler backend routing (patch6c §13): requested vs effective sampler,
    // which backend actually rendered, and whether a fallback/route happened.
    var sb = d.sampler_backend;
    if (sb) {
      var okSampler = sb.requested_sampler_name && sb.effective_sampler_name &&
        String(sb.requested_sampler_name) === String(sb.effective_sampler_name);
      html += "<div class='diag-subhead'>Sampler backend</div>";
      if (okSampler && !sb.routed_to_comfyui) {
        html += "<div class='diag-ok'>✓ Sampler OK: requested sampler matches effective sampler (" + esc(sb.effective_sampler_name) + ").</div>";
      } else if (sb.routed_to_comfyui) {
        html += "<div class='diag-note'>ⓘ Routed to ComfyUI backend — sampler \"" + esc(sb.requested_sampler_name) + "\" used exactly.</div>";
      } else if (sb.fallback_applied) {
        html += "<div class='diag-warnings'>⚠ Sampler fallback: requested \"" + esc(sb.requested_sampler_name) + "\" → effective \"" + esc(sb.effective_sampler_name) + "\".</div>";
      }
      html += "<table class='diag-table'><tbody>" +
        "<tr><td>Requested sampler</td><td>" + esc(sb.requested_sampler_name || "—") + "</td></tr>" +
        "<tr><td>Effective sampler</td><td>" + esc(sb.effective_sampler_name || "—") + "</td></tr>" +
        "<tr><td>Requested scheduler</td><td>" + esc(sb.requested_scheduler || "—") + "</td></tr>" +
        "<tr><td>Effective scheduler</td><td>" + esc(sb.effective_scheduler || "—") + "</td></tr>" +
        "<tr><td>Selected backend</td><td>" + esc(sb.selected_render_backend || "—") + "</td></tr>" +
        "<tr><td>Effective backend</td><td>" + esc(sb.effective_render_backend || "—") + "</td></tr>" +
        "<tr><td>Fallback policy</td><td>" + esc(sb.fallback_policy || "—") + "</td></tr>" +
        "<tr><td>Fallback applied</td><td>" + (sb.fallback_applied ? "yes" : "no") + "</td></tr>" +
        "<tr><td>Routed to ComfyUI</td><td>" + (sb.routed_to_comfyui ? "yes" : "no") + "</td></tr>" +
        "</tbody></table>";
    }

    // Model sampling modifier (patch7 §13): requested vs effective ModelSamplingSD3.
    var ms = d.model_sampling;
    if (ms) {
      var req = ms.requested || {}, eff = ms.effective || {};
      var applied = !!ms.applied;
      html += "<div class='diag-subhead'>Model Sampling Modifier</div>";
      if (applied) {
        html += "<div class='diag-ok'>✓ ModelSamplingSD3 applied before sampling (shift " + esc(eff.shift) + ") on " + esc(ms.backend) + " backend.</div>";
      } else if (req.enabled) {
        html += "<div class='diag-warnings'>⚠ ModelSamplingSD3 requested but NOT applied.</div>";
      } else {
        html += "<div class='diag-note'>ⓘ ModelSamplingSD3 disabled — model default flow shift used.</div>";
      }
      html += "<table class='diag-table'><tbody>" +
        "<tr><td>Requested modifier</td><td>" + (req.enabled ? "ModelSamplingSD3" : "None") + "</td></tr>" +
        "<tr><td>Effective modifier</td><td>" + (eff.enabled ? "ModelSamplingSD3" : "None") + "</td></tr>" +
        "<tr><td>Requested shift</td><td>" + esc(req.shift == null ? "—" : req.shift) + "</td></tr>" +
        "<tr><td>Effective shift</td><td>" + esc(eff.shift == null ? "—" : eff.shift) + "</td></tr>" +
        "<tr><td>Applied before sampling</td><td>" + (applied ? "Yes" : "No") + "</td></tr>" +
        "<tr><td>Backend</td><td>" + esc(ms.backend || "—") + "</td></tr>" +
        "</tbody></table>";
      (ms.warnings || []).forEach(function (w) {
        html += "<div class='diag-note'>ⓘ " + esc(w) + "</div>";
      });
    }

    // VRAM cleanup (patch8 §21): active policy + last cleanup before/after MB.
    var vcs = d.vram_cleanup_settings;
    var vc = d.vram_cleanup && (d.vram_cleanup.after_generation || d.vram_cleanup.after_cancel);
    if (vcs || vc) {
      html += "<div class='diag-subhead'>VRAM Cleanup</div>";
      if (vcs) {
        html += "<table class='diag-table'><tbody>" +
          "<tr><td>Cleanup after generation</td><td>" + (vcs.clear_after_generation ? "yes" : "no") + "</td></tr>" +
          "<tr><td>Cleanup after cancel</td><td>" + (vcs.clear_after_cancel ? "yes" : "no") + "</td></tr>" +
          "<tr><td>Unload model after generation</td><td>" + (vcs.unload_model_after_generation ? "yes" : "no") + "</td></tr>" +
          "<tr><td>Unload model after cancel</td><td>" + (vcs.unload_model_after_cancel ? "yes" : "no") + "</td></tr>" +
          "<tr><td>Keep model warm</td><td>" + (vcs.keep_model_warm ? "yes" : "no") + "</td></tr>" +
          "</tbody></table>";
      }
      if (vc) {
        if (!vc.cuda_available) {
          html += "<div class='diag-note'>ⓘ CUDA cleanup unavailable — garbage collection only.</div>";
        }
        html += "<table class='diag-table'><tbody>" +
          "<tr><td>Last cleanup reason</td><td>" + esc(vc.reason || "—") + "</td></tr>" +
          "<tr><td>gc.collect / empty_cache / ipc</td><td>" +
            (vc.gc_collect ? "✓" : "✗") + " / " + (vc.empty_cache ? "✓" : "✗") + " / " + (vc.ipc_collect ? "✓" : "✗") + "</td></tr>" +
          "<tr><td>Allocated MB (before → after)</td><td>" + esc(vc.before_allocated_mb == null ? "—" : vc.before_allocated_mb) + " → " + esc(vc.after_allocated_mb == null ? "—" : vc.after_allocated_mb) + "</td></tr>" +
          "<tr><td>Reserved MB (before → after)</td><td>" + esc(vc.before_reserved_mb == null ? "—" : vc.before_reserved_mb) + " → " + esc(vc.after_reserved_mb == null ? "—" : vc.after_reserved_mb) + "</td></tr>" +
          "<tr><td>Model unloaded</td><td>" + (vc.models_unloaded ? "yes" : "no") + "</td></tr>" +
          "</tbody></table>";
        (vc.errors || []).forEach(function (e) {
          html += "<div class='diag-note'>⚠ cleanup: " + esc(e) + "</div>";
        });
        html += "<div class='diag-note'>ⓘ Cleanup clears PyTorch's temporary cache and collectible tensors. It does not necessarily free VRAM held by live model objects unless the model is unloaded.</div>";
      }
    }

    // Requested vs Effective comparison (patch6 §5).
    var rows = [
      ["Model bundle", "model_bundle_id"], ["Precision", "precision"],
      ["Sampler", "sampler_name"], ["Scheduler", "scheduler"],
      ["Denoise", "denoise"], ["CFG / Guidance", "cfg"], ["Steps", "steps"],
      ["Seed", "seed"], ["Resolution", "resolution"], ["Frames", "frames"],
      ["FPS", "fps"], ["Offload policy", "offload_policy"]
    ];
    html += "<table class='diag-table'><thead><tr><th>Setting</th><th>Requested</th><th>Effective</th></tr></thead><tbody>";
    rows.forEach(function (r) {
      var rv = req[r[1]], ev = eff[r[1]];
      if (rv === undefined && ev === undefined) return;
      var diff = (rv !== undefined && ev !== undefined && String(rv) !== String(ev));
      html += "<tr" + (diff ? " class='diag-diff'" : "") + "><td>" + esc(r[0]) + "</td><td>" +
        esc(rv === undefined ? "—" : rv) + "</td><td>" + esc(ev === undefined ? "—" : ev) +
        (diff ? " ⚠" : "") + "</td></tr>";
    });
    html += "</tbody></table>";

    // Device + dtype map (patch6 §4).
    var dm = d.device_map || {}, dt = d.dtype_map || {};
    var comps = Object.keys(dm).length ? Object.keys(dm) : Object.keys(dt);
    if (comps.length) {
      html += "<div class='diag-subhead'>Component placement</div><table class='diag-table'><thead><tr><th>Component</th><th>Device</th><th>DType</th></tr></thead><tbody>";
      comps.forEach(function (c) {
        html += "<tr><td>" + esc(c) + "</td><td>" + esc(dm[c] || "—") + "</td><td>" + esc(dt[c] || "—") + "</td></tr>";
      });
      html += "</tbody></table>";
    }

    // Timings (patch6 §14).
    var t = d.timings || {};
    var tkeys = Object.keys(t).filter(function (k) { return t[k]; });
    if (tkeys.length) {
      html += "<div class='diag-subhead'>Timing breakdown (s)</div><table class='diag-table'><tbody>";
      tkeys.forEach(function (k) {
        html += "<tr><td>" + esc(k.replace(/_seconds$/, "").replace(/_/g, " ")) + "</td><td>" + esc(t[k]) + "</td></tr>";
      });
      html += "</tbody></table>";
    }

    // VRAM (patch6 §15).
    var g = d.gpu_memory || {};
    var gkeys = Object.keys(g);
    if (gkeys.length) {
      html += "<div class='diag-subhead'>GPU memory (MB)</div><table class='diag-table'><tbody>";
      gkeys.forEach(function (k) {
        html += "<tr><td>" + esc(k.replace(/_mb$/, "").replace(/_/g, " ")) + "</td><td>" + esc(g[k]) + "</td></tr>";
      });
      html += "</tbody></table>";
    }

    // Image preprocessing (patch6 §10).
    var ip = d.image_preprocessing;
    if (ip) {
      html += "<div class='diag-subhead'>Image preprocessing</div><table class='diag-table'><tbody>";
      Object.keys(ip).forEach(function (k) {
        html += "<tr><td>" + esc(k.replace(/_/g, " ")) + "</td><td>" + esc(ip[k]) + "</td></tr>";
      });
      html += "</tbody></table>";
    }

    el("diagnostics-body").innerHTML = html;
  }

  function showOutput(filename, previewName) {
    if (!WVG.project) return;
    var url = "/media/projects/" + WVG.project.id + "/outputs/" + filename;
    latestOutput = { filename: filename, url: url };
    var stage = el("preview-stage");
    var video = el("preview-video");
    var image = el("preview-image");
    var placeholder = el("preview-placeholder");
    placeholder.style.display = "none";
    stage.classList.toggle("portrait", WVG.project.resolution.height > WVG.project.resolution.width);
    if (/\.webp$/i.test(filename)) {
      video.style.display = "none";
      video.removeAttribute("src");
      image.src = url + "?t=" + Date.now();
      image.style.display = "";
    } else {
      image.style.display = "none";
      video.src = url + "?t=" + Date.now();
      video.style.display = "";
    }
    el("preview-caption").textContent = filename;
    el("btn-save-video").disabled = false;
  }

  function pollJob(jobId) {
    clearInterval(pollTimer);
    pollTimer = setInterval(async function () {
      var job;
      try {
        job = await WVG.api("/api/jobs/" + jobId);
      } catch (e) {
        clearInterval(pollTimer);
        finishUi();
        WVG.toast("Lost contact with the generation job", "error", e.message);
        return;
      }
      setProgress(job);
      if (job.status === "completed") {
        clearInterval(pollTimer);
        finishUi();
        WVG.toast("Video generated" + (job.is_mock ? " (mock backend)" : ""), "success");
        if (job.output_file) showOutput(job.output_file);
        setTimeout(function () { window.location = "/?project=" + WVG.project.id; }, 1400);
      } else if (job.status === "cancelled") {
        clearInterval(pollTimer);
        finishUi();
        el("progress-stage").textContent = "Generation cancelled.";
        WVG.toast("Generation cancelled", "warning");
      } else if (job.status === "failed") {
        clearInterval(pollTimer);
        finishUi();
        WVG.toast("Generation failed", "error", job.error || "");
      }
    }, 800);
  }

  function finishUi() {
    var btn = el("btn-generate");
    btn.disabled = false;
    btn.innerHTML = "✦ Generate Video";
    var row = el("stop-generation-row");
    if (row) row.style.display = "none";
  }

  /* Disk safety check before generation: warn above the warning threshold,
     ask for confirmation above the critical threshold. A failing disk monitor
     never blocks generation. */
  async function diskAllowsGeneration() {
    var disk;
    try {
      var res = await WVG.api("/api/system/stats");
      disk = res && res.stats && res.stats.disk;
    } catch (e) { return true; }
    if (!disk || disk.available === false) return true;
    var pct = Math.round(disk.percent || 0);
    if (disk.critical) {
      WVG.toast("Disk usage is high: " + pct + "%. Video generation may fail because of insufficient free space.", "error");
      return window.confirm(
        "Disk usage is high: " + pct + "% (" + disk.free_gb + " GB free).\n" +
        "Video generation may fail because of insufficient free space.\n\n" +
        "Start generation anyway?"
      );
    }
    if (disk.warning) {
      WVG.toast("Disk usage is above " + (disk.warning_percent || 75) + "%. Consider freeing space before long generations.", "warning");
    }
    return true;
  }

  async function generate() {
    if (!WVG.project) return;
    var btn = el("btn-generate");
    btn.disabled = true;
    btn.textContent = "Saving project…";

    var saved = await WVG.saveProject(true);
    if (!saved) { finishUi(); return; }

    btn.textContent = "Checking disk space…";
    var diskOk = await diskAllowsGeneration();
    if (!diskOk) { finishUi(); return; }

    btn.textContent = "Starting generation…";
    try {
      var job = await WVG.api("/api/generate", { method: "POST", body: { project_id: WVG.project.id } });
      setProgress(job);
      btn.textContent = "Generating…";
      pollJob(job.id);
    } catch (e) {
      finishUi();
      WVG.toast("Could not start generation", "error", e.message);
    }
  }

  async function exportWorkflow() {
    if (!WVG.project) return;
    var saved = await WVG.saveProject(true);
    if (!saved) return;
    try {
      var res = await WVG.api("/api/projects/" + WVG.project.id + "/export-workflow", { method: "POST" });
      WVG.toast("Workflow exported: " + res.filename, "success");
    } catch (e) {
      WVG.toast("Workflow export failed", "error", e.message);
    }
  }

  function saveVideo() {
    if (!latestOutput) {
      WVG.toast("No generated video to save yet", "error");
      return;
    }
    var a = document.createElement("a");
    a.href = latestOutput.url + "?download=1";
    a.download = latestOutput.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    WVG.toast("Download started — the video is also saved in the project outputs folder", "success");
  }

  /* Parity comparison tool (patch6 §18): save a JSON diff of requested vs
     effective (direct backend) vs exported ComfyUI workflow settings. */
  async function compareWithComfyui() {
    if (!WVG.project) return;
    var saved = await WVG.saveProject(true);
    if (!saved) return;
    try {
      var res = await WVG.api("/api/projects/" + WVG.project.id + "/compare-comfyui", { method: "POST" });
      var n = (res.mismatches || []).length;
      WVG.toast(
        n ? (n + " setting mismatch(es) found — saved to " + res.filename)
          : "No configuration mismatches — saved to " + res.filename,
        n ? "warning" : "success"
      );
    } catch (e) {
      WVG.toast("Comparison failed", "error", e.message);
    }
  }

  /* Recover an active job for this project after navigation or refresh —
     the job lives server-side, so the editor re-attaches to it on load. */
  async function resumeActiveJob() {
    if (!WVG.project) return;
    try {
      var jobs = await WVG.api("/api/projects/" + WVG.project.id + "/generation/jobs");
      var active = (jobs || []).find(function (j) {
        return ACTIVE_STATUSES.indexOf(j.status) !== -1;
      });
      if (!active) return;
      var btn = el("btn-generate");
      btn.disabled = true;
      btn.textContent = "Generating…";
      setProgress(active);
      pollJob(active.id);
    } catch (e) { /* no job to recover */ }
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!el("btn-generate")) return;
    resumeActiveJob();
    el("btn-generate").addEventListener("click", generate);
    el("btn-export-workflow").addEventListener("click", exportWorkflow);
    el("btn-save-video").addEventListener("click", saveVideo);
    el("preview-reload").addEventListener("click", function () {
      if (latestOutput) showOutput(latestOutput.filename);
      else WVG.toast("Nothing to reload yet");
    });
    el("preview-fullscreen").addEventListener("click", function () {
      var stage = el("preview-stage");
      if (stage.requestFullscreen) stage.requestFullscreen();
    });
    var cmpBtn = el("btn-compare-comfyui");
    if (cmpBtn) cmpBtn.addEventListener("click", compareWithComfyui);
    var stopBtn = el("btn-stop-generation");
    if (stopBtn) stopBtn.addEventListener("click", cancelGeneration);

    // Show the most recent generated video on load.
    if (WVG.project && WVG.project.generated_videos && WVG.project.generated_videos.length) {
      var last = WVG.project.generated_videos[WVG.project.generated_videos.length - 1];
      showOutput(last.filename, last.preview);
    }
  });

  return { showOutput: showOutput };
})();
