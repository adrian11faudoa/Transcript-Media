# ── Build stage ────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

# System deps: FFmpeg, libsndfile (for soundfile), build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    libgomp1 \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Install WhisperX (not on PyPI, install from GitHub)
RUN pip install --no-cache-dir \
    git+https://github.com/m-bain/whisperX.git \
    || pip install --no-cache-dir openai-whisper  # Fallback

# Copy application code
COPY . .

# Create storage directories
RUN mkdir -p /data/storage/videos /data/storage/audio /data/storage/exports /tmp/transcriptai

# Non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app /data /tmp/transcriptai
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
