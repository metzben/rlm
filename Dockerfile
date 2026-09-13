# The sandbox kit spec requires the image to provide: a non-root `agent` user
# (UID 1000) with passwordless sudo, /home/agent, and the HTTP proxy env vars
# preserved across sudo. docker/sandbox-templates:shell-docker ships all of that.
FROM docker/sandbox-templates:shell-docker

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv python3-pip \
 && rm -rf /var/lib/apt/lists/*

# Point Python's HTTP stack at the system CA store. The sandbox proxy
# terminates TLS to inject credentials, and its CA lives in the system store —
# httpx/requests default to certifi's bundle and would reject it otherwise.
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PYTHONUNBUFFERED=1

COPY --chown=1000:1000 . /home/agent/rlm-src

USER agent
RUN python3 -m venv /home/agent/.venv \
 && /home/agent/.venv/bin/pip install --no-cache-dir --upgrade pip \
 && /home/agent/.venv/bin/pip install --no-cache-dir /home/agent/rlm-src

WORKDIR /home/agent/workspace

# Do NOT set an ENTRYPOINT: the sandbox runtime needs the template's own
# init (`tini`) as the container's main process — it keeps the container
# alive while the runtime injects its start-agent helper, which then runs
# the kit's sandbox.entrypoint (rlm). Overriding it kills the container at
# start ("started hook: ... container is not running").
# For a local smoke test: docker run --rm <image> /home/agent/.venv/bin/rlm --help
