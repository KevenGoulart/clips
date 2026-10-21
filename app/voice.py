"""Narração: gera a fala com o Chatterbox (app/tts_worker.py, em .venv-tts) e monta a faixa completa,
definindo o início/duração de cada cena e o tempo de cada palavra das legendas.

Cenas seguidas do mesmo capítulo são narradas juntas num trecho só (até MAX_CHARS), para o TTS encadear a
entonação entre as frases em vez de cada uma começar do zero e terminar caindo. Depois o trecho é cortado
nas pausas entre as frases, e cada cena continua com o seu início e duração. O worker confere cada trecho com o
Whisper; o tempo das palavras que ele ouviu guia os cortes e as legendas (sem ele, ficam estimados pelo texto)."""
import hashlib
import json
import os
import re
import subprocess
import wave
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from .config import ROOT, TTS_PYTHON, VOICES_DIR, media_duration
from .script import Scene
from .speech import speakable, words

SR = 24000            # taxa do Chatterbox
GAP_SENTENCE = 0.28   # pausa entre trechos da mesma cena
GAP_SCENE = 0.45      # pausa entre cenas (quando caem em trechos diferentes)
MAX_HALF_PAUSE = 0.35 # pausa natural dentro de um trecho: até isso de cada lado do corte (0,7 s no total)
TITLE_S = 3.2         # cartela de capítulo (sem narração)
MAX_CHARS = 350       # tamanho máximo de um trecho falado (~23 s; o TTS degrada em textos longos)
EXAGGERATION = 0.5    # expressividade da voz (0.9 deixava a fala arrastada e teatral)
CFG_WEIGHT = 0.5      # mais baixo = fala mais solta e pausada


def voices() -> list[str]:
    return sorted(p.name for p in VOICES_DIR.iterdir() if p.suffix.lower() in (".wav", ".mp3", ".flac"))


def _pieces(narration: str) -> list[str]:
    """Frases da narração; as longas demais são quebradas na vírgula."""
    from .script import sentences
    out = []
    for s in sentences(narration):
        while len(speakable(s)) > MAX_CHARS:
            limit = MAX_CHARS * len(s) // len(speakable(s))
            cut = max((m.end() for m in re.finditer(r"[,;:—]\s", s[:limit])), default=0)
            if cut < 40:
                cut = s.rfind(" ", 0, limit) + 1 or limit
            out.append(s[:cut].strip())
            s = s[cut:].strip()
        if s:
            out.append(s)
    return out


def synthesize(scenes: list[Scene], audio_dir: Path, language: str, voice: str | None, log,
               exaggeration=EXAGGERATION, cfg_weight=CFG_WEIGHT) -> list[list[tuple]]:
    """Gera os .wav de cada trecho (pula os que já existem). Retorna, por cena, a lista de
    (arquivo do trecho, posição da frase no trecho, texto da legenda, textos falados do trecho)."""
    if not TTS_PYTHON.exists():
        raise RuntimeError("Ambiente da narração não instalado. Veja 'Reinstalar do zero' no README (.venv-tts).")
    audio_dir.mkdir(parents=True, exist_ok=True)
    spoken = speakable if language == "pt" else (lambda t: t)
    groups, cur = [], None  # cur = [(cena, legenda, falado)]; cartelas, clipes e troca de capítulo fecham o trecho
    for i, s in enumerate(scenes):
        texts = _pieces(s.narration)
        if not texts:
            cur = None
            continue
        for shown in texts:
            said = spoken(shown)
            if cur is None or scenes[cur[-1][0]].chapter != s.chapter                     or len(" ".join(x[2] for x in cur)) + 1 + len(said) > MAX_CHARS:
                cur = []
                groups.append(cur)
            cur.append((i, shown, said))

    items, per_scene, seen = [], [[] for _ in scenes], set()
    for g in groups:
        said = [x[2] for x in g]
        key = f"{' '.join(said)}|{exaggeration}|{cfg_weight}"
        out = audio_dir / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".wav")
        if str(out) not in seen:
            seen.add(str(out))
            items.append({"text": " ".join(said), "out": str(out)})
        for k, (i, shown, _) in enumerate(g):
            per_scene[i].append((out, k, shown, said))

    voice_path = VOICES_DIR / voice if voice else None
    if voice_path and not voice_path.exists():
        raise RuntimeError(f"Voz '{voice}' não encontrada na pasta vozes/.")
    req = audio_dir / "pedido.json"
    req.write_text(json.dumps({"language": language, "voice": str(voice_path) if voice_path else None,
                               "exaggeration": exaggeration, "cfg_weight": cfg_weight, "items": items},
                              ensure_ascii=False), encoding="utf-8")
    # sem o .json da conferência o worker ainda precisa transcrever o trecho (tempo das palavras)
    pending = sum(not Path(i["out"]).exists() or not Path(i["out"]).with_suffix(".json").exists() for i in items)
    done_before = len(items) - pending
    log(step="narracao", frac=done_before / max(1, len(items)), detail=f"{done_before}/{len(items)} trechos")
    log(f"Narrando {pending} de {len(items)} trechos ({sum(map(len, groups))} frases) com o Chatterbox" + (f" (voz {voice})" if voice else "") + "...")
    if pending:
        with open(audio_dir / "tts_log.txt", "w", encoding="utf-8") as errlog:
            proc = subprocess.Popen([str(TTS_PYTHON), str(ROOT / "app" / "tts_worker.py"), str(req)],
                                    stdout=subprocess.PIPE, stderr=errlog, text=True, encoding="utf-8",
                                    errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            last = 0
            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "status" in ev:
                    log(f"  {ev['status']}", detail=ev["status"])
                elif "done" in ev:
                    log(frac=(done_before + ev["done"]) / max(1, len(items)),
                        detail=f"{done_before + ev['done']}/{len(items)} trechos")
                    if ev["done"] * 10 // ev["total"] > last:  # a cada 10%
                        last = ev["done"] * 10 // ev["total"]
                        log(f"  narração {ev['done']}/{ev['total']}")
            proc.wait()
        if proc.returncode != 0:
            tail = (audio_dir / "tts_log.txt").read_text(encoding="utf-8", errors="replace")[-1200:]
            raise RuntimeError(f"Falha na narração:\n{tail}")
    return per_scene


def _read(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def _trim_bounds(a: np.ndarray, thresh=0.012, margin=0.04) -> tuple[int, int]:
    loud = np.flatnonzero(np.abs(a) > thresh)
    if not len(loud):
        return 0, 0
    pad = int(margin * SR)
    return max(0, loud[0] - pad), min(len(a), loud[-1] + pad)


def _trim(a: np.ndarray, thresh=0.012, margin=0.04) -> np.ndarray:
    """Corta o silêncio (e respirações fracas) do começo e do fim de cada frase."""
    x, y = _trim_bounds(a, thresh, margin)
    return a[x:y]


def _word_times(text: str, start: float, dur: float) -> list[tuple[float, str]]:
    """Sem alinhamento forçado: distribui o tempo da frase pelas palavras, proporcional ao tamanho de cada uma."""
    words = text.split()
    # peso pelo que é falado: "1950" dura como "mil novecentos e cinquenta"
    weights = [len(re.sub(r"\W", "", speakable(w))) + 2 + (4 if re.search(r"[,;:.!?…]$", w) else 0) for w in words]
    total, t, out = sum(weights) or 1, start, []
    for w, wt in zip(words, weights):
        out.append((t, w))
        t += dur * wt / total
    return out


def _caption_groups(words: list[tuple[float, str]], end: float, max_words=6, max_chars=34) -> list[dict]:
    groups, cur = [], []
    for i, (t, w) in enumerate(words):
        cur.append((t, w))
        nxt_len = len(" ".join(x for _, x in cur)) + (len(words[i + 1][1]) + 1 if i + 1 < len(words) else 0)
        if len(cur) >= max_words or nxt_len > max_chars or re.search(r"[.!?…:;]$", w) \
                or (re.search(r",$", w) and len(cur) >= 3):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    out = []
    for gi, g in enumerate(groups):
        g_end = groups[gi + 1][0][0] if gi + 1 < len(groups) else end
        out.append({"start": g[0][0], "end": g_end, "words": [{"start": t, "text": w} for t, w in g]})
    return out


def _split(a: np.ndarray, texts: list[str]) -> list[np.ndarray]:
    """Corta um trecho narrado em frases, nas pausas mais perto de onde cada frase deve terminar
    (estimado pelo tamanho do texto). Prefere pausas longas: o fim de frase pausa mais que uma vírgula."""
    if len(texts) == 1:
        return [a]
    hop = SR // 100  # 10 ms
    quiet = np.abs(a[: len(a) // hop * hop]).reshape(-1, hop).max(1) < 0.015
    edges = np.flatnonzero(np.diff(np.r_[0, quiet.astype(np.int8), 0]))
    pauses = [((b + e) / 2 * hop, (e - b) * hop) for b, e in zip(edges[::2], edges[1::2]) if e - b >= 8]  # >= 80 ms
    w = np.cumsum([len(t) + 6 for t in texts])
    cuts, prev = [], 0
    for k in range(len(texts) - 1):
        target = len(a) * w[k] / w[-1]
        cands = [(c, n) for c, n in pauses if prev < c < len(a) - SR // 5]
        if cands:  # pausa longa e perto do esperado (cada segundo de distância vale 0,15 s de pausa)
            c = max(cands, key=lambda p: p[1] / SR - 0.15 * abs(p[0] - target) / SR)[0]
        else:
            c = max(target, prev + 1)
        cuts.append(int(c))
        prev = c
    bounds = [0, *cuts, len(a)]
    return [_trim(a[x:y], margin=MAX_HALF_PAUSE) for x, y in zip(bounds, bounds[1:])]


def _heard_times(said: list[str], heard: list[list]) -> list[tuple[float, float]] | None:
    """(início, fim) de cada palavra falada do trecho, pelo que o Whisper ouviu; None se não deu para alinhar."""
    flat = [w for s in said for w in words(s)]
    if not flat or not heard:
        return None
    times: list = [None] * len(flat)
    sm = SequenceMatcher(None, flat, [h[0] for h in heard], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = (heard[j1 + k][1], heard[j1 + k][2])
        elif tag == "replace":  # ouvido de outro jeito (nome próprio, sigla): reparte o tempo do que foi ouvido
            a, b, n = heard[j1][1], heard[j2 - 1][2], i2 - i1
            for k in range(n):
                times[i1 + k] = (a + (b - a) * k / n, a + (b - a) * (k + 1) / n)
    if sum(t is not None for t in times) < 0.6 * len(flat):
        return None
    k = 0
    while k < len(times):  # palavras não ouvidas: repartem o espaço entre a anterior e a seguinte
        if times[k] is not None:
            k += 1
            continue
        e = k
        while e < len(times) and times[e] is None:
            e += 1
        a = times[k - 1][1] if k else times[e][0]
        b = times[e][0] if e < len(times) else a
        for x in range(k, e):
            times[x] = (a + (b - a) * (x - k) / (e - k), a + (b - a) * (x - k + 1) / (e - k))
        k = e
    return times


def _split_timed(a: np.ndarray, said: list[str], times: list[tuple]) -> list[tuple] | None:
    """Corta o trecho no meio da pausa entre as frases, pelo tempo das palavras. Cada frase vai até MAX_HALF_PAUSE
    além da última palavra ouvida (o que o Chatterbox inventa depois do fim fica de fora).
    Devolve [(frase, [(início, fim) de cada palavra, em segundos desde o começo da frase])]."""
    idx = np.cumsum([0] + [len(words(s)) for s in said])
    if any(idx[k] == idx[k + 1] for k in range(len(said))):
        return None
    cuts = [0] + [int((times[idx[k] - 1][1] + times[idx[k]][0]) / 2 * SR) for k in range(1, len(said))] + [len(a)]
    out = []
    for k in range(len(said)):
        tt = times[idx[k]:idx[k + 1]]
        x = max(cuts[k], int((tt[0][0] - MAX_HALF_PAUSE) * SR))
        y = min(cuts[k + 1], int((tt[-1][1] + MAX_HALF_PAUSE) * SR))
        x2, y2 = _trim_bounds(a[x:y], margin=MAX_HALF_PAUSE) if y > x else (0, 0)
        if y2 <= x2:
            return None
        start = (x + x2) / SR
        out.append((a[x + x2:x + y2], [(max(0.0, s - start), max(0.0, e - start)) for s, e in tt]))
    return out


def _load(f: Path, said: list[str]) -> list[tuple]:
    """Frases do trecho, cada uma com o tempo das palavras faladas (None quando não há conferência do Whisper)."""
    a = _read(f)
    side = f.with_suffix(".json")
    if side.exists():
        times = _heard_times(said, json.loads(side.read_text(encoding="utf-8")).get("words", []))
        parts = _split_timed(a, said, times) if times else None
        if parts:
            return parts
    return [(p, None) for p in _split(_trim(a), said)]


def _shown_times(text: str, tok: list[tuple], start: float) -> list[tuple[float, str]]:
    """Leva o tempo das palavras faladas para as da legenda: "1950" começa onde começa "mil novecentos e cinquenta"."""
    shown = text.split()
    weights = [max(1, len(words(w))) for w in shown]
    total, out, c = sum(weights), [], 0
    for w, wt in zip(shown, weights):
        out.append((start + tok[min(len(tok) - 1, c * len(tok) // total)][0], w))
        c += wt
    return out


def assemble(scenes: list[Scene], per_scene: list[list[tuple]], out_wav: Path, clips_dir: Path) -> list[dict]:
    """Junta os trechos na faixa final; define scene.start/duration e devolve as legendas (em segundos)."""
    chunks, captions, t = [], [], 0.0
    parts, last = {}, None  # arquivo -> frases já cortadas; last = trecho da frase anterior

    def silence(sec):
        nonlocal t
        n = int(round(sec * SR))
        chunks.append(np.zeros(n, np.float32))
        t += n / SR

    for s, files in zip(scenes, per_scene):
        if last is not None and (not files or files[0][0] != last):  # fim de trecho: pausa fixa
            silence(GAP_SCENE)
        s.start = t
        if s.kind == "clip":
            silence(max(1.0, media_duration(clips_dir / s.clip)))
            last = None
        elif not files:  # cartela de capítulo
            silence(TITLE_S)
            last = None
        else:
            for j, (f, k, text, said) in enumerate(files):
                if f not in parts:
                    parts[f] = _load(f, said)
                if j and f != files[j - 1][0]:
                    silence(GAP_SENTENCE)
                a, tok = parts[f][k]
                # dentro do trecho a pausa natural fica metade em cada frase; a legenda só cobre a fala
                if tok:  # tempo de cada palavra pelo que o Whisper ouviu
                    ws, end = _shown_times(text, tok, t), t + tok[-1][1]
                else:  # sem conferência: estimado pelo tamanho das palavras
                    speech = np.flatnonzero(np.abs(a) > 0.012)
                    lead, tail = (speech[0], speech[-1]) if len(speech) else (0, len(a))
                    ws, end = _word_times(text, t + lead / SR, (tail - lead) / SR), t + tail / SR
                chunks.append(a)
                t += len(a) / SR
                captions += _caption_groups(ws, end)
                last = f
    silence(GAP_SCENE)
    for s, nxt in zip(scenes, scenes[1:] + [None]):  # cenas contíguas: a pausa conta na cena anterior
        s.duration = (nxt.start if nxt else t) - s.start

    audio = np.concatenate(chunks) if chunks else np.zeros(SR, np.float32)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return captions
