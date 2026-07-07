"""Real Wan 2.2+ generation backend built on Hugging Face diffusers.

This module performs actual model loading and actual inference. It never
produces placeholder output: every failure raises WanBackendError with a
user-readable message describing exactly what is missing or broken.

Supported model bundle layouts
------------------------------
1. Diffusers pipeline folder (recommended for A14B dual-expert models):
   point `checkpoint_path`, `diffusion_model_path` or `config_path` at a
   directory containing `model_index.json` (e.g. a local snapshot of
   Wan-AI/Wan2.2-TI2V-5B-Diffusers). The whole pipeline is loaded from it.

2. Single-file components (ComfyUI-style safetensors):
   - `diffusion_model_path` / `checkpoint_path`: Wan DiT safetensors
     (e.g. wan2.2_ti2v_5B_fp16.safetensors) — loaded with
     WanTransformer3DModel.from_single_file().
   - `vae_path`: wan2.2_vae.safetensors / wan_2.1_vae.safetensors — loaded
     with AutoencoderKLWan.from_single_file().
   - `text_encoder_path`: either a Hugging Face UMT5EncoderModel directory or
     a single safetensors file such as umt5_xxl_fp8_e4m3fn_scaled.safetensors.
     fp8-scaled ComfyUI files are dequantized on load (weight * scale_weight).
   - `tokenizer_path`: optional local tokenizer directory. When empty, the
     UMT5-XXL tokenizer is fetched once from the Hugging Face Hub (small
     download) if WAN_ALLOW_HF_DOWNLOAD is enabled.
   - `custom_component_paths["transformer_2"]`: optional second (low-noise)
     DiT for Wan2.2 A14B dual-expert checkpoints.

The pipeline is cached between jobs and reloaded only when the bundle,
mode, device or dtype changes.
"""

from __future__ import annotations

import gc
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.config import logger, settings

ProgressCallback = Callable[[int, str], None]


class WanBackendError(Exception):
    """Raised for real-backend failures with a user-readable message."""


class WanGenerationCancelled(Exception):
    """Raised inside the backend when the user requested cancellation. Kept
    distinct from WanBackendError so a cancelled job is never reported as a
    failure (patch6b §4)."""


# --------------------------------------------------------------------------
# Diagnostics report (patch6 §4, §5, §14, §15)
# --------------------------------------------------------------------------

@dataclass
class GenerationReport:
    """Everything the backend actually did, for the diagnostics panel and the
    requested-vs-effective metadata. Nothing here is hidden from the user."""

    duration_seconds: float = 0.0
    # Effective sampling/model settings the backend really used.
    effective: dict[str, Any] = field(default_factory=dict)
    # Per-component device and dtype the backend resolved at load time.
    device_map: dict[str, str] = field(default_factory=dict)
    dtype_map: dict[str, str] = field(default_factory=dict)
    # Phase timings in seconds (patch6 §14).
    timings: dict[str, float] = field(default_factory=dict)
    # VRAM stats in MB (patch6 §15).
    gpu_memory: dict[str, float] = field(default_factory=dict)
    # Visible fallback / difference notes (patch6 §3). Never silently dropped.
    warnings: list[str] = field(default_factory=list)
    # Prompt conditioning traceability (patch6 §9).
    final_positive_prompt: str = ""
    final_negative_prompt: str = ""
    # Image2Video preprocessing details (patch6 §10).
    image_preprocessing: dict[str, Any] | None = None
    # Effective offload policy name (disabled | balanced | aggressive).
    offload_policy: str = ""
    reused_pipeline: bool = False
    # ModelSamplingSD3 requested/effective + whether it was really applied (patch7).
    model_sampling: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "effective_settings": self.effective,
            "device_map": self.device_map,
            "dtype_map": self.dtype_map,
            "timings": self.timings,
            "gpu_memory": self.gpu_memory,
            "warnings": self.warnings,
            "final_positive_prompt": self.final_positive_prompt,
            "final_negative_prompt": self.final_negative_prompt,
            "image_preprocessing": self.image_preprocessing,
            "offload_policy": self.offload_policy,
            "reused_pipeline": self.reused_pipeline,
            "model_sampling": self.model_sampling,
            "duration_seconds": self.duration_seconds,
        }


# --------------------------------------------------------------------------
# Dependency / device checks
# --------------------------------------------------------------------------

REQUIRED_PACKAGES = (
    ("torch", "torch"),
    ("diffusers", "diffusers"),
    ("transformers", "transformers"),
    ("accelerate", "accelerate"),
    ("safetensors", "safetensors"),
    ("imageio_ffmpeg", "imageio-ffmpeg"),
)

MIN_DIFFUSERS = (0, 35, 0)


def missing_dependencies() -> list[str]:
    """Names of pip packages required for real Wan inference that are not
    importable in this environment."""
    missing: list[str] = []
    for module_name, pip_name in REQUIRED_PACKAGES:
        try:
            __import__(module_name)
        except ImportError:
            missing.append(pip_name)
    if "diffusers" not in missing:
        import diffusers

        version = tuple(int(p) for p in diffusers.__version__.split(".")[:3] if p.isdigit())
        if version < MIN_DIFFUSERS:
            missing.append(f"diffusers>={'.'.join(map(str, MIN_DIFFUSERS))} "
                           f"(installed: {diffusers.__version__})")
    return missing


def _require_dependencies() -> None:
    missing = missing_dependencies()
    if missing:
        raise WanBackendError(
            "Real Wan generation requires Python packages that are not installed: "
            + ", ".join(missing)
            + ". Install them with:  pip install -r requirements.txt  "
              "(PyTorch with CUDA: see README → Real Wan Backend Requirements)."
        )


def resolve_device() -> str:
    """The torch device generation will run on, honoring USE_CUDA/DEFAULT_DEVICE."""
    import torch

    wanted = settings.default_device.lower()
    if settings.use_cuda and wanted.startswith("cuda"):
        if torch.cuda.is_available():
            return wanted
        raise WanBackendError(
            "CUDA was requested (USE_CUDA=true) but torch.cuda.is_available() is False. "
            "Install a CUDA-enabled PyTorch build for your GPU "
            "(see README → Real Wan Backend Requirements) or set DEFAULT_DEVICE=cpu. "
            "Note: CPU inference with Wan is extremely slow and not recommended."
        )
    if wanted == "cpu" or not settings.use_cuda:
        logger.warning("Wan backend running on CPU — expect extremely long generation times.")
        return "cpu"
    return wanted


def torch_dtype():
    import torch

    name = settings.wan_torch_dtype.lower()
    mapping = {"float16": torch.float16, "fp16": torch.float16,
               "bfloat16": torch.bfloat16, "bf16": torch.bfloat16,
               "float32": torch.float32, "fp32": torch.float32}
    if name not in mapping:
        raise WanBackendError(f"Unsupported WAN_TORCH_DTYPE '{settings.wan_torch_dtype}'. "
                              "Use float16, bfloat16 or float32.")
    return mapping[name]


# Friendly precision names for reporting. Maps torch dtypes both ways.
_DTYPE_TO_PRECISION = {
    "torch.float32": "fp32", "torch.float16": "fp16", "torch.bfloat16": "bf16",
    "torch.float8_e4m3fn": "fp8_e4m3fn", "torch.float8_e5m2": "fp8_e5m2",
}
_PRECISION_ALIASES = {
    "float32": "fp32", "fp32": "fp32",
    "float16": "fp16", "fp16": "fp16",
    "bfloat16": "bf16", "bf16": "bf16",
    "fp8": "fp8", "fp8_e4m3fn": "fp8_e4m3fn", "fp8_e5m2": "fp8_e5m2",
}


def dtype_to_precision_name(dtype) -> str:
    """'fp16' / 'bf16' / 'fp32' / 'fp8_e4m3fn' for a torch dtype."""
    return _DTYPE_TO_PRECISION.get(str(dtype), str(dtype).replace("torch.", ""))


def normalize_precision_name(name: str) -> str:
    return _PRECISION_ALIASES.get((name or "").lower().strip(), (name or "").lower().strip())


def effective_compute_precision() -> str:
    """The precision the backend actually computes in (WAN_TORCH_DTYPE)."""
    return dtype_to_precision_name(torch_dtype())


def _param_device_dtype(component):
    """(device_str, dtype) of a loaded component's first parameter, or (None, None)."""
    if component is None:
        return None, None
    try:
        p = next(component.parameters(), None)
    except (AttributeError, TypeError):
        p = None
    if p is None:
        return None, None
    return str(p.device), p.dtype


def _has_offload_hook(component) -> bool:
    """True when accelerate has attached a CPU-offload hook that streams the
    component to the GPU during forward (enable_*_cpu_offload)."""
    if component is None:
        return False
    return getattr(component, "_hf_hook", None) is not None or hasattr(component, "_diffusers_hook")


def _effective_component_device(component, offload_mode: str) -> str:
    """Where a component's compute actually happens, honestly.

    With model/sequential CPU offload, weights rest on CPU but are streamed to
    the GPU for each forward pass — reported as 'cuda (offloaded)' rather than
    the misleading resting device 'cpu'.
    """
    device, _ = _param_device_dtype(component)
    if device is None:
        return "n/a"
    if device.startswith("cpu") and offload_mode in ("model", "sequential") and _has_offload_hook(component):
        return "cuda (offloaded)"
    if device.startswith("cpu") and offload_mode in ("model", "sequential"):
        # Offload configured but no hook yet (pre-first-run): compute will move
        # it to the GPU on demand.
        return "cuda (offloaded)"
    return device


def device_info() -> dict[str, Any]:
    """Non-throwing device summary for the status API."""
    info: dict[str, Any] = {"cuda_available": False, "device_name": None,
                            "vram_total_gb": None, "torch_version": None}
    try:
        import torch

        info["torch_version"] = torch.__version__
        if torch.cuda.is_available():
            info["cuda_available"] = True
            props = torch.cuda.get_device_properties(0)
            info["device_name"] = props.name
            info["vram_total_gb"] = round(props.total_memory / (1024 ** 3), 1)
    except ImportError:
        pass
    return info


# --------------------------------------------------------------------------
# Bundle resolution
# --------------------------------------------------------------------------

@dataclass
class ResolvedBundle:
    """Validated, existing file-system paths for the selected model bundle."""

    mode: str  # "text2video" | "image2video"
    pipeline_dir: Path | None = None  # diffusers folder with model_index.json
    transformer_path: Path | None = None
    transformer_2_path: Path | None = None
    vae_path: Path | None = None
    text_encoder_path: Path | None = None
    tokenizer_path: Path | None = None
    scheduler_config_path: Path | None = None
    lora_paths: list[Path] = field(default_factory=list)
    bundle_id: str = ""
    bundle_name: str = ""

    def cache_key(self) -> tuple:
        return (self.mode, str(self.pipeline_dir), str(self.transformer_path),
                str(self.transformer_2_path), str(self.vae_path),
                str(self.text_encoder_path), str(self.tokenizer_path),
                str(self.scheduler_config_path),
                tuple(str(p) for p in self.lora_paths))


def _existing_dir_with(path_str: str, marker: str) -> Path | None:
    if not path_str:
        return None
    p = Path(path_str)
    if p.is_dir() and (p / marker).exists():
        return p
    return None


def resolve_bundle(bundle: dict, mode: str) -> ResolvedBundle:
    """Turn a model-bundle component snapshot into verified paths.

    Raises WanBackendError listing every missing required component instead of
    failing on the first one, so the user can fix the bundle in one pass.
    """
    resolved = ResolvedBundle(mode=mode,
                              bundle_id=bundle.get("model_bundle_id", ""),
                              bundle_name=bundle.get("model_bundle_name", ""))

    # Layout 1: a diffusers pipeline directory anywhere in the core paths.
    for key in ("config_path", "checkpoint_path", "diffusion_model_path"):
        d = _existing_dir_with(bundle.get(key, ""), "model_index.json")
        if d is not None:
            resolved.pipeline_dir = d
            return resolved

    problems: list[str] = []

    # Layout 2: single-file components. Prefer whichever core model path
    # actually exists on disk (diffusion_model_path first).
    dit_candidates = [p for p in (bundle.get("diffusion_model_path", ""),
                                  bundle.get("checkpoint_path", "")) if p]
    if not dit_candidates:
        problems.append("No diffusion model / main checkpoint is configured in the bundle.")
    else:
        existing = next((p for p in dit_candidates if Path(p).exists()), None)
        if existing is None:
            problems.append("Diffusion model not found on disk: "
                            + " | ".join(dit_candidates))
        else:
            resolved.transformer_path = Path(existing)

    t2 = (bundle.get("custom_component_paths") or {}).get("transformer_2", "")
    if t2:
        if Path(t2).exists():
            resolved.transformer_2_path = Path(t2)
        else:
            problems.append(f"Second diffusion model (transformer_2) not found: {t2}")

    vae = bundle.get("vae_path", "")
    if not vae:
        problems.append("No VAE is configured in the bundle (vae_path). Wan needs its VAE "
                        "(e.g. wan2.2_vae.safetensors) to decode frames.")
    elif not Path(vae).exists():
        problems.append(f"VAE not found on disk: {vae}")
    else:
        resolved.vae_path = Path(vae)

    te = bundle.get("text_encoder_path") or bundle.get("t5_encoder_path") or ""
    if not te:
        problems.append("No text encoder is configured in the bundle (text_encoder_path). "
                        "Wan needs the UMT5-XXL text encoder "
                        "(e.g. umt5_xxl_fp8_e4m3fn_scaled.safetensors).")
    elif not Path(te).exists():
        problems.append(f"Text encoder not found on disk: {te}")
    else:
        resolved.text_encoder_path = Path(te)

    tok = bundle.get("tokenizer_path", "")
    if tok:
        if Path(tok).exists():
            resolved.tokenizer_path = Path(tok)
        else:
            problems.append(f"Tokenizer directory not found: {tok}")
    elif not settings.wan_allow_hf_download:
        problems.append("No tokenizer_path is configured and WAN_ALLOW_HF_DOWNLOAD is "
                        "disabled — the UMT5 tokenizer cannot be resolved. Set "
                        "tokenizer_path to a local UMT5 tokenizer directory or enable "
                        "WAN_ALLOW_HF_DOWNLOAD.")

    sched = bundle.get("scheduler_config_path", "")
    if sched:
        if Path(sched).exists():
            resolved.scheduler_config_path = Path(sched)
        else:
            problems.append(f"Scheduler config not found: {sched}")

    for lora in bundle.get("lora_paths") or []:
        if not lora:
            continue
        if Path(lora).exists():
            resolved.lora_paths.append(Path(lora))
        else:
            problems.append(f"LoRA file not found: {lora}")

    if problems:
        raise WanBackendError(
            "The selected model bundle cannot be used for real Wan generation:\n- "
            + "\n- ".join(problems)
            + "\nFix the component paths in the Model Manager (Models page)."
        )
    return resolved


def validate_generation_params(width: int, height: int, frames: int, fps: int) -> None:
    if width % 16 or height % 16:
        raise WanBackendError(
            f"Wan requires width and height to be multiples of 16 (got {width}x{height}). "
            "Adjust the resolution in the project settings."
        )
    if (frames - 1) % 4 != 0:
        valid_down = frames - ((frames - 1) % 4)
        raise WanBackendError(
            f"Wan requires the frame count to be 4k+1 (e.g. 49, 81, 121). "
            f"Got {frames}; try {valid_down} or {valid_down + 4}."
        )
    if fps < 1:
        raise WanBackendError("FPS must be a positive value.")


# --------------------------------------------------------------------------
# Component loaders
# --------------------------------------------------------------------------

# UMT5-XXL encoder configuration (google/umt5-xxl), used when the text encoder
# is provided as a single safetensors file instead of a HF model directory.
UMT5_XXL_CONFIG = {
    "architectures": ["UMT5EncoderModel"],
    "model_type": "umt5",
    "d_ff": 10240,
    "d_kv": 64,
    "d_model": 4096,
    "num_heads": 64,
    "num_layers": 24,
    "vocab_size": 256384,
    "dropout_rate": 0.1,
    "layer_norm_epsilon": 1e-6,
    "relative_attention_num_buckets": 32,
    "relative_attention_max_distance": 128,
    "feed_forward_proj": "gated-gelu",
    "dense_act_fn": "gelu_new",
    "is_gated_act": True,
    "is_encoder_decoder": False,
    "tie_word_embeddings": False,
    "tokenizer_class": "T5Tokenizer",
    "pad_token_id": 0,
    "eos_token_id": 1,
}

UMT5_TOKENIZER_REPO = "google/umt5-xxl"


def _dequantize_fp8_state_dict(state_dict: dict, dtype) -> dict:
    """Convert a ComfyUI fp8-scaled state dict to a plain dtype state dict.

    fp8 weights are stored as float8_e4m3fn with a per-tensor `*.scale_weight`
    scalar: real_weight = fp8_weight.float() * scale_weight.
    """
    import torch

    out: dict = {}
    scales = {k: v for k, v in state_dict.items() if k.endswith(".scale_weight")}
    for key, tensor in state_dict.items():
        if key.endswith(".scale_weight") or key == "scaled_fp8":
            continue
        if tensor.dtype == torch.float8_e4m3fn:
            scale_key = key.rsplit(".", 1)[0] + ".scale_weight"
            scale = scales.get(scale_key)
            value = tensor.to(torch.float32)
            if scale is not None:
                value = value * scale.to(torch.float32)
            out[key] = value.to(dtype)
        else:
            out[key] = tensor.to(dtype)
    return out


def load_text_encoder(path: Path, dtype):
    """Load a UMT5EncoderModel from a HF directory or a single safetensors file
    (including ComfyUI fp8-scaled files, which are dequantized on the fly)."""
    from transformers import UMT5Config, UMT5EncoderModel

    if path.is_dir():
        try:
            return UMT5EncoderModel.from_pretrained(str(path), dtype=dtype)
        except TypeError:  # transformers < 5 uses torch_dtype
            return UMT5EncoderModel.from_pretrained(str(path), torch_dtype=dtype)

    import torch
    from safetensors.torch import load_file

    logger.info("Loading UMT5 text encoder from single file: %s", path)
    state_dict = load_file(str(path))
    state_dict = _dequantize_fp8_state_dict(state_dict, dtype)

    # The ComfyUI repackaged files use HF UMT5 key names, sometimes without the
    # tied encoder.embed_tokens copy.
    if "shared.weight" in state_dict and "encoder.embed_tokens.weight" not in state_dict:
        state_dict["encoder.embed_tokens.weight"] = state_dict["shared.weight"]

    config = UMT5Config(**UMT5_XXL_CONFIG)
    with torch.device("meta"):
        model = UMT5EncoderModel(config)
    model = model.to_empty(device="cpu")
    missing, unexpected = model.load_state_dict(state_dict, strict=False, assign=True)
    missing = [k for k in missing if not k.endswith("position_ids")]
    if missing:
        raise WanBackendError(
            f"The text encoder file '{path.name}' does not match the UMT5-XXL "
            f"architecture — {len(missing)} tensors are missing "
            f"(first: {missing[:3]}). Use the UMT5-XXL text encoder distributed "
            "for Wan (umt5_xxl_fp16.safetensors / umt5_xxl_fp8_e4m3fn_scaled.safetensors) "
            "or a Hugging Face text_encoder directory."
        )
    if unexpected:
        logger.warning("Text encoder: %d unexpected tensors ignored (first: %s)",
                       len(unexpected), unexpected[:3])
    model = model.to(dtype).eval()
    return model


def load_tokenizer(tokenizer_path: Path | None):
    from transformers import AutoTokenizer

    if tokenizer_path is not None:
        return AutoTokenizer.from_pretrained(str(tokenizer_path))
    try:
        return AutoTokenizer.from_pretrained(UMT5_TOKENIZER_REPO)
    except Exception as exc:  # noqa: BLE001 — network/auth errors must become readable
        raise WanBackendError(
            f"Could not load the UMT5 tokenizer from the Hugging Face Hub "
            f"({UMT5_TOKENIZER_REPO}): {exc}. Either allow network access once "
            "(it is cached afterwards) or set the bundle's tokenizer_path to a "
            "local UMT5 tokenizer directory."
        ) from exc


# Official WanTransformer3DModel configuration of Wan 2.2 TI2V-5B, from
# Wan-AI/Wan2.2-TI2V-5B-Diffusers transformer/config.json. Embedded because
# diffusers' single-file detection only distinguishes the 1.3B and 14B
# variants and would misconfigure the 5B (3072-dim) DiT.
WAN22_TI2V_5B_DIT_CONFIG = {
    "added_kv_proj_dim": None, "attention_head_dim": 128,
    "cross_attn_norm": True, "eps": 1e-06, "ffn_dim": 14336, "freq_dim": 256,
    "image_dim": None, "in_channels": 48, "num_attention_heads": 24,
    "num_layers": 30, "out_channels": 48, "patch_size": [1, 2, 2],
    "pos_embed_seq_len": None, "qk_norm": "rms_norm_across_heads",
    "rope_max_seq_len": 1024, "text_dim": 4096,
}


def _dit_config_overrides(path: Path) -> dict:
    """Config overrides for DiT variants diffusers cannot auto-detect."""
    import json as _json
    import struct

    with open(path, "rb") as fh:
        size = struct.unpack("<Q", fh.read(8))[0]
        header = _json.loads(fh.read(size))
    patch_key = next((k for k in ("patch_embedding.weight",
                                  "model.diffusion_model.patch_embedding.weight")
                      if k in header), None)
    if patch_key is None:
        return {}
    shape = header[patch_key]["shape"]  # [dim, in_channels, pt, ph, pw]
    dim, in_channels = shape[0], shape[1]
    if dim == 3072 and in_channels == 48:  # Wan 2.2 TI2V-5B
        return dict(WAN22_TI2V_5B_DIT_CONFIG)
    if dim == 5120 and in_channels != 16:
        # 14B-class checkpoint with conditioning channels (e.g. i2v 36ch):
        # only override what differs from the default 14B t2v config.
        return {"in_channels": in_channels}
    return {}


def _load_transformer(path: Path, dtype):
    from diffusers import WanTransformer3DModel

    if path.is_dir():
        return WanTransformer3DModel.from_pretrained(str(path), torch_dtype=dtype)
    overrides = _dit_config_overrides(path)
    return WanTransformer3DModel.from_single_file(str(path), torch_dtype=dtype, **overrides)


# Official AutoencoderKLWan configuration of the Wan 2.2 (48-latent-channel)
# VAE, from Wan-AI/Wan2.2-TI2V-5B-Diffusers vae/config.json. Embedded so the
# Wan 2.2 VAE single file can be loaded fully offline (diffusers' built-in
# single-file converter only covers the Wan 2.1 VAE).
WAN22_VAE_CONFIG = {
    "attn_scales": [], "base_dim": 160, "decoder_base_dim": 256,
    "dim_mult": [1, 2, 4, 4], "dropout": 0.0, "in_channels": 12,
    "is_residual": True, "num_res_blocks": 2, "out_channels": 12,
    "patch_size": 2, "scale_factor_spatial": 16, "scale_factor_temporal": 4,
    "temperal_downsample": [False, True, True], "z_dim": 48,
    "latents_mean": [-0.2289, -0.0052, -0.1323, -0.2339, -0.2799, 0.0174,
                     0.1838, 0.1557, -0.1382, 0.0542, 0.2813, 0.0891, 0.157,
                     -0.0098, 0.0375, -0.1825, -0.2246, -0.1207, -0.0698,
                     0.5109, 0.2665, -0.2108, -0.2158, 0.2502, -0.2055,
                     -0.0322, 0.1109, 0.1567, -0.0729, 0.0899, -0.2799,
                     -0.123, -0.0313, -0.1649, 0.0117, 0.0723, -0.2839,
                     -0.2083, -0.052, 0.3748, 0.0152, 0.1957, 0.1433,
                     -0.2944, 0.3573, -0.0548, -0.1681, -0.0667],
    "latents_std": [0.4765, 1.0364, 0.4514, 1.1677, 0.5313, 0.499, 0.4818,
                    0.5013, 0.8158, 1.0344, 0.5894, 1.0901, 0.6885, 0.6165,
                    0.8454, 0.4978, 0.5759, 0.3523, 0.7135, 0.6804, 0.5833,
                    1.4146, 0.8986, 0.5659, 0.7069, 0.5338, 0.4889, 0.4917,
                    0.4069, 0.4999, 0.6866, 0.4093, 0.5709, 0.6065, 0.6415,
                    0.4944, 0.5726, 1.2042, 0.5458, 1.6887, 0.3971, 1.06,
                    0.3943, 0.5537, 0.5444, 0.4089, 0.7468, 0.7744],
}

_RESIDUAL_SUBKEY = {"residual.0.gamma": "norm1.gamma",
                    "residual.2.weight": "conv1.weight",
                    "residual.2.bias": "conv1.bias",
                    "residual.3.gamma": "norm2.gamma",
                    "residual.6.weight": "conv2.weight",
                    "residual.6.bias": "conv2.bias",
                    "shortcut.weight": "conv_shortcut.weight",
                    "shortcut.bias": "conv_shortcut.bias"}


def _convert_wan22_vae_key(key: str) -> str:
    """Original Wan 2.2 VAE checkpoint key -> diffusers AutoencoderKLWan key."""
    import re

    if key.startswith("conv1."):
        return key.replace("conv1.", "quant_conv.")
    if key.startswith("conv2."):
        return key.replace("conv2.", "post_quant_conv.")

    for side in ("encoder", "decoder"):
        key = key.replace(f"{side}.conv1.", f"{side}.conv_in.")
        key = key.replace(f"{side}.head.0.gamma", f"{side}.norm_out.gamma")
        key = key.replace(f"{side}.head.2.", f"{side}.conv_out.")
        # middle: 0/2 = resnets, 1 = attention
        key = key.replace(f"{side}.middle.0.", f"{side}.mid_block.resnets.0.")
        key = key.replace(f"{side}.middle.2.", f"{side}.mid_block.resnets.1.")
        key = key.replace(f"{side}.middle.1.", f"{side}.mid_block.attentions.0.")

    # encoder.downsamples.I.downsamples.J... / decoder.upsamples.I.upsamples.J...
    m = re.match(r"(encoder)\.downsamples\.(\d+)\.downsamples\.(\d+)\.(.+)", key)
    if m is None:
        m = re.match(r"(decoder)\.upsamples\.(\d+)\.upsamples\.(\d+)\.(.+)", key)
    if m is not None:
        side, block, sub, rest = m.group(1), m.group(2), m.group(3), m.group(4)
        prefix = "down_blocks" if side == "encoder" else "up_blocks"
        sampler = "downsampler" if side == "encoder" else "upsampler"
        if rest.startswith(("resample.", "time_conv.")):
            return f"{side}.{prefix}.{block}.{sampler}.{rest}"
        return f"{side}.{prefix}.{block}.resnets.{sub}.{_RESIDUAL_SUBKEY.get(rest, rest)}"

    for old, new in _RESIDUAL_SUBKEY.items():
        if key.endswith(old):
            return key[: -len(old)] + new
    return key


def _load_wan22_vae_single_file(path: Path, dtype):
    import torch
    from diffusers import AutoencoderKLWan
    from safetensors.torch import load_file

    logger.info("Loading Wan 2.2 VAE from single file (built-in converter): %s", path)
    state_dict = {_convert_wan22_vae_key(k): v.to(dtype)
                  for k, v in load_file(str(path)).items()}
    with torch.device("meta"):
        vae = AutoencoderKLWan(**WAN22_VAE_CONFIG)
    vae = vae.to_empty(device="cpu")
    missing, unexpected = vae.load_state_dict(state_dict, strict=False, assign=True)
    if missing or unexpected:
        raise WanBackendError(
            f"The VAE file '{path.name}' does not match the Wan 2.2 VAE architecture "
            f"({len(missing)} missing, {len(unexpected)} unexpected tensors; "
            f"first missing: {missing[:2]}, first unexpected: {unexpected[:2]}). "
            "Use the official wan2.2_vae.safetensors (or a diffusers vae directory)."
        )
    return vae.to(dtype).eval()


def _vae_is_wan22_layout(path: Path) -> bool:
    """Detect the Wan 2.2 VAE by its nested (down|up)samples blocks."""
    import json as _json
    import re
    import struct

    with open(path, "rb") as fh:
        size = struct.unpack("<Q", fh.read(8))[0]
        header = _json.loads(fh.read(size))
    pattern = re.compile(r"(downsamples\.\d+\.downsamples|upsamples\.\d+\.upsamples)\.")
    return any(pattern.search(k) for k in header if k != "__metadata__")


def _load_vae(path: Path, dtype):
    import torch
    from diffusers import AutoencoderKLWan

    # The Wan VAE runs in float32 for quality/stability unless the user forces
    # a lower precision explicitly.
    vae_dtype = torch.float32 if dtype != torch.float32 else dtype
    if path.is_dir():
        return AutoencoderKLWan.from_pretrained(str(path), torch_dtype=vae_dtype)
    if _vae_is_wan22_layout(path):
        # diffusers' from_single_file only converts the Wan 2.1 VAE layout.
        return _load_wan22_vae_single_file(path, vae_dtype)
    return AutoencoderKLWan.from_single_file(str(path), torch_dtype=vae_dtype)


def auto_flow_shift(height: int, is_ti2v_5b: bool) -> float:
    """The model's default flow-matching shift when ModelSamplingSD3 is off.
    Official recipes: TI2V-5B uses 5.0; 14B-class models use 5.0 at >=720p and
    3.0 below. WAN_FLOW_SHIFT>0 overrides."""
    flow_shift = settings.wan_flow_shift
    if flow_shift <= 0:
        flow_shift = 5.0 if (is_ti2v_5b or height >= 720) else 3.0
    return flow_shift


def _flow_shift_for(height: int, is_ti2v_5b: bool,
                    model_sampling_shift: float | None = None) -> float:
    """Resolve the effective flow-matching shift. When ModelSamplingSD3 supplies
    a shift (patch7), it is used exactly — this IS the ComfyUI ModelSamplingSD3
    behavior (diffusers applies sigma = shift*sigma/(1+(shift-1)*sigma), the same
    time_snr_shift the node uses). Otherwise the model's default shift is used."""
    if model_sampling_shift is not None and model_sampling_shift > 0:
        return float(model_sampling_shift)
    return auto_flow_shift(height, is_ti2v_5b)


def _build_scheduler(resolved: ResolvedBundle, height: int, is_ti2v_5b: bool = False,
                     model_sampling_shift: float | None = None):
    """Default scheduler used at pipeline construction (UniPC flow-matching). The
    per-job sampler is applied later by build_direct_scheduler()."""
    from diffusers import UniPCMultistepScheduler

    if resolved.scheduler_config_path is not None and model_sampling_shift is None:
        # An explicit scheduler config file wins only when ModelSamplingSD3 is
        # not overriding the shift; otherwise the requested shift must take effect.
        config = json.loads(resolved.scheduler_config_path.read_text(encoding="utf-8"))
        return UniPCMultistepScheduler.from_config(config)
    flow_shift = _flow_shift_for(height, is_ti2v_5b, model_sampling_shift)
    return UniPCMultistepScheduler(
        prediction_type="flow_prediction", use_flow_sigmas=True, flow_shift=flow_shift,
        num_train_timesteps=1000, solver_order=2,
    )


# patch6c §7 — the real sampler→scheduler mapping. Every name in
# sampler_registry.DIRECT_BACKEND_SAMPLERS is handled here with an actual
# diffusers flow-matching scheduler; anything else raises so the direct backend
# never pretends to support a sampler it cannot run.
def build_direct_scheduler(sampler_name: str, resolved: ResolvedBundle,
                           height: int, is_ti2v_5b: bool,
                           model_sampling_shift: float | None = None):
    """Return (scheduler, effective_sampler, effective_scheduler_label) for the
    requested sampler, using a genuine flow-matching scheduler class. Wan2.2 is a
    flow-matching model, so only flow-matching solvers are valid (patch6c §7).

    When `model_sampling_shift` is given, it sets the flow-matching shift on the
    scheduler — the real ModelSamplingSD3 behavior (patch7)."""
    from app.services import sampler_registry

    canonical = sampler_registry.normalize_sampler(sampler_name) or sampler_registry.DEFAULT_DIRECT_SAMPLER
    flow_shift = _flow_shift_for(height, is_ti2v_5b, model_sampling_shift)

    if canonical == "uni_pc":
        return (_build_scheduler(resolved, height, is_ti2v_5b, model_sampling_shift),
                "uni_pc", "flow-matching (UniPC)")

    if canonical == "euler":
        from diffusers import FlowMatchEulerDiscreteScheduler

        return (FlowMatchEulerDiscreteScheduler(num_train_timesteps=1000, shift=flow_shift),
                "euler", "flow-matching (Euler)")

    if canonical == "heun":
        from diffusers import FlowMatchHeunDiscreteScheduler

        return (FlowMatchHeunDiscreteScheduler(num_train_timesteps=1000, shift=flow_shift),
                "heun", "flow-matching (Heun)")

    if canonical in ("dpmpp_2m", "dpmpp_2m_sde"):
        from diffusers import DPMSolverMultistepScheduler

        algorithm = "sde-dpmsolver++" if canonical == "dpmpp_2m_sde" else "dpmsolver++"
        label = "flow-matching (DPM++ 2M SDE)" if canonical == "dpmpp_2m_sde" else "flow-matching (DPM++ 2M)"
        return (DPMSolverMultistepScheduler(
                    prediction_type="flow_prediction", use_flow_sigmas=True,
                    flow_shift=flow_shift, num_train_timesteps=1000, solver_order=2,
                    algorithm_type=algorithm),
                canonical, label)

    raise WanBackendError(
        f'The direct backend cannot build a real flow-matching scheduler for sampler '
        f'"{canonical}". Supported direct-backend samplers: '
        + ", ".join(sorted(sampler_registry.DIRECT_BACKEND_SAMPLERS)) + ".")


# --------------------------------------------------------------------------
# Pipeline cache
# --------------------------------------------------------------------------

class _PipelineCache:
    """Holds at most one loaded Wan pipeline (they are huge)."""

    def __init__(self) -> None:
        self.key: tuple | None = None
        self.pipeline = None
        self.lock = threading.Lock()

    def clear(self) -> None:
        if self.pipeline is not None:
            logger.info("Unloading cached Wan pipeline")
            self.pipeline = None
            self.key = None
            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass


_cache = _PipelineCache()


def unload_pipeline() -> None:
    with _cache.lock:
        _cache.clear()


def _vram_allocated_mb() -> float | None:
    """Currently allocated CUDA memory in MB, or None when CUDA is unavailable."""
    try:
        import torch

        if torch.cuda.is_available():
            return round(torch.cuda.memory_allocated() / (1024 ** 2), 1)
    except ImportError:
        pass
    return None


def release_generation_memory() -> None:
    """Free transient generation VRAM (temporary latents/conditioning tensors)
    without unloading model weights (patch6 §16). Safe to call any time."""
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            if hasattr(torch.cuda, "ipc_collect"):
                torch.cuda.ipc_collect()
    except ImportError:
        pass


def _apply_memory_options(pipe, device: str, offload_mode: str) -> None:
    offload = (offload_mode or "").lower()
    if device == "cpu":
        return
    if offload in ("model", "auto", ""):
        pipe.enable_model_cpu_offload()
    elif offload == "sequential":
        pipe.enable_sequential_cpu_offload()
    elif offload == "none":
        pipe.to(device)
    else:
        raise WanBackendError(f"Unknown offload mode '{offload_mode}'. "
                              "Use model, sequential or none (or WAN_OFFLOAD_POLICY "
                              "balanced/aggressive/disabled).")
    if settings.wan_attention_slicing:
        pipe.enable_attention_slicing()
    if settings.enable_memory_optimization:
        try:
            pipe.vae.enable_tiling()
        except AttributeError:
            pass


def _check_vram(resolved: ResolvedBundle, device: str, offload_mode: str) -> None:
    """Best-effort memory sanity check before loading multi-GB weights."""
    if device == "cpu" or resolved.transformer_path is None:
        return
    import torch

    if not torch.cuda.is_available():
        return
    total = torch.cuda.get_device_properties(0).total_memory
    dit_bytes = resolved.transformer_path.stat().st_size
    if offload_mode.lower() == "none" and dit_bytes > total:
        raise WanBackendError(
            f"The diffusion model ({dit_bytes / 1024**3:.1f} GB) does not fit in GPU "
            f"memory ({total / 1024**3:.1f} GB) without offloading. Set "
            "WAN_OFFLOAD_POLICY=balanced (default) or =aggressive in .env."
        )
    if dit_bytes > total:
        logger.info("Model larger than VRAM (%.1f GB > %.1f GB) — CPU offload will be used.",
                    dit_bytes / 1024**3, total / 1024**3)


def _load_pipeline(resolved: ResolvedBundle, device: str, dtype, height: int,
                   offload_mode: str, progress: ProgressCallback):
    """Construct the real WanPipeline / WanImageToVideoPipeline."""
    import diffusers

    is_i2v = resolved.mode == "image2video"
    pipe_cls = diffusers.WanImageToVideoPipeline if is_i2v else diffusers.WanPipeline

    if resolved.pipeline_dir is not None:
        progress(12, f"Loading Wan pipeline from {resolved.pipeline_dir.name}")
        pipe = pipe_cls.from_pretrained(str(resolved.pipeline_dir), torch_dtype=dtype)
    else:
        progress(10, "Loading UMT5 text encoder")
        text_encoder = load_text_encoder(resolved.text_encoder_path, dtype)
        tokenizer = load_tokenizer(resolved.tokenizer_path)

        progress(18, "Loading Wan diffusion model (DiT)")
        transformer = _load_transformer(resolved.transformer_path, dtype)
        transformer_2 = None
        if resolved.transformer_2_path is not None:
            progress(24, "Loading second diffusion model (low-noise expert)")
            transformer_2 = _load_transformer(resolved.transformer_2_path, dtype)

        progress(27, "Loading Wan VAE")
        vae = _load_vae(resolved.vae_path, dtype)
        is_ti2v_5b = getattr(transformer.config, "in_channels", 0) == 48
        scheduler = _build_scheduler(resolved, height, is_ti2v_5b)

        kwargs: dict[str, Any] = dict(tokenizer=tokenizer, text_encoder=text_encoder,
                                      transformer=transformer, vae=vae, scheduler=scheduler)
        if is_i2v:
            # Wan 2.2 pipelines need no CLIP image encoder. TI2V-5B-style
            # checkpoints (DiT input channels == VAE latent channels, i.e. no
            # dedicated conditioning channels) require expand_timesteps mode.
            expand = (getattr(transformer.config, "in_channels", None)
                      == getattr(vae.config, "z_dim", None))
            kwargs.update(image_encoder=None, image_processor=None,
                          expand_timesteps=bool(expand))
        if transformer_2 is not None:
            kwargs.update(transformer_2=transformer_2,
                          boundary_ratio=settings.wan_boundary_ratio)
        try:
            pipe = pipe_cls(**kwargs)
        except TypeError as exc:
            raise WanBackendError(
                f"Your diffusers version does not accept the Wan pipeline components "
                f"used by this bundle ({exc}). Upgrade with: pip install -U diffusers"
            ) from exc

    for lora in resolved.lora_paths:
        progress(29, f"Loading LoRA {lora.name}")
        try:
            pipe.load_lora_weights(str(lora))
        except Exception as exc:  # noqa: BLE001 — LoRA errors must be readable
            raise WanBackendError(f"Failed to load LoRA '{lora}': {exc}") from exc

    _apply_memory_options(pipe, device, offload_mode)
    return pipe


def _get_pipeline(resolved: ResolvedBundle, device: str, dtype, height: int,
                  offload_mode: str, progress: ProgressCallback) -> tuple[Any, bool]:
    """Return (pipeline, reused). The cache key includes the offload mode so a
    preset that changes offload forces an honest reload rather than reusing a
    pipeline with the wrong placement hooks."""
    key = resolved.cache_key() + (device, str(dtype), offload_mode)
    if _cache.key == key and _cache.pipeline is not None:
        progress(28, "Reusing loaded Wan pipeline")
        return _cache.pipeline, True
    _cache.clear()
    pipe = _load_pipeline(resolved, device, dtype, height, offload_mode, progress)
    _cache.key = key
    _cache.pipeline = pipe
    return pipe, False


# --------------------------------------------------------------------------
# Video encoding
# --------------------------------------------------------------------------

def encode_video(frames, fps: int, output_path: Path) -> None:
    """Encode uint8 RGB frames (numpy array [f,h,w,3]) to mp4/webm."""
    import imageio_ffmpeg
    import numpy as np

    frames = np.asarray(frames)
    if frames.dtype != np.uint8:
        frames = (np.clip(frames, 0.0, 1.0) * 255).round().astype(np.uint8)
    f, h, w = frames.shape[0], frames.shape[1], frames.shape[2]
    fmt = output_path.suffix.lstrip(".").lower()
    codec = "libvpx-vp9" if fmt == "webm" else "libx264"
    extra = ["-b:v", "2M"] if fmt == "webm" else ["-crf", "18", "-preset", "medium"]
    writer = imageio_ffmpeg.write_frames(
        str(output_path), (w, h), fps=max(fps, 1), codec=codec,
        pix_fmt_in="rgb24", output_params=extra,
    )
    writer.send(None)
    try:
        for i in range(f):
            writer.send(np.ascontiguousarray(frames[i]).tobytes())
    finally:
        writer.close()
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise WanBackendError(
            f"Video encoding produced no output file ({output_path.name}). "
            "Check that the bundled ffmpeg (imageio-ffmpeg) is working."
        )


# --------------------------------------------------------------------------
# Diagnostics collection
# --------------------------------------------------------------------------

def _collect_component_maps(pipe, offload_mode: str) -> tuple[dict[str, str], dict[str, str]]:
    """Effective device and dtype for every loaded pipeline component."""
    components = {
        "diffusion_model": getattr(pipe, "transformer", None),
        "diffusion_model_2": getattr(pipe, "transformer_2", None),
        "text_encoder": getattr(pipe, "text_encoder", None),
        "vae": getattr(pipe, "vae", None),
        "vision_encoder": getattr(pipe, "image_encoder", None),
    }
    device_map: dict[str, str] = {}
    dtype_map: dict[str, str] = {}
    for name, comp in components.items():
        if comp is None:
            continue
        device_map[name] = _effective_component_device(comp, offload_mode)
        _, dt = _param_device_dtype(comp)
        dtype_map[name] = dtype_to_precision_name(dt) if dt is not None else "n/a"
    return device_map, dtype_map


def _precision_warnings(resolved: ResolvedBundle, requested_precision: str,
                        dtype_map: dict[str, str]) -> list[str]:
    """Honest requested-vs-effective precision notes (patch6 §7)."""
    warnings: list[str] = []
    eff = effective_compute_precision()
    req = normalize_precision_name(requested_precision) or normalize_precision_name(settings.wan_default_precision)

    if req in ("fp8", "fp8_e4m3fn", "fp8_e5m2"):
        warnings.append(
            f"Requested FP8 diffusion precision ('{req}'), but the direct diffusers "
            f"backend has no FP8 inference kernel — weights are dequantized to {eff} "
            "for compute. Effective precision is not FP8."
        )
    elif req and req != eff and settings.wan_warn_on_dtype_fallback:
        warnings.append(
            f"Requested precision '{req}', but the effective compute precision is "
            f"'{eff}' (WAN_TORCH_DTYPE). Set WAN_TORCH_DTYPE to change it."
        )

    if req == "bf16" and not settings.wan_allow_bf16:
        warnings.append("bf16 is disabled by WAN_ALLOW_BF16=false.")
    if req == "fp16" and not settings.wan_allow_fp16:
        warnings.append("fp16 is disabled by WAN_ALLOW_FP16=false.")
    if req in ("fp8", "fp8_e4m3fn", "fp8_e5m2") and not settings.wan_allow_fp8:
        warnings.append("FP8 checkpoints are disabled by WAN_ALLOW_FP8=false.")

    # Detect fp8-on-disk components (stored fp8, dequantized on load).
    for label, path in (("diffusion model", resolved.transformer_path),
                        ("text encoder", resolved.text_encoder_path)):
        if path is not None and "fp8" in path.name.lower():
            warnings.append(
                f"The {label} file is stored in FP8 on disk ('{path.name}') and is "
                f"dequantized to {eff} on load — this is expected and matches ComfyUI's "
                "fp8-scaled behavior, but the compute is not done in FP8."
            )

    vae_dtype = dtype_map.get("vae")
    if vae_dtype and vae_dtype != eff:
        warnings.append(
            f"The VAE runs in {vae_dtype} for decode stability/quality, independent of "
            f"the {eff} compute precision (this matches Wan's reference recipe)."
        )
    return warnings


def _device_warnings(device: str, device_map: dict[str, str]) -> list[str]:
    """Warn when the text encoder / VAE ended up purely on CPU (patch6 §6)."""
    warnings: list[str] = []
    te = device_map.get("text_encoder", "")
    vae = device_map.get("vae", "")
    if settings.wan_warn_if_text_encoder_on_cpu and te.startswith("cpu"):
        warnings.append(
            "The text encoder is running on CPU — this makes prompt encoding much "
            "slower than ComfyUI on GPU. Free VRAM or set WAN_TEXT_ENCODER_DEVICE=cuda."
        )
    if settings.wan_warn_if_vae_on_cpu and vae.startswith("cpu"):
        warnings.append(
            "The VAE is running on CPU — frame decoding will be slow. Free VRAM or set "
            "WAN_VAE_DEVICE=cuda."
        )
    if device == "cpu":
        warnings.append(
            "The entire pipeline is running on CPU — generation will be extremely slow. "
            "This is NOT comparable to ComfyUI on GPU."
        )
    return warnings


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def preflight(bundle: dict, mode: str, width: int, height: int,
              frames: int, fps: int) -> ResolvedBundle:
    """All §10 runtime checks. Raises WanBackendError with the exact problem."""
    _require_dependencies()
    resolved = resolve_bundle(bundle, mode)
    validate_generation_params(width, height, frames, fps)
    device = resolve_device()
    _check_vram(resolved, device, settings.effective_offload_mode())
    return resolved


def backend_status() -> dict[str, Any]:
    """Non-throwing readiness summary for the UI / status API."""
    deps = missing_dependencies()
    status: dict[str, Any] = {
        "backend": "wan",
        "ready": not deps,
        "missing_dependencies": deps,
        **device_info(),
        "offload_mode": settings.effective_offload_mode(),
        "offload_policy": settings.effective_offload_policy(),
        "torch_dtype": settings.wan_torch_dtype,
        "compute_precision": normalize_precision_name(settings.wan_torch_dtype),
        "keep_model_warm": settings.wan_keep_model_warm,
        "allow_hf_download": settings.wan_allow_hf_download,
    }
    if not deps:
        try:
            status["device"] = resolve_device()
        except WanBackendError as exc:
            status["ready"] = False
            status["device_error"] = str(exc)
    return status


def generate_video(
    *,
    bundle: dict,
    mode: str,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    frames: int,
    fps: int,
    steps: int,
    guidance_scale: float,
    seed: int,
    output_path: Path,
    preview_path: Path,
    source_image: Path | None = None,
    sampler_name: str = "",
    effective_sampler: str = "",
    scheduler: str = "",
    denoise: float = 1.0,
    model_sampling_enabled: bool = False,
    model_sampling_type: str = "sd3",
    model_sampling_shift: float = 8.0,
    requested_precision: str = "",
    offload_mode: str | None = None,
    resize_mode: str = "cover",
    extra_warnings: list[str] | None = None,
    should_cancel: Callable[[], bool] = lambda: False,
    progress: ProgressCallback = lambda pct, stage: None,
) -> GenerationReport:
    """Run real Wan inference and write the video + thumbnail.

    Returns a GenerationReport describing exactly what the backend did:
    effective settings, per-component device/dtype, timings, VRAM usage, the
    final prompts and every fallback warning (patch6 §4/§5/§14/§15).
    Raises WanBackendError on any failure — no fallback output is ever written.
    """
    progress(2, "Validating model bundle and runtime")
    resolved = preflight(bundle, mode, width, height, frames, fps)
    device = resolve_device()
    dtype = torch_dtype()
    offload_mode = (offload_mode or settings.effective_offload_mode()).lower()

    def _ck() -> None:
        """Raise if the user requested cancellation (patch6b §6)."""
        if should_cancel():
            raise WanGenerationCancelled()

    _ck()  # before model loading

    report = GenerationReport()
    report.final_positive_prompt = prompt
    report.final_negative_prompt = negative_prompt or ""
    report.offload_policy = settings._OFFLOAD_MODE_TO_POLICY.get(offload_mode, offload_mode)
    report.warnings.extend(extra_warnings or [])
    timings: dict[str, float] = {k: 0.0 for k in (
        "model_loading_seconds", "text_encoding_seconds", "image_encoding_seconds",
        "sampling_seconds", "vae_decode_seconds", "video_write_seconds", "total_seconds")}

    import torch
    from PIL import Image

    report.gpu_memory["before_mb"] = _vram_allocated_mb() or 0.0

    image = None
    if mode == "image2video":
        if source_image is None or not source_image.exists():
            raise WanBackendError("Image2Video requires a source image, but none was found.")
        try:
            image = Image.open(source_image).convert("RGB")
        except OSError as exc:
            raise WanBackendError(f"The source image could not be read: {exc}") from exc
        src_w, src_h = image.width, image.height
        # Deterministic cover-crop to the requested output resolution.
        scale = max(width / image.width, height / image.height)
        image = image.resize((round(image.width * scale), round(image.height * scale)),
                             Image.LANCZOS)
        left = (image.width - width) // 2
        top = (image.height - height) // 2
        image = image.crop((left, top, left + width, top + height))
        report.image_preprocessing = {
            "source_width": src_w, "source_height": src_h,
            "target_width": width, "target_height": height,
            "resize_mode": resize_mode,
            "crop_mode": "center",
            "aspect_ratio_mode": "preserve (cover-crop)",
            "effective_conditioning_size": f"{width}x{height}",
            "resampling": "LANCZOS",
            "note": "The source is scaled to cover the target and center-cropped. "
                    "ComfyUI's WanImageToVideo node may pad/letterbox instead — if "
                    "your ComfyUI result differs, match the crop/pad mode there.",
        }

    started = time.perf_counter()
    with _cache.lock:
        t0 = time.perf_counter()
        pipe, reused = _get_pipeline(resolved, device, dtype, height, offload_mode, progress)
        timings["model_loading_seconds"] = round(time.perf_counter() - t0, 3)
        report.reused_pipeline = reused
        _ck()  # after model loading, before encoding/sampling

        # Exact resolution requirement depends on the loaded model:
        # VAE spatial scale × DiT patch size (e.g. 32 for TI2V-5B, 16 for 14B).
        vae_scale = getattr(pipe, "vae_scale_factor_spatial", None) or 8
        patch = getattr(pipe.transformer.config, "patch_size", [1, 2, 2])
        req = int(vae_scale) * int(patch[-1])
        if width % req or height % req:
            raise WanBackendError(
                f"This model requires width and height to be multiples of {req} "
                f"(got {width}x{height}). Try {width - width % req}x{height - height % req}."
            )

        # patch6c §7 — apply the REAL sampler the user selected. The scheduler is
        # swapped on the (possibly cached) pipeline, so the requested sampler is
        # actually used for denoising instead of always UniPC. Swapping the
        # scheduler is cheap and stateless until set_timesteps runs inside pipe().
        # patch7 — when ModelSamplingSD3 is enabled, its shift is applied here as
        # the scheduler's flow-matching shift (the exact ComfyUI behavior), BEFORE
        # sampling starts. This is a real model-preparation stage, not cosmetic.
        is_ti2v_5b = getattr(pipe.transformer.config, "in_channels", 0) == 48
        ms_shift = float(model_sampling_shift) if model_sampling_enabled else None
        auto_shift = auto_flow_shift(height, is_ti2v_5b)
        eff_shift = _flow_shift_for(height, is_ti2v_5b, ms_shift)
        wanted_sampler = effective_sampler or sampler_name or "uni_pc"
        pipe.scheduler, eff_sampler_name, eff_scheduler_label = build_direct_scheduler(
            wanted_sampler, resolved, height, is_ti2v_5b, ms_shift)
        report.model_sampling = {
            "requested": {"enabled": bool(model_sampling_enabled),
                          "type": model_sampling_type if model_sampling_enabled else None,
                          "shift": float(model_sampling_shift) if model_sampling_enabled else None},
            "effective": {"enabled": bool(model_sampling_enabled),
                          "type": model_sampling_type if model_sampling_enabled else None,
                          "shift": eff_shift if model_sampling_enabled else None},
            "applied": bool(model_sampling_enabled),
            "applied_before_sampling": bool(model_sampling_enabled),
            "backend": "direct",
            "auto_flow_shift": auto_shift,
            "effective_flow_shift": eff_shift,
            "warnings": [],
        }
        logger.info("Direct backend sampler: requested=%s effective=%s (%s); "
                    "ModelSamplingSD3 enabled=%s shift=%.3f (auto=%.3f)",
                    sampler_name or "(default)", eff_sampler_name, eff_scheduler_label,
                    model_sampling_enabled, eff_shift, auto_shift)

        # Effective device/dtype maps + honest precision/device warnings.
        report.device_map, report.dtype_map = _collect_component_maps(pipe, offload_mode)
        report.warnings.extend(_precision_warnings(resolved, requested_precision, report.dtype_map))
        report.warnings.extend(_device_warnings(device, report.device_map))
        report.gpu_memory["after_load_mb"] = _vram_allocated_mb() or 0.0
        if torch.cuda.is_available():
            try:
                torch.cuda.reset_peak_memory_stats()
            except Exception:  # noqa: BLE001 — peak stats are best-effort
                pass

        progress(30, "Pipeline ready — encoding prompts")

        generator = torch.Generator(device="cpu").manual_seed(seed)

        # Phase timing is derived from the denoise-step callback: everything
        # before the first step is prompt/image encoding + latent prep; the last
        # step to pipeline return is the VAE decode.
        phase = {"call_start": 0.0, "first_step": 0.0, "last_step": 0.0}

        def on_step_end(pipeline, step_index, timestep, callback_kwargs):
            # Real mid-sampling cancellation: raising here stops the denoise loop
            # immediately (patch6b §6/§7).
            _ck()
            now = time.perf_counter()
            if step_index == 0:
                phase["first_step"] = now
            phase["last_step"] = now
            pct = 32 + int((step_index + 1) / max(steps, 1) * 53)  # 32 → 85
            progress(min(pct, 85), f"Denoising — step {step_index + 1}/{steps}")
            return callback_kwargs

        call_kwargs: dict[str, Any] = dict(
            prompt=prompt,
            negative_prompt=negative_prompt or None,
            height=height,
            width=width,
            num_frames=frames,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
            generator=generator,
            output_type="np",
            callback_on_step_end=on_step_end,
        )
        if image is not None:
            call_kwargs["image"] = image

        logger.info("Wan inference starting: %s %dx%d f=%d steps=%d seed=%d device=%s",
                    mode, width, height, frames, steps, seed, device)
        phase["call_start"] = time.perf_counter()
        try:
            result = pipe(**call_kwargs)
        except torch.cuda.OutOfMemoryError as exc:
            _cache.clear()  # lock is already held here — do not use unload_pipeline()
            raise WanBackendError(
                "The GPU ran out of memory during inference. Lower the resolution or "
                "frame count, or set WAN_OFFLOAD_POLICY=aggressive in .env."
            ) from exc
        except WanGenerationCancelled:
            # User cancelled mid-sampling — propagate as cancellation, not failure.
            release_generation_memory()
            raise
        except WanBackendError:
            raise
        except Exception as exc:  # noqa: BLE001 — inference errors must reach the user
            raise WanBackendError(f"Wan inference failed: {exc}") from exc
        call_end = time.perf_counter()
        _ck()  # after sampling, before VAE decode / video writing

        if phase["first_step"] and phase["last_step"]:
            encode_secs = round(phase["first_step"] - phase["call_start"], 3)
            timings["sampling_seconds"] = round(phase["last_step"] - phase["first_step"], 3)
            timings["vae_decode_seconds"] = round(call_end - phase["last_step"], 3)
            if image is not None:
                timings["image_encoding_seconds"] = encode_secs
            else:
                timings["text_encoding_seconds"] = encode_secs
        else:
            timings["sampling_seconds"] = round(call_end - phase["call_start"], 3)

        if torch.cuda.is_available():
            try:
                report.gpu_memory["peak_mb"] = round(torch.cuda.max_memory_allocated() / (1024 ** 2), 1)
            except Exception:  # noqa: BLE001
                pass

        video_frames = result.frames[0]

    progress(88, "Decoding complete — encoding video")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    t_write = time.perf_counter()
    try:
        encode_video(video_frames, fps, output_path)
    except WanBackendError:
        raise
    except Exception as exc:  # noqa: BLE001 — encoder errors must reach the user
        raise WanBackendError(f"Video encoding failed: {exc}") from exc
    timings["video_write_seconds"] = round(time.perf_counter() - t_write, 3)

    progress(94, "Writing preview thumbnail")
    import numpy as np

    first = np.asarray(video_frames[0])
    if first.dtype != np.uint8:
        first = (np.clip(first, 0.0, 1.0) * 255).round().astype(np.uint8)
    Image.fromarray(first).save(preview_path, quality=90)

    # Free transient VRAM (and optionally unload weights) after the run.
    del video_frames, result
    if settings.wan_unload_model_after_generation or not settings.wan_keep_model_warm:
        unload_pipeline()
    if settings.wan_clear_temp_vram_after_generation:
        release_generation_memory()
    report.gpu_memory["after_cleanup_mb"] = _vram_allocated_mb() or 0.0

    timings["total_seconds"] = round(time.perf_counter() - started, 3)
    report.timings = timings
    report.duration_seconds = frames / max(fps, 1)

    # Effective settings block — what the direct backend actually applied. The
    # sampler is now the real one the user selected (patch6c §7); denoise is still
    # full (the diffusers Wan pipeline denoises from pure noise). Requested values
    # are reported alongside so any difference is always visible.
    report.effective = {
        "mode": mode,
        "model_bundle_id": bundle.get("model_bundle_id", ""),
        "prompt": prompt,
        "negative_prompt": negative_prompt or "",
        "seed": seed,
        "steps": steps,
        "cfg": guidance_scale,
        "guidance_scale": guidance_scale,
        "sampler_name": eff_sampler_name,
        "scheduler": eff_scheduler_label,
        "denoise": 1.0,
        "resolution": f"{width}x{height}",
        "frames": frames,
        "fps": fps,
        "precision": effective_compute_precision(),
        "offload_policy": report.offload_policy,
        "requested_sampler_name": sampler_name,
        "requested_scheduler": scheduler,
        "requested_denoise": denoise,
        "model_sampling_sd3": bool(model_sampling_enabled),
        "model_sampling_shift": eff_shift if model_sampling_enabled else None,
        "flow_shift": eff_shift,
    }

    logger.info("Wan inference finished in %.1fs -> %s (warnings: %d)",
                timings["total_seconds"], output_path.name, len(report.warnings))
    return report
