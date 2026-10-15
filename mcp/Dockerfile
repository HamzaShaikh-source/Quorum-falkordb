FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY mcp/ ./mcp/
COPY contracts/ ./contracts/
COPY graph/ ./graph/
COPY agents/ ./agents/

ENV PYTHONPATH="/app/backend:/app/mcp:/app"

EXPOSE 8001

CMD ["python", "mcp/server.py", "--port", "8001", "--host", "0.0.0.0"]
