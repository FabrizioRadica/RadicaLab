/* System resource monitor — polls /api/system/stats and updates the topbar pills.
   Radica - WanVideoGenerator — Concept & Design: Fabrizio Radica — Project by RadicaDesign */

(function () {
  "use strict";

  var box = null;
  var failures = 0;

  function el(id) { return document.getElementById(id); }

  function setPill(id, text, pct, warnAt, critAt) {
    var pill = el(id);
    if (!pill) return;
    pill.textContent = text;
    pill.classList.remove("warn", "crit");
    if (typeof pct === "number") {
      if (critAt != null && pct >= critAt) pill.classList.add("crit");
      else if (warnAt != null && pct >= warnAt) pill.classList.add("warn");
    }
  }

  function gb(v) { return (v == null) ? "?" : v.toFixed ? v.toFixed(1) : v; }

  function render(stats) {
    var cpu = stats.cpu || {}, ram = stats.ram || {}, gpu = stats.gpu || {},
        vram = stats.vram || {}, disk = stats.disk || {};

    setPill("sm-cpu", cpu.available === false ? "CPU N/A" : "CPU " + Math.round(cpu.usage_percent) + "%",
            cpu.usage_percent, 90, 97);
    setPill("sm-ram", ram.available === false ? "RAM N/A" :
            "RAM " + gb(ram.used_gb) + "/" + gb(ram.total_gb) + " GB",
            ram.usage_percent, 85, 95);

    if (box.dataset.showGpu === "true") {
      setPill("sm-gpu", !gpu.available ? "GPU N/A" :
              (gpu.usage_percent == null ? "GPU —" : "GPU " + Math.round(gpu.usage_percent) + "%"),
              gpu.usage_percent, 95, 99);
      var gpuPill = el("sm-gpu");
      if (gpuPill && gpu.name) gpuPill.title = gpu.name;
      setPill("sm-vram", !vram.available ? "VRAM N/A" :
              "VRAM " + gb(vram.used_gb) + "/" + gb(vram.total_gb) + " GB",
              vram.usage_percent, 85, 95);
    } else {
      var g = el("sm-gpu"), v = el("sm-vram");
      if (g) g.style.display = "none";
      if (v) v.style.display = "none";
    }

    if (box.dataset.showDisk === "true") {
      // "Disk 51% · 488 GB free" — warn/crit thresholds come from the server
      // (SYSTEM_MONITOR_DISK_WARNING_PERCENT / _CRITICAL_PERCENT).
      var diskText = disk.available === false ? "Disk N/A" :
              "Disk " + Math.round(disk.percent || 0) + "% · " + gb(disk.free_gb) + " GB free";
      setPill("sm-disk", diskText, disk.percent,
              disk.warning_percent != null ? disk.warning_percent : 75,
              disk.critical_percent != null ? disk.critical_percent : 90);
      var d = el("sm-disk");
      if (d) {
        if (disk.available === false) d.title = "Disk stats unavailable" + (disk.error ? ": " + disk.error : "");
        else if (disk.path) d.title = "Disk usage of the volume containing " + disk.path;
      }
    } else {
      var dp = el("sm-disk");
      if (dp) dp.style.display = "none";
    }
  }

  async function tick() {
    try {
      var res = await fetch("/api/system/stats");
      var data = await res.json();
      if (data && data.ok && data.stats) {
        render(data.stats);
        failures = 0;
      } else if (data && data.enabled === false) {
        box.style.display = "none";
        return false; // stop polling — disabled server-side
      }
    } catch (e) {
      failures += 1;
      if (failures > 5) return false; // backend gone — stop polling quietly
    }
    return true;
  }

  document.addEventListener("DOMContentLoaded", function () {
    box = document.getElementById("sysmon");
    if (!box) return;
    var interval = Math.max(parseInt(box.dataset.pollMs, 10) || 2000, 500);
    tick();
    var timer = setInterval(async function () {
      if (document.hidden) return; // don't poll from background tabs
      var ok = await tick();
      if (!ok) clearInterval(timer);
    }, interval);
  });
})();
