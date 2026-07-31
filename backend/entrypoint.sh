#!/bin/sh
set -e

if [ ! -f "model.pkl" ]; then
  echo "model.pkl bulunamadı, model eğitiliyor..."
  python train_model.py
fi

# --reload is a development-only flag: it adds a file watcher, disables
# optimizations, and leaks tracebacks to callers. Multiple workers matter
# because scoring is CPU-bound -- one worker serialized every request behind
# ~50ms of sklearn/SHAP/torch work. Override with UVICORN_WORKERS for a
# machine with a different core count.
exec uvicorn main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "${UVICORN_WORKERS:-4}"
