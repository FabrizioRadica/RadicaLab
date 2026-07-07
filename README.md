# Radica · WanVideoGenerator

**A browser-based AI video generation studio for Wan 2.2+ — real model loading, real inference, real videos.**

```text
Radica - WanVideoGenerator
Concept & Design: Fabrizio Radica
Project by RadicaDesign
```

WanVideoGenerator is a professional, self-hosted **Python / FastAPI** web application that generates videos locally with **Wan 2.2 or later** through Hugging Face *diffusers*. It supports **Text2Video**, **Image2Video**, project management, real Wan backend diagnostics, post-processing Color & Look, multi-track audio mixing, ComfyUI workflow export, and a dedicated **VideoSequenceQueue** workflow for rendering several clips sequentially.

The UI is fully server-rendered with **Jinja2 + vanilla JavaScript** — no Node.js, no build step, no frontend framework. Everything runs on your machine; nothing is uploaded anywhere.

<img width="1264" height="690" alt="Screenshot 2026-07-07 084827" src="https://github.com/user-attachments/assets/20c683fd-6538-48b3-a50f-6147df525043" />

<img width="1276" height="638" alt="Screenshot 2026-07-07 121606" src="https://github.com/user-attachments/assets/8aa1b455-90fa-45ad-9602-cd108db66d2e" />

---

## Features

- 🎬 **Real Wan 2.2+ generation** — Text2Video and Image2Video via diffusers `WanPipeline` / `WanImageToVideoPipeline`, with prompt, negative prompt, seed, frames, FPS, guidance scale, steps and resolution control.
- 🧩 **VideoSequenceQueue** — a separate workflow next to Single Clip for building multiple ordered clips, rendering them sequentially, stopping/resuming from the current clip, reusing Color & Look and Audio Tracks, exporting separate clips, and optionally creating a merged final video.
- 📦 **Model Bundle manager** — register local model files such as diffusion model/DiT, VAE, text encoder, tokenizer and LoRAs. Supports ComfyUI-repackaged single `.safetensors` files, fp8-scaled text encoders, and diffusers pipeline folders.
- 🗂 **Project-based workflow** — every single clip project lives in a project folder with `source/`, `outputs/`, `previews/`, `metadata/`, `workflows/` and a portable `.wanproj` file.
- 🧵 **Sequence projects** — sequence projects are saved separately from single clip projects and can be reloaded later to continue work. Each sequence stores clips, assets, outputs, global settings, per-clip overrides, Color & Look state, Audio Tracks and render state.
- 🎥 **Camera Motion Assistant** — 16 camera movements with an animated diagram that composes a camera prompt fragment without touching your text.
- 📊 **Honest job system** — background generation with live progress, stage log, requested-vs-effective metadata and readable failures. The app never produces fake or placeholder output.
- ⏱ **Duration control** — set duration in seconds; duration, FPS and frame count stay synchronized with Wan's valid frame-count rule.
- 🖥 **System resource monitor** — live CPU / RAM / GPU / VRAM / disk pills in the topbar with warning states.
- 🔄 **Persistent generation progress** — jobs live server-side; switch pages or refresh the browser and the progress strip re-attaches to the running job.
- 🎨 **Color & Look video effects** — saturation, contrast, hue, temperature, shadows, highlights, brightness, gamma, vignette, film grain, sharpness and optional VHS effect. Applied through ffmpeg as post-processing; raw video is never modified.
- 🎵 **Audio Tracks post-processing** — upload multiple audio files, set volume/start/fades/loop/trim per track, and mix them into finished videos with ffmpeg. Raw video is never modified.
- 🖼 **Video, project and sequence libraries** — browse, preview, reload, download, copy metadata and manage outputs.
- 🔌 **ComfyUI workflow export** — JSON node graph plus complete generation metadata.
- ⚙️ **Everything configurable via `.env`**, with a live backend-readiness indicator in the UI.

---

## What is new: VideoSequenceQueue

The **VideoSequenceQueue** is a separate section beside the current Single Clip workflow:

```text
[ Single Clip ] [ VideoSequenceQueue ]
```

**Single Clip remains unchanged.** The sequence workflow is dedicated to building a short sequence made of multiple generated clips.

Example use case:

```text
Beach sequence

Clip 01 -> inside a beach bar -> bar ambience
Clip 02 -> outside on the beach -> waves, wind, crowd
Clip 03 -> sunset shoreline -> soft wind, seagulls
Sequence audio -> continuous music bed over the final merged video
```

Each sequence clip can be:

```text
Image Reference + prompt
Prompt Only
```

The queue renders **sequentially**:

```text
Clip 01 finishes
then Clip 02 starts
then Clip 03 starts
...
```

It never renders clips in parallel. This keeps VRAM/RAM usage safer and makes stop/resume predictable.

### Sequence clip states

Each clip has an explicit state:

```text
ready
queued
rendering
completed
failed
cancel_requested
cancelled
stopped
needs_regeneration
skipped
```

If you stop while Clip 03 is rendering:

```text
Clip 01 -> completed
Clip 02 -> completed
Clip 03 -> stopped / needs_regeneration
Clip 04 -> queued
```

You can edit Clip 03 and resume from Clip 03. Completed clips are preserved unless you explicitly regenerate them.

### Color & Look in sequences

The existing **Color & Look** module is reused. It is not rewritten.

It can run in these contexts:

```text
single_clip
sequence_global
sequence_clip
```

A sequence supports:

```text
Global Color & Look
Custom Color & Look per clip
Color & Look Off per clip
```

The clip pipeline is:

```text
Wan raw render
→ Color & Look / effects
→ Clip Audio Tracks
→ Clip final
```

Color & Look remains post-processing. It is not applied inside Wan inference, and raw videos are never modified.

### Audio in sequences

The existing **Audio Tracks** module is reused. It is not rewritten.

The sequence has two audio levels:

```text
Clip Audio Tracks
Sequence Audio Tracks
```

**Clip Audio Tracks** are ambience/sound effects specific to a clip:

```text
Clip 01: bar ambience, glasses, people talking
Clip 02: waves, wind, beach crowd
Clip 03: seagulls, soft wind
```

They are applied before the final merge.

**Sequence Audio Tracks** are global audio over the whole final sequence:

```text
music bed
voiceover
global cinematic ambience
```

They are applied after the final video merge, so music does not restart at each clip.

Full pipeline:

```text
Clip 01:
  Wan raw render
  Apply Color & Look
  Apply Clip Audio Tracks
  Output Clip_001_final.mp4

Clip 02:
  Wan raw render
  Apply Color & Look
  Apply Clip Audio Tracks
  Output Clip_002_final.mp4

Clip 03:
  Wan raw render
  Apply Color & Look
  Apply Clip Audio Tracks
  Output Clip_003_final.mp4

Merge:
  Merge Clip_001_final + Clip_002_final + Clip_003_final
  Output Sequence_merged_video.mp4

Sequence Audio:
  Apply Sequence Audio Tracks to Sequence_merged_video.mp4
  Output Sequence_final.mp4
```

If output mode is **Render clips only**, Sequence Audio Tracks are not applied because there is no final merged video.

### Sequence outputs

Per clip:

```text
BeachShort001_clip_001_raw.mp4
BeachShort001_clip_001_fx.mp4
BeachShort001_clip_001_final.mp4
```

Final optional outputs:

```text
BeachShort001_merged_video.mp4
BeachShort001_final.mp4
```

Regenerating a clip should archive or version old outputs instead of silently overwriting them.

### Sequence save/load

Single clip projects and sequence projects are separate.

Project type markers:

```json
{
  "project_type": "single_clip"
}
```

```json
{
  "project_type": "video_sequence"
}
```

Older projects without `project_type` are treated as `single_clip`.

A sequence project saves:

```text
sequence_id
name
global generation settings
global Color & Look
sequence audio tracks
clip list and order
per-clip prompts/images/settings
per-clip Color & Look mode
per-clip audio tracks
render state
generated outputs
diagnostics
```

Uploaded reference images and audio files are copied into the sequence project folder, not stored as temporary upload paths.

---

## Requirements

### Hardware

| Component | Minimum | Recommended |
|---|---:|---:|
| GPU | NVIDIA, 8 GB VRAM with CPU offload | NVIDIA RTX, 12 GB+ VRAM |
| System RAM | 16 GB | 32 GB |
| Disk | ~25 GB free for Wan 2.2 TI2V-5B model set | SSD with generous free space |

CPU-only inference is technically possible but unrealistically slow. The app warns clearly.

### Software

- **Python 3.10+**. Python 3.11 is recommended.
- **NVIDIA driver** compatible with your installed PyTorch CUDA build.
- **PyTorch with CUDA** matching your GPU.
- Python packages listed in `requirements.txt`.
- ffmpeg. The app can use `FFMPEG_PATH`, `ffmpeg` from PATH, or the binary bundled by `imageio-ffmpeg`.

Important package groups from `requirements.txt` include:

```text
fastapi
uvicorn
jinja2
python-dotenv
pydantic
torch
diffusers
transformers
accelerate
safetensors
sentencepiece
ftfy
Pillow
imageio-ffmpeg
psutil
nvidia-ml-py
```

Install from `requirements.txt`; do not manually install packages one by one unless you are fixing a CUDA/PyTorch version mismatch.

---

## Model files

A working Wan 2.2 TI2V-5B bundle needs at minimum:

| Component | Example file | Notes |
|---|---|---|
| Diffusion model / DiT | `wan2.2_ti2v_5B_fp16.safetensors` | main Wan model |
| VAE | `wan2.2_vae.safetensors` | required to decode video frames |
| Text encoder | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | fp8-scaled files are dequantized on load |
| Tokenizer | local UMT5 tokenizer folder | recommended to avoid Hugging Face lookup during startup |

Example folder layout:

```text
models/
  diffusion_models/
    wan2.2_ti2v_5B_fp16.safetensors
    wan2.2_ti2v_5B_fp8.safetensors
  vae/
    wan2.2_vae.safetensors
  text_encoders/
    umt5_xxl_fp8_e4m3fn_scaled.safetensors
  tokenizers/
    umt5_xxl/
      tokenizer files...
```

You can also point a model bundle to a local **diffusers pipeline folder** containing `model_index.json`.

A local `tokenizer_path` is recommended. Without it, the backend may try to fetch tokenizer/config files from Hugging Face once and cache them.

---

## Installation

### 1. Clone or copy the project

```bash
git clone <your-repo-url> RadicaWanGen
cd RadicaWanGen
```

If you already have the project folder locally, just open a terminal inside it.

### 2. Create a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Windows CMD:

```bat
python -m venv .venv
.venv\Scripts\activate.bat
```

Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
```

If activation is blocked on Windows PowerShell, run PowerShell as user and execute:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate again.

### 3. Install PyTorch CUDA first

For modern NVIDIA GPUs and RTX 50xx/40xx/30xx setups, use the CUDA 12.8 wheel when appropriate:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu128
```

If your GPU/driver requires another CUDA build, use the official PyTorch selector and install the matching command before continuing.

Check CUDA availability:

```bash
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO CUDA')"
```

Expected:

```text
True
NVIDIA ...
```

If `torch.cuda.is_available()` is `False`, the real Wan backend will not run on GPU.

### 4. Install project dependencies from requirements.txt

This project has a `requirements.txt`. Use it.

```bash
pip install -r requirements.txt
```

If you want to update already-installed packages:

```bash
pip install -r requirements.txt --upgrade
```

If `requirements.txt` also contains a torch line and you already installed the correct CUDA torch manually, verify that it did not replace your CUDA build with a CPU build:

```bash
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
```

### 5. Create `.env`

Windows:

```bat
copy .env.example .env
```

Linux/macOS:

```bash
cp .env.example .env
```

Then edit `.env`.

Recommended starting values for a 12 GB RTX GPU:

```env
GENERATION_BACKEND=wan
USE_CUDA=true
DEFAULT_DEVICE=cuda
WAN_TORCH_DTYPE=float16

WAN_KEEP_MODEL_WARM=true
WAN_CLEAR_TEMP_VRAM_AFTER_GENERATION=true
WAN_UNLOAD_MODEL_AFTER_GENERATION=false
WAN_CLEAR_TEMP_VRAM_AFTER_CANCEL=true
WAN_UNLOAD_MODEL_AFTER_CANCEL=true
WAN_LOG_VRAM_CLEANUP=true

SYSTEM_MONITOR_ENABLED=true
SYSTEM_MONITOR_SHOW_GPU=true
SYSTEM_MONITOR_SHOW_DISK=true
```

Offload depends on your config naming. The backend internally supports:

```text
model / auto
sequential
none
```

Recommended normal UI setting for RTX 5070 Ti 12 GB:

```text
Memory optimization: ON
Model offload: OFF
Unload model after generation: OFF
Precision: fp16
Device: CUDA
```

Use offload only if you hit CUDA out-of-memory errors.

### 6. Add model files

Place the Wan model files under `models/` or any stable folder.

Then register them in the **Models** page of the app or edit the model registry if your project supports that.

Make sure the bundle has valid paths for:

```text
diffusion_model_path
vae_path
text_encoder_path
tokenizer_path, recommended
```

Path mistakes are common. If a file is not detected, check the exact folder spelling.

Example issue previously found:

```text
diiffusion_models
diffusion_models
```

A single extra letter in the path is enough to mark a model as missing.

### 7. Start the app

Preferred:

```bash
python -m app.main
```

Alternative:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

The topbar should show a green backend indicator such as:

```text
wan backend ready - NVIDIA GeForce RTX ...
```

---

## Running with helper scripts

If the repository includes helper scripts such as:

```text
start.bat
start.ps1
run.bat
restart.bat
```

you can use them instead of manual terminal commands.

A correct Windows start script should essentially do:

```bat
@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m app.main
pause
```

If a script fails, run the manual installation/start steps above to see the real error.

---

## Quick start: Single Clip

1. Click **＋ New Project**.
2. Choose mode, orientation and resolution.
3. Select a model bundle.
4. Write the prompt and optional negative prompt.
5. For Image2Video, upload a source image.
6. Set generation parameters or choose a Wan2.2 preset.
7. Click **Generate Video**.
8. Preview the result, apply Color & Look if needed, then apply Audio Tracks if needed.

Final single-clip pipeline:

```text
Raw Wan output
→ Color & Look effects (..._fx)
→ Audio tracks (..._final)
→ final video
```

The raw output is never modified.

---

## Quick start: VideoSequenceQueue

1. Click **VideoSequenceQueue** next to Single Clip.
2. Create or select a sequence.
3. Add clips:
   - **Add Image Clip** for Image2Video clips.
   - **Add Prompt Clip** for Text2Video clips.
4. For each clip, set prompt, negative prompt, source image if needed and optional overrides.
5. Set **Global Color & Look** if all clips should share a look.
6. For a specific clip, use **Edit Look** if it needs a custom look.
7. Add **Clip Audio Tracks** for ambience or effects specific to that clip.
8. Add **Sequence Audio Tracks** for music/voiceover over the final merged video.
9. Choose output mode:
   - **Render clips only**
   - **Render clips + final merge**
   - **Render selected clips only**
10. Click **Render Queue**.

If you stop the queue:

```text
completed clips stay completed
current clip becomes stopped / needs_regeneration
queued clips remain queued
```

After editing the stopped clip, click:

```text
Resume from current clip
```

The current clip is regenerated and the queue continues.

---

## Recommended Wan2.2 5B presets

### Normal Wan2.2 5B

Fast Preview Portrait:

```text
352x640
49 frames
16 fps
steps 12
CFG 3.0
Euler / Simple
ModelSamplingSD3 ON
I2V shift 5.0
T2V shift 8.0
```

Safe Quality Portrait:

```text
480x832
81 frames
16 fps
steps 24
CFG 3.5
Euler / Simple
ModelSamplingSD3 ON
I2V shift 5.0
T2V shift 8.0
```

High Quality Portrait:

```text
704x1280
81 frames
16 fps
steps 32
CFG 4.0
warning: heavy render
```

Avoid using `704x1280 + 121 frames` as a normal test preset. It is a heavy/final render setting.

### Wan2.2 5B Turbo / Lightning / Lightx2v

Turbo models need different settings.

Recommended Turbo Preview Portrait:

```text
352x640
49 frames
16 fps
steps 4
CFG 1.0
Euler / Simple
ModelSamplingSD3 ON
I2V shift 5.0
T2V shift 8.0
```

Turbo Quality Portrait:

```text
480x832
49 frames
16 fps
steps 5
CFG 1.0
Euler / Simple
ModelSamplingSD3 ON
I2V shift 5.0
T2V shift 8.0
```

If you see:

```text
colored flashes
flicker
unstable colors
overexposed frames
```

on a Turbo model, lower CFG to `1.0` and use 4–6 steps.

Do not use normal Safe Quality values such as CFG 3.5/4.0 on Turbo unless you intentionally want to test and accept artifacts.

---

## Generation Parameters / KSampler compatibility

The **Generation Parameters** panel includes ComfyUI/KSampler-compatible sampling values:

```text
Seed
Control after generate
Steps
CFG / Guidance Scale
Sampler
Scheduler
Denoise
```

Important distinctions:

```text
Denoise is not CFG.
Denoise is not Motion Strength.
Denoise is not Image Influence.
CFG / Guidance Scale maps to ComfyUI cfg.
```

The selected sampler must be really used by the backend or explicitly blocked/routed. No silent fallback.

Direct backend supported flow-matching sampler mappings:

| Sampler | Direct backend scheduler |
|---|---|
| `uni_pc` | `UniPCMultistepScheduler` |
| `euler` | `FlowMatchEulerDiscreteScheduler` |
| `heun` | `FlowMatchHeunDiscreteScheduler` |
| `dpmpp_2m` | `DPMSolverMultistepScheduler` flow |
| `dpmpp_2m_sde` | `DPMSolverMultistepScheduler` flow SDE |

Unsupported direct samplers must not be faked.

---

## ModelSamplingSD3 Support

`ModelSamplingSD3` is a real model sampling modifier applied between model loading and sampling.

ComfyUI graph:

```text
Load Diffusion Model → ModelSamplingSD3(shift) → KSampler
```

For Wan2.2 flow-matching, this corresponds to the flow-matching sigma shift.

Recommended defaults:

```text
Text2Video: shift 8.0
Image2Video: shift 5.0
```

It is independent from:

```text
CFG
denoise
sampler
scheduler
motion strength
image influence
prompt text
```

It must be stored as a first-class setting and shown in requested-vs-effective diagnostics.

---

## VRAM Cleanup

VRAM cleanup runs after:

```text
successful render
failed render
Stop / Cancel
sequence clip completion
sequence clip failure
sequence stop/cancel
```

Cleanup uses:

```text
gc.collect()
torch.cuda.empty_cache()
torch.cuda.ipc_collect(), if available
```

Important honesty rule:

```text
empty_cache() frees PyTorch cached blocks.
It does not necessarily free VRAM still held by live model objects.
```

Model weights are unloaded only when configured.

For VideoSequenceQueue, default behavior should be:

```text
Balanced queue mode:
  keep pipeline warm
  clear temporary tensors between clips
```

Alternative modes:

```text
Aggressive cleanup between clips
Reload model every clip
```

---

## Backend Diagnostics

The diagnostics panel should show:

```text
requested vs effective settings
component device map
component dtype map
timings
GPU memory before/after/peak
image preprocessing
warnings
VRAM cleanup report
pipeline reused true/false
cache miss reasons when available
```

This is essential to understand performance differences between first and second render, ComfyUI and direct backend, or normal and Turbo models.

---

## Color & Look video effects

The **Color & Look** panel defines the visual look as post-processing.

Controls include:

```text
Basic Color:
  saturation
  contrast
  hue
  temperature
  shadows
  highlights
  brightness
  gamma

Vignette:
  intensity
  radius
  softness

Film Grain:
  intensity
  grain size
  animated grain

Sharpness

VHS Effect:
  intensity
  scanlines
  chromatic aberration
  noise
  jitter
  tracking distortion
  color bleeding
  tape damage
```

Apply Effects creates:

```text
<name>_fx.mp4
```

The raw video is never modified.

---

## Audio Tracks post-processing

Audio is applied after Wan generation and after Color & Look if effects are used.

Per track:

```text
Enabled
Volume
Start time
Fade in
Fade out
Loop
Trim to video
```

Apply Audio creates:

```text
<name>_final.mp4
```

The raw video is never modified.

In sequences:

```text
Clip Audio Tracks -> applied per clip before merge
Sequence Audio Tracks -> applied after final merge
```

---

## System resource monitor

The topbar shows:

```text
CPU
RAM
GPU
VRAM
Disk usage/free space
```

If NVML fails:

```text
System monitor: NVML query failed
```

the app should keep working. This affects monitoring, not necessarily rendering.

---

## Stopping a generation

Stop/cancel must reach the backend, not just hide the UI.

Behavior:

```text
request cancellation
mark job cancelled/stopped
delete incomplete partial output for current job
preserve completed videos
cleanup VRAM
write cancellation metadata
```

For sequences:

```text
Stop Queue stops the current clip safely.
Completed clips remain completed.
Current clip becomes stopped / needs_regeneration.
The sequence can resume from that clip.
```

---

## Project structure

Typical structure:

```text
app/
  main.py
  config.py
  routes/
  services/
    wan_backend.py
    generation_service.py
    model_service.py
    gpu_memory_service.py
    sequence_queue_service.py
  models/
  templates/
  static/
projects/
models/
workflows/
.env.example
requirements.txt
README.md
```

Single clip project folder:

```text
projects/
  <project_id>/
    source/
    outputs/
    previews/
    metadata/
    workflows/
    audio/
```

Sequence project folder:

```text
projects/
  <sequence_id>/
    sequence.json
    assets/
      images/
      audio/
    clips/
      clip_001/
        raw/
        fx/
        final/
        metadata.json
      clip_002/
        raw/
        fx/
        final/
        metadata.json
    exports/
      merged/
      final/
```

---

## Troubleshooting

### Backend is not ready

Check:

```bash
python -c "import torch; print(torch.cuda.is_available())"
pip install -r requirements.txt
```

Verify `.env`:

```env
GENERATION_BACKEND=wan
USE_CUDA=true
DEFAULT_DEVICE=cuda
```

### Model file not found

Check exact paths in the model bundle or `registry.json`.

Common mistake:

```text
diffusion_models
diiffusion_models
```

### App tries to contact Hugging Face

Add a local tokenizer path:

```text
models/tokenizers/umt5_xxl/
```

and set it in the bundle.

### Second render is much slower

Check:

```text
resolution
frames
steps
pipeline reused true/false
model offload
VRAM before/after cleanup
text encoder / VAE device
```

A render like:

```text
704x1280 + 121 frames
```

is much heavier than:

```text
352x640 + 49 frames
```

### Turbo output has color flashes

Use Turbo presets:

```text
CFG 1.0
steps 4-6
Euler / Simple
I2V shift 5.0
```

### Video controls look clipped

Check CSS for the preview video:

```css
.preview-video-shell {
  overflow: visible;
}

.preview-video {
  height: auto;
  object-fit: contain;
}
```

---

## Failure behavior — no fake output, ever

If a model component is missing, a dependency is not installed, CUDA is unavailable, resolution/frame count is invalid, inference fails, encoding fails, ffmpeg fails or an asset is missing, the job must fail clearly.

No placeholder video.
No fake preview.
No success metadata for failed jobs.

---

## Warning

Warning: this project is provided as is, without any warranty regarding functionality, stability, compatibility, or safety on your system.

I do not accept any responsibility, in any way, for any damage, malfunction, instability, data loss, crashes, overheating, melting, or even partial failure of GPUs, CPUs, power supplies, motherboards, drives, RAM, or any other hardware component, past, present, or future.

Installation, configuration, and execution of this project are entirely at your own risk and under your full responsibility.

Use it as you see fit, according to your technical skills and the limits of your hardware.

By downloading, installing, or running this project, you declare that you have read, understood, and fully accepted this disclaimer.

For a few selected cases, depending on my availability, I may offer direct support for installation and initial configuration.

---

## Credits

```text
Radica - WanVideoGenerator
Concept & Design: Fabrizio Radica
Project by RadicaDesign
```

Built on the shoulders of [Wan 2.2](https://github.com/Wan-Video/Wan2.2), [Hugging Face diffusers](https://github.com/huggingface/diffusers), FastAPI, PyTorch and ffmpeg.
