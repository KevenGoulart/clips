"""Servidor web local: cria projetos de documentário, roda o pipeline em fila e entrega o vídeo."""
import json
import queue
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import pipeline, voice, youtube
from .config import CLIPS_DIR, OUTPUT, save_settings, settings

app = FastAPI(title="Documentários IA")
projects: dict[str, dict] = {}
work_queue: queue.Queue = queue.Queue()
upload_queue: queue.Queue = queue.Queue()
login_state = {"running": False, "error": None}
_save_lock = threading.Lock()

BUSY = ("queued_write", "writing", "queued_produce", "producing")


class ProjectRequest(BaseModel):
    topic: str = ""                       # tema para a IA escrever o roteiro...
    script: str = ""                      # ...ou um roteiro pronto
    minutes: int = Field(10, ge=0, le=60)    # 0 = a IA decide a duração
    language: str = Field("pt", pattern="^(pt|en)$")
    style: str = ""                       # ex.: "dramático, com suspense", "leve e curioso"
    voice: str = ""                       # arquivo da pasta vozes/ (vazio = voz padrão)
    format: str = Field("horizontal", pattern="^(horizontal|vertical)$")
    captions: bool = True
    review: bool = True                   # para depois do roteiro para revisão
    auto_upload: bool = False


class ProjectUpdate(BaseModel):
    script: str | None = None
    voice: str | None = None
    format: str | None = Field(None, pattern="^(horizontal|vertical)$")
    captions: bool | None = None
    auto_upload: bool | None = None


class UploadRequest(BaseModel):
    project_id: str
    title: str
    description: str = ""


class SettingsUpdate(BaseModel):
    pexels_key: str | None = None
    pixabay_key: str | None = None


# --------------------------------------------------------------------------- persistência

def _save(p: dict):
    with _save_lock:
        d = OUTPUT / p["id"]
        d.mkdir(exist_ok=True)
        tmp = d / "project.tmp.json"
        tmp.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(d / "project.json")


def _load_all():
    for f in OUTPUT.glob("*/project.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if p.get("status") in BUSY:  # o servidor foi fechado no meio: dá para retomar
            p["status"] = "error"
            p["log"].append("Interrompido (o app foi fechado). Clique em produzir para retomar.")
        up = p.get("uploads") or {}
        if up.get("state") in ("queued", "uploading"):
            up.update(state="error", error="Interrompido")
        projects[p["id"]] = p


def _log(p, msg):
    p["log"].append(f"[{datetime.now():%H:%M:%S}] {msg}")
    p["log"] = p["log"][-400:]
    _save(p)


def _reporter(p: dict, task: str):
    """log(msg) das etapas do pipeline; step/frac/detail atualizam a barra de progresso da tela sem encher o log."""
    p["progress"] = {"task": task, "step": None, "frac": None, "detail": "", "since": time.time()}

    def report(msg=None, *, step=None, frac=None, detail=None):
        pr = p["progress"]
        if step and step != pr["step"]:
            pr.update(step=step, frac=None, detail="", since=time.time())
        if frac is not None:
            pr["frac"] = round(min(1.0, frac), 4)
        if detail is not None:
            pr["detail"] = detail
        if msg:
            _log(p, msg)
    return report


# --------------------------------------------------------------------------- filas

def worker():
    # um projeto por vez: Ollama, Chatterbox e Remotion disputam a mesma GPU/CPU
    while True:
        pid, task = work_queue.get()
        p = projects.get(pid)
        if not p:
            work_queue.task_done()
            continue
        try:
            if task == "write":
                p["status"] = "writing"
                _log(p, f"Escrevendo roteiro sobre: {p['topic']}")
                p["script"] = pipeline.write(p, _reporter(p, "write"))
                p["status"] = "script_ready"
                _log(p, "Roteiro pronto." + (" Revise e clique em Produzir vídeo." if p["review"] else ""))
                if not p["review"]:
                    _queue(p, "produce")
            else:
                p["status"] = "producing"
                _log(p, "Produzindo o vídeo...")
                p["result"] = pipeline.produce(OUTPUT / pid, p, _reporter(p, "produce"))
                p["status"] = "done"
                _save(p)
                if p.get("auto_upload"):
                    _enqueue_upload(UploadRequest(project_id=pid, title=p["result"]["title"],
                                                  description=p["result"]["description"]))
        except Exception as e:
            traceback.print_exc()
            p["status"] = "error" if task == "produce" or p.get("script") else "error_write"
            _log(p, f"ERRO: {e}")
        finally:
            p["progress"] = None
            _save(p)
            work_queue.task_done()


def _queue(p: dict, task: str):
    p["status"] = "queued_" + task
    work_queue.put((p["id"], task))
    _log(p, f"Na fila (posição {work_queue.qsize()})")


def _enqueue_upload(req: UploadRequest):
    p = projects[req.project_id]
    p["uploads"] = {"state": "queued", "progress": 0, "url": None, "error": None}
    _save(p)
    upload_queue.put(req)


def upload_worker():
    while True:
        req: UploadRequest = upload_queue.get()
        p = projects[req.project_id]
        up = p["uploads"]
        up["state"] = "uploading"
        try:
            up["url"] = youtube.upload(OUTPUT / p["id"] / p["result"]["file"], req.title, req.description,
                                       on_progress=lambda pct: up.update(progress=pct))
            up["state"] = "done"
            _log(p, f"Publicado no YouTube: {up['url']}")
            thumb = p["result"].get("thumbnail")
            if thumb and (OUTPUT / p["id"] / thumb).exists():
                try:
                    youtube.set_thumbnail(up["url"], OUTPUT / p["id"] / thumb)
                    _log(p, "Miniatura enviada.")
                except Exception as e:
                    _log(p, f"Aviso: a miniatura não foi enviada: {e}")
        except Exception as e:
            up.update(state="error", error=str(e))
            _log(p, f"ERRO no envio: {e}")
        finally:
            _save(p)
            upload_queue.task_done()


_load_all()
threading.Thread(target=worker, daemon=True).start()
threading.Thread(target=upload_worker, daemon=True).start()


# --------------------------------------------------------------------------- projetos

def _get(pid: str) -> dict:
    if pid not in projects:
        raise HTTPException(404, "Projeto não encontrado")
    return projects[pid]


@app.post("/api/projects")
def create_project(req: ProjectRequest):
    if not req.topic.strip() and not req.script.strip():
        raise HTTPException(400, "Informe um tema ou cole um roteiro.")
    if req.voice and req.voice not in voice.voices():
        raise HTTPException(400, "Voz não encontrada na pasta vozes/.")
    if req.auto_upload and not youtube.status().get("connected"):
        raise HTTPException(400, "Conecte sua conta do YouTube antes de ativar o envio automático.")
    pid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    p = {**req.model_dump(), "id": pid, "created": datetime.now().isoformat(timespec="seconds"),
         "status": "script_ready", "log": [], "result": None, "uploads": {}}
    p["title"] = req.topic.strip()[:100]
    projects[pid] = p
    if req.script.strip():
        _log(p, "Roteiro recebido.")
        if not req.review:
            _queue(p, "produce")
    else:
        _queue(p, "write")
    return p


@app.get("/api/projects")
def list_projects():
    items = sorted(projects.values(), key=lambda p: p["created"], reverse=True)
    return [{"id": p["id"], "title": (p.get("result") or {}).get("title") or _script_title(p) or p["title"],
             "status": p["status"], "created": p["created"]} for p in items]


def _script_title(p: dict) -> str:
    first = (p.get("script") or "").lstrip().split("\n", 1)[0]
    return first[2:].strip() if first.startswith("# ") else ""


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return _get(pid)


@app.put("/api/projects/{pid}")
def update_project(pid: str, req: ProjectUpdate):
    p = _get(pid)
    if p["status"] in BUSY:
        raise HTTPException(400, "Espere o processamento terminar para editar.")
    if req.voice and req.voice not in voice.voices():
        raise HTTPException(400, "Voz não encontrada na pasta vozes/.")
    p.update({k: v for k, v in req.model_dump().items() if v is not None})
    _save(p)
    return p


@app.post("/api/projects/{pid}/produce")
def produce(pid: str):
    p = _get(pid)
    if p["status"] in BUSY:
        raise HTTPException(400, "Esse projeto já está na fila.")
    if not (p.get("script") or "").strip():
        raise HTTPException(400, "O projeto ainda não tem roteiro.")
    _queue(p, "produce")
    return p


@app.post("/api/projects/{pid}/rewrite")
def rewrite(pid: str):
    p = _get(pid)
    if p["status"] in BUSY:
        raise HTTPException(400, "Esse projeto já está na fila.")
    if not p.get("topic", "").strip():
        raise HTTPException(400, "Esse projeto não tem tema (o roteiro foi colado).")
    _queue(p, "write")
    return p


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    p = _get(pid)
    if p["status"] in BUSY or p.get("uploads", {}).get("state") in ("queued", "uploading"):
        raise HTTPException(400, "Esse projeto está em processamento.")
    try:
        shutil.rmtree(OUTPUT / pid)
    except FileNotFoundError:
        pass
    except OSError:
        raise HTTPException(400, "Não foi possível apagar: algum arquivo do projeto está aberto em outro programa.")
    projects.pop(pid, None)
    return {"ok": True}


@app.get("/api/storage")
def storage():
    size = sum(f.stat().st_size for f in OUTPUT.rglob("*") if f.is_file())
    return {"projects": len(projects), "bytes": size}


# --------------------------------------------------------------------------- recursos e configurações

@app.get("/api/resources")
def resources():
    keys = settings()
    return {"voices": voice.voices(), "clips": sorted(p.name for p in CLIPS_DIR.iterdir() if p.is_file()),
            "pexels": bool(keys["pexels_key"]), "pixabay": bool(keys["pixabay_key"])}


@app.put("/api/settings")
def put_settings(req: SettingsUpdate):
    save_settings(req.model_dump())
    return resources()


# --------------------------------------------------------------------------- YouTube

@app.get("/api/youtube/status")
def youtube_status():
    return {**youtube.status(), "login_running": login_state["running"], "login_error": login_state["error"]}


@app.post("/api/youtube/login")
def youtube_login():
    if login_state["running"]:
        return {"ok": True}

    def run():
        login_state.update(running=True, error=None)
        try:
            youtube.login()
        except Exception as e:
            login_state["error"] = str(e)
        finally:
            login_state["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


@app.post("/api/youtube/logout")
def youtube_logout():
    youtube.logout()
    return {"ok": True}


@app.post("/api/youtube/upload")
def youtube_upload(req: UploadRequest):
    p = _get(req.project_id)
    if not p.get("result"):
        raise HTTPException(404, "O vídeo ainda não foi produzido.")
    if p.get("uploads", {}).get("state") in ("queued", "uploading", "done"):
        raise HTTPException(400, "Esse vídeo já foi enviado ou está na fila.")
    if not youtube.status().get("connected"):
        raise HTTPException(400, "Conecte sua conta do YouTube primeiro.")
    _enqueue_upload(req)
    return p["uploads"]


app.mount("/output", StaticFiles(directory=OUTPUT), name="output")
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
