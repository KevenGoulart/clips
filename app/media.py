"""Imagens e vídeos de cada cena: Wikimedia Commons (acervo histórico, sem chave), Pexels e Pixabay (banco de
imagens, chave gratuita). A IA confere os resultados do Commons, que são mais imprecisos."""
import hashlib
import html
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from . import llm
from .config import USER_AGENT, ffmpeg, settings
from .script import Scene, shots_needed

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
IMAGE_W = 1920  # largura baixada (tamanho padrão de miniatura do Wikimedia, que limita tamanhos fora do padrão)
session = requests.Session()
session.headers["User-Agent"] = USER_AGENT
_last_request: dict[str, float] = {}


def _request(url, **kw) -> requests.Response:
    """GET com intervalo mínimo por site e novas tentativas quando o site pede para esperar (HTTP 429/503)."""
    host = url.split("/")[2]
    timeout = kw.pop("timeout", 30)
    for attempt in range(5):
        wait = 0.6 - (time.monotonic() - _last_request.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last_request[host] = time.monotonic()
        r = session.get(url, timeout=timeout, **kw)
        if r.status_code not in (429, 503):
            return r
        r.close()
        retry = r.headers.get("Retry-After", "")
        time.sleep(min(60, int(retry)) if retry.isdigit() else 5 * 2 ** attempt)
    raise RuntimeError(f"Limite de buscas atingido em {host}. Espere alguns minutos e retome a produção.")


@dataclass
class Candidate:
    url: str
    type: str          # image | video
    width: int
    height: int
    title: str         # nome/descrição, usado pela IA para escolher
    credit: str        # autor e licença, vai para a descrição do vídeo
    page: str          # página de origem
    provider: str


def _get(url, cache: dict, **params) -> dict:
    headers = {"Authorization": params.pop("_auth")} if "_auth" in params else {}
    key = url + "?" + json.dumps({k: v for k, v in params.items() if k != "key"}, sort_keys=True)  # sem a chave da API
    if key not in cache:
        r = _request(url, params=params, headers=headers)
        r.raise_for_status()
        cache[key] = r.json()
    return cache[key]


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


# --------------------------------------------------------------------------- provedores

def commons(query: str, cache: dict) -> list[Candidate]:
    data = _get(COMMONS_API, cache, action="query", format="json", generator="search",
                gsrsearch=f"{query} filetype:bitmap", gsrnamespace=6, gsrlimit=12, prop="imageinfo",
                iiprop="url|size|mime|extmetadata", iiurlwidth=IMAGE_W,
                iiextmetadatafilter="LicenseShortName|Artist|ImageDescription")
    pages = sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        if info.get("width", 0) < 900 or info.get("mime") not in ("image/jpeg", "image/png", "image/webp", "image/tiff"):
            continue
        meta = info.get("extmetadata", {})
        artist = _strip_html(meta.get("Artist", {}).get("value", "")) or "autor desconhecido"
        lic = meta.get("LicenseShortName", {}).get("value", "")
        desc = _strip_html(meta.get("ImageDescription", {}).get("value", ""))[:160]
        name = p["title"].removeprefix("File:").rsplit(".", 1)[0]
        out.append(Candidate(info.get("thumburl") or info["url"], "image", info.get("thumbwidth") or info["width"],
                             info.get("thumbheight") or info["height"], f"{name} — {desc}" if desc else name,
                             f"{artist[:80]} ({lic}), via Wikimedia Commons", info.get("descriptionurl", ""), "commons"))
    return out


def pexels_videos(query: str, cache: dict, orientation: str) -> list[Candidate]:
    key = settings()["pexels_key"]
    if not key:
        return []
    data = _get("https://api.pexels.com/videos/search", cache, query=query, orientation=orientation,
                per_page=10, size="medium", _auth=key)
    out = []
    for v in data.get("videos", []):
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
        # o arquivo mais próximo de 1080p (4K pesa demais para baixar e decodificar)
        files.sort(key=lambda f: abs(min(f["width"], f["height"]) - 1080))
        if not files or v.get("duration", 0) < 4:
            continue
        f = files[0]
        out.append(Candidate(f["link"], "video", f["width"], f["height"], re.sub(r"[-/]", " ", v.get("url", "")).split("video ")[-1],
                             f"{v.get('user', {}).get('name', 'Pexels')} via Pexels", v.get("url", ""), "pexels"))
    return out


def pexels_photos(query: str, cache: dict, orientation: str) -> list[Candidate]:
    key = settings()["pexels_key"]
    if not key:
        return []
    data = _get("https://api.pexels.com/v1/search", cache, query=query, orientation=orientation, per_page=10, _auth=key)
    return [Candidate(p["src"]["original"] + f"?auto=compress&w={IMAGE_W}", "image", p["width"], p["height"],
                      p.get("alt", ""), f"{p.get('photographer', 'Pexels')} via Pexels", p.get("url", ""), "pexels")
            for p in data.get("photos", [])]


def pixabay_videos(query: str, cache: dict, orientation: str) -> list[Candidate]:
    key = settings()["pixabay_key"]
    if not key:
        return []
    data = _get("https://pixabay.com/api/videos/", cache, key=key, q=query[:100], per_page=10, safesearch="true")
    out = []
    for h in data.get("hits", []):
        v = h.get("videos", {}).get("large") or h.get("videos", {}).get("medium") or {}
        if not v.get("url") or h.get("duration", 0) < 4:
            continue
        if (orientation == "landscape") != (v.get("width", 0) >= v.get("height", 0)):
            continue
        out.append(Candidate(v["url"], "video", v["width"], v["height"], h.get("tags", ""),
                             f"{h.get('user', 'Pixabay')} via Pixabay", h.get("pageURL", ""), "pixabay"))
    return out


def pixabay_photos(query: str, cache: dict, orientation: str) -> list[Candidate]:
    key = settings()["pixabay_key"]
    if not key:
        return []
    data = _get("https://pixabay.com/api/", cache, key=key, q=query[:100], image_type="photo", per_page=10,
                orientation="horizontal" if orientation == "landscape" else "vertical", safesearch="true")
    return [Candidate(h["largeImageURL"], "image", h.get("imageWidth", 1280), h.get("imageHeight", 720),
                      h.get("tags", ""), f"{h.get('user', 'Pixabay')} via Pixabay", h.get("pageURL", ""), "pixabay")
            for h in data.get("hits", [])]


# --------------------------------------------------------------------------- escolha

PICK_SCHEMA = {"type": "object", "properties": {"picks": {"type": "array", "items": {"type": "integer"}}},
               "required": ["picks"]}

PICK_PROMPT = """Você escolhe imagens de arquivo para um documentário. Recebe o tema do documentário, o que o narrador
diz na cena e uma lista de imagens do Wikimedia Commons, cada uma como [N] nome do arquivo — descrição.
Devolva em picks os números das imagens que mostram de verdade o assunto da cena, da melhor para a pior, no máximo {n}.
A imagem precisa ser do MESMO assunto: a mesma pessoa, o mesmo país ou cidade, o mesmo evento, a mesma época.
Uma família real de outro país, uma cidade de outra época, um brasão ou selo, um logotipo, um ícone ou um diagrama
NÃO servem. Na dúvida, descarte. Se nenhuma servir, devolva lista vazia. Responda apenas JSON."""


def _pick_commons(scene: Scene, cands: list[Candidate], n: int, topic: str) -> list[Candidate]:
    if not cands:
        return []
    lines = "\n".join(f"[{i}] {c.title}" for i, c in enumerate(cands[:10]))
    r = llm.chat(PICK_PROMPT.format(n=n), f"Documentário: {topic}\nNarração: {scene.narration or scene.text}\n"
                                          f"Busca: {scene.query}\n\n{lines}",
                 PICK_SCHEMA, temperature=0.1, num_ctx=4096, timeout=300)
    picks = [i for i in r.get("picks", []) if isinstance(i, int) and 0 <= i < len(cands)]
    return [cands[i] for i in dict.fromkeys(picks)]


def _candidates(scene: Scene, need: int, orientation: str, cache: dict, used: set, topic: str,
                subject: str) -> list[Candidate]:
    def fresh(cs):
        return [c for c in cs if c.url not in used]

    stock_order = [pexels_videos, pixabay_videos, pexels_photos, pixabay_photos]
    queries = [scene.query]
    short = " ".join(scene.query.split()[:2])
    if short and short != scene.query and scene.source == "stock":
        queries.append(short)  # banco de imagens: busca mais ampla se a específica não render (no acervo erra demais)

    chosen: list[Candidate] = []
    for q in queries:
        if scene.source == "archive":
            chosen += _pick_commons(scene, fresh(commons(q, cache)), need - len(chosen), topic)
            for fn in stock_order:
                if len(chosen) >= need:
                    break
                chosen += fresh(fn(q, cache, orientation))[: need - len(chosen)]
        else:
            for fn in stock_order:
                if len(chosen) >= need:
                    break
                chosen += [c for c in fresh(fn(q, cache, orientation)) if c not in chosen][: need - len(chosen)]
            if len(chosen) < need:
                chosen += _pick_commons(scene, fresh(commons(q, cache)), need - len(chosen), topic)
        if len(chosen) >= need:
            break
    if len(chosen) < need and subject and subject.lower() not in (q.lower() for q in queries):
        # nada específico: imagens do assunto principal do vídeo (melhor do que repetir a da cena anterior)
        chosen += _pick_commons(scene, [c for c in fresh(commons(subject, cache)) if c not in chosen],
                                need - len(chosen), topic)
    return chosen[:need]


def _subject(scenes: list[Scene]) -> str:
    """Assunto principal do vídeo: o nome próprio que mais se repete nas buscas (ex.: "Al Capone")."""
    from collections import Counter
    pairs, singles = Counter(), Counter()
    for s in scenes:
        words = re.findall(r"[A-ZÀ-Ý][\w'.-]+", s.query)
        singles.update(words)
        pairs.update(f"{a} {b}" for a, b in zip(words, words[1:]) if f"{a} {b}" in s.query)
    for counter in (pairs, singles):
        if counter:
            name, n = counter.most_common(1)[0]
            if n >= 3:
                return name
    return ""


def _download(c: Candidate, media_dir: Path) -> dict | None:
    name = hashlib.sha1(c.url.encode()).hexdigest()[:16]
    out = media_dir / (name + (".mp4" if c.type == "video" else ".jpg"))
    if not out.exists():
        raw = media_dir / (name + ".download")
        try:
            with _request(c.url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(raw, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            if c.type == "image":  # padroniza em JPEG (o Commons também entrega PNG/WebP/TIFF)
                ffmpeg("-i", raw, "-vf", f"scale='min({IMAGE_W},iw)':-2", "-q:v", "3", out)
                raw.unlink()
            else:
                raw.replace(out)
        except (requests.RequestException, RuntimeError):
            raw.unlink(missing_ok=True)
            return None
    return {"src": f"media/{out.name}", "type": c.type, "aspect": round(c.width / max(1, c.height), 4),
            "credit": c.credit, "page": c.page, "provider": c.provider}


def fetch_all(scenes: list[Scene], public_dir: Path, fmt: str, topic: str, log):
    """Preenche scene.media. Resultados das buscas ficam em cache no projeto (retomar não gasta a cota das APIs)."""
    media_dir = public_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    cache_file = public_dir.parent / "buscas.json"
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    orientation = "landscape" if fmt == "horizontal" else "portrait"
    keys = settings()
    if not keys["pexels_key"] and not keys["pixabay_key"]:
        log("Sem chave do Pexels/Pixabay: usando só o Wikimedia Commons (configure as chaves para ter vídeos).")
    used = {m["src"] for s in scenes for m in s.media}
    used_urls: set[str] = set()
    todo = [s for s in scenes if s.kind != "clip" and not s.media]
    subject = _subject(scenes)
    for n, s in enumerate(todo, 1):
        need = shots_needed(s)
        try:
            cands = _candidates(s, need, orientation, cache, used_urls, topic, subject)
        finally:
            cache_file.write_text(json.dumps(cache), encoding="utf-8")
        for c in cands:
            m = _download(c, media_dir)
            if m and m["src"] not in used:
                used.add(m["src"])
                used_urls.add(c.url)
                s.media.append(m)
        log(frac=n / len(todo), detail=f"{n}/{len(todo)} cenas")
        if n % 5 == 0 or n == len(todo):
            log(f"  mídia {n}/{len(todo)} cenas")
    # cena sem nada encontrado reaproveita a imagem da cena anterior (ou da seguinte, no começo do vídeo)
    for order in (scenes, scenes[::-1]):
        last = []
        for s in order:
            if s.kind != "clip":
                if not s.media and last:
                    s.media = [last[-1] if order is scenes else last[0]]
                last = s.media or last


def credits(scenes: list[Scene]) -> list[str]:
    seen, out = set(), []
    for s in scenes:
        for m in s.media:
            if m.get("page") and m["page"] not in seen:
                seen.add(m["page"])
                out.append(f"{m['credit']} — {m['page']}")
    return out



# --------------------------------------------------------------------------- miniatura

THUMB_SCHEMA = {"type": "object", "properties": {"query": {"type": "string"},
                                                 "source": {"type": "string", "enum": ["archive", "stock"]}},
                "required": ["query", "source"]}

THUMB_PROMPT = """Você escolhe a imagem de fundo da miniatura (thumbnail) de um documentário no YouTube. Recebe o título
e os capítulos. Devolva em query uma busca SEMPRE EM INGLÊS (2 a 4 palavras) por uma foto ampla e marcante que represente
o tema como um todo (o personagem principal, o lugar ou o símbolo mais conhecido), não o detalhe de um capítulo.
source: "archive" para pessoas, lugares e eventos reais (acervo histórico da Wikimedia); "stock" para temas genéricos
(natureza, ciência, espaço, tecnologia) que um banco de imagens mostra bem. Responda apenas JSON."""


def thumbnail_image(title: str, chapter_titles: list[str], scenes: list[Scene], public_dir: Path, log) -> dict | None:
    """Uma foto que represente o vídeo inteiro, buscada à parte das cenas. Prefere fotos horizontais e grandes."""
    subject = _subject(scenes)
    try:
        r = llm.chat(THUMB_PROMPT, f"Título: {title}\nCapítulos: " + "; ".join(t for t in chapter_titles if t),
                     THUMB_SCHEMA, temperature=0.2, num_ctx=4096, timeout=300)
        query, source = str(r.get("query", "")).strip()[:80], r.get("source", "archive")
    except Exception:
        query, source = "", "archive"
    queries = list(dict.fromkeys(q for q in (query, subject) if q)) or [title]

    cache_file = public_dir.parent / "buscas.json"
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    scene = Scene(chapter=0, narration=f"Imagem de capa do documentário: {title}", query=queries[0], source=source)

    def found(cands: list[Candidate]) -> Candidate | None:
        cands = [c for c in cands if c.width >= 1280]
        wide = [c for c in cands if c.width / max(1, c.height) >= 1.2]
        return (wide or cands or [None])[0]

    pick = None
    try:
        for q in queries:
            scene.query = q
            archive = lambda: _pick_commons(scene, commons(q, cache), 5, title)
            stock = lambda: pexels_photos(q, cache, "landscape") + pixabay_photos(q, cache, "landscape")
            for fetch in ((archive, stock) if source == "archive" else (stock, archive)):
                if pick := found(fetch()):
                    break
            if pick:
                break
    finally:
        cache_file.write_text(json.dumps(cache), encoding="utf-8")
    if not pick:
        log("Miniatura: nenhuma imagem encontrada, usando fundo neutro.")
        return None
    media_dir = public_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    m = _download(pick, media_dir)
    if m:
        log(f"Miniatura: imagem de '{scene.query}' ({pick.provider}).")
    return m
