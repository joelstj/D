# l2arb detection engine — read-only HTTP service (POST /detect, GET /health).
#
# Detection only: this image holds no keys, signs nothing, and executes no
# transactions (ADR-001). Built for the root docker-compose stack; also runnable
# standalone:  docker build -t l2arb-engine engine/ && docker run -p 8080:8080 l2arb-engine
FROM python:3.12-slim

WORKDIR /app

# gcc covers the rare source-only wheel; most deps (numpy/numba/…) ship binaries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY . /app
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

EXPOSE 8080

# 0.0.0.0 so other compose services (the ingestion layer) can POST /detect.
CMD ["uvicorn", "l2arb.api.http:app", "--host", "0.0.0.0", "--port", "8080"]
