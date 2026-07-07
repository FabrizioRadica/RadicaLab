"""Centralized, safe VRAM cleanup (patch8 §13).

Runs after every generation exit path — success, failure, cancel/stop — to
release PyTorch's temporary CUDA cache and collectible tensors. It is deliberately
conservative and honest:

- It NEVER kills processes (no taskkill / nvidia-smi --kill / os.kill).
- It NEVER deletes model files or project files.
- It only unloads model weights when explicitly asked (`unload_models=True`).
- `torch.cuda.empty_cache()` frees cached blocks held by PyTorch; it does not by
  itself free VRAM still referenced by live model objects. We report measured
  before/after numbers rather than claiming "VRAM fully freed" (patch8 §23).
"""

from __future__ import annotations

import gc

from app.config import logger, settings


def _unload_model_cache() -> bool:
    """Unload the cached Wan pipeline (model weights) if one is loaded. Returns
    True when an unload hook ran. Never raises."""
    try:
        from app.services import wan_backend

        wan_backend.unload_pipeline()
        return True
    except Exception as exc:  # noqa: BLE001 — unload must never break cleanup
        logger.warning("Model cache unload failed during VRAM cleanup: %s", exc)
        return False


def cleanup_vram(reason: str = "", unload_models: bool = False) -> dict:
    """Release temporary VRAM and (optionally) unload model weights.

    Returns a structured report with before/after allocated & reserved MB when
    CUDA is available. Safe to call from any thread and any exit path; it catches
    and records every error instead of propagating (patch8 §14/§15/§16)."""
    report: dict = {
        "reason": reason,
        "gc_collect": False,
        "cuda_available": False,
        "empty_cache": False,
        "ipc_collect": False,
        "models_unloaded": False,
        "before_allocated_mb": None,
        "before_reserved_mb": None,
        "after_allocated_mb": None,
        "after_reserved_mb": None,
        "freed_allocated_mb": None,
        "freed_reserved_mb": None,
        "errors": [],
    }

    try:
        try:
            import torch
        except Exception as exc:  # noqa: BLE001 — torch may be absent
            report["errors"].append(f"torch unavailable: {exc}")
            gc.collect()
            report["gc_collect"] = True
            _log(report)
            return report

        cuda_available = bool(getattr(torch, "cuda", None) and torch.cuda.is_available())
        report["cuda_available"] = cuda_available

        if cuda_available:
            try:
                report["before_allocated_mb"] = round(torch.cuda.memory_allocated() / 1024 / 1024, 2)
                report["before_reserved_mb"] = round(torch.cuda.memory_reserved() / 1024 / 1024, 2)
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"cuda before memory read failed: {exc}")

        # Unload model weights only when explicitly requested (patch8 §18).
        if unload_models:
            report["models_unloaded"] = _unload_model_cache()

        gc.collect()
        report["gc_collect"] = True

        if cuda_available:
            try:
                torch.cuda.empty_cache()
                report["empty_cache"] = True
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"empty_cache failed: {exc}")
            try:
                torch.cuda.ipc_collect()
                report["ipc_collect"] = True
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"ipc_collect failed: {exc}")
            try:
                report["after_allocated_mb"] = round(torch.cuda.memory_allocated() / 1024 / 1024, 2)
                report["after_reserved_mb"] = round(torch.cuda.memory_reserved() / 1024 / 1024, 2)
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"cuda after memory read failed: {exc}")
            # Honest measured delta (never claims "fully freed").
            if report["before_allocated_mb"] is not None and report["after_allocated_mb"] is not None:
                report["freed_allocated_mb"] = round(
                    report["before_allocated_mb"] - report["after_allocated_mb"], 2)
            if report["before_reserved_mb"] is not None and report["after_reserved_mb"] is not None:
                report["freed_reserved_mb"] = round(
                    report["before_reserved_mb"] - report["after_reserved_mb"], 2)

        _log(report)
        return report

    except Exception as exc:  # noqa: BLE001 — cleanup must never raise
        report["errors"].append(str(exc))
        logger.warning("VRAM cleanup failed: %s", exc)
        return report


def _log(report: dict) -> None:
    if not settings.wan_log_vram_cleanup:
        return
    if report.get("cuda_available"):
        logger.info(
            "VRAM cleanup (%s): allocated %.1f->%.1f MB, reserved %.1f->%.1f MB, "
            "unloaded=%s, errors=%d",
            report.get("reason", ""),
            report.get("before_allocated_mb") or 0.0, report.get("after_allocated_mb") or 0.0,
            report.get("before_reserved_mb") or 0.0, report.get("after_reserved_mb") or 0.0,
            report.get("models_unloaded"), len(report.get("errors") or []))
    else:
        logger.info("VRAM cleanup (%s): CUDA cleanup unavailable (gc only), errors=%d",
                    report.get("reason", ""), len(report.get("errors") or []))


def cleanup_settings() -> dict:
    """Active cleanup policy for the diagnostics panel (patch8 §21)."""
    return {
        "clear_after_generation": settings.wan_clear_temp_vram_after_generation,
        "clear_after_cancel": settings.wan_clear_temp_vram_after_cancel,
        "unload_model_after_generation": settings.wan_unload_model_after_generation,
        "unload_model_after_cancel": settings.wan_unload_model_after_cancel,
        "keep_model_warm": settings.wan_keep_model_warm,
        "log_vram_cleanup": settings.wan_log_vram_cleanup,
    }


def summarize(report: dict | None) -> str:
    """One-line human summary of a cleanup report for job logs."""
    if not report:
        return "VRAM cleanup: not run."
    if not report.get("cuda_available"):
        return f"VRAM cleanup ({report.get('reason','')}): CUDA cleanup unavailable — gc only."
    ba, aa = report.get("before_allocated_mb"), report.get("after_allocated_mb")
    br, ar = report.get("before_reserved_mb"), report.get("after_reserved_mb")
    parts = [f"VRAM cleanup ({report.get('reason','')}): attempted"]
    if ba is not None and aa is not None:
        parts.append(f"allocated {ba:.0f}->{aa:.0f} MB")
    if br is not None and ar is not None:
        parts.append(f"reserved {br:.0f}->{ar:.0f} MB")
    if report.get("models_unloaded"):
        parts.append("model unloaded")
    if report.get("errors"):
        parts.append(f"{len(report['errors'])} warning(s)")
    return ", ".join(parts) + "."
