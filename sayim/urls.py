from django.urls import path
from .views import (
    index,
    kamera_yukle,
    OCRView,
    upload_excel,
    ocr_upload_and_match,
    kayit_ekle,
    kayit_listesi,
    export_excel
    # 'not_found' bir 404 handler'ıdır, normalde urlpatterns'e eklenmez.
)

app_name = 'sayim'

urlpatterns = [
    # ----------------------------------------
    # views.py dosyanızda GERÇEKTEN VAR OLAN path'ler:
    # ----------------------------------------
    path('', index, name='index'), 
    path('kamera-yukle/', kamera_yukle, name='kamera_yukle'),
    
    # OCR & Barkod & Eşleşme
    path('ocr/', OCRView.as_view(), name='ocr_view'),
    path('ocr-match/', ocr_upload_and_match, name='ocr_upload_and_match'),
    
    # Excel Yükleme
    path('upload-excel/', upload_excel, name='upload_excel'),

    # Kayıt Ekleme & Listeleme
    path('kayit-ekle/', kayit_ekle, name='kayit_ekle'),
    path('kayit-listesi/', kayit_listesi, name='kayit_listesi'),
    path('export-excel/', export_excel, name='export_excel'),
]