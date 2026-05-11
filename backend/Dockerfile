FROM python:3.11-slim

# Non-root user for security
RUN groupadd -r jarvis && useradd -r -g jarvis jarvis

WORKDIR /app

# System deps — gcc needed for some pip packages, then purged
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# Layer-cache: install deps before copying source
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove gcc

# Copy only the backend source (build context is project root)
COPY backend/ .

RUN chown -R jarvis:jarvis /app
USER jarvis

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
