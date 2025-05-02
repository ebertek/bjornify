# checkov:skip=CKV_DOCKER_3 Justification: UID is provided externally via Compose

FROM docker.io/gorialis/discord.py:minimal

# Set working directory
WORKDIR /app

# Copy source files
COPY requirements.txt ./
COPY wrapper.sh ./
COPY *.py ./

# Make script executable
RUN chmod +x wrapper.sh

# Create directory structure
RUN mkdir -p /app/logs /app/secrets

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 CMD pgrep -f wrapper.sh || exit 1

# Entrypoint
CMD ["./wrapper.sh"]
