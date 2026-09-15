import os
import json
import time
import re
import concurrent.futures
from datetime import datetime
import google.generativeai as genai
from dotenv import load_dotenv
from pubmed_fetcher import fetch_articles

load_dotenv()

# --- 1. GEMINI API HAVUZU YÖNETİMİ ---
api_keys_str = os.getenv("GEMINI_API_KEYS", "")
API_KEYS = [k.strip() for k in api_keys_str.split(",") if k.strip()]

if not API_KEYS:
    raise ValueError("GEMINI_API_KEYS bulunamadı! Lütfen .env dosyasını kontrol et.")

current_key_index = 0
genai.configure(api_key=API_KEYS[current_key_index])

def switch_api_key():
    """Kota dolduğunda bir sonraki Gemini API anahtarına geçer."""
    global current_key_index
    current_key_index += 1
    if current_key_index >= len(API_KEYS):
        raise Exception("Kritik Hata: Havuzdaki TÜM Gemini API anahtarlarının kotası doldu!")
    
    genai.configure(api_key=API_KEYS[current_key_index])
    print(f"\n[!] 429 Kota Uyarısı: {current_key_index + 1}. Gemini API anahtarına geçiş yapıldı.")

def generate_with_retry(model, prompt_text, **kwargs):
    """Gemini'a sorgu atar, kota hatası alırsa yeni anahtarla tekrar dener."""
    while True:
        try:
            return model.generate_content(prompt_text, **kwargs)
        except Exception as e:
            error_msg = str(e).lower()
            if "429" in error_msg or "quota" in error_msg or "exhausted" in error_msg:
                switch_api_key()
                time.sleep(1.5)
            else:
                raise e

# --- 2. SİSTEM PROMPTLARI ---
def get_system_prompt(target_language: str) -> str:
    return f"""
    You are an expert biomedical data analyst and researcher. 
    Your task is to review the provided medical abstracts and generate an academic, synthesized, and comprehensive response to the user's question in {target_language}.
    STRICT RULES:
    1. ONLY use information from the provided texts. Do not add outside knowledge.
    2. At the end of every informational sentence, cite the article using [PMID: xxxxx].
    3. At the very end of your response, add a section named "BIBLIOGRAPHY & USAGE PERCENTAGES".
    4. For each article you used, format it exactly as:
       - Article Title, Authors, DOI (Usage Percentage: %X)
    """

TRANSLATE_SYSTEM_PROMPT = """
You are a medical/scientific translation assistant.
Translate the query into natural English, and then into academic PubMed search terms (MeSH).
Respond ONLY with a valid JSON object matching this schema exactly: {"english": "...", "academic": "..."}
"""

# --- 3. ANA FONKSİYONLAR ---
def translate_query(query: str) -> dict:
    """Gemini-3.6-flash kullanarak çok hızlı çeviri yapar."""
    fallback = {"original": query, "english": query, "academic": query}
    try:
        translation_model = genai.GenerativeModel(
            model_name='gemini-3.6-flash',
            system_instruction=TRANSLATE_SYSTEM_PROMPT
        )
        response = generate_with_retry(
            translation_model, 
            query,
            generation_config={"response_mime_type": "application/json"}
        )
        data = json.loads(response.text)
        return {
            "original": query, 
            "english": data.get("english", query) or query, 
            "academic": data.get("academic", query) or query
        }
    except Exception as e:
        print(f"Çeviri Hatası (Gemini): {e}")
        return fallback

def _score_article(article: dict) -> float:
    year = article.get("year", 0)
    recency_score = max(0, 10 - (datetime.now().year - year)) if year else 0
    high_value_types = {"Systematic Review", "Meta-Analysis", "Practice Guideline", "Randomized Controlled Trial"}
    quality_bonus = 2 if set(article.get("pub_types", [])) & high_value_types else 0
    return recency_score + quality_bonus

def validate_pmids_in_response(response_text: str, fetched_articles: list) -> str:
    """Modelin ürettiği metindeki PMID'leri uydurmalara karşı test eder."""
    valid_pmids = {str(article['pmid']) for article in fetched_articles}
    found_pmids = set(re.findall(r'\[PMID:\s*(\d+)\]', response_text))
    hallucinated_pmids = found_pmids - valid_pmids
    
    if hallucinated_pmids:
        print(f"[UYARI] Tespit edilen sahte/bağlam dışı PMID'ler: {hallucinated_pmids}")
        for fake_pmid in hallucinated_pmids:
            response_text = response_text.replace(
                f"[PMID: {fake_pmid}]", 
                f"[PMID: {fake_pmid} ⚠️ DOĞRULANAMADI]"
            )
    return response_text

def run_literature_review(query: str, author: str = "", target_language: str = "English", num_articles: int = 15, progress_callback=None):
    if progress_callback: progress_callback("Adım 1: Sorgu akademik dile çevriliyor...", 0.1)
    
    translations = translate_query(query)
    query_variants = list(dict.fromkeys([translations["original"], translations["english"], translations["academic"]]))

    pool = {}
    if progress_callback: progress_callback(f"Adım 2: {len(query_variants)} farklı PubMed sorgusu paralel çalıştırılıyor...", 0.3)
    
    # ThreadPoolExecutor ile 3 sorgu varyantını paralel çekiyoruz
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_to_q = {
            executor.submit(fetch_articles, q, author, max(num_articles, 10), 10, None): q 
            for q in query_variants
        }
        
        for future in concurrent.futures.as_completed(future_to_q):
            try:
                results = future.result()
                for a in results:
                    pool.setdefault(a["pmid"], a)
            except Exception as exc:
                print(f"Paralel çekim hatası: {exc}")

    if not pool: raise ValueError("Bu kriterlere uygun PubMed makalesi bulunamadı.")

    articles = sorted(pool.values(), key=_score_article, reverse=True)[:num_articles]

    if progress_callback: progress_callback(f"Adım 3: En nitelikli {len(articles)} makale filtrelendi. Sentez başlıyor...", 0.6)

    context = "HERE ARE THE ARTICLES FOR YOUR REVIEW:\n\n"
    for i, a in enumerate(articles, 1):
        context += f"PMID: {a['pmid']} | DOI: {a['doi']} | AUTHORS: {a['authors']} | YEAR: {a['year']}\n"
        context += f"TITLE: {a['title']}\n"
        context += f"ABSTRACT: {a['abstract']}\n\n"

    synthesis_model = genai.GenerativeModel(
        model_name="gemini-3.6-flash", 
        system_instruction=get_system_prompt(target_language)
    )
    
    final_prompt = f"{context}\n\nUSER QUESTION: {query}\nPlease answer this question using the rules provided."
    
    response = generate_with_retry(synthesis_model, final_prompt)
    
    if progress_callback: progress_callback("Adım 4: Halüsinasyon ve bağlam kontrolü yapılıyor...", 0.95)
    
    # Doğrulama aşaması
    validated_text = validate_pmids_in_response(response.text, articles)
    
    if progress_callback: progress_callback("İşlem tamamlandı!", 1.0)
    
    return {"text": validated_text, "articles": articles}