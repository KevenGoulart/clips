"""Roteiro: a IA escreve a narração a partir de um tema, e todo roteiro (gerado ou colado) vira cenas com visual.

Formato do roteiro (texto editável na tela antes de produzir):
    # Título do vídeo
    ## Capítulo
    Parágrafo de narração...
    [clipe: arquivo.mp4]   <- insere um vídeo pronto da pasta clipes/ (ex.: exportado do Claude Motion)
"""
import math
import re
from dataclasses import dataclass, field

from . import llm, research
from .config import CLIPS_DIR, lang_name

WORDS_PER_MIN = 150  # ritmo médio da narração do Chatterbox em português


# --------------------------------------------------------------------------- 1. escrever a partir de um tema

OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "chapters": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "points": {"type": "array", "items": {"type": "string"}}},
            "required": ["title", "points"]}},
    },
    "required": ["title", "chapters"],
}

OUTLINE_PROMPT = """Você é roteirista de um canal de documentários no YouTube (história de países, biografias,
crimes reais, curiosidades, ciência). Monte o ESBOÇO de um documentário narrado de {minutes} minutos sobre o tema pedido.

Regras:
- Exatamente {n} capítulos, em ordem cronológica ou lógica. O primeiro é a abertura: um gancho forte
  (um fato surpreendente, um mistério, uma cena marcante) que faz a pessoa querer assistir até o fim.
  A abertura tem só 2 ou 3 pontos e NÃO revela o desfecho nem resume o vídeo.
  O último fecha a história com uma conclusão ou reflexão.
- Cada capítulo tem um título curto e marcante (máx. 40 caracteres) e de 3 a 6 pontos com os fatos que ele cobre:
  nomes, datas, lugares, números e acontecimentos concretos. Cada fato aparece em UM capítulo só.
- {facts}
- title: título chamativo para o vídeo no YouTube (máx. 80 caracteres).
{style}Escreva tudo em {lang}. Responda apenas JSON."""

CHAPTER_SCHEMA = {
    "type": "object",
    "properties": {"paragraphs": {"type": "array", "items": {"type": "string"}}},
    "required": ["paragraphs"],
}

CHAPTER_PROMPT = """Você é roteirista e narrador de documentários no YouTube. Escreva a NARRAÇÃO do capítulo indicado
de um documentário, seguindo o esboço completo do vídeo.

Regras:
- Cerca de {words} palavras, em {lang}, divididas em parágrafos de 2 a 4 frases.
- É texto para ser FALADO por um narrador: frases claras, ritmo de história, tom envolvente; varie o tamanho das frases.
- Conte com detalhes concretos (nomes, datas, lugares, números) e conecte causa e consequência.
- Nada de indicações de cena, títulos, listas, emojis, marcações ou texto entre colchetes.
- Não cumprimente, não diga "neste vídeo", não peça inscrição.
- Conte SOMENTE os pontos deste capítulo. O que já foi contado nos capítulos anteriores não se repete (no máximo
  uma menção de passagem para ligar a história); o que fica para os capítulos seguintes não se antecipa.
- Não feche parágrafos nem o capítulo com frases genéricas de efeito ("tornou-se um dos maiores mistérios da
  história"): só o último capítulo conclui. Termine cada parágrafo num fato ou numa pergunta que puxe o próximo.
- {facts}
{position}{style}Responda apenas JSON com a lista de parágrafos."""

REVISE_PROMPT = """Você revisa a narração de um documentário em {lang} antes de ela ser gravada por um narrador.
Corrija, mantendo o conteúdo e o tamanho:
- gramática, concordância, gênero das palavras e crase ("após o decolagem" -> "após a decolagem");
- palavras soltas em inglês ou outro idioma quando existe a palavra em {lang} ("fragmentos de debris" ->
  "fragmentos de destroços"); nomes próprios ficam como estão;
- siglas pouco conhecidas e fusos horários: escreva o que significam ("às 01:19 MYT" -> "à 1h19, no horário da
  Malásia"); siglas famosas (ONU, NASA, FBI) ficam;
- frases que repetem quase igual algo já dito no mesmo trecho.
Não acrescente fatos, não resuma, não mude números, datas e nomes. Devolva os parágrafos na mesma ordem e na mesma
quantidade. Responda apenas JSON com a lista de parágrafos."""


def _revise(paragraphs: list[str], language: str, ctx: int) -> list[str]:
    """Revisão de português do capítulo. Parágrafo que mudou de tamanho demais (a IA resumiu ou inventou) fica o original."""
    try:
        r = llm.chat(REVISE_PROMPT.format(lang=lang_name(language)), "\n\n".join(paragraphs), CHAPTER_SCHEMA,
                     temperature=0.2, num_ctx=ctx)
    except Exception:
        return paragraphs
    revised = _clean_paragraphs(r)
    if len(revised) != len(paragraphs):
        return paragraphs
    return [new if 0.8 <= len(new.split()) / max(1, len(old.split())) <= 1.2 else old
            for old, new in zip(paragraphs, revised)]


# duração livre (minutes = 0): a IA só classifica o tamanho do tema (modelos pequenos erram ao chutar minutos)
SIZE_MINUTES = {"curto": 5, "medio": 10, "longo": 15, "muito_longo": 25}
SIZE_SCHEMA = {"type": "object", "properties": {"size": {"type": "string", "enum": list(SIZE_MINUTES)}},
               "required": ["size"]}
SIZE_PROMPT = """Classifique quanta história o tema de um documentário do YouTube tem para contar, sem encher linguiça:
- curto: um objeto, uma invenção simples, uma curiosidade, um caso isolado (ex.: a história do clipe de papel)
- medio: a maioria das biografias, empresas, crimes e eventos (ex.: Al Capone, o naufrágio do Titanic)
- longo: um período ou personagem com muitas fases (ex.: Napoleão, a corrida espacial)
- muito_longo: guerras mundiais, impérios, a história inteira de um país (ex.: a Segunda Guerra Mundial)
Responda apenas JSON."""


def _free_minutes(topic: str) -> int:
    try:
        size = llm.chat(SIZE_PROMPT, f"Tema: {topic}", SIZE_SCHEMA, temperature=0, num_ctx=2048).get("size")
    except Exception:
        size = None
    return SIZE_MINUTES.get(size, SIZE_MINUTES["medio"])


FACTS_SOURCE = ("Use SOMENTE fatos que estão na FONTE (nomes, datas, lugares, números, citações). "
                "Não acrescente nada que não esteja nela; se a fonte não diz, não afirme.")
FACTS_MEMORY = "Use só fatos bem documentados. Não invente datas, números ou citações; na dúvida, deixe de fora."


def write_script(topic: str, minutes: int, language: str, style: str, log) -> str:
    """minutes = 0: a IA decide a duração pelo tamanho do tema."""
    llm.check()
    log(step="pesquisa", detail="Wikipédia")
    if minutes <= 0:
        minutes = _free_minutes(topic)
        log(f"Duração escolhida pela IA: ~{minutes} min.")
    n = max(3, min(12, round(minutes / 2) + 1))
    style_line = f"- Estilo pedido: {style}\n" if style.strip() else ""
    source = research.gather(topic, language, log)
    facts = FACTS_SOURCE if source else FACTS_MEMORY
    source_block = f"\n\nFONTE:\n{source}" if source else ""
    ctx = 16384 if source else 8192
    log(f"Escrevendo o esboço ({n} capítulos)...", step="roteiro", frac=0, detail="esboço")
    # se a resposta vier vazia: tenta com raciocínio e, por último, sem a fonte (pode ser um artigo que não ajuda)
    attempts = [(False, facts, source_block, ctx), (True, facts, source_block, ctx)]
    if source:
        attempts.append((False, FACTS_MEMORY, "", 8192))
    for think, rule, block, n_ctx in attempts:
        outline = llm.chat(OUTLINE_PROMPT.format(minutes=minutes, n=n, style=style_line, lang=lang_name(language),
                                                 facts=rule),
                           f"Tema: {topic}{block}", OUTLINE_SCHEMA, think=think, temperature=0.6, num_ctx=n_ctx)
        chapters = [c for c in outline.get("chapters", []) if str(c.get("title", "")).strip()]
        if chapters:
            break
        log("  o esboço veio vazio, tentando de novo...")
    if not chapters:
        raise RuntimeError("A IA não conseguiu montar o esboço. Tente reformular o tema.")
    if not block:  # a fonte foi descartada: os capítulos também são escritos sem ela
        facts, source_block, ctx = FACTS_MEMORY, "", 8192
    title = str(outline.get("title", "")).strip() or topic
    sketch = "\n".join(f"{i + 1}. {c['title']}: " + "; ".join(map(str, c.get("points", []))) for i, c in enumerate(chapters))

    total_words = minutes * WORDS_PER_MIN
    # a abertura é só o gancho, mais curta; o resto da duração fica com os outros capítulos
    opening = round(total_words / len(chapters) * 0.6) if len(chapters) > 1 else total_words
    per = (total_words - opening) // max(1, len(chapters) - 1)
    lines = [f"# {title}", ""]
    told = []  # narração dos capítulos já escritos: o próximo vê tudo, para não repetir
    for i, ch in enumerate(chapters):
        log(f"Escrevendo capítulo {i + 1}/{len(chapters)}: {ch['title']}...", frac=(i + 1) / (len(chapters) + 1),
            detail=f"capítulo {i + 1}/{len(chapters)}")
        words = opening if i == 0 else per
        if i == 0:
            position = ("- Este é o capítulo de ABERTURA: comece direto no gancho, sem introdução genérica. Apresente "
                        "o mistério ou a pergunta central, mas NÃO conte o desfecho nem resuma o vídeo.\n")
        elif i == len(chapters) - 1:
            position = "- Este é o ÚLTIMO capítulo: amarre a história e termine com uma frase de impacto.\n"
        else:
            position = ""
        earlier = "\n\n".join(told)[-8000:]
        later = "\n".join(f"- {c['title']}: " + "; ".join(map(str, c.get("points", []))) for c in chapters[i + 1:])
        user = (f"Título do vídeo: {title}\nEsboço:\n{sketch}\n\n"
                + (f"JÁ CONTADO nos capítulos anteriores (não repita):\n{earlier}\n\n" if earlier else "")
                + (f"FICA PARA OS PRÓXIMOS capítulos (não antecipe):\n{later}\n\n" if later else "")
                + f"Escreva agora o capítulo {i + 1}: {ch['title']}\n"
                + "Pontos deste capítulo: " + "; ".join(map(str, ch.get("points", []))) + "\n"
                + (f"\nFinal do capítulo anterior (continue a partir daqui):\n{told[-1].split(chr(10))[-1]}" if told else "")
                + source_block)
        n_ctx = 16384 if source_block or len(earlier) > 3000 else ctx
        system = CHAPTER_PROMPT.format(words=words, lang=lang_name(language), position=position, style=style_line,
                                       facts=facts)
        paragraphs = _clean_paragraphs(llm.chat(system, user, CHAPTER_SCHEMA, temperature=0.6, num_ctx=n_ctx))
        if sum(len(p.split()) for p in paragraphs) < words * 0.6:  # veio curto demais: pede de novo, mais longo
            retry = _clean_paragraphs(llm.chat(system, user + f"\n\nIMPORTANTE: escreva pelo menos {words} palavras.",
                                               CHAPTER_SCHEMA, temperature=0.6, num_ctx=n_ctx))
            paragraphs = max(paragraphs, retry, key=lambda ps: sum(len(p.split()) for p in ps))
        if not paragraphs:
            continue
        log(detail=f"capítulo {i + 1}/{len(chapters)}: revisão")
        paragraphs = _revise(paragraphs, language, 8192)
        lines += [f"## {ch['title']}", "", *(p + "\n" for p in paragraphs)]
        told.append("\n".join(paragraphs))
    return "\n".join(lines).strip() + "\n"


def _clean_paragraphs(r: dict) -> list[str]:
    out = []
    for p in r.get("paragraphs", []):
        p = re.sub(r"\[[^\]]*\]|\*+|#+", "", str(p))  # o roteiro final não pode ter marcações
        p = re.sub(r"\s+", " ", p).strip()
        if p:
            out.append(p)
    return out


# --------------------------------------------------------------------------- 2. roteiro -> cenas

@dataclass
class Scene:
    chapter: int
    kind: str = "image"          # image | title | date | quote | stat | list | clip
    narration: str = ""          # texto falado (vazio nas cartelas de capítulo e nos clipes)
    text: str = ""
    subtext: str = ""
    items: list = field(default_factory=list)
    label: str = ""
    query: str = ""              # busca de imagens/vídeos, em inglês
    source: str = "archive"      # archive (Wikimedia primeiro) | stock (Pexels/Pixabay primeiro)
    clip: str = ""               # arquivo da pasta clipes/
    media: list = field(default_factory=list)  # preenchido por media.py
    start: float = 0.0           # preenchidos depois da narração (segundos)
    duration: float = 0.0


@dataclass
class Chapter:
    title: str
    paragraphs: list


def parse(text: str) -> tuple[str, list[Chapter]]:
    """Lê o roteiro editável: '# título', '## capítulo' e parágrafos separados por linha em branco."""
    title, chapters, buf = "", [], []

    def flush():
        para = " ".join(buf).strip()
        buf.clear()
        if para:
            if not chapters:
                chapters.append(Chapter("", []))
            chapters[-1].paragraphs.append(para)

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            flush()
            chapters.append(Chapter(line[3:].strip(), []))
        elif line.startswith("# ") and not title:
            flush()
            title = line[2:].strip()
        elif not line or re.fullmatch(r"\[clipe:[^\]]+\]", line, re.I):
            flush()
            if line:
                buf.append(line)
                flush()
        else:
            buf.append(line)
    flush()
    chapters = [c for c in chapters if c.paragraphs]
    if not chapters:
        raise RuntimeError("O roteiro está vazio.")
    return title or chapters[0].title or "Documentário", chapters


# quebra depois de . ! ? … (mantendo aspas/parênteses que fecham a frase) quando a próxima começa com maiúscula
SENTENCE = re.compile(r"(?:(?<=[.!?…])|(?<=[.!?…][\"”')\]]))\s+(?=[A-ZÀ-Ý0-9\"“'(¿¡—-])")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE.split(text) if s.strip()]


def split_scenes(title: str, chapters: list[Chapter]) -> list[Scene]:
    """Cada cena tem 1 a 3 frases (~6 a 15 s de narração) e um visual. Capítulos começam com uma cartela."""
    scenes = []
    for ci, ch in enumerate(chapters):
        if ci == 1:  # depois do gancho de abertura entra o título do vídeo
            scenes.append(Scene(ci, "title", text=title))
        if ci >= 1:
            scenes.append(Scene(ci, "title", text=ch.title, subtext=f"Capítulo {ci}"))
        for para in ch.paragraphs:
            clip = re.fullmatch(r"\[clipe:\s*([^\]]+?)\s*\]", para, re.I)
            if clip:
                scenes.append(Scene(ci, "clip", clip=clip.group(1)))
                continue
            cur = []
            for s in sentences(para):
                # uma citação abre cena própria: a cartela aparece quando o narrador chega nela
                if cur and (len(" ".join(cur).split()) + len(s.split()) > 38 or len(" ".join(cur).split()) >= 16
                            or QUOTED.search(s)):
                    scenes.append(Scene(ci, narration=" ".join(cur)))
                    cur = []
                cur.append(s)
            if cur:
                scenes.append(Scene(ci, narration=" ".join(cur)))
    return scenes


# --------------------------------------------------------------------------- 3. visual de cada cena

ANNOTATE_SCHEMA = {
    "type": "object",
    "properties": {"scenes": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "kind": {"type": "string", "enum": ["image", "date", "quote", "stat", "list"]},
            "query": {"type": "string"},
            "source": {"type": "string", "enum": ["archive", "stock"]},
            "label": {"type": "string"},
            "text": {"type": "string"},
            "subtext": {"type": "string"},
            "items": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["id", "kind", "query", "source"]}}},
    "required": ["scenes"],
}

ANNOTATE_PROMPT = """Você é editor de um documentário no YouTube. Recebe as cenas de um capítulo; cada linha é
[ID] narração. Para CADA cena, escolha o que aparece na tela enquanto o narrador fala.

Campos:
- query: busca SEMPRE EM INGLÊS, nunca em português (2 a 5 palavras), por uma foto ou vídeo que mostre o que a narração diz.
  Seja concreto: pessoas, lugares, objetos, eventos, épocas ("Napoleon Bonaparte portrait", "Rio de Janeiro 1900",
  "medieval castle ruins", "dna double helix"). Para pessoas e eventos históricos use o nome próprio.
- source: "archive" para pessoas, lugares, eventos, documentos e mapas reais (acervo histórico da Wikimedia);
  "stock" para ideias genéricas, natureza, ciência, cidades atuais, ações e clima (vídeos de banco de imagens).
- kind: "image" na grande maioria das cenas. Use um destaque animado só quando a narração tiver:
  "date" um ano ou data marcante (text = a data curta, ex. "1822" ou "7 de setembro de 1822"; subtext = o que aconteceu, curto);
  "quote" uma citação dita por alguém (text = a citação exata como na narração; subtext = quem disse);
  "stat" um número impressionante (text = número com unidade, ex. "70 milhões"; subtext = do que é, curto);
  "list" uma enumeração de 2 a 4 itens (text = título curto; items = itens curtos).
  No máximo 1 destaque a cada 4 cenas; nunca dois seguidos.
- label: só em "image", quando a cena apresenta um lugar, pessoa ou ano pela primeira vez, uma tarja curta
  (ex. "Paris, 1789", "Dom Pedro I"); senão deixe vazio.
Textos que aparecem na tela (text, subtext, items, label) em {lang}. Responda apenas JSON, uma entrada por ID."""


def annotate(title: str, chapters: list[Chapter], scenes: list[Scene], language: str, log):
    llm.check()
    clip_names = {p.name.lower(): p.name for p in CLIPS_DIR.iterdir() if p.is_file()}
    for s in scenes:
        if s.kind == "clip":
            s.clip = clip_names.get(s.clip.lower(), "")
            if not s.clip:
                available = ", ".join(sorted(clip_names.values())) or "(pasta vazia)"
                raise RuntimeError(f"Clipe citado no roteiro não está na pasta clipes/. Disponíveis: {available}")
    for ci, ch in enumerate(chapters):
        idx = [i for i, s in enumerate(scenes) if s.chapter == ci and s.narration]
        if not idx:
            continue
        log(f"Planejando o visual do capítulo {ci + 1}/{len(chapters)} ({len(idx)} cenas)...", frac=ci / len(chapters),
            detail=f"capítulo {ci + 1}/{len(chapters)}")
        lines = "\n".join(f"[{i}] {scenes[i].narration}" for i in idx)
        r = llm.chat(ANNOTATE_PROMPT.format(lang=lang_name(language)),
                     f"Documentário: {title}\nCapítulo: {ch.title or title}\n\n{lines}", ANNOTATE_SCHEMA,
                     temperature=0.3, num_ctx=8192)
        got = {a.get("id"): a for a in r.get("scenes", []) if isinstance(a, dict)}
        prev_kind = "image"
        for i in idx:
            a, s = got.get(i, {}), scenes[i]
            s.query = str(a.get("query", "")).strip()[:80] or (ch.title or title)
            s.source = a.get("source") if a.get("source") in ("archive", "stock") else "archive"
            kind = a.get("kind") if a.get("kind") in ("image", "date", "quote", "stat", "list") else "image"
            s.text = str(a.get("text", "")).strip()[:120]
            s.subtext = str(a.get("subtext", "")).strip()[:80]
            s.items = [str(x).strip()[:50] for x in a.get("items", []) if str(x).strip()][:4]
            # o modelo local raramente marca os destaques: citação entre aspas e data no texto viram cartela
            quoted = QUOTED.search(s.narration)
            if kind == "image" and quoted and len(quoted.group(1).split()) >= 3:
                kind, s.text = "quote", quoted.group(1).strip()
                s.subtext = s.subtext or str(a.get("label", "")).strip()
            elif kind == "image" and DATE.fullmatch(s.text):
                kind = "date"
            # destaque sem o texto necessário ou logo depois de outro vira imagem simples
            if (kind != "image" and prev_kind != "image") or (kind != "list" and kind != "image" and not s.text) \
                    or (kind == "list" and len(s.items) < 2):
                kind = "image"
            s.kind = kind
            s.label = str(a.get("label", "")).strip()[:40] if kind == "image" else ""
            prev_kind = kind
    _translate_queries(scenes)
    # as cartelas de capítulo usam a imagem da primeira cena do capítulo (busca própria, mais genérica)
    for s in scenes:
        if s.kind == "title":
            first = next((x for x in scenes if x.chapter == s.chapter and x.narration), None)
            s.query, s.source = (first.query, first.source) if first else (s.text, "archive")


QUOTED = re.compile(r"[\"“]([^\"”]{8,200})[\"”]")
DATE = re.compile(r"(\d{1,2} de \w+ de )?\d{3,4}( (a\.?C\.?|d\.?C\.?))?", re.I)
NOT_ENGLISH = re.compile(r"[ãõçáéíóúâêôàñ]|\b(d[aeo]s?|em|para|com|uma?|história)\b", re.I)

TRANSLATE_SCHEMA = {"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
                    "required": ["queries"]}


def _translate_queries(scenes: list[Scene]):
    """O modelo às vezes devolve a busca em português; o Commons e os bancos de imagens acham muito mais em inglês."""
    todo = [s for s in scenes if s.query and NOT_ENGLISH.search(s.query)]
    if not todo:
        return
    lines = "\n".join(s.query for s in todo)
    r = llm.chat("Traduza cada linha para o inglês como uma busca curta de imagens (2 a 5 palavras), mantendo nomes "
                 "próprios. Devolva queries na mesma ordem, uma por linha recebida. Responda apenas JSON.",
                 lines, TRANSLATE_SCHEMA, temperature=0.1, num_ctx=4096)
    out = [str(q).strip() for q in r.get("queries", [])]
    if len(out) == len(todo):
        for s, q in zip(todo, out):
            if q:
                s.query = q[:80]


def estimated_seconds(scene: Scene) -> float:
    return max(3.0, len(scene.narration.split()) / (WORDS_PER_MIN / 60)) if scene.narration else 3.5


def shots_needed(scene: Scene) -> int:
    """Uma imagem nova a cada ~6 s mantém o ritmo; destaques animados usam uma só, de fundo."""
    if scene.kind in ("title", "date", "quote", "stat", "list"):
        return 1
    return max(1, math.ceil(estimated_seconds(scene) / 6.5))
