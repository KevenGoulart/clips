"""Montagem final: tomadas de cada cena -> timeline.json -> Remotion (motion/render.mjs) -> áudio mixado."""
import hashlib
import json
import os
import random
import shutil
import subprocess
from pathlib import Path

from .config import CLIPS_DIR, FFMPEG, FORMATS, FPS, MOTION_DIR, MUSIC_DIR, ffmpeg, media_duration
from .script import Scene

FADE = 12              # igual a FADE em motion/src/theme.ts
PART_FRAMES = FPS * 120  # o render é feito em partes de ~2 min (retoma de onde parou)
MUSIC_VOLUME = 0.16


def _encoder_args():
    if not hasattr(_encoder_args, "cache"):
        probe = subprocess.run([FFMPEG, "-hide_banner", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
                                "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True)
        _encoder_args.cache = (["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "22", "-b:v", "0"]
                               if probe.returncode == 0 else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])
    return _encoder_args.cache


def _prepare_video(src: Path, out: Path, seconds: float, w: int, h: int):
    """Corta (ou repete, se for curto) o vídeo de banco no tempo exato da tomada, já no tamanho do quadro."""
    if out.exists():
        return
    loop = ["-stream_loop", "-1"] if media_duration(src) < seconds else []
    tmp = out.with_suffix(".tmp.mp4")
    ffmpeg(*loop, "-i", src, "-t", f"{seconds:.3f}", "-an",
           "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS},setsar=1",
           *_encoder_args(), "-pix_fmt", "yuv420p", tmp)
    tmp.replace(out)


def build_timeline(scenes: list[Scene], captions: list[dict], public_dir: Path, title: str, fmt: str,
                   show_captions: bool, log) -> dict:
    w, h = FORMATS[fmt]
    shots_dir = public_dir / "shots"
    shots_dir.mkdir(exist_ok=True)
    out_scenes = []
    for si, s in enumerate(scenes):
        start, end = round(s.start * FPS), round((s.start + s.duration) * FPS)
        dur = max(1, end - start)
        if s.kind == "clip":
            clips = public_dir / "clips"
            clips.mkdir(exist_ok=True)
            shutil.copy(CLIPS_DIR / s.clip, clips / s.clip)
            media = {"src": f"clips/{s.clip}", "type": "video", "aspect": w / h}
            out_scenes.append({"from": start, "duration": dur, "kind": "clip",
                               "shots": [{"from": 0, "duration": dur, "media": media}]})
            continue
        media = s.media or [None]
        if s.kind != "image":
            media = media[:1]
        # divide a cena em tomadas iguais, uma por imagem/vídeo
        cuts = [round(dur * k / len(media)) for k in range(len(media) + 1)]
        shots = []
        for k, m in enumerate(media):
            shot_dur = cuts[k + 1] - cuts[k]
            if m and m["type"] == "video":
                out = shots_dir / f"{si:03d}_{k}.mp4"
                # + transição: a tomada continua por baixo enquanto a próxima aparece
                _prepare_video(public_dir / m["src"], out, (shot_dur + FADE + 2) / FPS, w, h)
                m = {**m, "src": f"shots/{out.name}"}
            shots.append({"from": cuts[k], "duration": shot_dur,
                          "media": {k2: m[k2] for k2 in ("src", "type", "aspect", "credit")} if m else None})
        out_scenes.append({"from": start, "duration": dur, "kind": s.kind, "shots": shots,
                           "text": s.text, "subtext": s.subtext, "items": s.items, "label": s.label})
    total = out_scenes[-1]["from"] + out_scenes[-1]["duration"]
    return {
        "fps": FPS, "width": w, "height": h, "durationInFrames": total, "title": title,
        "showCaptions": show_captions, "scenes": out_scenes,
        "captions": [{"start": round(c["start"] * FPS), "end": round(c["end"] * FPS),
                      "words": [{"start": round(x["start"] * FPS), "text": x["text"]} for x in c["words"]]}
                     for c in captions],
    }


def _node() -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js não encontrado. Instale com: winget install OpenJS.NodeJS.LTS")
    if not (MOTION_DIR / "node_modules").exists():
        raise RuntimeError("Dependências do Remotion não instaladas. Rode 'npm install' dentro da pasta motion/.")
    return node


def render_video(timeline: dict, job_dir: Path, log) -> Path:
    """Renderiza as partes com o Remotion e emenda sem recodificar."""
    node = _node()
    data = json.dumps(timeline, ensure_ascii=False)
    (job_dir / "timeline.json").write_text(data, encoding="utf-8")
    # partes já renderizadas só valem para o mesmo timeline; qualquer mudança (roteiro, voz, mídia) renderiza de novo
    parts_dir = job_dir / "partes" / hashlib.sha1(data.encode()).hexdigest()[:12]
    for old in (job_dir / "partes").glob("*"):
        if old != parts_dir:
            shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink(missing_ok=True)
    parts_dir.mkdir(parents=True, exist_ok=True)
    total = timeline["durationInFrames"]
    parts = [{"start": a, "end": min(total, a + PART_FRAMES) - 1, "out": str(parts_dir / f"parte_{i:03d}.mp4")}
             for i, a in enumerate(range(0, total, PART_FRAMES))]
    cfg = job_dir / "render.json"
    cfg.write_text(json.dumps({"timeline": str(job_dir / "timeline.json"), "publicDir": str(job_dir / "public"),
                               "concurrency": max(1, (os.cpu_count() or 4) // 2), "parts": parts}), encoding="utf-8")
    log(f"Animando com o Remotion ({total / FPS / 60:.1f} min, {len(parts)} partes)...", step="render", frac=0)
    with open(job_dir / "render_log.txt", "w", encoding="utf-8") as errlog:
        proc = subprocess.Popen([node, str(MOTION_DIR / "render.mjs"), str(cfg)], cwd=MOTION_DIR,
                                stdout=subprocess.PIPE, stderr=errlog, text=True, encoding="utf-8")
        last = -1
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            pct = int(ev.get("progress", 0) * 100)
            if "part" in ev:
                log(frac=ev["progress"], detail=f"parte {ev['part'] + 1}/{len(parts)}")
            if pct // 5 > last:  # a cada 5%
                last = pct // 5
                log(f"  render {pct}%")
        proc.wait()
    if proc.returncode != 0:
        tail = (job_dir / "render_log.txt").read_text(encoding="utf-8", errors="replace")[-1500:]
        raise RuntimeError(f"Falha no render do Remotion:\n{tail}")

    lst = parts_dir / "lista.txt"
    lst.write_text("".join(f"file '{Path(p['out']).name}'\n" for p in parts), encoding="utf-8")
    out = job_dir / "video_sem_audio.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", out.resolve(), cwd=parts_dir)
    return out


def render_thumbnail(public_dir: Path, title: str, media: dict | None, out: Path, log):
    """Miniatura do YouTube (1280x720, JPEG) com o Remotion: composição "Miniatura" de motion/src/Thumbnail.tsx."""
    image = {k: media[k] for k in ("src", "type", "aspect")} if media else None
    cfg = out.with_suffix(".json")
    cfg.write_text(json.dumps({"publicDir": str(public_dir), "out": str(out), "props": {"title": title, "image": image}},
                              ensure_ascii=False), encoding="utf-8")
    log("Gerando a miniatura...")
    proc = subprocess.run([_node(), str(MOTION_DIR / "thumbnail.mjs"), str(cfg)], cwd=MOTION_DIR,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    cfg.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Falha ao gerar a miniatura:\n{proc.stderr[-1500:]}")


def finish(video: Path, narration: Path, out: Path, seed: str, log) -> str | None:
    """Mixa narração + trilha (que abaixa sozinha quando o narrador fala) e junta com o vídeo."""
    tracks = sorted(p for p in MUSIC_DIR.iterdir() if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg", ".flac"))
    music = random.Random(seed).choice(tracks) if tracks else None
    if music:
        log(f"Mixando a narração com a trilha '{music.name}'...")
        audio = ["-i", narration, "-stream_loop", "-1", "-i", music]
        af = (f"[1:a]aresample=48000,asplit=2[n][sc];"
              f"[2:a]aresample=48000,volume={MUSIC_VOLUME}[m];"
              "[m][sc]sidechaincompress=threshold=0.02:ratio=6:attack=30:release=600[md];"
              "[n][md]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    else:
        log("Finalizando o áudio (coloque músicas na pasta musica/ para ter trilha de fundo)...")
        audio = ["-i", narration]
        af = "[1:a]aresample=48000,loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    ffmpeg("-i", video, *audio, "-filter_complex", af, "-map", "0:v", "-map", "[a]", "-c:v", "copy",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-shortest", "-movflags", "+faststart", out)
    return music.name if music else None
