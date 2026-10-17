# Clips IA

Cola um link do YouTube → a IA acha os melhores momentos → gera cortes 9:16 (1080x1920) de até 30s ou 1min, com título e legendas animadas.

## Como usar
Dê dois cliques em `iniciar.bat`. Ele abre o Ollama (se não estiver aberto), sobe o servidor e abre http://127.0.0.1:8000.
Os cortes ficam em `output/<id-do-job>/corte_XX.mp4`.

## Como funciona (`app/pipeline.py`)
1. **Download** — yt-dlp (até 1080p).
2. **Transcrição** — faster-whisper `large-v3-turbo` na GPU, com tempo por palavra.
3. **Momentos** — Ollama (`qwen3:14b`) lê a transcrição em partes e devolve início/fim, título, legenda e nota,
   no idioma detectado do vídeo; os limites são ajustados ao fim das frases.
4. **Enquadramento** (`app/framing.py`) — detecta trocas de câmera, os rostos de cada cena e quem está falando
   (movimento da boca); o corte 9:16 segue o falante. Cenas sem rosto usam o vídeo inteiro com fundo desfocado.
5. **Render** — frames montados em Python e codificados pelo ffmpeg (NVENC quando disponível), com legenda de 3 palavras
   e destaque na palavra atual.

## Publicar no YouTube (configuração única)
1. Acesse https://console.cloud.google.com e crie um projeto (ex: "Clips IA").
2. Em **APIs e serviços > Biblioteca**, ative a **YouTube Data API v3**.
3. Em **APIs e serviços > Tela de consentimento OAuth** (Google Auth Platform): tipo **Externo**, preencha nome e e-mail,
   e em **Público-alvo > Usuários de teste** adicione o e-mail da conta dona do canal.
4. Em **Credenciais > Criar credenciais > ID do cliente OAuth**, tipo **App para computador**. Baixe o JSON.
5. Salve o arquivo na pasta do projeto como `client_secret.json` (ou mantenha o nome `client_secret_....json`).
6. Abra o app e clique em **Conectar YouTube**; autorize no navegador (aviso "app não verificado" → Avançado → Continuar).

Depois disso, cada corte tem **Publicar no YouTube** (envio imediato como público, com título e descrição editáveis),
e o formulário tem a opção **Publicar automaticamente** ao terminar.

Limitações:
- Projetos não auditados pelo Google só sobem vídeos **privados**; publique manualmente no YouTube Studio ou peça a auditoria.
- No modo "Teste" o login expira em 7 dias; é só clicar em Conectar de novo.
- Cota padrão de 10.000 unidades/dia: poucos uploads por dia.

## Configuração (variáveis de ambiente)
- `OLLAMA_MODEL` (padrão `qwen3:14b`; `qwen2.5:7b` é mais rápido e mais simples)
- `WHISPER_MODEL` (padrão `large-v3-turbo`; use `small` se faltar VRAM)
- `OLLAMA_URL` (padrão `http://127.0.0.1:11434`)

## Reinstalar do zero
```
winget install Python.Python.3.12 Gyan.FFmpeg Ollama.Ollama
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
ollama pull qwen3:14b
```
