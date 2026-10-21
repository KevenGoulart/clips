"""Pesquisa: texto da Wikipédia sobre o tema, entregue à IA como fonte. O modelo local inventa fatos quando escreve
de memória; com o artigo no contexto ele se limita ao que está documentado."""
import re

import requests

from . import llm
from .config import USER_AGENT

MAX_CHARS = 14000  # cabe no contexto do modelo junto com as instruções


def _wiki(lang: str, topic: str) -> tuple[str, str]:
    api = f"https://{lang}.wikipedia.org/w/api.php"
    headers = {"User-Agent": USER_AGENT}
    hits = requests.get(api, headers=headers, timeout=30, params={
        "action": "query", "list": "search", "srsearch": topic, "srlimit": 1, "format": "json"}).json()
    found = hits.get("query", {}).get("search", [])
    if not found:
        return "", ""
    title = found[0]["title"]
    pages = requests.get(api, headers=headers, timeout=30, params={
        "action": "query", "prop": "extracts", "explaintext": 1, "titles": title, "redirects": 1,
        "format": "json"}).json().get("query", {}).get("pages", {})
    text = next(iter(pages.values()), {}).get("extract", "")
    # sem as seções finais de referências/ligações externas
    text = re.split(r"\n=+ ?(Referências|Ligações externas|Bibliografia|Ver também|Notas|References|External links|"
                    r"See also|Further reading|Notes) ?=+", text)[0]
    return title, re.sub(r"\n{3,}", "\n\n", text).strip()


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"\w+", s.lower()) if len(w) > 3}


def _related(title: str, topic: str) -> bool:
    """O título do artigo precisa ter alguma palavra do tema (evita usar um artigo qualquer como fonte)."""
    return bool(_words(title) & _words(topic))


TERMS_SCHEMA = {"type": "object", "properties": {"local": {"type": "string"}, "en": {"type": "string"}},
                "required": ["local", "en"]}


def gather(topic: str, language: str, log) -> str:
    """Artigo no idioma do vídeo + artigo em inglês (geralmente mais completo), até MAX_CHARS no total."""
    # o tema costuma vir como frase ("Al Capone, o gângster mais famoso..."): a busca funciona melhor só com o assunto
    terms = llm.chat("Diga o título do artigo PRINCIPAL da Wikipédia sobre a pessoa, o lugar ou o evento central do "
                     "tema: o nome dele, não um artigo derivado (ex.: 'Napoleão Bonaparte', e não 'Juventude de "
                     f"Napoleão'). Em local, o título na Wikipédia em {language}; em en, o título na Wikipédia em "
                     "inglês. Responda apenas JSON.",
                     topic, TERMS_SCHEMA, temperature=0, num_ctx=2048)
    langs = [language] + (["en"] if language != "en" else [])
    parts = []
    for lang in langs:
        term = str(terms.get("en" if lang == "en" else "local", "")).strip()
        title, text = "", ""
        for query in dict.fromkeys(q for q in (term, topic) if q):
            try:
                title, text = _wiki(lang, query)
            except (requests.RequestException, ValueError):
                continue
            if text and _related(title, f"{topic} {term}"):
                break
            title, text = "", ""  # a busca devolveu um artigo sem relação com o tema
        if text:
            log(f"Fonte: Wikipédia ({lang}) — {title}")
            parts.append(f"[Wikipédia {lang}: {title}]\n{text}")
    if not parts:
        log("Nenhum artigo da Wikipédia encontrado; a IA vai escrever de memória (revise os fatos com atenção).")
        return ""
    share = MAX_CHARS // len(parts)
    return "\n\n".join(p[:share] for p in parts)
