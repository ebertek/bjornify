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

# Set working directory.
WORKDIR /app

# Create a non-privileged user that the app will run under.
ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/app" \
    --shell "/sbin/nologin" \
    --no-create-home \
    --uid "${UID}" \
    appuser

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

# Copy only the runtime code. The build context is the repository root.
COPY bjornify/ .
COPY --from=connect-build /connect/target/release/bjornify-connect /usr/local/bin/bjornify-connect

# Make entrypoint script executable and create required directories.
RUN chmod +x wrapper.sh && \
    mkdir -p /app/logs /app/secrets && \
    chown -R appuser:appuser /app

# Switch to the non-privileged user to run the application.
# The named user is created earlier in this image; its UID is build-time configurable.
# hadolint ignore=DL3066
USER appuser

# Healthcheck.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 CMD ["/app/wrapper.sh", "healthcheck"]

# Declare persistent volume for secrets.
VOLUME ["/app/secrets"]

# Run the application.
ENTRYPOINT ["./wrapper.sh"]
