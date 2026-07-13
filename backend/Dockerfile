# Multi-stage build — keeps the final image lean by discarding build tools.
#
# Stage 1 (builder): compiles C extensions for hdbscan, umap-learn, numpy.
# Stage 2 (final):   copies only the venv + baked model weights + app code.

# ============================================================
# Stage 1: builder
# ============================================================
FROM python:3.12-slim AS builder

# Build tools needed to compile hdbscan / umap-learn / numpy C extensions.
# gcc, g++, and the Python headers ship in build-essential + python3-dev.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Create a venv so the final stage can copy it without mixing system packages.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install PyTorch CPU-only FIRST.
# The default PyPI torch wheel bundles CUDA (~2 GB). The CPU-only wheel from
# the pytorch index is ~200 MB and is all we need for sentence-transformers.
# This layer is separate so it's cached independently from requirements.txt.
RUN pip install --upgrade pip && \
    pip install torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the sentence-transformers model at build time.
# Without this, every cold start would fetch ~90 MB from huggingface.co,
# which adds ~30 s of latency to the first health-check and risks rate-limiting.
ENV HF_HOME=/app/models
ENV SENTENCE_TRANSFORMERS_HOME=/app/models

RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# ============================================================
# Stage 2: final runtime image
# ============================================================
FROM python:3.12-slim AS final

# Only runtime dependencies — no compilers.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user — running as root inside a container is unnecessary and a
# security smell even for a portfolio project.
RUN useradd --no-create-home --shell /bin/false appuser

# Copy the compiled venv from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Copy the baked model weights (~90 MB; avoids cold-start download).
COPY --from=builder /app/models /app/models

WORKDIR /app

# Copy application source (alembic.ini, db/, agents/, api/, etc.).
COPY --chown=appuser:appuser . .

# Fix ownership of the pre-baked model cache so appuser can read it.
RUN chown -R appuser:appuser /app/models

# Make the entrypoint script executable in case it lost the bit in the COPY.
RUN chmod +x /app/docker-entrypoint.sh

# Activate the venv for all subsequent commands and the container process.
ENV PATH="/opt/venv/bin:$PATH"

# Tell sentence-transformers and HuggingFace to use the baked-in cache.
ENV HF_HOME=/app/models
ENV SENTENCE_TRANSFORMERS_HOME=/app/models
ENV PYTHONUNBUFFERED=1

USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
