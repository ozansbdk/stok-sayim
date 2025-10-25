# -*- coding: utf-8 -*-

import json
import time
import os
from datetime import datetime
from io import BytesIO
import base64
from io import BytesIO as IO_Bytes 
from decimal import Decimal 

# Django Imports
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse, Http404
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import ListView, CreateView, DetailView, TemplateView
from django.urls import reverse_lazy, re_path # re_path import edildi
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.models import Max, F, Sum, Q 
from django.utils import timezone
from django.utils.translation import gettext as _ 
from django.core.management import call_command
from django.contrib import messages
from django.utils.text import slugify

# from django.contrib.auth import get_user_model # Kaldırılmıştı

# Third-party Imports
from PIL import Image
import pandas as pd
from PIL import Image, ImageFile

# Gemini (Google GenAI) Imports
# Google GenAI kütüphanesi kurulu değilse: pip install google-generativeai
try:
    import google.generativeai as genai
    # google.api_core.exceptions yerine google.generativeai.types.generation_types daha uygun olabilir
    # Import GenerationConfig and Schema for explicit JSON response
    from google.generativeai.types import GenerationConfig, Schema, Type 
    # Hata yakalama için daha spesifik exception'ları veya genel GoogleAPIError'ı import edelim
    # from google.api_core.exceptions import GoogleAPIError 
    # Veya daha genel
    from google.api_core import exceptions as google_exceptions

except ImportError:
    genai = None
    google_exceptions = None
    # Gerekli tipleri None olarak tanımla ki kod hata vermesin
    GenerationConfig, Schema, Type = None, None, None 
    print("UYARI: Google Generative AI kütüphanesi bulunamadı. OCR özelliği çalışmayacak.")
except AttributeError: # google.generativeai.types import edilemezse
     genai = None
     google_exceptions = None
     GenerationConfig, Schema, Type = None, None, None 
     print("UYARI: Google Generative AI kütüphanesinin versiyonu uyumsuz olabilir.")


# Local Imports
# NOT: Bu importları kendi model isimlerinizle eşleştirin!
from .models import SayimEmri, Malzeme, SayimDetay, standardize_id_part, generate_unique_id 
from .forms import SayimGirisForm

# --- SABİTLER ---
# Ortam değişkeninden API anahtarını alıyoruz
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_AVAILABLE = bool(GEMINI_API_KEY and genai) # genai import edilebildi mi kontrolü
ImageFile.LOAD_TRUNCATED_IMAGES = True

# --- GÖRÜNÜMLER (VIEWS) ---

class SayimEmirleriListView(ListView):
    model = SayimEmri
    template_name = 'sayim/sayim_emirleri.html'
    context_object_name = 'emirler'
    ordering = ['-tarih']

class SayimEmriCreateView(CreateView):
    model = SayimEmri
    fields = ['ad', 'atanan_personel'] 
    template_name = 'sayim/sayim_emri_olustur.html'
    success_url = reverse_lazy('sayim_emirleri')

    def form_valid(self, form):
        form.instance.durum = 'Açık'
        return super().form_valid(form)

class PersonelLoginView(TemplateView):
    template_name = 'sayim/personel_login.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sayim_emri_id = self.kwargs.get('sayim_emri_id')
        depo_kodu = self.kwargs.get('depo_kodu')
        sayim_emri = get_object_or_404(SayimEmri, pk=sayim_emri_id)
        context.update({
            'sayim_emri_id': sayim_emri.pk,
            'depo_kodu': depo_kodu,
            'sayim_emri': sayim_emri
        })
        return context

@csrf_exempt
def set_personel_session(request):
    """Personel girişinde görev atama kısıtlaması kontrolü yapar."""
    if request.method == 'POST':
        personel_adi_raw = request.POST.get('personel_adi', '').strip()
        sayim_emri_id = request.POST.get('sayim_emri_id')
        depo_kodu = request.POST.get('depo_kodu')

        if not personel_adi_raw:
             messages.error(request, "Lütfen adınızı girin.")
             return redirect('personel_login', sayim_emri_id=sayim_emri_id, depo_kodu=depo_kodu)

        personel_adi = personel_adi_raw.upper() 
        try:
             sayim_emri_id_int = int(sayim_emri_id) 
        except (ValueError, TypeError):
             messages.error(request, "Sayım Emri ID'si geçersiz formatta.")
             return redirect('sayim_emirleri')

        sayim_emri = get_object_or_404(SayimEmri, pk=sayim_emri_id_int)
        request.session['current_user'] = personel_adi
        atanan_listesi_raw = sayim_emri.atanan_personel.upper()

        if atanan_listesi_raw != 'ATANMADI' and atanan_listesi_raw:
             atananlar = [isim.strip() for isim in atanan_listesi_raw.split(',')]
             if personel_adi not in atananlar:
                 messages.error(request, f"Bu sayım emri sadece {atanan_listesi_raw} kişilerine atanmıştır. Giriş yetkiniz yok.")
                 return redirect('personel_login', sayim_emri_id=sayim_emri_id_int, depo_kodu=depo_kodu)
        
        return redirect('sayim_giris', sayim_emri_id=sayim_emri_id_int, depo_kodu=depo_kodu)
    return redirect('sayim_emirleri')


class DepoSecimView(TemplateView):
    template_name = 'sayim/depo_secim.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sayim_emri_id = kwargs['sayim_emri_id']
        # Depo kodlarını alırken boş veya None olanları filtrele ve standardize et
        lokasyon_listesi = Malzeme.objects.exclude(lokasyon_kodu__isnull=True).exclude(lokasyon_kodu__exact='')\
                                          .values_list('lokasyon_kodu', flat=True).distinct()
        # Standardize edilmiş ve boş olmayanları al, sonra sırala
        context['lokasyonlar'] = sorted([std_loc for loc in lokasyon_listesi if (std_loc := standardize_id_part(loc)) and std_loc != 'YOK'])
        context['sayim_emri_id'] = sayim_emri_id
        return context

class SayimGirisView(DetailView):
    model = SayimEmri
    template_name = 'sayim/sayim_giris.html'
    context_object_name = 'sayim_emri'
    pk_url_kwarg = 'sayim_emri_id'
    slug_url_kwarg = 'depo_kodu'
    slug_field = None 
    
    def get_object(self, queryset=None):
        pk = self.kwargs.get(self.pk_url_kwarg)
        depo_kodu_url = self.kwargs.get(self.slug_url_kwarg) # URL'den depo kodunu al
        if pk is None:
            raise Http404(_("Sayım Emri ID'si URL'de bulunamadı."))
        if queryset is None:
            queryset = self.get_queryset()
        try:
            # Sayım emrini alırken depo kodunu da kontrol edebiliriz (opsiyonel)
            obj = queryset.get(pk=pk)
            # Depo kodunu standardize et (URL'den geldiği için)
            # Gelen depo kodunu decode edip standardize et (örn: %20 -> boşluk)
            from urllib.parse import unquote
            self.standardized_depo_kodu = standardize_id_part(unquote(depo_kodu_url))
            print(f"SayimGirisView - Gelen Depo URL: {depo_kodu_url}, Standardize: {self.standardized_depo_kodu}") # Loglama
            return obj
        except self.model.DoesNotExist:
            raise Http404(_("Sayım Emri pk=%(pk)s ile bulunamadı.") % {'pk': pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sayim_emri_id'] = self.object.pk 
        # View içinde standardize edilmiş depo kodunu context'e aktar
        context['depo_kodu'] = self.standardized_depo_kodu 
        context['personel_adi'] = self.request.session.get('current_user', 'MISAFIR')
        context['gemini_available'] = GEMINI_AVAILABLE
        context['form'] = SayimGirisForm() # Bu form kullanılıyor mu? Evetse import edildi.
        return context

# --- RAPORLAMA VE ANALİZ VIEW'LARI ---

class RaporlamaView(DetailView):
    model = SayimEmri
    pk_url_kwarg = 'sayim_emri_id' # URL'den gelen ID'nin adını belirtiyoruz
    template_name = 'sayim/raporlama.html'
    context_object_name = 'sayim_emri'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sayim_emri = self.object # DetailView objeyi self.object olarak sağlar
        try:
            # Sadece ilgili sayım emrine ait detayları çek
            sayim_detaylari = SayimDetay.objects.filter(sayim_emri=sayim_emri).select_related('benzersiz_malzeme')
            
            # Tüm malzemeleri bir kere çekip, sayılanları map üzerinde toplamak daha verimli
            # Sadece bu depodaki malzemeleri çekmek daha mantıklı olabilir? Yoksa tüm stok mu raporlanıyor?
            # Şimdilik tümünü çekiyoruz.
            tum_malzemeler_dict = {m.benzersiz_id: m for m in Malzeme.objects.all()}
            sayilan_miktarlar = {}
            for detay in sayim_detaylari:
                 # Malzemenin varlığını ve ID'sinin sözlükte olup olmadığını kontrol et
                 # benzersiz_malzeme None olabilir mi? Evetse kontrol ekle.
                 if hasattr(detay, 'benzersiz_malzeme') and detay.benzersiz_malzeme and hasattr(detay.benzersiz_malzeme, 'benzersiz_id') and detay.benzersiz_malzeme.benzersiz_id in tum_malzemeler_dict:
                     malzeme_id = detay.benzersiz_malzeme.benzersiz_id
                     # Miktarların Decimal olduğundan emin ol
                     sayilan_stok = detay.sayilan_stok if isinstance(detay.sayilan_stok, Decimal) else Decimal(str(detay.sayilan_stok or '0.0'))
                     sayilan_miktarlar[malzeme_id] = sayilan_miktarlar.get(malzeme_id, Decimal('0.0')) + sayilan_stok
                 else:
                      # İlişkisiz veya hatalı detay kaydı varsa logla
                      print(f"Uyarı: Sayım Detayı ID {detay.pk} geçersiz veya eksik malzeme ilişkisine sahip.")

            rapor_list = []
            # Tüm malzemeler üzerinden dönerek raporu oluştur
            for malzeme_id, malzeme in tum_malzemeler_dict.items():
                sayilan_mik_dec = sayilan_miktarlar.get(malzeme_id, Decimal('0.0'))
                # Veritabanından gelen değerlerin Decimal olduğundan emin ol
                sistem_mik_dec = malzeme.sistem_stogu if isinstance(malzeme.sistem_stogu, Decimal) else Decimal(str(malzeme.sistem_stogu or '0.0'))
                birim_fiyat_dec = malzeme.birim_fiyat if isinstance(malzeme.birim_fiyat, Decimal) else Decimal(str(malzeme.birim_fiyat or '0.0'))
                
                mik_fark_dec = sayilan_mik_dec - sistem_mik_dec
                tutar_fark_dec = mik_fark_dec * birim_fiyat_dec
                sistem_tutar_dec = sistem_mik_dec * birim_fiyat_dec
                
                fark_mutlak = abs(mik_fark_dec)

                if fark_mutlak < Decimal('0.01'):
                    tag = 'tamam'
                # Hiç sayılmadı durumu: Sistemde var (>0.01), sayımda yok (<0.01)
                elif sistem_mik_dec > Decimal('0.01') and sayilan_mik_dec < Decimal('0.01'):
                    tag = 'hic_sayilmadi'
                # Yeni sayıldı durumu: Sistemde yok (<0.01), sayımda var (>0.01)
                elif sistem_mik_dec < Decimal('0.01') and sayilan_mik_dec > Decimal('0.01'):
                    tag = 'yeni_sayildi' # Yeni durum eklendi
                else: # Diğer tüm fark durumları
                    tag = 'fark_var'
                
                # ZeroDivisionError kontrolü
                mik_yuzde = (mik_fark_dec / sistem_mik_dec) * 100 if sistem_mik_dec > Decimal('0.0') else Decimal('0.0') # Sıfırdan büyükse
                 # Eğer sistem 0 iken sayım yapıldıysa yüzdeyi sonsuz veya 100% gösterme?
                if sistem_mik_dec < Decimal('0.01') and sayilan_mik_dec > Decimal('0.01'):
                     mik_yuzde = Decimal('100.0') # Veya 'Yeni' gibi bir metin? Şimdilik 100%

                
                rapor_list.append({
                    'kod': malzeme.malzeme_kodu, 'ad': malzeme.malzeme_adi, 'parti': malzeme.parti_no,
                    'renk': malzeme.renk, 'birim': malzeme.olcu_birimi,
                    # Depo bilgisini de ekleyelim
                    'depo': malzeme.lokasyon_kodu, 
                    'sistem_mik': f"{sistem_mik_dec:.2f}",
                    'sayilan_mik': f"{sayilan_mik_dec:.2f}",
                    'mik_fark': f"{mik_fark_dec:.2f}",
                    'mik_yuzde': f"{mik_yuzde:.2f}%",
                    'sistem_tutar': f"{sistem_tutar_dec:.2f}",
                    'tutar_fark': f"{tutar_fark_dec:.2f}",
                    'tag': tag
                })
            # Raporu önce fark durumuna, sonra depo koduna, sonra stok koduna göre sırala
            context['rapor_data'] = sorted(rapor_list, key=lambda x: (
                x['tag'] != 'fark_var' and x['tag'] != 'yeni_sayildi', # Önce farklar ve yeniler
                x['tag'] != 'hic_sayilmadi', # Sonra hiç sayılmayanlar
                x['depo'], # Depoya göre grupla
                x['kod'], 
                x['parti'], 
                x['renk']
            )) 
        except Exception as e:
            error_type = type(e).__name__
            print(f"Raporlama Hatası ({error_type}): {e}") # Debugging için
            context['hata'] = f"Raporlama verisi çekilirken bir hata oluştu: {e}"
            context['rapor_data'] = []
        return context


class PerformansAnaliziView(DetailView):
    model = SayimEmri
    pk_url_kwarg = 'sayim_emri_id'
    template_name = 'sayim/analiz_performans.html'
    context_object_name = 'sayim_emri'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sayim_emri_id = self.object.pk
        try:
            # Sadece güncelleme tarihi olanları ve personel adı olanları al
            detaylar = SayimDetay.objects.filter(
                sayim_emri_id=sayim_emri_id, 
                guncellenme_tarihi__isnull=False,
                personel_adi__isnull=False 
            ).exclude(personel_adi__exact='')\
             .order_by('personel_adi', 'guncellenme_tarihi')\
             .values('personel_adi', 'guncellenme_tarihi')

            if not detaylar.exists():
                context['analiz_data'] = []
                context['hata'] = f"Bu emre ait, performans analizi yapılabilecek geçerli sayım kaydı bulunamadı."
                return context

            # Veriyi personel bazında grupla
            personel_verileri = {}
            for d in detaylar:
                personel = d['personel_adi']
                tarih = d['guncellenme_tarihi']
                # Tarih geçerli bir datetime objesi mi kontrol et (Django otomatik yapar ama garanti)
                if isinstance(tarih, datetime):
                    if personel not in personel_verileri:
                        personel_verileri[personel] = []
                    personel_verileri[personel].append(tarih)

            analiz_list = []
            for personel, tarihler in personel_verileri.items():
                toplam_kayit = len(tarihler)
                ortalama_sure_sn = float('inf')
                etiket = 'Yetersiz Kayıt (N=1)'
                toplam_saniye = 0

                if toplam_kayit >= 2:
                    farklar_sn = []
                    for i in range(1, toplam_kayit):
                        t1 = tarihler[i-1]
                        t2 = tarihler[i]
                        # Zaman dilimi farkı varsa veya naive ise UTC'ye çevirerek karşılaştır
                        try:
                             if timezone.is_naive(t1): t1 = timezone.make_aware(t1)
                             if timezone.is_naive(t2): t2 = timezone.make_aware(t2)
                             # UTC'ye çevir
                             # t1_utc = t1.astimezone(timezone.utc)
                             # t2_utc = t2.astimezone(timezone.utc)
                             # fark = (t2_utc - t1_utc).total_seconds()
                             fark = (t2 - t1).total_seconds() # Direkt fark almayı dene, aynı timezone'da olmalılar
                             
                             # Makul farkları al (0sn < fark < 1 saat)
                             if 0 < fark < 3600: 
                                 farklar_sn.append(fark)
                             # else: print(f"Aykırı fark atlandı: {fark} sn") # Debugging
                        except Exception as time_err: 
                             print(f"Uyarı: Zaman farkı hesaplama hatası - {t1} vs {t2} - Hata: {time_err}")
                             continue 
                        
                    if farklar_sn: # Geçerli fark varsa hesapla
                        toplam_saniye = sum(farklar_sn)
                        ortalama_sure_sn = toplam_saniye / len(farklar_sn)
                        dakika = int(ortalama_sure_sn // 60)
                        saniye_kalan = int(ortalama_sure_sn % 60)
                        etiket = f"{dakika:02d} dk {saniye_kalan:02d} sn" # Formatı iyileştir
                    elif toplam_kayit >=2 : # Kayıt var ama geçerli fark yoksa
                        etiket = 'Aykırı Veri (>1 Saat)'


                analiz_list.append({
                    'personel': personel,
                    'toplam_kayit': toplam_kayit,
                    'toplam_sure_sn': f"{toplam_saniye:.2f}",
                    'ortalama_sure_formatli': etiket,
                    'ortalama_sure_sn_raw': ortalama_sure_sn 
                })

            analiz_list.sort(key=lambda x: x['ortalama_sure_sn_raw']) # Sonsuzlar en sona gider
            
            for item in analiz_list:
                 if item['ortalama_sure_sn_raw'] == float('inf'):
                     item['ortalama_sure_sn'] = 'N/A' 
                 else:
                     item['ortalama_sure_sn'] = f"{item['ortalama_sure_sn_raw']:.2f} sn" # Birim ekle
                 del item['ortalama_sure_sn_raw'] 

            context['analiz_data'] = analiz_list
        except Exception as e:
            error_type = type(e).__name__
            print(f"Performans Analizi Hatası ({error_type}): {e}") # Debugging için
            context['analiz_data'] = []
            context['hata'] = f"Performans analizi sırasında hata oluştu: {e}"
        return context

class CanliFarkOzetiView(DetailView):
    model = SayimEmri
    pk_url_kwarg = 'sayim_emri_id'
    template_name = 'sayim/analiz_fark_ozeti.html'
    context_object_name = 'sayim_emri'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sayim_emri = self.object
        try:
            # İlgili sayım emrine ait toplam sayılan miktarları malzeme bazında al
            sayilan_toplamlar = SayimDetay.objects.filter(sayim_emri=sayim_emri)\
                .values('benzersiz_malzeme__benzersiz_id')\
                .annotate(toplam_sayilan=Sum('sayilan_stok'))\
                .order_by('benzersiz_malzeme__benzersiz_id')

            # Dictionary'ye çevir: {malzeme_id: toplam_miktar}
            sayilan_miktarlar_dict = {
                item['benzersiz_malzeme__benzersiz_id']: item['toplam_sayilan'] or Decimal('0.0')
                for item in sayilan_toplamlar if item['benzersiz_malzeme__benzersiz_id']
            }

            # Tüm malzemeleri grup bilgisiyle birlikte çek (Decimal olduğundan emin ol)
            tum_malzemeler = Malzeme.objects.all().values(
                'benzersiz_id', 'stok_grup', 'sistem_stogu', 'birim_fiyat'
            )

            grup_ozet = {}
            # Grupları önceden tanımla ki sıralama tutarlı olsun (isteğe bağlı)
            # tum_gruplar = sorted(list(Malzeme.objects.values_list('stok_grup', flat=True).distinct()))
            # for grup in tum_gruplar:
            #      grup_ozet[grup or 'TANIMSIZ'] = {...} # Başlangıç değerleri

            for malzeme in tum_malzemeler:
                stok_grubu = malzeme['stok_grup'] or 'TANIMSIZ' 
                sistem_mik = malzeme['sistem_stogu'] if isinstance(malzeme['sistem_stogu'], Decimal) else Decimal('0.0')
                birim_fiyat = malzeme['birim_fiyat'] if isinstance(malzeme['birim_fiyat'], Decimal) else Decimal('0.0')
                sayilan_stok = sayilan_miktarlar_dict.get(malzeme['benzersiz_id'], Decimal('0.0'))

                mik_fark = sayilan_stok - sistem_mik
                tutar_fark = mik_fark * birim_fiyat
                sistem_tutar = sistem_mik * birim_fiyat

                if stok_grubu not in grup_ozet:
                    grup_ozet[stok_grubu] = {
                        'sistem_mik_toplam': Decimal('0.0'),
                        'sistem_tutar_toplam': Decimal('0.0'),
                        'sayilan_mik_toplam': Decimal('0.0'),
                        'tutar_fark_toplam': Decimal('0.0'), 
                    }
                
                grup_ozet[stok_grubu]['sistem_mik_toplam'] += sistem_mik
                grup_ozet[stok_grubu]['sistem_tutar_toplam'] += sistem_tutar
                grup_ozet[stok_grubu]['sayilan_mik_toplam'] += sayilan_stok
                grup_ozet[stok_grubu]['tutar_fark_toplam'] += tutar_fark 

            rapor_list = []
            for grup, data in grup_ozet.items():
                mik_fark_toplam = data['sayilan_mik_toplam'] - data['sistem_mik_toplam']
                tutar_fark_toplam = data['tutar_fark_toplam']
                
                rapor_list.append({
                    'grup': grup,
                    'sistem_mik': f"{data['sistem_mik_toplam']:.2f}",
                    'sistem_tutar': f"{data['sistem_tutar_toplam']:.2f}",
                    'fazla_mik': f"{mik_fark_toplam:.2f}" if mik_fark_toplam > 0 else "0.00",
                    'eksik_mik': f"{-mik_fark_toplam:.2f}" if mik_fark_toplam < 0 else "0.00",
                    'fazla_tutar': f"{tutar_fark_toplam:.2f}" if tutar_fark_toplam > 0 else "0.00",
                    'eksik_tutar': f"{-tutar_fark_toplam:.2f}" if tutar_fark_toplam < 0 else "0.00"
                })
            context['analiz_data'] = sorted(rapor_list, key=lambda x: x['grup']) # Gruplara göre sırala
        except Exception as e:
            error_type = type(e).__name__
            print(f"Fark Özeti Hatası ({error_type}): {e}") # Debugging için
            context['hata'] = f"Canlı Fark Özeti çekilirken hata oluştu: {e}"
            context['analiz_data'] = []
        return context

class KonumAnaliziView(DetailView):
    model = SayimEmri
    pk_url_kwarg = 'sayim_emri_id'
    template_name = 'sayim/analiz_konum.html'
    context_object_name = 'sayim_emri'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sayim_emri = self.object
        
        # Geçerli koordinatları filtrele (Sayısal değerler olmalı)
        konum_data_qs = SayimDetay.objects.filter(
            sayim_emri=sayim_emri,
            latitude__isnull=False, 
            longitude__isnull=False,
        ).exclude(latitude__in=['YOK', '']).exclude(longitude__in=['YOK', ''])\
         .values('personel_adi', 'latitude', 'longitude', 'kayit_tarihi', 'sayilan_stok')\
         .order_by('kayit_tarihi') # Sıralama önemli

        markers = []
        gecersiz_koordinat_sayisi = 0
        for item in konum_data_qs:
            try:
                # Sayısal değerlere çevir, virgülü noktaya çevir
                lat_str = str(item['latitude']).replace(',', '.').strip()
                lng_str = str(item['longitude']).replace(',', '.').strip()
                lat = float(lat_str) 
                lng = float(lng_str)
                # Basit bir geçerlilik kontrolü (Türkiye sınırları içinde mi?)
                if not (25 < lng < 45 and 35 < lat < 43): 
                     print(f"Uyarı: Koordinatlar Türkiye sınırı dışında atlandı: Lat={lat}, Lng={lng}")
                     gecersiz_koordinat_sayisi += 1
                     continue

                markers.append({
                    'personel': item['personel_adi'],
                    'lat': lat, 
                    'lng': lng,
                    'tarih': item['kayit_tarihi'].strftime("%Y-%m-%d %H:%M:%S") if item['kayit_tarihi'] else 'Bilinmiyor',
                    # Sayılan stoğun Decimal olduğundan emin ol
                    'stok': float(item['sayilan_stok'] if isinstance(item['sayilan_stok'], Decimal) else Decimal(str(item['sayilan_stok'] or '0.0'))) 
                })
            except (ValueError, TypeError) as coord_err:
                 print(f"Geçersiz koordinat atlandı: Lat='{item['latitude']}', Lng='{item['longitude']}' - Hata: {coord_err}")
                 gecersiz_koordinat_sayisi += 1
                 continue

        context['konum_json'] = json.dumps(markers, cls=DjangoJSONEncoder)
        context['toplam_kayit'] = len(markers)
        # 'YOK' olanları, null olanları veya boş olanları say
        konum_almayan_db = SayimDetay.objects.filter(
            Q(sayim_emri=sayim_emri) & 
            (Q(latitude='YOK') | Q(longitude='YOK') | Q(latitude__exact='') | Q(longitude__exact='') | Q(latitude__isnull=True) | Q(longitude__isnull=True))
        ).count()
        context['konum_almayan_kayitlar'] = konum_almayan_db + gecersiz_koordinat_sayisi # Geçersizleri de ekle
        
        context['hata'] = None
        if not markers:
             context['hata'] = "Bu emre ait haritada gösterilebilir geçerli konum verisi (GPS) bulunamadı."
        elif gecersiz_koordinat_sayisi > 0:
             context['uyari'] = f"{gecersiz_koordinat_sayisi} kaydın koordinatları geçersiz veya Türkiye dışında olduğu için haritada gösterilemedi."

        return context

# Stok Onaylama: pk yerine sayim_emri_id kullanıldı ve mantık iyileştirildi
@csrf_exempt
@transaction.atomic
def stoklari_onayla_ve_kapat(request, sayim_emri_id): 
    """Stokları günceller ve sayım emrini kapatır."""
    sayim_emri = get_object_or_404(SayimEmri, pk=sayim_emri_id)
    
    if request.method != 'POST':
        # GET isteği gelirse raporlama sayfasına geri yönlendir
        return redirect('raporlama_onay', sayim_emri_id=sayim_emri_id)

    if sayim_emri.durum != 'Açık':
        messages.warning(request, "Bu sayım emri zaten kapatılmış veya onaylanmış.")
        return redirect('sayim_emirleri')

    try:
        now = timezone.now()

        # İlgili sayım emrine ait, malzeme bazında toplam sayılan stokları al
        # Gruplama ve toplama işlemini veritabanında yap
        sayilan_toplamlar = SayimDetay.objects.filter(sayim_emri=sayim_emri, benzersiz_malzeme__isnull=False)\
            .values('benzersiz_malzeme__benzersiz_id')\
            .annotate(toplam_sayilan=Sum('sayilan_stok'))\
            .order_by('benzersiz_malzeme__benzersiz_id') # Sıralama gereksiz olabilir ama tutarlılık için kalsın

        # Güncellenecek malzemeleri tutacak dictionary {benzersiz_id: toplam_miktar}
        guncellenecek_stoklar = {
            item['benzersiz_malzeme__benzersiz_id']: item['toplam_sayilan'] or Decimal('0.0')
            for item in sayilan_toplamlar 
        }

        # Güncellenecek ID listesini al
        guncellenecek_ids = list(guncellenecek_stoklar.keys())
        
        updated_count = 0
        skipped_count = 0 # Değişiklik olmayanları saymak için

        # Transaction içinde toplu güncelleme (daha performanslı olabilir ama dikkatli kullanılmalı)
        # Alternatif: Tek tek güncelleme (daha güvenli)
        with transaction.atomic(): 
            # Güncellenecek malzemeleri ID listesi ile çek
            malzemeler_to_update = Malzeme.objects.filter(benzersiz_id__in=guncellenecek_ids)
            
            for malzeme in malzemeler_to_update:
                yeni_stok = guncellenecek_stoklar.get(malzeme.benzersiz_id, None) # ID'ye göre miktarı al
                
                # Eğer ID eşleşmezse (teoride olmamalı) veya miktar None ise atla
                if yeni_stok is None: 
                    print(f"Uyarı: Stok onaylarken {malzeme.benzersiz_id} için sayım miktarı bulunamadı.")
                    skipped_count += 1
                    continue

                # Miktarın Decimal olduğundan emin ol
                if not isinstance(yeni_stok, Decimal):
                    try:
                        yeni_stok = Decimal(str(yeni_stok))
                    except (ValueError, TypeError, Decimal.InvalidOperation):
                         print(f"Uyarı: Stok onaylarken {malzeme.benzersiz_id} için geçersiz miktar ({yeni_stok}), atlanıyor.")
                         skipped_count += 1
                         continue

                # Sadece mevcut stoktan farklıysa güncelle
                if malzeme.sistem_stogu != yeni_stok:
                    malzeme.sistem_stogu = yeni_stok
                    # Birim fiyatın Decimal olduğundan emin ol
                    birim_fiyat = malzeme.birim_fiyat if isinstance(malzeme.birim_fiyat, Decimal) else Decimal('0.0')
                    malzeme.sistem_tutari = yeni_stok * birim_fiyat
                    # update_fields kullanarak sadece değişen alanları kaydet
                    malzeme.save(update_fields=['sistem_stogu', 'sistem_tutari']) 
                    updated_count +=1
                else:
                     skipped_count +=1 # Değişiklik yoksa say

            # Sayım emrini kapat
            sayim_emri.durum = 'Tamamlandı'
            sayim_emri.onay_tarihi = now
            sayim_emri.save(update_fields=['durum', 'onay_tarihi'])

        messages.success(request, f"Sayım emri başarıyla onaylandı. {updated_count} stok güncellendi, {skipped_count} stok aynı kaldı.")
        return redirect('sayim_emirleri')

    except Exception as e:
        error_type = type(e).__name__
        print(f"Stok Onaylama Hatası ({error_type}): {e}") # Debugging
        messages.error(request, f"Stok güncelleme sırasında beklenmedik bir hata oluştu: {e}")
        # Hata durumunda işlemi geri al (transaction sayesinde) ve raporlama sayfasına dön
        return redirect('raporlama_onay', sayim_emri_id=sayim_emri_id)


# --- YÖNETİM ARAÇLARI ---

def yonetim_araclari(request):
    """Veri temizleme ve yükleme araçları sayfasını gösterir."""
    return render(request, 'sayim/yonetim.html', {}) 

# Bu fonksiyon views.py içinde mevcut ve urls.py tarafından çağrılıyor.
@csrf_exempt
@transaction.atomic
def reset_sayim_data(request):
    """Tüm sayım emirlerini ve detaylarını siler (Yönetici aracı)."""
    if request.method == 'POST':
        try:
            # Önce detayları, sonra emirleri sil (ilişki nedeniyle)
            detay_count, _ = SayimDetay.objects.all().delete()
            emir_count, _ = SayimEmri.objects.all().delete()
            message = f'Başarıyla {detay_count} sayım detayı ve {emir_count} sayım emri SIFIRLANDI.'
            return JsonResponse({'success': True, 'message': message})
        except Exception as e:
            return JsonResponse({'success': False, 'message': f'Veri silinirken hata oluştu: {e}'})
    return JsonResponse({'success': False, 'message': 'Geçersiz metot.'}, status=400)


@csrf_exempt
@transaction.atomic # Toplu işlemlerde transaction kullanmak önemli
def upload_and_reload_stok_data(request):
    """
    Excel dosyasını alır, Pandas ile okur ve Malzeme tablosunu günceller/yeni kayıt ekler.
    Daha sağlam hata kontrolü ve veri temizliği eklendi.
    """
    if request.method == 'POST':
        if 'excel_file' not in request.FILES:
            return JsonResponse({'success': False, 'message': 'Yüklenen dosya bulunamadı.'}, status=400)

        excel_file = request.FILES['excel_file']

        if not excel_file.name.endswith(('.xlsx', '.xls', '.csv')):
             return JsonResponse({'success': False, 'message': 'Sadece Excel (.xlsx, .xls) veya CSV dosyaları desteklenir.'}, status=400)

        try:
            file_data = excel_file.read()
            excel_io = IO_Bytes(file_data)
            
            # Dosya tipine göre oku
            if excel_file.name.endswith('.csv'):
                 try:
                    # dtype=str ile oku, keep_default_na=False boşları '' yapar
                    df = pd.read_csv(excel_io, sep=None, encoding='utf-8', engine='python', dtype=str, keep_default_na=False) 
                 except Exception as read_err:
                     try: 
                         excel_io.seek(0) 
                         df = pd.read_csv(excel_io, sep=None, encoding='latin1', engine='python', dtype=str, keep_default_na=False)
                     except Exception as read_err_alt:
                         return JsonResponse({'success': False, 'message': f'CSV okuma hatası: {read_err_alt}. Ayırıcı veya encoding yanlış olabilir.'}, status=400)
            else: 
                 try:
                    df = pd.read_excel(excel_io, engine='openpyxl' if excel_file.name.endswith('.xlsx') else 'xlrd', dtype=str, keep_default_na=False)
                 except Exception as read_err:
                     return JsonResponse({'success': False, 'message': f'Excel okuma hatası: {read_err}. Dosya formatı bozuk veya desteklenmiyor olabilir.'}, status=400)

            # --- VERİ TEMİZLEME ---
            df.columns = df.columns.str.strip() 
            required_cols = ["Stok Kodu", "Depo Kodu", "Miktar", "Maliyet birim"]
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                return JsonResponse({'success': False, 'message': f'Excel dosyasında eksik sütunlar var: {", ".join(missing_cols)}'}, status=400)

            # Varsayılan değerler (String olarak)
            defaults = {
                "Parti": 'YOK', "Renk": 'YOK', "Depo Kodu": 'MERKEZ',
                "Miktar": '0.0', "Maliyet birim": '0.0', "Grup": 'GENEL',
                "Stok Adı": '', "Birim": 'ADET'
            }
            # Eksik sütunları ekle
            for col, default_val in defaults.items():
                 if col not in df.columns: df[col] = default_val

            # Tüm sütunlardaki baştaki/sondaki boşlukları temizle
            df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
            # Boş stringleri varsayılanlarla doldur
            for col, default_val in defaults.items():
                 # Eğer sütun varsa ve boşsa doldur
                 if col in df.columns:
                      df[col] = df[col].replace('', default_val)


            # --- VERİ DÖNÜŞTÜRME VE KONTROL ---
            processed_rows = [] 
            for index, row in df.iterrows():
                 row_num = index + 2 # Excel satır numarası (başlık hariç)
                 try:
                      stok_kod_raw = row['Stok Kodu']
                      depo_kod_raw = row['Depo Kodu']
                      miktar_raw = row['Miktar']
                      maliyet_raw = row['Maliyet birim']
                      
                      # Zorunlu alanlar boş olamaz
                      if not stok_kod_raw or stok_kod_raw == 'YOK':
                           raise ValueError(f"Satır {row_num}: Stok Kodu boş olamaz.")
                      if not depo_kod_raw or depo_kod_raw == 'YOK':
                            raise ValueError(f"Satır {row_num}: Depo Kodu boş olamaz.")

                      # Sayısal alanları Decimal yap
                      miktar_str = str(miktar_raw).replace(',', '.').strip() or '0.0'
                      maliyet_str = str(maliyet_raw).replace(',', '.').strip() or '0.0'
                      miktar_dec = Decimal(miktar_str)
                      maliyet_dec = Decimal(maliyet_str)
                      
                      # İşlenmiş veriyi listeye ekle
                      processed_row = {col: str(row[col]) for col in df.columns if col not in ['Miktar', 'Maliyet birim']}
                      processed_row['Miktar'] = miktar_dec
                      processed_row['Maliyet birim'] = maliyet_dec
                      processed_rows.append(processed_row)

                 except (ValueError, TypeError, Decimal.InvalidOperation) as conv_err:
                      error_msg = f'Satır {row_num}: Veri hatası - "{conv_err}". Miktar="{miktar_raw}", Maliyet="{maliyet_raw}". Lütfen Excel verisini kontrol edin.'
                      print(error_msg) 
                      # Hatalı satırda durmak yerine devam et ama logla? Şimdilik duruyoruz.
                      return JsonResponse({'success': False, 'message': error_msg}, status=400)
                 except KeyError as key_err: # Olmayan sütun adı hatası (nadiren)
                      error_msg = f'Satır {row_num}: Sütun hatası - "{key_err}" bulunamadı.'
                      print(error_msg)
                      return JsonResponse({'success': False, 'message': error_msg}, status=400)


            if not processed_rows: 
                 return JsonResponse({'success': False, 'message': 'Excel\'de işlenecek geçerli veri bulunamadı.'}, status=400)
                 
            # --- VERİTABANI İŞLEMLERİ ---
            success_count = 0
            fail_count = 0
            created_count = 0
            updated_count = 0
            
            with transaction.atomic():
                for index, row_data in enumerate(processed_rows): 
                    row_num = index + 2
                    try:
                        # Standardize et
                        stok_kod = standardize_id_part(row_data['Stok Kodu'])
                        parti_no = standardize_id_part(row_data['Parti']) 
                        renk = standardize_id_part(row_data['Renk'])
                        lokasyon_kodu = standardize_id_part(row_data['Depo Kodu']) 
                        
                        # Standardize sonrası tekrar kontrol (YOK olmamalı)
                        if stok_kod == 'YOK' or lokasyon_kodu == 'YOK':
                             print(f"Satır {row_num}: Standardizasyon sonrası geçersiz kod (Stok:'{stok_kod}', Depo:'{lokasyon_kodu}'). Atlanıyor.")
                             fail_count += 1
                             continue

                        benzersiz_id = generate_unique_id(stok_kod, parti_no, lokasyon_kodu, renk)
                        
                        sistem_miktari = row_data['Miktar'] 
                        birim_fiyati = row_data['Maliyet birim'] 
                        stok_grubu = row_data['Grup'] or 'GENEL' 
                        stok_adi = row_data['Stok Adı'] if row_data['Stok Adı'] else f"Stok {stok_kod}"
                        birim = row_data['Birim'] or 'ADET'
                        
                        # Var olanı güncelle veya yenisini yarat
                        obj, created = Malzeme.objects.update_or_create(
                            benzersiz_id=benzersiz_id,
                            defaults={
                                'malzeme_kodu': stok_kod, 'malzeme_adi': stok_adi,
                                'parti_no': parti_no, 'renk': renk,
                                'lokasyon_kodu': lokasyon_kodu, 'olcu_birimi': birim,
                                'stok_grup': stok_grubu, 'sistem_stogu': sistem_miktari,
                                'birim_fiyat': birim_fiyati, 
                                'sistem_tutari': sistem_miktari * birim_fiyati 
                            }
                        )
                        success_count += 1
                        if created: created_count += 1
                        else: updated_count +=1
                        
                    except Exception as e:
                        print(f"Satır {row_num} DB hatası ({type(e).__name__}): {e} - Veri: {row_data}") 
                        fail_count += 1
                        continue 
            
            message = f"✅ Yükleme Tamamlandı: {created_count} yeni, {updated_count} güncellenen. Atlanan/Hatalı: {fail_count}."
            return JsonResponse({'success': True, 'message': message})

        except Exception as e:
            print(f"Excel Yükleme Kritik Hatası ({type(e).__name__}): {e}") 
            return JsonResponse({'success': False, 'message': f'Stok yükleme sırasında beklenmedik hata: {e}'}, status=500)

    return JsonResponse({'success': False, 'message': 'Geçersiz istek metodu.'}, status=400)


# --- AJAX FONKSİYONLARI ---

# Yardımcı Fonksiyon: Son sayım bilgisini getirir
def get_last_sayim_info(malzeme_nesnesi): 
    if not malzeme_nesnesi: return None
    last_sayim = SayimDetay.objects.filter(benzersiz_malzeme=malzeme_nesnesi)\
                                   .order_by('-kayit_tarihi').first() 
    if last_sayim:
        tarih_str = last_sayim.kayit_tarihi.strftime("%d %b %H:%M") if last_sayim.kayit_tarihi else 'Bilinmiyor'
        return { 'tarih': tarih_str, 'personel': last_sayim.personel_adi or 'Bilinmiyor' }
    return None

# ####################################################################################
# ⭐ AKILLI ARAMA FONKSİYONU - Kesin Çözüm: Benzersiz ID + DEBUG Loglama
# ####################################################################################
@csrf_exempt
def ajax_akilli_stok_ara(request):
    seri_no = standardize_id_part(request.GET.get('seri_no', 'YOK'))
    stok_kod = standardize_id_part(request.GET.get('stok_kod', 'YOK'))
    parti_no = standardize_id_part(request.GET.get('parti_no', 'YOK'))
    renk = standardize_id_part(request.GET.get('renk', 'YOK'))
    depo_kod = standardize_id_part(request.GET.get('depo_kod', 'YOK')) 
    
    print(f"\n--- ARAMA BAŞLADI ---")
    print(f"Gelen Parametreler: Seri='{request.GET.get('seri_no', '')}', Stok='{request.GET.get('stok_kod', '')}', Parti='{request.GET.get('parti_no', '')}', Renk='{request.GET.get('renk', '')}', Depo='{request.GET.get('depo_kod', '')}'")
    print(f"Standardize Edilmiş: Seri='{seri_no}', Stok='{stok_kod}', Parti='{parti_no}', Renk='{renk}', Depo='{depo_kod}'")
    
    response_data = {
        'found': False, 'urun_bilgi': 'Stok veya Barkod bulunamadı.', 'benzersiz_id': None, 
        'stok_kod': 'YOK', 'parti_no': 'YOK', 'renk': 'YOK', 'sistem_stok': '0.00', 
        'sayilan_stok': '0.00', 'last_sayim': 'Bilinmiyor', 'parti_varyantlar': [], 
        'renk_varyantlar': [], 'farkli_depo_uyarisi': '' 
    }
    malzeme = None 
    
    if depo_kod == 'YOK':
        response_data['urun_bilgi'] = 'HATA: Depo kodu belirtilmedi.'
        print(">> ARAMA SONUCU: Depo kodu YOK.")
        return JsonResponse(response_data, status=400)

    # 1. Seri No / Barkod ile TAM EŞLEŞME (benzersiz_id öncelikli)
    if seri_no != 'YOK':
        print(f">> 1. ADIM: Seri No/Benzersiz ID ({seri_no}) ile aranıyor...")
        malzeme = Malzeme.objects.filter(benzersiz_id=seri_no, lokasyon_kodu__iexact=depo_kod).first()
        if not malzeme:
             malzeme = Malzeme.objects.filter(malzeme_kodu__iexact=seri_no, lokasyon_kodu__iexact=depo_kod).first()
        print(f"   -> Sonuç: {'Bulundu: '+malzeme.benzersiz_id if malzeme else 'Bulunamadı'}")

    # 2. Parti No ile Arama
    if not malzeme and parti_no != 'YOK':
        print(f">> 2. ADIM: Parti No ({parti_no}) ile aranıyor (Stok Kodu: {stok_kod})...")
        query_params = {'parti_no__iexact': parti_no, 'lokasyon_kodu__iexact': depo_kod}
        if stok_kod != 'YOK': query_params['malzeme_kodu__iexact'] = stok_kod
        malzeme = Malzeme.objects.filter(**query_params).first() 
        print(f"   -> Sonuç: {'Bulundu: '+malzeme.benzersiz_id if malzeme else 'Bulunamadı'}")

    # 3. Stok Kodu + Parti No + Renk ile Tam Eşleşme
    if not malzeme and stok_kod != 'YOK' and parti_no != 'YOK' and renk != 'YOK':
        print(f">> 3. ADIM: Stok ({stok_kod}), Parti ({parti_no}), Renk ({renk}) ile aranıyor...")
        malzeme = Malzeme.objects.filter(
            malzeme_kodu__iexact=stok_kod, parti_no__iexact=parti_no,
            renk__iexact=renk, lokasyon_kodu__iexact=depo_kod
        ).first()
        print(f"   -> Sonuç: {'Bulundu: '+malzeme.benzersiz_id if malzeme else 'Bulunamadı'}")

    # 4. Sadece Stok Kodu ile Arama (Varyant Listeleme)
    if not malzeme and stok_kod != 'YOK':
        print(f">> 4. ADIM: Sadece Stok Kodu ({stok_kod}) ile varyant aranıyor...")
        varyantlar = Malzeme.objects.filter(malzeme_kodu__iexact=stok_kod, lokasyon_kodu__iexact=depo_kod)
        varyant_count = varyantlar.count()
        print(f"   -> {varyant_count} varyant bulundu.")
        if varyant_count == 1:
            malzeme = varyantlar.first()
            print(f"      -> Tek varyant bulundu ve seçildi: {malzeme.benzersiz_id}")
        elif varyant_count > 1:
            response_data['urun_bilgi'] = f"Varyant Seçimi Gerekli: {stok_kod} ({varyant_count} varyant)"
            response_data['stok_kod'] = stok_kod 
            partiler = sorted(list(varyantlar.values_list('parti_no', flat=True).distinct()))
            renkler = sorted(list(varyantlar.values_list('renk', flat=True).distinct()))
            response_data['parti_varyantlar'] = [p for p in partiler if p != 'YOK']
            response_data['renk_varyantlar'] = [r for r in renkler if r != 'YOK']
            print(f"      -> Birden fazla varyant bulundu, liste döndürülüyor.")
            print(f"--- ARAMA BİTTİ (Varyant Listesi) ---")
            return JsonResponse(response_data) 
            
    # NİHAİ SONUÇ İŞLEME
    if malzeme:
        print(f">> NİHAİ SONUÇ: Malzeme bulundu: {malzeme.benzersiz_id}. Detaylar işleniyor...")
        toplam_sayilan = Decimal('0.0') 
        sayim_emri_id_str = request.GET.get('sayim_emri_id') 
        if sayim_emri_id_str:
            try:
                sayim_emri_id_int = int(sayim_emri_id_str)
                toplam_sayilan = SayimDetay.objects.filter(sayim_emri_id=sayim_emri_id_int, benzersiz_malzeme=malzeme)\
                               .aggregate(total_sayilan=Sum('sayilan_stok'))['total_sayilan'] or Decimal('0.0')
            except (ValueError, TypeError): 
                 print(f"   -> Uyarı: Geçersiz sayim_emri_id ('{sayim_emri_id_str}') geldi.")
        else: print("   -> Uyarı: Arama isteğinde sayim_emri_id gelmedi.")
        print(f"   -> Bu sayım için toplam: {toplam_sayilan:.2f}")

        diger_depolar = Malzeme.objects.filter(malzeme_kodu__iexact=malzeme.malzeme_kodu)\
            .exclude(lokasyon_kodu__iexact=malzeme.lokasyon_kodu)\
            .values_list('lokasyon_kodu', flat=True).distinct()
        farkli_depo_uyarisi = ""
        if diger_depolar.exists():
            depo_isimleri = ", ".join(sorted([standardize_id_part(d) for d in diger_depolar]))
            farkli_depo_uyarisi = f"⚠️ DİKKAT! Başka depolarda da var: {depo_isimleri}"
            print(f"   -> Farklı depo uyarısı: {farkli_depo_uyarisi}")

        response_data.update({
            'found': True, 'benzersiz_id': malzeme.benzersiz_id, 
            'urun_bilgi': f"{malzeme.malzeme_adi} ({malzeme.malzeme_kodu}) - P:{malzeme.parti_no} R:{malzeme.renk}",
            'stok_kod': malzeme.malzeme_kodu, 'parti_no': malzeme.parti_no, 'renk': malzeme.renk,
            'sistem_stok': f"{malzeme.sistem_stogu:.2f}", 
            'sayilan_stok': f"{toplam_sayilan:.2f}", 
            'last_sayim': get_last_sayim_info(malzeme) or 'Yok', 
            'farkli_depo_uyarisi': farkli_depo_uyarisi
        })
        print(f"--- ARAMA BİTTİ (Başarılı) ---")
        return JsonResponse(response_data)
        
    # HİÇBİR ŞEY BULUNAMADI
    aranan_deger = seri_no if seri_no != 'YOK' else (parti_no if parti_no != 'YOK' else stok_kod)
    response_data['urun_bilgi'] = f"'{aranan_deger}' bilgisi ile '{depo_kod}' deposunda stok bulunamadı."
    print(f">> NİHAİ SONUÇ: Malzeme bulunamadı.")
    print(f"--- ARAMA BİTTİ (Başarısız) ---")
    return JsonResponse(response_data)


# ####################################################################################
# ⭐ KRİTİK REVİZYON: ajax_sayim_kaydet (Kesin Çözüm: Benzersiz ID + DEBUG Loglama)
# ####################################################################################
@csrf_exempt
@transaction.atomic 
def ajax_sayim_kaydet(request, sayim_emri_id):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            gelen_benzersiz_id = data.get('benzersiz_id') 
            
            print(f"\n--- KAYIT BAŞLADI (Benzersiz ID ile) ---")
            print(f"Gelen JSON: {data}")
            print(f"Gelen Benzersiz ID: {gelen_benzersiz_id}")

            if not gelen_benzersiz_id:
                 print(">> KAYIT HATASI: Benzersiz ID gelmedi.")
                 return JsonResponse({'success': False, 'message': "HATA: Kaydedilecek ürün ID'si eksik. Önce arama yapın."}, status=400)

            try:
                miktar_str = str(data.get('miktar', '0.0')).replace(',', '.') 
                miktar = Decimal(miktar_str)
                if miktar <= Decimal('0.0'): # Sıfır veya negatif olamaz
                   print(f">> KAYIT HATASI: Miktar ({miktar}) pozitif değil.")
                   return JsonResponse({'success': False, 'message': "HATA: Miktar pozitif bir sayı olmalıdır."}, status=400) 
            except (ValueError, TypeError, Decimal.InvalidOperation) as e:
                print(f">> KAYIT HATASI: Miktar ({data.get('miktar')}) dönüştürülemedi: {e}")
                return JsonResponse({'success': False, 'message': f"HATA: Geçersiz miktar formatı ('{data.get('miktar')}')."}, status=400)

            personel_adi = data.get('personel_adi', 'MISAFIR').strip().upper() or 'MISAFIR'
            latitude = str(data.get('lat', 'YOK')) 
            longitude = str(data.get('lon', 'YOK'))
           
            # 1. Malzeme ve Sayım Emrini Bul (Benzersiz ID ile)
            try:
                print(f">> Malzeme Aranıyor: Benzersiz ID='{gelen_benzersiz_id}'")
                malzeme = get_object_or_404(Malzeme, benzersiz_id=gelen_benzersiz_id) 
                print(f"   -> Malzeme Bulundu: {malzeme.benzersiz_id} ({malzeme.malzeme_kodu})") 
            except Http404: 
                 hata_mesaji = f"HATA: ID '{gelen_benzersiz_id}' ile eşleşen malzeme bulunamadı."
                 print(f">> KAYIT HATASI: Malzeme Bulunamadı (Http404) - ID: {gelen_benzersiz_id}")
                 return JsonResponse({'success': False, 'message': hata_mesaji}, status=404)
            
            sayim_emri = get_object_or_404(SayimEmri, pk=sayim_emri_id)
            if sayim_emri.durum != 'Açık':
                 print(f">> KAYIT HATASI: Sayım Emri ({sayim_emri_id}) durumu Açık değil: {sayim_emri.durum}")
                 return JsonResponse({'success': False, 'message': 'Sayım emri kapalı, kayıt yapılamaz.'}, status=403) 

            # 2. Yeni Sayım Detayını Oluştur
            print(f">> Sayım Detayı Oluşturuluyor: Miktar={miktar}, Personel={personel_adi}...")
            SayimDetay.objects.create(
                sayim_emri=sayim_emri,
                benzersiz_malzeme=malzeme, 
                personel_adi=personel_adi,
                sayilan_stok=miktar,
                latitude=latitude,       
                longitude=longitude      
            )
            print("   -> Sayım Detayı Başarıyla Oluşturuldu.")

            # 3. Güncel toplamı hesapla (Sadece bu sayım emri için)
            toplam_sayilan = SayimDetay.objects.filter(sayim_emri=sayim_emri, benzersiz_malzeme=malzeme)\
                               .aggregate(total_sayilan=Sum('sayilan_stok'))['total_sayilan'] or Decimal('0.0')
            print(f"   -> Yeni Toplam (Bu Emir İçin): {toplam_sayilan}")
            
            print(f"--- KAYIT BİTTİ (Başarılı) ---")
            return JsonResponse({
                'success': True, 
                'message': f"✅ {malzeme.malzeme_kodu} ({malzeme.parti_no}) {miktar:.2f} kayıt edildi.",
                'yeni_miktar': f"{toplam_sayilan:.2f}" 
            })

        except SayimEmri.DoesNotExist:
             print(f">> KAYIT HATASI: SayimEmri.DoesNotExist - ID: {sayim_emri_id}")
             return JsonResponse({'success': False, 'message': "HATA: Geçersiz Sayım Emri ID'si."}, status=404)
        except json.JSONDecodeError as e:
             print(f">> KAYIT HATASI: json.JSONDecodeError - {e}")
             return JsonResponse({'success': False, 'message': "HATA: Geçersiz JSON verisi."}, status=400)
        except Exception as e:
             error_type = type(e).__name__
             print(f">> Kritik Kayıt Hatası ({error_type}): {e}") 
             user_message = f"Beklenmedik sunucu hatası ({error_type}). Yöneticiye bildirin."
             return JsonResponse({'success': False, 'message': user_message}, status=500)

    print(f"KAYIT HATASI: Geçersiz metot ({request.method})")
    return JsonResponse({'success': False, 'message': 'Geçersiz istek metodu (POST bekleniyor).'}, status=405) 


# ####################################################################################
# ⭐ GEMINI OCR ANALİZ FONKSİYONU - Model ve Config Düzeltmesi (Stabil versiyon)
# ####################################################################################

@csrf_exempt
@require_POST
def gemini_ocr_analiz(request):
    if not GEMINI_AVAILABLE:
        return JsonResponse({'success': False, 'message': "Gemini API özelliği aktif değil."}, status=501) 
    if 'image_file' not in request.FILES:
        return JsonResponse({'success': False, 'message': "Görsel dosyası yüklenmedi."}, status=400)

    try:
        image_file = request.FILES['image_file']
        
        if image_file.size > 5 * 1024 * 1024:
             return JsonResponse({'success': False, 'message': "Görsel boyutu çok büyük (Maks 5MB)."}, status=413) 

        try:
            img_bytes = BytesIO(image_file.read())
            img = Image.open(img_bytes)
        except Exception as img_err:
             print(f"OCR GÖRSEL AÇMA HATASI: {img_err}")
             return JsonResponse({'success': False, 'message': f"Görsel dosyası açılamadı veya desteklenmiyor: {img_err}"}, status=400)

        genai.configure(api_key=GEMINI_API_KEY) 
        model_name = 'gemini-1.5-flash' # Flash modeli generateContent ile JSON yanıtı için daha uygun
        model = genai.GenerativeModel(model_name) 

        system_instruction = (
            "You are an expert Optical Character Recognition (OCR) and data extraction system specialized in inventory labels. "
            "Analyze the labels in the image. For each distinct label, extract 'stok_kod' (stock code), 'parti_no' (batch number), "
            "'renk' (color/variant), and 'miktar' (quantity). "
            "If a field is not present on the label, use the value 'YOK'. "
            "Always return the quantity ('miktar') as a decimal number (e.g., 1.0, 500.0). Use '.' as the decimal separator."
            "Respond ONLY with a valid JSON list (array) of objects, where each object represents one label. Provide no other text or explanation."
        )
        prompt = (
            "Analyze all inventory labels in this image. Create a JSON list (array) using the fields 'stok_kod', 'parti_no', 'renk', and 'miktar' for each label found. "
            "Remember: Return 'miktar' as a decimal number (float)."
        )

        # JSON yanıtını zorlamak için Generation Config (google-generativeai kütüphanesi için)
        generation_config = GenerationConfig(
            response_mime_type="application/json",
            response_schema=Schema(
                type=Type.ARRAY,
                items=Schema(
                    type=Type.OBJECT,
                    properties={
                        'stok_kod': Schema(type=Type.STRING),
                        'parti_no': Schema(type=Type.STRING),
                        'renk': Schema(type=Type.STRING),
                        'miktar': Schema(type=Type.NUMBER)
                    },
                    required=['stok_kod'] # Stok kodu zorunlu, diğerleri opsiyonel olabilir
                )
            )
        )

        print(f"Gemini API Çağrısı Başlatılıyor: Model={model_name}")
        response = model.generate_content([prompt, img], generation_config=generation_config)
        print("Gemini API Yanıtı Alındı.")

        try:
            # Yanıtın text kısmını alıp JSON olarak parse et
            json_text = response.text 
            json_results = json.loads(json_text.strip()) 
            
            if not isinstance(json_results, list):
                 raise json.JSONDecodeError("API'den beklenen liste formatı gelmedi.", json_text, 0)
            print(f"Gemini Yanıtı Başarıyla Parse Edildi: {len(json_results)} sonuç bulundu.")

        except (json.JSONDecodeError, IndexError, AttributeError, TypeError) as json_err: # TypeError eklendi
             # Yanıtın kendisini veya parts'ını logla
             raw_response_text = "BOŞ"
             try: raw_response_text = response.text
             except: pass
             print(f"Gemini JSON Parse/İşleme Hatası: {json_err}. Gelen Yanıt Metni: '{raw_response_text}'") 
             return JsonResponse({'success': False, 'message': f"YZ'den gelen yanıt işlenemedi (JSON format hatası olabilir)."}, status=500)
        except Exception as resp_err: 
             print(f"Gemini Yanıt İşleme Beklenmedik Hata: {resp_err}, Yanıt Obj: {response}") 
             return JsonResponse({'success': False, 'message': f"YZ yanıtı işlenirken beklenmedik hata: {resp_err}"}, status=500)

        # Sonuçları işle ve standardize et
        processed_results = []
        print("OCR Sonuçları İşleniyor...")
        for i, item in enumerate(json_results):
             if not isinstance(item, dict): 
                  print(f"   -> Sonuç {i+1} atlandı (dictionary değil).")
                  continue 

             try:
                 miktar_raw = item.get('miktar', '0.0') 
                 if isinstance(miktar_raw, (int, float)):
                      miktar_decimal = Decimal(miktar_raw)
                 elif isinstance(miktar_raw, str):
                      miktar_str = miktar_raw.replace(',', '.').strip().upper()
                      if miktar_str == 'YOK' or not miktar_str:
                           miktar_decimal = Decimal('0.0')
                      else:
                           miktar_decimal = Decimal(miktar_str)
                 else: 
                     miktar_decimal = Decimal('0.0')

             except (ValueError, TypeError, Decimal.InvalidOperation):
                 print(f"   -> Sonuç {i+1}: Miktar ('{item.get('miktar')}') geçersiz, varsayılan 1.0 kullanıldı.")
                 miktar_decimal = Decimal('1.0') # Varsayılan 1 ata

             stok_kod_std = standardize_id_part(item.get('stok_kod', 'YOK'))
             if stok_kod_std == 'YOK': 
                  print(f"   -> Sonuç {i+1} atlandı (Stok Kodu YOK).")
                  continue 

             processed_results.append({
                 'stok_kod': stok_kod_std,
                 'parti_no': standardize_id_part(item.get('parti_no', 'YOK')),
                 'renk': standardize_id_part(item.get('renk', 'YOK')),
                 'miktar': f"{miktar_decimal:.2f}",
                 'barkod': stok_kod_std # Barkodu şimdilik stok koduyla aynı tutuyoruz
             })
             print(f"   -> Sonuç {i+1} işlendi: Stok={stok_kod_std}, Miktar={miktar_decimal:.2f}")


        count = len(processed_results)
        if count == 0:
            print("OCR SONUÇ: Geçerli etiket bulunamadı.")
            return JsonResponse({'success': True, 'message': "Analiz başarılı, ancak görselde geçerli (Stok Kodu olan) etiket bulunamadı.", 'count': 0, 'results': []})

        print(f"OCR SONUÇ: {count} geçerli etiket bulundu.")
        return JsonResponse({
            'success': True,
            'message': f"✅ Gemini ile {count} etiket başarıyla okundu.",
            'count': count,
            'results': processed_results
        })

    # Hata yakalamayı google_exceptions üzerinden yap
    except google_exceptions.GoogleAPICallError as e: if google_exceptions else Exception as e: 
        print(f"Gemini API Hatası: {e}") 
        error_detail = str(e)
        user_message = f"Gemini API ile iletişim hatası oluştu. Lütfen API anahtarınızı, kotanızı veya model adını kontrol edin."
        if "API key not valid" in error_detail or "PERMISSION_DENIED" in error_detail:
             user_message = "Gemini API anahtarı geçersiz veya yetki sorunu. Yönetici ile iletişime geçin."
        elif "quota" in error_detail.lower() or "RESOURCE_EXHAUSTED" in error_detail:
             user_message = "Gemini API kullanım kotası aşıldı."
        elif "model" in error_detail.lower() and ("not found" in error_detail.lower() or "is not supported" in error_detail.lower()):
             user_message = f"Kullanılan Gemini modeli ('{model_name}') bulunamadı veya bu işlem için desteklenmiyor."
        # Diğer 4xx/5xx hataları için genel mesaj
        elif hasattr(e, 'grpc_status_code'): # gRPC hatasıysa
              user_message = f"Gemini API sunucu hatası ({e.grpc_status_code}). Lütfen tekrar deneyin."


        return JsonResponse({'success': False, 'message': user_message}, status=502) 
    except Exception as e:
        error_type = type(e).__name__
        print(f"Kritik YZ Analiz Hatası ({error_type}): {e}") 
        return JsonResponse({'success': False, 'message': f"Görsel analizi sırasında beklenmedik sunucu hatası ({error_type})."}, status=500)


# --- EXCEL EXPORT FONKSİYONLARI ---
@csrf_exempt
def export_excel(request, sayim_emri_id): 
    sayim_emri = get_object_or_404(SayimEmri, pk=sayim_emri_id)
    # Gerçek Excel oluşturma kodu buraya gelmeli (RaporlamaView'daki mantık kullanılabilir)
    # ...
    return HttpResponse(f"'{sayim_emri.ad}' için Excel Raporu İndirme İşlevi Henüz Uygulanmadı.", status=501)

@csrf_exempt
def export_mutabakat_excel(request, sayim_emri_id): 
    sayim_emri = get_object_or_404(SayimEmri, pk=sayim_emri_id)
    # Gerçek Mutabakat Excel oluşturma kodu buraya gelmeli
    # ...
    return HttpResponse(f"'{sayim_emri.ad}' için Mutabakat Excel İndirme İşlevi Henüz Uygulanmadı.", status=501)