FROM python:3.12-slim

# Keep runtime sane + deterministic
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    GRC_DB_PATH=/data/pipeline.db

WORKDIR /app

# Create a dedicated non-root user
RUN addgroup --system app && adduser --system --ingroup app app

# Ensure /data exists and is writable for arbitrary UID/GID (bind mounts)
# 1777 = world-writable + sticky bit (safe-ish for shared dirs)
RUN mkdir -p /data && chmod 1777 /data

# Create and prime a virtualenv (owned by root, usable by app user)
RUN python -m venv /opt/venv && \
    pip install -U pip

# Install deps (editable install since you're copying src)
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install -e .

# Drop privileges (docker run -u can override this)
USER app

EXPOSE 8000

CMD ["python", "-m", "grc_pipeline.cli", "serve", "--db", "/data/pipeline.db", "--host", "0.0.0.0", "--port", "8000"]
