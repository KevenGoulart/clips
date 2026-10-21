"""Narração com Chatterbox. Roda no ambiente separado .venv-tts (torch/CUDA), chamado por app/voice.py.

Uso: .venv-tts/Scripts/python app/tts_worker.py <pedido.json>
  pedido = {"language": "pt", "voice": "caminho.wav" | null, "exaggeration": 0.5, "cfg_weight": 0.5,
            "items": [{"text": "...", "out": "caminho.wav"}]}
Imprime uma linha JSON por frase gerada. Frases que já têm .wav são puladas (permite retomar).

Cada trecho gerado é conferido com o Whisper: o Chatterbox às vezes engole palavras, repete um pedaço ou inventa
som no fim. Se a transcrição não bate com o texto, o trecho é gerado de novo (fica a melhor tentativa). O tempo de
cada palavra ouvida vai para um .json ao lado do .wav, usado pelas legendas e para cortar as frases.
"""
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path

import torch
import torchaudio

sys.stdout.reconfigure(encoding="utf-8")  # no Windows o pipe usaria cp1252 e o voice.py lê em UTF-8

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.speech import words  # noqa: E402

PT_BR_DIR = ROOT / "models" / "chatterbox-pt-br"
PT_BR_FILES = [  # (repo, arquivo no repo, nome local)
    ("ResembleAI/chatterbox", "ve.pt", "ve.pt"),
    ("ResembleAI/chatterbox", "conds.pt", "conds.pt"),
    ("ResembleAI/Chatterbox-Multilingual-pt-br", "grapheme_mtl_merged_expanded_v1.json", "grapheme_mtl_merged_expanded_v1.json"),
    ("ResembleAI/Chatterbox-Multilingual-pt-br", "s3gen_v3.pt", "s3gen_v3.pt"),
    ("ResembleAI/Chatterbox-Multilingual-pt-br", "t3_pt_br.safetensors", "t3_pt_br.safetensors"),
]
WHISPER = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
ATTEMPTS = 3       # gerações por trecho, no máximo
MIN_RATIO = 0.85   # semelhança mínima entre o texto e o que foi ouvido
MAX_GAP = 4        # palavras seguidas faltando, sobrando ou trocadas: a partir disso o trecho é refeito


def emit(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def load_model(language: str, device: str):
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    if language != "pt":  # demais idiomas: checkpoint multilíngue V3
        return ChatterboxMultilingualTTS.from_pretrained(device=device, t3_model="v3")
    # português: fine-tune pt-BR da Resemble (Single Language Pack), montado na pasta models/
    import shutil
    from huggingface_hub import hf_hub_download
    PT_BR_DIR.mkdir(parents=True, exist_ok=True)
    for repo, name, local in PT_BR_FILES:
        if not (PT_BR_DIR / local).exists():
            emit(status=f"baixando {name} (só na primeira vez)...")
            shutil.copy(hf_hub_download(repo, name), PT_BR_DIR / local)
    if not (PT_BR_DIR / "s3gen.pt").exists():
        # o s3gen_v3 vem sem os buffers fixos do tokenizer (filtros mel e janela); completa com os valores padrão
        from chatterbox.models.s3gen import S3Gen
        state = torch.load(PT_BR_DIR / "s3gen_v3.pt", map_location="cpu", weights_only=True)
        for k, v in S3Gen().state_dict().items():
            state.setdefault(k, v)
        torch.save(state, PT_BR_DIR / "s3gen.pt")
    return ChatterboxMultilingualTTS.from_local(PT_BR_DIR, device, t3_model="t3_pt_br.safetensors")


def load_whisper(device: str):
    try:
        from faster_whisper import WhisperModel
        emit(status="carregando o Whisper (conferência da narração)...")
        return WhisperModel(WHISPER, device=device, compute_type="float16" if device == "cuda" else "int8")
    except Exception as e:  # sem o Whisper a narração sai sem conferência, como antes
        emit(status=f"Whisper indisponível, narração sem conferência ({e.__class__.__name__})")
        return None


def hear(whisper, wav: torch.Tensor, sr: int, language: str) -> list[list]:
    """[[palavra normalizada, início, fim], ...] em segundos. Um "1950" ouvido vira as palavras faladas dele."""
    audio = torchaudio.functional.resample(wav.float().mean(0), sr, 16000).numpy()
    segments, _ = whisper.transcribe(audio, language=language, word_timestamps=True, condition_on_previous_text=False)
    out = []
    for seg in segments:
        for w in seg.words or []:
            toks = words(w.word)
            for k, tok in enumerate(toks):  # divide o tempo da palavra entre as partes dela
                step = (w.end - w.start) / len(toks)
                out.append([tok, round(w.start + k * step, 3), round(w.start + (k + 1) * step, 3)])
    return out


def score(text: str, heard: list[list]) -> tuple[float, int]:
    """(semelhança, maior trecho de palavras seguidas que não bate)."""
    exp, got = words(text), [h[0] for h in heard]
    sm = SequenceMatcher(None, exp, got, autojunk=False)
    gap = max((max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"), default=0)
    return sm.ratio(), gap


def main():
    req = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    language = req.get("language", "pt")
    ex, cfg = req.get("exaggeration", 0.5), req.get("cfg_weight", 0.5)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = whisper = None

    items = req["items"]
    for i, item in enumerate(items):
        out = Path(item["out"])
        side = out.with_suffix(".json")
        if out.exists() and side.exists():
            emit(done=i + 1, total=len(items), skipped=True)
            continue
        if whisper is None:
            whisper = load_whisper(device) or False
        if out.exists():  # gerado antes da conferência: só transcreve, para as legendas
            if whisper:
                wav, sr = torchaudio.load(str(out))
                heard = hear(whisper, wav, sr, language)
                side.write_text(json.dumps({"score": score(item["text"], heard)[0], "words": heard}), encoding="utf-8")
            emit(done=i + 1, total=len(items), skipped=True)
            continue
        if model is None:
            emit(status=f"carregando Chatterbox na {'GPU' if device == 'cuda' else 'CPU (lento)'}...")
            model = load_model(language, device)
            if req.get("voice"):
                model.prepare_conditionals(req["voice"], exaggeration=ex)

        best = None  # (nota, wav, ouvido, semelhança)
        for attempt in range(ATTEMPTS):
            with torch.inference_mode():
                wav = model.generate(item["text"], language_id=language, exaggeration=ex, cfg_weight=cfg).cpu()
            if not whisper:
                best = (0, wav, None, None)
                break
            heard = hear(whisper, wav, model.sr, language)
            ratio, gap = score(item["text"], heard)
            note = ratio - 0.05 * max(0, gap - MAX_GAP + 1)
            if best is None or note > best[0]:
                best = (note, wav, heard, ratio)
            if ratio >= MIN_RATIO and gap < MAX_GAP:
                break
            if attempt + 1 < ATTEMPTS:
                emit(status=f"trecho {i + 1}: a fala não bateu com o texto ({ratio:.0%}), gerando de novo...")
        _, wav, heard, ratio = best
        tmp = out.with_suffix(".tmp.wav")
        torchaudio.save(str(tmp), wav, model.sr, encoding="PCM_S", bits_per_sample=16)
        tmp.replace(out)
        if heard is not None:
            side.write_text(json.dumps({"score": ratio, "words": heard}), encoding="utf-8")
        emit(done=i + 1, total=len(items))
    emit(finished=True)


if __name__ == "__main__":
    main()
