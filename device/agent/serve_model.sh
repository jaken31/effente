#!/bin/sh
# Start the model server the agent's local mode asks (llama.cpp's llama-server on
# 127.0.0.1:8081), with the settings that were measured to fit the agent's
# 120 second timeout. See "A real run" in README.md for the measurements.
#
#   device/agent/serve_model.sh           # small.gguf
#   device/agent/serve_model.sh large     # large.gguf
#
# The model files are read from ~/effente-models (or EFFENTE_MODEL_FOLDER).
# The thinking budget matters: with unlimited thinking both models ran past the
# timeout, and with thinking off their reports failed the checks.
set -eu

size="${1:-small}"
case "$size" in
  small) budget=600; limit=1200 ;;
  large) budget=400; limit=800 ;;
  *) echo "usage: $0 [small|large]" >&2; exit 2 ;;
esac

model="${EFFENTE_MODEL_FOLDER:-$HOME/effente-models}/$size.gguf"
if [ ! -f "$model" ]; then
  echo "The model file $model is missing." >&2
  exit 1
fi
if ! command -v llama-server >/dev/null 2>&1; then
  echo "llama-server is not installed. On macOS: brew install llama.cpp" >&2
  exit 1
fi

# One slot and a 16k context: the agent sends one request at a time, and its
# prompt was about 1,400 tokens on the test logs. --predict ends an answer that
# runs away (seen once: an answer still going at 120 seconds) before the timeout.
exec llama-server -m "$model" --host 127.0.0.1 --port 8081 --offline \
  --reasoning-budget "$budget" --predict "$limit" --ctx-size 16384 --parallel 1
