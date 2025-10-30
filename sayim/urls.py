# sayim/urls.py
from django.urls import path, re_path 
from django.views.generic import TemplateView # Tahmini: Eğer hala CBV kullanıyorsanız

# views.py dosyasındaki TÜM AKTİF view'ları (görünümleri) içe aktarıyoruz.
# Eksik veya fazla olan view'larınız olabilir, lütfen kendi views.py dosyanıza göre kontrol edin.
from .views import (
    # 1. Ana Akış Fonksiyonları
    sayim_emri_listesi,   # <<< SayimEmirleriListView yerine düzeltildi
    sayim_giris,
    raporlama_onay,
    
    # 2. AJAX Fonksiyonları
    ajax_akilli_stok_ara, 
    ajax_sayim_kaydet,
    gemini_ocr_analiz,
    
    # 3. Raporlama
    export_mutabakat_excel,
    
    # 4. Diğer Tahmini View'lar (Eğer views.py'da mevcutsa)
    PersonelLoginView, 
    SayimEmriCreateView, 
    set_personel_session, 
    DepoSecimView, 
    yonetim_araclari, 
    reset_sayim_data, 
    upload_and_reload_stok_data,
)


urlpatterns = [
    # ----------------------------------------
    # 1. ANA AKIŞ VE EMİR YÖNETİMİ
    # ----------------------------------------
    # Ana Sayfa: Aktif Sayım Emirleri Listesi
    path('', sayim_emri_listesi, name='sayim_emirleri'),
    
    # Sayım Giriş Ekranı (UUID ile)
    path('giris/<uuid:sayim_emri_id>/', sayim_giris, name='sayim_giris'),
    
    # Raporlama ve Onay Ekranı (UUID ile)
    path('rapor/<uuid:sayim_emri_id>/', raporlama_onay, name='raporlama_onay'),
    
    # ----------------------------------------
    # 2. AJAX ENDPOINT'LERİ (veri çekme ve kaydetme)
    # ----------------------------------------
    # Stok Arama (GET)
    path('ajax/akilli-stok-ara/', ajax_akilli_stok_ara, name='ajax_akilli_stok_ara'),
    
    # Sayım Kaydetme (POST)
    path('ajax/sayim-kaydet/<uuid:sayim_emri_id>/', ajax_sayim_kaydet, name='ajax_sayim_kaydet'),
    
    # Gemini OCR Analiz
    path('ajax/gemini-ocr/', gemini_ocr_analiz, name='gemini_ocr_analiz'),

    # ----------------------------------------
    # 3. YÖNETİM VE AUTHENTICATION (TAHMİNİ)
    # ----------------------------------------
    
    # Excel Dışa Aktarma
    path('export/mutabakat/<uuid:sayim_emri_id>/', export_mutabakat_excel, name='export_mutabakat_excel'),
    
    # Yeni Sayım Emri Oluşturma (CBV kullanıyorsa)
    path('yeni-emir/', SayimEmriCreateView.as_view(), name='yeni_sayim_emri'),

    # Kullanıcı Yönetimi
    path('login/', PersonelLoginView.as_view(), name='login'),
    path('set-session/', set_personel_session, name='set_personel_session'),
    path('depo-secim/', DepoSecimView.as_view(), name='depo_secim'),

    # Yönetim Araçları
    path('admin-tools/', yonetim_araclari, name='yonetim_araclari'),
    path('admin-tools/reset/', reset_sayim_data, name='reset_sayim_data'),
    path('admin-tools/upload-stok/', upload_and_reload_stok_data, name='upload_stok_data'),
]