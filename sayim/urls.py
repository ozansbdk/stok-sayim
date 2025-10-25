from django.urls import path, re_path # re_path import edildi

# views.py dosyasındaki TÜM AKTİF fonksiyonları ve Class-Based View'ları (CBV) buraya import ediyoruz.
from .views import (
    SayimEmirleriListView, SayimEmriCreateView, PersonelLoginView, 
    set_personel_session, DepoSecimView, SayimGirisView, 
    RaporlamaView, PerformansAnaliziView, CanliFarkOzetiView, KonumAnaliziView, 
    stoklari_onayla_ve_kapat, yonetim_araclari, reset_sayim_data, 
    
    # Excel Yükleme/Kurulum Fonksiyonları
    upload_and_reload_stok_data, 
    
    # AJAX Fonksiyonları ve Export
    ajax_akilli_stok_ara, ajax_sayim_kaydet, 
    gemini_ocr_analiz, export_excel, export_mutabakat_excel
)

urlpatterns = [
    # ----------------------------------------
    # 1. ANA AKIŞ VE EMİR YÖNETİMİ
    # ----------------------------------------
    path('', SayimEmirleriListView.as_view(), name='sayim_emirleri'),
    path('yeni/', SayimEmriCreateView.as_view(), name='yeni_sayim_emri'),

    # 2. PERSONEL GİRİŞİ VE SAYIM
    path('login-personel/<int:sayim_emri_id>/<str:depo_kodu>/', PersonelLoginView.as_view(), name='personel_login'),
    path('set-personel-session/', set_personel_session, name='set_personel_session'),
    path('<int:sayim_emri_id>/depo-secim/', DepoSecimView.as_view(), name='depo_secim'),
    
    # ⭐ KRİTİK ÇÖZÜM: SAYIM GİRİŞİ URL'Sİ
    # View'daki pk_url_kwarg ayarı ile uyumlu olması için sayim_emri_id kullanıldı.
    path('sayim/<int:sayim_emri_id>/<str:depo_kodu>/', SayimGirisView.as_view(), name='sayim_giris'),
    
    # 3. RAPORLAMA VE ANALİZ (Tüm pk'lar sayim_emri_id ile değiştirildi)
    path('rapor/<int:sayim_emri_id>/', RaporlamaView.as_view(), name='raporlama_onay'),
    path('analiz/performans/<int:sayim_emri_id>/', PerformansAnaliziView.as_view(), name='analiz_performans'),
    path('analiz/fark-ozeti/<int:sayim_emri_id>/', CanliFarkOzetiView.as_view(), name='canli_fark_ozeti'),
    path('analiz/konum/<int:sayim_emri_id>/', KonumAnaliziView.as_view(), name='analiz_konum'),

    # 4. YÖNETİM VE VERİ İŞLEMLERİ
    path('stoklari-onayla/<int:sayim_emri_id>/', stoklari_onayla_ve_kapat, name='stoklari_onayla'),
    path('yonetim-araclari/', yonetim_araclari, name='yonetim_araclari'),
    path('reset-sayim-data/', reset_sayim_data, name='reset_sayim_data'),

    # Excel Yükleme ve İndirme
    path('upload-stok-excel/', upload_and_reload_stok_data, name='upload_stok_excel'), 
    path('export/excel/<int:sayim_emri_id>/', export_excel, name='export_excel'),
    path('export/mutabakat-excel/<int:sayim_emri_id>/', export_mutabakat_excel, name='export_mutabakat_excel'),
    
    # 5. AJAX Endpoints
    path('ajax/akilli-stok-ara/', ajax_akilli_stok_ara, name='ajax_akilli_stok_ara'),
    
    # AJAX SAYIM KAYDETME (404 HATASI ÇÖZÜMÜ: Sonda / opsiyonel)
    # path yerine re_path kullanılarak sonda / işaretinin olup olmamasına bakılmaz.
    re_path(r'^ajax/sayim-kaydet/(?P<sayim_emri_id>[0-9]+)/?$', ajax_sayim_kaydet, name='ajax_sayim_kaydet'), 
    
    path('ajax/ocr-analiz/', gemini_ocr_analiz, name='gemini_ocr_analiz'),
]

