"""Optional ComfyUI API render backend (patch6c §8/§11).

Used only when the sampler fallback policy routes an unsupported-in-direct
sampler to ComfyUI. It submits the exported workflow to a running ComfyUI
instance over its HTTP API and downloads the resulting video. It is a genuine
client — never a stub: if ComfyUI is not reachable or the render fails, it
raises a clear error instead of silently falling back to the direct backend.

Compatibility caveat (shared with the workflow export): the exported graph uses
common Wan/video node class names. If the local ComfyUI does not have those node
packs installed, the submitted prompt will error — that error is surfaced to the
user verbatim, and no substitute sampler is ever used.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from app.config import logger, settings


class ComfyUIError(Exception):
    """Raised for ComfyUI API failures with a user-readable message."""


def _base_url() -> str:
    return settings.comfyui_api_url.rstrip("/")


def is_available() -> bool:
    """True when ComfyUI routing is enabled AND a ComfyUI instance answers.

    Reachability is checked with a short timeout so the UI/preflight never hangs
    (patch6c §8 route_to_comfyui: 'if not available, block')."""
    if not settings.comfyui_api_enabled:
        return False
    try:
        import requests

        resp = requests.get(f"{_base_url()}/system_stats", timeout=2)
        return resp.status_code == 200
    except Exception as exc:  # noqa: BLE001 — any failure means 'not available'
        logger.info("ComfyUI backend not reachable at %s: %s", _base_url(), exc)
        return False


def unavailable_reason() -> str:
    if not settings.comfyui_api_enabled:
        return "ComfyUI backend is disabled (set COMFYUI_API_ENABLED=true)."
    return (f"ComfyUI backend is enabled but not reachable at {_base_url()} "
            "(is ComfyUI running?).")


def _post_prompt(client_id: str, workflow_nodes: dict) -> str:
    import requests

    payload = {"prompt": workflow_nodes, "client_id": client_id}
    try:
        resp = requests.post(f"{_base_url()}/prompt", json=payload,
                             timeout=30)
    except Exception as exc:  # noqa: BLE001
        raise ComfyUIError(f"Could not submit the workflow to ComfyUI: {exc}") from exc
    if resp.status_code != 200:
        raise ComfyUIError(
            f"ComfyUI rejected the workflow (HTTP {resp.status_code}): {resp.text[:400]}. "
            "The exported graph may use node classes that are not installed in your "
            "ComfyUI. No sampler substitution was performed.")
    data = resp.json()
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise ComfyUIError(f"ComfyUI did not return a prompt_id: {json.dumps(data)[:400]}")
    return prompt_id


def _poll_history(prompt_id: str) -> dict:
    import requests

    deadline = time.time() + max(settings.comfyui_api_timeout_seconds, 30)
    while time.time() < deadline:
        try:
            resp = requests.get(f"{_base_url()}/history/{prompt_id}", timeout=15)
        except Exception as exc:  # noqa: BLE001
            raise ComfyUIError(f"Lost connection to ComfyUI while rendering: {exc}") from exc
        if resp.status_code == 200:
            hist = resp.json().get(prompt_id)
            if hist:
                status = (hist.get("status") or {})
                if status.get("status_str") == "error" or status.get("completed") is False and status.get("messages"):
                    # Surface ComfyUI execution errors verbatim.
                    raise ComfyUIError(
                        "ComfyUI reported an execution error: "
                        + json.dumps(status.get("messages", []))[:600])
                if hist.get("outputs"):
                    return hist
        time.sleep(2)
    raise ComfyUIError(
        f"ComfyUI render timed out after {settings.comfyui_api_timeout_seconds}s.")


def _download_output(hist: dict, output_path: Path) -> None:
    import requests

    # Find the first video/gif output produced by the workflow.
    for node_out in hist.get("outputs", {}).values():
        for key in ("videos", "gifs", "images"):
            for item in node_out.get(key, []) or []:
                params = {"filename": item.get("filename", ""),
                          "subfolder": item.get("subfolder", ""),
                          "type": item.get("type", "output")}
                try:
                    resp = requests.get(f"{_base_url()}/view", params=params, timeout=120)
                except Exception as exc:  # noqa: BLE001
                    raise ComfyUIError(f"Could not download the ComfyUI output: {exc}") from exc
                if resp.status_code == 200 and resp.content:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(resp.content)
                    return
    raise ComfyUIError(
        "ComfyUI finished but produced no downloadable video output. Check that the "
        "workflow contains a save/output node compatible with your installation.")


def render(workflow: dict, output_path: Path, preview_path: Path) -> dict:
    """Submit `workflow['nodes']` to ComfyUI, wait for completion, download the
    video to `output_path` and write a preview thumbnail. Returns a small report
    dict. Raises ComfyUIError on any failure (never silent)."""
    if not settings.comfyui_api_enabled:
        raise ComfyUIError(unavailable_reason())
    nodes = workflow.get("nodes")
    if not nodes:
        raise ComfyUIError("The workflow to render has no nodes.")

    client_id = uuid.uuid4().hex
    started = time.perf_counter()
    logger.info("Routing generation to ComfyUI backend at %s", _base_url())
    prompt_id = _post_prompt(client_id, nodes)
    hist = _poll_history(prompt_id)
    _download_output(hist, output_path)

    # Best-effort preview thumbnail from the first video frame.
    try:
        _write_preview(output_path, preview_path)
    except Exception as exc:  # noqa: BLE001 — preview is non-critical
        logger.info("Could not create ComfyUI preview thumbnail: %s", exc)

    return {
        "effective_render_backend": "comfyui",
        "prompt_id": prompt_id,
        "comfyui_url": _base_url(),
        "duration_seconds": round(time.perf_counter() - started, 3),
    }


def _write_preview(video_path: Path, preview_path: Path) -> None:
    import imageio_ffmpeg
    import numpy as np
    from PIL import Image

    reader = imageio_ffmpeg.read_frames(str(video_path))
    meta = reader.__next__()
    w, h = meta["size"]
    frame = reader.__next__()
    reader.close()
    arr = np.frombuffer(frame, dtype=np.uint8).reshape((h, w, 3))
    Image.fromarray(arr).save(preview_path, quality=90)
