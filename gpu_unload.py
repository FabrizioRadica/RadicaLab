#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gpu_unload.py

Simple utility to free NVIDIA GPU VRAM by listing GPU processes and,
optionally, terminating selected Python/Uvicorn/ComfyUI/Wan generation processes.

IMPORTANT:
- Python cannot truly "unload the GPU" from another running process.
- VRAM is released when the process that owns it releases tensors or exits.
- This script helps you identify GPU processes and safely terminate them.
- Use --kill only when you are sure you want to stop those processes.

Windows examples:
    python gpu_unload.py
    python gpu_unload.py --kill --only-python
    python gpu_unload.py --kill --pid 12345
    python gpu_unload.py --kill-all-gpu

Linux/macOS examples:
    python3 gpu_unload.py
    python3 gpu_unload.py --kill --only-python
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass


@dataclass
class GpuProcess:
    pid: int
    name: str
    used_memory_mb: int


def run_cmd(args: list[str]) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"Command not found: {args[0]}"


def nvidia_smi_available() -> bool:
    code, _, _ = run_cmd(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    return code == 0


def get_gpu_summary() -> str:
    code, out, err = run_cmd([
        "nvidia-smi",
        "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
        "--format=csv,noheader,nounits",
    ])
    if code != 0:
        return err or "Unable to read GPU summary."

    lines = []
    for row in out.splitlines():
        parts = [p.strip() for p in row.split(",")]
        if len(parts) >= 5:
            idx, name, used, total, util = parts[:5]
            lines.append(f"GPU {idx}: {name} | VRAM {used}/{total} MB | GPU {util}%")
    return "\n".join(lines) if lines else "No GPU information available."


def list_gpu_processes() -> list[GpuProcess]:
    code, out, err = run_cmd([
        "nvidia-smi",
        "--query-compute-apps=pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ])

    if code != 0:
        print(err or "Unable to query GPU processes.", file=sys.stderr)
        return []

    processes: list[GpuProcess] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue

        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue

        try:
            pid = int(parts[0])
            name = parts[1]
            used_memory_mb = int(parts[2])
            processes.append(GpuProcess(pid=pid, name=name, used_memory_mb=used_memory_mb))
        except ValueError:
            continue

    return processes


def is_python_like_process(name: str) -> bool:
    n = name.lower().replace("\\", "/")
    return (
        "python" in n
        or "uvicorn" in n
        or "comfyui" in n
        or "wan" in n
        or n.endswith("/python.exe")
        or n.endswith("/python")
    )


def terminate_process(pid: int, force: bool = False) -> bool:
    try:
        if os.name == "nt":
            if force:
                code, _, err = run_cmd(["taskkill", "/PID", str(pid), "/F"])
            else:
                code, _, err = run_cmd(["taskkill", "/PID", str(pid)])
            if code != 0:
                print(f"Could not terminate PID {pid}: {err}", file=sys.stderr)
                return False
            return True

        sig = signal.SIGKILL if force else signal.SIGTERM
        os.kill(pid, sig)
        return True

    except ProcessLookupError:
        print(f"PID {pid} not found.")
        return True
    except PermissionError:
        print(f"Permission denied for PID {pid}. Run as administrator/root if needed.", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"Could not terminate PID {pid}: {exc}", file=sys.stderr)
        return False


def print_processes(processes: list[GpuProcess]) -> None:
    if not processes:
        print("No active NVIDIA compute processes found.")
        return

    print("GPU processes:")
    print("-" * 78)
    print(f"{'PID':>8}  {'VRAM MB':>8}  PROCESS")
    print("-" * 78)
    for p in processes:
        print(f"{p.pid:>8}  {p.used_memory_mb:>8}  {p.name}")
    print("-" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List and optionally terminate NVIDIA GPU processes to free VRAM."
    )
    parser.add_argument("--kill", action="store_true", help="Enable termination mode.")
    parser.add_argument("--force", action="store_true", help="Force kill instead of graceful terminate.")
    parser.add_argument("--only-python", action="store_true", help="Target only Python/Uvicorn/ComfyUI-like GPU processes.")
    parser.add_argument("--kill-all-gpu", action="store_true", help="Target all listed GPU compute processes.")
    parser.add_argument("--pid", type=int, action="append", help="Specific PID to terminate. Can be used multiple times.")
    parser.add_argument("--yes", action="store_true", help="Do not ask confirmation.")
    parser.add_argument("--wait", type=float, default=2.0, help="Seconds to wait after terminating before rechecking.")
    args = parser.parse_args()

    if not nvidia_smi_available():
        print("nvidia-smi not found or NVIDIA driver not available.")
        print("Install NVIDIA drivers and ensure nvidia-smi is available in PATH.")
        return 1

    print("GPU summary:")
    print(get_gpu_summary())
    print()

    processes = list_gpu_processes()
    print_processes(processes)

    if not args.kill and not args.kill_all_gpu and not args.pid:
        print()
        print("No action taken.")
        print("Use one of these commands to free VRAM:")
        print("  python gpu_unload.py --kill --only-python")
        print("  python gpu_unload.py --kill --pid <PID>")
        print("  python gpu_unload.py --kill-all-gpu")
        return 0

    target_pids: set[int] = set()

    if args.pid:
        target_pids.update(args.pid)

    if args.kill_all_gpu:
        target_pids.update(p.pid for p in processes)

    if args.only_python:
        target_pids.update(p.pid for p in processes if is_python_like_process(p.name))

    if args.kill and not args.pid and not args.kill_all_gpu and not args.only_python:
        print("You used --kill but did not specify what to kill.")
        print("Use --only-python, --pid <PID>, or --kill-all-gpu.")
        return 2

    if not target_pids:
        print("No matching GPU processes selected.")
        return 0

    selected = [p for p in processes if p.pid in target_pids]
    unknown = sorted(target_pids - {p.pid for p in processes})

    print()
    print("Selected processes:")
    print_processes(selected)
    if unknown:
        print(f"Additional PID(s) not currently listed by nvidia-smi: {unknown}")

    if not args.yes:
        answer = input("Terminate selected process(es)? Type YES to continue: ").strip()
        if answer != "YES":
            print("Cancelled.")
            return 0

    ok = True
    for pid in sorted(target_pids):
        print(f"Terminating PID {pid}...")
        if not terminate_process(pid, force=args.force):
            ok = False

    time.sleep(max(0.0, args.wait))

    print()
    print("GPU summary after termination:")
    print(get_gpu_summary())
    print()

    remaining = list_gpu_processes()
    print_processes(remaining)

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
