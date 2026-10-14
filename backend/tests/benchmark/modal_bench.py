"""Run benchmark sweeps on a Modal GPU instead of a rented pod.

    modal run --detach tests/benchmark/modal_bench.py --specs sweeps/a.json,sweeps/b.json   # from backend/
    modal volume get orb-bench Results/<spec> ../pod-state-modal/Results/                 # results home

The image holds the code and the CUDA builds (llama-cpp-python, llama-server). The volume "orb-bench" holds what
outlives a run: models, the model-call cache, index snapshots and results. It is committed every 10 minutes and at
the end, so a crash loses at most 10 minutes. --detach keeps the run going when this machine loses its network.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

import modal

LLAMA_CPP_PYTHON = "0.3.35"
LLAMA_SERVER_TAG = "v0.5.0"
STUB_LINK = "-DCMAKE_EXE_LINKER_FLAGS=-Wl,-rpath-link,/usr/local/cuda/lib64/stubs -DCMAKE_SHARED_LINKER_FLAGS=-L/usr/local/cuda/lib64/stubs"
CUDA_ARCH = "89"  # Ada: L40S, L4. No GPU at build time, so the architecture is named, not "native".
GPU = os.environ.get("ORB_MODAL_GPU", "L40S")
ROOT = Path("/workspace/orb")  # the harness's REPO: backend/, llama.cpp/, models/, llm-cache/, snapshots/, Results/
KEPT = ("models", "llm-cache", "snapshots", "Results")  # live on the volume
HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
# Read here, where the image is defined; inside the container this file is not at this path.
REQUIREMENTS = [line.strip() for line in (BACKEND / "requirements.txt").read_text().splitlines()
                if line.strip() and not line.startswith("#") and not line.startswith("llama-cpp-python")
                ] if modal.is_local() else []

image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.12")
    .apt_install("git", "cmake", "build-essential", "gcc", "g++", "rsync", "curl")
    .env({"CC": "/usr/bin/gcc", "CXX": "/usr/bin/g++", "CUDACXX": "/usr/local/cuda/bin/nvcc"})
    # Everything but llama-cpp-python, which is built for CUDA below (the plain install would compile it for CPU).
    .pip_install(*REQUIREMENTS, "httpx", "tqdm", "pytest")
    .run_commands(
        # No GPU driver at build time: link against CUDA's stub libcuda (the real one is there when the GPU is).
        "ln -sf /usr/local/cuda/lib64/stubs/libcuda.so /usr/local/cuda/lib64/stubs/libcuda.so.1",
        f'CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES={CUDA_ARCH} {STUB_LINK}" CMAKE_BUILD_PARALLEL_LEVEL=8 '
        f'pip install --no-cache-dir "llama-cpp-python=={LLAMA_CPP_PYTHON}"',
        f"git clone -q --depth 1 --branch {LLAMA_SERVER_TAG} https://github.com/ggml-org/llama.cpp {ROOT}/llama.cpp",
        f"cmake -S {ROOT}/llama.cpp -B {ROOT}/llama.cpp/build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES={CUDA_ARCH} "
        f"-DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF {STUB_LINK}",
        f"cmake --build {ROOT}/llama.cpp/build --target llama-server -j 8",
    )
    .add_local_dir(str(BACKEND), str(ROOT / "backend"),
                   ignore=[".venv", "**/__pycache__", "**/.pytest_cache", "sweeps/../../data*"])
)
volume = modal.Volume.from_name("orb-bench", create_if_missing=True)
app = modal.App("orb-bench", image=image)


@app.function(gpu=GPU, cpu=8.0, memory=49152, volumes={"/vol": volume}, timeout=12 * 3600)
def run_specs(specs: list[str]) -> str:
    """Run each sweep spec in turn, like the pod queue; the log is Results/modal-queue.log on the volume."""
    for name in KEPT:
        (Path("/vol") / name).mkdir(parents=True, exist_ok=True)
        link = ROOT / name
        if not link.exists():
            link.symlink_to(Path("/vol") / name)
    stop = threading.Event()

    def commit_every_10_minutes() -> None:
        while not stop.wait(600):
            volume.commit()

    threading.Thread(target=commit_every_10_minutes, daemon=True).start()
    log = ROOT / "Results" / "modal-queue.log"
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    try:
        with open(log, "a") as out:
            for spec in specs:
                out.write(f"[queue {time.strftime('%F %T')}] start {spec}\n")
                out.flush()
                rc = subprocess.run(["python", "tests/benchmark/sweep.py", spec], cwd=ROOT / "backend",
                                    stdout=out, stderr=subprocess.STDOUT, env=env, check=False).returncode
                out.write(f"[queue {time.strftime('%F %T')}] end {spec} (exit {rc})\n")
                out.flush()
            out.write(f"[queue {time.strftime('%F %T')}] all done\n")
    finally:
        stop.set()
        volume.commit()
    return log.read_text()[-2000:]


@app.function(gpu=GPU, volumes={"/vol": volume}, timeout=1800)
def gpu_check() -> str:
    """The GPU, and whether both CUDA builds can use it."""
    import llama_cpp

    smi = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                         capture_output=True, text=True, check=False).stdout.strip()
    server = subprocess.run([str(ROOT / "llama.cpp/build/bin/llama-server"), "--version"],
                            capture_output=True, text=True, check=False)
    return f"{smi} | llama-cpp-python offload={llama_cpp.llama_supports_gpu_offload()} | {(server.stdout + server.stderr).strip().splitlines()[-1]}"


@app.local_entrypoint()
def main(specs: str = "", check: bool = False):
    if check:
        print(gpu_check.remote())
        return
    print(run_specs.remote([s.strip() for s in specs.split(",") if s.strip()]))
