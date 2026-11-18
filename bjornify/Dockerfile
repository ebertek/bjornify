# syntax=docker/dockerfile:1
# checkov:skip=CKV_DOCKER_3 Justification: UID is provided externally via Compose

ARG PYTHON_VERSION=3.15-slim
ARG DEBIAN=trixie

# Build librespot once and ship only its command-line executable.
# Use the same Debian release as the Python runtime for OpenSSL ABI compatibility.
FROM rust:slim-trixie AS connect-build

# hadolint ignore=DL3008
RUN apt-get update && apt-get install --no-install-recommends -y \
    ca-certificates pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /connect
COPY spotify-connect/Cargo.toml ./Cargo.toml
COPY spotify-connect/src/ ./src/
RUN cargo build --release

FROM python:${PYTHON_VERSION}-${DEBIAN}

# libnss-wrapper resolves arbitrary Compose numeric UIDs in docker exec shells.
# Set paths in the image so docker exec inherits the same NSS configuration.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install --no-install-recommends -y \
    libnss-wrapper \
    && rm -rf /var/lib/apt/lists/*
# Accept version as build argument.
ARG VERSION=dev
ARG GITHUB_SHA
ARG BUILD_DATE
ENV BJORNIFY_VERSION=$VERSION

# Add OCI labels.
LABEL org.opencontainers.image.description="Discord bot based on discord.py that adds requested tracks to your Spotify playback queue."
LABEL org.opencontainers.image.documentation="https://github.com/ebertek/bjornify#readme"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/ebertek/bjornify"
LABEL org.opencontainers.image.title="Björnify"
LABEL org.opencontainers.image.url="https://github.com/ebertek/bjornify"
LABEL org.opencontainers.image.vendor="ebertek"

# Add version labels.
LABEL org.opencontainers.image.created=$BUILD_DATE
LABEL org.opencontainers.image.revision=$GITHUB_SHA
LABEL org.opencontainers.image.version=$VERSION

# Prevent Python from writing pyc files.
ENV PYTHONDONTWRITEBYTECODE=1

# Keep Python from buffering stdout and stderr to avoid situations where
# the application crashes without emitting any logs due to buffering.
ENV PYTHONUNBUFFERED=1

# Disable pip cache.
ENV PIP_NO_CACHE_DIR=1

# Preserve the original non-root user's home for numeric UID execution.
ENV HOME=/app

# Set working directory.
WORKDIR /app

# Use a numeric, non-root default identity. Docker Compose may override
# it with a runtime UID:GID; NSS wrapper supplies that identity's display name.
# Do not create a separate appuser that is never used in such deployments.
ARG UID=10001

# Download dependencies as a separate step to take advantage of Docker's caching.
# Leverage a cache mount to /root/.cache/pip to speed up subsequent builds.
# Leverage a bind mount to requirements.txt to avoid having to copy them into
# this layer.
# Re-resolve minimum-version requirements for each release build instead of
# reusing an old, cached pip-install layer.
ARG DEPENDENCY_REFRESH=local
# hadolint ignore=DL3042
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=bjornify/requirements.txt,target=requirements.txt \
    echo "Resolving latest compatible dependencies for ${DEPENDENCY_REFRESH}" && \
    python -m pip install --root-user-action ignore -r requirements.txt

# Copy runtime code only. Do not ship the source Dockerfile, pip
# requirements, or local build metadata into the running container.
# Runtime code is immutable to the unprivileged process, even when Compose
# selects an arbitrary UID/GID. Copy with deterministic root ownership/modes.
COPY --chown=0:0 --chmod=0644 bjornify/*.py ./
COPY --chown=0:0 --chmod=0755 bjornify/wrapper.sh ./
COPY --from=connect-build /connect/target/release/bjornify-connect /usr/local/bin/bjornify-connect

# Only logs and secrets need writable directories. Never chown source code.
RUN printf '%s\n' '#!/bin/sh' 'exec python /app/cli.py "$@"' > /usr/local/bin/bjornify && \
    chmod 0755 /usr/local/bin/bjornify && \
    install -d -m 0750 -o "${UID}" -g "${UID}" /app/logs && \
    install -d -m 0700 -o "${UID}" -g "${UID}" /app/secrets && \
    mkdir -p /run/bjornify-identity && \
    cp /etc/passwd /run/bjornify-identity/passwd && \
    cp /etc/group /run/bjornify-identity/group && \
    chmod 0644 /run/bjornify-identity/passwd /run/bjornify-identity/group && \
    chmod 0777 /run/bjornify-identity

# These are paths to runtime lookup files, NOT passwords or credentials.
# Keep them in ENV so docker exec sees the same NSS view as the bot.
#trivy:ignore:DS-0031
ENV LD_PRELOAD=libnss_wrapper.so \
    NSS_WRAPPER_PASSWD=/run/bjornify-identity/passwd \
    NSS_WRAPPER_GROUP=/run/bjornify-identity/group

# Run non-root even when Compose does not specify a user. The startup
# wrapper creates a matching NSS identity for both default and overridden UIDs.
# hadolint ignore=DL3066
USER ${UID}:${UID}

# Healthcheck.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 CMD ["/app/wrapper.sh", "healthcheck"]

# Copy-up seeds NSS data into its own writable volume even with a read-only
# root filesystem and an empty /tmp tmpfs. No credentials are stored there.
VOLUME ["/app/secrets", "/run/bjornify-identity"]

# Run the application.
ENTRYPOINT ["./wrapper.sh"]
