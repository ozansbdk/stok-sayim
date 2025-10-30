# sayim/urls.py
from django.urls import path, re_path 
from django.views.generic import TemplateView # Eğer hala bazı basit CBV'ler kullanılıyorsa kalsın

# views.py dosyasından içe aktarılan TÜM AKTİF view'lar (fonksiyonlar ve CBV'ler)
from .views import (
    # 1. ANA AKIŞ FONKSİYONLARI (CBV'ler yerine)
    sayim_emri_listesi,   # SayimEmirleriListView yerine
    sayim_giris,          # SayimGirisView yerine
    raporlama_onay,
    
    # 2. AJAX FONKSİYONLARI
    ajax_akilli_stok_ara, 
    ajax_sayim_kaydet,
    gemini_ocr_analiz,
    
    # 3. YÖNETİM, AUTH ve DİĞERLERİ
    export_mutabakat_excel,
    yonetim_araclari, 
    reset_sayim_data, 
    upload_and_reload_stok_data,
    set_personel_session, # Login sonrası session ayarlama
    
    # Not: views.py'da fonksiyonel karşılığı varsa bu CBV'lerin adını DÜZELTİN.
    # Örnek olarak PersonelLoginView ve SayimEmriCreateView fonksiyonel varsayıldı:
    personel_login,  # Eğer views.py'da bu adla fonksiyon varsa
    yeni_sayim_emri, # Eğer views.py'da bu adla fonksiyon varsa
    depo_secim,      # Eğer views.py'da bu adla fonksiyon varsa
)


urlpatterns = [
    # ----------------------------------------
    # 1. ANA AKIŞ VE EMİR YÖNETİMİ
    # ----------------------------------------
    path('', sayim_emri_listesi, name='sayim_emirleri'),
    
    # Sayım Giriş ve Raporlama (UUID veya INT kullanıyorsanız doğru formatı seçin. UUID varsayıyorum.)
    path('giris/<uuid:sayim_emri_id>/', sayim_giris, name='sayim_giris'),
    path('rapor/<uuid:sayim_emri_id>/', raporlama_onay, name='raporlama_onay'),
    
    # Yeni Sayım Emri Oluşturma
    path('yeni-emir/', yeni_sayim_emri, name='yeni_sayim_emri'), # Fonksiyonel
    
    # ----------------------------------------
    # 2. AUTH VE YÖNLENDİRME (Fonksiyonel varsayılan)
    # ----------------------------------------
    path('login/', personel_login, name='personel_login'), # PersonelLoginView yerine fonksiyon
    path('set-session/', set_personel_session, name='set_personel_session'),
    path('depo-secim/', depo_secim, name='depo_secim'),
    
    # ----------------------------------------
    # 3. AJAX ENDPOINT'LERİ
    # ----------------------------------------
    path('ajax/akilli-stok-ara/', ajax_akilli_stok_ara, name='ajax_akilli_stok_ara'),
    path('ajax/sayim-kaydet/<uuid:sayim_emri_id>/', ajax_sayim_kaydet, name='ajax_sayim_kaydet'),
    path('ajax/gemini-ocr/', gemini_ocr_analiz, name='gemini_ocr_analiz'),

    # ----------------------------------------
    # 4. YÖNETİM ve RAPORLAMA
    # ----------------------------------------
    path('export/mutabakat/<uuid:sayim_emri_id>/', export_mutabakat_excel, name='export_mutabakat_excel'),
    path('admin-tools/', yonetim_araclari, name='yonetim_araclari'),
    path('admin-tools/reset/', reset_sayim_data, name='reset_sayim_data'),
    path('admin-tools/upload-stok/', upload_and_reload_stok_data, name='upload_stok_data'),
]