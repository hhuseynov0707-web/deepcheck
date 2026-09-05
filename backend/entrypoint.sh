#!/bin/sh
set -e

if [ ! -f "model.pkl" ]; then
  # A cold container trains before it can serve. Without this notice the
  # several silent minutes look exactly like a hang, which is not something
  # to discover in front of an audience.
  echo "======================================================================"
  echo " model.pkl bulunamadi. Modeller ilk kez egitiliyor."
  echo " 25.000 oturum x 10 akis penceresi uretilecek ve uc model egitilecek."
  echo " Beklenen sure: 4-8 dakika (makineye gore degisir). Lutfen bekleyin;"
  echo " bu asamada API henuz istek kabul etmez."
  echo "======================================================================"
  python train_model.py
  echo "Model egitimi tamamlandi. API baslatiliyor..."
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
