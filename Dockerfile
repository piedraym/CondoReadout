# The whole application in one image: the API also serves the web pages, and the nightly
# refresh runs from the same image with another command (python nightly.py).

# 1. The web pages: React built into plain files
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 2. Python with the libraries WeasyPrint needs to draw the PDF, and fonts close to Helvetica
FROM python:3.14-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY db/ db/
COPY --from=frontend /app/frontend/dist frontend/dist
WORKDIR /app/backend
ENV PYTHONUNBUFFERED=1
# Render gives the port in PORT
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}"]
