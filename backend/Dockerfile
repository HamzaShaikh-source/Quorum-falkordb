FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install backend dependencies
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application
COPY backend/ backend/

# Copy P1 graph package (graph/queries.py)
COPY graph/ graph/

# The scenario's agents (backend/app imports them)
COPY agents/ agents/

# The last recorded benchmark run, served at /api/bench/results
COPY bench/results.json bench/results.json

# Set working directory to repo root so both `backend/app/` and `graph/` are resolvable
ENV PYTHONPATH=/app/backend:/app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
