import json
import uuid
import os
import base64
import requests
import pytz
from decimal import Decimal, InvalidOperation
from datetime import datetime
from importlib import import_module

from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.db.models import Q, Sum
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required
from django.conf import settings # settings.GEMINI_API_KEY için
from django.utils import timezone
from django.utils.text import slugify
from django.db import transaction

# Third-party Imports (Excel için gerekli)
import pandas as pd
import xlsxwriter 
from io import BytesIO as IO_Bytes 


# Varsayılan modelleri içe aktar: Depo kaldırıldı.
from .models import SayimEmri, SayimDetay, Malzeme # <<< Depo kaldırıldı!

# --- YARDIMCI FONKSİYONLAR (CORE MANTIK) ---

def generate_unique_id(stok_kod, parti_no, depo_kod, renk):
    """
    Stok Kodu, Parti No, Depo Kodu ve Renk/Varyantı birleştirerek benzersiz bir ID oluşturur.
    """
    stok_kod = stok_kod.upper().strip()
    parti_no = parti_no.upper().strip() if parti_no else 'YOK'
    depo_kod = depo_kod.upper().strip()
    renk = renk.upper().strip() if renk else 'YOK'
    
    # Tüm varyantların tek bir ID'de birleşmesini sağlar.
    return f"{stok_kod}_{parti_no}_{depo_kod}_{renk}".replace(" ", "")

def get_malzeme_from_unique_id(unique_id):
    """Benzersiz ID'den Malzeme nesnesini bulur."""
    try:
        # unique_id parçalarına ayırarak malzemeyi bulma
        parts = unique_id.split('_')
        if len(parts) != 4:
            # Sadece 4 parçalı formatı desteklemiyoruz, direkt benzersiz ID ile arıyoruz.
            return Malzeme.objects.get(benzersiz_id=unique_id)
        
        stok_kod = parts[0]
        parti_no = parts[1]
        depo_kod = parts[2]
        renk = parts[3]
        
        # Django'da bir Malzeme kaydını bulma (Daha güvenli arama)
        malzeme = Malzeme.objects.get(
            stok_kod=stok_kod,
            parti_no=parti_no,
            depo_kod=depo_kod,
            renk=renk
        )
        return malzeme
    except Malzeme.DoesNotExist:
        return None
    except Exception as e:
        print(f"Malzeme ID ayrıştırma hatası: {e}")
        return None

def create_or_update_malzeme(data):
    """
    Verilen verilerle bir Malzeme nesnesi oluşturur veya günceller (UPSERT).
    """
    stok_kod = data.get('stok_kod', 'YOK').upper().strip()
    depo_kod = data.get('depo_kod', 'YOK').upper().strip()
    parti_no = data.get('parti_no', 'YOK').upper().strip()
    renk = data.get('renk', 'YOK').upper().strip()
    
    # Benzersiz ID'yi oluştururuz
    unique_id = generate_unique_id(stok_kod, parti_no, depo_kod, renk)
    
    if stok_kod == 'YOK' or depo_kod == 'YOK':
        raise ValueError("Stok ve Depo Kodu olmadan Malzeme oluşturulamaz.")

    # Veritabanında güncelleme veya oluşturma
    malzeme, created = Malzeme.objects.update_or_create(
        benzersiz_id=unique_id, # benzersiz_id'yi kullanarak arama yap (performans için ideal)
        defaults={
            'stok_kod': stok_kod,
            'malzeme_adi': data.get('malzeme_adi', f"Yeni Stok: {stok_kod}"),
            'depo_kod': depo_kod,
            'parti_no': parti_no,
            'renk': renk,
            'olcu_birimi': data.get('olcu_birimi', 'ADET'),
            'sistem_stok': Decimal('0.00'), # Yeni stoklar için sistem stoku 0.00
            'aktif': True,
        }
    )
    return malzeme, created


# --- WEB SAYFASI VIEW'LARI ---

@login_required
def sayim_emri_listesi(request):
    """Ana sayfa: Aktif sayım emirlerini listeler."""
    aktif_emirler = SayimEmri.objects.filter(durum='BASLADI').order_by('-baslangic_tarihi')
    return render(request, 'sayim/sayim_emri_listesi.html', {'aktif_emirler': aktif_emirler})

@login_required
def sayim_giris(request, sayim_emri_id):
    """Sayım Giriş Ekranı: Belirtilen sayım emri için sayım yapar."""
    sayim_emri = get_object_or_404(SayimEmri, id=sayim_emri_id)
    
    # Personel ve Depo bilgileri (Örnek: Depo kodu Sayım Emri'nden geliyor olsun)
    depo_kodu = sayim_emri.depo_kod # Depo kodu Sayım Emri'nden (örnek)
    personel_adi = request.user.get_full_name() or request.user.username
    
    # Gemini API anahtarının ayarlı olup olmadığını kontrol et
    gemini_available = bool(os.environ.get("GEMINI_API_KEY"))
    
    context = {
        'sayim_emri': sayim_emri,
        'sayim_emri_id': sayim_emri_id,
        'depo_kodu': depo_kodu,
        'personel_adi': personel_adi,
        'gemini_available': gemini_available
    }
    return render(request, 'sayim/sayim_giris.html', context)

@login_required
def raporlama_onay(request, sayim_emri_id):
    """Raporlama ve Onay Ekranı."""
    sayim_emri = get_object_or_404(SayimEmri, id=sayim_emri_id)
    
    # Sayım girişlerini Malzeme bazında toplama
    sayim_ozet = SayimDetay.objects.filter(sayim_emri=sayim_emri).values(
        'malzeme__stok_kod',
        'malzeme__malzeme_adi',
        'malzeme__parti_no',
        'malzeme__renk',
        'malzeme__sistem_stok',
        'malzeme__olcu_birimi'
    ).annotate(
        toplam_sayim_miktar=Sum('miktar')
    ).order_by('malzeme__stok_kod')

    # Rapor verilerini hazırlama ve fark hesaplama
    rapor_verileri = []
    for ozet in sayim_ozet:
        sistem_stok = ozet['malzeme__sistem_stok'] or Decimal('0.00')
        sayim_miktar = ozet['toplam_sayim_miktar'] or Decimal('0.00')
        fark = sayim_miktar - sistem_stok
        
        rapor_verileri.append({
            'stok_kod': ozet['malzeme__stok_kod'],
            'malzeme_adi': ozet['malzeme__malzeme_adi'],
            'parti_no': ozet['malzeme__parti_no'] if ozet['malzeme__parti_no'] != 'YOK' else '-',
            'renk': ozet['malzeme__renk'] if ozet['malzeme__renk'] != 'YOK' else '-',
            'olcu_birimi': ozet['malzeme__olcu_birimi'],
            'sistem_stok': f"{sistem_stok:.2f}",
            'sayim_miktar': f"{sayim_miktar:.2f}",
            'fark': f"{fark:.2f}",
            'fark_yuzdesi': f"{(fark / sistem_stok * 100):.2f}%" if sistem_stok != 0 else 'N/A',
            'fark_negatif': fark < 0
        })

    context = {
        'sayim_emri': sayim_emri,
        'rapor_verileri': rapor_verileri,
        'sayim_emri_id': sayim_emri_id
    }
    return render(request, 'sayim/raporlama_onay.html', context)


# --- AJAX FONKSİYONLARI ---

@require_GET
def ajax_akilli_stok_ara(request):
    """
    Barkod, Stok Kodu, Parti No ve Renk'e göre Malzeme arar.
    Varyant/Eşleşme bulma mantığını uygular.
    """
    seri_no = request.GET.get('seri_no', 'YOK').upper().strip()
    stok_kod_param = request.GET.get('stok_kod', 'YOK').upper().strip()
    parti_no_param = request.GET.get('parti_no', 'YOK').upper().strip()
    renk_param = request.GET.get('renk', 'YOK').upper().strip()
    depo_kod = request.GET.get('depo_kod', 'YOK').upper().strip()
    sayim_emri_id = request.GET.get('sayim_emri_id')
    
    sayim_emri = get_object_or_404(SayimEmri, id=sayim_emri_id)
    
    # 1. Seri No (Barkod) ile Arama (En Yüksek Öncelik)
    malzeme = None
    if seri_no != 'YOK':
        try:
            malzeme = Malzeme.objects.filter(
                Q(stok_kod=seri_no) | Q(barkod=seri_no), 
                depo_kod=depo_kod
            ).first()
            if malzeme:
                stok_kod_param = malzeme.stok_kod # Bulunduysa stok kodu parametresini güncelle
                parti_no_param = malzeme.parti_no
                renk_param = malzeme.renk

        except Exception as e:
            print(f"Seri No ile arama hatası: {e}")
            pass 

    # 2. Stok Kodu + Varyantlar ile Birebir Arama
    if not malzeme and stok_kod_param != 'YOK':
        try:
            malzeme = Malzeme.objects.get(
                stok_kod=stok_kod_param,
                parti_no=parti_no_param,
                renk=renk_param,
                depo_kod=depo_kod
            )
        except Malzeme.DoesNotExist:
             pass 

    # --- Yanıt Verilerini Hazırlama ---
    data = {
        'found': False,
        'urun_bilgi': 'Stok Kodu bulunamadı veya varyant eksik.',
        'stok_kod': stok_kod_param,
        'parti_no': parti_no_param,
        'renk': renk_param,
        'sistem_stok': '0.00',
        'sayilan_stok': '0.00',
        'last_sayim': None,
        'benzersiz_id': None,
        'parti_varyantlar': [],
        'renk_varyantlar': [],
        'farkli_depo_uyarisi': ''
    }

    if malzeme:
        # Malzeme bulundu, bilgileri doldur
        data['found'] = True
        data['urun_bilgi'] = f"{malzeme.malzeme_adi} ({malzeme.stok_kod})"
        data['stok_kod'] = malzeme.stok_kod
        data['parti_no'] = malzeme.parti_no
        data['renk'] = malzeme.renk
        data['sistem_stok'] = f"{malzeme.sistem_stok:.2f}"
        
        # Benzersiz ID'yi oluştur
        data['benzersiz_id'] = generate_unique_id(
            malzeme.stok_kod, malzeme.parti_no, malzeme.depo_kod, malzeme.renk
        )
        
        # Bu sayım emrindeki toplam sayım miktarını bul
        sayilan_miktar = SayimDetay.objects.filter( # SayimDetay kullanıldı
            sayim_emri=sayim_emri,
            malzeme__stok_kod=malzeme.stok_kod,
            malzeme__parti_no=malzeme.parti_no,
            malzeme__renk=malzeme.renk
        ).aggregate(Sum('miktar'))['miktar__sum'] or Decimal('0.00')
        
        data['sayilan_stok'] = f"{sayilan_miktar:.2f}"

        # Son sayım kaydını bul 
        last_sayim_giris = SayimDetay.objects.filter(malzeme=malzeme).order_by('-kayit_tarihi').first() # SayimDetay kullanıldı
        if last_sayim_giris:
            data['last_sayim'] = {
                'tarih': last_sayim_giris.kayit_tarihi.strftime("%d.%m.%Y %H:%M"),
                'personel': last_sayim_giris.personel_adi
            }

        # Depo Uyarısı 
        if malzeme.depo_kod != depo_kod: # depo_kod, sayım ekranından gelen fiili depo olmalıdır
            data['farkli_depo_uyarisi'] = f"UYARI: Ürün ({malzeme.depo_kod}) bu sayım deposu ({depo_kod}) ile eşleşmiyor!"

    elif stok_kod_param != 'YOK':
        # Malzeme bulunamadı, ancak Stok Kodu var. Varyantları ara
        data['urun_bilgi'] = f"Stok Kodu: {stok_kod_param} (Varyant Aranıyor...)"

        # 3. Aynı Stok Kodu altındaki Parti ve Renk Varyantlarını bul
        # Sadece bu depodaki varyantları dikkate al
        varyant_malzemeleri = Malzeme.objects.filter(
            stok_kod=stok_kod_param, 
            depo_kod=depo_kod
        )
        
        parti_varyantlar = set(varyant_malzemeleri.values_list('parti_no', flat=True))
        renk_varyantlar = set(varyant_malzemeleri.values_list('renk', flat=True))
        
        if 'YOK' in parti_varyantlar: parti_varyantlar.remove('YOK')
        if 'YOK' in renk_varyantlar: renk_varyantlar.remove('YOK')

        data['parti_varyantlar'] = sorted(list(parti_varyantlar))
        data['renk_varyantlar'] = sorted(list(renk_varyantlar))
        
        if data['parti_varyantlar'] or data['renk_varyantlar']:
            data['urun_bilgi'] = f"Stok Kodu: {stok_kod_param}. Lütfen varyant seçin."
        else:
            data['urun_bilgi'] = f"Stok Kodu: {stok_kod_param}. Sistemde varyant veya stok kaydı yok. Yeni kayıt oluşturulabilir."

    return JsonResponse(data)


@require_POST
@transaction.atomic
def ajax_sayim_kaydet(request, sayim_emri_id):
    """
    Stok sayım girişini kaydeder. Sistemde olmayan stoğu otomatik oluşturur (UPSERT).
    """
    try:
        data = json.loads(request.body)
        
        # Zorunlu alanları al
        benzersiz_id = data.get('benzersiz_id')
        miktar_str = data.get('miktar')
        personel_adi = data.get('personel_adi', 'Bilinmiyor')
        lat = data.get('lat', 'YOK')
        lon = data.get('lon', 'YOK')
        
        # Yeni stok oluşturmak için gelen veriler
        stok_kod_new = data.get('stok_kod', 'YOK').upper().strip()
        depo_kod_new = data.get('depo_kod', 'YOK').upper().strip()
        parti_no_new = data.get('parti_no', 'YOK').upper().strip()
        renk_new = data.get('renk', 'YOK').upper().strip()
        malzeme_adi_new = data.get('malzeme_adi', f"Yeni Stok: {stok_kod_new}")
        olcu_birimi_new = data.get('olcu_birimi', 'ADET')

        if not all([benzersiz_id, miktar_str]):
            return JsonResponse({'success': False, 'message': "Eksik parametreler."}, status=400)

        # Miktarı Decimal'e çevir
        try:
            miktar = Decimal(miktar_str.replace(',', '.').strip()) 
            if miktar <= 0:
                return JsonResponse({'success': False, 'message': "Miktar sıfırdan büyük olmalıdır."}, status=400)
        except InvalidOperation:
            return JsonResponse({'success': False, 'message': "Geçersiz miktar formatı."}, status=400)

        # 1. Sayım Emri'ni al
        sayim_emri = get_object_or_404(SayimEmri, id=sayim_emri_id)

        # 2. Malzemeyi bulmaya çalış. Eğer bulamazsa YENİ STOK OLUŞTUR
        malzeme = get_malzeme_from_unique_id(benzersiz_id)
        malzeme_created = False
        
        if not malzeme:
            # Malzeme sistemde yok. Yeni Malzeme oluşturma verilerini hazırla
            malzeme_data = {
                'stok_kod': stok_kod_new,
                'depo_kod': depo_kod_new,
                'parti_no': parti_no_new,
                'renk': renk_new,
                'malzeme_adi': malzeme_adi_new,
                'olcu_birimi': olcu_birimi_new
            }
            # Malzeme (UPSERT)
            malzeme, malzeme_created = create_or_update_malzeme(malzeme_data)
        
        if not malzeme:
             return JsonResponse({'success': False, 'message': "Kritik Hata: Malzeme bulunamadı ve oluşturulamadı."}, status=500)


        # 3. Sayım Girişi Kaydını Oluştur
        sayim_giris = SayimDetay.objects.create( # SayimDetay kullanıldı
            sayim_emri=sayim_emri,
            malzeme=malzeme,
            miktar=miktar,
            personel_adi=personel_adi,
            kayit_tarihi=timezone.now(),
            lokasyon_lat=lat,
            lokasyon_lon=lon
        )
        
        # 4. Toplam Sayılan Miktarı Güncelle (UI için)
        toplam_sayilan_miktar = SayimDetay.objects.filter( # SayimDetay kullanıldı
            sayim_emri=sayim_emri,
            malzeme=malzeme
        ).aggregate(Sum('miktar'))['miktar__sum'] or Decimal('0.00')

        # 5. Yanıt Mesajı
        mesaj = f"{malzeme.stok_kod} ({malzeme.parti_no}/{malzeme.renk}) sayım kaydedildi: {miktar:.2f} {malzeme.olcu_birimi}"
        if malzeme_created:
            mesaj = f"YENİ STOK OLUŞTURULDU ve sayım kaydedildi: {malzeme.stok_kod}."

        # 6. Yanıt Gönderme
        return JsonResponse({
            'success': True,
            'message': mesaj,
            'yeni_miktar': f"{toplam_sayilan_miktar:.2f}"
        })

    except SayimEmri.DoesNotExist: 
        print(f">> HATA: Sayım Emri ID({sayim_emri_id}) yok.")
        return JsonResponse({'success': False, 'message': "HATA: Sayım Emri bulunamadı."}, status=404)
        
    except json.JSONDecodeError: 
        print(f">> HATA: JSON Decode.")
        return JsonResponse({'success': False, 'message': "HATA: Geçersiz JSON verisi alındı."}, status=400)
        
    except Exception as e: 
        et = type(e).__name__ 
        print(f">> Kritik HATA ({et}): {e}")
        return JsonResponse({'success': False, 'message': f"Sunucu hatası ({et}). Kayıt başarısız."}, status=500)
    
    return JsonResponse({'success': False, 'message': 'Geçersiz HTTP metodu.'}, status=405)

@require_POST
def gemini_ocr_analiz(request):
    """
    Yüklenen görseldeki etiketleri Gemini Vision ile okur ve sonuçları döndürür.
    """
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        return JsonResponse({'success': False, 'message': 'API anahtarı ayarlanmamış.'}, status=500)

    if 'image_file' not in request.FILES:
        return JsonResponse({'success': False, 'message': 'Görsel dosyası bulunamadı.'}, status=400)
    
    # ... (kodun geri kalanı) ...
    
    return JsonResponse({
        'success': False,
        'message': 'Gemini Analizi için kod tamamlanmadı.',
        'count': 0,
        'results': []
    })


# --- EXCEL EXPORT --- 
@transaction.atomic
def export_mutabakat_excel(request, sayim_emri_id):
    """
    Sayım emrine ait detaylı mutabakat raporunu Excel olarak dışa aktarır.
    """
    sayim_emri = get_object_or_404(SayimEmri, id=sayim_emri_id)
    
    try:
        tum_malzemeler = Malzeme.objects.all()

        sayilan_miktarlar = {}
        # SayimDetay'dan toplam sayım miktarını çek
        for detay in SayimDetay.objects.filter(sayim_emri=sayim_emri):
            if detay.malzeme:
                malzeme_id = detay.malzeme.benzersiz_id
                sayilan_miktarlar[malzeme_id] = sayilan_miktarlar.get(malzeme_id, Decimal('0.0')) + (detay.miktar or Decimal('0.0'))

        rapor_data = []
        for malzeme in tum_malzemeler:
            malzeme_id = malzeme.benzersiz_id
            sayilan_mik_dec = sayilan_miktarlar.get(malzeme_id, Decimal('0.0'))
            sistem_mik_dec = malzeme.sistem_stok or Decimal('0.0')
            birim_fiyat_dec = malzeme.birim_fiyat or Decimal('0.0')
            
            mik_fark_dec = sayilan_mik_dec - sistem_mik_dec
            tutar_fark_dec = mik_fark_dec * birim_fiyat_dec
            sistem_tutar_dec = sistem_mik_dec * birim_fiyat_dec
            
            rapor_data.append({
                'Stok Kodu': malzeme.stok_kod,
                'Stok Adı': malzeme.malzeme_adi,
                'Parti No': malzeme.parti_no,
                'Renk': malzeme.renk,
                'Depo Kodu': malzeme.depo_kod,
                'Sistem Miktar': sistem_mik_dec,
                'Sayım Miktar': sayilan_mik_dec,
                'Miktar Fark': mik_fark_dec,
                'Birim Fiyat': birim_fiyat_dec,
                'Sistem Tutar': sistem_tutar_dec,
                'Tutar Fark': tutar_fark_dec,
                'Birim': malzeme.olcu_birimi
            })

        df = pd.DataFrame(rapor_data)
        output = IO_Bytes() 
        writer = pd.ExcelWriter(output, engine='xlsxwriter')
        
        sheet_name = slugify(sayim_emri.ad)[:30].replace('-', '_').upper() or 'MUTABAKAT'
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        writer.close()

        output.seek(0)
        file_name = f"Mutabakat_Raporu_{slugify(sayim_emri.ad)}.xlsx"
        
        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{file_name}"'
        
        return response

    except Exception as e:
        error_type = type(e).__name__
        print(f"Mutabakat Excel Dışa Aktarma Hatası ({error_type}): {e}")
        return HttpResponse(
            f"Excel oluşturulurken kritik hata oluştu: {e}. Lütfen logları kontrol edin.", 
            status=500
        )