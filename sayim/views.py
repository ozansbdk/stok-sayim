# sayim/views.py
import json
import os
import base64
import logging
from io import BytesIO as IO_Bytes
from decimal import Decimal, InvalidOperation

from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.db.models import Q, Sum
from django.views.decorators.http import require_POST, require_GET
from django.db import transaction, IntegrityError
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt

# pandas/xlsxwriter for excel export & uploads
import pandas as pd

# Gemini (Google Generative AI) client
import google.generativeai as genai

# Models & forms
from .models import Malzeme, SayimEmri, SayimDetay
from .forms import SayimEmriForm

logger = logging.getLogger(__name__)

# --- Helper functions -------------------------------------------------------

def generate_unique_id(stok_kod, parti_no, depo_kod, renk):
    """
    Tekrarlı standardizasyonu tutarlı yapmak için models.py'deki
    generate_unique_id ile aynı mantıkta çalışmalı.
    """
    def std(v):
        v = (v or '').strip().upper()
        if not v or v in ('NAN', 'NONE', 'NULL', 'NA'):
            return 'YOK'
        return v
    return f"{std(stok_kod)}_{std(parti_no)}_{std(depo_kod)}_{std(renk)}"

def safe_decimal(value, default=Decimal('0.0')):
    try:
        if value is None or (isinstance(value, str) and value.strip() == ''):
            return default
        return Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError):
        return default

def upsert_malzeme_by_bid(bid, defaults):
    """
    Benzersiz ID'ye göre güvenli UPSERT. IntegrityError durumunda retry (get+update).
    Returns (malzeme, created_bool)
    """
    try:
        malzeme, created = Malzeme.objects.update_or_create(
            benzersiz_id=bid,
            defaults=defaults
        )
        return malzeme, created
    except IntegrityError as e:
        # Çok nadir: unique constraint race condition; fallback ile getir ve güncelle
        logger.warning("IntegrityError on update_or_create (retrying get+save): %s", e)
        malzeme = Malzeme.objects.filter(benzersiz_id=bid).first()
        if malzeme:
            for k, v in defaults.items():
                setattr(malzeme, k, v)
            malzeme.save()
            return malzeme, False
        else:
            # Eğer yine yoksa yeniden raise
            raise

# --- Views: temel sayfalar --------------------------------------------------

def sayim_emri_listesi(request):
    aktif_emirler = SayimEmri.objects.filter(durum__in=['Açık','BASLADI']).order_by('-tarih')
    return render(request, 'sayim/sayim_emirleri.html', {'aktif_emirler': aktif_emirler})

def sayim_giris(request, sayim_emri_id):
    if 'current_user' not in request.session:
        return redirect('personel_login')
    sayim_emri = get_object_or_404(SayimEmri, id=sayim_emri_id)
    depo_kodu = getattr(sayim_emri, 'depo_kodu', 'YOK')
    personel_adi = request.session.get('current_user', 'MISAFIR')
    gemini_available = bool(os.getenv("GEMINI_API_KEY"))
    context = {
        'sayim_emri': sayim_emri,
        'sayim_emri_id': sayim_emri_id,
        'depo_kodu': depo_kodu,
        'personel_adi': personel_adi,
        'gemini_available': gemini_available
    }
    return render(request, 'sayim/sayim_giris.html', context)

def raporlama(request, sayim_emri_id):
    """
    Raporlama sayfası: template adın 'raporlama.html' olduğu için bu view
    rapor verilerini 'rapor_data' olarak sağlar.
    """
    sayim_emri = get_object_or_404(SayimEmri, id=sayim_emri_id)

    # Toplama: SayimDetay -> benzersiz_malzeme
    sayim_ozet = (
        SayimDetay.objects.filter(sayim_emri=sayim_emri)
        .values(
            'benzersiz_malzeme__malzeme_kodu',
            'benzersiz_malzeme__malzeme_adi',
            'benzersiz_malzeme__parti_no',
            'benzersiz_malzeme__renk',
            'benzersiz_malzeme__sistem_stogu',
            'benzersiz_malzeme__olcu_birimi',
            'benzersiz_malzeme__benzersiz_id'
        )
        .annotate(toplam_sayilan_stok=Sum('sayilan_stok'))
        .order_by('benzersiz_malzeme__malzeme_kodu')
    )

    rapor_data = []
    for o in sayim_ozet:
        sistem_stok = o.get('benzersiz_malzeme__sistem_stogu') or Decimal('0.0')
        sayilan = o.get('toplam_sayilan_stok') or Decimal('0.0')
        fark = (sayilan - sistem_stok)
        sistem_tutar = (sistem_stok or Decimal('0.0')) * (o.get('benzersiz_malzeme__benzersiz_id') and Decimal('0.0') or Decimal('0.0'))  # placeholder if needed
        # prepare display values
        try:
            fark_yuzde = f"{(fark / sistem_stok * Decimal(100)):.2f}%" if sistem_stok != 0 else "N/A"
        except Exception:
            fark_yuzde = "N/A"

        tag = ""
        if sayilan == 0:
            tag = "hic_sayilmadi"
        elif fark != 0:
            tag = "fark_var"
        else:
            tag = "tamam"

        rapor_data.append({
            'kod': o.get('benzersiz_malzeme__malzeme_kodu'),
            'ad': o.get('benzersiz_malzeme__malzeme_adi'),
            'parti': o.get('benzersiz_malzeme__parti_no') or '-',
            'renk': o.get('benzersiz_malzeme__renk') or '-',
            'birim': o.get('benzersiz_malzeme__olcu_birimi') or '',
            'sistem_mik': f"{(sistem_stok or Decimal('0.0')):.2f}",
            'sayilan_mik': f"{(sayilan or Decimal('0.0')):.2f}",
            'mik_fark': f"{fark:.2f}",
            'mik_yuzde': fark_yuzde,
            'sistem_tutar': f"{sistem_tutar:.2f}",
            'tutar_fark': f"{(fark * Decimal('0.0')):.2f}",  # placeholder
            'tag': tag
        })

    context = {'sayim_emri': sayim_emri, 'rapor_data': rapor_data}
    return render(request, 'sayim/raporlama.html', context)

# --- Auth & session helpers -------------------------------------------------

def personel_login(request):
    sayim_emirleri = SayimEmri.objects.filter(durum__in=['Açık','BASLADI']).order_by('-tarih')
    depo_kodlari = Malzeme.objects.values_list('lokasyon_kodu', flat=True).distinct().order_by('lokasyon_kodu')
    context = {
        'sayim_emirleri': sayim_emirleri,
        'depo_kodlari': [d for d in depo_kodlari if d and d != 'YOK'],
    }
    return render(request, 'sayim/personel_login.html', context)

@require_POST
def set_personel_session(request):
    personel_adi = request.POST.get('personel_adi', 'MISAFIR').upper().strip()
    sayim_emri_id = request.POST.get('sayim_emri_id')
    depo_kodu = request.POST.get('depo_kodu')
    if not personel_adi or not sayim_emri_id or not depo_kodu:
        return redirect('personel_login')
    request.session['current_user'] = personel_adi
    return redirect('sayim_giris', sayim_emri_id=sayim_emri_id)

@transaction.atomic
def yeni_sayim_emri(request):
    if request.method == 'POST':
        form = SayimEmriForm(request.POST)
        if form.is_valid():
            yeni_emir = form.save(commit=False)
            yeni_emir.durum = 'Açık'
            yeni_emir.tarih = timezone.now()
            yeni_emir.save()
            return redirect('sayim_giris', sayim_emri_id=yeni_emir.id)
    else:
        form = SayimEmriForm()
    return render(request, 'sayim/yeni_sayim_emri.html', {'form': form})

# --- Yönetim: Excel Upload (güçlendirilmiş) ------------------------------

@require_POST
def upload_and_reload_stok_data(request):
    """
    Geliştirilmiş: duplicates/unique constraint hatalarını daha dayanıklı işle.
    Excel/CSV yükler, her satır için UPSERT yapar. Hataları satır satır loglar.
    """
    if 'excel_file' not in request.FILES:
        return JsonResponse({'success': False, 'message': 'Dosya bulunamadı.'}, status=400)

    excel_file = request.FILES['excel_file']
    try:
        content = excel_file.read()
        excel_io = IO_Bytes(content)

        if excel_file.name.lower().endswith('.csv'):
            try:
                df = pd.read_csv(excel_io, encoding='utf-8', engine='python', dtype=str, keep_default_na=False)
            except Exception:
                excel_io.seek(0)
                df = pd.read_csv(excel_io, encoding='latin1', engine='python', dtype=str, keep_default_na=False)
        else:
            df = pd.read_excel(excel_io, dtype=str, keep_default_na=False)

        df.columns = df.columns.str.strip()
        required_cols = ["Stok Kodu", "Miktar", "Depo Kodu", "Maliyet birim", "Birim"]
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            return JsonResponse({'success': False, 'message': f'Eksik sütunlar: {", ".join(missing_cols)}'}, status=400)

        defaults_template = {
            'Parti': 'YOK', 'Renk': 'YOK', 'Stok Adı': '', 'seri_no': 'YOK', 'barkod': 'YOK'
        }
        for col, dv in defaults_template.items():
            if col not in df.columns:
                df[col] = dv

        created_count = updated_count = fail_count = 0

        # Satır bazlı işlem: her satır kendi atomic bloğunda (böylece tek bir hata tüm dosyayı bozmaz)
        for idx, row in df.iterrows():
            try:
                stok_kod = (row.get('Stok Kodu') or '').strip().upper()
                depo = (row.get('Depo Kodu') or '').strip().upper()
                parti = (row.get('Parti') or 'YOK').strip().upper()
                renk = (row.get('Renk') or 'YOK').strip().upper()

                if not stok_kod or not depo:
                    raise ValueError("Stok Kodu ve Depo Kodu zorunlu.")

                miktar = safe_decimal(row.get('Miktar'))
                birim_fiyat = safe_decimal(row.get('Maliyet birim'))

                bid = generate_unique_id(stok_kod, parti, depo, renk)

                defaults = {
                    'malzeme_kodu': stok_kod,
                    'malzeme_adi': row.get('Stok Adı') or f"Stok {stok_kod}",
                    'lokasyon_kodu': depo,
                    'parti_no': parti,
                    'renk': renk,
                    'olcu_birimi': row.get('Birim') or 'ADET',
                    'sistem_stogu': miktar,
                    'birim_fiyat': birim_fiyat,
                    'seri_no': row.get('seri_no') or 'YOK',
                    'barkod': row.get('barkod') or 'YOK'
                }

                with transaction.atomic():
                    malzeme, created = upsert_malzeme_by_bid(bid, defaults)
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1

            except Exception as e:
                fail_count += 1
                logger.exception("Satır %s yükleme hatası: %s", idx + 2, e)
                continue

        msg = f"✅ Bitti: {created_count} yeni, {updated_count} güncellenen. Hata/Atlanan: {fail_count}."
        return JsonResponse({'success': True, 'message': msg})

    except Exception as e:
        logger.exception("Kritik Yükleme Hatası: %s", e)
        return JsonResponse({'success': False, 'message': f'Kritik Yükleme Hatası: {e}'}, status=500)

# --- AJAX: akıllı stok arama -----------------------------------------------

@require_GET
def ajax_akilli_stok_ara(request):
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
        malzeme = Malzeme.objects.filter(
            Q(malzeme_kodu=seri_no) | Q(barkod=seri_no) | Q(seri_no=seri_no)
        ).filter(lokasyon_kodu=depo_kod).first()

    if not malzeme and stok_kod_param != 'YOK':
        malzeme = Malzeme.objects.filter(
            malzeme_kodu=stok_kod_param, parti_no=parti_no_param, renk=renk_param, lokasyon_kodu=depo_kod
        ).first()

    data = {
        'found': False, 'urun_bilgi': 'Stok Kodu bulunamadı.', 'stok_kod': stok_kod_param, 'parti_no': parti_no_param, 'renk': renk_param,
        'sistem_stok': '0.00', 'sayilan_stok': '0.00', 'last_sayim': None, 'benzersiz_id': None,
        'parti_varyantlar': [], 'renk_varyantlar': [], 'farkli_depo_uyarisi': ''
    }

    if malzeme:
        data['found'] = True
        data['urun_bilgi'] = f"{malzeme.malzeme_adi} ({malzeme.malzeme_kodu})"
        data['benzersiz_id'] = malzeme.benzersiz_id
        data['sistem_stok'] = f"{malzeme.sistem_stogu:.2f}"
        sayilan_stok = SayimDetay.objects.filter(sayim_emri=sayim_emri, benzersiz_malzeme=malzeme).aggregate(Sum('sayilan_stok'))['sayilan_stok__sum'] or Decimal('0.00')
        data['sayilan_stok'] = f"{sayilan_stok:.2f}"
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

# --- AJAX: sayım kaydet ----------------------------------------------------

@require_POST
@transaction.atomic
def ajax_sayim_kaydet(request, sayim_emri_id):
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
            miktar = safe_decimal(miktar_str)
            if miktar <= 0:
                return JsonResponse({'success': False, 'message': "Miktar sıfırdan büyük olmalıdır."}, status=400)
        except Exception:
            return JsonResponse({'success': False, 'message': "Geçersiz miktar formatı."}, status=400)

        sayim_emri = get_object_or_404(SayimEmri, id=sayim_emri_id)

        # önce benzersiz_id ile malzeme ara
        malzeme = Malzeme.objects.filter(benzersiz_id=benzersiz_id).first()
        malzeme_created = False

        if not malzeme:
            # oluştur (UPSERT)
            bid = generate_unique_id(stok_kod_new, parti_no_new, depo_kod_new, renk_new)
            defaults = {
                'malzeme_kodu': stok_kod_new,
                'malzeme_adi': malzeme_adi_new,
                'lokasyon_kodu': depo_kod_new,
                'parti_no': parti_no_new,
                'renk': renk_new,
                'olcu_birimi': olcu_birimi_new,
                'sistem_stogu': Decimal('0.0'),
                'birim_fiyat': Decimal('0.0'),
                'seri_no': 'YOK',
                'barkod': 'YOK'
            }
            with transaction.atomic():
                malzeme, created = upsert_malzeme_by_bid(bid, defaults)
                malzeme_created = created

        if not malzeme:
            return JsonResponse({'success': False, 'message': "Kritik Hata: Malzeme bulunamadı ve oluşturulamadı."}, status=500)

        # Kaydı oluştur
        SayimDetay.objects.create(
            sayim_emri=sayim_emri,
            benzersiz_malzeme=malzeme,
            sayilan_stok=miktar,
            personel_adi=personel_adi,
            kayit_tarihi=timezone.now(),
            latitude=lat, longitude=lon
        )

        toplam_sayilan_stok = SayimDetay.objects.filter(sayim_emri=sayim_emri, benzersiz_malzeme=malzeme).aggregate(Sum('sayilan_stok'))['sayilan_stok__sum'] or Decimal('0.00')

        mesaj = f"{malzeme.malzeme_kodu} ({malzeme.parti_no}/{malzeme.renk}) sayım kaydedildi: {miktar:.2f} {malzeme.olcu_birimi}"
        if malzeme_created:
            mesaj = f"YENİ STOK OLUŞTURULDU ve sayım kaydedildi: {malzeme.malzeme_kodu}."

        return JsonResponse({'success': True, 'message': mesaj, 'yeni_miktar': f"{toplam_sayilan_stok:.2f}"})

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': "Geçersiz JSON verisi."}, status=400)
    except Exception as e:
        logger.exception("Kritik HATA (ajax_sayim_kaydet): %s", e)
        return JsonResponse({'success': False, 'message': f"Sunucu hatası: {e}"}, status=500)

# --- Gemini OCR endpoints (web & mobile) ----------------------------------

def _configure_genai():
    api_key = os.getenv("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY ortam değişkeni ayarlı değil.")
    genai.configure(api_key=api_key)
    # client/ yaratma şekli SDK sürümüne göre değişebilir; burada model kullanımını kolay tutuyoruz
    return genai

@require_POST
def gemini_ocr_analiz(request):
    """
    Web formdan multipart file upload (input type=file).
    Dönen JSON yapısı: {'stok_kod':..., 'seri_no':..., 'parti_no':..., 'miktar':...}
    """
    try:
        if 'image_file' not in request.FILES:
            return JsonResponse({'success': False, 'message': 'image_file bulunamadı.'}, status=400)

        image_file = request.FILES['image_file']
        image_data = image_file.read()
        content_type = image_file.content_type or 'image/png'

        gen = _configure_genai()
        model = gen.GenerativeModel('gemini-2.5-flash')  # hızlı multimodal

        # Compose request parts: prompt + image (image as dict)
        prompt = (
            "Bu bir stok/gönderi etiketi. Sadece JSON çıktı ver. "
            "JSON içinde alanlar: stok_kod, seri_no, parti_no, miktar. Değer bulunmazsa 'YOK'. "
            "Sadece JSON ver, ek metin olmasın. Örnek: {\"stok_kod\":\"123\",\"seri_no\":\"SN1\",\"parti_no\":\"LOT1\",\"miktar\":\"5\"}"
        )

        image_part = {"mime_type": content_type, "data": image_data}

        # model.generate_content kabul şekli SDK sürümüne göre değişebilir; yaygın kullanım: list içinde prompt ve part
        response = model.generate_content([prompt, image_part])

        # response.text genelde döner; güvenli temizleme
        text = getattr(response, "text", None) or getattr(response, "output", None) or str(response)
        clean_text = str(text).strip().replace("```json", "").replace("```", "").strip()

        # Bazı durumlarda model açıklama satırları döner -> JSON parçasını bul
        # En güvenlisi en son '{' ile başlayan substring'i almak
        start = clean_text.find('{')
        end = clean_text.rfind('}')
        if start != -1 and end != -1 and end > start:
            json_text = clean_text[start:end+1]
        else:
            json_text = clean_text

        try:
            ocr_result = json.loads(json_text)
        except Exception:
            # fallback: tek satırdaki key:value yakalama (basit)
            logger.warning("Gemini OCR parse hatası, ham:%s", clean_text)
            return JsonResponse({'success': False, 'message': 'Gemini cevabı JSON formatında değil.', 'raw': clean_text}, status=500)

        return JsonResponse({'success': True, 'message': 'OCR tamamlandı.', 'results': ocr_result})

    except RuntimeError as e:
        logger.exception("Gemini konfigürasyon hatası: %s", e)
        return JsonResponse({'success': False, 'message': str(e)}, status=500)
    except Exception as e:
        logger.exception("Gemini OCR kritik hata: %s", e)
        return JsonResponse({'success': False, 'message': f"Sunucu hatası: {e}"}, status=500)

@csrf_exempt
@require_POST
def gemini_ocr_mobile(request):
    """
    Mobil cihazdan base64 image (JSON body) ile çağrılacak endpoint.
    Beklenen body: {"image_base64": "...", "prompt_extra": "... (opsiyonel)"} 
    Döner: JSON parse edilmiş alanlar.
    """
    try:
        data = json.loads(request.body)
        b64 = data.get('image_base64')
        if not b64:
            return JsonResponse({'success': False, 'message': 'image_base64 eksik.'}, status=400)

        header_split = b64.split(',', 1)
        if len(header_split) == 2:
            # "data:image/png;base64,XXXX"
            b64str = header_split[1]
        else:
            b64str = b64

        image_data = base64.b64decode(b64str)
        # opsiyonel prompt ek
        prompt_extra = data.get('prompt_extra', '')
        gen = _configure_genai()
        model = gen.GenerativeModel('gemini-2.5-flash')

        prompt = (
            "Bu bir mobil taramadır. Sadece JSON çıktı ver: stok_kod, seri_no, parti_no, miktar. "
            + (prompt_extra or "")
        )
        image_part = {"mime_type": "image/png", "data": image_data}
        response = model.generate_content([prompt, image_part])

        text = getattr(response, "text", None) or getattr(response, "output", None) or str(response)
        clean_text = str(text).strip().replace("```json", "").replace("```", "").strip()
        start = clean_text.find('{'); end = clean_text.rfind('}')
        if start != -1 and end != -1 and end > start:
            json_text = clean_text[start:end+1]
        else:
            json_text = clean_text

        try:
            ocr_result = json.loads(json_text)
        except Exception:
            logger.warning("Gemini mobile parse hatası, ham:%s", clean_text)
            return JsonResponse({'success': False, 'message': 'Gemini cevabı JSON formatında değil.', 'raw': clean_text}, status=500)

        return JsonResponse({'success': True, 'message': 'OCR tamamlandı.', 'results': ocr_result})

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Geçersiz JSON.'}, status=400)
    except RuntimeError as e:
        logger.exception("Gemini konfig hatası: %s", e)
        return JsonResponse({'success': False, 'message': str(e)}, status=500)
    except Exception as e:
        logger.exception("Gemini mobile kritik hata: %s", e)
        return JsonResponse({'success': False, 'message': f"Sunucu hatası: {e}"}, status=500)

# --- Excel export (korunan) -----------------------------------------------

@transaction.atomic
def export_mutabakat_excel(request, sayim_emri_id):
    sayim_emri = get_object_or_404(SayimEmri, id=sayim_emri_id)
    try:
        tum_malzemeler = Malzeme.objects.all()
        sayilan_miktarlar = {}
        for detay in SayimDetay.objects.filter(sayim_emri=sayim_emri):
            if detay.benzersiz_malzeme:
                mid = detay.benzersiz_malzeme.benzersiz_id
                sayilan_miktarlar[mid] = sayilan_miktarlar.get(mid, Decimal('0.0')) + (detay.sayilan_stok or Decimal('0.0'))

        rapor_data = []
        for malzeme in tum_malzemeler:
            malzeme_id = malzeme.benzersiz_id
            sayilan = sayilan_miktarlar.get(malzeme_id, Decimal('0.0'))
            sistem = malzeme.sistem_stogu or Decimal('0.0')
            birim_fiyat = malzeme.birim_fiyat or Decimal('0.0')
            fark = sayilan - sistem
            tutar_fark = fark * birim_fiyat
            raport_row = {
                'Stok Kodu': malzeme.malzeme_kodu,
                'Stok Adı': malzeme.malzeme_adi,
                'Parti No': malzeme.parti_no,
                'Renk': malzeme.renk,
                'Depo Kodu': malzeme.lokasyon_kodu,
                'Sistem Miktar': float(sistem),
                'Sayım Miktar': float(sayilan),
                'Miktar Fark': float(fark),
                'Birim Fiyat': float(birim_fiyat),
                'Sistem Tutar': float(sistem * birim_fiyat),
                'Tutar Fark': float(tutar_fark),
                'Birim': malzeme.olcu_birimi
            }
            rapor_data.append(raport_row)

        df = pd.DataFrame(rapor_data)
        output = IO_Bytes()
        writer = pd.ExcelWriter(output, engine='xlsxwriter')
        sheet_name = slugify(sayim_emri.ad)[:28].replace('-', '_').upper() or 'MUTABAKAT'
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        writer.close()
        output.seek(0)
        file_name = f"Mutabakat_Raporu_{slugify(sayim_emri.ad)}.xlsx"
        resp = HttpResponse(output.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="{file_name}"'
        return resp

    except Exception as e:
        logger.exception("Mutabakat Excel hatası: %s", e)
        return HttpResponse(f"Excel oluşturulurken hata: {e}", status=500)
