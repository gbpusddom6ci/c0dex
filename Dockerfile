# Fly.io için optimize edilmiş Dockerfile
# Docker Hub'daki genel 3.11-slim etiketinde yaşanan manifest hatalarını
# aşmak için kararlı bir slim sürüme pinliyoruz.
FROM python:3.11.7-slim-bullseye

# Çalışma dizini
WORKDIR /app

# Python optimizasyonları
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Sistem bağımlılıkları (gerekirse)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Python bağımlılıklarını kopyala ve yükle
COPY requirements.txt .
RUN pip install -r requirements.txt

# Uygulama kodunu kopyala
COPY . .

# Port açıklama (Fly.io bu portu kullanacak)
EXPOSE 8080

# Uygulamayı başlat
CMD ["python", "-m", "appsuite.web", "--host", "0.0.0.0", "--port", "8080"]
