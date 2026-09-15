# Premise — Kanıta dayalı literatür asistanı

Hekim ve araştırmacılar için, **maliyetsiz** akademik kaynakları tarayıp düzenleyen bir
literatür motoru. Klinik sorunuzu istediğiniz dilde yazarsınız; sistem sorguyu akademik arama
diline çevirir, PubMed / PubMed Central / Europe PMC'yi eş zamanlı tarar, ücretsiz tam metinleri
indirir, kanıt düzeyine göre sıralar ve her cümlesinde kaynak gösteren bir rapor üretir.

Arayüz İngilizcedir; **raporun dili** arama sırasında seçilir (English, Türkçe, Deutsch,
Français, Español).

```bash
py app.py
```

Tarayıcı `http://127.0.0.1:8765` adresinde otomatik açılır. Windows'ta `baslat.bat` dosyasına
çift tıklamak da yeterlidir.

---

## Ne yapar

| Aşama | Açıklama |
|---|---|
| 1. Çeviri | Soru, Gemini ile İngilizce akademik ifadeye ve MeSH terimli bir PubMed sorgusuna çevrilir |
| 2. Tarama | 3 sorgu varyantı × PubMed + Europe PMC eş zamanlı çalışır |
| 3. Birleştirme | Aynı makalenin DOI / PMID / PMCID kopyaları tek kayıtta toplanır |
| 4. Zenginleştirme | OpenAlex'ten atıf sayısı, Unpaywall'dan ücretsiz PDF bağlantısı eklenir |
| 5. Sıralama | Güncellik + kanıt düzeyi + atıf + erişilebilirlik puanı hesaplanır |
| 6. Tam metin | En nitelikli makalelerin PubMed Central tam metni indirilip özete değil **makalenin tamamına** dayanan sentez yapılır |
| 7. Sayısal çıkarım | Her makaleden etki büyüklüğü, güven aralığı, denek sayısı ve p değeri yapılandırılmış olarak çekilir (sentezle eş zamanlı çalışır, süre eklemez) |
| 8. Doğrulama | Raporda geçen her PMID sonuç kümesiyle karşılaştırılır; her sayı kaynak metinde birebir aranır, bulunamayan bulgu elenir |

Tarama sırasında **Cancel** düğmesi vardır. Arayüz anında serbest kalır, hat bir sonraki
kontrol noktasında durur ve iptal edilen tarama geçmişe kaydedilmez.

## Sayısal bulgular ve doğrulama

Kaynakça yalnızca künye listesi değil. Her makalenin altında, o makaleden çıkarılan
sayısal sonuçlar tablo halinde görünür:

| Sonlanım | Ölçüt | %95 GA | p |
|---|---|---|---|
| kardiyovasküler ölüm | HR 0.86 | 0.79 – 0.93 | |
| kalp yetmezliği yatışı | HR 0.71 | 0.67 – 0.77 | |

Bu sayılar modele yazdırılmaz, doğrulanır:

- Her değer, modele verilen kaynak metinde **birebir aranır**. Bulunamayan bulgu rapora girmez.
- Metinde eksiyle geçen bir değerin işareti geri konur. Etki büyüklüğünde işaret hatası
  klinik olarak yanıltıcıdır (`MD 136.03` ile `MD -136.03` zıt sonuçlardır).
- Oran ölçütlerinde (HR, OR, RR) güven aralığı sınırları negatif olamaz. Bazı dergiler
  aralığı `-0.69-0.95` gibi hatalı dizer; bu, ayırıcı tire olarak düzeltilir.
- Ters sıralanmış sınırlar küçükten büyüğe çevrilir.
- Lancet tarzı orta nokta (`0·79`) ve farklı tire karakterleri tek biçime indirgenir.

Elenen bulgu sayısı konsola yazılır. Bu katman, "yapay zekâ sayı uydurdu" riskini
ölçülebilir biçimde düşürür ve aracın diğer literatür araçlarından ayrıldığı noktadır.

## Önbellek

Tıbbi sorular birbirini çok tekrar eder. Aynı soru aynı ayarlarla geldiğinde hat baştan
çalışmaz, saklanan sonuç döner: model çağrısı da NCBI isteği de yapılmaz.

- Anahtar, sorunun normalleştirilmiş hâli ile sonucu etkileyen tüm parametrelerden üretilir.
  Büyük harf, noktalama ve Türkçe aksan farkları aynı satıra düşer: "Yaşlı hastalarda AF"
  ile "yasli hastalarda af" tek aramadır.
- Makale sayısı, dil, filtreler ya da tam metin ayarı değişirse anahtar da değişir, çünkü
  sonuç gerçekten farklıdır.
- Varsayılan geçerlilik süresi 7 gündür (`CACHE_TTL_DAYS`). Süresi dolan satırlar sunucu
  açılışında temizlenir.
- Önbellekten gelen her sonuç arayüzde "reused from a search N hours ago" etiketiyle
  işaretlenir ve yanındaki **run it fresh** düğmesi taramayı baştan çalıştırır. Eski bir
  cevabın yeni sanılması tıbbi bir üründe kabul edilemez, bu yüzden etiket gizlenmez.
- `GET /api/cache` isabet oranını verir, `DELETE /api/cache` önbelleği boşaltır.
  `CACHE_ENABLED=0` ile tamamen kapatılır.

Ölçülen etki: aynı sorgunun ikinci çalışması 11 saniye yerine 0,0 saniye sürüyor ve sıfır
model maliyeti üretiyor.

## Kaynağı gör

Sentezdeki her cümlenin sonunda küçük bir düğme vardır. Tıklayınca sağdan açılan panel, o
cümlenin dayandığı paragrafı makale metninden bulup gösterir; eşleşen sayılar ve terimler
vurgulanır.

Eşleştirme tamamen tarayıcıda, ek model çağrısı olmadan çalışır:

- Aday paragrafların kümesi makalenin özeti ve indirilen PMC tam metnidir.
- Kelime örtüşmesi Dice katsayısıyla ölçülür, böylece uzun paragraflar sırf uzun oldukları
  için öne geçmez.
- Ortak sayılar dört kat ağırlıklıdır. Bir etki büyüklüğü ya da denek sayısı neredeyse
  benzersizdir, kelimelerden çok daha ayırt edicidir.
- Dergilerin farklı yazımları normalleştirilir: Lancet'in orta noktası (`0·63`), binlik
  ayıraçları (`21.947` / `21 947`) ve güven aralığı tireleri aynı sayıya indirgenir.
- Her paragraf "strong / likely / weak match" olarak etiketlenir. Hiçbiri yeterince
  eşleşmezse panel bunu dürüstçe söyler, uydurma bir kaynak göstermez.

Rapor Türkçe, makale İngilizce olduğunda kelime eşleşmesi zayıflar; bu durumda sayılar
eşleştirmeyi taşır. Latin kökenli terimler (mortality / mortalite) kök eşleşmesiyle yakalanır.

## Kaynaklar

**Ücretsiz ve otomatik taranan**

- **PubMed** — NCBI E-utilities, tüm biyomedikal literatür
- **PubMed Central** — açık erişim tam metinler (XML olarak indirilip bölümlere ayrılır)
- **Europe PMC** — açık erişim durumu, atıf sayısı, PDF bağlantıları
- **OpenAlex** — atıf sayısı ve açık erişim durumu
- **Unpaywall** — yasal ücretsiz PDF kopyaları
- **StatPearls (NCBI Bookshelf)** — tamamen ücretsiz klinik başvuru bölümleri, UpToDate'in en yakın açık muadili
- **Cochrane** ve **kılavuzlar** — PubMed yayın tipi filtreleriyle

**Bağlantı olarak sunulan**

- **UpToDate** — abonelik gerektirir. İçeriği kazınmaz veya kopyalanmaz; yalnızca kendi kurumsal
  erişiminizle (TÜBİTAK ULAKBİM **EKUAL** kapsamı ya da hastane/üniversite aboneliği) açabileceğiniz
  arama bağlantısı üretilir. Telif ve kullanım şartları nedeniyle başka bir yol mümkün değildir.
- Cochrane Library, TRIP, NICE, PubMed Clinical Queries, TR Dizin, EKUAL

## Arayüzler

**Web (önerilen)**

```bash
py app.py
```

Canlı ilerleme göstergesi, sentez / makaleler / klinik kaynaklar / arama stratejisi sekmeleri,
koyu–açık tema, geçmiş ve kişisel kütüphane. PDF, Word, Markdown, RIS, BibTeX ve CSV dışa
aktarma. RIS dosyası Zotero, Mendeley ve EndNote'a doğrudan aktarılır.

PDF çıktısı raporun bölümlerini, sayısal bulgu tablolarını ve tıklanabilir PMID / DOI
bağlantılarını korur. Yazı tipi (DejaVu Sans) depoda gömülüdür, böylece Türkçe ve Avrupa dilleri
ile tıbbi semboller her sunucuda doğru basılır.

**Terminal**

```bash
py cli.py "Gebelikte hipotiroidi tedavisinde levotiroksin hedefi nedir?"
py cli.py "atrial fibrillation ablation" --sayi 20 --yil 5 --filtre meta rct
py cli.py "sepsiste vitamin C" --dil English --disa-aktar docx ris
```

`--yazar`, `--dergi`, `--sadece-acik`, `--sentez-yok`, `--tam-metin-yok`, `--sayisal-yok`
seçenekleri de vardır.
`py cli.py --help` tümünü listeler.

**Eski masaüstü arayüz**

`gui.pyw` dosyası olduğu gibi çalışmaya devam eder; yeni kaynakları kullanmaz.

## Kurulum

```bash
pip install -r requirements.txt
```

`.env.example` dosyasını `.env` olarak kopyalayıp doldurun:

- `NCBI_EMAIL` — zorunlu, NCBI kuralı
- `NCBI_API_KEY` — **ücretsiz** ve şiddetle önerilir. PubMed hız limitini 3 istek/sn'den 10 istek/sn'ye
  çıkarır; anahtarsız kullanımda paralel taramalarda "429 Too Many Requests" hataları görülür.
  [account.ncbi.nlm.nih.gov/settings](https://account.ncbi.nlm.nih.gov/settings/) adresinden alınır.
- `GEMINI_API_KEYS` — virgülle ayrılmış birden çok anahtar; biri kotayı doldurunca otomatik
  sonrakine geçilir, hepsi biterse `GROQ_API_KEY` yedeği devreye girer.

## Proje yapısı

```
app.py                  FastAPI sunucusu ve JSON API
cli.py                  Terminal arayüzü
web/                    Tek sayfa arayüz (bağımlılıksız HTML + CSS + JS, İngilizce)
assets/fonts/           PDF için gömülü DejaVu Sans yazı tipleri
litrag/
  pipeline.py           Arama hattının tamamı
  llm.py                Gemini anahtar havuzu, çeviri, sentez, atıf doğrulama
  extract.py            Sayısal sonuç çıkarımı ve kaynak metne karşı doğrulama
  models.py             Makale modeli, birleştirme ve puanlama
  store.py              SQLite geçmiş ve kütüphane
  cache.py              Arama önbelleği (normalleştirilmiş sorgu anahtarı, 7 gün)
  exporters.py          Word / RIS / BibTeX / Markdown / CSV
  pdf.py                PDF raporu (reportlab, gömülü DejaVu yazı tipi)
  ratelimit.py          NCBI hız sınırı ve tekrar deneme
  sources/
    pubmed.py           NCBI E-utilities
    europepmc.py        Europe PMC REST
    pmc.py              PMC tam metin XML ayrıştırma
    openaccess.py       OpenAlex + Unpaywall
    clinical.py         StatPearls, kılavuzlar, Cochrane, hızlı bağlantılar
```

## Sınırlar

- Üretilen sentez, kaynak makalelerle doğrulanmadan klinik kararda kullanılmamalıdır.
  Araç klinik karar desteği sağlamaz.
- Yalnızca özeti bulunan makaleler dikkate alınır; özetsiz kayıtlar elenir.
- Tam metin okuma yalnızca PubMed Central açık erişim koleksiyonu için mümkündür.
- Abonelikli içerik (UpToDate, paywall'lı dergiler) indirilmez; erişim kendi kurumsal
  aboneliğinizle sizin tarafınızdan yapılır.
