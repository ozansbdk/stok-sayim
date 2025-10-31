from django import forms
from .models import SayimEmri # Sayım Emri modelinizi buraya import ettiğinizden emin olun.

# Mevcut Sayım Giriş Formu
class SayimGirisForm(forms.Form):
    # Stok Kodu standard kalır
    stok_kod = forms.CharField(label='1. Stok Kodu (ENTER)', max_length=100, required=False, widget=forms.TextInput(attrs={'autofocus': 'autofocus'}))
    
    # Parti No: Datalist kullanacağız, varsayılan değer atanmıyor
    parti_no = forms.CharField(label='2. Parti No (Seçim / Opsiyonel)', max_length=100, required=False, 
                                widget=forms.TextInput(attrs={'list': 'parti-datalist'}))

    # Renk: Datalist kullanacağız, varsayılan değer atanmıyor
    renk = forms.CharField(label='3. Renk / Varyant (Seçim / Opsiyonel)', max_length=100, required=False, 
                            widget=forms.TextInput(attrs={'list': 'renk-datalist'}))
                            
    miktar = forms.CharField(label='4. Sayım Miktarı', max_length=50)

# Yeni Sayım Emri Oluşturma Formu (ModelForm)
class SayimEmriForm(forms.ModelForm):
    class Meta:
        model = SayimEmri
        # KRİTİK DÜZELTME: 'depo_kod' alanı modelde OLMADIĞI için kaldırıldı. 
        # Şu an sadece 'ad' ve 'atanan_personel' alanları bekleniyor.
        fields = ['ad', 'atanan_personel'] 

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Formu daha düzenli göstermek için CSS sınıfları ekleyin
        for name, field in self.fields.items():
            field.widget.attrs.update({'class': 'form-control'})