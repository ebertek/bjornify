FROM docker.io/gorialis/discord.py:minimal

# Set working directory
WORKDIR /app

# Copy source files
COPY requirements.txt ./
COPY wrapper.sh ./
COPY *.py ./

# Make script executable
RUN chmod +x wrapper.sh

# Add a non-root user before installing dependencies
RUN adduser --disabled-password --gecos '' appuser

# Give appuser ownership of the /app directory
RUN chown -R appuser:appuser /app

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Switch to non-root user
USER appuser

# Update PATH to include user base binary directory
ENV PATH="/home/appuser/.local/bin:${PATH}"

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 CMD pgrep -f wrapper.sh || exit 1

# Entrypoint
CMD ["./wrapper.sh"]
