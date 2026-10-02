# checkout-api: TechStore's own server. Build context is this folder
# (apps/checkout-server); it needs nothing else from the repository.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Pure-Python wheels and prebuilt binaries only (uvicorn[standard]'s uvloop,
# httptools, watchfiles all ship manylinux wheels for 3.11), so no compiler.
COPY requirements.txt .
RUN pip install --timeout 120 --retries 5 -r requirements.txt

COPY main.py .

# Not root: this process holds the merchant key and talks to the network.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin checkout
USER checkout

# Whose X-Forwarded-For uvicorn believes. The rate limit in main.py is keyed by
# client address, and behind checkout-web's nginx the TCP peer is nginx for
# EVERY visitor -- with uvicorn's default (127.0.0.1 only) all payers would
# share one bucket.
#
# What this does NOT buy under Docker Desktop (the jury laptop): the address
# nginx forwards is not the visitor's either. Docker Desktop's port forwarder
# hands nginx the compose network's gateway as the peer -- with a connection
# held open from the host, /proc/net/tcp in deepcheck-checkout-web showed
# 172.20.0.1, the gateway of 172.20.0.0/16 -- so $remote_addr, and with it
# this server's key, is the same for everyone. The 10/min checkout and verify
# limits are therefore per HOST, not per visitor: a bot looping from laptop A
# and a person paying from laptop B draw from one bucket, and a burst from one
# can 429 the other. LAN connections were not measured from a second machine;
# the forwarder is expected to present them the same way. Per-visitor keys
# need the client address to reach nginx in the first place, which nothing in
# this file can do; no alternative has been tried.
#
# "*" is safe here for one reason, and only while it holds: checkout-api is an
# INTERNAL service. It publishes no port (docker-compose.yml), so the only
# peers that can reach it are containers on the compose network, and the one
# that forwards browser traffic -- checkout-web -- REPLACES X-Forwarded-For
# with the address it saw (proxy_set_header X-Forwarded-For $remote_addr in
# apps/checkout/nginx.conf), never appends a client-supplied chain. uvicorn
# 0.30.1 with "*" takes the leftmost entry, which nginx wrote. If port 8100 is
# ever published, set this to the nginx container's address instead (exact
# addresses only, no CIDR, in this uvicorn version) or every caller can pick
# its own bucket.
ENV FORWARDED_ALLOW_IPS="*"

EXPOSE 8100

# One worker on purpose: the rate-limit counters and the pending OTP
# challenges live in this process's memory (main.py). A second worker would
# split both, and a code typed into the dialog could land on a worker that
# never issued the challenge. --no-access-log: an access log is a list of every
# payer's address, and this server has no use for one.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100", "--no-access-log", "--proxy-headers"]
