# sayim/urls.py
from django.urls import path, re_path 
from django.views.generic import TemplateView 

# views.py dosyasından içe aktarılan TÜM AKTİF view'lar
from .views import (
    # 1. ANA AKIŞ FONKSİYONLARI 
    sayim_emri_listesi,   
    sayim_giris,          
    raporlama_onay,
    
    # 2. AJAX FONKSİYONLARI
    ajax_akilli_stok_ara, 
    ajax_sayim_kaydet,
    gemini_ocr_analiz,
    
    # 3. Raporlama ve Auth
    export_mutabakat_excel,
    set_personel_session, 
    
    # 4. AUTH ve DİĞER FONKSİYONLAR
    personel_login,  
    yeni_sayim_emri, 
    depo_secim,
)


urlpatterns = [
    # ----------------------------------------
    # 1. ANA AKIŞ VE EMİR YÖNETİMİ
    # ----------------------------------------
    path('', sayim_emri_listesi, name='sayim_emirleri'),
    
    # Sayım Giriş ve Raporlama (UUID formatını korudum)
    path('giris/<uuid:sayim_emri_id>/', sayim_giris, name='sayim_giris'),
    path('rapor/<uuid:sayim_emri_id>/', raporlama_onay, name='raporlama_onay'),
    path('yeni-emir/', yeni_sayim_emri, name='yeni_sayim_emri'), 
    
    # ----------------------------------------
    # 2. AUTH VE YÖNLENDİRME (KRİTİK DÜZELTME)
    # ----------------------------------------
    # LOGIN_URL'nin yönlendirdiği yer: Sayım Emri ID'si ve Depo Kodu almalıdır.
    path('login/<uuid:sayim_emri_id>/<str:depo_kodu>/', personel_login, name='personel_login'), 
    
    path('set-session/', set_personel_session, name='set_personel_session'),
    path('depo-secim/', depo_secim, name='depo_secim'),
    
    # ----------------------------------------
    # 3. AJAX ENDPOINT'LERİ
    # ----------------------------------------
    path('ajax/akilli-stok-ara/', ajax_akilli_stok_ara, name='ajax_akilli_stok_ara'),
    path('ajax/sayim-kaydet/<uuid:sayim_emri_id>/', ajax_sayim_kaydet, name='ajax_sayim_kaydet'),
    path('ajax/gemini-ocr/', gemini_ocr_analiz, name='ajax_gemini_ocr'), 

    # ----------------------------------------
    # 4. RAPORLAMA
    # ----------------------------------------
    path('export/mutabakat/<uuid:sayim_emri_id>/', export_mutabakat_excel, name='export_mutabakat_excel'),
]