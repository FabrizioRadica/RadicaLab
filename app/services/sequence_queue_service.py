"""Sequential VideoSequenceQueue render engine (patchRC2 §9-§11, §16-§19).

A real, thread-based queue runner. It renders clips strictly one at a time
through the existing generation backend, then runs the existing Color & Look and
Audio Tracks post-processing per clip, cleans VRAM between clips, and (when the
output mode requests it) merges the clip finals and applies sequence-master
audio. Nothing here is faked — with the mock backend it produces real mp4 files;
with the wan backend it runs real inference.

Pipeline per clip (§7.2 / §8.2):
    Wan raw render -> Color & Look -> Clip Audio Tracks -> Clip_final
Then, if final merge is enabled:
    merge Clip finals -> Sequence merged -> Sequence Audio -> Sequence final
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

from app.config import logger, settings
from app.models.sequence_models import (
    ClipStatus,
    ColorLookMode,
    OutputMode,
    SequenceStatus,
    VideoSequence,
    VramMode,
    utc_now,
)
from app.services import gpu_memory_service, project_service, sequence_service
from app.services.audio_service import AudioError, media_duration, resolve_ffmpeg
from app.services.sequence_service import SequenceError


class SequenceQueueManager:
    """One background render at a time per sequence, with cooperative cancel."""

    def __init__(self) -> None:
        self._threads: dict[str, threading.Thread] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    # -- public control -----------------------------------------------------
    def is_running(self, sequence_id: str) -> bool:
        with self._lock:
            t = self._threads.get(sequence_id)
            return bool(t and t.is_alive())

    def any_running(self) -> bool:
        """True if ANY sequence is currently rendering — used by the AI resource
        manager to block local LLM inference during a WAN render (patch §5)."""
        with self._lock:
            return any(t.is_alive() for t in self._threads.values())

    def start(self, sequence_id: str, clip_ids: list[str], do_merge: bool,
              force_ids: set[str] | None = None) -> None:
        if self.is_running(sequence_id):
            raise SequenceError("This sequence is already rendering.")
        cancel = threading.Event()
        thread = threading.Thread(
            target=self._run, args=(sequence_id, clip_ids, do_merge, force_ids or set()),
            daemon=True)
        with self._lock:
            self._cancels[sequence_id] = cancel
            self._threads[sequence_id] = thread
        thread.start()
        logger.info("Sequence render thread started: %s (%d clip(s), merge=%s)",
                    sequence_id, len(clip_ids), do_merge)

    def stop(self, sequence_id: str) -> dict:
        with self._lock:
            cancel = self._cancels.get(sequence_id)
        if cancel is None or not self.is_running(sequence_id):
            raise SequenceError("This sequence is not currently rendering.")
        cancel.set()
        try:
            seq = sequence_service.load_sequence(sequence_id)
            seq.render_state.status = SequenceStatus.STOPPING
            seq.render_state.current_stage = "Stopping current clip safely..."
            sequence_service.save_sequence(seq)
        except SequenceError:
            pass
        return {"sequence_id": sequence_id, "status": "stopping",
                "message": "Stopping current clip safely..."}

    # -- render selection helpers ------------------------------------------
    def render_sequence(self, sequence_id: str, only_clip_ids: list[str] | None = None) -> dict:
        seq = sequence_service.load_sequence(sequence_id)
        if not seq.clips:
            raise SequenceError("Add at least one clip before rendering.")
        if seq.output_mode == OutputMode.SELECTED_ONLY:
            if not only_clip_ids:
                raise SequenceError("Select at least one clip for 'Render selected clips only'.")
            targets = [c.clip_id for c in seq.clips if c.clip_id in set(only_clip_ids)]
            force = set(targets)
            do_merge = False
        else:
            targets = [c.clip_id for c in seq.clips]
            force = set()
            do_merge = (seq.output_mode == OutputMode.CLIPS_AND_MERGE)
        self.start(sequence_id, targets, do_merge, force)
        return self.status(sequence_id)

    def resume(self, sequence_id: str) -> dict:
        seq = sequence_service.load_sequence(sequence_id)
        start_idx = next((c.index for c in seq.clips
                          if c.status not in (ClipStatus.COMPLETED, ClipStatus.SKIPPED)), None)
        if start_idx is None:
            raise SequenceError("All clips are already completed or skipped — nothing to resume.")
        targets = [c.clip_id for c in seq.clips if c.index >= start_idx]
        do_merge = (seq.output_mode == OutputMode.CLIPS_AND_MERGE)
        self.start(sequence_id, targets, do_merge, set())
        return self.status(sequence_id)

    def resume_from(self, sequence_id: str, clip_id: str) -> dict:
        """Render from a specific clip onward (§5.3 'Resume from this Clip'). A
        completed target clip is archived and re-rendered; earlier completed
        clips are left untouched."""
        seq = sequence_service.load_sequence(sequence_id)
        clip = seq.get_clip(clip_id)
        if clip is None:
            raise SequenceError(f"Clip '{clip_id}' not found.")
        force: set[str] = set()
        if clip.status == ClipStatus.COMPLETED:
            self._archive_clip_outputs(seq, clip)
            clip.status = ClipStatus.QUEUED
            force.add(clip_id)
        sequence_service.save_sequence(seq)
        targets = [c.clip_id for c in seq.clips if c.index >= clip.index]
        do_merge = (seq.output_mode == OutputMode.CLIPS_AND_MERGE)
        self.start(sequence_id, targets, do_merge, force)
        return self.status(sequence_id)

    def regenerate_clip(self, sequence_id: str, clip_id: str) -> dict:
        seq = sequence_service.load_sequence(sequence_id)
        clip = seq.get_clip(clip_id)
        if clip is None:
            raise SequenceError(f"Clip '{clip_id}' not found.")
        self._archive_clip_outputs(seq, clip)
        clip.status = ClipStatus.QUEUED
        clip.needs_regeneration_reason = None
        sequence_service.save_sequence(seq)
        self.start(sequence_id, [clip_id], do_merge=False, force_ids={clip_id})
        return self.status(sequence_id)

    def skip_clip(self, sequence_id: str, clip_id: str) -> dict:
        seq = sequence_service.load_sequence(sequence_id)
        clip = seq.get_clip(clip_id)
        if clip is None:
            raise SequenceError(f"Clip '{clip_id}' not found.")
        clip.status = ClipStatus.SKIPPED
        sequence_service.save_sequence(seq)
        return self.status(sequence_id)

    def status(self, sequence_id: str) -> dict:
        seq = sequence_service.load_sequence(sequence_id)
        rs = seq.render_state
        completed = sum(1 for c in seq.clips if c.status == ClipStatus.COMPLETED)
        return {
            "sequence_id": seq.sequence_id,
            "status": rs.status.value,
            "current_clip_id": rs.current_clip_id,
            "current_clip_index": rs.current_clip_index,
            "clips_completed": completed,
            "clips_total": len(seq.clips),
            "overall_progress": rs.overall_progress,
            "current_stage": rs.current_stage,
            "can_resume": rs.can_resume,
            "last_error": rs.last_error,
            "running": self.is_running(sequence_id),
            "clips": [
                {"clip_id": c.clip_id, "index": c.index, "name": c.name,
                 "type": c.type.value, "status": c.status.value, "progress": c.progress,
                 "stage": c.stage, "last_error": c.last_error,
                 "needs_regeneration_reason": c.needs_regeneration_reason,
                 "has_raw": bool(c.outputs.raw), "has_fx": bool(c.outputs.fx),
                 "has_final": bool(c.outputs.final)}
                for c in seq.clips
            ],
            "outputs": seq.outputs.model_dump(mode="json"),
        }

    # ======================================================================
    # Worker
    # ======================================================================
    def _run(self, sequence_id: str, clip_ids: list[str], do_merge: bool, force_ids: set[str]) -> None:
        cancel = self._cancels.get(sequence_id, threading.Event())
        try:
            from app.services.generation_service import get_backend

            seq = sequence_service.load_sequence(sequence_id)
            try:
                backend = get_backend()
            except Exception as exc:  # noqa: BLE001 — surface as a sequence failure
                self._finish(sequence_id, SequenceStatus.FAILED, last_error=str(exc))
                return

            seq.render_state.status = SequenceStatus.RENDERING
            seq.render_state.started_at = utc_now()
            seq.render_state.last_error = None
            seq.render_state.clips_total = len(seq.clips)
            self._log(seq, f"Sequence '{seq.name}' render started — backend={backend.name}, "
                           f"output_mode={seq.output_mode.value}, vram_mode={seq.vram_mode.value}")
            # Mark targets queued.
            for c in seq.clips:
                if c.clip_id in clip_ids and (c.status != ClipStatus.COMPLETED or c.clip_id in force_ids):
                    c.status = ClipStatus.QUEUED
            sequence_service.save_sequence(seq)

            target_set = set(clip_ids)
            for order_clip in list(seq.clips):
                if order_clip.clip_id not in target_set:
                    continue
                if cancel.is_set():
                    break
                # Reload to honor any edits the user made (resume-after-modify).
                seq = sequence_service.load_sequence(sequence_id)
                clip = seq.get_clip(order_clip.clip_id)
                if clip is None:
                    continue
                if clip.status == ClipStatus.SKIPPED:
                    self._log(seq, f"Clip {clip.index + 1} skipped by user")
                    continue
                if clip.status == ClipStatus.COMPLETED and clip.clip_id not in force_ids:
                    self._log(seq, f"Clip {clip.index + 1} already completed — kept")
                    continue

                ok = self._render_one(seq, clip, backend, cancel)

                seq = sequence_service.load_sequence(sequence_id)
                clip = seq.get_clip(order_clip.clip_id)
                if cancel.is_set():
                    self._cleanup_between_clips(seq, clip)
                    self._finish(sequence_id, SequenceStatus.STOPPED)
                    return
                if clip and clip.status == ClipStatus.FAILED:
                    self._cleanup_between_clips(seq, clip)
                    if seq.continue_on_error:
                        self._log(seq, f"Clip {clip.index + 1} failed — continue_on_error is ON, "
                                       "proceeding to next clip")
                        continue
                    self._finish(sequence_id, SequenceStatus.FAILED, last_error=clip.last_error)
                    return
                self._cleanup_between_clips(seq, clip)

            # --- final merge + sequence audio ---------------------------------
            seq = sequence_service.load_sequence(sequence_id)
            if do_merge and seq.output_mode == OutputMode.CLIPS_AND_MERGE and not cancel.is_set():
                try:
                    self._merge_and_master(seq, cancel)
                except Exception as exc:  # noqa: BLE001
                    self._log(seq, f"Merge/sequence-audio failed: {exc}")
                    self._finish(sequence_id, SequenceStatus.FAILED, last_error=str(exc))
                    return

            self._finish(sequence_id, SequenceStatus.COMPLETED)
        except Exception as exc:  # noqa: BLE001 — never leave a hung 'rendering' state
            logger.exception("Sequence render crashed: %s", sequence_id)
            self._finish(sequence_id, SequenceStatus.FAILED, last_error=str(exc))
        finally:
            with self._lock:
                self._cancels.pop(sequence_id, None)
                self._threads.pop(sequence_id, None)

    # -- one clip -----------------------------------------------------------
    def _render_one(self, seq: VideoSequence, clip, backend, cancel: threading.Event) -> bool:
        from app.services.generation_service import GenerationCancelled

        base = project_service.sanitize_folder_name(seq.name)
        stem = f"{base}_clip_{clip.index + 1:03d}"
        cdir = sequence_service.clip_dir(seq, clip)
        raw_dir, fx_dir, final_dir = cdir / "raw", cdir / "fx", cdir / "final"
        for d in (raw_dir, fx_dir, final_dir):
            d.mkdir(parents=True, exist_ok=True)

        clip.status = ClipStatus.RENDERING
        clip.progress = 0
        clip.stage = "Starting"
        clip.last_error = None
        seq.render_state.current_clip_id = clip.clip_id
        seq.render_state.current_clip_index = clip.index
        seq.render_state.current_stage = f"Clip {clip.index + 1}: starting"
        sequence_service.save_sequence(seq)
        self._log(seq, f"Clip {clip.index + 1} ('{clip.name}') started — type={clip.type.value}")

        timings: dict = {}
        t0 = time.time()
        project, seed = sequence_service.build_clip_project(seq, clip)
        self._log(seq, f"Clip {clip.index + 1} settings: {project.resolution.label()}, "
                       f"{project.params.frames}f@{project.params.fps}fps, steps={project.params.advanced.steps}, "
                       f"cfg={project.params.guidance_scale}, seed={seed}, mode={project.generation_mode.value}")

        last_saved_stage = [""]

        def progress(pct: int, stage: str) -> None:
            clip.progress = int(pct)
            clip.stage = stage
            done = sum(1 for c in seq.clips if c.status == ClipStatus.COMPLETED)
            total = max(len(seq.clips), 1)
            seq.render_state.overall_progress = int((done + pct / 100.0) / total * 100)
            seq.render_state.current_stage = f"Clip {clip.index + 1}: {stage}"
            if stage != last_saved_stage[0]:
                last_saved_stage[0] = stage
                sequence_service.save_sequence(seq)

        try:
            result = backend.generate(project, seed, f"{stem}_raw", raw_dir, cdir, progress,
                                      should_cancel=cancel.is_set)
        except GenerationCancelled:
            clip.status = ClipStatus.STOPPED
            clip.needs_regeneration_reason = "Stopped by user during rendering"
            self._remove_partial(raw_dir)
            sequence_service.save_sequence(seq)
            self._log(seq, f"Clip {clip.index + 1} stopped by user")
            return False
        except Exception as exc:  # noqa: BLE001
            clip.status = ClipStatus.FAILED
            clip.last_error = str(exc)
            sequence_service.save_sequence(seq)
            self._log(seq, f"Clip {clip.index + 1} FAILED: {exc}")
            return False

        raw_path = result.output_path
        clip.outputs.raw = raw_path.name
        timings["render_seconds"] = round(time.time() - t0, 2)
        if result.preview_path and Path(result.preview_path).exists():
            clip.outputs.preview = Path(result.preview_path).name

        # --- Color & Look (post-processing, raw never modified) --------------
        base_video = raw_path
        try:
            fx_path = self._apply_color_look(seq, clip, raw_path, fx_dir, stem, timings)
            if fx_path:
                clip.outputs.fx = fx_path.name
                base_video = fx_path
        except Exception as exc:  # noqa: BLE001 — effects failure must not lose the raw clip
            self._log(seq, f"Clip {clip.index + 1} Color & Look failed (raw kept): {exc}")

        # --- Clip Audio Tracks (before merge) --------------------------------
        try:
            final_path = self._apply_clip_audio(seq, clip, base_video, final_dir, stem, timings)
        except Exception as exc:  # noqa: BLE001
            self._log(seq, f"Clip {clip.index + 1} audio failed (using video without audio): {exc}")
            final_path = final_dir / f"{stem}_final{base_video.suffix}"
            shutil.copy2(base_video, final_path)
        clip.outputs.final = final_path.name

        clip.status = ClipStatus.COMPLETED
        clip.progress = 100
        clip.stage = "Completed"
        clip.seed_used = seed
        clip.needs_regeneration_reason = None
        clip.diagnostics = {
            "effective_settings": {
                "resolution": project.resolution.label(),
                "frames": project.params.frames, "fps": project.params.fps,
                "steps": project.params.advanced.steps,
                "guidance_scale": project.params.guidance_scale,
                "sampler_name": project.params.sampler_name,
                "scheduler": project.params.scheduler,
                "denoise": project.params.denoise,
                "seed": seed, "mode": project.generation_mode.value,
                "model_id": project.model_id,
                "model_sampling": project.params.model_sampling.model_dump(mode="json"),
            },
            "color_look_mode": clip.color_look_mode.value,
            "clip_audio_tracks": [t.model_dump(mode="json") for t in clip.clip_audio_tracks if t.enabled],
            "timings": timings,
            "backend": backend.name,
            "is_mock": backend.is_mock,
        }
        self._write_clip_metadata(seq, clip)
        sequence_service.save_sequence(seq)
        self._log(seq, f"Clip {clip.index + 1} completed in {timings.get('render_seconds')}s "
                       f"-> {clip.outputs.final}")
        return True

    # -- post-processing (reuse existing engines) ---------------------------
    def _apply_color_look(self, seq, clip, raw_path: Path, fx_dir: Path, stem: str,
                          timings: dict) -> Path | None:
        from app.services import video_effects_service as ve

        if clip.color_look_mode == ColorLookMode.OFF:
            return None
        fx = clip.custom_color_look if clip.color_look_mode == ColorLookMode.CUSTOM else seq.global_color_look
        if not fx.enabled:
            return None
        ffmpeg = resolve_ffmpeg()
        width, height = ve._probe_resolution(ffmpeg, raw_path)
        filters = ve.build_filter_chain(fx, width, height)
        if not filters:
            return None
        out = fx_dir / f"{stem}_fx{raw_path.suffix}"
        vcodec = (["-c:v", "libvpx-vp9", "-b:v", "2M"] if raw_path.suffix.lower() == ".webm"
                  else ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"])
        cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(raw_path), "-vf", ",".join(filters),
               "-map", "0", "-c:a", "copy", *vcodec, str(out)]
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, timeout=900)
        if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            out.unlink(missing_ok=True)
            tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-4:]
            raise RuntimeError(" ".join(tail) or "ffmpeg color & look failed")
        timings["color_look_seconds"] = round(time.time() - t0, 2)
        self._log(seq, f"Clip {clip.index + 1} Color & Look applied ({len(filters)} filter(s), "
                       f"mode={clip.color_look_mode.value})")
        return out

    def _apply_clip_audio(self, seq, clip, video_path: Path, final_dir: Path, stem: str,
                          timings: dict) -> Path:
        from app.services import audio_service as au

        tracks = [t for t in clip.clip_audio_tracks if t.enabled]
        audio_dir = sequence_service.assets_audio_dir(seq)
        present, missing = [], []
        for t in tracks:
            (present if (audio_dir / Path(t.filename).name).exists() else missing).append(t)
        for t in missing:
            self._log(seq, f"Clip {clip.index + 1} audio track '{t.filename}' missing — skipped")
        out = final_dir / f"{stem}_final{video_path.suffix}"
        if not present:
            shutil.copy2(video_path, out)
            return out

        ffmpeg = resolve_ffmpeg()
        video_len = media_duration(video_path)
        cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(video_path)]
        filters = []
        for i, track in enumerate(present, start=1):
            apath = audio_dir / Path(track.filename).name
            audio_len = media_duration(apath)
            if track.loop:
                cmd += ["-stream_loop", "-1"]
            cmd += ["-i", str(apath)]
            filters.append(au._track_filter(i, track, audio_len, video_len))
        if len(present) > 1:
            labels = "".join(f"[a{i}]" for i in range(1, len(present) + 1))
            filters.append(f"{labels}amix=inputs={len(present)}:duration=longest:"
                           "dropout_transition=0:normalize=0[aout]")
            out_label = "[aout]"
        else:
            out_label = "[a1]"
        acodec = (["-c:a", "libopus", "-b:a", "160k"] if video_path.suffix.lower() == ".webm"
                  else ["-c:a", "aac", "-b:a", "192k"])
        cmd += ["-filter_complex", ";".join(filters), "-map", "0:v", "-map", out_label,
                "-c:v", "copy", *acodec, str(out)]
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, timeout=600)
        if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            out.unlink(missing_ok=True)
            tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-4:]
            raise AudioError(" ".join(tail) or "ffmpeg clip audio failed")
        timings["clip_audio_seconds"] = round(time.time() - t0, 2)
        self._log(seq, f"Clip {clip.index + 1} clip audio applied ({len(present)} track(s))")
        return out

    # -- VRAM (§11) ---------------------------------------------------------
    def _cleanup_between_clips(self, seq, clip) -> None:
        mode = seq.vram_mode
        unload = (mode == VramMode.RELOAD)
        reason = f"sequence:{seq.sequence_id}:after_clip"
        report = gpu_memory_service.cleanup_vram(reason, unload_models=unload)
        # Aggressive: if reserved VRAM is still high, unload the pipeline too.
        if (mode == VramMode.AGGRESSIVE and settings.sequence_unload_pipeline_on_oom
                and (report.get("after_reserved_mb") or 0) >= settings.sequence_aggressive_unload_reserved_mb):
            report = gpu_memory_service.cleanup_vram(reason + ":high_vram", unload_models=True)
        if clip is not None and clip.diagnostics is not None:
            clip.diagnostics["vram_cleanup"] = report
            sequence_service.save_sequence(seq)
        self._log(seq, gpu_memory_service.summarize(report) + f" (vram_mode={mode.value})")

    # -- merge + sequence audio (§16, §17) ---------------------------------
    def _merge_and_master(self, seq: VideoSequence, cancel: threading.Event) -> None:
        finals = []
        for clip in seq.clips:
            if clip.status == ClipStatus.COMPLETED and clip.outputs.final:
                fpath = sequence_service.clip_dir(seq, clip) / "final" / clip.outputs.final
                if fpath.exists():
                    finals.append(fpath)
        if not finals:
            self._log(seq, "Merge skipped: no completed clip finals available")
            return

        base = project_service.sanitize_folder_name(seq.name)
        sdir = sequence_service.sequence_dir(seq.folder)
        merged_dir = sdir / "exports" / "merged"
        merged_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg = resolve_ffmpeg()
        g = seq.global_generation_settings
        W, H, F = g.width, g.height, g.fps

        seq.render_state.current_stage = "Merging clips"
        sequence_service.save_sequence(seq)
        self._log(seq, f"Sequence merge started: {len(finals)} clip(s) -> {W}x{H}@{F}fps")

        # Normalize every clip to identical params (with a guaranteed audio
        # stream) so concat -c copy is safe (§16).
        tmp_dir = merged_dir / "_normalized"
        tmp_dir.mkdir(exist_ok=True)
        norm_files = []
        try:
            for i, fpath in enumerate(finals, start=1):
                if cancel.is_set():
                    return
                norm = tmp_dir / f"norm_{i:03d}.mp4"
                self._normalize_clip(ffmpeg, fpath, norm, W, H, F)
                norm_files.append(norm)

            concat_list = tmp_dir / "concat.txt"
            concat_list.write_text(
                "".join(f"file '{p.as_posix()}'\n" for p in norm_files), encoding="utf-8")
            merged_name = f"{base}_merged_video.mp4"
            merged_path = merged_dir / merged_name
            cmd = [ffmpeg, "-hide_banner", "-y", "-f", "concat", "-safe", "0",
                   "-i", str(concat_list), "-c", "copy", str(merged_path)]
            proc = subprocess.run(cmd, capture_output=True, timeout=1800)
            if proc.returncode != 0 or not merged_path.exists() or merged_path.stat().st_size == 0:
                tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
                raise RuntimeError("ffmpeg concat failed: " + (" ".join(tail) or "unknown"))
            seq.outputs.merged = merged_name
            sequence_service.save_sequence(seq)
            self._log(seq, f"Sequence merged -> {merged_name}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # Sequence-master audio applied AFTER the merge (§17) so music does not
        # restart at every clip.
        master = [t for t in seq.sequence_audio_tracks if t.enabled]
        if master and settings.sequence_enable_sequence_audio:
            if cancel.is_set():
                return
            seq.render_state.current_stage = "Applying sequence audio"
            sequence_service.save_sequence(seq)
            try:
                final_name = self._apply_sequence_audio(seq, merged_path, master, base)
                seq.outputs.final = final_name
                sequence_service.save_sequence(seq)
                self._log(seq, f"Sequence audio applied -> {final_name}")
            except Exception as exc:  # noqa: BLE001
                self._log(seq, f"Sequence audio failed (merged video kept): {exc}")
        else:
            # No master audio: the merged video is the final deliverable.
            self._log(seq, "No sequence-master audio — merged video is the final output")

    def _normalize_clip(self, ffmpeg: str, src: Path, dst: Path, W: int, H: int, F: int) -> None:
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={F},format=yuv420p")
        cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(src)]
        if not self._has_audio(ffmpeg, src):
            # Add a silent stereo track so all normalized clips share stream layout.
            cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-shortest", "-map", "0:v:0", "-map", "1:a:0"]
        else:
            cmd += ["-map", "0:v:0", "-map", "0:a:0"]
        cmd += ["-vf", vf, "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(dst)]
        proc = subprocess.run(cmd, capture_output=True, timeout=1800)
        if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
            tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
            raise RuntimeError(f"normalize failed for {src.name}: " + (" ".join(tail) or "unknown"))

    def _has_audio(self, ffmpeg: str, path: Path) -> bool:
        proc = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)],
                              capture_output=True, timeout=60)
        return b"Audio:" in proc.stderr

    def _apply_sequence_audio(self, seq, merged_path: Path, tracks, base: str) -> str:
        from app.services import audio_service as au

        ffmpeg = resolve_ffmpeg()
        audio_dir = sequence_service.assets_audio_dir(seq)
        present = [t for t in tracks if (audio_dir / Path(t.filename).name).exists()]
        for t in tracks:
            if t not in present:
                self._log(seq, f"Sequence audio '{t.filename}' missing — skipped")
        final_dir = sequence_service.sequence_dir(seq.folder) / "exports" / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        final_name = f"{base}_final.mp4"
        out = final_dir / final_name
        if not present:
            shutil.copy2(merged_path, out)
            return final_name

        video_len = media_duration(merged_path)
        cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(merged_path)]
        filters = []
        for i, track in enumerate(present, start=1):
            apath = audio_dir / Path(track.filename).name
            audio_len = media_duration(apath)
            if track.loop:
                cmd += ["-stream_loop", "-1"]
            cmd += ["-i", str(apath)]
            filters.append(au._track_filter(i, track, audio_len, video_len))
        # Mix the merged video's own audio (clip ambience) with the master tracks
        # so clip sound and sequence music coexist.
        inputs = "[0:a]" + "".join(f"[a{i}]" for i in range(1, len(present) + 1))
        n = len(present) + 1
        filters.append(f"{inputs}amix=inputs={n}:duration=longest:dropout_transition=0:normalize=0[aout]")
        cmd += ["-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(out)]
        proc = subprocess.run(cmd, capture_output=True, timeout=1800)
        if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            out.unlink(missing_ok=True)
            tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
            raise AudioError(" ".join(tail) or "sequence audio failed")
        return final_name

    # -- helpers ------------------------------------------------------------
    def _archive_clip_outputs(self, seq, clip) -> None:
        """Move a clip's existing outputs into an archive/ folder (§15)."""
        cdir = sequence_service.clip_dir(seq, clip)
        if not cdir.exists():
            return
        stamp = time.strftime("%Y%m%d_%H%M%S")
        archive = cdir / "archive" / stamp
        moved = []
        for sub in ("raw", "fx", "final"):
            d = cdir / sub
            if d.exists():
                for f in d.iterdir():
                    if f.is_file():
                        archive.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(f), str(archive / f.name))
                        moved.append(f"{sub}/{f.name}")
        if moved:
            clip.outputs.archived = clip.outputs.archived + moved
            clip.outputs.raw = clip.outputs.fx = clip.outputs.final = None
            self._log(seq, f"Clip {clip.index + 1} previous outputs archived ({len(moved)} file(s))")

    def _remove_partial(self, raw_dir: Path) -> None:
        if raw_dir.exists():
            for f in raw_dir.iterdir():
                if f.is_file():
                    f.unlink(missing_ok=True)

    def _write_clip_metadata(self, seq, clip) -> None:
        cdir = sequence_service.clip_dir(seq, clip)
        cdir.mkdir(parents=True, exist_ok=True)
        meta = {
            "application": settings.app_name,
            "credits": {"concept_and_design": settings.credits_concept,
                        "project_by": settings.credits_project},
            "sequence_id": seq.sequence_id,
            "sequence_name": seq.name,
            "clip_id": clip.clip_id,
            "clip_index": clip.index,
            "clip_name": clip.name,
            "type": clip.type.value,
            "prompt": clip.prompt,
            "negative_prompt": clip.negative_prompt,
            "status": clip.status.value,
            "seed": clip.seed_used,
            "outputs": clip.outputs.model_dump(mode="json"),
            "diagnostics": clip.diagnostics,
            "written_at": utc_now(),
        }
        (cdir / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                            encoding="utf-8")

    def _log(self, seq: VideoSequence, message: str) -> None:
        """Append a diagnostics line to the in-memory sequence and persist it.
        Operates on the caller's `seq` (never a reload) so it can't clobber
        unsaved in-memory state."""
        line = f"{utc_now()} | {message}"
        logger.info("[sequence %s] %s", seq.sequence_id, message)
        seq.diagnostics.append(line)
        seq.diagnostics = seq.diagnostics[-500:]
        try:
            sequence_service.save_sequence(seq)
        except SequenceError:
            pass

    def _finish(self, sequence_id: str, status: SequenceStatus, last_error: str | None = None) -> None:
        try:
            seq = sequence_service.load_sequence(sequence_id)
        except SequenceError:
            return
        completed = sum(1 for c in seq.clips if c.status == ClipStatus.COMPLETED)
        rs = seq.render_state
        rs.status = status
        rs.finished_at = utc_now()
        rs.clips_completed = completed
        rs.clips_total = len(seq.clips)
        rs.overall_progress = int(completed / max(len(seq.clips), 1) * 100)
        rs.last_error = last_error
        rs.can_resume = any(c.status not in (ClipStatus.COMPLETED, ClipStatus.SKIPPED) for c in seq.clips)
        rs.current_stage = {
            SequenceStatus.COMPLETED: "Sequence completed",
            SequenceStatus.STOPPED: "Sequence stopped — resume available",
            SequenceStatus.FAILED: f"Sequence failed: {last_error or 'unknown error'}",
        }.get(status, rs.current_stage)
        sequence_service.save_sequence(seq)
        self._log(seq, f"Sequence finished: {status.value} ({completed}/{len(seq.clips)} clips completed)")


queue_manager = SequenceQueueManager()
