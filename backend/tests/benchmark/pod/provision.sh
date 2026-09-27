#!/usr/bin/env bash
# Runs on the rented pod. Idempotent: re-running after new code only installs what is missing.
# Everything lives on /workspace, the pod's persistent volume, so a stopped pod keeps its results.
set -uo pipefail
ROOT=/workspace/orb
LOG=$ROOT/pod-provision.log
exec >>"$LOG" 2>&1
log() { echo "[provision $(date '+%F %T')] $*"; }
fail() { log "FAILED: $*"; echo "failed: $*" > $ROOT/pod-state; exit 1; }
echo provisioning > $ROOT/pod-state
cd $ROOT/backend || fail "no code at $ROOT/backend"

log "gpu: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo none)"
command -v nvcc >/dev/null || export PATH=/usr/local/cuda/bin:$PATH
command -v nvcc >/dev/null || fail "no nvcc in this image: llama-cpp-python cannot be built for CUDA"
export DEBIAN_FRONTEND=noninteractive
command -v cmake >/dev/null && command -v rsync >/dev/null || { apt-get update -qq && apt-get install -y -qq cmake rsync build-essential >/dev/null; } || fail "apt-get"

[ -x .venv/bin/python ] || python3 -m venv .venv || fail "venv"
.venv/bin/pip install -q -U pip wheel || fail "pip upgrade"
if ! .venv/bin/python -c "import llama_cpp, sys; sys.exit(0 if llama_cpp.llama_supports_gpu_offload() else 1)" 2>/dev/null; then
  log "building llama-cpp-python ${LLAMA_CPP_VERSION} for CUDA (about 10-20 minutes)"
  CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=native" CMAKE_BUILD_PARALLEL_LEVEL=$(nproc) \
    .venv/bin/pip install -q --no-cache-dir "llama-cpp-python==${LLAMA_CPP_VERSION}" || fail "llama-cpp-python CUDA build"
fi
.venv/bin/python -c "import llama_cpp, sys; sys.exit(0 if llama_cpp.llama_supports_gpu_offload() else 1)" || fail "llama-cpp-python has no GPU offload"
.venv/bin/pip install -q -r requirements.txt -r requirements-dev.txt httpx tqdm || fail "requirements"
.venv/bin/python tests/benchmark/fetch_notes.py || fail "fetch_notes"

# The watchdog holds no API key: it ends the container, which stops the GPU charge and keeps /workspace.
if ! pgrep -f orb-watchdog >/dev/null; then
  cat > /usr/local/bin/orb-watchdog <<'WATCHDOG'
#!/usr/bin/env bash
ROOT=/workspace/orb
HARD=$(( ${ORB_MAX_HOURS:-48} * 3600 )); IDLE=$(( ${ORB_IDLE_HOURS:-3} * 3600 ))
START=$(date +%s); BUSY=$START
while true; do
  sleep 60; NOW=$(date +%s)
  [ -f $ROOT/Results/queue.pid ] && kill -0 "$(cat $ROOT/Results/queue.pid)" 2>/dev/null && BUSY=$NOW
  if [ $((NOW - START)) -gt $HARD ]; then echo "[watchdog $(date '+%F %T')] ${ORB_MAX_HOURS:-48} h cap reached, stopping"; break; fi
  if [ $((NOW - BUSY)) -gt $IDLE ]; then echo "[watchdog $(date '+%F %T')] idle ${ORB_IDLE_HOURS:-3} h, stopping"; break; fi
done
pkill -INT -f "tests/benchmark/" ; sleep 30; pkill -f "run.py|data/bin/" ; sync
kill -TERM 1
WATCHDOG
  chmod +x /usr/local/bin/orb-watchdog
  nohup setsid /usr/local/bin/orb-watchdog >> $ROOT/pod-watchdog.log 2>&1 < /dev/null &
fi
log "ready"
echo ready > $ROOT/pod-state
