# ============================
# MACRS – Unified Dockerfile
# ============================
FROM python:3.11-slim AS base

# Install essential system deps (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for safety
RUN useradd -ms /bin/bash appuser
WORKDIR /app

# Copy only requirements for caching
COPY requirements.txt /app/requirements.txt

# Install Python deps
RUN pip install --no-cache-dir -r requirements.txt

# Copy full application code (for production build)
COPY . /app

# Switch to non-root user
USER appuser

# Default entrypoint overridden by docker-compose
CMD ["bash"]
