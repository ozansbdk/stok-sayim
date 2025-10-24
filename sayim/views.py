# sayim/views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse, Http404
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum, F, ExpressionWrapper, DecimalField, Q
import pandas as pd
from datetime import datetime, timedelta
import os
from decimal import Decimal

# Model ve Form İthalatları (Kendi model ve form dosyalarınıza göre güncelleyin)
# (Bu kısım sizde farklı olabilir, ancak ana fonksiyonlar için gereklidir)
from .models import SayimEmri, SayimKaydi, StokMiktari
# from .forms import SayimEmriForm # Varsayalım ki bir SayimEmri formu var


# ==============================================================================
# 1. YÖNETİM PANELİ İŞLEMLERİ (Admin/Yönetici)
# ==============================================================================

def ozel_admin_login(request):
    """ Özel Yönetici Giriş Ekranı (Basit şifre kontrolü) """
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'defaultadminpass')
    
    if request.method == 'POST':
        if request.POST.get('password') == ADMIN_PASSWORD:
            request.session['is_admin'] = True
            return redirect('ozel_yonetim_paneli')
        else:
            return render(request, 'ozel_admin_login.html', {'error': 'Yanlış Yönetici Şifresi.'})
    
    return render(request, 'ozel_admin_login.html')

def ozel_yonetim_paneli(request):
    """ Yönetim Paneli - Mevcut Sayım Emirlerini Listeleme """
    if not request.session.get('is_admin'):
        return redirect('ozel_admin_login')

    emirler = SayimEmri.objects.all().order_by('-tarih')
    return render(request, 'ozel_yonetim_paneli.html', {'emirler': emirler})

def yeni_sayim_emri(request):
    """ Yeni Sayım Emri Oluşturma ve Excel Yükleme """
    if not request.session.get('is_admin'):
        return redirect('ozel_admin_login')
    
    if request.method == 'POST':
        ad = request.POST.get('ad')
        personel = request.POST.get('atanan_personel')
        excel_file = request.FILES.get('excel_file')
        
        if not ad or not excel_file:
            messages.error(request, 'Emir Adı ve Excel dosyası zorunludur.')
            return render(request, 'sayim_emri_olustur.html')

        try:
            # Pandas ile Excel okuma
            df = pd.read_excel(excel_file)
            
            # Gerekli kolonlar kontrolü (Kendi kolon adlarınıza göre güncelleyin)
            # ÖRN: 'StokKodu', 'StokAdı', 'PartiNo', 'Renk', 'Birim', 'Miktar', 'BirimFiyat', 'DepoKodu', 'Lokasyon'
            gerekli_kolonlar = ['StokKodu', 'Miktar', 'BirimFiyat', 'DepoKodu'] 
            if not all(col in df.columns for col in gerekli_kolonlar):
                messages.error(request, 'Excel dosyasında gerekli kolonlar (StokKodu, Miktar, BirimFiyat, DepoKodu vb.) bulunmuyor.')
                return render(request, 'sayim_emri_olustur.html')

            with transaction.atomic():
                # Sayım Emri Oluşturma
                yeni_emir = SayimEmri.objects.create(
                    ad=ad,
                    atanan_personel=personel if personel else "Belirtilmedi",
                    durum='Açık'
                )

                # Stok Miktarlarını Kaydetme
                stok_listesi = []
                for index, row in df.iterrows():
                    # NaN kontrolü ve varsayılan değer atama
                    miktar = row.get('Miktar', 0) if pd.notna(row.get('Miktar')) else 0
                    birim_fiyat = row.get('BirimFiyat', 0) if pd.notna(row.get('BirimFiyat')) else 0

                    stok_listesi.append(StokMiktari(
                        sayim_emri=yeni_emir,
                        kod=str(row.get('StokKodu', '')),
                        ad=str(row.get('StokAdı', '')),
                        parti=str(row.get('PartiNo', 'YOK')),
                        renk=str(row.get('Renk', 'YOK')),
                        birim=str(row.get('Birim', 'ADET')),
                        sistem_mik=Decimal(str(miktar)),
                        birim_fiyat=Decimal(str(birim_fiyat)),
                        depo_kodu=str(row.get('DepoKodu', 'MERKEZ')),
                        lokasyon=str(row.get('Lokasyon', 'YOK')), # Lokasyon kolonu yoksa 'YOK'
                        stok_grup=str(row.get('StokGrup', 'Genel')) # StokGrup kolonu yoksa 'Genel'
                    ))
                
                # Toplu Kayıt
                StokMiktari.objects.bulk_create(stok_listesi)
            
            messages.success(request, f"'{ad}' adlı yeni sayım emri başarıyla oluşturuldu ve {len(stok_listesi)} stok kaydı yüklendi.")
            return redirect('ozel_yonetim_paneli')

        except Exception as e:
            messages.error(request, f"Dosya işlenirken kritik bir hata oluştu: {e}")
            return render(request, 'sayim_emri_olustur.html')

    return render(request, 'sayim_emri_olustur.html')


# ==============================================================================
# 2. SAYIM AKIŞI İŞLEMLERİ (Personel)
# ==============================================================================

def sayim_emirleri(request):
    """ Personel Giriş Sayfası - Açık Sayım Emirlerini Listeleme """
    emirler = SayimEmri.objects.filter(durum='Açık').order_by('-tarih')
    return render(request, 'sayim_emirleri.html', {'emirler': emirler})

def depo_secim(request, sayim_emri_id):
    """ Sayım Emri seçildikten sonra Depo Seçimi Ekranı """
    try:
        sayim_emri = SayimEmri.objects.get(pk=sayim_emri_id)
    except SayimEmri.DoesNotExist:
        raise Http404("Sayım Emri bulunamadı.")
    
    # O emre ait tüm stok kayıtlarındaki DepoKodu listesi
    lokasyonlar = StokMiktari.objects.filter(sayim_emri=sayim_emri).values_list('depo_kodu', flat=True).distinct()
    
    context = {
        'sayim_emri': sayim_emri,
        'lokasyonlar': sorted(list(lokasyonlar)),
        'sayim_emri_id': sayim_emri_id # Yeni URL parametresi için düzeltildi
    }
    return render(request, 'depo_secim.html', context)

def set_personel_session(request):
    """ Personel Adını oturuma kaydet ve sayım girişine yönlendir """
    if request.method == 'POST':
        # UnboundLocalError'dan kaçınmak için değişkeni başta tanımlıyoruz.
        personel_adi = request.POST.get('personel_adi')
        sayim_emri_id = request.POST.get('sayim_emri_id')
        depo_kodu = request.POST.get('depo_kodu')
        
        if not personel_adi or not sayim_emri_id or not depo_kodu:
            messages.error(request, "Personel Adı, Emir ID ve Depo kodu boş olamaz.")
            return redirect('sayim_emirleri') # Hata durumunda ana sayfaya dön
        
        # Oturum bilgilerini kaydet
        request.session['sayim_emri_id'] = int(sayim_emri_id) # Integer olarak kaydetmek daha güvenli
        request.session['depo_kodu'] = depo_kodu
        request.session['personel_adi'] = personel_adi

        # Sayım giriş ekranına yönlendir
        return redirect('sayim_giris') # Sayım girişinin URL adını kullanıyoruz
        
    return redirect('sayim_emirleri')

def sayim_giris(request):
    """ Ana Sayım Giriş Ekranı (Oturum kontrolü ile) """
    # Oturum kontrolü
    sayim_emri_id = request.session.get('sayim_emri_id')
    depo_kodu = request.session.get('depo_kodu')
    personel_adi = request.session.get('personel_adi')

    if not sayim_emri_id or not depo_kodu or not personel_adi:
        messages.error(request, "Lütfen bir sayım emri ve depo seçimi yaparak oturum açın.")
        return redirect('sayim_emirleri')

    try:
        sayim_emri = SayimEmri.objects.get(pk=sayim_emri_id)
        if sayim_emri.durum != 'Açık':
            messages.error(request, "Bu sayım emri kapalı veya onaylanmıştır.")
            return redirect('sayim_emirleri')
    except SayimEmri.DoesNotExist:
        messages.error(request, "Seçili sayım emri bulunamadı.")
        return redirect('sayim_emirleri')
    
    # Mevcut Personelin Sayım Toplamını Getir
    sayilan_toplam = SayimKaydi.objects.filter(
        sayim_emri=sayim_emri,
        personel=personel_adi
    ).aggregate(Sum('miktar'))['miktar__sum'] or 0

    context = {
        'sayim_emri': sayim_emri,
        'depo_kodu': depo_kodu,
        'personel_adi': personel_adi,
        'sayilan_toplam': sayilan_toplam,
    }
    return render(request, 'sayim_giris.html', context)


# ==============================================================================
# 3. API UÇ NOKTALARI (AJAX)
# ==============================================================================

@csrf_exempt
def akilli_stok_ara_api(request):
    """ Stok Kodu/Parti/Lokasyon'a göre arama API'si """
    if request.method == 'POST':
        kod = request.POST.get('kod', '').strip()
        parti = request.POST.get('parti', '').strip()
        lokasyon = request.POST.get('lokasyon', '').strip()
        depo_kodu = request.POST.get('depo_kodu', '').strip() # Session'dan değil, POST'tan geldi varsayımı

        sayim_emri_id = request.session.get('sayim_emri_id')
        personel_adi = request.session.get('personel_adi')

        if not sayim_emri_id or not depo_kodu:
            return JsonResponse({'success': False, 'message': 'Oturum bilgileri eksik.'}, status=400)

        # Stok Miktarı modelinde arama
        filters = Q(sayim_emri_id=sayim_emri_id, depo_kodu=depo_kodu)
        
        if kod:
            filters &= Q(kod__icontains=kod)
        if parti and parti != 'YOK':
            filters &= Q(parti__icontains=parti)
        if lokasyon and lokasyon != 'YOK':
            filters &= Q(lokasyon__icontains=lokasyon)

        stoklar = StokMiktari.objects.filter(filters).order_by('kod', 'parti', 'lokasyon')[:50] # Limit 50
        
        if not stoklar:
            return JsonResponse({'success': False, 'message': 'Stok bulunamadı.'})

        data = []
        for stok in stoklar:
            # Personelin bu stok için yaptığı sayım toplamını al
            sayilan_mik = SayimKaydi.objects.filter(
                sayim_emri_id=sayim_emri_id,
                stok_kod=stok.kod,
                parti_no=stok.parti,
                depo_kodu=depo_kodu,
                personel=personel_adi # Sadece kendi sayımını göster
            ).aggregate(Sum('miktar'))['miktar__sum'] or 0
            
            data.append({
                'id': stok.pk,
                'kod': stok.kod,
                'ad': stok.ad,
                'parti': stok.parti,
                'renk': stok.renk,
                'birim': stok.birim,
                'sistem_mik': float(stok.sistem_mik),
                'lokasyon': stok.lokasyon,
                'sayilan_mik': float(sayilan_mik)
            })

        return JsonResponse({'success': True, 'results': data})
    return JsonResponse({'success': False, 'message': 'Geçersiz istek metodu.'}, status=400)


@csrf_exempt
def sayim_kaydet_api(request):
    """ Sayım Kaydı Ekleme API'si """
    if request.method == 'POST':
        # Oturumdan gelen bilgiler
        sayim_emri_id = request.session.get('sayim_emri_id')
        personel_adi = request.session.get('personel_adi')
        depo_kodu = request.session.get('depo_kodu')
        
        if not all([sayim_emri_id, personel_adi, depo_kodu]):
            return JsonResponse({'success': False, 'message': 'Oturum bilgisi eksik.'}, status=400)

        try:
            # POST'tan gelen bilgiler
            stok_kodu = request.POST.get('stok_kod')
            parti_no = request.POST.get('parti_no')
            lokasyon = request.POST.get('lokasyon')
            miktar = Decimal(request.POST.get('miktar', '0').replace(',', '.'))
            
            # Konum bilgileri
            lat = request.POST.get('lat')
            lng = request.POST.get('lng')

            if miktar <= 0:
                return JsonResponse({'success': False, 'message': 'Miktar sıfırdan büyük olmalıdır.'})

            with transaction.atomic():
                SayimKaydi.objects.create(
                    sayim_emri_id=sayim_emri_id,
                    personel=personel_adi,
                    depo_kodu=depo_kodu,
                    stok_kod=stok_kodu,
                    parti_no=parti_no if parti_no else 'YOK',
                    lokasyon=lokasyon if lokasyon else 'YOK',
                    miktar=miktar,
                    latitude=lat,
                    longitude=lng,
                )
            
            # Yeni Sayılan Toplamı Hesapla
            yeni_toplam = SayimKaydi.objects.filter(
                sayim_emri_id=sayim_emri_id,
                personel=personel_adi,
            ).aggregate(Sum('miktar'))['miktar__sum'] or 0
            
            return JsonResponse({
                'success': True, 
                'message': f'{stok_kodu} için {miktar} adet sayım kaydedildi.',
                'yeni_miktar': f"{float(yeni_toplam):,.2f}"
            })

        except SayimEmri.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Sayım emri bulunamadı.'}, status=404)
        except Exception as e:
            return JsonResponse({'success': False, 'message': f'Kayıt hatası: {e}'}, status=500)
    
    return JsonResponse({'success': False, 'message': 'Geçersiz istek metodu.'}, status=400)


# ==============================================================================
# 4. RAPORLAMA VE ANALİZ İŞLEMLERİ
# ==============================================================================

def raporlama_onay(request, pk):
    """ Mutabakat Raporu Görüntüleme ve Onay Öncesi Kontrol """
    if not request.session.get('is_admin'):
        return redirect('ozel_admin_login')
        
    sayim_emri = get_object_or_404(SayimEmri, pk=pk)
    
    # 1. Sayılan Miktarları Hesapla
    sayim_toplamlari = SayimKaydi.objects.filter(sayim_emri=sayim_emri).values(
        'stok_kod', 'parti_no', 'depo_kodu'
    ).annotate(
        sayilan_mik=Sum('miktar')
    ).order_by('stok_kod', 'parti_no')

    # 2. StokMiktari ile Karşılaştırma için Veriyi Hazırla
    stok_miktarlari = StokMiktari.objects.filter(sayim_emri=sayim_emri).values(
        'kod', 'ad', 'parti', 'renk', 'birim', 'sistem_mik', 'birim_fiyat'
    )
    
    rapor_data = []
    
    # Sayım toplamlarını bir sözlüğe aktar
    sayim_map = {
        (item['stok_kod'], item['parti_no'], item['depo_kodu']): item['sayilan_mik']
        for item in sayim_toplamlari
    }
    
    for stok in stok_miktarlari:
        key = (stok['kod'], stok['parti'], sayim_emri.depo_kodu) # Varsayım: StokMiktari'ndaki depo kodu kullanılmalı
        sayilan = sayim_map.get(key, Decimal(0))
        sistem = stok['sistem_mik']
        fiyat = stok['birim_fiyat']
        
        mik_fark = sayilan - sistem
        
        # Tutar hesaplaması
        sistem_tutar = sistem * fiyat
        tutar_fark = mik_fark * fiyat
        
        # Etiketleme (CSS için)
        tag = 'tam'
        if mik_fark > 0:
            tag = 'fazla'
        elif mik_fark < 0:
            tag = 'eksik'

        # Farkın yüzde olarak gösterimi
        mik_yuzde = (mik_fark / sistem * 100) if sistem else 0

        rapor_data.append({
            'kod': stok['kod'],
            'ad': stok['ad'],
            'parti': stok['parti'],
            'renk': stok['renk'],
            'birim': stok['birim'],
            'sistem_mik': f"{float(sistem):,.2f}",
            'sayilan_mik': f"{float(sayilan):,.2f}",
            'mik_fark': f"{float(mik_fark):,.2f}",
            'mik_yuzde': f"{float(mik_yuzde):,.2f}%",
            'sistem_tutar': f"{float(sistem_tutar):,.2f} ₺",
            'tutar_fark': f"{float(tutar_fark):,.2f} ₺",
            'tag': tag
        })
        
    context = {
        'sayim_emri': sayim_emri,
        'rapor_data': rapor_data,
        'pk': pk
    }
    return render(request, 'raporlama.html', context)


def stoklari_onayla(request, pk):
    """ Stokları Güncelleme ve Sayım Emrini Onaylama İşlemi """
    if not request.session.get('is_admin'):
        return redirect('ozel_admin_login')

    sayim_emri = get_object_or_404(SayimEmri, pk=pk)

    if sayim_emri.durum != 'Açık':
        messages.warning(request, f"Sayım Emri ID:{pk} zaten '{sayim_emri.durum}' durumundadır. Güncelleme yapılmadı.")
        return redirect('ozel_yonetim_paneli')

    if request.method == 'POST':
        try:
            # 1. Sayılan Miktarları Hesapla
            sayim_toplamlari = SayimKaydi.objects.filter(sayim_emri=sayim_emri).values(
                'stok_kod', 'parti_no', 'depo_kodu'
            ).annotate(
                sayilan_mik=Sum('miktar')
            )

            # 2. Stok Miktarlarını güncelle
            with transaction.atomic():
                for item in sayim_toplamlari:
                    # Sayılan miktarı StokMiktari modelindeki 'sayilan_mik' alanına yaz
                    StokMiktari.objects.filter(
                        sayim_emri=sayim_emri,
                        kod=item['stok_kod'],
                        parti=item['parti_no'],
                        depo_kodu=item['depo_kodu']
                    ).update(
                        sayilan_mik=item['sayilan_mik'],
                        durum='Sayılmış' # Yeni bir durum alanı varsayalım
                    )

                # 3. Sayım Emrinin Durumunu Tamamlandı olarak Güncelle
                sayim_emri.durum = 'Tamamlandı'
                sayim_emri.save()

            messages.success(request, f"Sayım Emri ID:{pk} başarıyla onaylandı. Stoklar güncellendi.")
            return redirect('ozel_yonetim_paneli')

        except Exception as e:
            messages.error(request, f"Stokları onaylama sırasında hata oluştu: {e}")
            return redirect('raporlama_onay', pk=pk)

    return redirect('raporlama_onay', pk=pk)


# ==============================================================================
# 5. ANALİZ RAPORLARI
# ==============================================================================

def analiz_performans(request, pk):
    """ Personel Sayım Performans Analizi """
    if not request.session.get('is_admin'):
        return redirect('ozel_admin_login')
    
    sayim_emri = get_object_or_404(SayimEmri, pk=pk)
    
    kayitlar = SayimKaydi.objects.filter(sayim_emri=sayim_emri).order_by('tarih')

    if not kayitlar.exists():
        return render(request, 'analiz_performans.html', {'sayim_emri': sayim_emri, 'hata': 'Bu emre ait sayım kaydı bulunamadı.'})

    personel_data = {}
    
    # Tüm kayıtları personel bazında topla
    for kayit in kayitlar:
        personel = kayit.personel
        tarih = kayit.tarih
        
        if personel not in personel_data:
            personel_data[personel] = {
                'ilk_kayit': tarih,
                'son_kayit': tarih,
                'kayit_sayisi': 0
            }
        
        personel_data[personel]['kayit_sayisi'] += 1
        personel_data[personel]['son_kayit'] = tarih
        # İlk kaydı güncelleme ihtiyacı yok, sadece son kaydı güncelleyeceğiz.

    analiz_data = []
    for personel, data in personel_data.items():
        ilk = data['ilk_kayit']
        son = data['son_kayit']
        toplam_kayit = data['kayit_sayisi']
        
        # Toplam süreyi hesapla (saniye cinsinden)
        if ilk == son:
            toplam_sure = timedelta(seconds=0) # Tek kayıt varsa süre 0
        else:
            toplam_sure = son - ilk
        
        toplam_sure_sn = toplam_sure.total_seconds()

        # Ortalama kayıt hızı (Kayıt başına saniye)
        if toplam_kayit > 1:
            ortalama_sure_sn = toplam_sure_sn / (toplam_kayit - 1) 
        elif toplam_kayit == 1:
            ortalama_sure_sn = 0 # Tek kayıt için hız anlamsız
        else:
            ortalama_sure_sn = 0
            
        # Süreyi okunabilir formatta göster
        def format_sure(saniye):
            if saniye < 60:
                return f"{int(saniye)} sn"
            elif saniye < 3600:
                return f"{int(saniye // 60)} dk {int(saniye % 60)} sn"
            else:
                saat = int(saniye // 3600)
                dakika = int((saniye % 3600) // 60)
                return f"{saat} sa {dakika} dk"

        analiz_data.append({
            'personel': personel,
            'toplam_kayit': toplam_kayit,
            'toplam_sure_sn': f"{toplam_sure_sn:,.0f}",
            'ortalama_sure_sn': f"{ortalama_sure_sn:,.2f}",
            'ortalama_sure_formatli': format_sure(ortalama_sure_sn)
        })

    # Hıza göre sırala (en hızlı önde)
    analiz_data.sort(key=lambda x: float(x['ortalama_sure_sn'].replace(',', '.'))) 

    context = {
        'sayim_emri': sayim_emri,
        'analiz_data': analiz_data,
    }
    return render(request, 'analiz_performans.html', context)


def analiz_konum(request, pk):
    """ Sayım Konumlarının Harita Üzerinde Gösterimi """
    if not request.session.get('is_admin'):
        return redirect('ozel_admin_login')
    
    sayim_emri = get_object_or_404(SayimEmri, pk=pk)
    
    # Konum bilgisi olan kayıtları al
    kayitlar = SayimKaydi.objects.filter(
        sayim_emri=sayim_emri,
        latitude__isnull=False, 
        longitude__isnull=False
    ).exclude(
        latitude='0', longitude='0'
    ).order_by('-tarih')

    if not kayitlar.exists():
        return render(request, 'analiz_konum.html', {'sayim_emri': sayim_emri, 'hata': 'Bu emre ait konum bilgisi olan sayım kaydı bulunamadı.'})

    konum_data = []
    for kayit in kayitlar:
        konum_data.append({
            'personel': kayit.personel,
            'tarih': kayit.tarih.strftime("%d.%m %H:%M:%S"),
            'stok': f"{float(kayit.miktar):,.2f} - {kayit.stok_kod}",
            'lat': float(kayit.latitude),
            'lng': float(kayit.longitude)
        })

    context = {
        'sayim_emri': sayim_emri,
        'konum_data_json': konum_data, # JSON formatında template'e gönderilecek
    }
    return render(request, 'analiz_konum.html', context)


def analiz_fark_ozeti(request, pk):
    """ Stok Gruplarına Göre Fark Özeti Raporu """
    if not request.session.get('is_admin'):
        return redirect('ozel_admin_login')
        
    sayim_emri = get_object_or_404(SayimEmri, pk=pk)

    # 1. Sayılan Miktarları Hesapla (Stok Grubu Bazında)
    sayim_toplamlari = SayimKaydi.objects.filter(sayim_emri=sayim_emri).values(
        'stok_kod', 'parti_no', 'depo_kodu'
    ).annotate(
        sayilan_mik=Sum('miktar')
    )
    
    # Sayım toplamlarını bir sözlüğe aktar
    sayim_map = {
        (item['stok_kod'], item['parti_no'], item['depo_kodu']): item['sayilan_mik']
        for item in sayim_toplamlari
    }
    
    # 2. Stok Miktari'ndaki verileri grupla ve farkları hesapla
    stok_miktarlari = StokMiktari.objects.filter(sayim_emri=sayim_emri)

    grup_ozeti = {}

    for stok in stok_miktarlari:
        stok_grup = stok.stok_grup # Modelde bu alanın var olduğunu varsayıyoruz
        if stok_grup not in grup_ozeti:
            grup_ozeti[stok_grup] = {
                'sistem_mik': Decimal(0),
                'sistem_tutar': Decimal(0),
                'fazla_mik': Decimal(0),
                'fazla_tutar': Decimal(0),
                'eksik_mik': Decimal(0),
                'eksik_tutar': Decimal(0),
            }

        key = (stok.kod, stok.parti, stok.depo_kodu)
        sayilan = sayim_map.get(key, Decimal(0))
        sistem = stok.sistem_mik
        fiyat = stok.birim_fiyat
        mik_fark = sayilan - sistem
        tutar_fark = mik_fark * fiyat
        
        # Grup toplamlarını güncelle
        grup_ozeti[stok_grup]['sistem_mik'] += sistem
        grup_ozeti[stok_grup]['sistem_tutar'] += (sistem * fiyat)

        if mik_fark > 0:
            grup_ozeti[stok_grup]['fazla_mik'] += mik_fark
            grup_ozeti[stok_grup]['fazla_tutar'] += tutar_fark
        elif mik_fark < 0:
            grup_ozeti[stok_grup]['eksik_mik'] += abs(mik_fark)
            grup_ozeti[stok_grup]['eksik_tutar'] += abs(tutar_fark)

    analiz_data = []
    for grup, veriler in grup_ozeti.items():
        analiz_data.append({
            'grup': grup,
            'sistem_mik': f"{float(veriler['sistem_mik']):,.2f}",
            'sistem_tutar': f"{float(veriler['sistem_tutar']):,.2f} ₺",
            'fazla_mik': f"{float(veriler['fazla_mik']):,.2f}",
            'fazla_tutar': f"{float(veriler['fazla_tutar']):,.2f} ₺",
            'eksik_mik': f"{float(veriler['eksik_mik']):,.2f}",
            'eksik_tutar': f"{float(veriler['eksik_tutar']):,.2f} ₺",
        })

    # En çok fark olan grupları öne almak için tutar farkı büyüklüğüne göre sırala
    analiz_data.sort(key=lambda x: float(x['fazla_tutar'].split()[0].replace(',', '.')) + float(x['eksik_tutar'].split()[0].replace(',', '.')), reverse=True)

    context = {
        'sayim_emri': sayim_emri,
        'analiz_data': analiz_data,
    }
    return render(request, 'analiz_fark_ozeti.html', context)