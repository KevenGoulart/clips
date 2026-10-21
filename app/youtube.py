"""Envio dos vídeos para o YouTube via YouTube Data API v3 (OAuth)."""
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT_SECRET = ROOT / "client_secret.json"
TOKEN_FILE = ROOT / "youtube_token.json"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",  # só para mostrar o nome do canal
]

_lock = threading.Lock()


def _find_client_secret() -> Path | None:
    if CLIENT_SECRET.exists():
        return CLIENT_SECRET
    # o Google baixa como client_secret_XXXX.apps.googleusercontent.com.json
    return next(ROOT.glob("client_secret*.json"), None)


def _load_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN_FILE.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception:
            # no modo "Teste" do Google o login expira em 7 dias: precisa conectar de novo
            TOKEN_FILE.unlink(missing_ok=True)
    return None


def login():
    """Abre o navegador para o usuário autorizar o app (bloqueia até concluir)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    secret = _find_client_secret()
    if not secret:
        raise RuntimeError("Arquivo client_secret.json não encontrado na pasta do projeto.")
    with _lock:
        flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True, prompt="consent",
                                      success_message="Conectado! Pode fechar esta aba e voltar ao app.")
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")


def logout():
    TOKEN_FILE.unlink(missing_ok=True)


def _service(creds=None):
    from googleapiclient.discovery import build

    creds = creds or _load_credentials()
    if not creds:
        raise RuntimeError("YouTube não conectado. Clique em 'Conectar YouTube'.")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def status() -> dict:
    if not _find_client_secret():
        return {"configured": False, "connected": False}
    creds = _load_credentials()
    if not creds:
        return {"configured": True, "connected": False}
    try:
        items = _service(creds).channels().list(part="snippet", mine=True).execute().get("items", [])
        channel = items[0]["snippet"]["title"] if items else "(conta sem canal)"
    except Exception as e:
        return {"configured": True, "connected": False, "error": str(e)}
    return {"configured": True, "connected": True, "channel": channel}


def _clean_title(title: str) -> str:
    title = re.sub(r"[<>]", "", title).strip() or "Documentário"
    return title[:100]  # limite do YouTube


def upload(path: Path, title: str, description: str, on_progress=None) -> str:
    """Envia o vídeo como público e retorna a URL."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {"title": _clean_title(title), "description": description[:5000], "categoryId": "27"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = _service().videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    try:
        while response is None:
            prog, response = request.next_chunk()
            if prog and on_progress:
                on_progress(int(prog.progress() * 100))
    except HttpError as e:
        reason = e.reason or str(e)
        if "quota" in reason.lower():
            reason = "Cota diária da API do YouTube esgotada. Tente amanhã."
        elif "uploadLimitExceeded" in str(e):
            reason = "Limite de uploads do canal atingido. Tente mais tarde."
        raise RuntimeError(reason) from None
    if on_progress:
        on_progress(100)
    return f"https://youtu.be/{response['id']}"


def set_thumbnail(video_url: str, image: Path):
    """Troca a miniatura do vídeo já enviado. Exige canal verificado por telefone."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    video_id = video_url.rstrip("/").rsplit("/", 1)[-1]
    try:
        _service().thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(image), mimetype="image/jpeg")).execute()
    except HttpError as e:
        if e.resp.status == 403:
            raise RuntimeError("o canal precisa estar verificado para usar miniaturas personalizadas "
                               "(youtube.com/verify)") from None
        raise RuntimeError(e.reason or str(e)) from None
