"""System resource monitor: CPU, RAM, GPU, VRAM and disk usage.

CPU/RAM/disk come from psutil. GPU/VRAM come from NVML (nvidia-ml-py) with a
torch.cuda fallback. Every metric degrades gracefully: if a source is
unavailable the corresponding block reports {"available": false} and the
application keeps running.
"""

from __future__ import annotations

import threading

from app.config import logger, settings

_NVML_LOCK = threading.Lock()
_nvml_state = {"tried": False, "handle": None, "name": None}


def _round_gb(value_bytes: float) -> float:
    return round(value_bytes / (1024 ** 3), 1)


def _cpu_ram_disk() -> tuple[dict, dict, dict]:
    try:
        import psutil
    except ImportError:
        return ({"available": False}, {"available": False}, {"available": False})

    # interval=None: non-blocking, usage since the previous call.
    cpu = {"available": True, "usage_percent": round(psutil.cpu_percent(interval=None), 1)}

    vm = psutil.virtual_memory()
    ram = {
        "available": True,
        "used_gb": _round_gb(vm.total - vm.available),
        "total_gb": _round_gb(vm.total),
        "usage_percent": round(vm.percent, 1),
    }

    disk_path = settings.system_monitor_disk_path
    warn_at = settings.system_monitor_disk_warning_percent
    crit_at = settings.system_monitor_disk_critical_percent
    try:
        du = psutil.disk_usage(str(disk_path))
        pct = round(du.percent, 1)
        disk = {
            "available": True,
            "path": str(disk_path),
            "used_gb": _round_gb(du.used),
            "total_gb": _round_gb(du.total),
            "free_gb": _round_gb(du.free),
            "percent": pct,
            "usage_percent": pct,  # legacy alias, kept for compatibility
            "warning": pct >= warn_at,
            "critical": pct >= crit_at,
            "warning_percent": warn_at,
            "critical_percent": crit_at,
        }
    except OSError as exc:
        logger.warning("System monitor: disk usage failed for %s: %s", disk_path, exc)
        disk = {"available": False, "path": str(disk_path), "error": str(exc)}
    return cpu, ram, disk


def _nvml_handle():
    """Initialize NVML once; returns a device handle or None."""
    with _NVML_LOCK:
        if not _nvml_state["tried"]:
            _nvml_state["tried"] = True
            try:
                import pynvml

                pynvml.nvmlInit()
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", errors="replace")
                _nvml_state["handle"] = handle
                _nvml_state["name"] = name
                logger.info("System monitor: NVML initialized (%s)", name)
            except Exception as exc:  # noqa: BLE001 — any NVML failure means "no GPU stats"
                logger.info("System monitor: NVML unavailable (%s) — trying torch fallback", exc)
        return _nvml_state["handle"]


def _gpu_vram() -> tuple[dict, dict]:
    handle = _nvml_handle()
    if handle is not None:
        try:
            import pynvml

            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            gpu = {"available": True, "name": _nvml_state["name"],
                   "usage_percent": round(float(util.gpu), 1)}
            vram = {"available": True,
                    "used_gb": _round_gb(mem.used),
                    "total_gb": _round_gb(mem.total),
                    "usage_percent": round(mem.used / mem.total * 100, 1) if mem.total else 0.0}
            return gpu, vram
        except Exception as exc:  # noqa: BLE001 — NVML can fail at runtime (driver reset etc.)
            logger.warning("System monitor: NVML query failed: %s", exc)

    # torch fallback: no utilization %, but VRAM numbers are available.
    try:
        import torch

        if torch.cuda.is_available():
            free_b, total_b = torch.cuda.mem_get_info(0)
            used_b = total_b - free_b
            gpu = {"available": True, "name": torch.cuda.get_device_name(0),
                   "usage_percent": None}
            vram = {"available": True, "used_gb": _round_gb(used_b),
                    "total_gb": _round_gb(total_b),
                    "usage_percent": round(used_b / total_b * 100, 1) if total_b else 0.0}
            return gpu, vram
    except Exception:  # noqa: BLE001 — no torch / no CUDA is a normal situation
        pass
    return {"available": False}, {"available": False}


def get_system_stats() -> dict:
    """JSON-safe snapshot of current system resource usage."""
    cpu, ram, disk = _cpu_ram_disk()
    if settings.system_monitor_show_gpu:
        gpu, vram = _gpu_vram()
    else:
        gpu, vram = {"available": False}, {"available": False}
    return {"cpu": cpu, "ram": ram, "gpu": gpu, "vram": vram, "disk": disk}
