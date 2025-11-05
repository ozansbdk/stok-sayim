# -*- coding: utf-8 -*-
import json
import time
import os
import base64
import pandas as pd
from datetime import datetime
from io import BytesIO

from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.core.files.storage import default_storage

# OCR & Barkod
import pytesseract
from PIL import Image
import cv2
import numpy as np
from pyzbar.pyzbar import decode

# Gemini / Yapay Zeka
import google.generativeai as genai

# === TESSERACT MANUAL PATH ===
# ARTIK BU SATIRA GEREK YOK! Dockerfile bunu sunucuya kuruyor.
# pytesseract.pytesseract.tesseract_cmd = (
#     r"C:\Users\ozan.sabudak\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
# )

# === GEMINI ANAHTARI ===
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


# === OCR Fonksiyonu ===
def ocr_read_image(image):
    """Tesseract ile metin okur"""
    try:
        gray = image.convert("L")
        text = pytesseract.image_to_string(gray, lang="eng+tur")
        return text.strip()
    except Exception as e:
        return f"OCR hata: {str(e)}"


# === Barkod / QR Kod Okuma ===
def decode_barcode(image):
    """pyzbar ile barkod/QR kod okuma"""
    try:
        img_cv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        decoded = decode(img_cv)
        if decoded:
            return decoded[0].data.decode("utf-8")
        return None
    except Exception as e:
        return f"Barkod hata: {str(e)}"


# === Gemini Entegrasyonu ===
def gemini_analyze(text):
    """Gemini ile görsel metin yorumlama veya doğrulama"""
    if not GEMINI_API_KEY:
        return "Gemini API anahtarı bulunamadı."

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(
            f"Bu metindeki stok kodunu ayıkla ve doğrula:\n{text}"
        )
        return response.text
    except Exception as e:
        return f"Gemini hata: {str(e)}"


# === Stok Eşleşme Fonksiyonu ===
def stok_eslesme(aranan_kod, stok_listesi):
    """Basit stok kodu eşleşmesi yapar"""
    for stok in stok_listesi:
        # Ekstra bir güvenlik önlemi: aranan_kod 'None' gelirse patlamasın
        if not aranan_kod:
            continue
        if aranan_kod.strip().lower() in stok["stok_kodu"].lower():
            return stok
    return None


# === Ana Sayfa ===
def index(request):
    return render(request, "sayim/index.html")


# === Kamera Destekli Yükleme Sayfası ===
def kamera_yukle(request):
    """Mobil destekli kamera yükleme sayfası"""
    return render(request, "sayim/kamera_yukle.html")


# === OCR & Barkod İşleme ===
@method_decorator(csrf_exempt, name="dispatch")
class OCRView(View):
    def post(self, request, *args, **kwargs):
        """Resim yükleme veya mobil kameradan gelen görseli OCR + Barkod + Gemini ile okur."""
        try:
            image_file = request.FILES.get("image")
            if not image_file:
                return JsonResponse({"error": "Görsel bulunamadı"}, status=400)

            # Görseli oku
            image = Image.open(image_file)

            # Barkod veya QR kod dene
            barkod_sonuc = decode_barcode(image)
            ocr_sonuc = ocr_read_image(image)

            # AI destekli doğrulama
            gemini_yanit = gemini_analyze(ocr_sonuc) if GEMINI_API_KEY else None

            # Sahte stok listesi (örnek) - DİKKAT: Bu hala geçici
            stok_listesi = [
                {"stok_kodu": "DF12345", "urun_adi": "Mavi Tişört"},
                {"stok_kodu": "DF67890", "urun_adi": "Siyah Pantolon"},
                {"stok_kodu": "DF11111", "urun_adi": "Beyaz Gömlek"},
            ]

            # Barkod öncelikli eşleşme
            aranan_kod = barkod_sonuc or ocr_sonuc
            eslesen_stok = stok_eslesme(aranan_kod, stok_listesi)

            response = {
                "barkod_sonuc": barkod_sonuc,
                "ocr_sonuc": ocr_sonuc,
                "gemini_yanit": gemini_yanit,
                "eslesen_stok": eslesen_stok,
            }

            return JsonResponse(response)

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


# === Excel Yükleme ve Stok Listesi ===
@csrf_exempt
def upload_excel(request):
    """Excel dosyasından stok listesi yükler"""
    try:
        file = request.FILES.get("file")
        if not file:
            return JsonResponse({"error": "Dosya seçilmedi"}, status=400)

        df = pd.read_excel(file)
        stok_listesi = df.to_dict(orient="records")

        request.session["stok_listesi"] = stok_listesi
        return JsonResponse({"status": "OK", "count": len(stok_listesi)})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# === OCR + Eşleşme ===
@csrf_exempt
def ocr_upload_and_match(request):
    """OCR / Barkod okuma + Stok eşleşmesi (Excel listesinden)"""
    try:
        image_file = request.FILES.get("image")
        if not image_file:
            return JsonResponse({"error": "Resim bulunamadı"}, status=400)

        # Görseli oku
        image = Image.open(image_file)

        barkod_sonuc = decode_barcode(image)
        ocr_sonuc = ocr_read_image(image)
        aranan_kod = barkod_sonuc or ocr_sonuc

        stok_listesi = request.session.get("stok_listesi", [])

        # Eşleşme yap
        eslesen = stok_eslesme(aranan_kod, stok_listesi)

        # Benzer adaylar (AI olmadan basit string match)
        candidate_codes = []
        if aranan_kod: # Eğer aranan_kod None değilse
            candidate_codes = [
                s["stok_kodu"] for s in stok_listesi if aranan_kod.strip() in s["stok_kodu"]
            ]

        return JsonResponse(
            {
                "aranan_kod": aranan_kod,
                "eslesen": eslesen,
                "adaylar": candidate_codes[:5],
            }
        )

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# === Basit Sayım Kayıtları (örnek) ===
@csrf_exempt
def kayit_ekle(request):
    """Sayım kaydı ekler"""
    try:
        data = json.loads(request.body)
        kod = data.get("stok_kodu")
        adet = data.get("adet")
        personel = data.get("personel", "Anonim")

        kayit = {
            "stok_kodu": kod,
            "adet": adet,
            "personel": personel,
            "tarih": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # Oturumda kaydet (DİKKAT: Bu hala geçici)
        kayitlar = request.session.get("kayitlar", [])
        kayitlar.append(kayit)
        request.session["kayitlar"] = kayitlar

        return JsonResponse({"status": "OK", "kayit": kayit})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# === Kayıtları Görüntüle ===
def kayit_listesi(request):
    """Kayıtları listeler"""
    kayitlar = request.session.get("kayitlar", [])
    return render(request, "sayim/kayit_listesi.html", {"kayitlar": kayitlar})


# === Export Excel ===
def export_excel(request):
    """Kayıtları Excel olarak dışa aktarır"""
    try:
        kayitlar = request.session.get("kayitlar", [])
        if not kayitlar:
            return JsonResponse({"error": "Kayıt yok"}, status=400)

        df = pd.DataFrame(kayitlar)
        buffer = BytesIO()
        df.to_excel(buffer, index=False)
        buffer.seek(0)

        response = HttpResponse(
            buffer, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = 'attachment; filename="sayim_kayitlari.xlsx"'
        return response
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# === 404 handler ===
def not_found(request, exception):
    return render(request, "sayim/404.html", status=404)