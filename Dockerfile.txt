# Python'un resmi, stabil bir sürümünü temel al
FROM python:3.11-slim

# Gerekli sistem paketlerini (Tesseract ve ZBar) kur
# Kodunda 'tur' (Türkçe) kullandığın için onu da ekliyorum
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-tur \
    libzbar-dev \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Uygulama kodları için bir klasör oluştur
WORKDIR /app

# Önce sadece requirements'ı kopyala ki, her seferinde tekrar kurmasın
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Tüm proje kodunu kopyala
COPY . .

# Sunucuyu başlat (gunicorn kullandığınızı varsayıyorum)
# "stok_sayim_projesi" yazan yeri kendi projenizin (settings.py'nin olduğu klasör) adıyla değiştirin.
CMD ["gunicorn", "stok_sayim_projesi.wsgi:application", "--bind", "0.0.0.0:8000"]