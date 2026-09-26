FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ src/
COPY resources/ resources/
# The reviewer console, served at /console.
COPY web/ web/

# Copy alembic migration files
COPY alembic/ alembic/
COPY alembic.ini .

# Expose port
EXPOSE 8000

# Run application
CMD ["uvicorn", "src.claimbridge.main:app", "--host", "0.0.0.0", "--port", "8000"]