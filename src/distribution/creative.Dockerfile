# User-owned Pi runtime, authentication and licensed skills are mounted at runtime.
FROM node:22.22.1-bookworm-slim AS node
FROM docker:28.5.2-cli AS docker
FROM ghcr.io/astral-sh/uv:0.8.22 AS uv
FROM python:3.12-slim
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=docker /usr/local/bin/docker /usr/local/bin/docker
COPY --from=uv /uv /usr/local/bin/uv
ARG CREATIVE_UID=1000
ARG CREATIVE_GID=1000
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates fonts-dejavu-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && uv pip install --system --no-cache Pillow==12.1.1 \
    && (getent group ${CREATIVE_GID} >/dev/null || groupadd --gid ${CREATIVE_GID} studio) \
    && useradd --uid ${CREATIVE_UID} --gid ${CREATIVE_GID} --create-home studio \
    && ln -s /opt/pi-runtime/dist/cli.js /usr/local/bin/pi
COPY creative /app/creative
COPY worker/adapters/project_bundle_contract.py worker/adapters/listening_timeline.py /app/worker/adapters/
# Restored source is deliberately private0600/0700 on the host. Only application
# code inside this image needs to be readable by the configured non-root UID.
RUN chmod -R a+rX /app
ENV HOME=/home/studio PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PI_CODING_AGENT_DIR=/auth PI_SKIP_VERSION_CHECK=1 PI_TELEMETRY=0
WORKDIR /app
USER ${CREATIVE_UID}:${CREATIVE_GID}
CMD ["python","-m","creative.worker","--config","/config/creative-runtime.json"]
