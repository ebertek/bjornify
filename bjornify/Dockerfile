# syntax=docker/dockerfile:1
# checkov:skip=CKV_DOCKER_3 Justification: UID is provided externally via Compose

FROM docker.io/gorialis/discord.py:minimal

# Accept version as build argument.
ARG VERSION=dev
ARG GITHUB_SHA
ARG BUILD_DATE
ENV BJORNIFY_VERSION=$VERSION

# Add version labels.
LABEL org.opencontainers.image.version=$VERSION
LABEL org.opencontainers.image.revision=$GITHUB_SHA
LABEL org.opencontainers.image.created=$BUILD_DATE

# Prevent Python from writing pyc files.
ENV PYTHONDONTWRITEBYTECODE=1

# Keep Python from buffering stdout and stderr to avoid situations where
# the application crashes without emitting any logs due to buffering.
ENV PYTHONUNBUFFERED=1

# Set working directory.
WORKDIR /app

# Create a non-privileged user that the app will run under.
ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/nonexistent" \
    --shell "/sbin/nologin" \
    --no-create-home \
    --uid "${UID}" \
    appuser

# Download dependencies as a separate step to take advantage of Docker's caching.
# Leverage a cache mount to /root/.cache/pip to speed up subsequent builds.
# Leverage a bind mount to requirements.txt to avoid having to copy them into
# into this layer.
# hadolint ignore=DL3042
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements.txt,target=requirements.txt \
    python -m pip install -r requirements.txt

# Switch to the non-privileged user to run the application.
USER appuser

# Copy the source code into the container.
COPY . .

# Make entrypoint script executable and create required directories.
RUN chmod +x wrapper.sh && \
    mkdir -p /app/logs /app/secrets

# Healthcheck.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 CMD pgrep -f wrapper.sh || exit 1

# Declare persistent volume for secrets.
VOLUME ["/app/secrets"]

# Run the application.
ENTRYPOINT ["./wrapper.sh"]
