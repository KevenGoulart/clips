"""Pipeline: tema -> roteiro (IA) -> [revisão] -> cenas com visual -> mídia -> narração -> Remotion -> vídeo final + miniatura.

Cada etapa salva o resultado na pasta do projeto; se algo falhar, produzir de novo retoma de onde parou."""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from . import llm, media, render, script, voice
from .config import CLIPS_DIR, fmt_ts, lang_name
from .script import Scene

DESC_SCHEMA = {"type": "object", "properties": {"description": {"type": "string"}}, "required": ["description"]}
DESC_PROMPT = """Escreva a descrição de YouTube de um documentário, inteiramente em {lang}: 2 ou 3 frases que despertam
curiosidade sobre o que o vídeo conta, seguidas de 3 a 5 hashtags. Não use emojis. Responda apenas JSON."""


def write(project: dict, log) -> str:
    return script.write_script(project["topic"], project["minutes"], project["language"], project.get("style", ""), log)


def _save_scenes(path: Path, key: str, scenes: list[Scene]):
    path.write_text(json.dumps({"key": key, "scenes": [asdict(s) for s in scenes]}, ensure_ascii=False, indent=1),
                    encoding="utf-8")


def _summary(title: str, chapters: list[script.Chapter], language: str) -> str:
    try:
        r = llm.chat(DESC_PROMPT.format(lang=lang_name(language)),
                     f"Título: {title}\nCapítulos: " + "; ".join(c.title for c in chapters if c.title), DESC_SCHEMA,
                     temperature=0.5, timeout=300)
        return str(r.get("description", "")).strip()
    except Exception:
        return ""


def _chapter_marks(chapters: list[script.Chapter], scenes: list[Scene]) -> str:
    # capítulos do YouTube: o primeiro em 00:00 e cada cartela de capítulo marca o início do seguinte
    marks = [f"00:00 {chapters[0].title or 'Introdução'}"]
    marks += [f"{fmt_ts(s.start)} {s.text}" for s in scenes if s.kind == "title" and s.subtext]
    return "\n".join(marks)


def _thumbnail_media(job_dir: Path, key: str, title: str, chapters: list[script.Chapter], scenes: list[Scene],
                     public: Path, log) -> dict | None:
    # a imagem escolhida fica salva: retomar ou produzir de novo só busca outra se o roteiro mudar
    saved_file = job_dir / "miniatura.json"
    saved = json.loads(saved_file.read_text(encoding="utf-8")) if saved_file.exists() else {}
    if saved.get("key") == key and (not saved["media"] or (public / saved["media"]["src"]).exists()):
        return saved["media"]
    m = media.thumbnail_image(title, [c.title for c in chapters], scenes, public, log)
    saved_file.write_text(json.dumps({"key": key, "media": m}, ensure_ascii=False), encoding="utf-8")
    return m


def produce(job_dir: Path, project: dict, log) -> dict:
    title, chapters = script.parse(project["script"])
    lang, fmt = project["language"], project["format"]
    public = job_dir / "public"
    public.mkdir(parents=True, exist_ok=True)

    # 1. cenas + visual (refeito só se o roteiro ou o formato mudar)
    scenes_file = job_dir / "cenas.json"
    key = hashlib.sha1((project["script"] + fmt).encode()).hexdigest()
    saved = json.loads(scenes_file.read_text(encoding="utf-8")) if scenes_file.exists() else {}
    log(step="cenas")
    if saved.get("key") == key:
        scenes = [Scene(**d) for d in saved["scenes"]]
        log(f"Retomando: {len(scenes)} cenas já planejadas.")
    else:
        scenes = script.split_scenes(title, chapters)
        log(f"Roteiro dividido em {len(scenes)} cenas, {len(chapters)} capítulos.")
        script.annotate(title, chapters, scenes, lang, log)
        _save_scenes(scenes_file, key, scenes)
        for old in (job_dir / "narracao.wav", job_dir / "timeline.json"):
            old.unlink(missing_ok=True)

    # 2. imagens e vídeos
    log("Buscando imagens e vídeos...", step="midia", frac=0)
    media.fetch_all(scenes, public, fmt, title, log)
    _save_scenes(scenes_file, key, scenes)
    log(detail="miniatura")
    thumb = _thumbnail_media(job_dir, key, title, chapters, scenes, public, log) if fmt == "horizontal" else None
    summary = _summary(title, chapters, lang)
    llm.unload()  # libera a GPU para o Chatterbox

    # 3. narração (um .wav por trecho em audio/, nomeado pelo texto: editar uma cena só refaz ela)
    voice_key = hashlib.sha1(f"{project.get('voice')}|{lang}".encode()).hexdigest()[:8]
    per_scene = voice.synthesize(scenes, job_dir / "audio" / voice_key, lang, project.get("voice") or None, log)
    narration = job_dir / "narracao.wav"
    captions = voice.assemble(scenes, per_scene, narration, CLIPS_DIR)
    _save_scenes(scenes_file, key, scenes)
    log(f"Narração pronta: {fmt_ts(scenes[-1].start + scenes[-1].duration)}.")

    # 4. render
    timeline = render.build_timeline(scenes, captions, public, title, fmt, project.get("captions", True), log)
    silent = render.render_video(timeline, job_dir, log)
    log(step="final", detail="áudio")
    final = job_dir / "documentario.mp4"
    music = render.finish(silent, narration, final, project["id"], log)
    silent.unlink(missing_ok=True)
    thumb_file = None
    if fmt == "horizontal":  # nos Shorts o YouTube não usa miniatura personalizada
        log(frac=0.6, detail="miniatura")
        try:
            render.render_thumbnail(public, title, thumb, job_dir / "miniatura.jpg", log)
            thumb_file = "miniatura.jpg"
        except RuntimeError as e:
            log(f"Aviso: {e}")

    # 5. título, descrição (com capítulos e créditos das imagens)
    description = "\n\n".join(p for p in (summary, _chapter_marks(chapters, scenes)) if p)
    credit_lines = media.credits(scenes)
    if thumb and thumb.get("page") and not any(thumb["page"] in line for line in credit_lines):
        credit_lines.insert(0, f"{thumb['credit']} — {thumb['page']}")
    if music:
        credit_lines.append(f"Música: {music}")
    (job_dir / "creditos.txt").write_text("\n".join(credit_lines), encoding="utf-8")
    full = description + "\n\nCréditos das imagens e vídeos:\n"
    for i, line in enumerate(credit_lines):
        if len(full) + len(line) > 4800:  # limite de 5000 caracteres do YouTube
            full += f"... e mais {len(credit_lines) - i} (lista completa em creditos.txt)"
            break
        full += line + "\n"
    log(f"Pronto: {final.name} ({fmt_ts(timeline['durationInFrames'] / render.FPS)}).")
    return {"file": final.name, "title": title[:100], "description": full.strip(),
            "duration": timeline["durationInFrames"] / render.FPS, "thumbnail": thumb_file}

