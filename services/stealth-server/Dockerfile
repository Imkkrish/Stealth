FROM python:3.12-slim

WORKDIR /app

# Install deps first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy server code
COPY . .

# Cloud Run injects PORT
ENV PORT=8080
EXPOSE 8080

# `uvicorn` is the production server. workers=1 because Socket.IO sticky-sessions
# don't fan out across workers without a Redis adapter (out of scope for v1).
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1 --log-level info"]
