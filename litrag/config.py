"""Ortam değişkenleri ve uygulama ayarları."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

APP_NAME = "Premise"

# --- Kimlik / API anahtarları ---
NCBI_EMAIL = os.getenv("NCBI_EMAIL", "").strip()
NCBI_API_KEY = os.getenv("NCBI_API_KEY", "").strip()
GEMINI_API_KEYS = [k.strip() for k in os.getenv("GEMINI_API_KEYS", "").split(",") if k.strip()]

# --- Model isimleri (.env ile değiştirilebilir) ---
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
GEMINI_FAST_MODEL = os.getenv("GEMINI_FAST_MODEL", GEMINI_MODEL).strip()
# Güç modlarının kullandığı modeller (bkz. modes.py). Bunlar olmadan her aşama pahalı
# modelde koşar; "fast" bayrağı yıllarca tam da bu yüzden etkisizdi.
# Yüksek modun sentezi bilerek GEMINI_MODEL'den ayrıdır: .env'deki eski değer
# (gemini-3.5-flash, 1.50/9.00 USD) buraya sızarsa tek başına 5 TL tavanını doldurur.
GEMINI_HIGH_MODEL = os.getenv("GEMINI_HIGH_MODEL", "gemini-3.6-flash").strip()
GEMINI_MID_MODEL = os.getenv("GEMINI_MID_MODEL", "gemini-3.5-flash-lite").strip()
GEMINI_LITE_MODEL = os.getenv("GEMINI_LITE_MODEL", "gemini-3.1-flash-lite").strip()
# Tek bir model çağrısının en uzun süresi. Yüksek modun sentezi (10.000 token'a kadar)
# bunun rahatça altında kalır; aşan çağrı hata sayılır, arama ücretlendirilmez.
GEMINI_TIMEOUT_SECONDS = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "180"))

# --- Maliyet ölçümü ---
USD_TRY = float(os.getenv("USD_TRY", "49"))
# Model -> (girdi, çıktı) USD / 1M token. Flash fiyatları 1 Ocak 2027'de iki katına
# çıkıyor; o gün burayı güncellemek yeterli, mod profilleri zamma dayanıklı kuruldu.
MODEL_PRICES = {
    "gemini-3.8-flash":      (0.75, 3.75),
    "gemini-3.7-flash":      (0.75, 3.75),
    "gemini-3.6-flash":      (0.75, 3.75),
    "gemini-3.5-flash":      (1.50, 9.00),   # .env'deki eski GEMINI_MODEL
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.1-pro-preview": (2.00, 12.00),
}
# Tanınmayan bir model .env ile devreye alınırsa maliyeti olduğundan ucuz saymaktansa
# en pahalı bilinen modelmiş gibi sayarız: yanlış taraf ucuz olan değil, pahalı olandır.
UNKNOWN_MODEL_PRICE = (2.00, 12.00)

# --- Ağ ---
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "40"))
USER_AGENT = f"premise/1.0 (literature-review-assistant; mailto:{NCBI_EMAIL or 'anonymous@example.com'})"
CONTACT_EMAIL = NCBI_EMAIL or os.getenv("CONTACT_EMAIL", "").strip()

# --- Dosyalar ---
DB_PATH = Path(os.getenv("LITRAG_DB", BASE_DIR / "history.db"))

# Verilmişse Postgres'e (Neon), verilmemişse yerel SQLite dosyasına bağlanılır.
# Render'ın kapsayıcı diski geçicidir: her yeniden başlatmada sıfırlanır. Ücretli
# diske para verilmediği için kalıcılık Postgres'ten geliyor; SQLite artık yalnız
# yerel geliştirme ve testler için.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Render'da çalışıp da hâlâ SQLite kullanıyorsa (DATABASE_URL boşsa) veri geçicidir.
DB_IS_EPHEMERAL = bool(os.getenv("RENDER")) and not DATABASE_URL
WEB_DIR = BASE_DIR / "web"
EXPORT_DIR = BASE_DIR / "exports"

# --- Varsayılan davranış ---
DEFAULT_MAX_ARTICLES = 15
DEFAULT_RECENT_YEARS = 10
# Kaç makalenin tam metni okunacağı ve ne kadarının prompt'a gireceği güç moduna
# bağlıdır (bkz. modes.py); buradaki sınır yalnızca indirmenin üst çatısıdır.
FULLTEXT_CHAR_LIMIT = int(os.getenv("FULLTEXT_CHAR_LIMIT", "14000"))
# "Kaynağı gör" panelinde saklanan tam metin parçasının üst sınırı
SOURCE_EXCERPT_LIMIT = int(os.getenv("SOURCE_EXCERPT_LIMIT", "9000"))
# Aynı soru tekrar sorulduğunda saklanan sonucun geçerli kalacağı süre
CACHE_TTL_DAYS = int(os.getenv("CACHE_TTL_DAYS", "7"))
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "1").strip() not in ("0", "false", "False")

# Taranan kayıt sayısı bunun altında kalırsa tarih/filtre sınırları olmadan bir kez daha taranır.
MIN_EVIDENCE_POOL = int(os.getenv("MIN_EVIDENCE_POOL", "8"))

# --- Cevap güvenlik katmanı ---
# Sentezin çalışabilmesi için gereken en az "soruyla alakalı" makale sayısı. Altına
# düşüldüğünde hat hiç cevap üretmez; kullanıcı boş dönmeyi, kendinden emin ve yanlış
# bir cevaba tercih eder (bkz. pipeline._answer_mode).
MIN_RELEVANT_ARTICLES = int(os.getenv("MIN_RELEVANT_ARTICLES", "3"))

# Bunun altındaki her cevap "ince kanıt" sayılır. Kullanıcının istediği makale sayısından
# bağımsızdır: bir cevabın sağlamlığı talebe değil, elde gerçekten ne olduğuna bağlıdır.
LOW_EVIDENCE_FLOOR = int(os.getenv("LOW_EVIDENCE_FLOOR", "5"))

# Alaka triyajı: sentezden önce makalelerin soruyu gerçekten ele alıp almadığını ölçer.
# Kaç adayın triyajdan geçeceği güç moduna bağlıdır (bkz. modes.py).
TRIAGE_ENABLED = os.getenv("TRIAGE_ENABLED", "1").strip() not in ("0", "false", "False")

# Konusu soruyla en çok örtüşen kaç makale, puanı ne olursa olsun triyaja girmeyi
# garantiler. Puanda atıf sayısı konu örtüşmesini bastırabildiği için gerekli.
TOPIC_QUOTA = int(os.getenv("TOPIC_QUOTA", "4"))

# İddia doğrulama: Kısa Cevap bölümündeki her cümleyi atıf verdiği makalenin metnine
# karşı sınar. Uydurma PMID'i değil, gerçek makaleye yanlış atfı yakalar.
CLAIM_CHECK_ENABLED = os.getenv("CLAIM_CHECK_ENABLED", "1").strip() not in ("0", "false", "False")

# --- Hesaplar ve oturum ---
# Boş bırakılırsa ilk açılışta üretilip veritabanında saklanır (bkz. accounts.app_secret).
JWT_SECRET = os.getenv("JWT_SECRET", "").strip()
ACCESS_TOKEN_HOURS = int(os.getenv("ACCESS_TOKEN_HOURS", "2"))
REFRESH_TOKEN_DAYS = int(os.getenv("REFRESH_TOKEN_DAYS", "30"))

# Yönetici hesabı yalnızca ortam değişkeninden tohumlanır, koda hiç yazılmaz.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# Sınırsız kredili, arayüzde kalp rozetiyle gösterilen özel hesap. Yalnızca e-postası
# doğrulanmışsa geçerlidir; yoksa bu adresle ilk kaydolan herkes sınırsız kredi alırdı.
# Adres yalnızca ortam değişkeninden gelir: depo herkese açık, kişisel bir adres koda
# yazılmaz. Boşsa özel hesap yoktur.
LOVE_EMAIL = os.getenv("LOVE_EMAIL", "").strip().lower()

# Render gibi bir ters vekilin arkasında request.client.host vekilin adresidir; bütün
# kullanıcılar tek IP görünür ve IP başına hız sınırları herkesi birlikte kilitler.
# Açıkken istemci IP'si vekilin eklediği başlıklardan okunur (Render'da otomatik açık).
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "1" if os.getenv("RENDER") else "0"
                                ).strip() not in ("0", "false", "False")

# Tarayıcıdan gelen isteklerde izin verilen kaynaklar (virgülle ayrılır)
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

# Oturum çerezi yalnızca HTTPS üzerinden gönderilir. Canlıda varsayılan açık:
# "unutulduğunda güvensiz" bir ayar, er ya da geç unutulur.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1" if os.getenv("RENDER") else "0"
                          ).strip() not in ("0", "false", "False")

# HTTP ile gelen istekler HTTPS'e yönlendirilir. Yerelde kapalı (sertifika yok);
# canlıda açık, çünkü çerez Secure olsa bile ilk HTTP isteği hâlâ dinlenebilir.
FORCE_HTTPS = os.getenv("FORCE_HTTPS", "1" if os.getenv("RENDER") else "0"
                        ).strip() not in ("0", "false", "False")

# Çerezsiz, kişiyi tanımlamayan ziyaret sayacı (Plausible veya Umami). İkisi de boşsa
# hiçbir analitik betiği yüklenmez. Örn. ANALYTICS_SRC=https://plausible.io/js/script.js
# ANALYTICS_SITE=premise.example.com (Umami için web sitesi kimliği).
ANALYTICS_SRC = os.getenv("ANALYTICS_SRC", "").strip()
ANALYTICS_SITE = os.getenv("ANALYTICS_SITE", "").strip()

# Kullanıcıya gönderilen bağlantılarda kullanılan genel adres.
APP_URL = os.getenv("APP_URL", "http://127.0.0.1:8765").strip().rstrip("/")

# --- İşlemsel e-posta (Brevo HTTP API; bkz. mailer.py) ---
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()
MAIL_FROM = os.getenv("MAIL_FROM", "").strip()

VERIFY_TOKEN_HOURS = int(os.getenv("VERIFY_TOKEN_HOURS", "48"))

# Açıkken doğrulanmamış e-postayla arama yapılamaz. Mevcut hesapları kırmamak için
# varsayılan kapalı; ödeme açılmadan önce 1 yapılmalı.
REQUIRE_EMAIL_VERIFICATION = os.getenv("REQUIRE_EMAIL_VERIFICATION", "0").strip() \
    not in ("0", "false", "False")

# --- Kredi sistemi ---
# Kredi artık makale/tam metin formülünden değil, aramanın gerçekten harcadığı token
# maliyetinden hesaplanır (bkz. meter.py). 1 kredi = CREDIT_TRY kadar gerçek API gideri.
# Free planın 150 kredisi bu orana göre tam 5 TL'lik bir tavan demektir.
CREDIT_TRY = float(os.getenv("CREDIT_TRY", "0.0333"))

# Ücretli katman henüz açılmadı: her hesap aynı tavanı paylaşır. 150 kredi,
# CREDIT_TRY oranıyla tam olarak 5 TL'lik gerçek API giderine denktir — yani tek bir
# hesabın bize maliyeti hiçbir koşulda 5 TL'yi geçemez. Satış açıldığında planlar
# burada kredi sayısı ve fiyatla ayrışacak; anahtarlar o gün için duruyor.
ACCOUNT_CREDITS = int(os.getenv("ACCOUNT_CREDITS", "150"))
_SHARED_PLAN = {"credits": ACCOUNT_CREDITS, "fulltext": True, "price_try": 0,
                "modes": ("low", "medium", "high")}

PLANS = {
    "free":    {"label": "Free", **_SHARED_PLAN},
    "asistan": {"label": "Assistant", **_SHARED_PLAN},
    "pro":     {"label": "Pro", **_SHARED_PLAN},
}

# Kayıt sırasında seçilebilecek roller. Genel halk 2. faza kadar kapalı (bkz. plan, madde 3).
SIGNUP_ROLES = ("physician", "student")
