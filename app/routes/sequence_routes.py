"""VideoSequenceQueue API (patchRC2 §13).

CRUD for sequences and clips, sequential render controls (render/stop/resume/
regenerate/skip), merge + sequence-audio, status polling, asset uploads and
media serving. All render work is delegated to the real queue runner
(sequence_queue_service); these routes never render inline.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.services import media_service, sequence_service
from app.services.audio_service import AudioError, EDITABLE_TRACK_FIELDS
from app.services.sequence_queue_service import queue_manager
from app.services.sequence_service import SequenceError

router = APIRouter()


async def _json_body(request: Request) -> dict:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object.")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="Request body must be a JSON object.")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object.")
    return payload


def _err(exc: SequenceError) -> HTTPException:
    status = 404 if "not found" in str(exc).lower() else 400
    return HTTPException(status_code=status, detail=str(exc))


# ==========================================================================
# Sequence CRUD
# ==========================================================================
@router.post("/api/sequences")
async def api_create_sequence(request: Request):
    body = await _json_body(request)
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Sequence name is required.")
    try:
        seq = sequence_service.create_sequence(name, body.get("description", ""),
                                               body.get("model_id", ""))
    except SequenceError as exc:
        raise _err(exc)
    return seq.to_json_dict()


@router.get("/api/sequences")
async def api_list_sequences():
    return {"sequences": sequence_service.list_sequences()}


@router.get("/api/sequences/{sequence_id}")
async def api_get_sequence(sequence_id: str):
    try:
        return sequence_service.load_sequence(sequence_id).to_json_dict()
    except SequenceError as exc:
        raise _err(exc)


@router.put("/api/sequences/{sequence_id}")
async def api_update_sequence(sequence_id: str, request: Request):
    body = await _json_body(request)
    try:
        return sequence_service.update_sequence(sequence_id, body).to_json_dict()
    except SequenceError as exc:
        raise _err(exc)


@router.delete("/api/sequences/{sequence_id}")
async def api_delete_sequence(sequence_id: str):
    if queue_manager.is_running(sequence_id):
        raise HTTPException(status_code=409, detail="Stop the sequence render before deleting it.")
    try:
        name = sequence_service.delete_sequence(sequence_id)
    except SequenceError as exc:
        raise _err(exc)
    return {"deleted": True, "name": name}


# ==========================================================================
# Clip CRUD
# ==========================================================================
@router.post("/api/sequences/{sequence_id}/clips")
async def api_add_clip(sequence_id: str, request: Request):
    body = await _json_body(request)
    try:
        return sequence_service.add_clip(sequence_id, body).model_dump(mode="json")
    except (SequenceError, ValueError) as exc:
        raise _err(exc) if isinstance(exc, SequenceError) else HTTPException(400, str(exc))


@router.put("/api/sequences/{sequence_id}/clips/{clip_id}")
async def api_update_clip(sequence_id: str, clip_id: str, request: Request):
    body = await _json_body(request)
    try:
        return sequence_service.update_clip(sequence_id, clip_id, body).model_dump(mode="json")
    except SequenceError as exc:
        raise _err(exc)


@router.delete("/api/sequences/{sequence_id}/clips/{clip_id}")
async def api_delete_clip(sequence_id: str, clip_id: str):
    try:
        sequence_service.delete_clip(sequence_id, clip_id)
    except SequenceError as exc:
        raise _err(exc)
    return {"deleted": True, "clip_id": clip_id}


@router.post("/api/sequences/{sequence_id}/clips/{clip_id}/duplicate")
async def api_duplicate_clip(sequence_id: str, clip_id: str):
    try:
        return sequence_service.duplicate_clip(sequence_id, clip_id).model_dump(mode="json")
    except SequenceError as exc:
        raise _err(exc)


@router.post("/api/sequences/{sequence_id}/clips/reorder")
async def api_reorder_clips(sequence_id: str, request: Request):
    body = await _json_body(request)
    ordered = body.get("clip_ids") or body.get("order")
    if not isinstance(ordered, list):
        raise HTTPException(status_code=400, detail="'clip_ids' must be a list of clip ids.")
    try:
        return sequence_service.reorder_clips(sequence_id, ordered).to_json_dict()
    except SequenceError as exc:
        raise _err(exc)


# ==========================================================================
# Render controls
# ==========================================================================
@router.post("/api/sequences/{sequence_id}/render")
async def api_render(sequence_id: str, request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — body is optional
        body = {}
    only = body.get("clip_ids") if isinstance(body, dict) else None
    try:
        return queue_manager.render_sequence(sequence_id, only)
    except SequenceError as exc:
        raise _err(exc)


@router.post("/api/sequences/{sequence_id}/stop")
async def api_stop(sequence_id: str):
    try:
        return queue_manager.stop(sequence_id)
    except SequenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/api/sequences/{sequence_id}/resume")
async def api_resume(sequence_id: str):
    try:
        return queue_manager.resume(sequence_id)
    except SequenceError as exc:
        raise _err(exc)


@router.post("/api/sequences/{sequence_id}/resume-from/{clip_id}")
async def api_resume_from(sequence_id: str, clip_id: str):
    try:
        return queue_manager.resume_from(sequence_id, clip_id)
    except SequenceError as exc:
        raise _err(exc)


@router.post("/api/sequences/{sequence_id}/clips/{clip_id}/regenerate")
async def api_regenerate_clip(sequence_id: str, clip_id: str):
    try:
        return queue_manager.regenerate_clip(sequence_id, clip_id)
    except SequenceError as exc:
        raise _err(exc)


@router.post("/api/sequences/{sequence_id}/clips/{clip_id}/skip")
async def api_skip_clip(sequence_id: str, clip_id: str):
    try:
        return queue_manager.skip_clip(sequence_id, clip_id)
    except SequenceError as exc:
        raise _err(exc)


@router.post("/api/sequences/{sequence_id}/merge")
async def api_merge(sequence_id: str):
    import threading

    if queue_manager.is_running(sequence_id):
        raise HTTPException(status_code=409, detail="The sequence is currently rendering.")
    try:
        seq = sequence_service.load_sequence(sequence_id)
    except SequenceError as exc:
        raise _err(exc)
    try:
        queue_manager._merge_and_master(seq, threading.Event())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Merge failed: {exc}")
    return queue_manager.status(sequence_id)


@router.post("/api/sequences/{sequence_id}/apply-sequence-audio")
async def api_apply_sequence_audio(sequence_id: str):
    try:
        seq = sequence_service.load_sequence(sequence_id)
    except SequenceError as exc:
        raise _err(exc)
    if not seq.outputs.merged:
        raise HTTPException(status_code=400, detail="Merge the sequence before applying sequence audio.")
    merged_path = sequence_service.sequence_dir(seq.folder) / "exports" / "merged" / seq.outputs.merged
    if not merged_path.exists():
        raise HTTPException(status_code=400, detail="Merged video file is missing — re-run the merge.")
    tracks = [t for t in seq.sequence_audio_tracks if t.enabled]
    if not tracks:
        raise HTTPException(status_code=400, detail="No enabled sequence audio tracks to apply.")
    from app.services import project_service
    base = project_service.sanitize_folder_name(seq.name)
    try:
        final_name = queue_manager._apply_sequence_audio(seq, merged_path, tracks, base)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Sequence audio failed: {exc}")
    seq.outputs.final = final_name
    sequence_service.save_sequence(seq)
    return queue_manager.status(sequence_id)


@router.get("/api/sequences/{sequence_id}/status")
async def api_status(sequence_id: str):
    try:
        return queue_manager.status(sequence_id)
    except SequenceError as exc:
        raise _err(exc)


# ==========================================================================
# Assets (reference images + audio)
# ==========================================================================
@router.post("/api/sequences/{sequence_id}/assets/image")
async def api_add_image(sequence_id: str, file: UploadFile = File(...)):
    data = await file.read()
    try:
        name = sequence_service.add_image_asset(sequence_id, file.filename or "image.png", data)
    except SequenceError as exc:
        raise _err(exc)
    except Exception as exc:  # noqa: BLE001 — media validation errors
        raise HTTPException(status_code=400, detail=str(exc))
    return {"filename": name, "url": f"/media/sequences/{sequence_id}/asset/image/{name}"}


@router.post("/api/sequences/{sequence_id}/assets/audio")
async def api_add_audio(sequence_id: str, file: UploadFile = File(...),
                        clip_id: str | None = Form(None)):
    data = await file.read()
    try:
        track = sequence_service.add_audio_asset(sequence_id, file.filename or "audio.mp3", data,
                                                 clip_id or None)
    except SequenceError as exc:
        raise _err(exc)
    except AudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return track.model_dump(mode="json")


def _find_track(seq, track_id, clip_id):
    if clip_id:
        clip = seq.get_clip(clip_id)
        if clip is None:
            raise SequenceError(f"Clip '{clip_id}' not found.")
        return clip.clip_audio_tracks, clip
    return seq.sequence_audio_tracks, None


@router.put("/api/sequences/{sequence_id}/audio/{track_id}")
async def api_update_audio_track(sequence_id: str, track_id: str, request: Request):
    body = await _json_body(request)
    clip_id = body.get("clip_id")
    try:
        seq = sequence_service.load_sequence(sequence_id)
        tracks, _ = _find_track(seq, track_id, clip_id)
    except SequenceError as exc:
        raise _err(exc)
    from app.models.project_models import AudioTrack
    from app.models.sequence_models import utc_now
    for i, t in enumerate(tracks):
        if t.id == track_id:
            merged = t.model_dump()
            for key in EDITABLE_TRACK_FIELDS:
                if key in body:
                    merged[key] = body[key]
            merged["updated_at"] = utc_now()
            try:
                tracks[i] = AudioTrack.model_validate(merged)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid track settings: {exc}")
            sequence_service.save_sequence(seq)
            return tracks[i].model_dump(mode="json")
    raise HTTPException(status_code=404, detail=f"Audio track '{track_id}' not found.")


@router.delete("/api/sequences/{sequence_id}/audio/{track_id}")
async def api_delete_audio_track(sequence_id: str, track_id: str, clip_id: str | None = None):
    try:
        seq = sequence_service.load_sequence(sequence_id)
        tracks, _ = _find_track(seq, track_id, clip_id)
    except SequenceError as exc:
        raise _err(exc)
    before = len(tracks)
    tracks[:] = [t for t in tracks if t.id != track_id]
    if len(tracks) == before:
        raise HTTPException(status_code=404, detail=f"Audio track '{track_id}' not found.")
    sequence_service.save_sequence(seq)
    return {"deleted": True, "track_id": track_id}


# ==========================================================================
# Media serving
# ==========================================================================
def _safe_name(name: str) -> str:
    if not name or name != Path(name).name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid file name.")
    return name


@router.get("/media/sequences/{sequence_id}/clip/{clip_id}/{stage}")
async def serve_clip_media(sequence_id: str, clip_id: str, stage: str, download: bool = False):
    if stage not in ("raw", "fx", "final", "preview"):
        raise HTTPException(status_code=400, detail="Invalid stage.")
    try:
        seq = sequence_service.load_sequence(sequence_id)
    except SequenceError as exc:
        raise _err(exc)
    clip = seq.get_clip(clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    cdir = sequence_service.clip_dir(seq, clip)
    if stage == "preview":
        if not clip.outputs.preview:
            raise HTTPException(status_code=404, detail="No preview for this clip yet.")
        path = cdir / clip.outputs.preview
    else:
        fname = getattr(clip.outputs, stage)
        if not fname:
            raise HTTPException(status_code=404, detail=f"No {stage} output for this clip yet.")
        path = cdir / stage / fname
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk.")
    headers = {"Content-Disposition": f'attachment; filename="{path.name}"'} if download else None
    return FileResponse(path, media_type=media_service.content_type_for(path), headers=headers)


@router.get("/media/sequences/{sequence_id}/export/{kind}")
async def serve_sequence_export(sequence_id: str, kind: str, download: bool = False):
    if kind not in ("merged", "final"):
        raise HTTPException(status_code=400, detail="Invalid export kind.")
    try:
        seq = sequence_service.load_sequence(sequence_id)
    except SequenceError as exc:
        raise _err(exc)
    fname = getattr(seq.outputs, kind)
    if not fname:
        raise HTTPException(status_code=404, detail=f"No {kind} output available yet.")
    path = sequence_service.sequence_dir(seq.folder) / "exports" / kind / fname
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk.")
    headers = {"Content-Disposition": f'attachment; filename="{path.name}"'} if download else None
    return FileResponse(path, media_type=media_service.content_type_for(path), headers=headers)


@router.get("/media/sequences/{sequence_id}/asset/image/{filename}")
async def serve_sequence_image(sequence_id: str, filename: str):
    filename = _safe_name(filename)
    try:
        seq = sequence_service.load_sequence(sequence_id)
    except SequenceError as exc:
        raise _err(exc)
    path = sequence_service.assets_images_dir(seq) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found.")
    return FileResponse(path, media_type=media_service.content_type_for(path))


@router.get("/media/sequences/{sequence_id}/asset/audio/{filename}")
async def serve_sequence_audio(sequence_id: str, filename: str):
    """Serve an uploaded sequence audio asset so the reused Audio Tracks module's
    preview player works in the sequence_master / sequence_clip contexts."""
    filename = _safe_name(filename)
    try:
        seq = sequence_service.load_sequence(sequence_id)
    except SequenceError as exc:
        raise _err(exc)
    path = sequence_service.assets_audio_dir(seq) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio not found.")
    return FileResponse(path, media_type=media_service.content_type_for(path))
