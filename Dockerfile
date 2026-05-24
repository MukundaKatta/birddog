# Cloud Run dashboard for birddog. Wraps the published library +
# bundled audit log + Streamlit UI so judges can interact with the
# Bright Data audit dashboard live.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# Install the library itself + dashboard extras.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[dashboard]"

# Copy the entrypoint + bundled sample audit log last so tweaks don't
# bust the deps layer.
COPY app.py ./
COPY runs ./runs

EXPOSE 8080

# Cloud Run health probe hits /. Streamlit's default health endpoint is
# /_stcore/health, but the root works fine for liveness too.
CMD streamlit run app.py \
    --server.port=$PORT \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
