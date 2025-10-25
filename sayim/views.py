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
    from google.api_core.exceptions import GoogleAPIError # Daha genel hata yakalama
except ImportError:
    genai = None
    GoogleAPIError = None
    print("UYARI: Google Generative AI kütüphanesi bulunamadı. OCR özelliği çalışmayacak.")


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
        context['lokasyonlar'] = sorted([standardize_id_part(loc) for loc in lokasyon_listesi if loc])
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
        if pk is None:
            raise Http404(_("Sayım Emri ID'si URL'de bulunamadı."))
        if queryset is None:
            queryset = self.get_queryset()
        try:
            return queryset.get(pk=pk) 
        except self.model.DoesNotExist:
            raise Http404(_("Sayım Emri pk=%(pk)s ile bulunamadı.") % {'pk': pk})


    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sayim_emri_id'] = self.object.pk 
        context['depo_kodu'] = self.kwargs['depo_kodu'] 
        context['personel_adi'] = self.request.session.get('current_user', 'MISAFIR')
        context['gemini_available'] = GEMINI_AVAILABLE
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
            tum_malzemeler_dict = {m.benzersiz_id: m for m in Malzeme.objects.all()}
            sayilan_miktarlar = {}
            for detay in sayim_detaylari:
                 # Malzemenin varlığını kontrol et
                 if detay.benzersiz_malzeme and detay.benzersiz_malzeme.benzersiz_id in tum_malzemeler_dict:
                     malzeme_id = detay.benzersiz_malzeme.benzersiz_id
                     sayilan_miktarlar[malzeme_id] = sayilan_miktarlar.get(malzeme_id, Decimal('0.0')) + detay.sayilan_stok
                 # else: Hatalı ilişki varsa loglanabilir.

            rapor_list = []
            # Tüm malzemeler üzerinden dönerek raporu oluştur
            for malzeme_id, malzeme in tum_malzemeler_dict.items():
                sayilan_mik_dec = sayilan_miktarlar.get(malzeme_id, Decimal('0.0'))
                sistem_mik_dec = malzeme.sistem_stogu # Modelde DecimalField varsayılıyor
                birim_fiyat_dec = malzeme.birim_fiyat # Modelde DecimalField varsayılıyor
                
                mik_fark_dec = sayilan_mik_dec - sistem_mik_dec
                tutar_fark_dec = mik_fark_dec * birim_fiyat_dec
                sistem_tutar_dec = sistem_mik_dec * birim_fiyat_dec
                
                fark_mutlak = abs(mik_fark_dec)

                if fark_mutlak < Decimal('0.01'):
                    tag = 'tamam'
                elif sistem_mik_dec > Decimal('0.01') and sayilan_mik_dec < Decimal('0.01'):
                    tag = 'hic_sayilmadi'
                else:
                    tag = 'fark_var'
                
                mik_yuzde = (mik_fark_dec / sistem_mik_dec) * 100 if sistem_mik_dec != 0 else Decimal('0.0')
                
                rapor_list.append({
                    'kod': malzeme.malzeme_kodu, 'ad': malzeme.malzeme_adi, 'parti': malzeme.parti_no,
                    'renk': malzeme.renk, 'birim': malzeme.olcu_birimi,
                    'sistem_mik': f"{sistem_mik_dec:.2f}",
                    'sayilan_mik': f"{sayilan_mik_dec:.2f}",
                    'mik_fark': f"{mik_fark_dec:.2f}",
                    'mik_yuzde': f"{mik_yuzde:.2f}%",
                    'sistem_tutar': f"{sistem_tutar_dec:.2f}",
                    'tutar_fark': f"{tutar_fark_dec:.2f}",
                    'tag': tag
                })
            context['rapor_data'] = rapor_list
        except Exception as e:
            # print(f"Raporlama Hatası: {e}") # Debugging için
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
            # Pandas yerine Django ORM kullanmak genellikle daha güvenli ve verimlidir.
            detaylar = SayimDetay.objects.filter(sayim_emri_id=sayim_emri_id)\
                                        .order_by('personel_adi', 'guncellenme_tarihi')\
                                        .values('personel_adi', 'guncellenme_tarihi')

            if not detaylar.exists():
                context['analiz_data'] = []
                context['hata'] = f"Bu emre ait analiz edilebilir sayım verisi bulunamadı."
                return context

            # Veriyi personel bazında gruplamak için dictionary kullanabiliriz.
            personel_verileri = {}
            for d in detaylar:
                personel = d['personel_adi']
                tarih = d['guncellenme_tarihi']
                if tarih: # None değerleri atla
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
                        # Tarihlerin timezone aware olduğundan emin olun
                        if timezone.is_aware(tarihler[i]) and timezone.is_aware(tarihler[i-1]):
                           fark = (tarihler[i] - tarihler[i-1]).total_seconds()
                           # Çok büyük farkları (örn: 1 saatten fazla) aykırı veri kabul et
                           if fark < 3600: 
                               farklar_sn.append(fark)
                        # else: Farklı timezone'lar varsa veya naive ise loglama yapılabilir.
                        
                    if farklar_sn:
                        toplam_saniye = sum(farklar_sn)
                        ortalama_sure_sn = toplam_saniye / len(farklar_sn)
                        dakika = int(ortalama_sure_sn // 60)
                        saniye_kalan = int(ortalama_sure_sn % 60)
                        etiket = f"{dakika:02d}:{saniye_kalan:02d}"
                    else: # Eğer tüm farklar aykırıysa
                        etiket = 'Aykırı Veri ( > 1 Saat/Kayıt)'


                analiz_list.append({
                    'personel': personel,
                    'toplam_kayit': toplam_kayit,
                    'toplam_sure_sn': f"{toplam_saniye:.2f}",
                    'ortalama_sure_formatli': etiket,
                    # Sıralama için sayısal değeri sakla, sonra formatla
                    'ortalama_sure_sn_raw': ortalama_sure_sn 
                })

            analiz_list.sort(key=lambda x: x['ortalama_sure_sn_raw'])
            
            # Son formatlama
            for item in analiz_list:
                 if item['ortalama_sure_sn_raw'] == float('inf'):
                     item['ortalama_sure_sn'] = 'N/A' # Veya '-'
                 else:
                     item['ortalama_sure_sn'] = f"{item['ortalama_sure_sn_raw']:.2f}"
                 del item['ortalama_sure_sn_raw'] # Geçici alanı kaldır

            context['analiz_data'] = analiz_list
        except Exception as e:
            # print(f"Performans Analizi Hatası: {e}") # Debugging için
            context['analiz_data'] = []
            context['hata'] = f"Performans analizi hatası: {e}"
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

            # Tüm malzemeleri grup bilgisiyle birlikte çek
            tum_malzemeler = Malzeme.objects.all().values(
                'benzersiz_id', 'stok_grup', 'sistem_stogu', 'birim_fiyat'
            )

            grup_ozet = {}
            for malzeme in tum_malzemeler:
                stok_grubu = malzeme['stok_grup'] or 'TANIMSIZ' # Grup yoksa TANIMSIZ ata
                sistem_mik = malzeme['sistem_stogu']
                birim_fiyat = malzeme['birim_fiyat']
                sayilan_stok = sayilan_miktarlar_dict.get(malzeme['benzersiz_id'], Decimal('0.0'))

                mik_fark = sayilan_stok - sistem_mik
                tutar_fark = mik_fark * birim_fiyat
                sistem_tutar = sistem_mik * birim_fiyat

                if stok_grubu not in grup_ozet:
                    grup_ozet[stok_grubu] = {
                        'sistem_mik_toplam': Decimal('0.0'),
                        'sistem_tutar_toplam': Decimal('0.0'),
                        'sayilan_mik_toplam': Decimal('0.0'),
                        'tutar_fark_toplam': Decimal('0.0'), # Tutar farkını doğrudan toplamak daha doğru
                    }
                
                grup_ozet[stok_grubu]['sistem_mik_toplam'] += sistem_mik
                grup_ozet[stok_grubu]['sistem_tutar_toplam'] += sistem_tutar
                grup_ozet[stok_grubu]['sayilan_mik_toplam'] += sayilan_stok
                grup_ozet[stok_grubu]['tutar_fark_toplam'] += tutar_fark # Tutar farkını topla

            rapor_list = []
            for grup, data in grup_ozet.items():
                # Toplam miktar farkı ve tutar farkını hesapla
                mik_fark_toplam = data['sayilan_mik_toplam'] - data['sistem_mik_toplam']
                tutar_fark_toplam = data['tutar_fark_toplam']
                
                rapor_list.append({
                    'grup': grup,
                    'sistem_mik': f"{data['sistem_mik_toplam']:.2f}",
                    'sistem_tutar': f"{data['sistem_tutar_toplam']:.2f}",
                    # Miktar farkını pozitif/negatif olarak ayır
                    'fazla_mik': f"{mik_fark_toplam:.2f}" if mik_fark_toplam > 0 else "0.00",
                    'eksik_mik': f"{-mik_fark_toplam:.2f}" if mik_fark_toplam < 0 else "0.00",
                    # Tutar farkını pozitif/negatif olarak ayır
                    'fazla_tutar': f"{tutar_fark_toplam:.2f}" if tutar_fark_toplam > 0 else "0.00",
                    'eksik_tutar': f"{-tutar_fark_toplam:.2f}" if tutar_fark_toplam < 0 else "0.00"
                })
            context['analiz_data'] = sorted(rapor_list, key=lambda x: x['grup']) # Gruplara göre sırala
        except Exception as e:
            # print(f"Fark Özeti Hatası: {e}") # Debugging için
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
        # ". " içeriyor kontrolü yerine Decimal'e çevrilebilirliği kontrol etmek daha sağlam
        konum_data = SayimDetay.objects.filter(
            sayim_emri=sayim_emri,
            latitude__isnull=False, 
            longitude__isnull=False,
        ).exclude(latitude='YOK').exclude(longitude='YOK')\
         .values('personel_adi', 'latitude', 'longitude', 'kayit_tarihi', 'sayilan_stok')\
         .order_by('kayit_tarihi')

        markers = []
        for item in konum_data:
            try:
                # Decimal'e çevirmeyi dene, başarısız olursa atla
                lat = Decimal(item['latitude'])
                lng = Decimal(item['longitude'])
                markers.append({
                    'personel': item['personel_adi'],
                    'lat': float(lat), # JSON float kabul eder
                    'lng': float(lng),
                    'tarih': item['kayit_tarihi'].strftime("%Y-%m-%d %H:%M:%S") if item['kayit_tarihi'] else 'Bilinmiyor',
                    'stok': float(item['sayilan_stok']) # JSON float kabul eder
                })
            except (ValueError, TypeError, Decimal.InvalidOperation):
                # Geçersiz koordinatları atla
                continue

        context['konum_json'] = json.dumps(markers, cls=DjangoJSONEncoder)
        context['toplam_kayit'] = len(markers)
        # 'YOK' olanları veya null olanları say
        context['konum_almayan_kayitlar'] = SayimDetay.objects.filter(
            Q(sayim_emri=sayim_emri) & 
            (Q(latitude='YOK') | Q(longitude='YOK') | Q(latitude__isnull=True) | Q(longitude__isnull=True))
        ).count()
        context['hata'] = None
        if not markers:
             context['hata'] = "Bu emre ait haritada gösterilebilir geçerli konum verisi (GPS) bulunamadı."
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
        sayilan_toplamlar = SayimDetay.objects.filter(sayim_emri=sayim_emri)\
            .values('benzersiz_malzeme__benzersiz_id')\
            .annotate(toplam_sayilan=Sum('sayilan_stok'))\
            .order_by('benzersiz_malzeme__benzersiz_id')

        # Güncellenecek malzemeleri tutacak dictionary
        guncellenecek_stoklar = {
            item['benzersiz_malzeme__benzersiz_id']: item['toplam_sayilan'] or Decimal('0.0')
            for item in sayilan_toplamlar if item['benzersiz_malzeme__benzersiz_id']
        }

        # İlgili malzemeleri topluca çek ve güncelle
        malzemeler_to_update = Malzeme.objects.filter(benzersiz_id__in=guncellenecek_stoklar.keys())
        
        updated_count = 0
        for malzeme in malzemeler_to_update:
            yeni_stok = guncellenecek_stoklar[malzeme.benzersiz_id]
            # Sadece değişiklik varsa güncelleme yap (performans için)
            if malzeme.sistem_stogu != yeni_stok:
                malzeme.sistem_stogu = yeni_stok
                malzeme.sistem_tutari = yeni_stok * malzeme.birim_fiyat
                malzeme.save(update_fields=['sistem_stogu', 'sistem_tutari']) # Sadece ilgili alanları güncelle
                updated_count +=1

        # Sayım emrini kapat
        sayim_emri.durum = 'Tamamlandı'
        sayim_emri.onay_tarihi = now
        sayim_emri.save(update_fields=['durum', 'onay_tarihi'])

        messages.success(request, f"Sayım emri başarıyla onaylandı ve kapatıldı. {updated_count} malzemenin stoğu güncellendi.")
        return redirect('sayim_emirleri')

    except Exception as e:
        # print(f"Stok Onaylama Hatası: {e}") # Debugging
        messages.error(request, f"Stok güncelleme sırasında kritik bir hata oluştu: {e}")
        # Hata durumunda raporlama sayfasına geri dön, hatayı göster
        # RaporlamaView context'ini burada tekrar oluşturmak yerine redirect daha basit
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
                 # CSV okurken ayırıcı ve encoding belirtmek gerekebilir
                 try:
                    df = pd.read_csv(excel_io, sep=';', encoding='utf-8') # Örnek: ';' ayırıcı, utf-8 encoding
                 except Exception as read_err:
                    try: 
                        excel_io.seek(0) # Başa sar
                        df = pd.read_csv(excel_io, sep=',', encoding='latin1') # Başka bir format dene
                    except Exception as read_err_alt:
                        return JsonResponse({'success': False, 'message': f'CSV okuma hatası: {read_err_alt}. Ayırıcı veya encoding yanlış olabilir.'}, status=400)
            else: # xlsx veya xls ise
                 try:
                    df = pd.read_excel(excel_io, engine='openpyxl' if excel_file.name.endswith('.xlsx') else 'xlrd')
                 except Exception as read_err:
                     return JsonResponse({'success': False, 'message': f'Excel okuma hatası: {read_err}. Dosya formatı bozuk olabilir.'}, status=400)

            # --- VERİ TEMİZLEME VE DÖNÜŞTÜRME ---
            # Gerekli sütunları kontrol et (Excel'deki başlıklarla eşleşmeli)
            required_cols = ["Stok Kodu", "Depo Kodu", "Miktar", "Maliyet birim"]
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                return JsonResponse({'success': False, 'message': f'Excel dosyasında eksik sütunlar var: {", ".join(missing_cols)}'}, status=400)

            # NaN değerleri uygun varsayılanlarla doldur
            df.fillna({
                "Parti": 'YOK',
                "Renk": 'YOK',
                "Depo Kodu": 'MERKEZ',
                "Miktar": 0.0,
                "Maliyet birim": 0.0,
                "Grup": 'GENEL',
                "Stok Adı": '', # Stok adı boşsa koddan türetilecek
                "Birim": 'ADET'
            }, inplace=True)
            
            # Sayısal alanları Decimal'e çevir, hataları yakala
            try:
                df['Miktar'] = df['Miktar'].apply(lambda x: Decimal(str(x).replace(',', '.')) if str(x).strip() else Decimal('0.0'))
                df['Maliyet birim'] = df['Maliyet birim'].apply(lambda x: Decimal(str(x).replace(',', '.')) if str(x).strip() else Decimal('0.0'))
            except (ValueError, TypeError, Decimal.InvalidOperation) as conv_err:
                 return JsonResponse({'success': False, 'message': f'Sayısal alan (Miktar/Maliyet) dönüştürme hatası: {conv_err}. Lütfen verileri kontrol edin.'}, status=400)


            # --- VERİTABANI İŞLEMLERİ ---
            success_count = 0
            fail_count = 0
            processed_ids = set() # Aynı ID'nin tekrar işlenmesini engellemek için

            # Tüm Malzeme tablosunu silmek yerine, update_or_create kullanmak daha güvenli.
            # Ancak Excel tüm envanteri temsil ediyorsa, önce mevcutları pasif yapıp sonra güncellemek de bir yöntem olabilir.
            # Şimdilik update_or_create ile devam ediyoruz.
            
            for index, row in df.iterrows():
                 try:
                     stok_kod = standardize_id_part(str(row['Stok Kodu']))
                     parti_no = standardize_id_part(str(row['Parti'])) 
                     renk = standardize_id_part(str(row['Renk']))
                     lokasyon_kodu = standardize_id_part(str(row['Depo Kodu'])) 
                     
                     if not stok_kod or stok_kod == 'YOK':
                         # print(f"Satır {index+1}: Geçersiz Stok Kodu, atlanıyor.")
                         fail_count += 1
                         continue

                     benzersiz_id = generate_unique_id(stok_kod, parti_no, lokasyon_kodu, renk)
                     
                     # Aynı ID daha önce işlendiyse atla (Excel'de tekrar eden satırlar olabilir)
                     if benzersiz_id in processed_ids:
                         continue
                     processed_ids.add(benzersiz_id)

                     sistem_miktari = row['Miktar'] # Zaten Decimal
                     birim_fiyati = row['Maliyet birim'] # Zaten Decimal
                     stok_grubu = str(row['Grup']) 
                     stok_adi = str(row['Stok Adı']) if str(row['Stok Adı']).strip() else f"Stok {stok_kod}"
                     birim = str(row['Birim'])
                     
                     # Var olanı güncelle veya yenisini yarat
                     obj, created = Malzeme.objects.update_or_create(
                          benzersiz_id=benzersiz_id,
                          defaults={
                              'malzeme_kodu': stok_kod,
                              'malzeme_adi': stok_adi,
                              'parti_no': parti_no,
                              'renk': renk,
                              'lokasyon_kodu': lokasyon_kodu,
                              'olcu_birimi': birim,
                              'stok_grup': stok_grubu,
                              'sistem_stogu': sistem_miktari,
                              'birim_fiyat': birim_fiyati,
                              'sistem_tutari': sistem_miktari * birim_fiyati 
                          }
                      )
                     success_count += 1
                     
                 except Exception as e:
                     # print(f"Satır {index+1} işlenirken hata oluştu: {e}") # Debugging
                     fail_count += 1
                     continue # Hata durumunda diğer satırlara devam et
            
            message = f"✅ Başarılı: {success_count} stok verisi yüklendi/güncellendi. Hata/Atlanan satır sayısı: {fail_count}."
            return JsonResponse({'success': True, 'message': message})

        except Exception as e:
            # Genel dosya işleme veya veritabanı hatası
            # print(f"Excel Yükleme Hatası: {e}") # Debugging
            return JsonResponse({'success': False, 'message': f'Stok yükleme sırasında kritik hata: {e}'}, status=500)

    return JsonResponse({'success': False, 'message': 'Geçersiz istek metodu.'}, status=400)


# --- AJAX FONKSİYONLARI ---

# Yardımcı Fonksiyon: Son sayım bilgisini getirir
def get_last_sayim_info(malzeme_nesnesi): # benzersiz_id yerine nesneyi al
    if not malzeme_nesnesi:
        return None
    last_sayim = SayimDetay.objects.filter(benzersiz_malzeme=malzeme_nesnesi)\
                                   .order_by('-kayit_tarihi').first() # En son kaydı al
    if last_sayim:
        return {
            'tarih': last_sayim.kayit_tarihi.strftime("%d %b %H:%M") if last_sayim.kayit_tarihi else 'Bilinmiyor',
            'personel': last_sayim.personel_adi
        }
    return None

# ####################################################################################
# ⭐ OPTİMİZE EDİLMİŞ AKILLI ARAMA FONKSİYONU (views.py) - SON HİYERARŞİ VE UYARI + DEBUG
# ####################################################################################

@csrf_exempt
def ajax_akilli_stok_ara(request):
    # Parametreleri al ve standardize et
    seri_no = standardize_id_part(request.GET.get('seri_no', 'YOK'))
    stok_kod = standardize_id_part(request.GET.get('stok_kod', 'YOK'))
    parti_no = standardize_id_part(request.GET.get('parti_no', 'YOK'))
    renk = standardize_id_part(request.GET.get('renk', 'YOK'))
    depo_kod = standardize_id_part(request.GET.get('depo_kod', 'YOK')) # Depo kodu zorunlu olmalı
    
    # DEBUG: Gelen ve standardize edilmiş parametreleri yazdır
    print(f"--- ARAMA BAŞLADI ---")
    print(f"Gelen Seri No: {request.GET.get('seri_no', 'YOK')}, Standart: {seri_no}")
    print(f"Gelen Stok Kod: {request.GET.get('stok_kod', 'YOK')}, Standart: {stok_kod}")
    print(f"Gelen Parti No: {request.GET.get('parti_no', 'YOK')}, Standart: {parti_no}")
    print(f"Gelen Renk: {request.GET.get('renk', 'YOK')}, Standart: {renk}")
    print(f"Gelen Depo Kod: {request.GET.get('depo_kod', 'YOK')}, Standart: {depo_kod}")
    
    # Başlangıç yanıt verisi
    response_data = {
        'found': False, 'urun_bilgi': 'Stok veya Barkod bulunamadı.',
        'stok_kod': 'YOK', 'parti_no': 'YOK', 'renk': 'YOK',
        'sistem_stok': '0.00', 'sayilan_stok': '0.00', 'last_sayim': 'Bilinmiyor',
        'parti_varyantlar': [], 'renk_varyantlar': [], 'farkli_depo_uyarisi': '' 
    }
    
    malzeme = None # Bulunan Malzeme nesnesini tutacak değişken
    
    # Depo kodu gelmediyse hata döndür
    if depo_kod == 'YOK':
        response_data['urun_bilgi'] = 'HATA: Depo kodu belirtilmedi.'
        print("ARAMA SONUCU: Depo kodu YOK, hata döndü.")
        return JsonResponse(response_data, status=400)

    # -----------------------------------------------------------
    # 1. Hiyerarşi: Seri No / Barkod ile TAM EŞLEŞME (iexact)
    # -----------------------------------------------------------
    if seri_no != 'YOK':
        print(f"1. ADIM: Seri No ({seri_no}) ile aranıyor...")
        # Sadece ilgili depoda ara
        malzeme = Malzeme.objects.filter(
            Q(benzersiz_id__iexact=seri_no) | Q(malzeme_kodu__iexact=seri_no),
            lokasyon_kodu__iexact=depo_kod
        ).first()
        if malzeme:
             print(f"1. ADIM SONUCU: Seri No ile bulundu: {malzeme.benzersiz_id}") # Loglamayı iyileştir
        else:
             print(f"1. ADIM SONUCU: Seri No ile bulunamadı.")


    # -----------------------------------------------------------
    # 2. Hiyerarşi: Parti No ile Arama (Parti No + Depo + Opsiyonel Stok Kodu)
    # -----------------------------------------------------------
    if not malzeme and parti_no != 'YOK':
        print(f"2. ADIM: Parti No ({parti_no}) ile aranıyor (Stok Kodu: {stok_kod})...")
        query_params = {
            'parti_no__iexact': parti_no,
            'lokasyon_kodu__iexact': depo_kod
        }
        if stok_kod != 'YOK':
             query_params['malzeme_kodu__iexact'] = stok_kod
        
        malzeme = Malzeme.objects.filter(**query_params).first()
             
        if malzeme:
             print(f"2. ADIM SONUCU: Parti No ile bulundu: {malzeme.benzersiz_id}")
        else:
             print(f"2. ADIM SONUCU: Parti No ile bulunamadı.")


    # -----------------------------------------------------------
    # 3. Hiyerarşi: Stok Kodu + Parti No + Renk ile Tam Eşleşme
    # -----------------------------------------------------------
    if not malzeme and stok_kod != 'YOK' and parti_no != 'YOK' and renk != 'YOK':
        print(f"3. ADIM: Stok ({stok_kod}), Parti ({parti_no}), Renk ({renk}) ile aranıyor...")
        malzeme = Malzeme.objects.filter(
            malzeme_kodu__iexact=stok_kod,
            parti_no__iexact=parti_no,
            renk__iexact=renk,
            lokasyon_kodu__iexact=depo_kod
        ).first()
        if malzeme:
             print(f"3. ADIM SONUCU: Tam kombinasyon ile bulundu: {malzeme.benzersiz_id}")
        else:
             print(f"3. ADIM SONUCU: Tam kombinasyon ile bulunamadı.")

    # -----------------------------------------------------------
    # 4. Hiyerarşi: Sadece Stok Kodu ile Arama (Varyant Listeleme)
    # -----------------------------------------------------------
    if not malzeme and stok_kod != 'YOK':
        print(f"4. ADIM: Sadece Stok Kodu ({stok_kod}) ile varyant aranıyor...")
        varyantlar = Malzeme.objects.filter(
            malzeme_kodu__iexact=stok_kod,
            lokasyon_kodu__iexact=depo_kod
        )
        varyant_count = varyantlar.count()
        print(f"4. ADIM SONUCU: {varyant_count} varyant bulundu.")
        
        if varyant_count == 1:
            # Tek varyant varsa, onu seç
            malzeme = varyantlar.first()
            print(f"   -> Tek varyant bulundu ve seçildi: {malzeme.benzersiz_id}")
        elif varyant_count > 1:
            # Birden fazla varyant varsa, listele
            response_data['urun_bilgi'] = f"Varyant Seçimi Gerekli: {stok_kod} için {varyant_count} varyant bulundu."
            response_data['stok_kod'] = stok_kod # Stok kodunu teyit et
            partiler = sorted(list(varyantlar.values_list('parti_no', flat=True).distinct()))
            renkler = sorted(list(varyantlar.values_list('renk', flat=True).distinct()))
            response_data['parti_varyantlar'] = [p for p in partiler if p != 'YOK']
            response_data['renk_varyantlar'] = [r for r in renkler if r != 'YOK']
            print(f"   -> Birden fazla varyant bulundu, liste döndürülüyor.")
            print(f"--- ARAMA BİTTİ (Varyant Listesi) ---")
            return JsonResponse(response_data) 
            

    # -----------------------------------------------------------
    # NİHAİ SONUÇ İŞLEME: Eğer bir malzeme bulunduysa
    # -----------------------------------------------------------
    if malzeme:
        print(f"NİHAİ SONUÇ: Malzeme bulundu: {malzeme.benzersiz_id}. Detaylar işleniyor...")
        # Toplam sayılan miktarı al (SADECE BU SAYIM EMRİ İÇİN)
        sayim_emri_id = request.GET.get('sayim_emri_id') # URL'den veya JS'den gelmeli
        toplam_sayilan = Decimal('0.0')
        if sayim_emri_id:
            try:
                toplam_sayilan = SayimDetay.objects.filter(
                    sayim_emri_id=sayim_emri_id, 
                    benzersiz_malzeme=malzeme
                ).aggregate(total_sayilan=Sum('sayilan_stok'))['total_sayilan'] or Decimal('0.0')
            except ValueError: # Geçersiz sayim_emri_id gelirse
                pass 
        
        # Farklı depo uyarısı
        diger_depolar = Malzeme.objects.filter(malzeme_kodu__iexact=malzeme.malzeme_kodu)\
            .exclude(lokasyon_kodu__iexact=malzeme.lokasyon_kodu)\
            .values_list('lokasyon_kodu', flat=True).distinct()
        
        farkli_depo_uyarisi = ""
        if diger_depolar.exists():
            depo_isimleri = ", ".join(sorted([standardize_id_part(d) for d in diger_depolar]))
            farkli_depo_uyarisi = f"⚠️ DİKKAT! Bu ürünün stoğu {depo_isimleri} depolarında da mevcut."
            print(f"   -> Farklı depo uyarısı eklendi: {farkli_depo_uyarisi}")


        # Başarılı yanıtı oluştur
        response_data.update({
            'found': True,
            'urun_bilgi': f"{malzeme.malzeme_adi} ({malzeme.malzeme_kodu}) - P:{malzeme.parti_no} R:{malzeme.renk}",
            'stok_kod': malzeme.malzeme_kodu,
            'parti_no': malzeme.parti_no,
            'renk': malzeme.renk,
            'sistem_stok': f"{malzeme.sistem_stogu:.2f}", 
            'sayilan_stok': f"{toplam_sayilan:.2f}", # Bu sayım emri için toplam
            'last_sayim': get_last_sayim_info(malzeme) or 'Yok', # Nesneyi gönder
            'parti_varyantlar': [], 
            'renk_varyantlar': [],
            'farkli_depo_uyarisi': farkli_depo_uyarisi
        })
        print(f"--- ARAMA BİTTİ (Başarılı) ---")
        return JsonResponse(response_data)
        
    # Eğer hiçbir arama adımında malzeme bulunamadıysa
    aranan_deger = seri_no if seri_no != 'YOK' else (parti_no if parti_no != 'YOK' else stok_kod)
    response_data['urun_bilgi'] = f"'{aranan_deger}' bilgisi ile '{depo_kod}' deposunda stok bulunamadı."
    print(f"NİHAİ SONUÇ: Malzeme bulunamadı. Aranan: {aranan_deger}, Depo: {depo_kod}")
    print(f"--- ARAMA BİTTİ (Başarısız) ---")
    return JsonResponse(response_data)


# ####################################################################################
# ⭐ KRİTİK REVİZYON: ajax_sayim_kaydet (TypeError Çözümü - Gereksiz Alanlar Kaldırıldı + LOGGING)
# ####################################################################################

@csrf_exempt
@transaction.atomic # Kayıt işlemi atomik olmalı
def ajax_sayim_kaydet(request, sayim_emri_id):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # Gelen veriyi temizle ve doğrula
            stok_kod = standardize_id_part(data.get('stok_kod', 'YOK'))
            parti_no = standardize_id_part(data.get('parti_no', 'YOK'))
            renk = standardize_id_part(data.get('renk', 'YOK'))
            depo_kod = standardize_id_part(data.get('depo_kod', 'YOK'))
            
            # DEBUG: Kayıt için gelen ve standardize edilmiş veriyi yazdır
            print(f"--- KAYIT BAŞLADI ---")
            print(f"Gelen JSON: {data}")
            print(f"Standardize Edilmiş Stok Kod: {stok_kod}")
            print(f"Standardize Edilmiş Parti No: {parti_no}")
            print(f"Standardize Edilmiş Renk: {renk}")
            print(f"Standardize Edilmiş Depo Kod: {depo_kod}")

            # Gerekli alanlar boşsa hata ver
            if stok_kod == 'YOK' or depo_kod == 'YOK':
                 print("KAYIT HATASI: Stok Kodu veya Depo Kodu YOK.")
                 return JsonResponse({'success': False, 'message': "HATA: Stok Kodu ve Depo Kodu boş olamaz."}, status=400)

            try:
                miktar = Decimal(data.get('miktar', '0.0'))
                if miktar <= 0:
                   print(f"KAYIT HATASI: Miktar ({miktar}) sıfırdan büyük değil.")
                   return JsonResponse({'success': False, 'message': "HATA: Miktar sıfırdan büyük olmalıdır."}, status=400) 
            except (ValueError, TypeError, Decimal.InvalidOperation) as e:
                print(f"KAYIT HATASI: Miktar ({data.get('miktar')}) dönüştürülemedi: {e}")
                return JsonResponse({'success': False, 'message': "HATA: Geçersiz miktar formatı."}, status=400)

            personel_adi = data.get('personel_adi', 'MISAFIR').strip().upper()
            if not personel_adi: personel_adi = 'MISAFIR' # Boşsa MISAFIR ata
            
            latitude = str(data.get('lat', 'YOK')) # String olarak sakla
            longitude = str(data.get('lon', 'YOK'))
            # loc_hata = data.get('loc_hata', '') # Bu alan modelde yoktu, kaldırıldı

            # 1. Malzeme ve Sayım Emrini Bul (Büyük/küçük harf duyarsız eşleşme)
            try:
                # DEBUG: Hangi filtrelerle arama yapıldığını yazdır
                print(f"Malzeme Aranıyor: Stok='{stok_kod}', Parti='{parti_no}', Renk='{renk}', Depo='{depo_kod}' (iexact ile)")
                malzeme = Malzeme.objects.get(
                    malzeme_kodu__iexact=stok_kod,
                    parti_no__iexact=parti_no,
                    renk__iexact=renk,
                    lokasyon_kodu__iexact=depo_kod
                )
                print(f"Malzeme Bulundu: {malzeme.benzersiz_id}") # ID'yi logla
            except Malzeme.DoesNotExist:
                 hata_mesaji = f"HATA: Stok Kodu: {stok_kod}, Parti: {parti_no}, Renk: {renk}, Depo: {depo_kod} kombinasyonu veritabanında bulunamadı. Lütfen arama yapıp doğru ürünü seçin."
                 print(f"KAYIT HATASI: Malzeme.DoesNotExist - {hata_mesaji}")
                 return JsonResponse({'success': False, 'message': hata_mesaji}, status=404)
            except Malzeme.MultipleObjectsReturned:
                 hata_mesaji = f"HATA: Belirtilen kombinasyon ({stok_kod}, {parti_no}, {renk}, {depo_kod}) için birden fazla stok kaydı bulundu. Veritabanı yöneticinize başvurun."
                 print(f"KAYIT HATASI: Malzeme.MultipleObjectsReturned - {hata_mesaji}")
                 return JsonResponse({'success': False, 'message': hata_mesaji}, status=400) 

            # Sayım Emrini kontrol et
            sayim_emri = get_object_or_404(SayimEmri, pk=sayim_emri_id)
            if sayim_emri.durum != 'Açık':
                 print(f"KAYIT HATASI: Sayım Emri ({sayim_emri_id}) durumu Açık değil: {sayim_emri.durum}")
                 return JsonResponse({'success': False, 'message': 'Sayım emri kapalı olduğu için kayıt yapılamaz.'}, status=403) 

            # 2. Yeni Sayım Detayını Oluştur
            print(f"Sayım Detayı Oluşturuluyor: Miktar={miktar}, Personel={personel_adi}, Lat={latitude}, Lon={longitude}")
            # --- TypeError Düzeltmesi: Modelde olmayan alanları kaldır ---
            SayimDetay.objects.create(
                sayim_emri=sayim_emri,
                benzersiz_malzeme=malzeme, # Sadece ilişkiyi kurmak yeterli
                personel_adi=personel_adi,
                sayilan_stok=miktar,
                latitude=latitude, # Modelde bu alanların olduğunu varsayıyoruz
                longitude=longitude
                # konum_hata_mesaji alanı modelde yoksa buraya eklenmemeli
            )
            print("Sayım Detayı Başarıyla Oluşturuldu.")

            # 3. Bu malzeme için güncel toplam sayılan miktarı hesapla (Sadece bu sayım emri için)
            toplam_sayilan = SayimDetay.objects.filter(
                sayim_emri=sayim_emri, 
                benzersiz_malzeme=malzeme
            ).aggregate(total_sayilan=Sum('sayilan_stok'))['total_sayilan'] or Decimal('0.0')
            print(f"Yeni Toplam Sayılan Miktar (Bu Emir İçin): {toplam_sayilan}")
            
            # Başarılı yanıt
            print(f"--- KAYIT BİTTİ (Başarılı) ---")
            return JsonResponse({
                'success': True, 
                'message': f"✅ {malzeme.malzeme_kodu} ({malzeme.parti_no}) {miktar:.2f} adet kayıt edildi.",
                'yeni_miktar': f"{toplam_sayilan:.2f}" 
            })

        except SayimEmri.DoesNotExist:
             print(f"KAYIT HATASI: SayimEmri.DoesNotExist - ID: {sayim_emri_id}")
             return JsonResponse({'success': False, 'message': "HATA: Geçersiz Sayım Emri ID'si."}, status=404)
        except json.JSONDecodeError as e:
             print(f"KAYIT HATASI: json.JSONDecodeError - {e}")
             return JsonResponse({'success': False, 'message': "HATA: Geçersiz istek verisi formatı (JSON bekleniyor)."}, status=400)
        except Exception as e:
             print(f"Kritik Kayıt Hatası ({type(e).__name__}): {e}") # Debugging için
             return JsonResponse({'success': False, 'message': f"Beklenmedik bir sunucu hatası oluştu: {type(e).__name__}. Lütfen tekrar deneyin veya yöneticiye bildirin."}, status=500)

    # POST dışındaki metodlar için
    print(f"KAYIT HATASI: Geçersiz metot ({request.method})")
    return JsonResponse({'success': False, 'message': 'Geçersiz istek metodu (POST bekleniyor).'}, status=405) 


# ####################################################################################
# ⭐ GEMINI OCR ANALİZ FONKSİYONU - Hata Yakalama İyileştirildi
# ####################################################################################

@csrf_exempt
@require_POST
def gemini_ocr_analiz(request):
    if not GEMINI_AVAILABLE:
        return JsonResponse({'success': False, 'message': "Gemini API özelliği aktif değil (kütüphane veya anahtar eksik)."}, status=501) # 501 Not Implemented
    if 'image_file' not in request.FILES:
        return JsonResponse({'success': False, 'message': "Görsel dosyası yüklenmedi."}, status=400)

    try:
        image_file = request.FILES['image_file']
        
        # Dosya boyutunu kontrol et (Örn: 5MB limit)
        if image_file.size > 5 * 1024 * 1024:
             return JsonResponse({'success': False, 'message': "Görsel boyutu çok büyük (Maks 5MB)."}, status=413) # 413 Payload Too Large

        # Görüntüyü açmayı dene
        try:
            img = Image.open(image_file)
            # Görüntü formatını kontrol et (isteğe bağlı ama önerilir)
            # img.verify() # Bu bazen dosyayı kapatır, tekrar açmak gerekebilir.
            # img = Image.open(image_file) # Tekrar aç
        except Exception as img_err:
             return JsonResponse({'success': False, 'message': f"Görsel dosyası açılamadı veya bozuk: {img_err}"}, status=400)


        # Gemini Client'ı oluştur
        genai.configure(api_key=GEMINI_API_KEY) # configure kullanmak daha standart

        # Model ve konfigürasyon
        # model = genai.GenerativeModel('gemini-1.5-flash') # Güncel model adını kullanın - Bazen 1.0 Pro daha stabil olabilir
        model = genai.GenerativeModel('gemini-pro-vision') # Veya Vision modelini deneyin

        system_instruction = (
            "You are an expert Optical Character Recognition (OCR) and data extraction system specialized in inventory labels. "
            "Analyze the labels in the image. For each distinct label, extract 'stok_kod' (stock code), 'parti_no' (batch number), "
            "'renk' (color/variant), and 'miktar' (quantity). "
            "If a field is not present on the label, use the value 'YOK'. "
            "Always return the quantity ('miktar') as a decimal number (e.g., 1.0, 500.0). "
            "Respond ONLY with a valid JSON list (array) of objects, where each object represents one label. Provide no other text or explanation."
        )
        
        prompt = (
            "Analyze all inventory labels in this image. Create a JSON list (array) using the fields 'stok_kod', 'parti_no', 'renk', and 'miktar' for each label found. "
            "Remember: Return 'miktar' as a decimal number (float)."
        )

        # API Çağrısı (GenerationConfig olmadan, Vision modeliyle deneyelim)
        # Vision modeli genellikle şema zorlamasına ihtiyaç duymaz ve daha esnektir.
        response = model.generate_content([prompt, img])


        # Yanıtı işle (response.text doğrudan JSON içerebilir)
        try:
            json_text = response.text 
            # JSON'u parse etmeden önce başında/sonunda olabilecek ```json ``` gibi işaretleri temizle
            if json_text.strip().startswith("```json"):
                json_text = json_text.strip()[7:]
            if json_text.strip().endswith("```"):
                 json_text = json_text.strip()[:-3]
                 
            json_results = json.loads(json_text.strip()) # Temizlenmiş metni parse et
            
            if not isinstance(json_results, list):
                 raise json.JSONDecodeError("API'den beklenen liste formatı gelmedi.", json_text, 0)

        except (json.JSONDecodeError, IndexError, AttributeError) as json_err:
             # print(f"Gemini JSON Parse Hatası: {json_err}, Gelen Yanıt: {response.text}") # Debugging
             return JsonResponse({'success': False, 'message': f"YZ'den gelen yanıt işlenemedi (JSON format hatası olabilir): {json_err}"}, status=500)
        except Exception as resp_err: # Diğer olası response hataları
             # print(f"Gemini Yanıt İşleme Hatası: {resp_err}, Yanıt: {response}") # Debugging
              return JsonResponse({'success': False, 'message': f"YZ yanıtı işlenirken beklenmedik hata: {resp_err}"}, status=500)


        # Sonuçları işle ve standardize et
        processed_results = []
        for item in json_results:
             if not isinstance(item, dict): continue 

             try:
                 miktar_raw = item.get('miktar', 0)
                 if isinstance(miktar_raw, str) and (miktar_raw.upper() == 'YOK' or not miktar_raw.strip()):
                      miktar_decimal = Decimal('0.0')
                 else:
                      miktar_decimal = Decimal(str(miktar_raw)) 
             except (ValueError, TypeError, Decimal.InvalidOperation):
                 miktar_decimal = Decimal('1.0') # Varsayılan 1 ata?

             stok_kod_std = standardize_id_part(item.get('stok_kod', 'YOK'))
             if stok_kod_std == 'YOK': continue # Stok kodu olmayanları atla

             processed_results.append({
                 'stok_kod': stok_kod_std,
                 'parti_no': standardize_id_part(item.get('parti_no', 'YOK')),
                 'renk': standardize_id_part(item.get('renk', 'YOK')),
                 'miktar': f"{miktar_decimal:.2f}",
                 'barkod': stok_kod_std 
             })

        count = len(processed_results)
        if count == 0:
            return JsonResponse({'success': True, 'message': "Analiz başarılı, ancak görselde geçerli (Stok Kodu olan) etiket bulunamadı.", 'count': 0, 'results': []})

        return JsonResponse({
            'success': True,
            'message': f"✅ Gemini ile {count} etiket başarıyla okundu.",
            'count': count,
            'results': processed_results
        })

    except GoogleAPIError as e: 
        # print(f"Gemini API Hatası: {e}") # Debugging
        return JsonResponse({'success': False, 'message': f"Gemini API ile iletişim hatası: {e}. API Anahtarınızı veya kotanızı kontrol edin."}, status=502) 
    except Exception as e:
        # print(f"Kritik YZ Analiz Hatası ({type(e).__name__}): {e}") # Debugging
        return JsonResponse({'success': False, 'message': f"Görsel analizi sırasında beklenmedik sunucu hatası: {e}"}, status=500)


# --- EXCEL EXPORT FONKSİYONLARI ---
@csrf_exempt
def export_excel(request, sayim_emri_id): 
    sayim_emri = get_object_or_404(SayimEmri, pk=sayim_emri_id)
    return HttpResponse(f"'{sayim_emri.ad}' için Excel Raporu İndirme İşlevi Henüz Uygulanmadı.", status=501)

@csrf_exempt
def export_mutabakat_excel(request, sayim_emri_id): 
    sayim_emri = get_object_or_404(SayimEmri, pk=sayim_emri_id)
    return HttpResponse(f"'{sayim_emri.ad}' için Mutabakat Excel İndirme İşlevi Henüz Uygulanmadı.", status=501)