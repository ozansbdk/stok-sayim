from django.db import models
from django.utils import timezone
from decimal import Decimal # DecimalField için eklendi

# --- MERKEZİ ID TEMİZLEME VE OLUŞTURMA FONKSİYONLARI ---

def standardize_id_part(value):
    """
    ID'yi oluşturan bir parçayı temizler ve standartlaştırır (Büyük harf, YOK kontrolü).
    """
    cleaned = str(value).strip().upper()
    if not cleaned or cleaned in ('NAN', 'NONE', 'NULL', 'NA'):
        return 'YOK'
    return cleaned

def generate_unique_id(stok_kod, parti_no, lokasyon_kod, renk):
    """
    Standartlaştırılmış parçalardan ana benzersiz ID'yi oluşturur.
    """
    stok_kod_s = standardize_id_part(stok_kod)
    parti_no_s = standardize_id_part(parti_no)
    lokasyon_kod_s = standardize_id_part(lokasyon_kod)
    renk_s = standardize_id_part(renk)
    
    return f"{stok_kod_s}_{parti_no_s}_{lokasyon_kod_s}_{renk_s}"

# --------------------------------------------------------

class Malzeme(models.Model):
    # Ana Benzersiz Tanımlayıcı
    benzersiz_id = models.CharField(max_length=255, unique=True, db_index=True, editable=False)
    
    # Yeni Eklenen Alan: Seri No (Akıllı arama için öncelikli anahtar)
    seri_no = models.CharField(max_length=100, null=True, blank=True, default='YOK', db_index=True)
    
    # Stok Tanımlama Alanları
    malzeme_kodu = models.CharField(max_length=100, db_index=True) # Arama için index eklendi
    parti_no = models.CharField(max_length=100, null=True, blank=True, default='YOK', db_index=True) # Arama için index eklendi
    lokasyon_kodu = models.CharField(max_length=100, default='YOK', db_index=True) # Arama için index eklendi
    renk = models.CharField(max_length=50, null=True, blank=True, default='YOK')
    
    # Açıklayıcı Alanlar
    depo_adi = models.CharField(max_length=100, null=True, blank=True)
    stok_grup = models.CharField(max_length=100, null=True, blank=True)
    depo_sinif = models.CharField(max_length=100, null=True, blank=True)
    malzeme_adi = models.CharField(max_length=255)
    barkod = models.CharField(max_length=100, null=True, blank=True)
    olcu_birimi = models.CharField(max_length=20)
    
    # Stok/Finansal Alanlar
    # ⭐ DÜZELTME 1: FloatField -> DecimalField olarak değiştirildi
    sistem_stogu = models.DecimalField(max_digits=19, decimal_places=5, default=Decimal('0.0'))
    sistem_tutari = models.DecimalField(max_digits=19, decimal_places=5, default=Decimal('0.0'))
    birim_fiyat = models.DecimalField(max_digits=19, decimal_places=5, default=Decimal('0.0'))

    class Meta:
        verbose_name = "Malzeme"
        verbose_name_plural = "Malzemeler"
        
    def __str__(self):
        return f"{self.malzeme_kodu} ({self.benzersiz_id})"

    def save(self, *args, **kwargs):
        # Kayıt edilmeden hemen önce benzersiz_id'yi hesapla
        self.benzersiz_id = generate_unique_id(
            self.malzeme_kodu, 
            self.parti_no, 
            self.lokasyon_kodu, 
            self.renk
        )
        # ⭐ DÜZELTME 1.1: Sistem tutarını da otomatik hesapla (opsiyonel ama önerilir)
        if isinstance(self.sistem_stogu, (int, float, str)):
             self.sistem_stogu = Decimal(str(self.sistem_stogu))
        if isinstance(self.birim_fiyat, (int, float, str)):
             self.birim_fiyat = Decimal(str(self.birim_fiyat))
             
        self.sistem_tutari = self.sistem_stogu * self.birim_fiyat
        super().save(*args, **kwargs)

# --- SAYIM YÖNETİM MODELLERİ ---

class SayimEmri(models.Model):
    DURUM_SECENEKLERI = [
        ('Açık', 'Açık'),
        ('Tamamlandı', 'Tamamlandı'),
    ]

    ad = models.CharField(max_length=255, verbose_name="Sayım Emri Adı")
    tarih = models.DateTimeField(default=timezone.now)
    durum = models.CharField(max_length=20, choices=DURUM_SECENEKLERI, default='Açık')
    onay_tarihi = models.DateTimeField(null=True, blank=True)

    # ⭐ REVİZYON: Çoklu Personel Atama Alanı
    atanan_personel = models.CharField(
        max_length=255, 
        default='ATANMADI', 
        blank=True, 
        null=True,
        verbose_name="Atanan Personeller (Virgül ile ayırın)"
    ) 

    class Meta:
        verbose_name = "Sayım Emri"
        verbose_name_plural = "Sayım Emirleri"
        
    def __str__(self):
        return f"Emir ID:{self.pk} - {self.ad} ({self.durum})"


class SayimDetay(models.Model):
    sayim_emri = models.ForeignKey(SayimEmri, on_delete=models.CASCADE, related_name="detaylar")

    # Personelin bir önceki kaydı ile arasındaki zamanı hesaplamak için kullanılır
    guncellenme_tarihi = models.DateTimeField(auto_now=True) 
    
    # Malzeme modeline Foreign Key bağlantısı (Benzersiz ID'yi temsil eder)
    benzersiz_malzeme = models.ForeignKey(Malzeme, on_delete=models.CASCADE, related_name="sayim_detaylari") 
    
    # ⭐ DÜZELTME 1: FloatField -> DecimalField olarak değiştirildi
    sayilan_stok = models.DecimalField(max_digits=19, decimal_places=5)
    kayit_tarihi = models.DateTimeField(default=timezone.now)
    personel_adi = models.CharField(max_length=100, db_index=True) # Analiz için index eklendi
    saniye_stamp = models.FloatField(default=0.0) # Bu muhtemelen kullanılmıyor, kaldırılabilir

    # ⭐ YENİ EKLENEN KONUM ALANLARI
    latitude = models.CharField(max_length=50, default='YOK', blank=True, null=True)
    longitude = models.CharField(max_length=50, default='YOK', blank=True, null=True)
    loc_hata = models.CharField(max_length=255, default='', blank=True, null=True)

    class Meta:
        verbose_name = "Sayım Detay"
        verbose_name_plural = "Sayım Detayları"
        
        # ⭐ DÜZELTME 2: 'unique_together' kaldırıldı.
        # Bu kısıtlama, bir malzemeyi birden fazla kez saymanızı engelliyordu.
        # unique_together = (('sayim_emri', 'benzersiz_malzeme'),) # BU SATIR KALDIRILDI

    def __str__(self):
        # İlişkili malzeme silinmişse hata vermemesi için kontrol
        malzeme_kodu = self.benzersiz_malzeme.malzeme_kodu if self.benzersiz_malzeme else "SİLİNMİŞ MALZEME"
        return f"{malzeme_kodu} - {self.sayilan_stok} sayıldı"