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
# re_path import'unu django.urls'dan yapmalıyız
from django.urls import reverse_lazy, re_path 
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
    # Import GenerationConfig and Schema for explicit JSON response
    from google.generativeai.types import GenerationConfig, Schema, Type 
    # Hata yakalama için google.api_core.exceptions kullanmak daha standart
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
            # Sayım emrini al
            obj = queryset.get(pk=pk)
            # Depo kodunu urlden alıp, decode edip standardize et
            from urllib.parse import unquote
            self.standardized_depo_kodu = standardize_id_part(unquote(depo_kodu_url)) if depo_kodu_url else 'YOK'
            print(f"SayimGirisView - Gelen Depo URL: {depo_kodu_url}, Standardize: {self.standardized_depo_kodu}") 
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
        # SayimGirisForm tanımlıysa ve kullanılıyorsa:
        # from .forms import SayimGirisForm # import yukarıda olmalı
        context['form'] = SayimGirisForm() 
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
                     print(f"Uyarı: Raporlamada Sayım Detayı ID {detay.pk} geçersiz malzeme ilişkisine sahip.")

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
                x['tag'] == 'fark_var' or x['tag'] == 'yeni_sayildi', # Önce farklar ve yeniler (True başa gelir)
                x['tag'] == 'hic_sayilmadi', # Sonra hiç sayılmayanlar
                x['depo'], 
                x['kod'], 
                x['parti'], 
                x['renk']
            ), reverse=True) # Farklı olanları en başa almak için reverse=True
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
                             # Django'nun timezone ayarına göre aware yap
                             if timezone.is_naive(t1): t1 = timezone.make_aware(t1)
                             if timezone.is_naive(t2): t2 = timezone.make_aware(t2)
                             fark = (t2 - t1).total_seconds() 
                             
                             # Makul farkları al (0sn < fark < 1 saat)
                             if 0 < fark < 3600: 
                                 farklar_sn.append(fark)
                             # else: print(f"Aykırı fark atlandı: {fark} sn") 
                        except Exception as time_err: 
                             print(f"Uyarı: Zaman farkı hesaplama hatası - {t1} vs {t2} - Hata: {time_err}")
                             continue 
                         
                    if farklar_sn: # Geçerli fark varsa hesapla
                        toplam_saniye = sum(farklar_sn)
                        ortalama_sure_sn = toplam_saniye / len(farklar_sn)
                        dakika = int(ortalama_sure_sn // 60)
                        saniye_kalan = int(ortalama_sure_sn % 60)
                        etiket = f"{dakika:02d} dk {saniye_kalan:02d} sn" 
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
                     item['ortalama_sure_sn'] = f"{item['ortalama_sure_sn_raw']:.2f} sn" 
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
            sayilan_toplamlar = SayimDetay.objects.filter(sayim_emri=sayim_emri, benzersiz_malzeme__isnull=False)\
                 .values('benzersiz_malzeme__benzersiz_id')\
                 .annotate(toplam_sayilan=Sum('sayilan_stok'))

            # Dictionary: {malzeme_id: toplam_miktar}
            sayilan_miktarlar_dict = {
                 item['benzersiz_malzeme__benzersiz_id']: item['toplam_sayilan'] or Decimal('0.0')
                 for item in sayilan_toplamlar 
            }

            # Tüm malzemeleri grup bilgisiyle birlikte çek
            tum_malzemeler = Malzeme.objects.all().values(
                 'benzersiz_id', 'stok_grup', 'sistem_stogu', 'birim_fiyat'
            )

            grup_ozet = {}
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
                        'sistem_mik_toplam': Decimal('0.0'), 'sistem_tutar_toplam': Decimal('0.0'),
                        'sayilan_mik_toplam': Decimal('0.0'), 'tutar_fark_toplam': Decimal('0.0'), 
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
            context['analiz_data'] = sorted(rapor_list, key=lambda x: x['grup']) 
        except Exception as e:
            error_type = type(e).__name__
            print(f"Fark Özeti Hatası ({error_type}): {e}") 
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
        
        konum_data_qs = SayimDetay.objects.filter(
            sayim_emri=sayim_emri, latitude__isnull=False, longitude__isnull=False,
        ).exclude(latitude__in=['YOK', '']).exclude(longitude__in=['YOK', ''])\
         .values('personel_adi', 'latitude', 'longitude', 'kayit_tarihi', 'sayilan_stok')\
         .order_by('kayit_tarihi') 

        markers = []
        gecersiz_koordinat_sayisi = 0
        for item in konum_data_qs:
            try:
                lat_str = str(item['latitude']).replace(',', '.').strip()
                lng_str = str(item['longitude']).replace(',', '.').strip()
                lat = float(lat_str) 
                lng = float(lng_str)
                if not (25 < lng < 45 and 35 < lat < 43): 
                     print(f"Uyarı: Koordinatlar Türkiye sınırı dışında atlandı: Lat={lat}, Lng={lng}")
                     gecersiz_koordinat_sayisi += 1
                     continue
                markers.append({
                    'personel': item['personel_adi'], 'lat': lat, 'lng': lng,
                    'tarih': item['kayit_tarihi'].strftime("%Y-%m-%d %H:%M:%S") if item['kayit_tarihi'] else 'Bilinmiyor',
                    'stok': float(item['sayilan_stok'] if isinstance(item['sayilan_stok'], Decimal) else Decimal(str(item['sayilan_stok'] or '0.0'))) 
                })
            except (ValueError, TypeError) as coord_err:
                 print(f"Geçersiz koordinat atlandı: Lat='{item['latitude']}', Lng='{item['longitude']}' - Hata: {coord_err}")
                 gecersiz_koordinat_sayisi += 1
                 continue

        context['konum_json'] = json.dumps(markers, cls=DjangoJSONEncoder)
        context['toplam_kayit'] = len(markers)
        konum_almayan_db = SayimDetay.objects.filter(
            Q(sayim_emri=sayim_emri) & 
            (Q(latitude='YOK') | Q(longitude='YOK') | Q(latitude__exact='') | Q(longitude__exact='') | Q(latitude__isnull=True) | Q(longitude__isnull=True))
        ).count()
        context['konum_almayan_kayitlar'] = konum_almayan_db + gecersiz_koordinat_sayisi 
        
        context['hata'] = None
        if not markers:
             context['hata'] = "Bu emre ait haritada gösterilebilir geçerli konum verisi (GPS) bulunamadı."
        elif gecersiz_koordinat_sayisi > 0:
             context['uyari'] = f"{gecersiz_koordinat_sayisi} kaydın koordinatları geçersiz veya Türkiye dışında."
        return context

# Stok Onaylama
@csrf_exempt
@transaction.atomic
def stoklari_onayla_ve_kapat(request, sayim_emri_id): 
    sayim_emri = get_object_or_404(SayimEmri, pk=sayim_emri_id)
    if request.method != 'POST': return redirect('raporlama_onay', sayim_emri_id=sayim_emri_id)
    if sayim_emri.durum != 'Açık':
        messages.warning(request, "Bu sayım emri zaten kapalı.")
        return redirect('sayim_emirleri')

    try:
        now = timezone.now()
        sayilan_toplamlar = SayimDetay.objects.filter(sayim_emri=sayim_emri, benzersiz_malzeme__isnull=False)\
             .values('benzersiz_malzeme__benzersiz_id')\
             .annotate(toplam_sayilan=Sum('sayilan_stok'))
        guncellenecek_stoklar = { i['benzersiz_malzeme__benzersiz_id']: i['toplam_sayilan'] or Decimal('0.0') for i in sayilan_toplamlar }
        guncellenecek_ids = list(guncellenecek_stoklar.keys())
        updated_count, skipped_count = 0, 0

        with transaction.atomic(): 
            malzemeler_to_update = Malzeme.objects.filter(benzersiz_id__in=guncellenecek_ids)
            for malzeme in malzemeler_to_update:
                yeni_stok = guncellenecek_stoklar.get(malzeme.benzersiz_id)
                if yeni_stok is None: skipped_count += 1; continue
                try: 
                    # Gelen değer zaten Decimal olmalı (Sum('sayilan_stok') DecimalField üzerinden)
                    yeni_stok_dec = yeni_stok if isinstance(yeni_stok, Decimal) else Decimal(str(yeni_stok))
                except: 
                    skipped_count += 1; continue
                
                # Malzeme.sistem_stogu da artık Decimal, doğrudan karşılaştırılabilir
                if malzeme.sistem_stogu != yeni_stok_dec:
                    malzeme.sistem_stogu = yeni_stok_dec
                    # birim_fiyat ve sistem_tutari artık models.py içindeki save() metodunda
                    # otomatik hesaplanıyor.
                    malzeme.save(update_fields=['sistem_stogu', 'sistem_tutari']) 
                    updated_count +=1
                else: 
                    skipped_count +=1 
                    
            sayim_emri.durum = 'Tamamlandı'
            sayim_emri.onay_tarihi = now
            sayim_emri.save(update_fields=['durum', 'onay_tarihi'])
            
        messages.success(request, f"Sayım onaylandı. {updated_count} stok güncellendi, {skipped_count} aynı kaldı.")
        return redirect('sayim_emirleri')
    except Exception as e:
        error_type = type(e).__name__
        print(f"Stok Onaylama Hatası ({error_type}): {e}") 
        messages.error(request, f"Stok güncelleme hatası: {e}")
        return redirect('raporlama_onay', sayim_emri_id=sayim_emri_id)


# --- YÖNETİM ARAÇLARI ---
def yonetim_araclari(request): return render(request, 'sayim/yonetim.html', {}) 

@csrf_exempt
@transaction.atomic
def reset_sayim_data(request):
    if request.method == 'POST':
        try:
            detay_count, _ = SayimDetay.objects.all().delete()
            emir_count, _ = SayimEmri.objects.all().delete()
            message = f'Başarıyla {detay_count} detay ve {emir_count} emir SIFIRLANDI.'
            return JsonResponse({'success': True, 'message': message})
        except Exception as e: return JsonResponse({'success': False, 'message': f'Veri silme hatası: {e}'})
    return JsonResponse({'success': False, 'message': 'Geçersiz metot.'}, status=400)

@csrf_exempt
@transaction.atomic 
def upload_and_reload_stok_data(request):
    if request.method == 'POST':
        if 'excel_file' not in request.FILES: return JsonResponse({'success': False, 'message': 'Dosya bulunamadı.'}, status=400)
        excel_file = request.FILES['excel_file']
        if not excel_file.name.endswith(('.xlsx', '.xls', '.csv')): return JsonResponse({'success': False, 'message': 'Sadece Excel/CSV desteklenir.'}, status=400)

        try:
            excel_io = IO_Bytes(excel_file.read())
            if excel_file.name.endswith('.csv'):
                 try: df = pd.read_csv(excel_io, sep=None, encoding='utf-8', engine='python', dtype=str, keep_default_na=False) 
                 except: excel_io.seek(0); df = pd.read_csv(excel_io, sep=None, encoding='latin1', engine='python', dtype=str, keep_default_na=False)
            else: df = pd.read_excel(excel_io, engine='openpyxl' if excel_file.name.endswith('.xlsx') else 'xlrd', dtype=str, keep_default_na=False)
            
            df.columns = df.columns.str.strip() 
            
            # ⭐ DÜZELTME 1: 'seri_no' (veya barkod) sütununu zorunlu alanlara ekle
            # Excel'deki tam sütun adını yazdığınızdan emin olun
            required_cols = ["Stok Kodu", "Depo Kodu", "Miktar", "Maliyet birim", "seri_no"]
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols: 
                # seri_no yoksa uyar ama devam et, barkod varsa onu kullan
                if 'seri_no' in missing_cols and 'barkod' in df.columns:
                    df['seri_no'] = df['barkod'] # barkod'u seri_no olarak kullan
                    print("Uyarı: 'seri_no' sütunu yok, 'barkod' sütunu 'seri_no' olarak kullanılacak.")
                    missing_cols.remove('seri_no') # Listeden çıkar
                
                # Hala eksik sütun varsa hata ver
                if missing_cols:
                    return JsonResponse({'success': False, 'message': f'Eksik sütunlar: {", ".join(missing_cols)}'}, status=400)

            # ⭐ DÜZELTME 1.1: 'seri_no' için de bir default ekle
            defaults = {
                "Parti": 'YOK', "Renk": 'YOK', "Depo Kodu": 'MERKEZ', 
                "Miktar": '0.0', "Maliyet birim": '0.0', "Grup": 'GENEL', 
                "Stok Adı": '', "Birim": 'ADET', "seri_no": 'YOK' # Eklendi
            }
            for col, dv in defaults.items():
                 if col not in df.columns: df[col] = dv
            df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
            for col, dv in defaults.items():
                 if col in df.columns: df[col] = df[col].replace('', dv)

            processed_rows = [] 
            for index, row in df.iterrows():
                 rn = index + 2 
                 try:
                     sk, dk, m, c = row['Stok Kodu'], row['Depo Kodu'], row['Miktar'], row['Maliyet birim']
                     if not sk or sk == 'YOK': raise ValueError(f"Stok Kodu boş.")
                     if not dk or dk == 'YOK': raise ValueError(f"Depo Kodu boş.")
                     ms, cs = str(m).replace(',', '.').strip() or '0.0', str(c).replace(',', '.').strip() or '0.0'
                     md, cd = Decimal(ms), Decimal(cs) # models.py'de DecimalField'e geçildiği için bu doğru
                     pr = {col: str(row[col]) for col in df.columns if col not in ['Miktar', 'Maliyet birim']}
                     pr['Miktar'], pr['Maliyet birim'] = md, cd
                     processed_rows.append(pr)
                 except Exception as conv_err:
                     msg = f'Satır {rn}: Veri hatası - "{conv_err}". Miktar="{m}", Maliyet="{c}".'
                     print(msg); return JsonResponse({'success': False, 'message': msg}, status=400)
            if not processed_rows: return JsonResponse({'success': False, 'message': 'Geçerli veri yok.'}, status=400)
                 
            created_count, updated_count, fail_count = 0, 0, 0
            with transaction.atomic():
                # ESKİ MALZEMELERİ SİLME (OPSİYONEL AMA TEMİZLİK İÇİN İYİ)
                # Malzeme.objects.all().delete() 
                # print("Eski tüm malzeme kayıtları silindi.")
                
                for index, rd in enumerate(processed_rows): 
                    rn = index + 2
                    try:
                        sk, pn, rk, lk = map(standardize_id_part, [rd['Stok Kodu'], rd['Parti'], rd['Renk'], rd['Depo Kodu']])
                        if sk == 'YOK' or lk == 'YOK': fail_count += 1; continue
                        bid = generate_unique_id(sk, pn, lk, rk)
                        sm, bf = rd['Miktar'], rd['Maliyet birim']
                        sg, sa, birim = rd['Grup'] or 'GENEL', rd['Stok Adı'] or f"Stok {sk}", rd['Birim'] or 'ADET'
                        
                        # ⭐ DÜZELTME 1.2: 'seri_no'yu defaults'a ekle
                        # Excel'den gelen 'seri_no' (veya 'barkod') değerini al
                        seri_no_val = standardize_id_part(rd.get('seri_no', 'YOK')) 
                        
                        _, created = Malzeme.objects.update_or_create(
                            benzersiz_id=bid, 
                            defaults={
                                'malzeme_kodu': sk, 
                                'malzeme_adi': sa, 
                                'parti_no': pn, 
                                'renk': rk, 
                                'lokasyon_kodu': lk, 
                                'olcu_birimi': birim, 
                                'stok_grup': sg, 
                                'seri_no': seri_no_val, # EKLENDİ
                                'sistem_stogu': sm, 
                                'birim_fiyat': bf
                                # sistem_tutari models.py'deki save() metodunda otomatik hesaplanıyor
                            }
                        )
                        if created: created_count += 1
                        else: updated_count +=1
                    except Exception as e: print(f"Satır {rn} DB hatası: {e}"); fail_count += 1; continue 
            msg = f"✅ Bitti: {created_count} yeni, {updated_count} güncellenen. Hata/Atlanan: {fail_count}."
            return JsonResponse({'success': True, 'message': msg})
        except Exception as e: print(f"Excel Yükleme Hatası: {e}"); return JsonResponse({'success': False, 'message': f'Kritik hata: {e}'}, status=500)
    return JsonResponse({'success': False, 'message': 'Geçersiz metot.'}, status=400)


# --- AJAX FONKSİYONLARI ---
def get_last_sayim_info(malzeme_nesnesi): 
    if not malzeme_nesnesi: return None
    ls = SayimDetay.objects.filter(benzersiz_malzeme=malzeme_nesnesi).order_by('-kayit_tarihi').first() 
    if ls: ts = ls.kayit_tarihi.strftime("%d %b %H:%M") if ls.kayit_tarihi else '?'; return { 'tarih': ts, 'personel': ls.personel_adi or '?'}
    return None

@csrf_exempt
def ajax_akilli_stok_ara(request):
    # Standardize edilmiş değerleri al
    seri_no, stok_kod, parti_no, renk, depo_kod = map(standardize_id_part, [
        request.GET.get('seri_no', 'YOK'), request.GET.get('stok_kod', 'YOK'),
        request.GET.get('parti_no', 'YOK'), request.GET.get('renk', 'YOK'), 
        request.GET.get('depo_kod', 'YOK')
    ])
    
    print(f"\n--- ARAMA --- Gelen: Seri='{request.GET.get('seri_no', '')}', Stok='{request.GET.get('stok_kod', '')}', Parti='{request.GET.get('parti_no', '')}', Renk='{request.GET.get('renk', '')}', Depo='{request.GET.get('depo_kod', '')}' -> Standart: S='{seri_no}', K='{stok_kod}', P='{parti_no}', R='{renk}', D='{depo_kod}'")
    
    response_data = {'found': False, 'urun_bilgi': 'Bulunamadı.', 'benzersiz_id': None, 'stok_kod': 'YOK', 'parti_no': 'YOK', 'renk': 'YOK', 'sistem_stok': '0.00', 'sayilan_stok': '0.00', 'last_sayim': 'Yok', 'parti_varyantlar': [], 'renk_varyantlar': [], 'farkli_depo_uyarisi': '' }
    malzeme = None 
    if depo_kod == 'YOK': response_data['urun_bilgi'] = 'HATA: Depo Kodu Yok.'; print(">> HATA: Depo Kodu Yok."); return JsonResponse(response_data, status=400)

    # ⭐ DÜZELTME 2: Seri No sorgusu güncellendi
    # 1. Seri No / Barkod (benzersiz_id, malzeme_kodu VEYA seri_no)
    if seri_no != 'YOK':
        print(f">> 1: Seri ({seri_no})")
        malzeme = Malzeme.objects.filter(
            Q(benzersiz_id=seri_no) | 
            Q(malzeme_kodu__iexact=seri_no) |
            Q(seri_no__iexact=seri_no), # EKLENDİ
            lokasyon_kodu__iexact=depo_kod
        ).first()
        print(f"   -> {'Bulundu: '+malzeme.benzersiz_id if malzeme else 'Bulunamadı'}")

    # 2. Parti No (Stok Kodu varsa onunla, yoksa tek başına)
    if not malzeme and parti_no != 'YOK':
        print(f">> 2: Parti ({parti_no}), Stok ({stok_kod})")
        q = {'parti_no__iexact': parti_no, 'lokasyon_kodu__iexact': depo_kod}
        if stok_kod != 'YOK': q['malzeme_kodu__iexact'] = stok_kod
        malzeme = Malzeme.objects.filter(**q).first() 
        print(f"   -> {'Bulundu: '+malzeme.benzersiz_id if malzeme else 'Bulunamadı'}")

    # 3. Stok Kodu + Parti + Renk (Tam eşleşme)
    if not malzeme and stok_kod != 'YOK' and parti_no != 'YOK' and renk != 'YOK':
        print(f">> 3: Stok ({stok_kod}), Parti ({parti_no}), Renk ({renk})")
        malzeme = Malzeme.objects.filter(malzeme_kodu__iexact=stok_kod, parti_no__iexact=parti_no, renk__iexact=renk, lokasyon_kodu__iexact=depo_kod).first()
        print(f"   -> {'Bulundu: '+malzeme.benzersiz_id if malzeme else 'Bulunamadı'}")

    # 4. Sadece Stok Kodu (Varyantları listele)
    if not malzeme and stok_kod != 'YOK':
        print(f">> 4: Stok ({stok_kod}) Varyantları")
        varyantlar = Malzeme.objects.filter(malzeme_kodu__iexact=stok_kod, lokasyon_kodu__iexact=depo_kod)
        vc = varyantlar.count()
        print(f"   -> {vc} varyant bulundu.")
        if vc == 1: malzeme = varyantlar.first(); print(f"      -> Tek varyant seçildi: {malzeme.benzersiz_id}")
        elif vc > 1:
            response_data.update({'urun_bilgi': f"Varyant Seç ({stok_kod} - {vc} adet)", 'stok_kod': stok_kod})
            partiler = sorted(list(varyantlar.values_list('parti_no', flat=True).distinct()))
            renkler = sorted(list(varyantlar.values_list('renk', flat=True).distinct()))
            response_data['parti_varyantlar'] = [p for p in partiler if p != 'YOK']
            response_data['renk_varyantlar'] = [r for r in renkler if r != 'YOK']
            print(f"      -> Varyant listesi döndürülüyor."); print(f"--- ARAMA BİTTİ (Varyant) ---")
            return JsonResponse(response_data) 
            
    # Sonuç
    if malzeme:
        print(f">> SONUÇ: Bulundu: {malzeme.benzersiz_id}. Detaylar işleniyor...")
        ts = Decimal('0.0') 
        seid_str = request.GET.get('sayim_emri_id') 
        if seid_str:
            try: 
                # models.py'de DecimalField'e geçildiği için Sum sonucu zaten Decimal olacak
                ts = SayimDetay.objects.filter(sayim_emri_id=int(seid_str), benzersiz_malzeme=malzeme).aggregate(t=Sum('sayilan_stok'))['t'] or Decimal('0.0')
            except: pass 
        print(f"   -> Bu sayım toplamı: {ts:.2f}")
        dd = Malzeme.objects.filter(malzeme_kodu__iexact=malzeme.malzeme_kodu).exclude(lokasyon_kodu__iexact=malzeme.lokasyon_kodu).values_list('lokasyon_kodu', flat=True).distinct()
        fdu = f"⚠️ Başka depolarda: {', '.join(sorted([standardize_id_part(d) for d in dd]))}" if dd.exists() else ""
        if fdu: print(f"   -> Farklı depo uyarısı var.")
        
        # models.py'de DecimalField'e geçildi, .f2 formatlaması aynı kalabilir
        response_data.update({
            'found': True, 
            'benzersiz_id': malzeme.benzersiz_id, 
            'urun_bilgi': f"{malzeme.malzeme_adi} ({malzeme.malzeme_kodu}) P:{malzeme.parti_no} R:{malzeme.renk}", 
            'stok_kod': malzeme.malzeme_kodu, 
            'parti_no': malzeme.parti_no, 
            'renk': malzeme.renk, 
            'sistem_stok': f"{malzeme.sistem_stogu:.2f}", 
            'sayilan_stok': f"{ts:.2f}", 
            'last_sayim': get_last_sayim_info(malzeme) or 'Yok', 
            'farkli_depo_uyarisi': fdu
        })
        print(f"--- ARAMA BİTTİ (Başarılı) ---")
        return JsonResponse(response_data)
        
    # Bulunamadı
    aranan = seri_no if seri_no != 'YOK' else (parti_no if parti_no != 'YOK' else stok_kod)
    response_data['urun_bilgi'] = f"'{aranan}' bilgisi ile '{depo_kod}' deposunda bulunamadı."
    print(f">> SONUÇ: Bulunamadı.")
    print(f"--- ARAMA BİTTİ (Başarısız) ---")
    return JsonResponse(response_data)

@csrf_exempt
@transaction.atomic 
def ajax_sayim_kaydet(request, sayim_emri_id):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            bid = data.get('benzersiz_id') 
            print(f"\n--- KAYIT (ID ile) --- Gelen JSON: {data} -> ID: {bid}")
            if not bid: print(">> HATA: Benzersiz ID yok."); return JsonResponse({'success': False, 'message': "HATA: Ürün ID eksik."}, status=400)
            try:
                # Gelen miktar string'ini Decimal'e çevir
                m = Decimal(str(data.get('miktar', '0.0')).replace(',', '.'))
                if m <= Decimal('0.0'): raise ValueError("Miktar pozitif olmalı")
            except Exception as e: print(f">> HATA: Miktar ({data.get('miktar')}) geçersiz: {e}"); return JsonResponse({'success': False, 'message': f"HATA: Geçersiz miktar."}, status=400)
            
            pa = data.get('personel_adi', 'MISAFIR').strip().upper() or 'MISAFIR'
            lat, lon = str(data.get('lat', 'YOK')), str(data.get('lon', 'YOK'))
            
            try: malzeme = get_object_or_404(Malzeme, benzersiz_id=bid) 
            except Http404: print(f">> HATA: Malzeme ID({bid}) bulunamadı."); return JsonResponse({'success': False, 'message': f"HATA: ID '{bid}' bulunamadı."}, status=404)
            
            se = get_object_or_404(SayimEmri, pk=sayim_emri_id)
            if se.durum != 'Açık': print(f">> HATA: Sayım Emri ({sayim_emri_id}) kapalı."); return JsonResponse({'success': False, 'message': 'Sayım kapalı.'}, status=403) 
            
            print(f">> Detay Oluşturuluyor: Miktar={m}, Personel={pa}...")
            
            # SayimDetay.objects.create (models.py'de unique_together kaldırıldığı için bu artık sorunsuz çalışır)
            SayimDetay.objects.create(
                sayim_emri=se, 
                benzersiz_malzeme=malzeme, # Sadece ilişki
                personel_adi=pa, 
                sayilan_stok=m, # Decimal olarak kaydedilecek
                latitude=lat, 
                longitude=lon
            )
            print("   -> Oluşturuldu.")
            
            # Toplamı al (Sum sonucu Decimal gelecek)
            ts = SayimDetay.objects.filter(sayim_emri=se, benzersiz_malzeme=malzeme).aggregate(t=Sum('sayilan_stok'))['t'] or Decimal('0.0')
            print(f"   -> Yeni Toplam: {ts}")
            print(f"--- KAYIT BİTTİ (Başarılı) ---")
            
            # JSON yanıtını formatla
            return JsonResponse({'success': True, 'message': f"✅ {malzeme.malzeme_kodu} ({malzeme.parti_no}) {m:.2f} kayıt.", 'yeni_miktar': f"{ts:.2f}" })
        
        except SayimEmri.DoesNotExist: print(f">> HATA: Sayım Emri ID({sayim_emri_id}) yok."); return JsonResponse({'success': False, 'message': "HATA: Sayım Emri yok."}, status=404)
        except json.JSONDecodeError: print(f">> HATA: JSON Decode."); return JsonResponse({'success': False, 'message': "HATA: Geçersiz JSON."}, status=400)
        except Exception as e: et = type(e).__name__; print(f">> Kritik HATA ({et}): {e}"); return JsonResponse({'success': False, 'message': f"Sunucu hatası ({et})."}, status=500)
    
    return JsonResponse({'success': False, 'message': 'Geçersiz metot.'}, status=405) 

@csrf_exempt
@require_POST
def gemini_ocr_analiz(request):
    if not GEMINI_AVAILABLE: return JsonResponse({'success': False, 'message': "Gemini aktif değil."}, status=501) 
    if 'image_file' not in request.FILES: return JsonResponse({'success': False, 'message': "Dosya yüklenmedi."}, status=400)
    try:
        img_file = request.FILES['image_file']
        if img_file.size > 5*1024*1024: return JsonResponse({'success': False, 'message': "Dosya > 5MB."}, status=413) 
        try: img = Image.open(BytesIO(img_file.read()))
        except Exception as img_err: print(f"OCR Resim Açma Hatası: {img_err}"); return JsonResponse({'success': False, 'message': f"Resim açılamadı: {img_err}"}, status=400)
        
        genai.configure(api_key=GEMINI_API_KEY) 
        
        # ⭐ DÜZELTME 3: Model adı 'gemini-2.0-flash' olarak güncellendi
        model_name = 'gemini-2.0-flash' 
        model = genai.GenerativeModel(model_name) 
        
        system_instruction = ("Extract 'stok_kod', 'parti_no', 'renk', 'miktar' from labels. Use 'YOK' if missing. Quantity ('miktar') as decimal (e.g., 1.0). Respond ONLY with JSON list.")
        prompt = ("Analyze labels, create JSON list with 'stok_kod', 'parti_no', 'renk', 'miktar'. Quantity as decimal.")
        
        # --- Config'i tekrar ekledik ---
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
                    required=['stok_kod']
                )
            )
        )
        
        print(f"Gemini API Çağrısı: Model={model_name}")
        response = model.generate_content([prompt, img], generation_config=generation_config) # Config ile çağır
        print("Gemini Yanıtı Alındı.")
        
        try:
            json_text = response.text 
            json_results = json.loads(json_text.strip()) 
            if not isinstance(json_results, list): raise json.JSONDecodeError("Liste bekleniyordu.", json_text, 0)
            print(f"Gemini Yanıt Parse Edildi: {len(json_results)} sonuç.")
        except Exception as json_err: print(f"Gemini JSON Hatası: {json_err}. Yanıt: '{response.text}'"); return JsonResponse({'success': False, 'message': f"YZ yanıtı işlenemedi."}, status=500)

        processed = []
        print("OCR Sonuçları İşleniyor...")
        for i, item in enumerate(json_results):
             if not isinstance(item, dict): print(f"   -> {i+1} atlandı (dict değil)."); continue 
             try:
                 mr = item.get('miktar', '0.0'); md = Decimal('0.0')
                 if isinstance(mr, (int, float)): md = Decimal(mr)
                 elif isinstance(mr, str): ms = mr.replace(',', '.').strip().upper(); md = Decimal(ms) if ms and ms != 'YOK' else Decimal('0.0')
             except: print(f"   -> {i+1}: Miktar ('{mr}') geçersiz, 1.0 kullanıldı."); md = Decimal('1.0') 
             sk = standardize_id_part(item.get('stok_kod', 'YOK'))
             if sk == 'YOK': print(f"   -> {i+1} atlandı (Stok Kodu YOK)."); continue 
             processed.append({'stok_kod': sk, 'parti_no': standardize_id_part(item.get('parti_no', 'YOK')), 'renk': standardize_id_part(item.get('renk', 'YOK')), 'miktar': f"{md:.2f}", 'barkod': sk })
             print(f"   -> {i+1} işlendi: Stok={sk}, Miktar={md:.2f}")

        c = len(processed)
        if c == 0: print("OCR SONUÇ: Geçerli etiket yok."); return JsonResponse({'success': True, 'message': "Geçerli etiket bulunamadı.", 'count': 0, 'results': []})
        print(f"OCR SONUÇ: {c} geçerli etiket."); return JsonResponse({'success': True, 'message': f"✅ {c} etiket okundu.", 'count': c, 'results': processed})

    # --- SyntaxError Düzeltmesi UYGULANDI ---
    # except bloğunu doğru şekilde ayır
    except google_exceptions.GoogleAPICallError as e: # Önce spesifik Google hatasını yakala (google_exceptions import edildi)
        print(f"Gemini API Hatası: {e}") 
        error_detail = str(e)
        # Kullanıcıya daha anlamlı mesajlar ver
        user_message = f"Gemini API ile iletişim hatası oluştu. Lütfen API anahtarınızı, kotanızı veya model adını kontrol edin."
        # Hata tiplerini daha doğru kontrol et (varsa)
        if isinstance(e, google_exceptions.PermissionDenied) or "API key not valid" in error_detail:
             user_message = "Gemini API anahtarı geçersiz veya yetki sorunu. Yönetici ile iletişime geçin."
        elif isinstance(e, google_exceptions.ResourceExhausted) or "quota" in error_detail.lower():
             user_message = "Gemini API kullanım kotası aşıldı."
        elif isinstance(e, google_exceptions.NotFound) or ("model" in error_detail.lower() and ("not found" in error_detail.lower() or "is not supported" in error_detail.lower())):
             user_message = f"Kullanılan Gemini modeli ('{model_name}') bulunamadı veya bu işlem için desteklenmiyor."
        elif isinstance(e, google_exceptions.InvalidArgument):
             user_message = f"Gemini API'ye geçersiz bir argüman gönderildi: {e}"
        # Diğer 4xx/5xx hataları
        elif hasattr(e, 'message'): # Genel Google API hatası
             user_message = f"Gemini API Hatası: {e.message}"
             
        return JsonResponse({'success': False, 'message': user_message}, status=502) # Bad Gateway daha uygun
    except Exception as e: # Sonra diğer genel hataları yakala
        error_type = type(e).__name__
        print(f"Kritik YZ Analiz Hatası ({error_type}): {e}") 
        return JsonResponse({'success': False, 'message': f"Görsel analizi sırasında beklenmedik sunucu hatası ({error_type})."}, status=500)


# --- EXCEL EXPORT --- (Placeholderlar)
@csrf_exempt
def export_excel(request, sayim_emri_id): se = get_object_or_404(SayimEmri, pk=sayim_emri_id); return HttpResponse(f"'{se.ad}' Excel Raporu Henüz Yok.", status=501)
@csrf_exempt
def export_mutabakat_excel(request, sayim_emri_id): se = get_object_or_404(SayimEmri, pk=sayim_emri_id); return HttpResponse(f"'{se.ad}' Mutabakat Excel Henüz Yok.", status=501)