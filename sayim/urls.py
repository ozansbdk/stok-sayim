# sayim/urls.py
from django.urls import path, re_path 
from django.views.generic import TemplateView 

# views.py dosyasından içe aktarılan TÜM AKTİF view'lar
from .views import (
    # 1. ANA AKIŞ FONKSİYONLARI 
    sayim_emri_listesi,   
    sayim_giris,          # <<< ID tipi int olmalı
    raporlama_onay,       # <<< ID tipi int olmalı
    
    # 2. AJAX FONKSİYONLARI
    ajax_akilli_stok_ara, 
    ajax_sayim_kaydet,    # <<< ID tipi int olmalı
    gemini_ocr_analiz,
    
    # 3. Raporlama ve Auth
    export_mutabakat_excel, # <<< ID tipi int olmalı
    set_personel_session, 
    
    # 4. AUTH ve DİĞER FONKSİYONLAR
    personel_login,  
    yeni_sayim_emri, 
    depo_secim,
    
    # Yönetim araçları
    yonetim_araclari,
    upload_and_reload_stok_data,
)


urlpatterns = [
    # ----------------------------------------
    # 1. ANA AKIŞ VE EMİR YÖNETİMİ
    # ----------------------------------------
    path('', sayim_emri_listesi, name='sayim_emirleri'),
    
    # Sayım Giriş (ID TİPİ DÜZELTİLDİ: INT)
    path('giris/<int:sayim_emri_id>/', sayim_giris, name='sayim_giris'),
    
    # Raporlama (ID TİPİ DÜZELTİLDİ: INT)
    path('rapor/<int:sayim_emri_id>/', raporlama_onay, name='raporlama_onay'),
    path('yeni-emir/', yeni_sayim_emri, name='yeni_sayim_emri'), 
    
    # ----------------------------------------
    # 2. AUTH VE YÖNLENDİRME 
    # ----------------------------------------
    # Login (Parametresiz)
    path('login/', personel_login, name='personel_login'), 
    
    path('set-session/', set_personel_session, name='set_personel_session'),
    path('depo-secim/', depo_secim, name='depo_secim'),
    
    # ----------------------------------------
    # 3. AJAX ENDPOINT'LERİ (ID TİPİ DÜZELTİLDİ: INT)
    # ----------------------------------------
    path('ajax/akilli-stok-ara/', ajax_akilli_stok_ara, name='ajax_akilli_stok_ara'),
    path('ajax/sayim-kaydet/<int:sayim_emri_id>/', ajax_sayim_kaydet, name='ajax_sayim_kaydet'),
    path('ajax/gemini-ocr/', gemini_ocr_analiz, name='ajax_gemini_ocr'), 

    # ----------------------------------------
    # 4. YÖNETİM VE RAPORLAMA (ID TİPİ DÜZELTİLDİ: INT)
    # ----------------------------------------
    path('export/mutabakat/<int:sayim_emri_id>/', export_mutabakat_excel, name='export_mutabakat_excel'),
    
    # Yönetim araçları
    path('admin-tools/', yonetim_araclari, name='yonetim_araclari'),
    path('admin-tools/upload-stok/', upload_and_reload_stok_data, name='upload_stok_data'),
]