#!/bin/sh
set -e

# Ask scorer.py whether the artifacts on disk are actually usable, rather than
# just checking that files exist. It refuses a model.pkl written by a
# different scikit-learn (which sklearn only warns about, then scores anyway
# with "possibly invalid results") or against a different feature set.
#
# That second case is routine here rather than exotic: backend/ is bind-mounted
# from the host, so a model trained by the host's Python is the very file this
# container loads with the pinned scikit-learn from requirements.txt.
# Retraining is the right answer -- the artifacts are reproducible from a fixed
# seed and are deliberately not in git.
if ! python -c "import scorer; scorer.get_bundle()" >/dev/null 2>&1; then
  # A cold container trains before it can serve. Without this notice the
  # several silent minutes look exactly like a hang, which is not something
  # to discover in front of an audience.
  echo "======================================================================"
  echo " Kullanilabilir model yok (eksik dosya veya surum uyumsuzlugu)."
  echo " Modeller egitiliyor."
  echo " 25.000 oturum x 10 akis penceresi uretilecek ve model egitilecek."
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
#
# Two, not four. Each worker holds its own forests, SHAP explainer and torch
# runtime -- roughly 300-400 MB resident. Four need ~1.5 GB, and Docker Desktop
# allocates about 2 GB to the whole VM by default; with Postgres and nginx
# alongside, that machine swaps. Measured on exactly that setup: /api/health
# took 25 SECONDS to answer while the CPU sat at 1%. Two workers fit and still
# overlap the ~50 ms of CPU-bound scoring. Raise UVICORN_WORKERS on a host that
# has the memory.
#
# --no-access-log: uvicorn's access log writes the client IP address of every
# request to the container log, which docker keeps with no retention limit.
# The application never stores an IP (it holds one in memory only, for the
# per-address session-minting limit), and the privacy statement says so; the
# access log would have made that untrue. Application logs (the "deepcheck"
# loggers) never name an address.
exec uvicorn main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "${UVICORN_WORKERS:-2}" \
  --no-access-log
