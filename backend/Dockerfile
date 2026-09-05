FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# torch is the whole story of this layer. Installed from PyPI, the default
# linux wheel drags in the CUDA runtime as nvidia-* dependencies -- around 2 GB
# of downloads for a service that does CPU-only inference and runs with
# torch.set_num_threads(1). That is why this step used to fail with
# "ReadTimeoutError ... files.pythonhosted.org" on any connection that was not
# fast and stable: pip's default 15-second socket timeout against gigabytes of
# wheels.
#
# The CPU index serves the same torch version without the CUDA payload, which
# takes the image from ~5 GB to well under 1 GB and makes the build finish on
# an ordinary connection. Every version in requirements.txt is pinned exactly,
# so adding a second index does not open the door to a surprise upgrade.
#
# --timeout / --retries cover the rest: a slow mirror should make the build
# slow, not make it fail.
RUN pip install --no-cache-dir --timeout 180 --retries 10     --index-url https://download.pytorch.org/whl/cpu     --extra-index-url https://pypi.org/simple     -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

EXPOSE 8000

CMD ["./entrypoint.sh"]
