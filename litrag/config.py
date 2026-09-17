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
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

# --- Model isimleri (.env ile değiştirilebilir) ---
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
GEMINI_FAST_MODEL = os.getenv("GEMINI_FAST_MODEL", GEMINI_MODEL).strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()

# --- Ağ ---
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "40"))
USER_AGENT = f"premise/1.0 (literature-review-assistant; mailto:{NCBI_EMAIL or 'anonymous@example.com'})"
CONTACT_EMAIL = NCBI_EMAIL or os.getenv("CONTACT_EMAIL", "").strip()

# --- Dosyalar ---
DB_PATH = Path(os.getenv("LITRAG_DB", BASE_DIR / "history.db"))
WEB_DIR = BASE_DIR / "web"
EXPORT_DIR = BASE_DIR / "exports"

# --- Varsayılan davranış ---
DEFAULT_MAX_ARTICLES = 15
DEFAULT_RECENT_YEARS = 10
FULLTEXT_TOP_N = int(os.getenv("FULLTEXT_TOP_N", "5"))   # kaç makalenin tam metni RAG'a girsin
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
TRIAGE_ENABLED = os.getenv("TRIAGE_ENABLED", "1").strip() not in ("0", "false", "False")
TRIAGE_CANDIDATES = int(os.getenv("TRIAGE_CANDIDATES", "30"))

# İddia doğrulama: Kısa Cevap bölümündeki her cümleyi atıf verdiği makalenin metnine
# karşı sınar. Uydurma PMID'i değil, gerçek makaleye yanlış atfı yakalar.
CLAIM_CHECK_ENABLED = os.getenv("CLAIM_CHECK_ENABLED", "1").strip() not in ("0", "false", "False")

# --- Hesaplar ve oturum ---
# Boş bırakılırsa ilk açılışta üretilip veritabanında saklanır (bkz. accounts.app_secret).
JWT_SECRET = os.getenv("JWT_SECRET", "").strip()
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()      # NCBI anahtarlarını şifreler
ACCESS_TOKEN_HOURS = int(os.getenv("ACCESS_TOKEN_HOURS", "2"))
REFRESH_TOKEN_DAYS = int(os.getenv("REFRESH_TOKEN_DAYS", "30"))

# Yönetici hesabı yalnızca ortam değişkeninden tohumlanır, koda hiç yazılmaz.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# Tarayıcıdan gelen isteklerde izin verilen kaynaklar (virgülle ayrılır)
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

# Canlıda 1 olmalı: oturum çerezi yalnızca HTTPS üzerinden gönderilir.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0").strip() not in ("0", "false", "False")

# Kullanıcıya gönderilen bağlantılarda kullanılan genel adres.
APP_URL = os.getenv("APP_URL", "http://127.0.0.1:8765").strip().rstrip("/")

# --- İşlemsel e-posta (SMTP konuşan her sağlayıcı çalışır) ---
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "1").strip() not in ("0", "false", "False")
MAIL_FROM = os.getenv("MAIL_FROM", "").strip()

VERIFY_TOKEN_HOURS = int(os.getenv("VERIFY_TOKEN_HOURS", "48"))

# Açıkken doğrulanmamış e-postayla arama yapılamaz. Mevcut hesapları kırmamak için
# varsayılan kapalı; ödeme açılmadan önce 1 yapılmalı.
REQUIRE_EMAIL_VERIFICATION = os.getenv("REQUIRE_EMAIL_VERIFICATION", "0").strip() \
    not in ("0", "false", "False")

# --- Kredi sistemi ---
# kredi = TABAN + makale sayısı + TAM_METIN_KREDI × tam metin + SENTEZ_KREDI + DOGRULAMA
CREDIT_BASE = 5
CREDIT_PER_ARTICLE = 1
CREDIT_PER_FULLTEXT = 3
CREDIT_SYNTHESIS = 5
CREDIT_VERIFY = 2          # triyaj + iddia doğrulama (ek model çağrıları)

PLANS = {
    "free":    {"label": "Ücretsiz", "credits": 150,  "fulltext": False, "price_try": 0},
    "asistan": {"label": "Asistan",  "credits": 1600, "fulltext": True,  "price_try": 349},
    "pro":     {"label": "Pro",      "credits": 2600, "fulltext": True,  "price_try": 499},
}

# Kayıt sırasında seçilebilecek roller. Genel halk 2. faza kadar kapalı (bkz. plan, madde 3).
SIGNUP_ROLES = ("physician", "student")
