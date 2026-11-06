FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8050 RADAR_CACHE_DIR=/tmp/radar-hrrr-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --create-home radar && chown -R radar:radar /app
USER radar
EXPOSE 8050
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8050} --workers 1 --worker-class gthread --threads 4 --timeout 180 --access-logfile - app:server"]
