"""Pipeline: YouTube -> transcrição -> IA escolhe momentos -> cortes 9:16 legendados."""
import gc
import glob
import json
import os
import re
import shutil
import site
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path

import requests

from . import framing

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
YUNET_MODEL = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:14b")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")

OUT_W, OUT_H = 1080, 1920

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


# --------------------------------------------------------------------------- utilidades

def _register_cuda_dlls():
    """No Windows o ctranslate2 precisa achar as DLLs do cuBLAS/cuDNN instaladas via pip."""
    if os.name != "nt":
        return
    for sp in site.getsitepackages():
        for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            os.add_dll_directory(d)
            os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    pattern = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe")
    hits = glob.glob(pattern)
    if hits:
        return hits[0]
    raise RuntimeError("ffmpeg não encontrado. Instale com: winget install Gyan.FFmpeg")


FFMPEG = find_ffmpeg()


def fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list = field(default_factory=list)


@dataclass
class Clip:
    start: float
    end: float
    title: str
    description: str
    score: int
    reason: str
    file: str = ""


# --------------------------------------------------------------------------- 1. download

def download(url: str, job_dir: Path, log) -> tuple[Path, dict]:
    import yt_dlp

    log("Baixando vídeo do YouTube...")
    opts = {
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(job_dir / "source.%(ext)s"),
        "ffmpeg_location": str(Path(FFMPEG).parent),
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "retries": 15,
        "fragment_retries": 15,
        "continuedl": True,  # retoma o arquivo parcial em vez de recomeçar
    }
    for attempt in range(1, 4):  # quedas de conexão do YouTube são comuns em vídeos longos
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except yt_dlp.utils.DownloadError as e:
            if attempt == 3 or "bytes read" not in str(e) and "timed out" not in str(e).lower():
                raise
            log(f"Conexão caiu durante o download, retomando (tentativa {attempt + 1}/3)...")
    path = job_dir / "source.mp4"
    if not path.exists():
        path = next(job_dir.glob("source.*"))
    meta = {"title": info.get("title", ""), "duration": info.get("duration", 0),
            "width": info.get("width"), "height": info.get("height")}
    log(f"Baixado: {meta['title']} ({fmt_ts(meta['duration'] or 0)})")
    return path, meta


# --------------------------------------------------------------------------- 2. transcrição

def load_audio(video: Path, sr=16000):
    """Decodifica o áudio com ffmpeg (mono, 16 kHz, float32) no formato que o Whisper espera."""
    import numpy as np
    proc = subprocess.run([FFMPEG, "-nostdin", "-i", str(video), "-vn", "-ac", "1", "-ar", str(sr),
                           "-f", "f32le", "-loglevel", "error", "-"], capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Falha ao extrair áudio: {proc.stderr.decode(errors='replace')[-500:]}")
    return np.frombuffer(proc.stdout, np.float32)


def transcribe(video: Path, language: str | None, log) -> tuple[list[Segment], str]:
    """Retorna os trechos transcritos e o idioma (informado ou detectado pelo Whisper)."""
    _register_cuda_dlls()
    from faster_whisper import WhisperModel

    try:
        model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="int8_float16")
        device = "GPU"
    except Exception as e:  # sem CUDA -> CPU
        log(f"GPU indisponível ({e.__class__.__name__}), usando CPU (mais lento)...")
        model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        device = "CPU"

    log(f"Transcrevendo com Whisper {WHISPER_MODEL} na {device}...")
    seg_iter, info = model.transcribe(
        load_audio(video), language=language or None, word_timestamps=True,
        vad_filter=True, beam_size=5,
    )
    segments = []
    for s in seg_iter:
        words = [Word(w.start, w.end, w.word.strip()) for w in (s.words or []) if w.word.strip()]
        segments.append(Segment(s.start, s.end, s.text.strip(), words))
        if len(segments) % 50 == 0:
            log(f"  ...{fmt_ts(s.end)} transcritos")
    log(f"Transcrição pronta: {len(segments)} trechos, idioma '{info.language}'.")

    del model
    gc.collect()  # libera VRAM para o Ollama
    return segments, info.language


# --------------------------------------------------------------------------- 3. IA escolhe os momentos

CLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "score": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["start", "end", "title", "description", "score", "reason"],
            },
        }
    },
    "required": ["clips"],
}

SYSTEM_PROMPT = """Você é um editor especialista em vídeos curtos virais (TikTok, Reels, Shorts).
Recebe a transcrição de um trecho de vídeo, onde cada linha é: [ID | tempo] fala.
Escolha os melhores momentos para virar cortes independentes de NO MÁXIMO {max_s} segundos.

Regra mais importante: cada corte trata de UM ÚNICO assunto, do início ao fim.
- Começa exatamente onde a ideia começa, com um gancho (pergunta, afirmação forte, revelação, emoção, humor).
- Termina exatamente onde a ideia se conclui (conclusão, punchline, frase de impacto).
- NUNCA inclua o começo do próximo assunto nem pedaços do assunto anterior.
- {max_s} s é um limite, não uma meta: um corte de 15 ou 20 s com o assunto completo é melhor
  que um de {max_s} s que mistura assuntos. Não estique o corte para chegar ao limite.
- Se a ideia completa não cabe em {max_s} s, escolha outro momento.

Para cada corte retorne:
- start: ID da linha onde o assunto começa
- end: ID da linha onde o assunto termina (duração total até {max_s} s)
- title: título curto e chamativo (máx. 50 caracteres), escrito em {lang_name}
- description: legenda para a postagem (uma frase + 3 a 5 hashtags), escrita em {lang_name}
- score: nota de 1 a 100 do potencial viral
- reason: em uma frase, em português, por que esse momento é bom

O vídeo está em {lang_name}. title e description DEVEM estar inteiramente em {lang_name}, sem misturar idiomas.
Retorne no máximo {per_chunk} cortes. Se nada for bom, retorne lista vazia. Responda apenas JSON."""

LANG_NAMES = {"pt": "português do Brasil", "en": "inglês (English)", "es": "espanhol (español)"}


def _chunks(segments: list[Segment], max_chars=9000):
    chunk, size = [], 0
    for i, s in enumerate(segments):
        line = f"[{i} | {fmt_ts(s.start)}] {s.text}"
        if chunk and size + len(line) > max_chars:
            yield chunk
            # sobreposição de alguns trechos para não perder momentos na fronteira
            chunk, size = chunk[-6:], sum(len(l) for _, l in chunk[-6:])
        chunk.append((i, line))
        size += len(line)
    if chunk:
        yield chunk


def ollama_check():
    try:
        tags = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).json()
    except requests.RequestException:
        raise RuntimeError("Ollama não está rodando. Abra o app Ollama ou rode 'ollama serve'.")
    names = [m["name"] for m in tags.get("models", [])]
    if not any(n == OLLAMA_MODEL or n.startswith(OLLAMA_MODEL + ":") for n in names):
        raise RuntimeError(f"Modelo '{OLLAMA_MODEL}' não instalado. Rode: ollama pull {OLLAMA_MODEL}")


def _ask_llm(lines: str, max_s, per_chunk, language) -> list[dict]:
    lang_name = LANG_NAMES.get(language, f"no idioma '{language}'")
    resp = requests.post(f"{OLLAMA_URL}/api/chat", timeout=600, json={
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": CLIP_SCHEMA,
        "think": False,  # modelos com raciocínio (qwen3 etc.): resposta direta em JSON, bem mais rápida
        "keep_alive": "2m",
        "options": {"temperature": 0.3, "num_ctx": 8192},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.format(max_s=max_s, per_chunk=per_chunk, lang_name=lang_name)},
            {"role": "user", "content": lines},
        ],
    })
    resp.raise_for_status()
    content = resp.json()["message"]["content"]
    try:
        return json.loads(content).get("clips", [])
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        return json.loads(m.group(0)).get("clips", []) if m else []


SENTENCE_END = re.compile(r"[.!?…]['\"”)]*$")


def _fit_bounds(segments, s_idx, e_idx, max_s) -> tuple[float, float]:
    """Mantém o fim escolhido pela IA; só encurta se passar do máximo, sempre no fim de uma frase.

    Nunca estende o corte para "completar" a duração: isso puxaria o próximo assunto.
    """
    def closes(i):
        return bool(SENTENCE_END.search(segments[i].text))

    # a IA às vezes aponta para um pedaço de frase: completa só a frase, sem puxar outro assunto
    for _ in range(2):
        if s_idx > 0 and not closes(s_idx - 1) and segments[e_idx].end - segments[s_idx - 1].start <= max_s:
            s_idx -= 1
        if e_idx + 1 < len(segments) and not closes(e_idx) and segments[e_idx + 1].end - segments[s_idx].start <= max_s:
            e_idx += 1

    start = segments[s_idx].start
    if segments[e_idx].end - start <= max_s and closes(e_idx):
        return max(0.0, start - 0.15), segments[e_idx].end + 0.25

    # passou do máximo: procura a última palavra que fecha uma frase dentro do limite
    words = [w for s in segments[s_idx:e_idx + 1] for w in s.words if w.end - start <= max_s]
    ends = [w.end for w in words if SENTENCE_END.search(w.text)]
    if ends:
        end = ends[-1]
    else:  # nenhuma frase fecha dentro do limite: corta na última palavra que cabe
        end = words[-1].end if words else start + max_s
    return max(0.0, start - 0.15), end + 0.25


def unload_llm():
    """Tira o modelo da memória (VRAM/RAM) para a renderização e o próximo Whisper."""
    try:
        requests.post(f"{OLLAMA_URL}/api/generate", json={"model": OLLAMA_MODEL, "keep_alive": 0}, timeout=30)
    except requests.RequestException:
        pass


def find_moments(segments: list[Segment], max_s: int, count: int, log, language: str = "pt") -> list[Clip]:
    ollama_check()
    min_s = 8  # só descarta trechos curtos demais para ter conteúdo
    chunks = list(_chunks(segments))
    # pede ~1,5x a quantidade desejada (sobra margem para descartar sobreposições), até 8 por parte
    per_chunk = max(2, min(8, -(-count * 3 // (2 * len(chunks)))))
    candidates: list[Clip] = []

    for n, chunk in enumerate(chunks, 1):
        log(f"IA analisando parte {n}/{len(chunks)} com {OLLAMA_MODEL}...")
        valid_ids = {i for i, _ in chunk}
        for c in _ask_llm("\n".join(l for _, l in chunk), max_s, per_chunk, language):
            try:
                s, e = int(c["start"]), int(c["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if s not in valid_ids:
                continue
            e = min(max(e, s), len(segments) - 1)
            start, end = _fit_bounds(segments, s, e, max_s)
            if end - start < min_s:
                continue
            candidates.append(Clip(start, end, str(c.get("title", "")).strip()[:70],
                                   str(c.get("description", "")).strip(),
                                   int(c.get("score", 50) or 50), str(c.get("reason", "")).strip()))

    # melhores primeiro, descartando cortes muito sobrepostos
    chosen: list[Clip] = []
    for c in sorted(candidates, key=lambda c: c.score, reverse=True):
        overlap = any(min(c.end, o.end) - max(c.start, o.start) > 0.3 * (c.end - c.start) for o in chosen)
        if not overlap:
            chosen.append(c)
        if len(chosen) >= count:
            break
    chosen.sort(key=lambda c: c.start)
    unload_llm()
    log(f"IA escolheu {len(chosen)} momento(s).")
    return chosen


# --------------------------------------------------------------------------- 4. enquadramento 9:16

def _face_detector():
    import cv2
    if not YUNET_MODEL.exists():
        MODELS_DIR.mkdir(exist_ok=True)
        YUNET_MODEL.write_bytes(requests.get(YUNET_URL, timeout=60).content)
    return cv2.FaceDetectorYN.create(str(YUNET_MODEL), "", (320, 320), 0.75)


def video_size(video: Path) -> tuple[int, int]:
    import cv2
    cap = cv2.VideoCapture(str(video))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return w, h


# --------------------------------------------------------------------------- 5. legendas (ASS)

def _ass_escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def write_ass(path: Path, words: list[Word], clip: Clip, font: str):
    def ts(t):
        t = max(0, t)
        return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"

    out = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {OUT_W}", f"PlayResY: {OUT_H}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sub,{font},86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,3,2,60,60,560,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    # agrupa 3 palavras por vez; a palavra falada no momento fica amarela e maior
    groups, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) == 3 or re.search(r"[.!?,]$", w.text):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)

    for gi, g in enumerate(groups):
        g_end = groups[gi + 1][0].start if gi + 1 < len(groups) else g[-1].end
        for wi, w in enumerate(g):
            w_start = w.start
            w_end = g[wi + 1].start if wi + 1 < len(g) else g_end
            parts = []
            for k, other in enumerate(g):
                txt = _ass_escape(other.text.upper())
                parts.append(r"{\c&H00FFFF&\fscx110\fscy110}" + txt + r"{\r}" if k == wi else txt)
            a, b = w_start - clip.start, w_end - clip.start
            if b > a:
                out.append(f"Dialogue: 0,{ts(a)},{ts(b)},Sub,,0,0,0,,{' '.join(parts)}")

    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- 6. render

def _encoder_args():
    if not hasattr(_encoder_args, "cache"):
        probe = subprocess.run([FFMPEG, "-hide_banner", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
                                "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True)
        _encoder_args.cache = (["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21", "-b:v", "0"]
                               if probe.returncode == 0 else
                               ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])
    return _encoder_args.cache


def render_clip(video: Path, clip: Clip, segments: list[Segment], out_path: Path,
                crop_mode: str, subtitles: bool, log):
    work = out_path.parent
    ass_name = out_path.stem + ".ass"
    words = [w for s in segments for w in s.words if w.end > clip.start and w.start < clip.end]
    write_ass(work / ass_name, words if subtitles else [], clip, font="Arial Black" if os.name == "nt" else "DejaVu Sans")

    w, h = video_size(video)
    dur = clip.end - clip.start
    if w / max(h, 1) <= 9 / 16 + 0.01:  # vídeo já vertical: não há o que reenquadrar
        crop_mode = "crop"
    plan = framing.analyze(FFMPEG, video.resolve(), clip.start, dur, w, h, _face_detector(), crop_mode)
    log(f"Renderizando '{clip.title}' ({dur:.0f}s): {plan.summary}...")
    # cwd = pasta do job: evita escapar caminho do Windows no filtro 'ass'
    framing.render(FFMPEG, video.resolve(), clip.start, dur, w, h, plan, OUT_W, OUT_H,
                   _encoder_args(), ass_name, out_path.name, cwd=work)
    (work / ass_name).unlink(missing_ok=True)


# --------------------------------------------------------------------------- orquestração

def process(url: str, job_dir: Path, max_s: int = 60, count: int = 3, crop_mode: str = "auto",
            subtitles: bool = True, language: str | None = None, log=print) -> list[dict]:
    job_dir.mkdir(parents=True, exist_ok=True)
    ollama_check()  # falha cedo, antes de baixar
    video, meta = download(url, job_dir, log)
    segments, lang = transcribe(video, language, log)
    if not segments:
        raise RuntimeError("Não foi encontrada fala no vídeo.")
    (job_dir / "transcript.json").write_text(
        json.dumps([asdict(s) for s in segments], ensure_ascii=False), encoding="utf-8")

    clips = find_moments(segments, max_s, count, log, language=lang)
    if not clips:
        raise RuntimeError("A IA não encontrou momentos adequados. Tente outro vídeo ou duração.")

    results = []
    for i, clip in enumerate(clips, 1):
        out = job_dir / f"corte_{i:02d}.mp4"
        render_clip(video, clip, segments, out, crop_mode, subtitles, log)
        clip.file = out.name
        results.append(asdict(clip))
    (job_dir / "clips.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log("Pronto!")
    return results
