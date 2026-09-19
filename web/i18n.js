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

    /* ----------------------------------------------------------- güç modları
       Sunucu yalnız sayıları gönderir (bkz. accounts.public); metin burada, o
       sayılardan kurulur. Bir knob değişince açıklama da onunla değişir. */
    "mode.medium.label":   { en: "Medium power", tr: "Orta güç" },
    "mode.high.label":     { en: "High power",   tr: "Yüksek güç" },
    "mode.medium.use": {
      en: "The everyday setting for a clinical question — enough depth to quote a number, without paying for a full review.",
      tr: "Klinik bir soru için günlük ayar: bir sayıyı alıntılayacak kadar derin, tam bir derlemenin parasını ödemeden."
    },
    "mode.high.use": {
      en: "For work you will have to defend — a case presentation, a journal club, a review draft, or a decision someone will question.",
      tr: "Savunmanız gereken işler için: olgu sunumu, makale saati, derleme taslağı ya da birinin sorgulayacağı bir karar."
    },
    "mode.feat.screens":   { en: "Screens {triage} candidates, answers from {articles}",
                             tr: "{triage} aday tarar, {articles} makaleden cevaplar" },
    "mode.feat.fulltext":  { en: "Reads the full text of up to {n} articles",
                             tr: "En fazla {n} makalenin tam metnini okur" },
    "mode.feat.abstracts": { en: "Abstracts only — full text is not read",
                             tr: "Yalnız özetler — tam metin okunmaz" },
    "mode.feat.extract": {
      en: "Extracts effect sizes, confidence intervals and sample sizes, each checked against the source text",
      tr: "Etki büyüklüğü, güven aralığı ve örneklem sayısını çıkarır; her biri kaynak metne karşı sınanır"
    },
    "mode.feat.tables":    { en: "Adds an evidence table and a numeric findings table",
                             tr: "Kanıt tablosu ve sayısal bulgu tablosu ekler" },
    "mode.feat.guards": {
      en: "Drops off-topic articles, flags invented citations and checks every claim against the article it cites",
      tr: "Konu dışı makaleleri eler, uydurma atıfları işaretler ve her iddiayı atıf verdiği makaleye karşı sınar"
    },
    "mode.locked":         { en: "This power mode is available on paid plans.",
                             tr: "Bu güç modu ücretli planlarda kullanılabilir." },
    "mode.estimate":       { en: "≈ {n} credits", tr: "≈ {n} kredi" },
    "mode.hint": {
      en: "About {estimate} credits per search. At most {max} are held while it runs; you are charged only what the search actually costs.",
      tr: "Arama başına yaklaşık {estimate} kredi. Çalışırken en fazla {max} kredi bloke edilir; sizden yalnızca aramanın gerçek maliyeti düşülür."
    },
    "mode.fulltextOn":     { en: "Up to {n} full texts are read in this mode.",
                             tr: "Bu modda en fazla {n} tam metin okunur." },
    "mode.fulltextOff":    { en: "Full text is not read in this mode; answers come from abstracts only.",
                             tr: "Bu modda tam metin okunmaz; cevap yalnız özetlerden üretilir." },
    "mode.fulltextLocked": { en: "Full-text reading is available on paid plans.",
                             tr: "Tam metin okuma ücretli planlarda kullanılabilir." },

    /* ------------------------------------------------------------ üst çubuk */
    "top.account":         { en: "My account",  tr: "Hesabım" },
    "top.accountTitle":    { en: "My account and credits", tr: "Hesabım ve kredilerim" },
    "top.theme":           { en: "Light / dark theme", tr: "Açık / koyu tema" },
    "top.creditsTitle":    { en: "{plan} plan · {left} of {total} credits left",
                             tr: "{plan} plan · {total} kredinin {left} tanesi kaldı" },
    "top.freeLimited": {
      en: "No free monthly credits on this account (too many free accounts from this network or browser).",
      tr: "Bu hesapta aylık ücretsiz kredi yok (aynı ağdan ya da tarayıcıdan çok sayıda ücretsiz hesap açılmış)."
    },
    "top.unlimitedTitle":  { en: "Unlimited credits", tr: "Sınırsız kredi" },

    /* --------------------------------------------------------- doğrulama kapısı */
    "verify.title":        { en: "Verify your email to search",
                             tr: "Arama yapmak için e-postanızı doğrulayın" },
    "verify.body": {
      en: "We've sent a confirmation link to your inbox. Click it, then reload this page. Didn't get it? Check your spam folder, or request a new one below.",
      tr: "Gelen kutunuza bir doğrulama bağlantısı gönderdik. Bağlantıya tıklayıp bu sayfayı yenileyin. Ulaşmadıysa spam klasörünüze bakın ya da aşağıdan yenisini isteyin."
    },
    "verify.resend":       { en: "Resend verification email",
                             tr: "Doğrulama e-postasını tekrar gönder" },
    "verify.goAccount":    { en: "Go to my account", tr: "Hesabıma git" },
    "verify.already":      { en: "Your email is already verified.",
                             tr: "E-postanız zaten doğrulanmış." },
    "verify.sent":         { en: "Verification email sent, check your inbox.",
                             tr: "Doğrulama e-postası gönderildi, gelen kutunuza bakın." },
    "verify.failed":       { en: "Could not send the email.",
                             tr: "E-posta gönderilemedi." },

    /* ------------------------------------------------------------------ hero */
    "hero.title":          { en: "Ask a clinical question, we gather the evidence.",
                             tr: "Klinik sorunuzu sorun, kanıtı biz toplayalım." },
    "hero.sub": {
      en: "Your question is turned into academic search terms, then PubMed, PubMed Central and Europe PMC are searched at once. Free full texts are downloaded, ranked by level of evidence, and summarised with a citation behind every claim.",
      tr: "Sorunuz akademik arama terimlerine çevrilir, ardından PubMed, PubMed Central ve Europe PMC aynı anda taranır. Ücretsiz tam metinler indirilir, kanıt düzeyine göre sıralanır ve her iddianın arkasında bir atıf olacak şekilde özetlenir."
    },
    "hero.privacy": {
      en: "<b>Not clinical decision support.</b> Verify every summary against the cited articles before acting on it. Do not type patient-identifying details (name, MRN, exact birth date) into the search box; describe the case in de-identified form. Your questions stay private to your account; see the <a href=\"/gizlilik\" target=\"_blank\" rel=\"noopener\">privacy policy</a>.",
      tr: "<b>Klinik karar desteği değildir.</b> Her özeti, ona dayanarak hareket etmeden önce atıf verdiği makalelere karşı doğrulayın. Arama kutusuna hastayı tanımlayan bilgi (ad, dosya numarası, tam doğum tarihi) yazmayın; olguyu kimliksizleştirerek anlatın. Sorularınız hesabınıza özel kalır; <a href=\"/gizlilik\" target=\"_blank\" rel=\"noopener\">gizlilik politikasına</a> bakın."
    },

    /* ----------------------------------------------------------------- arama */
    "search.placeholder": {
      en: "e.g. Are DOACs safer than warfarin in elderly patients with atrial fibrillation?",
      tr: "ör. Atriyal fibrilasyonu olan yaşlı hastalarda DOAK'lar varfarinden daha güvenli mi?"
    },
    "search.button":       { en: "Search",     tr: "Ara" },
    "search.searching":    { en: "Searching…", tr: "Aranıyor…" },
    "search.expected":     { en: "≈ {n} credits expected · ", tr: "≈ {n} kredi bekleniyor · " },
    "search.held":         { en: "{n} held until the final cost is known",
                             tr: "kesin maliyet belli olana kadar {n} kredi bloke" },

    "filter.meta":         { en: "Meta-analysis / systematic review",
                             tr: "Meta-analiz / sistematik derleme" },
    "filter.rct":          { en: "Randomised controlled", tr: "Randomize kontrollü" },
    "filter.guideline":    { en: "Guidelines",  tr: "Kılavuzlar" },
    "filter.freeFulltext": { en: "Free full text", tr: "Ücretsiz tam metin" },
    "filter.humans":       { en: "Human studies", tr: "İnsan çalışmaları" },
    "filter.advancedOpen": { en: "Advanced ⌄",  tr: "Gelişmiş ⌄" },
    "filter.advancedShut": { en: "Advanced ⌃",  tr: "Gelişmiş ⌃" },

    "adv.author":          { en: "Author",      tr: "Yazar" },
    "adv.authorHint":      { en: "e.g. Smith J", tr: "ör. Smith J" },
    "adv.journal":         { en: "Journal",     tr: "Dergi" },
    "adv.journalHint":     { en: "e.g. Lancet", tr: "ör. Lancet" },
    "adv.published":       { en: "Published within", tr: "Yayın tarihi" },
    "adv.years3":          { en: "Last 3 years",  tr: "Son 3 yıl" },
    "adv.years5":          { en: "Last 5 years",  tr: "Son 5 yıl" },
    "adv.years10":         { en: "Last 10 years", tr: "Son 10 yıl" },
    "adv.years20":         { en: "Last 20 years", tr: "Son 20 yıl" },
    "adv.yearsAny":        { en: "No limit",      tr: "Sınır yok" },
    "adv.answerLanguage":  { en: "Answer language", tr: "Cevap dili" },
    "adv.articles":        { en: "Articles to read", tr: "Okunacak makale" },
    "adv.readFulltext":    { en: "Read PMC full text", tr: "PMC tam metnini oku" },
    "adv.openAccess":      { en: "Open access only", tr: "Yalnız açık erişim" },
    "adv.clinical":        { en: "Include clinical resources", tr: "Klinik kaynakları dahil et" },
    "adv.extractStats":    { en: "Extract numeric findings", tr: "Sayısal bulguları çıkar" },

    "examples.try":        { en: "Try:", tr: "Deneyin:" },
    "examples.1": {
      en: "What is the levothyroxine target in hypothyroidism during pregnancy?",
      tr: "Gebelikte hipotiroidide levotiroksin hedefi nedir?"
    },
    "examples.2": {
      en: "Do SGLT2 inhibitors improve kidney outcomes in type 2 diabetes?",
      tr: "SGLT2 inhibitörleri tip 2 diyabette böbrek sonuçlarını iyileştirir mi?"
    },
    "examples.3": {
      en: "When is imaging indicated in paediatric febrile seizures?",
      tr: "Çocukluk çağı febril konvülziyonlarında görüntüleme ne zaman gerekir?"
    },

    /* --------------------------------------------------------------- ilerleme */
    "progress.starting":   { en: "Starting…", tr: "Başlıyor…" },
    "progress.cancelling": { en: "Cancelling…", tr: "İptal ediliyor…" },

    /* --------------------------------------------------------------- sonuçlar */
    "tab.synthesis":       { en: "Synthesis", tr: "Sentez" },
    "tab.articles":        { en: "Articles",  tr: "Makaleler" },
    "tab.clinical":        { en: "Clinical resources", tr: "Klinik kaynaklar" },
    "tab.strategy":        { en: "Search strategy", tr: "Arama stratejisi" },

    "sort.relevance":      { en: "Relevance", tr: "İlgi" },
    "sort.year":           { en: "Year",      tr: "Yıl" },
    "sort.citations":      { en: "Citations", tr: "Atıf" },
    "sort.freeOnly":       { en: "Free to read only", tr: "Yalnız ücretsiz okunanlar" },

    "article.showAbstract": { en: "Show abstract", tr: "Özeti göster" },
    "article.hideAbstract": { en: "Hide abstract", tr: "Özeti gizle" },
    "article.save":        { en: "Save to library", tr: "Kütüphaneye kaydet" },
    "article.saved":       { en: "Saved to your library.", tr: "Kütüphanenize kaydedildi." },
    "article.already":     { en: "Already in your library.", tr: "Zaten kütüphanenizde." },
    "article.noMatch":     { en: "No article matches this filter.",
                             tr: "Bu filtreye uyan makale yok." },
    "article.noAuthors":   { en: "Authors not listed in the record",
                             tr: "Kayıtta yazar bilgisi yok" },

    "report.empty":        { en: "No synthesis was generated.", tr: "Sentez üretilmedi." },
    "report.unverified": {
      en: "Warning: {n} citation(s) were not found in the result set and have been flagged in the text.",
      tr: "Uyarı: {n} atıf sonuç kümesinde bulunamadı ve metinde işaretlendi."
    },
    "report.uncited": {
      en: "Warning: the short answer carries no citation that could be traced to an article below, so the claim check could not verify it. Read the studies before relying on it.",
      tr: "Uyarı: kısa cevap, aşağıdaki makalelere kadar izlenebilen hiçbir atıf taşımıyor; bu yüzden iddia kontrolü onu doğrulayamadı. Ona güvenmeden önce çalışmaları okuyun."
    },

    "drawer.title":        { en: "Source passages", tr: "Kaynak pasajları" },
    "drawer.sub":          { en: "Where this claim comes from in the article text",
                             tr: "Bu iddianın makale metnindeki karşılığı" },
    "drawer.show":         { en: "Show source", tr: "Kaynağı göster" },
    "drawer.showTitle":    { en: "Show the source passage behind this sentence",
                             tr: "Bu cümlenin arkasındaki kaynak pasajı göster" },

    "panel.recent":        { en: "Recent searches", tr: "Son aramalar" },
    "panel.library":       { en: "My library", tr: "Kütüphanem" },
    "panel.noSearches":    { en: "No searches yet.", tr: "Henüz arama yok." },
    "panel.emptyLibrary":  { en: "Empty. Save articles from the results list.",
                             tr: "Boş. Sonuç listesinden makale kaydedebilirsiniz." },
    "clinical.none":       { en: "No clinical resource found.",
                             tr: "Klinik kaynak bulunamadı." },

    /* ---------------------------------------------------------------- altbilgi */
    "footer.sources": {
      en: "Sources: PubMed, PubMed Central, Europe PMC, OpenAlex, Unpaywall, StatPearls, Cochrane, TR Dizin. UpToDate requires a subscription; its content is never copied here. You only get a search link to open with your own institutional access.",
      tr: "Kaynaklar: PubMed, PubMed Central, Europe PMC, OpenAlex, Unpaywall, StatPearls, Cochrane, TR Dizin. UpToDate abonelik gerektirir; içeriği buraya hiçbir zaman kopyalanmaz. Yalnızca kendi kurumsal erişiminizle açacağınız bir arama bağlantısı verilir."
    },
    "footer.warning": {
      en: "This tool is not clinical decision support. Verify every summary against the cited articles before acting on it.",
      tr: "Bu araç klinik karar desteği değildir. Her özeti, ona dayanarak hareket etmeden önce atıf verdiği makalelere karşı doğrulayın."
    },

    /* -------------------------------------------------------------- hatalar */
    "errors.generic":      { en: "Something went wrong, please try again.",
                             tr: "Bir şeyler ters gitti, lütfen tekrar deneyin." },
    "errors.network":      { en: "Could not reach the server.",
                             tr: "Sunucuya ulaşılamadı." },
    "errors.server":       { en: "Server error", tr: "Sunucu hatası" },
    "errors.lost":         { en: "Lost connection to the server.",
                             tr: "Sunucuyla bağlantı koptu." },

    /* --------------------------------------------------------- dışa aktarma */
    "export.downloaded":   { en: "{name} downloaded.", tr: "{name} indirildi." },
    "export.failed":       { en: "Export failed", tr: "Dışa aktarma başarısız" },

    /* ---------------------------------------------------- makale rozetleri */
    "badge.fulltext":      { en: "Full text read", tr: "Tam metin okundu" },
    "badge.fulltextTitle": { en: "The synthesis draws on this article's full text",
                             tr: "Sentez bu makalenin tam metnine dayanıyor" },
    "badge.abstract":      { en: "Abstract only", tr: "Yalnız özet" },
    "badge.abstractTitle": {
      en: "Only the abstract was read from this article; abstracts can overstate the result",
      tr: "Bu makaleden yalnız özet okundu; özetler sonucu olduğundan güçlü gösterebilir"
    },
    "badge.offFilter":     { en: "Off filter", tr: "Filtre dışı" },
    "badge.offFilterTitle": {
      en: "This record does not match your chosen publication-type filter",
      tr: "Bu kayıt seçtiğiniz yayın türü filtresine uymuyor"
    },
    "badge.typeUnclear":   { en: "Type unclear", tr: "Tür belirsiz" },
    "badge.typeUnclearTitle": {
      en: "This record has no publication-type data, so filter compliance could not be checked",
      tr: "Bu kayıtta yayın türü bilgisi yok, filtreye uygunluğu denetlenemedi"
    },
    "badge.indirect":      { en: "Indirect", tr: "Dolaylı" },
    "badge.indirectTitle": {
      en: "Related to the topic, but does not directly investigate your question",
      tr: "Konuyla ilgili, ama sorunuzu doğrudan araştırmıyor"
    },
    "badge.cached":        { en: "reused from {age}", tr: "{age} önceki aramadan" },
    "badge.cachedTitle": {
      en: "This answer was reused from an identical search, so no new sources were fetched",
      tr: "Bu cevap birebir aynı bir aramadan yeniden kullanıldı, yeni kaynak çekilmedi"
    },
    "badge.rerun":         { en: "run it fresh", tr: "baştan çalıştır" },

    /* -------------------------------------------------------- kaynak paneli */
    "drawer.noMatch": {
      en: "No stored passage matched this sentence closely.",
      tr: "Bu cümleyle yeterince örtüşen saklanmış bir pasaj bulunamadı."
    },
    "drawer.abstractOnly": {
      en: "Only the abstract is stored for this article, so the claim may rest on text we did not keep.",
      tr: "Bu makale için yalnız özet saklanıyor; iddia, saklamadığımız bir metne dayanıyor olabilir."
    },
    "drawer.fullText":     { en: "Full text", tr: "Tam metin" },
    "drawer.openArticle":  { en: "Open the article to check it yourself.",
                             tr: "Kendiniz denetlemek için makaleyi açın." },
    "drawer.searchedBoth": { en: "abstract and full text searched",
                             tr: "özet ve tam metin tarandı" },
    "drawer.searchedAbstract": { en: "abstract searched", tr: "özet tarandı" },

    /* ------------------------------------------------------- kanıt uyarıları */
    "badge.freeFulltext":  { en: "Free full text", tr: "Ücretsiz tam metin" },
    "bib.head":            { en: "References and numeric findings",
                             tr: "Kaynaklar ve sayısal bulgular" },
    "agreement.head":      { en: "Where studies agree on direction",
                             tr: "Çalışmaların yön olarak birleştiği noktalar" },
    "evidence.limited": {
      en: "<b>Limited answer.</b> Only {n} article(s) directly investigate your question, not enough to build a recommendation on. The report therefore skips clinical inference and reports the studies one by one instead.",
      tr: "<b>Sınırlı cevap.</b> Sorunuzu doğrudan araştıran yalnızca {n} makale var; bu, bir öneri kurmaya yetmez. Bu yüzden rapor klinik çıkarım yapmaz, çalışmaları tek tek aktarır."
    },
    "evidence.low": {
      en: "<b>This answer rests on only {n} article(s).</b> That is too few to answer the question with confidence; do not base a clinical decision on it alone.",
      tr: "<b>Bu cevap yalnızca {n} makaleye dayanıyor.</b> Soruyu güvenle yanıtlamak için bu sayı çok az; klinik bir kararı tek başına buna dayandırmayın."
    },
    "evidence.broadened": {
      en: "The initial search did not return enough results, so it was rerun with the {names} limit removed, so the list may include articles outside your original criteria.",
      tr: "İlk tarama yeterli sonuç vermedi, bu yüzden {names} sınırı kaldırılarak tekrarlandı; liste özgün ölçütlerinizin dışındaki makaleleri içerebilir."
    },
    "evidence.relaxed.date": { en: "date range", tr: "tarih aralığı" },
    "evidence.relaxed.type": { en: "publication-type filters", tr: "yayın türü filtresi" },
    "noAnswer.tips":       { en: "Things you could try:", tr: "Deneyebilecekleriniz:" },
    "noAnswer.foot": {
      en: "The search results are still on the <b>Articles</b> tab, you can review them yourself.",
      tr: "Tarama sonuçları <b>Makaleler</b> sekmesinde duruyor, kendiniz inceleyebilirsiniz."
    },

    /* ------------------------------------------------------- klinik kaynaklar */
    "clinical.statpearls":     { en: "StatPearls point-of-care chapters",
                                 tr: "StatPearls klinik başvuru bölümleri" },
    "clinical.statpearlsSub": {
      en: "Continuously updated clinical summaries, free to read in full on NCBI Bookshelf.",
      tr: "Sürekli güncellenen klinik özetler; NCBI Bookshelf üzerinde tam metni ücretsiz."
    },
    "clinical.guidelines":     { en: "Guidelines and Cochrane reviews",
                                 tr: "Kılavuzlar ve Cochrane derlemeleri" },
    "clinical.guidelinesSub": {
      en: "Publications indexed in PubMed as practice guidelines, consensus statements or systematic reviews.",
      tr: "PubMed'de uygulama kılavuzu, uzlaşı bildirisi ya da sistematik derleme olarak dizinlenmiş yayınlar."
    },
    "clinical.freeFull":       { en: " · free full text", tr: " · ücretsiz tam metin" },
    "clinical.elsewhere":      { en: "Open the same question elsewhere",
                                 tr: "Aynı soruyu başka kaynaklarda açın" },
    "clinical.elsewhereSub": {
      en: "Clinical decision resources, pre-filled with your question. Subscription resources have a dashed border.",
      tr: "Sorunuzla önceden doldurulmuş klinik karar kaynakları. Abonelik gerektirenlerin kenarlığı kesiklidir."
    },

    /* --------------------------------------------------------- arama stratejisi */
    "strategy.question":   { en: "Your question",     tr: "Sorunuz" },
    "strategy.pubmed":     { en: "PubMed query",      tr: "PubMed sorgusu" },
    "strategy.academic":   { en: "Academic phrasing", tr: "Akademik ifade" },
    "strategy.mesh":       { en: "MeSH terms",        tr: "MeSH terimleri" },
    "strategy.screening":  { en: "Screening summary", tr: "Tarama özeti" },
    "strategy.screeningText": {
      en: "{pool} unique records screened, {articles} articles selected, {fulltext} full texts read.",
      tr: "{pool} tekil kayıt tarandı, {articles} makale seçildi, {fulltext} tam metin okundu."
    }
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
