# sayim/urls.py
from django.urls import path, re_path 
from django.views.generic import TemplateView # Eğer hala bazı basit CBV'ler kullanılıyorsa kalsın

# views.py dosyasından içe aktarılan TÜM AKTİF view'lar
from .views import (
    # 1. ANA AKIŞ FONKSİYONLARI (Views.py'daki fonksiyonel karşılıkları)
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
    
    # Not: Bu fonksiyonlar views.py'da tanımlıysa kalsın. (Önceki CBV'lerin fonksiyonel adları)
    personel_login,  
    yeni_sayim_emri, 
    depo_secim,
    
    # Yönetim araçları (yonetim_araclari, reset_sayim_data, upload_and_reload_stok_data) 
    # views.py'da olmadığı için içe aktarma listesinden KALDIRILDI.
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
    # 2. AUTH VE YÖNLENDİRME 
    # ----------------------------------------
    path('login/', personel_login, name='personel_login'), 
    path('set-session/', set_personel_session, name='set_personel_session'),
    path('depo-secim/', depo_secim, name='depo_secim'),
    
    # ----------------------------------------
    # 3. AJAX ENDPOINT'LERİ
    # ----------------------------------------
    path('ajax/akilli-stok-ara/', ajax_akilli_stok_ara, name='ajax_akilli_stok_ara'),
    path('ajax/sayim-kaydet/<uuid:sayim_emri_id>/', ajax_sayim_kaydet, name='ajax_sayim_kaydet'),
    path('ajax/gemini-ocr/', gemini_ocr_analiz, name='gemini_ocr_analiz'),

    # ----------------------------------------
    # 4. RAPORLAMA
    # ----------------------------------------
    path('export/mutabakat/<uuid:sayim_emri_id>/', export_mutabakat_excel, name='export_mutabakat_excel'),
    
    # Yönetim araçları (yonetim_araclari, reset_sayim_data, upload_and_reload_stok_data) 
    # views.py'da olmadığı için URL listesinden KALDIRILMIŞTIR / YORUM SATIRI YAPILMIŞTIR.
]