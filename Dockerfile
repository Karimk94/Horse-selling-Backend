FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
# Ensure uploads directory exists in the image so StaticFiles can mount it
RUN mkdir -p /app/uploads

COPY . .

# Expose port
EXPOSE 8000

# Apply database migrations, then start the application.
# Runs inside the Railway container so the internal DATABASE_URL hostname resolves.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
