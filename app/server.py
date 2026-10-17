"""Servidor web local: recebe o link, roda o pipeline em fila e entrega os cortes."""
import threading
import queue
import shutil
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import pipeline, youtube

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
OUTPUT.mkdir(exist_ok=True)

app = FastAPI(title="Clips IA")
jobs: dict[str, dict] = {}
work_queue: queue.Queue = queue.Queue()
upload_queue: queue.Queue = queue.Queue()
login_state = {"running": False, "error": None}


class JobRequest(BaseModel):
    url: str
    duration: int = Field(60, ge=15, le=90)
    count: int = Field(3, ge=1, le=20)
    crop_mode: str = Field("auto", pattern="^(auto|crop|blur)$")
    subtitles: bool = True
    language: str | None = None
    auto_upload: bool = False


class UploadRequest(BaseModel):
    job_id: str
    file: str
    title: str
    description: str = ""


def _log(job, msg):
    job["log"].append(f"[{datetime.now():%H:%M:%S}] {msg}")


def _enqueue_upload(req: UploadRequest):
    job = jobs[req.job_id]
    job["uploads"][req.file] = {"state": "queued", "progress": 0, "url": None, "error": None}
    upload_queue.put(req)


def worker():
    # um job por vez: GPU compartilhada entre Whisper e Ollama
    while True:
        job_id, req = work_queue.get()
        job = jobs[job_id]
        job["status"] = "running"
        try:
            job["clips"] = pipeline.process(
                req.url, OUTPUT / job_id, max_s=req.duration, count=req.count,
                crop_mode=req.crop_mode, subtitles=req.subtitles,
                language=req.language or None, log=lambda m: _log(job, m))
            job["status"] = "done"
            if req.auto_upload:
                _log(job, f"Enviando {len(job['clips'])} corte(s) para o YouTube...")
                for c in job["clips"]:
                    _enqueue_upload(UploadRequest(job_id=job_id, file=c["file"], title=c["title"],
                                                  description=c["description"]))
        except Exception as e:
            _log(job, f"ERRO: {e}")
            traceback.print_exc()
            job["status"] = "error"
        finally:
            work_queue.task_done()


def upload_worker():
    # uploads em sequência, separados do processamento de vídeo
    while True:
        req: UploadRequest = upload_queue.get()
        job = jobs[req.job_id]
        up = job["uploads"][req.file]
        up["state"] = "uploading"
        try:
            up["url"] = youtube.upload(
                OUTPUT / req.job_id / req.file, req.title, req.description,
                on_progress=lambda p: up.update(progress=p))
            up["state"] = "done"
            _log(job, f"Publicado no YouTube: {req.file} -> {up['url']}")
        except Exception as e:
            up.update(state="error", error=str(e))
            _log(job, f"ERRO no envio de {req.file}: {e}")
        finally:
            upload_queue.task_done()


threading.Thread(target=worker, daemon=True).start()
threading.Thread(target=upload_worker, daemon=True).start()


# --------------------------------------------------------------------------- cortes

@app.post("/api/jobs")
def create_job(req: JobRequest):
    if "youtu" not in req.url:
        raise HTTPException(400, "Envie um link do YouTube.")
    if req.auto_upload and not youtube.status().get("connected"):
        raise HTTPException(400, "Conecte sua conta do YouTube antes de ativar o envio automático.")
    job_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    jobs[job_id] = {"id": job_id, "url": req.url, "status": "queued",
                    "log": [f"Na fila (posição {work_queue.qsize() + 1})"], "clips": [], "uploads": {}}
    work_queue.put((job_id, req))
    return jobs[job_id]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job não encontrado")
    return jobs[job_id]


@app.get("/api/download/{job_id}/{name}")
def download(job_id: str, name: str):
    path = (OUTPUT / job_id / name).resolve()
    if OUTPUT.resolve() not in path.parents or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, filename=name, media_type="video/mp4")


def _busy_jobs() -> set[str]:
    # jobs que ainda estão processando ou enviando não podem ser apagados
    return {jid for jid, j in jobs.items()
            if j["status"] in ("queued", "running")
            or any(u["state"] in ("queued", "uploading") for u in j["uploads"].values())}


@app.get("/api/storage")
def storage():
    dirs = [d for d in OUTPUT.iterdir() if d.is_dir()]
    size = sum(f.stat().st_size for d in dirs for f in d.rglob("*") if f.is_file())
    return {"jobs": len(dirs), "bytes": size}


@app.delete("/api/output")
def delete_all():
    busy, deleted, failed = _busy_jobs(), 0, []
    for d in OUTPUT.iterdir():
        if not d.is_dir() or d.name in busy:
            continue
        try:
            shutil.rmtree(d)
            jobs.pop(d.name, None)
            deleted += 1
        except OSError:
            failed.append(d.name)  # arquivo aberto em outro programa
    return {"deleted": deleted, "skipped": len(busy), "failed": failed}


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
    job = jobs.get(req.job_id)
    if not job or not any(c["file"] == req.file for c in job["clips"]):
        raise HTTPException(404, "Corte não encontrado")
    if job["uploads"].get(req.file, {}).get("state") in ("queued", "uploading", "done"):
        raise HTTPException(400, "Esse corte já foi enviado ou está na fila.")
    if not youtube.status().get("connected"):
        raise HTTPException(400, "Conecte sua conta do YouTube primeiro.")
    _enqueue_upload(req)
    return job["uploads"][req.file]


app.mount("/output", StaticFiles(directory=OUTPUT), name="output")
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
