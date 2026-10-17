"""Enquadramento 9:16 dinâmico: detecta cortes de câmera, rostos e quem está falando.

Fluxo:
1. Amostra o trecho a 10 fps (em baixa resolução) e detecta rostos com YuNet.
2. Separa o trecho em cenas (trocas de câmera) comparando histogramas de cor.
3. Em cada cena agrupa os rostos em "pessoas" pela posição e descarta detecções esporádicas.
4. Mede o movimento da boca de cada pessoa (descontando o movimento da cabeça) para saber
   quem está falando, com histerese para não ficar trocando a toda hora.
5. Gera, para cada amostra, o modo (corte no rosto ou vídeo inteiro com fundo desfocado)
   e a posição horizontal do corte, suavizada dentro de cada trecho do mesmo falante.
"""
import subprocess
from dataclasses import dataclass

import cv2
import numpy as np

SAMPLE_FPS = 10
ANALYSIS_W = 640
CUT_CORR_HARD = 0.55       # cores mudaram muito: troca de câmera
CUT_CORR_SOFT = 0.8        # cores mudaram um pouco...
CUT_DIFF = 15              # ...e a imagem mudou bastante: troca de câmera (cenários escuros/parecidos)
POS_JUMP = 0.08            # salto de posição maior que isso não é suavizado (corte seco)
MIN_PRESENCE = 0.25        # pessoa precisa aparecer em 25% da cena para contar
MIN_FACE_H = 0.05          # ignora rostos menores que 5% da altura (plateia, fundo)
SPEAKER_SMOOTH = 10        # janela (amostras) para suavizar o movimento da boca (1 s)
MIN_SPEAKER_RUN = 15       # falante precisa durar 1,5 s para o corte trocar de pessoa
TALK_THRESHOLD = 0.8       # movimento mínimo da boca para considerar que alguém fala
SWITCH_RATIO = 1.3         # novo falante precisa mexer a boca 30% mais que o atual


@dataclass
class Plan:
    """Plano de enquadramento por amostra (10 por segundo)."""
    times: np.ndarray       # tempo de cada amostra, relativo ao início do corte
    wide: np.ndarray        # True = vídeo inteiro com fundo desfocado
    cx: np.ndarray          # centro do corte (0..1 da largura) quando wide=False
    run: np.ndarray         # id do trecho contínuo (troca = corte seco, sem interpolar)
    summary: str


def _read_frames(ffmpeg, video, start, dur, w, h, fps, out_w=None, out_h=None):
    """Decodifica frames com ffmpeg (sincronizado com o áudio, fps constante)."""
    vf = f"fps={fps}" + (f",scale={out_w}:{out_h}" if out_w else "")
    fw, fh = (out_w, out_h) if out_w else (w, h)
    proc = subprocess.Popen(
        [ffmpeg, "-v", "error", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{dur:.3f}",
         "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=fw * fh * 3 * 4)
    size = fw * fh * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(fh, fw, 3)
    finally:
        proc.stdout.close()
        proc.wait()


def _region_diff(a, b, x0, y0, x1, y1):
    h, w = a.shape
    x0, x1 = max(0, int(x0)), min(w, int(x1))
    y0, y1 = max(0, int(y0)), min(h, int(y1))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return 0.0
    return float(cv2.absdiff(a[y0:y1, x0:x1], b[y0:y1, x0:x1]).mean())


def _cut_features(frame_small):
    """Miniatura em cinza + histograma de cor, usados para detectar troca de câmera."""
    thumb = cv2.resize(cv2.cvtColor(frame_small, cv2.COLOR_BGR2GRAY), (64, 36),
                       interpolation=cv2.INTER_AREA).astype(np.float32)
    hsv = cv2.cvtColor(frame_small, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return thumb, hist


def _is_cut(prev, cur) -> bool:
    corr = cv2.compareHist(prev[1], cur[1], cv2.HISTCMP_CORREL)
    diff = float(np.abs(prev[0] - cur[0]).mean())
    return corr < CUT_CORR_HARD or (corr < CUT_CORR_SOFT and diff > CUT_DIFF)


def _runs(labels):
    """Lista de (inicio, fim_exclusivo, valor) para sequências iguais."""
    out, s = [], 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[s]:
            out.append((s, i, labels[s]))
            s = i
    return out


def analyze(ffmpeg, video, start, dur, w, h, detector, crop_mode="auto") -> Plan:
    sw = ANALYSIS_W
    sh = int(round(h * sw / w / 2)) * 2
    detector.setInputSize((sw, sh))

    grays, feats, dets = [], [], []
    for frame in _read_frames(ffmpeg, video, start, dur, w, h, SAMPLE_FPS, sw, sh):
        _, faces = detector.detect(frame)
        faces = [] if faces is None else [f for f in faces if f[3] >= MIN_FACE_H * sh]
        grays.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        feats.append(_cut_features(frame))
        dets.append(faces)

    n = len(grays)
    times = np.arange(n) / SAMPLE_FPS
    if n == 0:
        return Plan(times, np.ones(0, bool), np.full(0, 0.5), np.zeros(0, int), "sem frames")

    # ---- cenas (trocas de câmera)
    shot = np.zeros(n, int)
    for i in range(1, n):
        shot[i] = shot[i - 1] + (1 if _is_cut(feats[i - 1], feats[i]) else 0)

    # ---- movimento da boca de cada rosto (relativo à amostra anterior da mesma cena)
    face_info = []  # por amostra: lista de (cx_norm, largura_norm, movimento)
    for i in range(n):
        info = []
        for f in dets[i]:
            x, y, fw, fh = f[:4]
            nose_y = f[9]
            mouth_x = (f[10], f[12])
            mouth_y = max(f[11], f[13])
            eye_y = min(f[5], f[7])
            motion = 0.0
            if i > 0 and shot[i] == shot[i - 1]:
                mouth = _region_diff(grays[i], grays[i - 1], min(mouth_x) - 0.1 * fw, nose_y,
                                     max(mouth_x) + 0.1 * fw, mouth_y + 0.25 * fh)
                eyes = _region_diff(grays[i], grays[i - 1], x + 0.1 * fw, eye_y - 0.15 * fh,
                                    x + 0.9 * fw, nose_y)
                motion = max(0.0, mouth - eyes)  # desconta movimento da cabeça/câmera
            info.append(((x + fw / 2) / sw, fw / sw, motion))
        face_info.append(info)

    wide = np.zeros(n, bool)
    cx = np.full(n, 0.5)
    run = np.zeros(n, int)
    crop_frac = min(1.0, (h * 9 / 16) / w)  # largura do corte em relação ao vídeo
    run_id, n_people_max, switches = 0, 0, 0

    for s_id in np.unique(shot):
        idx = np.where(shot == s_id)[0]
        # ---- agrupa rostos em pessoas pela posição horizontal
        tracks = []  # cada uma: {"x": ultima_x, "pts": {amostra: (cx, largura, movimento)}}
        for i in idx:
            for fx, fwid, mot in sorted(face_info[i], key=lambda t: -t[1]):
                # mesma pessoa = posição próxima e rosto de tamanho parecido
                cands = [t for t in tracks if i not in t["pts"] and 0.5 < fwid / t["w"] < 2.0
                         and abs(t["x"] - fx) < max(0.08, fwid)]
                best = min(cands, key=lambda t: abs(t["x"] - fx), default=None)
                if best is not None:
                    best["pts"][i] = (fx, fwid, mot)
                    best["x"], best["w"] = fx, fwid
                else:
                    tracks.append({"x": fx, "w": fwid, "pts": {i: (fx, fwid, mot)}})
        min_hits = max(2, int(MIN_PRESENCE * len(idx))) if len(idx) >= 8 else 1
        tracks = [t for t in tracks if len(t["pts"]) >= min_hits]

        if crop_mode == "blur" or (not tracks and crop_mode == "auto"):
            wide[idx] = True
            run[idx] = run_id
            run_id += 1
            continue
        if not tracks:  # modo "crop" sem rosto: centro
            cx[idx] = 0.5
            run[idx] = run_id
            run_id += 1
            continue
        n_people_max = max(n_people_max, len(tracks))

        # posição de cada pessoa em todas as amostras da cena (interpolando falhas de detecção)
        pos = []
        for t in tracks:
            ks = sorted(t["pts"])
            pos.append(np.interp(idx, ks, [t["pts"][k][0] for k in ks]))
        widths = [np.median([v[1] for v in t["pts"].values()]) for t in tracks]

        # todos cabem juntos no corte? enquadra o grupo e não troca de pessoa
        left = min(np.median(p) - wd * 0.7 for p, wd in zip(pos, widths))
        right = max(np.median(p) + wd * 0.7 for p, wd in zip(pos, widths))
        if len(tracks) > 1 and right - left <= crop_frac * 0.95:
            cx[idx] = (left + right) / 2
            run[idx] = run_id
            run_id += 1
            continue

        # ---- quem está falando: movimento da boca suavizado + histerese
        motion = np.zeros((len(tracks), len(idx)))
        for ti, t in enumerate(tracks):
            for j, i in enumerate(idx):
                if i in t["pts"]:
                    motion[ti, j] = t["pts"][i][2]
        kernel = np.ones(SPEAKER_SMOOTH) / SPEAKER_SMOOTH
        motion = np.array([np.convolve(m, kernel, mode="same") for m in motion])

        # começa pela pessoa que mais fala na cena (ou a maior, se ninguém mexe a boca)
        totals = motion.sum(axis=1)
        current = int(np.argmax(totals)) if totals.max() > 0 else int(np.argmax(widths))
        labels = []
        for j in range(len(idx)):
            best = int(np.argmax(motion[:, j]))
            if best != current and motion[best, j] > TALK_THRESHOLD \
                    and motion[best, j] > SWITCH_RATIO * motion[current, j]:
                current = best
            labels.append(current)

        # remove trocas curtas demais (vira "pisca-pisca" de câmera)
        changed = True
        while changed and len(labels) > 1:
            changed = False
            runs_ = _runs(labels)
            for k, (a, b, val) in enumerate(runs_):
                if b - a < MIN_SPEAKER_RUN and len(runs_) > 1:
                    fill = runs_[k - 1][2] if k > 0 else runs_[k + 1][2]
                    labels[a:b] = [fill] * (b - a)
                    changed = True
                    break

        for a, b, who in _runs(labels):
            seg = pos[who][a:b]
            # saltos grandes de posição viram corte seco; o resto é suavizado (segue a pessoa sem tremer)
            breaks = [0] + [j for j in range(1, len(seg)) if abs(seg[j] - seg[j - 1]) > POS_JUMP] + [len(seg)]
            for p0, p1 in zip(breaks, breaks[1:]):
                part = seg[p0:p1]
                if len(part) >= 5:
                    k = min(len(part), 15)
                    padded = np.pad(part, (k // 2, k - 1 - k // 2), mode="edge")
                    part = np.convolve(padded, np.ones(k) / k, mode="valid")
                cx[idx[a + p0:a + p1]] = part
                run[idx[a + p0:a + p1]] = run_id
                run_id += 1
        switches += len(_runs(labels)) - 1

    n_shots = int(shot.max()) + 1
    wide_pct = int(100 * wide.mean())
    summary = (f"{n_shots} cena(s), até {n_people_max} pessoa(s) por cena, "
               f"{switches} troca(s) de falante, {wide_pct}% com vídeo inteiro")
    return Plan(times, wide, cx, run, summary)


def render(ffmpeg, video, start, dur, w, h, plan: Plan, out_w, out_h, encoder_args, ass_name, out_name, cwd):
    """Renderiza o corte frame a frame seguindo o plano e grava com áudio e legendas."""
    fps = 30
    # recorte 9:16 do vídeo original
    if w / h > out_w / out_h:
        cw, ch = int(h * out_w / out_h) // 2 * 2, h
    else:
        cw, ch = w, int(w * out_h / out_w) // 2 * 2
    y0 = (h - ch) // 2
    fg_h = int(h * out_w / w) // 2 * 2
    fg_y = (out_h - fg_h) // 2

    enc = subprocess.Popen(
        [ffmpeg, "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{out_w}x{out_h}", "-r", str(fps), "-i", "-",
         "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(video),
         "-map", "0:v:0", "-map", "1:a:0?", "-vf", f"ass={ass_name}",
         *encoder_args, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
         "-shortest", "-movflags", "+faststart", out_name],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)

    n = len(plan.times)
    prev_feat, jump_to = None, -1
    try:
        for fi, frame in enumerate(_read_frames(ffmpeg, video, start, dur, w, h, fps)):
            t = fi / fps
            i = min(int(t * SAMPLE_FPS), n - 1) if n else 0
            # troca de câmera entre duas amostras: aplica já o enquadramento da cena nova
            feat = _cut_features(cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA))
            if prev_feat is not None and i + 1 < n and plan.run[i + 1] != plan.run[i] \
                    and _is_cut(prev_feat, feat):
                jump_to = i + 1
            prev_feat = feat
            if jump_to > i:
                i = jump_to
            if n == 0 or plan.wide[i]:
                # vídeo inteiro sobre fundo desfocado
                bg_src = frame[y0:y0 + ch, (w - cw) // 2:(w - cw) // 2 + cw]
                bg = cv2.resize(bg_src, (out_w // 10, out_h // 10), interpolation=cv2.INTER_AREA)
                bg = cv2.GaussianBlur(bg, (0, 0), 4)
                out = cv2.resize(bg, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
                out = cv2.convertScaleAbs(out, alpha=0.75)
                fg = cv2.resize(frame, (out_w, fg_h), interpolation=cv2.INTER_AREA)
                out[fg_y:fg_y + fg_h] = fg
            else:
                c = plan.cx[i]
                if i + 1 < n and plan.run[i + 1] == plan.run[i]:  # interpola dentro do mesmo trecho
                    frac = t * SAMPLE_FPS - i
                    c = c + (plan.cx[i + 1] - c) * frac
                x = int(min(max(c * w - cw / 2, 0), w - cw))
                out = cv2.resize(frame[y0:y0 + ch, x:x + cw], (out_w, out_h), interpolation=cv2.INTER_LINEAR)
            enc.stdin.write(out.tobytes())
        enc.stdin.close()
    except BrokenPipeError:
        pass
    err = enc.stderr.read().decode(errors="replace")
    if enc.wait() != 0:
        raise RuntimeError(f"Falha ao gerar o vídeo: {err[-1500:]}")
