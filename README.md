# Radica · WanVideoGenerator

**A browser-based AI video generation studio for Wan 2.2+ — real model loading, real inference, real videos.**

```text
Radica - WanVideoGenerator
Concept & Design: Fabrizio Radica
Project by RadicaDesign
```

WanVideoGenerator is a professional, self-hosted **Python / FastAPI** web application that generates videos locally with **Wan 2.2 or later** through Hugging Face *diffusers*. It supports **Text2Video** and **Image2Video**, organizes everything into projects, and ships with a visual Camera Motion Assistant, a Model Bundle manager, a video library and ComfyUI workflow export.

The UI is fully server-rendered (Jinja2 + vanilla JavaScript) — no Node.js, no build step, no frontend framework. Everything runs on your machine; nothing is uploaded anywhere.

<img width="1264" height="690" alt="Screenshot 2026-07-07 084827" src="https://github.com/user-attachments/assets/b6bfce1c-3372-4cb1-8fa7-f4515a021a2c" />


---

## Features

- 🎬 **Real Wan 2.2+ generation** — Text2Video and Image2Video via diffusers `WanPipeline` / `WanImageToVideoPipeline`, with prompt, negative prompt, seed, frames, FPS, guidance scale, steps and resolution control
- 📦 **Model Bundle manager** — register your local model files (diffusion model/DiT, VAE, text encoder, tokenizer, LoRAs…), validate them, and switch bundles per project. Works directly with **ComfyUI-repackaged single `.safetensors` files** (including fp8-scaled text encoders) or diffusers pipeline folders
- 🗂 **Project-based workflow** — every video lives in a project folder (`source/`, `outputs/`, `previews/`, `metadata/`, `workflows/`) with a portable `.wanproj` file
- 🎥 **Camera Motion Assistant** — 16 camera movements (orbit, push in, pan, crane, handheld…) with an animated diagram that composes a camera prompt fragment without touching your text
- 📊 **Honest job system** — background generation with live progress, stage log, and truthful metadata: seed, bundle snapshot, duration, backend. If generation fails, it fails clearly — the app **never produces fake or placeholder output**
- ⏱ **Duration control** — set the video length in seconds (2s/3s/5s/10s presets or custom); duration, FPS and frame count stay synchronized automatically
- 🖥 **System resource monitor** — live CPU / RAM / GPU / VRAM / disk pills in the topbar with warning states, so you always know what a generation is costing
- 🔄 **Persistent generation progress** — jobs live server-side; switch pages or refresh the browser and the progress strip picks the running job right back up
- 🎨 **Color & Look video effects** — saturation/contrast/hue/temperature/shadows/highlights/brightness/gamma plus vignette, film grain, sharpness and an optional VHS effect, with a real-time canvas preview; applied via ffmpeg on export
- 🎵 **Audio Tracks post-processing** — upload multiple audio files, set volume/start/fades/loop/trim per track, and mix them into the finished video with ffmpeg (the raw video is never touched)
- 🖼 **Video & project libraries** — browse, preview, download, copy metadata
- 🔌 **ComfyUI workflow export** — JSON node graph plus complete generation metadata
- ⚙️ **Everything configurable via `.env`**, with a live backend-readiness indicator in the UI

---

## Requirements

### Hardware

| Component | Minimum | Recommended |
|---|---|---|
| GPU | NVIDIA, 8 GB VRAM (with CPU offload) | NVIDIA RTX, 12 GB+ VRAM |
| System RAM | 16 GB | 32 GB |
| Disk | ~25 GB free for the Wan 2.2 TI2V-5B model set | SSD |

CPU-only inference is technically possible but unrealistically slow — the app will warn you clearly.

### Software

- **Python 3.10+** (tested with 3.11)
- **PyTorch with CUDA** matching your GPU (RTX 50xx needs cu128 wheels)
- Python packages from `requirements.txt`: FastAPI, uvicorn, **diffusers ≥ 0.35**, transformers, accelerate, safetensors, sentencepiece, ftfy, imageio-ffmpeg (bundles its own ffmpeg), Pillow, pydantic

### Model files

A working bundle needs at minimum (example: **Wan 2.2 TI2V-5B**, handles both T2V and I2V):

| Component | Example file | Source |
|---|---|---|
| Diffusion model (DiT) | `wan2.2_ti2v_5B_fp16.safetensors` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged) |
| VAE | `wan2.2_vae.safetensors` | same repo |
| Text encoder | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | same repo |

Alternatively point a bundle at a local **diffusers pipeline folder** (e.g. a snapshot of [Wan-AI/Wan2.2-TI2V-5B-Diffusers](https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B-Diffusers)) and everything is loaded from there. The small UMT5 tokenizer files are fetched once from the Hugging Face Hub and cached (or set a local `tokenizer_path`).

---

## Installation

```bash
git clone <your-repo-url> RadicaWanGen
cd RadicaWanGen

# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

# 2. Install PyTorch with the CUDA build matching your GPU — do this FIRST
#    RTX 50xx (Blackwell) / 40xx / 30xx:
pip install torch --index-url https://download.pytorch.org/whl/cu128
#    other CUDA versions: https://pytorch.org/get-started/locally/

# 3. Install the remaining dependencies
pip install -r requirements.txt

# 4. Create your configuration
copy .env.example .env          # Windows
# cp .env.example .env          # Linux/macOS
```

Download the model files (see table above) into `models/` (any folder works — bundle paths are absolute), then register them on the **Models** page or via the `DEFAULT_T2V_*` variables in `.env`.

## Running

```bash
python -m app.main
# or: uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. The topbar shows a live indicator — green means the real Wan backend is ready (dependencies + CUDA + device detected).

---

## Quick start

1. **＋ New Project** → follow the wizard (mode → orientation → resolution)
2. Pick your **model bundle**, write a **prompt** (and optionally apply a camera motion)
3. *Image2Video only:* upload a **source image** — the video starts from it and animates it
4. Click **✦ Generate Video** — progress and stage log update live; the result plays in the preview panel and is saved to `projects/<name>/outputs/` with full JSON metadata

### Tips for good results (TI2V-5B)

- Resolutions must be **multiples of 32**, best near the native **1280×704** (704×1280 portrait); use the test presets (640×352, 384×224) for quick iterations
- Frame count must be **4k+1** (17, 33, 49, 81, 121); native pacing is 24 fps
- Use **25–40 steps**; very few steps produce washed-out output
- Keep `WAN_TORCH_DTYPE=float16` for fp16 checkpoints

### Key `.env` options

| Variable | Default | Meaning |
|---|---|---|
| `GENERATION_BACKEND` | `wan` | real inference; `mock` is a developer-only UI simulation (requires `APP_ENV=development`) |
| `WAN_TORCH_DTYPE` | `float16` | `float16` / `bfloat16` / `float32` |
| `WAN_OFFLOAD_MODE` | `model` | `model` fits 5B/14B on consumer GPUs · `sequential` lowest VRAM · `none` all on GPU |
| `WAN_FLOW_SHIFT` | `0` (auto) | UniPC flow shift (auto: 5.0 for TI2V-5B / ≥720p, 3.0 below) |
| `WAN_ALLOW_HF_DOWNLOAD` | `true` | allow fetching the small tokenizer/config files once from the HF Hub |

## Generation Parameters / KSampler compatibility

The **Generation Parameters** panel includes ComfyUI/KSampler-compatible
sampling values: **Seed**, **Control after generate**
(fixed/randomize/increment/decrement — applied to the stored seed after each
successful generation; `-1 = random seed` keeps working), **Steps**,
**CFG / Guidance Scale**, **Sampler**, **Scheduler** and **Denoise**.

Important distinctions:

- **Denoise is not CFG.** Denoise is not Motion Strength and not Image
  Influence either — it is its own sampling parameter and is never remapped.
  For Image2Video it controls how much the source image may be transformed.
- **CFG / Guidance Scale maps to ComfyUI `cfg`.** Internally the value is
  stored as `guidance_scale`; metadata and ComfyUI exports carry both names.

### Samplers actually used by the direct backend (patch6c)

The selected sampler is **really used** by the direct diffusers backend — it is
never silently replaced by UniPC. Wan2.2 is a flow-matching model, so each
supported sampler maps to a genuine diffusers flow-matching scheduler:

| Sampler | Direct backend scheduler |
| --- | --- |
| `uni_pc` | `UniPCMultistepScheduler` (flow) |
| `euler` | `FlowMatchEulerDiscreteScheduler` |
| `heun` | `FlowMatchHeunDiscreteScheduler` |
| `dpmpp_2m` | `DPMSolverMultistepScheduler` (flow, dpmsolver++) |
| `dpmpp_2m_sde` | `DPMSolverMultistepScheduler` (flow, sde-dpmsolver++) |

Samplers with no correct flow-matching implementation (`euler_ancestral`,
`dpm_2`, `dpm_2_ancestral`, `lms`, `dpmpp_sde`, `ddim`) are **not faked**. When
one is selected, the `WAN_SAMPLER_FALLBACK_POLICY` decides what happens — there
is **no silent fallback**:

- `block` (default) — generation does not start; a clear error explains why.
- `ask` — you must confirm a fallback before rendering.
- `route_to_comfyui` — render through a running ComfyUI (`COMFYUI_API_ENABLED`)
  so the requested sampler is used exactly; if ComfyUI is unavailable, it blocks.
- `allow_with_warning` — debug only: renders with `uni_pc`, clearly reported in
  the UI, job log, diagnostics and metadata.

`scheduler` (simple/karras/…) and partial `denoise` still have no separate
effect in the direct backend — Wan's flow-matching sigma schedule and full
denoise are used, and any difference is reported honestly (never a claim that
"Wan2.2 cannot use euler"). Every generated video's metadata includes the
grouped `sampling` object plus a `sampler_backend` block recording
requested-vs-effective sampler, the effective render backend and the fallback
policy outcome.

## ModelSamplingSD3 Support

`ModelSamplingSD3` is a **real model sampling modifier** applied *between model
loading and sampling* — the ComfyUI graph is:

```text
Load Diffusion Model → ModelSamplingSD3(shift) → KSampler
```

For a flow-matching model like Wan2.2, `ModelSamplingSD3` **is** the
flow-matching sigma shift. ComfyUI applies
`sigma = shift·sigma / (1 + (shift−1)·sigma)`, and diffusers' flow-matching
schedulers apply the identical formula. So the **direct backend applies it
exactly** by setting the scheduler's flow shift before sampling — this is not
faked, not export-only and not post-processing.

- It can **significantly change/improve quality** (the user verified this).
- It is **independent** from CFG, denoise, scheduler, sampler, motion strength,
  image influence and prompt text — a first-class, structured setting
  (`params.model_sampling = { enabled, type: "sd3", shift }`).
- Configure it in **Generation Parameters → Advanced settings → Model Sampling**:
  an *Enable ModelSamplingSD3* toggle and a *Shift* control (default `8.0`).
- New projects enable it by default (`WAN_MODEL_SAMPLING_SD3_ENABLED_DEFAULT`);
  existing projects keep their saved value and are never silently changed.
- **ComfyUI export** inserts the `ModelSamplingSD3` node with the configured
  shift and wires the KSampler to its output (not the raw loaded model).
- **Requested vs effective** settings appear in the Backend Diagnostics
  *Model Sampling Modifier* section and in each video's metadata
  (`model_sampling.requested` / `.effective` / `.applied` / `.backend`).

Quality presets integrate it: **High Quality** and **ComfyUI Match** enable
ModelSamplingSD3 with shift `8.0` when it is off, and the change is shown as a
preset note (never hidden).

### Troubleshooting ModelSamplingSD3

- **Enabled but the direct backend can't apply it** — this only happens if the
  selected sampler is not one of the direct flow-matching samplers (patch6c
  already blocks/routes those first). `WAN_MODEL_SAMPLING_FALLBACK_POLICY`
  decides: `route_to_comfyui` (default), `ask`, or `block`. There is no silent
  ignore.
- **Routed to ComfyUI** — the diagnostics/metadata show `backend: comfyui` and a
  note; the exported workflow (with `ModelSamplingSD3`) is submitted to ComfyUI.
- **Blocked** — `block` policy with no ComfyUI available: a clear error tells you
  to enable ComfyUI routing or disable ModelSamplingSD3.
- **Shift changed from default** — the effective shift is shown in diagnostics
  and the render log (`ModelSamplingSD3: applied (shift=…)`).
- **Quality changed** — enabling/disabling the modifier or changing the shift
  changes the sigma schedule and therefore the result; compare the effective
  shift in diagnostics between runs.

## Wan2.2 5B Presets

Three practical, dependency-free profiles (plus **Manual**) tuned for Wan2.2
TI2V 5B on 8–12 GB GPUs. **No SageAttention, no TeaCache, no experimental
accelerators** — the quality comes from correct ModelSamplingSD3 shift, sane
step counts, valid Wan resolutions and real sampler/scheduler preservation.

Pick one from **Generation Parameters → Wan 2.2 5B Preset**. A preset never
changes anything silently: it shows a *Preset applied* summary of exactly what
it changed (e.g. `Steps 22 → 32`, `Frames 49 → 81`, `ModelSamplingSD3 shift set
to 8.0 (T2V)`, `Resolution 704x1280 → 832x480`) and you review it before saving.

| Preset | Resolution | Frames | Steps | CFG | ModelSamplingSD3 | Offload |
| --- | --- | --- | --- | --- | --- | --- |
| **Safe Quality** (default) | 832×480 | 81 | 32 (30–40) | 4.0 | on, shift 8.0 T2V / 5.0 I2V | balanced |
| **Fast Preview** | 832×480 | 49 | 18 (16–20) | 3.5 | on, shift 8.0 / 5.0 | balanced |
| **Low VRAM** | 640×352 | 49 | 22 (20–24) | 3.5 | on, shift 8.0 / 5.0 | aggressive + unload |

- All presets use **euler / simple** and the selected sampler is really used —
  never silently replaced (see the sampler section above).
- Resolutions are validated as multiples of 16; portrait flips the dimensions.
- **FPS 16**: 81 frames ≈ 5.06 s, 49 frames ≈ 3.06 s (shown live in the UI).
- **Low VRAM** reduces resolution/frame count and enables *Unload model after
  generation* to keep VRAM low; a warning makes the trade-off explicit.
- A **hardware recommendation** (based on detected VRAM) is shown next to the
  selector — `≥12 GB → Safe Quality`, `8–12 GB → Low VRAM / Fast Preview`,
  unknown → Fast Preview first. It only recommends; it never blocks your choice.
- Each generation's metadata stores the requested-vs-effective preset values.

## VRAM Cleanup

Centralized VRAM cleanup (`app/services/gpu_memory_service.py`) runs after
**every** generation exit path:

- after a **successful** render,
- after a **failed** render (the original error is preserved; a cleanup problem
  is only a warning),
- after **Stop / Cancel**.

It uses `gc.collect()`, `torch.cuda.empty_cache()` and `torch.cuda.ipc_collect()`
when available. Honesty matters here:

- It **does not kill processes** (no `taskkill`, no `nvidia-smi --kill`, no
  `os.kill`) and **never deletes models or project files**.
- `empty_cache()` frees PyTorch's cached blocks; it does **not necessarily free
  VRAM still held by live model objects**. The app reports measured
  before/after allocated & reserved MB rather than claiming "VRAM fully freed".
- Model **weights** are only unloaded when configured
  (`WAN_UNLOAD_MODEL_AFTER_GENERATION` / `WAN_UNLOAD_MODEL_AFTER_CANCEL`, or the
  **Low VRAM** preset / *Unload model after generation* checkbox). **Low VRAM**
  mode releases the model more aggressively.

The cleanup report (reason, gc/empty_cache/ipc flags, before/after MB, warnings)
is written to the **job log**, the **video metadata** (`vram_cleanup`), and the
**Backend Diagnostics → VRAM Cleanup** section. If CUDA/torch is unavailable the
panel shows *CUDA cleanup unavailable*, not an error.

## Backend Diagnostics — Effective Generation Settings

The **Preview** panel has an expandable **Backend Diagnostics — Effective
Generation Settings** section that appears after each generation. It is the
honest record of what the backend actually did, never hidden only in logs:

- **Requested vs Effective** table — model bundle, precision, sampler,
  scheduler, denoise, CFG, steps, seed, resolution, frames, FPS and offload
  policy. Rows where the effective value differs from the requested one are
  highlighted.
- **Component placement** — the device (`cuda`, `cuda (offloaded)`, `cpu`) and
  dtype (`fp16`, `bf16`, `fp32`, `fp8_e4m3fn` …) of the diffusion model, text
  encoder, VAE and vision encoder.
- **Timing breakdown** — model load, text/image encoding, sampling, VAE decode
  and video write time in seconds, so slowness can be diagnosed instead of
  guessed.
- **GPU memory** — VRAM before load, after load, peak during generation and
  after cleanup (MB).
- **Image preprocessing** (Image2Video) — source/target size, resize/crop mode
  and the effective conditioning size.
- **Warnings** — every fallback (dtype dequant, CPU placement, unsupported
  sampler/scheduler/denoise) and every quality-preset change.

The same information is stored in each video's metadata JSON as
`requested_settings`, `effective_settings`, `device_map`, `dtype_map`,
`timings`, `gpu_memory`, `image_preprocessing` and `warnings`.

### Quality presets

The **Quality preset** selector offers:

- **None** — use your parameters exactly.
- **ComfyUI Match** — prioritize matching ComfyUI: disables hidden aggressive
  offload and warns on every fallback. The requested sampler is applied exactly
  when the direct backend supports it (uni_pc, euler, heun, dpmpp_2m,
  dpmpp_2m_sde); for other samplers use `route_to_comfyui` or the ComfyUI API
  backend for guaranteed parity.
- **High Quality** — prioritize visual quality over speed: raises steps to at
  least 30 and avoids aggressive offload.

Presets **never silently override** your parameters — every change is written
to the job log, the metadata (`preset_notes`) and the diagnostics panel.

### Compare With ComfyUI Settings

The diagnostics panel has a **Compare With ComfyUI Settings** button (parity
tool). It saves a JSON under the project's `diagnostics/` folder comparing your
requested settings, the direct backend's effective settings and the exported
ComfyUI workflow settings, and lists every configuration mismatch.

## Why output may look worse than ComfyUI

Quality differences between the direct backend and ComfyUI almost always come
from a configuration difference, not from the model itself. Common causes:

- different sampler
- different scheduler
- different denoise
- different CFG
- different seed handling
- negative prompt ignored
- camera prompt not included
- text encoder mismatch
- VAE mismatch
- vision encoder mismatch
- different dtype
- FP8 fallback (fp8 files are dequantized to fp16/bf16 for compute)
- CPU offload (text encoder / VAE on CPU is much slower)
- different image preprocessing (crop vs pad/letterbox)
- different resolution / frame count
- different latent size
- different video decoding / write path

Open **Backend Diagnostics** to see which of these applies to your run — the
mismatched rows and warnings point straight at the cause.

## How to match ComfyUI output

1. Select the same model bundle.
2. Use the same prompt and negative prompt.
3. Use the same seed.
4. Use the same steps, CFG, sampler, scheduler and denoise.
5. Use the same resolution, frames and FPS.
6. Select the **ComfyUI Match** quality preset.
7. Open **Backend Diagnostics** and check the **Effective Settings**.
8. Resolve every warning shown.

If exact parity still matters after resolving warnings (e.g. you need a sampler
the direct backend cannot run, such as `ddim` or `lms`), render through ComfyUI
itself. Enable the ComfyUI API render backend (`COMFYUI_API_ENABLED`,
`COMFYUI_API_URL`, `COMFYUI_API_TIMEOUT_SECONDS`) and set
`WAN_SAMPLER_FALLBACK_POLICY=route_to_comfyui`: the app then submits the
exported workflow to the running ComfyUI, monitors progress and imports the
resulting video, so the requested sampler is used exactly — while keeping this
app as the project manager/UI. You can also always use **Export Workflow** to
run the identical graph in ComfyUI manually. The direct diffusers backend runs
the requested sampler when it is one of its supported flow-matching solvers
(uni_pc, euler, heun, dpmpp_2m, dpmpp_2m_sde); the diagnostics panel's
**Sampler backend** section tells you exactly which sampler and backend were
used.

## Generated video delete

Generated videos can be deleted from the **Video Library** page and from the
**Generated Videos** list in the project editor (trash icon). Deletion always
asks for confirmation and cannot be undone. The raw/project files are
protected: the app only deletes the selected generated output file and its
direct metadata/preview files, strictly inside the project's `outputs/`,
`previews/` and `metadata/` folders. Model files, source/reference images,
audio files and `.wanproj` project files can never be deleted through this
path, and absolute paths or path traversal are rejected.

## Duration and frame count

The UI exposes video length as **Duration** (seconds) while generation itself
remains frame-based: `duration = frames / fps`. Choosing a duration preset
(2s/3s/5s/10s) sets `frames = round(duration × fps)`, snapped to Wan's `4k+1`
frame rule; changing FPS re-applies the selected duration; editing the frame
count manually switches duration to *Custom* and updates the estimated length.
Project files store `duration_seconds`, and every generated video's metadata
records `requested_duration_seconds`, `actual_duration_seconds`, `fps` and
`frames` (ComfyUI exports include the requested duration too).

## System resource monitor

The topbar shows live **CPU / RAM / GPU / VRAM / disk-free** pills, refreshed
every 2 seconds (configurable). CPU, RAM and disk come from `psutil`; GPU and
VRAM come from NVML (`nvidia-ml-py`) with a `torch.cuda` fallback — if neither
is available the pills show `N/A` and the app keeps working. Values turn
yellow/red at sensible thresholds (RAM/VRAM 85 %, CPU 90 %, GPU 95 %).

The **Disk** pill shows usage percentage and free space for the volume
containing the configured projects/output path — e.g. `Disk 51% · 488 GB free`
— and turns yellow at the warning threshold (75 % by default) and red at the
critical threshold (90 %). Before a generation starts, the same thresholds
produce a warning toast (warning) or an explicit confirmation prompt
(critical); generation is never silently started under critical disk
conditions and no files are ever deleted automatically. If disk stats fail,
the rest of the monitor keeps working. `.env` options:

```env
SYSTEM_MONITOR_ENABLED=true
SYSTEM_MONITOR_POLL_INTERVAL_MS=2000
SYSTEM_MONITOR_SHOW_GPU=true
SYSTEM_MONITOR_SHOW_DISK=true
# volume that holds the generated project/video outputs (not necessarily C:)
SYSTEM_MONITOR_DISK_PATH=./projects
SYSTEM_MONITOR_DISK_WARNING_PERCENT=75
SYSTEM_MONITOR_DISK_CRITICAL_PERCENT=90
```

## Persistent generation progress

Generation jobs run **server-side** — navigating between pages or refreshing
the browser never interrupts them. Every page polls `/api/generation/active`
and shows a compact progress strip under the topbar (project name, backend,
percentage, current stage); the project editor re-attaches to its running job
on load. Completed jobs stay visible for 30 s with a link to the result,
failed jobs for 60 s with the error (both dismissible and configurable via
`GENERATION_*` variables in `.env`). Polling slows automatically when nothing
is running. Note: the job list is in-memory — a **server restart** clears job
history (generated files and metadata are always persisted on disk).

## Stopping a Generation

While a generation is running you can stop it. A **Stop Generation** button
appears in the Preview panel's progress area, and every page's global progress
strip shows a **⏹ Stop** button next to the running job. Both ask for
confirmation first (*"Stop the current generation? The partial output may be
deleted."*).

How cancellation behaves:

- The request reaches the **backend**, not just the UI — it is not faked by
  hiding the progress bar.
- The **direct Wan backend** stops mid-sampling: cancellation is checked in the
  denoise step callback and before/after each major stage (model load,
  encoding, sampling, decode, write), so most runs stop within a step. If a
  cancellation lands inside a single long call with no callback, it stops as
  soon as that call returns — the UI shows *"Stopping after current inference
  step…"*.
- A cancelled job is marked **cancelled**, never *completed* or *failed*.
- **Partial output** for that job (its incomplete video/preview, matched by the
  job's unique output name) is deleted; transient VRAM is released where CUDA is
  available. A small `*.cancelled.json` record is written to the project's
  `metadata/` folder.
- **Previously completed videos, source images, models and project files are
  never touched.** Cancelled jobs are not added to the generated-video list.
- If the job already finished before the cancellation was processed, it stays
  completed (the result is not thrown away).

Endpoint: `POST /api/generation/jobs/{job_id}/cancel` (also available
project-scoped). It validates the job, flags it for cancellation and returns
immediately without blocking on cleanup.

## Video Effects and Color & Look

The **Color & Look** panel defines the visual look of the final video as pure
**post-processing** — it never affects Wan inference. Controls: saturation,
contrast, hue, temperature, shadows, highlights, brightness, gamma, plus
creative effects in collapsible sections: **Vignette** (intensity/radius/
softness), **Film Grain** (intensity/size/animated), **Sharpness**, and an
optional **VHS Effect** (intensity, scanlines, chromatic aberration, noise,
jitter, tracking distortion, color bleeding, tape damage — disabled by
default, never forced).

Every change updates a **real-time preview** rendered on a frame of your
latest generated video (or the source image) with vanilla-JS canvas — the
video is never regenerated by slider changes; the preview is clearly labeled
"Preview only". **✨ Apply Effects** renders the look onto a selected raw
video with ffmpeg (`eq`, `hue`, `colortemperature`, `curves`, `unsharp`,
`vignette`, `noise`, `rgbashift` …), producing `<name>_fx.mp4` with a ✨ FX
badge in the libraries — **the raw video is never modified**. Metadata records
`video_effects_applied` and the full effect snapshot. Settings live in the
project file (`video_effects`), so old projects load unchanged.

### Final export pipeline

```text
Raw Wan output  →  Color & Look effects (…_fx)  →  Audio tracks (…_final)  →  final video
```

Apply effects first, then apply audio to the `_fx` video: each step keeps its
input untouched, so you always retain the raw generation, the graded version
and the final version with sound.

## Audio Tracks Post-Processing

Audio is applied **after** Wan generation — it never influences the AI
inference itself. In the project editor, the **Audio Tracks** panel lets you
upload multiple audio files (`mp3`, `wav`, `ogg`, `flac`, `m4a`; limits
configurable via `ALLOWED_AUDIO_EXTENSIONS` / `MAX_AUDIO_UPLOAD_SIZE_MB`),
which are copied into the project's `audio/` folder so projects stay portable.

Per track you can set: **Enabled**, **Volume** (0–200 %), **Start time** (offset
in seconds), **Fade in / Fade out**, **Loop** (repeat until the video ends) and
**Trim to video** (never exceed the video duration). Audio shorter than the
video leaves silence; longer audio is cut when trim is on.

**Apply Audio** mixes all enabled tracks with ffmpeg and muxes them with the
selected generated video, producing `<name>_final.mp4` — **the raw generated
video is never modified or overwritten**. The final video appears in the
libraries with a ♫ AUDIO badge, and its metadata records `has_audio`,
`audio_mix_applied`, `raw_output_file`, `final_output_file` and the exact track
settings used.

**FFmpeg requirement:** the app uses `FFMPEG_PATH` if set, then `ffmpeg` from
PATH, then automatically falls back to the binary bundled with
`imageio-ffmpeg` — so it works out of the box. If no ffmpeg can be found, or an
audio file is missing/corrupted, the operation fails with a clear error and the
raw video is left untouched. Not implemented by design: lipsync, TTS,
beat-sync, audio-driven generation, timeline/DAW editing.

## Failure behavior — no fake output, ever

If a model component is missing, a dependency is not installed, CUDA is unavailable, the resolution/frame count is invalid, or inference/encoding fails, the job is marked **failed with the precise reason** — no placeholder video, no fake preview, no success metadata. Successful runs record `mock_generation: false`, the real backend name and the exact bundle snapshot used.

---

## Project structure

```text
app/
  main.py            FastAPI entry point
  config.py          .env loading + logging
  routes/            page + API routers
  services/
    wan_backend.py   REAL Wan 2.2+ inference (diffusers pipelines, bundle loading)
    …                projects, models, generation jobs, camera motion, export, media
  models/            pydantic data models (.wanproj, jobs, registry)
  templates/         Jinja2 pages and partials
  static/            css / js / img (no build step)
projects/            your projects (one folder each)
models/              model storage + registry.json
workflows/           exported ComfyUI workflows
.env.example         documented configuration template
```

---

## Credits

```text
Radica - WanVideoGenerator
Concept & Design: Fabrizio Radica
Project by RadicaDesign
```

Built on the shoulders of [Wan 2.2](https://github.com/Wan-Video/Wan2.2) (Alibaba), [Hugging Face diffusers](https://github.com/huggingface/diffusers), FastAPI and PyTorch.
