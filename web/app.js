/* Premise front end — no dependencies, single file. */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  result: null,
  filters: new Set(),
  sort: "score",
  onlyOA: false,
  jobId: null,
  eventSource: null,
  claims: [],          // sentences in the report that carry a citation
};

/* ------------------------------------------------------------------ theme */
const savedTheme = localStorage.getItem("litrag-theme");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
$("#theme-toggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("litrag-theme", next);
});

/* ----------------------------------------------------------------- helpers */
function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let toastTimer;
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 2600);
}

/* ------------------------------------------------------------ form wiring */
$("#toggle-advanced").addEventListener("click", (e) => {
  const panel = $("#advanced");
  panel.hidden = !panel.hidden;
  e.target.textContent = panel.hidden ? "Advanced ⌄" : "Advanced ⌃";
});

$$("#quick-filters .chip[data-filter]").forEach((chip) => {
  chip.addEventListener("click", () => {
    const key = chip.dataset.filter;
    if (state.filters.has(key)) state.filters.delete(key);
    else state.filters.add(key);
    chip.classList.toggle("active");
  });
});

$("#max_articles").addEventListener("input", (e) => {
  $("#count-label").textContent = e.target.value;
});

$$("#examples button").forEach((btn) =>
  btn.addEventListener("click", () => {
    $("#query").value = btn.textContent.trim();
    $("#query").focus();
  }));

/* ------------------------------------------------------------------ search */
$("#search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  runSearch();
});

async function runSearch(options = {}) {
  const query = $("#query").value.trim();
  if (!query) return;

  const payload = {
    query,
    author: $("#author").value.trim(),
    journal: $("#journal").value.trim(),
    language: $("#language").value,
    max_articles: Number($("#max_articles").value),
    recent_years: Number($("#recent_years").value),
    filters: Array.from(state.filters),
    only_open_access: $("#only_open_access").checked,
    use_fulltext: $("#use_fulltext").checked,
    use_clinical: $("#use_clinical").checked,
    extract_stats: $("#extract_stats").checked,
    refresh: Boolean(options.refresh),
  };

  startProgress();
  try {
    const res = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.status === 401) { location.replace("/giris"); return; }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      const detail = typeof data.detail === "string" ? data.detail
        : Array.isArray(data.detail) ? (data.detail[0]?.msg || "Sunucu hatası")
        : "Sunucu hatası";
      throw new Error(detail);
    }
    const { job_id } = await res.json();
    state.jobId = job_id;
    listen(job_id);
  } catch (err) {
    endProgress();
    showError(err.message);
  }
}

function startProgress() {
  $("#error").hidden = true;
  $("#results").hidden = true;
  $("#progress").hidden = false;
  $("#progress-steps").innerHTML = "";
  $("#progress-bar").style.width = "2%";
  $("#progress-pct").textContent = "0%";
  $("#progress-msg").textContent = "Starting…";
  $("#cancel-btn").disabled = false;
  $("#cancel-btn").textContent = "Cancel";
  $("#search-btn").disabled = true;
  $("#search-btn").querySelector(".btn-label").textContent = "Searching…";
  $("#progress").scrollIntoView({ behavior: "smooth", block: "center" });
}

function endProgress() {
  $("#progress").hidden = true;
  $("#search-btn").disabled = false;
  $("#search-btn").querySelector(".btn-label").textContent = "Search";
  state.jobId = null;
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
}

/* Cancelling: the server stops at its next checkpoint, the UI returns at once. */
$("#cancel-btn").addEventListener("click", async () => {
  if (!state.jobId) return;
  const jobId = state.jobId;
  $("#cancel-btn").disabled = true;
  $("#cancel-btn").textContent = "Cancelling…";
  try {
    await fetch(`/api/cancel/${jobId}`, { method: "POST" });
  } catch {
    /* the search is abandoned locally even if the request fails */
  }
  endProgress();
  toast("Search cancelled.");
});

function listen(jobId) {
  const source = new EventSource(`/api/stream/${jobId}`);
  state.eventSource = source;

  source.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === "progress") {
      $("#progress-msg").textContent = data.message;
      $("#progress-pct").textContent = Math.round(data.pct * 100) + "%";
      $("#progress-bar").style.width = Math.max(2, data.pct * 100) + "%";
      const li = document.createElement("li");
      li.textContent = data.message;
      $("#progress-steps").appendChild(li);
    } else if (data.type === "done") {
      endProgress();
      render(data.result);
      loadHistory();
      loadAccount();
    } else if (data.type === "cancelled") {
      endProgress();
    } else if (data.type === "error") {
      endProgress();
      showError(data.message);
    }
  };

  source.onerror = () => {
    if (!state.jobId) return;          // we closed it ourselves after a cancel
    endProgress();
    showError("Lost connection to the server.");
  };
}

function showError(message) {
  const box = $("#error");
  box.textContent = message;
  box.hidden = false;
}

/* ------------------------------------------------------- markdown to HTML */
function inlineMarkup(text) {
  return esc(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\*)\*(?!\s)([^*]+?)\*(?!\*)/g, "<em>$1</em>")
    .replace(/\[PMID:\s*(\d+)([^\]]*)\]/g, (m, pmid, rest) => {
      const bad = /unverified/i.test(rest);
      return `<a class="pmid${bad ? " pmid-bad" : ""}" target="_blank" rel="noopener"
               href="https://pubmed.ncbi.nlm.nih.gov/${pmid}/">PMID ${pmid}${bad ? " ⚠" : ""}</a>`;
    });
}

/* Splits a line into sentences so each claim can be traced back to its source. */
function splitSentences(line) {
  return line.split(/(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜÄÉ"'(\[])/).filter(Boolean);
}

function renderLine(line) {
  return splitSentences(line).map((sentence) => {
    const pmids = Array.from(sentence.matchAll(/\[PMID:\s*(\d+)/g), (m) => m[1]);
    const html = inlineMarkup(sentence);
    if (!pmids.length) return html;

    const claimId = state.claims.length;
    state.claims.push({ text: sentence.replace(/\[PMID:[^\]]*\]/g, "").trim(), pmids });
    return `<span class="claim">${html}<button class="src-btn" data-claim="${claimId}"
              title="Show the source passage behind this sentence" aria-label="Show source">
              <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor"
                   stroke-width="2.2"><path d="M4 6h16M4 12h10M4 18h7"/></svg></button></span>`;
  }).join(" ");
}

function markdown(text) {
  const out = [];
  let inList = false;

  for (const raw of String(text || "").split("\n")) {
    const line = raw.trim();
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    const bullet = line.match(/^[-*•]\s+(.*)$/) || line.match(/^\d+\.\s+(.*)$/);

    if (!line) { if (inList) { out.push("</ul>"); inList = false; } continue; }
    if (heading) {
      if (inList) { out.push("</ul>"); inList = false; }
      const level = Math.min(Math.max(heading[1].length, 2), 4);
      out.push(`<h${level}>${inlineMarkup(heading[2])}</h${level}>`);
    } else if (bullet) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${renderLine(bullet[1])}</li>`);
    } else {
      if (inList) { out.push("</ul>"); inList = false; }
      out.push(`<p>${renderLine(line)}</p>`);
    }
  }
  if (inList) out.push("</ul>");
  return out.join("\n");
}

/* ------------------------------------------------- source passage matching */
const STOPWORDS = new Set([
  "that", "this", "with", "from", "have", "been", "were", "which", "their", "there",
  "these", "those", "than", "then", "when", "what", "while", "also", "more", "most",
  "such", "into", "over", "after", "before", "between", "among", "using", "based",
  "study", "studies", "patients", "results", "showed", "however", "although",
  "icin", "ile", "olan", "olarak", "daha", "ancak", "veya", "gibi", "sonra", "once",
  "calisma", "calismada", "hastalarda", "hasta", "bulunmustur", "gosterilmistir",
  "arasinda", "uzerinde", "bununla", "birlikte", "ayrica", "tarafindan",
]);

function fold(text) {
  return String(text || "").toLowerCase()
    .replace(/[ıİ]/g, "i").replace(/[şŞ]/g, "s").replace(/[ğĞ]/g, "g")
    .replace(/[çÇ]/g, "c").replace(/[öÖ]/g, "o").replace(/[üÜ]/g, "u")
    // Journals typeset decimals and dashes differently: 0·63 (Lancet), 0.58–0.69
    .replace(/·/g, ".")
    .replace(/[−–—]/g, "-")
    .normalize("NFD").replace(/[̀-ͯ]/g, "");
}

function wordsOf(text) {
  return fold(text).split(/[^a-z0-9]+/).filter((t) => t.length > 3 && !STOPWORDS.has(t));
}

function numbersOf(text) {
  // Thousands separators differ by language: 21.947 (tr), 21,947 and 21 947 (en) are one number
  const folded = fold(text).replace(/(\d)[\s ](\d{3})/g, "$1$2");
  const found = new Set();
  for (const raw of folded.match(/-?\d+(?:[.,]\d+)*/g) || []) {
    const normalised = raw.replace(/,/g, ".");
    if (normalised.replace(/[-.]/g, "").length < 2) continue;
    found.add(normalised);
    // A dash inside a confidence interval reads as a minus sign, so keep both forms
    found.add(normalised.replace(/^-/, ""));
    if (/^-?\d{1,3}([.,]\d{3})+$/.test(raw)) found.add(raw.replace(/[.,-]/g, ""));
  }
  return Array.from(found);
}

/* Builds the searchable passages of one article: abstract plus full-text excerpt. */
function passagesFor(pmid) {
  const article = (state.result?.articles || []).find((a) => a.pmid === pmid);
  const excerpt = (state.result?.sources || {})[pmid] || "";
  const blocks = [];

  const push = (text, section) => {
    const clean = text.replace(/\s+/g, " ").trim();
    if (clean.length < 45) return;
    blocks.push({ text: clean, section, words: wordsOf(clean), numbers: numbersOf(clean) });
  };

  if (article?.abstract) {
    splitSentences(article.abstract).forEach((s) => push(s, "Abstract"));
  }
  let section = "Full text";
  for (const line of excerpt.split("\n")) {
    const heading = line.match(/^##\s+(.*)$/);
    if (heading) { section = heading[1].trim() || "Full text"; continue; }
    splitSentences(line).forEach((s) => push(s, section));
  }
  return blocks;
}

function scorePassage(claimWords, claimNumbers, passage) {
  const passageWords = new Set(passage.words);
  let hits = 0;
  for (const word of claimWords) {
    if (passageWords.has(word)) { hits += 1; continue; }
    // Latin-root cognates across languages: mortality / mortalite, hospital / hospitalizasyon
    const stem = word.slice(0, 5);
    if (word.length > 4 && passage.words.some((w) => w.length > 4 && w.startsWith(stem))) {
      hits += 0.6;
    }
  }
  const numberHits = claimNumbers.filter((n) => passage.numbers.includes(n)).length;

  // Dice coefficient rather than plain containment: a long passage should not win
  // just by being long enough to contain many of the claim's words by chance.
  const wordScore = (claimWords.length + passage.words.length)
    ? (2 * hits) / (claimWords.length + passage.words.length) : 0;

  // A shared effect size or sample size is near-unique, so it outweighs vocabulary.
  const numberScore = claimNumbers.length ? numberHits / claimNumbers.length : 0;
  return { score: wordScore + 4 * numberScore, numberHits };
}

function bestPassages(pmid, claim, limit = 3) {
  const claimWords = wordsOf(claim);
  const claimNumbers = numbersOf(claim);
  return passagesFor(pmid)
    .map((p) => ({ ...p, ...scorePassage(claimWords, claimNumbers, p) }))
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .filter((p) => p.score > 0.05);
}

function highlight(passage, claim) {
  const claimWords = new Set(wordsOf(claim));
  const claimNumbers = new Set(numbersOf(claim));
  return esc(passage.text).replace(/[\wğüşöçıİĞÜŞÖÇ.\-]+/g, (token) => {
    const folded = fold(token).replace(/[^a-z0-9.\-]/g, "");
    const isNumber = /^-?\d/.test(folded) && claimNumbers.has(folded.replace(/[.\-]+$/, ""));
    const isWord = claimWords.has(folded.replace(/[.\-]/g, ""));
    return isNumber || isWord ? `<mark${isNumber ? ' class="num"' : ""}>${token}</mark>` : token;
  });
}

function matchLabel(score) {
  if (score >= 2) return { text: "strong match", cls: "ok" };
  if (score >= 0.7) return { text: "likely match", cls: "mid" };
  return { text: "weak match", cls: "low" };
}

/* --------------------------------------------------------- source drawer */
function openDrawer(claimId) {
  const claim = state.claims[claimId];
  if (!claim) return;

  const sections = claim.pmids.map((pmid) => {
    const article = (state.result?.articles || []).find((a) => a.pmid === pmid);
    const passages = bestPassages(pmid, claim.text);
    const hasFullText = Boolean((state.result?.sources || {})[pmid]);

    const links = [
      `<a href="https://pubmed.ncbi.nlm.nih.gov/${pmid}/" target="_blank" rel="noopener">PubMed</a>`,
      article?.pmc_url ? `<a href="${article.pmc_url}" target="_blank" rel="noopener">PMC</a>` : "",
      article?.pdf_url ? `<a href="${esc(article.pdf_url)}" target="_blank" rel="noopener">PDF</a>` : "",
    ].filter(Boolean).join("");

    const body = passages.length
      ? passages.map((p) => {
          const label = matchLabel(p.score);
          return `<blockquote class="passage">
                    <div class="passage-head">
                      <span class="passage-section">${esc(p.section)}</span>
                      <span class="match match-${label.cls}">${label.text}</span>
                    </div>
                    <p>${highlight(p, claim.text)}</p>
                  </blockquote>`;
        }).join("")
      : `<p class="drawer-empty">No stored passage matched this sentence closely.
           ${hasFullText ? "" : "Only the abstract is stored for this article, so the claim may rest on text we did not keep."}
           Open the article to check it yourself.</p>`;

    return `<section class="drawer-article">
              <h4>${esc(article?.title || "PMID " + pmid)}</h4>
              <p class="drawer-meta">${esc(article?.journal || "")} ${article?.year || ""} ·
                 ${hasFullText ? "abstract and full text searched" : "abstract searched"}</p>
              <div class="drawer-links">${links}</div>
              ${body}
            </section>`;
  }).join("");

  $("#drawer-body").innerHTML =
    `<blockquote class="claim-quote">${esc(claim.text)}</blockquote>${sections}`;
  $("#drawer").hidden = false;
  $("#drawer-backdrop").hidden = false;
  requestAnimationFrame(() => $("#drawer").classList.add("open"));
}

function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawer-backdrop").hidden = true;
  setTimeout(() => ($("#drawer").hidden = true), 200);
}

$("#drawer-close").addEventListener("click", closeDrawer);
$("#drawer-backdrop").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#drawer").hidden) closeDrawer();
});

/* ------------------------------------------------------ findings and cards */
function statBadges(a) {
  const label = (a.evidence_label || "").toLowerCase();
  const design = (a.design || "").toLowerCase();
  const showDesign = design && !design.includes(label) && !label.includes(design);
  return [
    `<span class="badge badge-ev">${esc(a.evidence_label)}</span>`,
    showDesign ? `<span class="badge">${esc(a.design)}</span>` : "",
    a.year ? `<span class="badge">${a.year}</span>` : "",
    a.sample_size ? `<span class="badge badge-n">n = ${Number(a.sample_size).toLocaleString("en-US")}</span>` : "",
    a.citations ? `<span class="badge">${a.citations} citations</span>` : "",
    a.is_oa || a.pmcid ? '<span class="badge badge-oa">Free full text</span>' : "",
    a.fulltext_used
      ? '<span class="badge badge-ft" title="Sentez bu makalenin tam metnine dayanıyor">Full text read</span>'
      : '<span class="badge badge-abs" title="Bu makaleden yalnızca özet okundu; özetler sonucu olduğundan güçlü gösterebilir">Abstract only</span>',
    a.filter_status === "mismatch"
      ? '<span class="badge badge-off" title="Bu kayıt seçtiğiniz yayın türü filtresine uymuyor">Filtre dışı</span>' : "",
    a.filter_status === "unknown"
      ? '<span class="badge badge-unk" title="Kaydın yayın türü bilgisi boş, filtreye uyup uymadığı doğrulanamadı">Türü belirsiz</span>' : "",
    a.relevance === 1
      ? '<span class="badge badge-rel" title="Konuyla ilgili, ama sorunuzu doğrudan araştırmıyor">Dolaylı</span>' : "",
  ].filter(Boolean).join("");
}

function findingsTable(a) {
  if (!a.findings || !a.findings.length) return "";
  const rows = a.findings.map((f) => `
    <tr>
      <td class="f-outcome">${esc(f.outcome)}</td>
      <td class="f-value">${esc([f.measure, f.value].filter(Boolean).join(" "))}</td>
      <td class="f-ci">${f.ci ? "95% CI " + esc(f.ci) : ""}</td>
      <td class="f-p">${f.p ? "p " + esc(f.p) : ""}</td>
    </tr>`).join("");
  return `<table class="findings"><tbody>${rows}</tbody></table>`;
}

function articleLinks(a) {
  return [
    a.pmid ? `<a href="${a.pubmed_url}" target="_blank" rel="noopener">PMID ${a.pmid}</a>` : "",
    a.pmc_url ? `<a href="${a.pmc_url}" target="_blank" rel="noopener">PMC</a>` : "",
    a.pdf_url ? `<a href="${esc(a.pdf_url)}" target="_blank" rel="noopener">PDF</a>` : "",
    a.doi_url ? `<a href="${esc(a.doi_url)}" target="_blank" rel="noopener">DOI</a>` : "",
  ].filter(Boolean).join("");
}

function renderBibliography() {
  const items = state.result?.articles || [];
  const box = $("#bibliography");
  if (!items.length) { box.innerHTML = ""; return; }

  const withStats = items.filter((a) => a.findings && a.findings.length).length;
  const dropped = state.result?.dropped_findings || 0;
  box.innerHTML = `
    <h3 class="bib-head">References and numeric findings</h3>
    <p class="bib-note">
      Effect sizes, confidence intervals and p values were extracted from ${withStats} of these
      articles. Every number is copied verbatim from the source text and verified against it,
      never calculated. Click a PMID to open the article.
      ${dropped ? `<b>${dropped} sayı kaynak metinde birebir bulunamadığı için elendi
        ve rapora hiç girmedi.</b>` : ""}
    </p>
    <ol class="bib-list">
      ${items.map((a) => `
        <li class="bib-item">
          <a class="bib-title" href="${a.best_free_url || a.pubmed_url || "#"}"
             target="_blank" rel="noopener">${esc(a.title)}</a>
          <p class="bib-meta">${esc(a.authors || "Authors not listed in the record")}${a.journal ? " · " + esc(a.journal) : ""}</p>
          ${a.population ? `<p class="bib-pop">Population: ${esc(a.population)}</p>` : ""}
          <div class="badges">${statBadges(a)}</div>
          ${findingsTable(a)}
          <div class="bib-links">${articleLinks(a)}</div>
        </li>`).join("")}
    </ol>`;
}

/* Cached answers are labelled so nobody mistakes an old search for a fresh one. */
function cacheAge(result) {
  const hours = Number(result.cache_age_hours || 0);
  if (hours < 1) return "a search minutes ago";
  if (hours < 24) return `a search ${Math.round(hours)} h ago`;
  const days = Math.round(hours / 24);
  return `a search ${days} day${days === 1 ? "" : "s"} ago`;
}

/* Az sayıda makale bulunduğunda sonucun ne kadarına güvenilebileceğini açıkça söyler. */
function renderEvidenceNotice(result) {
  const box = $("#evidence-notice");
  const n = result.articles.length;
  const off = result.off_filter_count || 0;
  if (!result.low_evidence && !result.broadened && result.answer_mode === "full" && !off) {
    box.hidden = true;
    return;
  }

  const lines = [];
  if (result.answer_mode === "limited") {
    lines.push(`<b>Sınırlı cevap.</b> Sorunuzu doğrudan araştıran
      ${result.relevant_count} makale bulundu; bu, bir öneriye temel olacak kadar değil.
      Bu yüzden rapor klinik çıkarım bölümü içermiyor, çalışmaları tek tek aktarıyor.`);
  }
  if (result.low_evidence) {
    lines.push(`<b>Bu yanıt yalnızca ${n} makaleye dayanıyor.</b> Soruyu kesin olarak
      yanıtlamak için bu sayı düşük; sonucu tek başına klinik karara temel almayın.`);
  }
  if (result.broadened) {
    const what = { "date range": "tarih aralığı",
                   "publication type filters": "yayın türü filtreleri" };
    const names = (result.relaxed || []).map((r) => what[r] || r).join(" ve ");
    lines.push(`İlk taramada yeterli sonuç çıkmadığı için ${names} sınırı kaldırılarak
      tekrar arandı, bu yüzden listede seçtiğiniz ölçütlerin dışında kalan makaleler olabilir.`);
  }
  if (off) {
    lines.push(`Listedeki ${off} makale seçtiğiniz yayın türü filtresine uymuyor ve
      <b>Filtre dışı</b> etiketiyle işaretlendi. Sıralamada geri çekildiler, ama silinmediler:
      yayın türü bilgisi eksik gelen kayıtları haksız yere kaybetmemek için.`);
  }
  if (result.low_evidence) {
    lines.push(`Daha geniş sonuç için: sorunuzu daha genel yazın, tarih aralığını
      genişletin veya yayın türü filtrelerini kaldırın.`);
  }

  box.innerHTML = lines.map((l) => `<p>${l}</p>`).join("");
  box.hidden = false;
}

/* Soruyu yanıtlayan makale bulunamadığında: uydurma cevap yerine dürüst bir boş sonuç. */
function noAnswerHtml(result) {
  const tips = (result.suggestions || []).map((t) => `<li>${esc(t)}</li>`).join("");
  return `<div class="no-answer">
    <h3>Bu soruyu doğrudan yanıtlayan makale bulunamadı</h3>
    <p>Tarama ${result.pool_size} kayıt getirdi, ancak hiçbiri sorunuzu doğrudan ele almıyor.
       Birkaç alakasız makaleden kendinden emin bir cevap yazmak yerine durduk; böyle bir
       cevap kaynaklı göründüğü için daha da yanıltıcı olurdu.
       <b>Bu arama için kredi düşülmedi.</b></p>
    ${tips ? `<p>Deneyebilecekleriniz:</p><ul>${tips}</ul>` : ""}
    <p class="no-answer-foot">Tarama sonuçları <b>Articles</b> sekmesinde duruyor,
       kendiniz inceleyebilirsiniz.</p>
  </div>`;
}

/* Modelin yazdığı uzlaşı değil, çıkarılan sayılardan hesaplanan yön uyuşması. */
function renderAgreement(result) {
  const box = $("#agreement");
  const groups = result.agreement || [];
  if (!groups.length) { box.innerHTML = ""; box.hidden = true; return; }

  const word = { decrease: "azalma", increase: "artış", null: "anlamsız" };
  box.innerHTML = `
    <h3 class="bib-head">Çalışmalar ne yönde uyuşuyor</h3>
    <p class="bib-note">Bu tablo sentezden bağımsızdır: her satır, makalelerden birebir
      çıkarılan etki büyüklüğü ve güven aralığından hesaplanır. Güven aralığı etkisizlik
      noktasını kesiyorsa sonuç "anlamsız" sayılır.</p>
    ${groups.map((g) => {
      const parts = Object.entries(g.counts)
        .filter(([, v]) => v)
        .map(([k, v]) => `${v} çalışma ${word[k]}`).join(" · ");
      const rows = g.entries.map((e) => `
        <tr>
          <td><a href="https://pubmed.ncbi.nlm.nih.gov/${e.pmid}/" target="_blank"
                 rel="noopener">PMID ${esc(e.pmid)}</a></td>
          <td>${e.year || ""}</td>
          <td class="f-value">${esc([e.measure, e.value].filter(Boolean).join(" "))}</td>
          <td class="f-ci">${e.ci ? "95% CI " + esc(e.ci) : ""}</td>
          <td><span class="dir dir-${e.direction}">${word[e.direction]}</span></td>
        </tr>`).join("");
      return `<div class="agree-group">
                <div class="agree-head">
                  <b>${esc(g.outcome)}</b>
                  <span class="agree-counts">${parts}</span>
                  ${g.conflicting ? '<span class="badge badge-off">çelişkili</span>' : ""}
                </div>
                <table class="findings"><tbody>${rows}</tbody></table>
              </div>`;
    }).join("")}`;
  box.hidden = false;
}

/* Kısa Cevap'taki cümleleri kaynak metne karşı sınayan katmanın bulguları. */
function renderClaimFlags(result) {
  const box = $("#claim-flags");
  const flags = result.flagged_claims || [];
  if (!flags.length) { box.innerHTML = ""; box.hidden = true; return; }

  const label = { partial: "kaynağı aşıyor", unsupported: "kaynakta yok" };
  box.innerHTML = `
    <h4>Kısa Cevap'ta gözden geçirilmesi gereken ${flags.length} cümle</h4>
    <p>Her cümle, atıf verdiği makalenin metnine karşı ayrıca sınandı. Aşağıdakiler
       birebir doğrulanamadı; cümleyi açıp kaynağın ne dediğini kendiniz görün.</p>
    <ul>
      ${flags.map((f) => `<li>
         <span class="badge badge-off">${label[f.verdict] || f.verdict}</span>
         <span class="flag-reason">${esc(f.reason)}</span>
         <blockquote>${esc(f.sentence)}</blockquote>
       </li>`).join("")}
    </ul>`;
  box.hidden = false;
}

/* -------------------------------------------------------------- rendering */
function render(result) {
  state.result = result;
  state.claims = [];
  const tr = result.translation || {};

  $("#results").hidden = false;
  $("#result-title").textContent = tr.topic || result.query;
  const statsPart = result.stats_count ? `numeric data from ${result.stats_count} · ` : "";
  // Kapsam: kullanıcı kaç kaydın okunmadığını bilmeden sonucun ağırlığını tartamaz.
  const matched = Math.max(result.coverage?.pubmed || 0, result.coverage?.europepmc || 0);
  const coveragePart = matched
    ? `${matched.toLocaleString("en-US")} records matched your query · `
    : "";
  $("#result-meta").innerHTML =
    esc(`${coveragePart}${result.articles.length} articles selected · ` +
        `${result.pool_size} records screened · ` +
        `${result.fulltext_count} full texts read · ${statsPart}${result.elapsed} s · ` +
        `${result.generated_at}`) +
    (result.cached
      ? ` <span class="cache-tag" title="This answer was reused from an identical search, so no new sources were fetched">reused from ${cacheAge(result)}</span>
         <button class="link-btn" id="rerun-fresh">run it fresh</button>`
      : "");

  const rerun = $("#rerun-fresh");
  if (rerun) rerun.addEventListener("click", () => runSearch({ refresh: true }));

  renderEvidenceNotice(result);

  $("#report").innerHTML = result.answer_mode === "none"
    ? noAnswerHtml(result)
    : (result.report ? markdown(result.report)
                     : '<p class="empty">No synthesis was generated.</p>');
  renderClaimFlags(result);
  renderAgreement(result);

  if (result.unverified_pmids && result.unverified_pmids.length) {
    $("#report").insertAdjacentHTML("afterbegin",
      `<p class="badge badge-ft">Warning: ${result.unverified_pmids.length} citation(s) were not
       found in the result set and have been flagged in the text.</p>`);
  }

  $$("#report .src-btn").forEach((btn) =>
    btn.addEventListener("click", () => openDrawer(Number(btn.dataset.claim))));

  renderBibliography();
  renderArticles();
  renderClinical(result.clinical || {});
  renderStrategy(tr, result);

  $("#tab-count-articles").textContent = result.articles.length;
  const clinical = result.clinical || {};
  $("#tab-count-clinical").textContent =
    (clinical.statpearls || []).length + (clinical.guidelines || []).length;

  switchTab("synthesis");
  $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderArticles() {
  const list = $("#article-list");
  let items = [...(state.result?.articles || [])];
  if (state.onlyOA) items = items.filter((a) => a.is_oa || a.pmcid);

  const sorters = {
    score: (a, b) => b.score - a.score,
    year: (a, b) => b.year - a.year,
    citations: (a, b) => b.citations - a.citations,
  };
  items.sort(sorters[state.sort]);

  if (!items.length) {
    list.innerHTML = '<p class="empty">No article matches this filter.</p>';
    return;
  }

  list.innerHTML = items.map((a, i) => {
    const badges = statBadges(a) + (a.journal ? `<span class="badge">${esc(a.journal)}</span>` : "");
    return `
      <div class="card" data-index="${i}">
        <span class="card-score">${a.score.toFixed(1)}</span>
        <h4 class="card-title"><a href="${a.best_free_url || "#"}" target="_blank" rel="noopener">${esc(a.title)}</a></h4>
        <p class="card-authors">${esc(a.authors || "Authors not listed in the record")}</p>
        <div class="badges">${badges}</div>
        ${findingsTable(a)}
        <p class="card-abstract">${esc(a.abstract)}</p>
        <div class="card-actions">
          <button class="link-btn" data-toggle="${i}">Show abstract</button>
          ${articleLinks(a)}
          <button class="link-btn" data-save="${i}" style="margin-left:auto">Save to library</button>
        </div>
      </div>`;
  }).join("");

  list.querySelectorAll("[data-toggle]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const card = btn.closest(".card");
      card.classList.toggle("open");
      btn.textContent = card.classList.contains("open") ? "Hide abstract" : "Show abstract";
    }));

  list.querySelectorAll("[data-save]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const article = items[Number(btn.dataset.save)];
      const res = await fetch("/api/library", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ article }),
      }).then((r) => r.json());
      toast(res.added ? "Saved to your library." : "Already in your library.");
      loadLibrary();
    }));
}

function renderClinical(clinical) {
  const box = $("#clinical-content");
  const parts = [];

  const chapters = clinical.statpearls || [];
  if (chapters.length) {
    parts.push(`
      <div class="group">
        <h3>StatPearls point-of-care chapters</h3>
        <p>Continuously updated clinical summaries, free to read in full on NCBI Bookshelf.</p>
        <div class="grid-2">
          ${chapters.map((c) => `
            <a class="res-card" href="${esc(c.url)}" target="_blank" rel="noopener">
              <b>${esc(c.title)}</b>
              <small>${esc(c.note)}${c.year ? " · " + esc(c.year) : ""}</small>
              <span class="res-src">${esc(c.source)}</span>
            </a>`).join("")}
        </div>
      </div>`);
  }

  const guidelines = clinical.guidelines || [];
  if (guidelines.length) {
    parts.push(`
      <div class="group">
        <h3>Guidelines and Cochrane reviews</h3>
        <p>Publications indexed in PubMed as practice guidelines, consensus statements or systematic reviews.</p>
        <div class="grid-2">
          ${guidelines.map((g) => `
            <a class="res-card" href="${g.best_free_url || g.pubmed_url}" target="_blank" rel="noopener">
              <b>${esc(g.title)}</b>
              <small>${esc(g.journal)} ${g.year} · ${esc(g.evidence_label)}${g.is_oa ? " · free full text" : ""}</small>
              <span class="res-src">PubMed ${g.pmid}</span>
            </a>`).join("")}
        </div>
      </div>`);
  }

  const links = clinical.links || [];
  if (links.length) {
    parts.push(`
      <div class="group">
        <h3>Open the same question elsewhere</h3>
        <p>Clinical decision resources, pre-filled with your question. Subscription resources have a dashed border.</p>
        <div class="grid-2">
          ${links.map((l) => `
            <a class="res-card ${l.free ? "" : "locked"}" href="${esc(l.url)}" target="_blank" rel="noopener">
              <b>${esc(l.title)}</b>
              <small>${esc(l.note)}</small>
              <span class="res-src">${esc(l.source)}${l.free ? "" : " · subscription"}</span>
            </a>`).join("")}
        </div>
      </div>`);
  }

  box.innerHTML = parts.join("") || '<p class="empty">No clinical resource found.</p>';
}

function renderStrategy(tr, result) {
  $("#strategy-content").innerHTML = `
    <div class="strategy-block">
      <h4>Your question</h4>
      <code>${esc(result.query)}</code>
    </div>
    <div class="strategy-block">
      <h4>PubMed query</h4>
      <code>${esc(tr.pubmed_query || "-")}</code>
    </div>
    <div class="strategy-block">
      <h4>Academic phrasing</h4>
      <code>${esc(tr.academic || "-")}</code>
    </div>
    <div class="strategy-block">
      <h4>MeSH terms</h4>
      <div class="mesh-list">${(tr.mesh || []).map((m) => `<span class="badge">${esc(m)}</span>`).join("") || "-"}</div>
    </div>
    <div class="strategy-block">
      <h4>Screening summary</h4>
      <code>${result.pool_size} unique records screened, ${result.articles.length} articles selected, ${result.fulltext_count} full texts read.
Sources: PubMed, Europe PMC, PubMed Central, OpenAlex, Unpaywall.</code>
    </div>`;
}

/* ------------------------------------------------------------------- tabs */
$$(".tab").forEach((tab) => tab.addEventListener("click", () => switchTab(tab.dataset.tab)));
function switchTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + name));
}

$$("[data-sort]").forEach((btn) =>
  btn.addEventListener("click", () => {
    state.sort = btn.dataset.sort;
    $$("[data-sort]").forEach((b) => b.classList.toggle("active", b === btn));
    renderArticles();
  }));

$("#filter-oa").addEventListener("change", (e) => {
  state.onlyOA = e.target.checked;
  renderArticles();
});

/* ----------------------------------------------------------------- export */
$$("[data-export]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    if (!state.result) return;
    const fmt = btn.dataset.export;
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "…";
    try {
      const res = await fetch(`/api/export/${fmt}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ report_id: state.result.report_id }),
      });
      if (!res.ok) throw new Error("Export failed");
      const blob = await res.blob();
      const disposition = res.headers.get("Content-Disposition") || "";
      const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/i);
      const plain = disposition.match(/filename="(.+?)"/);
      const name = utf8 ? decodeURIComponent(utf8[1]) : (plain ? plain[1] : `premise.${fmt}`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      link.click();
      URL.revokeObjectURL(url);
      toast(`${name} downloaded.`);
    } catch (err) {
      toast(err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }));

/* ------------------------------------------------------ history & library */
async function loadHistory() {
  const rows = await fetch("/api/history?limit=30").then((r) => r.json()).catch(() => []);
  const box = $("#history-list");
  if (!rows.length) { box.innerHTML = '<p class="empty">No searches yet.</p>'; return; }
  box.innerHTML = rows.map((r) => `
    <div class="row">
      <div class="row-main">
        <b>${esc(r.topic || r.query)}</b>
        <small>${esc(r.created_at)} · ${r.article_count} articles · ${esc(r.language || "")}</small>
      </div>
      <button class="link-btn" data-load="${r.id}">Open</button>
      <button class="link-btn" data-del="${r.id}" style="color:var(--muted)">Delete</button>
    </div>`).join("");

  box.querySelectorAll("[data-load]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const data = await fetch(`/api/report/${btn.dataset.load}`).then((r) => r.json());
      render(data);
      toast("Saved report opened.");
    }));
  box.querySelectorAll("[data-del]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      await fetch(`/api/report/${btn.dataset.del}`, { method: "DELETE" });
      loadHistory();
    }));
}

async function loadLibrary() {
  const rows = await fetch("/api/library").then((r) => r.json()).catch(() => []);
  const box = $("#library-list");
  if (!rows.length) {
    box.innerHTML = '<p class="empty">Empty. Save articles from the results list.</p>';
    return;
  }
  box.innerHTML = rows.map((r) => `
    <div class="row">
      <div class="row-main">
        <b><a href="${esc(r.url || "#")}" target="_blank" rel="noopener">${esc(r.title)}</a></b>
        <small>${esc(r.journal || "")} ${r.year || ""} · ${esc((r.authors || "").split(",")[0])} et al.</small>
      </div>
      <button class="link-btn" data-rm="${r.id}" style="color:var(--muted)">Remove</button>
    </div>`).join("");
  box.querySelectorAll("[data-rm]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      await fetch(`/api/library/${btn.dataset.rm}`, { method: "DELETE" });
      loadLibrary();
    }));
}

$("#refresh-history").addEventListener("click", loadHistory);
$("#refresh-library").addEventListener("click", loadLibrary);

/* ------------------------------------------------------------------ hesap */
async function loadAccount() {
  const pill = $("#status-pill");
  let me;
  try {
    const res = await fetch("/api/me");
    if (res.status === 401) { location.replace("/giris"); return null; }
    if (!res.ok) throw new Error();
    me = await res.json();
  } catch {
    pill.className = "pill pill-warn";
    pill.textContent = "offline";
    return null;
  }

  if (me.is_admin) {
    pill.className = "pill";
    pill.textContent = "yönetici · sınırsız";
  } else {
    const low = me.credits_left <= 40;          // bir varsayılan aramanın altı
    pill.className = low ? "pill pill-warn" : "pill";
    pill.textContent = `${me.credits_left} kredi`;
    pill.title = `${me.plan_label} planı · ${me.credits_total} krediden ${me.credits_left} tanesi kaldı`;
  }

  // Ücretsiz katmanda tam metin okuma kapalı: anahtarı kilitle ve sebebini söyle.
  const ft = $("#use_fulltext");
  if (ft && !me.fulltext_allowed) {
    ft.checked = false;
    ft.disabled = true;
    const label = ft.closest(".switch");
    if (label) label.title = "Tam metin okuma ücretli planlarda açıktır.";
  }
  return me;
}

/* ----------------------------------------------------------------- start */
(async function init() {
  const me = await loadAccount();
  if (!me) return;
  loadHistory();
  loadLibrary();
})();
