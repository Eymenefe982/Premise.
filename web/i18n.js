/* Arayüz çevirisi.
 *
 * Cevap dilinden (aramanın "Gelişmiş" panelindeki ayarı) BİLEREK ayrıdır: modeli
 * herhangi bir dilde yazdırabiliriz, ama arayüz metinlerinin çevrilmiş olması gerekir.
 * Bu yüzden burada yalnız gerçekten çevrilmiş diller var; yarım çevrilmiş bir arayüz,
 * İngilizce bir arayüzden kötüdür.
 *
 * Sözlük anahtar başına iki dili YAN YANA tutar. Dile göre ayrı bloklar daha düzenli
 * görünürdü ama bir metni güncelleyen kişinin diğerini atlaması işten bile olmazdı;
 * böyle yazıldığında eksik çeviri göze batar.
 *
 * Kullanımı — HTML tarafında:
 *   <span data-i18n="search.button">Search</span>        metin
 *   <input data-i18n-placeholder="search.placeholder">    öznitelik
 *   <p data-i18n-html="privacy.banner">                   içinde bağlantı/kalın olanlar
 * JS tarafında: t("errors.network")
 *
 * data-i18n-html yalnız BU dosyadaki sabit metinlerle kullanılır. Dışarıdan gelen
 * hiçbir şey buraya girmez; kullanıcı verisi app.js'teki esc() ile kaçışlanır.
 */
(function () {
  "use strict";

  var DEFAULT = "en";
  var SUPPORTED = ["en", "tr"];
  var STORAGE_KEY = "premise_ui_lang";

  var STRINGS = {
    /* ---------------------------------------------------------------- ortak */
    "common.close":        { en: "Close",        tr: "Kapat" },
    "common.refresh":      { en: "Refresh",      tr: "Yenile" },
    "common.cancel":       { en: "Cancel",       tr: "İptal" },
    "common.save":         { en: "Save",         tr: "Kaydet" },
    "common.offline":      { en: "offline",      tr: "çevrimdışı" },
    "common.connecting":   { en: "connecting…",  tr: "bağlanıyor…" },
    "common.credits":      { en: "{n} credits",  tr: "{n} kredi" },
    "common.unlimited":    { en: "admin · unlimited", tr: "yönetici · sınırsız" },

    /* ------------------------------------------------------------- dil seçimi */
    "lang.label":          { en: "Interface language", tr: "Arayüz dili" },
    "lang.hint": {
      en: "This changes the interface only. The language of the answers is chosen per search, under Advanced.",
      tr: "Bu yalnız arayüzü değiştirir. Cevapların dili her aramada, Gelişmiş bölümünden seçilir."
    },
    /* Dil adları çevrilmez: seçicide herkes kendi dilini kendi adıyla görmeli. */

    /* Doğrulamadan sonraki ilk girişte bir kez gösterilir. */
    "lang.notice.title":   { en: "Your interface language has changed",
                             tr: "Arayüz diliniz değişti" },
    "lang.notice.body": {
      en: "The answers themselves are written in a language you choose for each search. Open <b>Advanced</b> under the search box to set it.",
      tr: "Cevapların kendisi ise her arama için ayrıca seçtiğiniz dilde yazılır. Ayarlamak için arama kutusunun altındaki <b>Gelişmiş</b> bölümünü açın."
    },
    "lang.notice.ok":      { en: "Got it",       tr: "Anladım" },

    /* ------------------------------------------------------------ giriş/kayıt */
    "auth.tagline":        { en: "Evidence-based literature search and summarisation tool",
                             tr: "Kanıta dayalı literatür arama ve özetleme aracı" },
    "auth.signin":         { en: "Sign in",      tr: "Giriş yap" },
    "auth.signup":         { en: "Create account", tr: "Hesap oluştur" },
    "auth.email":          { en: "Email",        tr: "E-posta" },
    "auth.password":       { en: "Password",     tr: "Şifre" },
    "auth.pwHint":         { en: "Must be at least 8 characters.",
                             tr: "En az 8 karakter olmalı." },
    "auth.roleLabel":      { en: "How will you be using it?",
                             tr: "Premise'i nasıl kullanacaksınız?" },
    "auth.rolePhysician":  { en: "Physician / researcher", tr: "Hekim / araştırmacı" },
    "auth.roleStudent":    { en: "Medical / health student",
                             tr: "Tıp / sağlık öğrencisi" },
    "auth.roleHint": {
      en: "Premise is currently open to health professionals and students only.",
      tr: "Premise şimdilik yalnız sağlık profesyonellerine ve öğrencilere açık."
    },
    "auth.consent": {
      en: "I understand that my clinical questions are treated as <b>special-category personal data</b>, are sent to language model providers outside my country during search, and I <b>explicitly consent</b> to this. My search content stays private to my account; see the <a href=\"/gizlilik\" target=\"_blank\" rel=\"noopener\">privacy policy</a>. I accept the <a href=\"/kosullar\" target=\"_blank\" rel=\"noopener\">terms of use</a>.",
      tr: "Klinik sorularımın <b>özel nitelikli kişisel veri</b> sayıldığını, arama sırasında yurt dışındaki dil modeli sağlayıcılarına gönderildiğini anlıyor ve buna <b>açık rızamı</b> veriyorum. Arama içeriğim hesabıma özel kalır; <a href=\"/gizlilik\" target=\"_blank\" rel=\"noopener\">gizlilik politikasına</a> bakın. <a href=\"/kosullar\" target=\"_blank\" rel=\"noopener\">Kullanım koşullarını</a> kabul ediyorum."
    },
    "auth.forgot":         { en: "Forgot password?", tr: "Şifremi unuttum" },
    "auth.foot": {
      en: "Premise is a literature search and summarisation tool. <b>It is not clinical decision support</b>, does not diagnose, and does not recommend treatment. Every generated summary must be verified against the cited articles before it is used in a clinical decision.",
      tr: "Premise bir literatür arama ve özetleme aracıdır. <b>Klinik karar desteği değildir</b>, tanı koymaz ve tedavi önermez. Üretilen her özet, klinik bir kararda kullanılmadan önce atıf verdiği makalelere karşı doğrulanmalıdır."
    },
    "auth.howItWorks":     { en: "How does Premise work? →", tr: "Premise nasıl çalışır? →" },
    "auth.privacy":        { en: "Privacy policy →", tr: "Gizlilik politikası →" },
    "auth.terms":          { en: "Terms of use →",  tr: "Kullanım koşulları →" },
    "auth.err.consent":    { en: "You must give explicit consent to continue.",
                             tr: "Devam etmek için açık rıza vermeniz gerekiyor." },
    "auth.err.exists": {
      en: "Something went wrong. If you already have an account, please try signing in.",
      tr: "Bir şeyler ters gitti. Zaten bir hesabınız varsa giriş yapmayı deneyin."
    },
    "auth.err.details":    { en: "Please check your details.",
                             tr: "Lütfen bilgilerinizi kontrol edin." },

    /* -------------------------------------------------------------- hatalar */
    "errors.generic":      { en: "Something went wrong, please try again.",
                             tr: "Bir şeyler ters gitti, lütfen tekrar deneyin." },
    "errors.network":      { en: "Could not reach the server.",
                             tr: "Sunucuya ulaşılamadı." }
  };

  /* Sayfa açılırken dil: hesabınki henüz bilinmiyor (o /api/me ile gelir), bu yüzden
     önce tarayıcıda saklanan son seçim uygulanır. Böylece giriş yapmış kullanıcı
     sayfayı her açtığında bir an İngilizce metin görüp sonra Türkçeye atlamaz. */
  function stored() {
    try {
      var value = localStorage.getItem(STORAGE_KEY);
      return SUPPORTED.indexOf(value) >= 0 ? value : null;
    } catch (e) {
      return null;               // gizli sekme ya da site verisi kapalı
    }
  }

  var current = stored() || DEFAULT;

  function t(key, vars) {
    var entry = STRINGS[key];
    if (!entry) return key;                    // eksik anahtar görünür kalsın
    var text = entry[current] || entry[DEFAULT] || key;
    if (vars) {
      Object.keys(vars).forEach(function (name) {
        text = text.split("{" + name + "}").join(String(vars[name]));
      });
    }
    return text;
  }

  var ATTRS = ["placeholder", "title", "aria-label", "content", "value"];

  function apply(root) {
    var scope = root || document;

    scope.querySelectorAll("[data-i18n]").forEach(function (el) {
      el.textContent = t(el.getAttribute("data-i18n"));
    });
    /* Yalnız bu dosyadaki sabit metinler; dışarıdan gelen hiçbir şey buraya girmez. */
    scope.querySelectorAll("[data-i18n-html]").forEach(function (el) {
      el.innerHTML = t(el.getAttribute("data-i18n-html"));
    });
    ATTRS.forEach(function (attr) {
      scope.querySelectorAll("[data-i18n-" + attr + "]").forEach(function (el) {
        el.setAttribute(attr, t(el.getAttribute("data-i18n-" + attr)));
      });
    });

    var title = document.querySelector("title[data-i18n-title-key]");
    if (title) title.textContent = t(title.getAttribute("data-i18n-title-key"));
    document.documentElement.lang = current;
  }

  /* `persist` yalnız kullanıcının kendi seçiminde true olur. Hesaptan gelen dili
     yazarken de saklanır: bir sonraki açılışta doğru dille başlanır. */
  function setLanguage(code, persist) {
    var next = SUPPORTED.indexOf(code) >= 0 ? code : DEFAULT;
    var changed = next !== current;
    current = next;
    if (persist !== false) {
      try { localStorage.setItem(STORAGE_KEY, next); } catch (e) { /* yoksay */ }
    }
    apply();
    return changed;
  }

  window.I18N = {
    t: t,
    apply: apply,
    setLanguage: setLanguage,
    supported: SUPPORTED.slice(),
    get language() { return current; }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { apply(); });
  } else {
    apply();
  }
})();
