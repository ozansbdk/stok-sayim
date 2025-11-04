# sayim/urls.py
from django.urls import path, re_path
from django.views.generic import TemplateView

# views.py dosyasından içe aktarılan TÜM AKTİF view'lar
from .views import (
    # --- 1. ANA AKIŞ FONKSİYONLARI ---
    sayim_emri_listesi,
    sayim_giris,
    raporlama_onay,
    yeni_sayim_emri,

    # --- 2. AJAX FONKSİYONLARI ---
    ajax_akilli_stok_ara,
    ajax_sayim_kaydet,
    gemini_ocr_analiz,

    # --- 3. RAPORLAMA & AUTH ---
    export_mutabakat_excel,
    set_personel_session,
    personel_login,
    depo_secim,

    # --- 4. YÖNETİM ---
    yonetim_araclari,
    upload_and_reload_stok_data,

    # --- 5. OCR & BARKOD & GEMINI ---
    OCRView,
    kamera_yukle,
    index,
)

urlpatterns = [
    # ----------------------------------------
    # 1️⃣ ANA AKIŞ VE EMİR YÖNETİMİ
    # ----------------------------------------
    path('', sayim_emri_listesi, name='sayim_emirleri'),
    path('giris/<int:sayim_emri_id>/', sayim_giris, name='sayim_giris'),
    path('rapor/<int:sayim_emri_id>/', raporlama_onay, name='raporlama_onay'),
    path('yeni-emir/', yeni_sayim_emri, name='yeni_sayim_emri'),

    # ----------------------------------------
    # 2️⃣ GİRİŞ & PERSONEL AUTH
    # ----------------------------------------
    path('login/', personel_login, name='personel_login'),
    path('set-session/', set_personel_session, name='set_personel_session'),
    path('depo-secim/', depo_secim, name='depo_secim'),

    # ----------------------------------------
    # 3️⃣ AJAX ENDPOINTLERİ
    # ----------------------------------------
    path('ajax/akilli-stok-ara/', ajax_akilli_stok_ara, name='ajax_akilli_stok_ara'),
    path('ajax/sayim-kaydet/<int:sayim_emri_id>/', ajax_sayim_kaydet, name='ajax_sayim_kaydet'),
    path('ajax/gemini-ocr/', gemini_ocr_analiz, name='ajax_gemini_ocr'),

    # ----------------------------------------
    # 4️⃣ RAPORLAMA VE YÖNETİM
    # ----------------------------------------
    path('export/mutabakat/<int:sayim_emri_id>/', export_mutabakat_excel, name='export_mutabakat_excel'),
    path('admin-tools/', yonetim_araclari, name='yonetim_araclari'),
    path('admin-tools/upload-stok/', upload_and_reload_stok_data, name='upload_stok_data'),

    # ----------------------------------------
    # 5️⃣ OCR & BARKOD & GEMINI (YENİ)
    # ----------------------------------------
    path('ocr/', OCRView.as_view(), name='ocr_view'),                 # POST: Görselden OCR + Barkod okuma
    path('kamera-yukle/', kamera_yukle, name='kamera_yukle'),         # Mobil destekli kamera yükleme
    path('index/', index, name='index'),                              # Basit test sayfası
]
