# /worker/Dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install --no-install-recommends -y \
    g++ gdal-bin libgdal-dev build-essential gcc && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install flask supervisor

COPY . .

COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
