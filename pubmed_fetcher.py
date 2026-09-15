import os
from datetime import datetime
from Bio import Entrez
from dotenv import load_dotenv

load_dotenv()
Entrez.email = os.getenv("NCBI_EMAIL")
ncbi_api_key = os.getenv("NCBI_API_KEY")
if ncbi_api_key:
    Entrez.api_key = ncbi_api_key  # Rate limiti 3/sn'den 10/sn'ye çıkarır

def _extract_year(medline_citation: dict) -> int:
    try:
        pub_date = medline_citation["Article"]["Journal"]["JournalIssue"]["PubDate"]
        year = pub_date.get("Year")
        if year: return int(year)
        medline_date = pub_date.get("MedlineDate", "")
        if medline_date[:4].isdigit(): return int(medline_date[:4])
    except: pass
    return 0

def fetch_articles(query: str, author: str = "", max_results: int = 15, recent_years: int = 10, progress_callback=None):
    final_query = f"({query}) AND {author}[Author]" if author else query
    
    if progress_callback: progress_callback(f"PubMed'de aranıyor: '{final_query}'...", 0.4)
    
    esearch_kwargs = dict(db="pubmed", term=final_query, retmax=max_results, sort="relevance")
    if recent_years is not None:
        current_year = datetime.now().year
        esearch_kwargs.update(
            datetype="pdat", mindate=f"{current_year - recent_years}/01/01", maxdate=f"{current_year}/12/31",
        )

    try:
        search_handle = Entrez.esearch(**esearch_kwargs)
        search_results = Entrez.read(search_handle)
        search_handle.close()
        
        id_list = search_results.get("IdList", [])
        if not id_list: return []
        
        if progress_callback: progress_callback(f"{len(id_list)} makale bulundu, özetler çekiliyor...", 0.5)
        
        fetch_handle = Entrez.efetch(db="pubmed", id=id_list, rettype="medline", retmode="xml")
        fetch_results = Entrez.read(fetch_handle)
        fetch_handle.close()
    except Exception as e:
        print(f"PubMed API Hatası: {e}")
        return []

    articles = []
    if 'PubmedArticle' in fetch_results:
        for article in fetch_results['PubmedArticle']:
            medline = article['MedlineCitation']
            article_data = medline['Article']
            pmid = str(medline['PMID'])
            title = article_data.get('ArticleTitle', 'No Title')

            abstract = ""
            if 'Abstract' in article_data and 'AbstractText' in article_data['Abstract']:
                abstract = " ".join([str(p) for p in article_data['Abstract']['AbstractText']])

            authors_list = []
            if 'AuthorList' in article_data:
                for au in article_data['AuthorList']:
                    if au.get('LastName'): authors_list.append(f"{au.get('LastName', '')} {au.get('Initials', '')}".strip())
            authors_str = ", ".join(authors_list) if authors_list else "Unknown Authors"

            doi = "DOI Not Found"
            if 'ELocationID' in article_data:
                for eloc in article_data['ELocationID']:
                    if eloc.attributes.get('EIdType') == 'doi':
                        doi = str(eloc)
                        break
            
            if abstract:
                articles.append({
                    "pmid": pmid, "title": title, "abstract": abstract,
                    "year": _extract_year(medline), "pub_types": [str(pt) for pt in article_data.get('PublicationTypeList', [])],
                    "authors": authors_str, "doi": doi
                })
    return articles