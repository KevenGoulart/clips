"""Caminhos, variáveis de ambiente e utilidades usadas por todo o pipeline."""
import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"          # um projeto (vídeo) por pasta
MODELS_DIR = ROOT / "models"
MOTION_DIR = ROOT / "motion"      # projeto Remotion (cenas animadas)
VOICES_DIR = ROOT / "vozes"       # .wav de referência (~10 s) para clonar a voz do narrador
MUSIC_DIR = ROOT / "musica"       # trilhas de fundo (.mp3/.wav), uma é sorteada por vídeo
CLIPS_DIR = ROOT / "clipes"       # vídeos prontos (ex.: exportados do Claude Motion) que o roteiro pode citar
TTS_PYTHON = ROOT / ".venv-tts" / "Scripts" / "python.exe"
for d in (OUTPUT, VOICES_DIR, MUSIC_DIR, CLIPS_DIR):
    d.mkdir(exist_ok=True)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:14b")
SETTINGS_FILE = ROOT / "settings.json"  # chaves das APIs de imagens, salvas pela tela de configurações

FPS = 30
FORMATS = {"horizontal": (1920, 1080), "vertical": (1080, 1920)}
LANG_NAMES = {"pt": "português do Brasil", "en": "inglês (English)"}
USER_AGENT = "DocumentariosIA/1.0 (app local de uso pessoal)"  # a Wikimedia recusa pedidos sem User-Agent próprio

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


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
FFPROBE = str(Path(FFMPEG).with_name("ffprobe" + Path(FFMPEG).suffix))


def ffmpeg(*args, cwd=None):
    proc = subprocess.run([FFMPEG, "-y", "-v", "error", "-nostdin", *map(str, args)], capture_output=True, cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou: {proc.stderr.decode(errors='replace')[-1500:]}")


def media_duration(path: Path) -> float:
    proc = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True)
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def settings() -> dict:
    """Chaves gratuitas: https://www.pexels.com/api/ e https://pixabay.com/api/docs/ (variável de ambiente ou tela)."""
    saved = {}
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"pexels_key": saved.get("pexels_key") or os.environ.get("PEXELS_API_KEY", ""),
            "pixabay_key": saved.get("pixabay_key") or os.environ.get("PIXABAY_API_KEY", "")}


def save_settings(values: dict):
    current = settings()
    current.update({k: v.strip() for k, v in values.items() if k in current and v is not None})
    SETTINGS_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")


def fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def lang_name(language: str) -> str:
    return LANG_NAMES.get(language, f"no idioma '{language}'")
