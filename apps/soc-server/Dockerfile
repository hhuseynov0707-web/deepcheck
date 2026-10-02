# soc-api: the SOC's backend-for-frontend (soc_api.py, docs/architecture-two-apps.md).
#
# Build context: apps/soc-server. It holds DASHBOARD_KEY and SOC_SESSION_SECRET
# at runtime (environment, never baked in) and refuses to start without them.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Pure-Python wheels plus uvicorn[standard]'s binary ones: no compiler needed.
# requirements-dev.txt (pytest) stays out of the image.
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 5 -r requirements.txt

COPY soc_api.py .

# Nothing here needs root, and this process is the one holding the key.
RUN useradd --system --no-create-home --uid 10001 soc
USER soc

EXPOSE 8200

# Unauthenticated, content-free: is this process up (soc_api.health).
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8200/api/health', timeout=2).status == 200 else 1)"

# --factory: create_app() reads the environment and raises on a missing
# secret, so a misconfigured container exits at boot instead of serving 502s.
# One worker: the login rate limit and the logout revocations are in-process
# memory (see the module docstring). --no-access-log: uvicorn's access log
# would write every client address to the container log; the application
# logs never name one.
CMD ["uvicorn", "--factory", "soc_api:create_app", "--host", "0.0.0.0", "--port", "8200", "--workers", "1", "--no-access-log"]
