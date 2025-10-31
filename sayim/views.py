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
from django.conf import settings 
from django.utils import timezone
from django.utils.text import slugify
from django.db import transaction

# Third-party Imports (Excel için gerekli)
import pandas as pd
import xlsxwriter 
from io import BytesIO as IO_Bytes 


# Varsayılan modelleri ve yeni formu içe aktar
from .models import SayimEmri, SayimDetay, Malzeme 
from .forms import SayimEmriForm # <<< YENİ FORMU İÇE AKTARDIK

# --- YARDIMCI FONKSİYONLAR (CORE MANTIK) ---

def generate_unique_id(stok_kod, parti_no, depo_kod, renk):
    """
    Stok Kodu, Parti No, Depo Kodu ve Renk/Varyantı birleştirerek benzersiz bir ID oluşturur.
    """
    stok_kod = stok_kod.upper().strip()
    parti_no = parti_no.upper().strip() if parti_no else 'YOK'
    depo_kod = depo_kod.upper().strip()
    renk = renk.upper().strip() if renk else 'YOK'
    
    return f"{stok_kod}_{parti_no}_{depo_kod}_{renk}".replace(" ", "")

def get_malzeme_from_unique_id(unique_id):
    """Benzersiz ID'den Malzeme nesnesini bulur."""
    try:
        return Malzeme.objects.get(benzersiz_id=unique_id)
    except Malzeme.DoesNotExist:
        parts = unique_id.split('_')
        if len(parts) == 4:
            stok_kod, parti_no, depo_kod_alias, renk = parts
            try:
                # MODEL ALANI: lokasyon_kodu
                return Malzeme.objects.get(
                    malzeme_kodu=stok_kod, parti_no=parti_no, lokasyon_kodu=depo_kod_alias, renk=renk
                )
            except Malzeme.DoesNotExist:
                return None
        return None
    except Exception as e:
        print(f"Malzeme ID ayrıştırma/bulma hatası: {e}")
        return None

def create_or_update_malzeme(data):
    """
    Verilen verilerle bir Malzeme nesnesi oluşturur veya günceller (UPSERT).
    """
    stok_kod = data.get('stok_kod', 'YOK').upper().strip()
    depo_kod = data.get('depo_kod', 'YOK').upper().strip()
    parti_no = data.get('parti_no', 'YOK').upper().strip()
    renk = data.get('renk', 'YOK').upper().strip()
    
    unique_id = generate_unique_id(stok_kod, parti_no, depo_kod, renk)
    
    if stok_kod == 'YOK' or depo_kod == 'YOK':
        raise ValueError("Stok ve Depo Kodu olmadan Malzeme oluşturulamaz.")

    malzeme, created = Malzeme.objects.update_or_create(
        benzersiz_id=unique_id,
        defaults={
            'malzeme_kodu': stok_kod, 
            'malzeme_adi': data.get('malzeme_adi', f"Yeni Stok: {stok_kod}"),
            'lokasyon_kodu': depo_kod, 
            'parti_no': parti_no,
            'renk': renk,
            'olcu_birimi': data.get('olcu_birimi', 'ADET'),
            'sistem_stogu': Decimal('0.00'), 
            'aktif': True,
        }
    )
    return malzeme, created


# --- WEB SAYFASI VIEW'LARI ---

# Bu, uygulamanın ana sayfasıdır ve giriş yapmayı gerektirir.
@login_required
def sayim_emri_listesi(request):
    """Ana sayfa: Aktif sayım emirlerini listeler."""
    aktif_emirler = SayimEmri.objects.filter(durum='BASLADI').order_by('-tarih') 
    return render(request, 'sayim/sayim_emirleri.html', {'aktif_emirler': aktif_emirler})

@login_required
def sayim_giris(request, sayim_emri_id):
    """Sayım Giriş Ekranı: Belirtilen sayım emri için sayım yapar."""
    sayim_emri = get_object_or_404(SayimEmri, id=sayim_emri_id)
    
    depo_kodu = sayim_emri.depo_kod 
    personel_adi = request.user.get_full_name() or request.user.username
    
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
    
    sayim_ozet = SayimDetay.objects.filter(sayim_emri=sayim_emri).values(
        'malzeme__malzeme_kodu', 'malzeme__malzeme_adi', 'malzeme__parti_no',
        'malzeme__renk', 'malzeme__sistem_stogu', 'malzeme__olcu_birimi'
    ).annotate(
        toplam_sayim_miktar=Sum('miktar')
    ).order_by('malzeme__malzeme_kodu')

    rapor_verileri = []
    for ozet in sayim_ozet:
        sistem_stok = ozet['malzeme__sistem_stogu'] or Decimal('0.00')
        sayim_miktar = ozet['toplam_sayim_miktar'] or Decimal('0.00')
        fark = sayim_miktar - sistem_stok
        
        rapor_verileri.append({
            'stok_kod': ozet['malzeme__malzeme_kodu'],
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

    context = {'sayim_emri': sayim_emri, 'rapor_verileri': rapor_verileri, 'sayim_emri_id': sayim_emri_id}
    return render(request, 'sayim/raporlama_onay.html', context)


# --- AUTH ve YÖNLENDİRME FONKSİYONLARI ---

# Login View: Parametresiz URL'ye karşılık gelir
def personel_login(request): 
    """
    Personel giriş ekranı. Kullanıcının Sayım Emri ve Depo Kodu seçmesini sağlar.
    """
    sayim_emirleri = SayimEmri.objects.filter(durum='BASLADI').order_by('-tarih')
    depo_kodlari = Malzeme.objects.values_list('lokasyon_kodu', flat=True).distinct().order_by('lokasyon_kodu')
    
    context = {
        'sayim_emirleri': sayim_emirleri,
        'depo_kodlari': [d for d in depo_kodlari if d and d != 'YOK'],
    }
    
    return render(request, 'sayim/personel_login.html', context)

# Yeni Sayım Emri View: Form işlemesi ve kaydı
@transaction.atomic
def yeni_sayim_emri(request):
    """Yeni sayım emri oluşturur ve Sayım Emri Listesine yönlendirir."""
    if request.method == 'POST':
        form = SayimEmriForm(request.POST) # Formu al
        if form.is_valid():
            yeni_emir = form.save(commit=False)
            yeni_emir.durum = 'BASLADI' 
            yeni_emir.tarih = timezone.now() 
            yeni_emir.save()
            
            # Başarılı kayıttan sonra Sayım Emri Listesi'ne yönlendir
            return redirect('sayim_emri_listesi') 
        # Form geçerli değilse, hatalı formu tekrar göster (aşağıdaki return ile)
    else:
        form = SayimEmriForm() # Boş formu göster
    
    return render(request, 'sayim/yeni_sayim_emri.html', {'form': form})

def depo_secim(request):
    """Depo Seçim Ekranı (Fonksiyonel yer tutucu)."""
    return render(request, 'sayim/depo_secim.html', {})

@require_POST
def set_personel_session(request):
    """Personel oturumunu ayarlar ve sayım girişine yönlendirir."""
    personel_adi = request.POST.get('personel_adi', 'MISAFIR').upper().strip()
    sayim_emri_id = request.POST.get('sayim_emri_id')
    depo_kodu = request.POST.get('depo_kodu')
    
    if not personel_adi or not sayim_emri_id or not depo_kodu:
         return redirect('personel_login') 

    request.session['current_user'] = personel_adi
    
    return redirect('sayim_giris', sayim_emri_id=sayim_emri_id)


# --- YÖNETİM ARAÇLARI (Veri Yükleme için Geri Getirildi) ---

def yonetim_araclari(request): 
    """Yönetim araçları ana sayfası."""
    return render(request, 'sayim/yonetim.html', {}) 

@require_POST
def upload_and_reload_stok_data(request):
    """
    Excel/CSV dosyasından Malzeme listesini okur ve veritabanına yükler/günceller (UPSERT).
    """
    if 'excel_file' not in request.FILES: 
        return JsonResponse({'success': False, 'message': 'Dosya bulunamadı.'}, status=400)
        
    excel_file = request.FILES['excel_file']
    
    try:
        excel_io = IO_Bytes(excel_file.read())
        
        # Dosya türüne göre okuma (CSV veya XLSX)
        if excel_file.name.endswith('.csv'):
             try:
                 df = pd.read_csv(excel_io, encoding='utf-8', sep=None, engine='python', dtype=str, keep_default_na=False)
             except:
                 excel_io.seek(0)
                 df = pd.read_csv(excel_io, encoding='latin1', sep=None, engine='python', dtype=str, keep_default_na=False)
        else: 
             df = pd.read_excel(excel_io, dtype=str, keep_default_na=False)
             
        # Sütun başlıklarını temizleme
        df.columns = df.columns.str.strip()
        
        # --- KRİTİK BAŞLIK KONTROLÜ VE ALAN EŞLEŞTİRME ---
        required_cols = ["Stok Kodu", "Miktar", "Depo Kodu", "Maliyet birim", "Birim"] 
        missing_cols = [col for col in required_cols if col not in df.columns]
        
        if missing_cols:
             return JsonResponse({'success': False, 'message': f'Eksik sütunlar: {", ".join(missing_cols)}'}, status=400)

        # Varsayılan değerler
        defaults = {
             "Parti": 'YOK', "Renk": 'YOK', "Stok Adı": '', "seri_no": 'YOK', "barkod": 'YOK'
        }
        for col, dv in defaults.items():
             if col not in df.columns: df[col] = dv
             
        created_count, updated_count, fail_count = 0, 0, 0
        
        with transaction.atomic():
            for index, row in df.iterrows():
                try:
                    # Değerleri al ve standardize et
                    stok_kod_excel = row['Stok Kodu'].upper().strip()
                    lokasyon_kodu = row['Depo Kodu'].upper().strip()
                    parti_no = row['Parti'].upper().strip()
                    renk = row['Renk'].upper().strip()
                    
                    if not stok_kod_excel or not lokasyon_kodu: raise ValueError("Stok/Depo Kodu boş olamaz.")
                    
                    # Miktar ve Fiyatı Decimal'e çevir (virgül yerine nokta kabul et)
                    miktar_str = str(row['Miktar']).replace(',', '.').strip()
                    fiyat_str = str(row['Maliyet birim']).replace(',', '.').strip()
                    
                    stok_miktari = Decimal(miktar_str) if miktar_str else Decimal('0.0')
                    birim_fiyati = Decimal(fiyat_str) if fiyat_str else Decimal('0.0')
                    
                    bid = generate_unique_id(stok_kod_excel, parti_no, lokasyon_kodu, renk)
                    
                    # Malzeme oluştur veya güncelle (UPSERT)
                    _, created = Malzeme.objects.update_or_create(
                        benzersiz_id=bid,
                        defaults={
                            'malzeme_kodu': stok_kod_excel, # ALAN ADI DÜZELTİLDİ
                            'malzeme_adi': row['Stok Adı'] or f"Stok {stok_kod_excel}",
                            'lokasyon_kodu': lokasyon_kodu, # Modeldeki doğru alan adı
                            'parti_no': parti_no,
                            'renk': renk,
                            'olcu_birimi': row['Birim'],
                            'sistem_stogu': stok_miktari, # ALAN ADI DÜZELTİLDİ
                            'birim_fiyat': birim_fiyati,
                            'seri_no': row['seri_no'], 
                            'barkod': row['barkod'], 
                        }
                    )
                    
                    if created: created_count += 1
                    else: updated_count += 1

                except Exception as e:
                    print(f"Satır {index+2} yükleme hatası: {e}")
                    fail_count += 1; continue
            
            # Başarılı dönüş
            msg = f"✅ Bitti: {created_count} yeni, {updated_count} güncellenen. Hata/Atlanan: {fail_count}."
            return JsonResponse({'success': True, 'message': msg})
        
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'Kritik Yükleme Hatası: {e}'}, status=500)


# --- AJAX FONKSİYONLARI ---

@require_GET
def ajax_akilli_stok_ara(request):
    """
    Barkod, Stok Kodu, Parti No ve Renk'e göre Malzeme arar.
    """
    seri_no = request.GET.get('seri_no', 'YOK').upper().strip()
    stok_kod_param = request.GET.get('stok_kod', 'YOK').upper().strip()
    parti_no_param = request.GET.get('parti_no', 'YOK').upper().strip()
    renk_param = request.GET.get('renk', 'YOK').upper().strip()
    depo_kod = request.GET.get('depo_kod', 'YOK').upper().strip()
    sayim_emri_id = request.GET.get('sayim_emri_id')
    
    try:
        sayim_emri = get_object_or_404(SayimEmri, id=sayim_emri_id)
    except:
        return JsonResponse({'found': False, 'urun_bilgi': 'HATA: Sayım Emri Bulunamadı.'}, status=404)

    malzeme = None
    if seri_no != 'YOK':
        malzeme = Malzeme.objects.filter(Q(malzeme_kodu=seri_no) | Q(barkod=seri_no), lokasyon_kodu=depo_kod).first() 
    
    if not malzeme and stok_kod_param != 'YOK':
        try:
            malzeme = Malzeme.objects.get(malzeme_kodu=stok_kod_param, parti_no=parti_no_param, renk=renk_param, lokasyon_kodu=depo_kod)
        except Malzeme.DoesNotExist:
             pass 

    data = {
        'found': False, 'urun_bilgi': 'Stok Kodu bulunamadı.', 'stok_kod': stok_kod_param, 'parti_no': parti_no_param, 'renk': renk_param,
        'sistem_stok': '0.00', 'sayilan_stok': '0.00', 'last_sayim': None, 'benzersiz_id': None,
        'parti_varyantlar': [], 'renk_varyantlar': [], 'farkli_depo_uyarisi': ''
    }

    if malzeme:
        data['found'] = True
        data['urun_bilgi'] = f"{malzeme.malzeme_adi} ({malzeme.malzeme_kodu})" 
        data['benzersiz_id'] = generate_unique_id(malzeme.malzeme_kodu, malzeme.parti_no, malzeme.lokasyon_kodu, malzeme.renk) 
        data['sistem_stok'] = f"{malzeme.sistem_stogu:.2f}" 
        
        sayilan_miktar = SayimDetay.objects.filter(sayim_emri=sayim_emri, malzeme=malzeme).aggregate(Sum('miktar'))['miktar__sum'] or Decimal('0.00')
        data['sayilan_stok'] = f"{sayilan_miktar:.2f}"

        if malzeme.lokasyon_kodu != depo_kod: 
            data['farkli_depo_uyarisi'] = f"UYARI: Ürün ({malzeme.lokasyon_kodu}) bu sayım deposu ({depo_kod}) ile eşleşmiyor!"

    elif stok_kod_param != 'YOK':
        varyant_malzemeleri = Malzeme.objects.filter(malzeme_kodu=stok_kod_param, lokasyon_kodu=depo_kod) 
        parti_varyantlar = set(v.parti_no for v in varyant_malzemeleri if v.parti_no != 'YOK')
        renk_varyantlar = set(v.renk for v in varyant_malzemeleri if v.renk != 'YOK')
        
        data['parti_varyantlar'] = sorted(list(parti_varyantlar))
        data['renk_varyantlar'] = sorted(list(renk_varyantlar))
        
        if data['parti_varyantlar'] or data['renk_varyantlar']:
            data['urun_bilgi'] = f"Stok Kodu: {stok_kod_param}. Lütfen varyant seçin."
        else:
            data['urun_bilgi'] = f"Stok Kodu: {stok_kod_param}. Yeni kayıt oluşturulabilir."

    return JsonResponse(data)


@require_POST
@transaction.atomic
def ajax_sayim_kaydet(request, sayim_emri_id):
    """
    Stok sayım girişini kaydeder. Sistemde olmayan stoğu otomatik oluşturur (UPSERT).
    """
    try:
        data = json.loads(request.body)
        
        benzersiz_id = data.get('benzersiz_id')
        miktar_str = data.get('miktar')
        personel_adi = data.get('personel_adi', 'Bilinmiyor')
        lat = data.get('lat', 'YOK')
        lon = data.get('lon', 'YOK')
        
        stok_kod_new = data.get('stok_kod', 'YOK').upper().strip()
        depo_kod_new = data.get('depo_kod', 'YOK').upper().strip()
        parti_no_new = data.get('parti_no', 'YOK').upper().strip()
        renk_new = data.get('renk', 'YOK').upper().strip()
        malzeme_adi_new = data.get('malzeme_adi', f"Yeni Stok: {stok_kod_new}")
        olcu_birimi_new = data.get('olcu_birimi', 'ADET')

        if not all([benzersiz_id, miktar_str]):
            return JsonResponse({'success': False, 'message': "Eksik parametreler."}, status=400)

        try:
            miktar = Decimal(miktar_str.replace(',', '.').strip()) 
            if miktar <= 0:
                return JsonResponse({'success': False, 'message': "Miktar sıfırdan büyük olmalıdır."}, status=400)
        except InvalidOperation:
            return JsonResponse({'success': False, 'message': "Geçersiz miktar formatı."}, status=400)

        sayim_emri = get_object_or_404(SayimEmri, id=sayim_emri_id)

        malzeme = get_malzeme_from_unique_id(benzersiz_id)
        malzeme_created = False
        
        if not malzeme:
            malzeme_data = {
                'stok_kod': stok_kod_new, 'depo_kod': depo_kod_new, 'parti_no': parti_no_new, 
                'renk': renk_new, 'malzeme_adi': malzeme_adi_new, 'olcu_birimi': olcu_birimi_new
            }
            malzeme, malzeme_created = create_or_update_malzeme(malzeme_data)
        
        if not malzeme:
             return JsonResponse({'success': False, 'message': "Kritik Hata: Malzeme bulunamadı ve oluşturulamadı."}, status=500)

        SayimDetay.objects.create(
            sayim_emri=sayim_emri, malzeme=malzeme, miktar=miktar,
            personel_adi=personel_adi, kayit_tarihi=timezone.now(),
            lokasyon_lat=lat, lokasyon_lon=lon
        )
        
        toplam_sayilan_miktar = SayimDetay.objects.filter(sayim_emri=sayim_emri, malzeme=malzeme).aggregate(Sum('miktar'))['miktar__sum'] or Decimal('0.00')

        mesaj = f"{malzeme.malzeme_kodu} ({malzeme.parti_no}/{malzeme.renk}) sayım kaydedildi: {miktar:.2f} {malzeme.olcu_birimi}" 
        if malzeme_created:
            mesaj = f"YENİ STOK OLUŞTURULDU ve sayım kaydedildi: {malzeme.malzeme_kodu}." 

        return JsonResponse({'success': True, 'message': mesaj, 'yeni_miktar': f"{toplam_sayilan_miktar:.2f}"})

    except SayimEmri.DoesNotExist: 
        return JsonResponse({'success': False, 'message': "HATA: Sayım Emri bulunamadı."}, status=404)
    except json.JSONDecodeError: 
        return JsonResponse({'success': False, 'message': "HATA: Geçersiz JSON verisi alındı."}, status=400)
    except Exception as e: 
        et = type(e).__name__ 
        print(f">> Kritik HATA ({et}): {e}")
        return JsonResponse({'success': False, 'message': f"Sunucu hatası ({et}). Kayıt başarısız."}, status=500)
    
    return JsonResponse({'success': False, 'message': 'Geçersiz HTTP metodu.'}, status=405)

@require_POST
def gemini_ocr_analiz(request):
    """
    Yüklenen görseldeki etiketleri Gemini Vision ile okur. (Yer tutucu)
    """
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        return JsonResponse({'success': False, 'message': 'API anahtarı ayarlanmamış.'}, status=500)

    # Bu fonksiyon tam olarak bitirilmediği için geçici bir mesaj döndürülüyor.
    return JsonResponse({'success': False, 'message': 'Gemini Analizi için kod tamamlanmadı.', 'count': 0, 'results': []})


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
        for detay in SayimDetay.objects.filter(sayim_emri=sayim_emri):
            if detay.malzeme:
                malzeme_id = detay.malzeme.benzersiz_id
                sayilan_miktarlar[malzeme_id] = sayilan_miktarlar.get(malzeme_id, Decimal('0.0')) + (detay.miktar or Decimal('0.0'))

        rapor_data = []
        for malzeme in tum_malzemeler:
            malzeme_id = malzeme.benzersiz_id
            sayilan_mik_dec = sayilan_miktarlar.get(malzeme_id, Decimal('0.0'))
            sistem_mik_dec = malzeme.sistem_stogu or Decimal('0.0') # Düzeltildi
            birim_fiyat_dec = malzeme.birim_fiyat or Decimal('0.0')
            
            mik_fark_dec = sayilan_mik_dec - sistem_mik_dec
            tutar_fark_dec = mik_fark_dec * birim_fiyat_dec
            sistem_tutar_dec = sistem_mik_dec * birim_fiyat_dec
            
            rapor_data.append({
                'Stok Kodu': malzeme.malzeme_kodu, 'Stok Adı': malzeme.malzeme_adi, 'Parti No': malzeme.parti_no, 
                'Renk': malzeme.renk, 'Depo Kodu': malzeme.lokasyon_kodu, 
                'Sistem Miktar': sistem_mik_dec, 'Sayım Miktar': sayilan_mik_dec, 'Miktar Fark': mik_fark_dec, 
                'Birim Fiyat': birim_fiyat_dec, 'Sistem Tutar': sistem_tutar_dec, 'Tutar Fark': tutar_fark_dec, 
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
        
        response = HttpResponse(output.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="{file_name}"'
        
        return response

    except Exception as e:
        error_type = type(e).__name__
        print(f"Mutabakat Excel Dışa Aktarma Hatası ({error_type}): {e}")
        return HttpResponse(f"Excel oluşturulurken kritik hata oluştu: {e}. Lütfen logları kontrol edin.", status=500)