# Documentários IA

Dê um tema (a história de um país, uma figura histórica, um crime famoso, uma curiosidade, um assunto de ciência) ou cole
um roteiro → a IA escreve a narração, busca fotos e vídeos de cada cena, narra com voz natural e anima tudo em um vídeo
longo (16:9 ou 9:16) com cartelas de capítulo, datas, citações, números, legendas e trilha. Tudo roda no seu PC.

## Como usar
Dê dois cliques em `iniciar.bat`. Ele abre o Ollama (se não estiver aberto), sobe o servidor e abre http://127.0.0.1:8000.

1. **Novo documentário**: escreva o tema (ou cole o roteiro), escolha a duração, a voz e o formato.
2. Com "revisar o roteiro" marcado, o app para depois de escrever: **leia e corrija o texto** (a IA local pode errar
   fatos, datas e nomes) e clique em **Produzir vídeo**.
3. O vídeo fica em `output/<projeto>/documentario.mp4`, com `creditos.txt` (autor e licença de cada imagem).
   Se algo falhar no meio, **Retomar produção** continua de onde parou.

### Formato do roteiro
```
# Título do vídeo

Abertura: parágrafos de narração separados por linha em branco.

## Nome do capítulo
Mais parágrafos...

[clipe: animacao.mp4]
```
Cada `##` vira uma cartela de capítulo (e um capítulo na descrição do YouTube). Uma linha `[clipe: arquivo.mp4]` insere,
naquele ponto, um vídeo pronto da pasta `clipes/` (por exemplo, uma animação exportada do Claude Motion).

### Pastas
- `vozes/`: `.wav` de ~10 s de alguém falando (limpo, sem música). O narrador imita essa voz. Vazio = voz padrão.
- `musica/`: trilhas de fundo; uma é sorteada por vídeo e abaixa sozinha quando o narrador fala.
- `clipes/`: vídeos prontos para citar no roteiro com `[clipe: ...]`.

## Como funciona
1. **Roteiro** (`app/research.py` + `app/script.py`): busca o artigo da Wikipédia sobre o tema (no idioma do vídeo e
   em inglês) e o entrega como fonte ao Ollama (`qwen3:14b`), que monta um esboço com capítulos e escreve cada capítulo
   (~150 palavras por minuto de vídeo) usando só os fatos da fonte. Cada capítulo vê o texto dos anteriores e os pontos
   dos seguintes, para não repetir nem antecipar, e passa por uma revisão de português (concordância, palavras em
   inglês, siglas obscuras). Sem a fonte, o modelo local inventa muito; mesmo
   com ela ainda erra detalhes, por isso a revisão do roteiro vem ligada por padrão.
2. **Cenas**: o roteiro é dividido em cenas de 1 a 3 frases. Para cada capítulo, a IA escolhe o visual de cada cena:
   uma busca de imagem em inglês, a fonte (acervo histórico ou banco de imagens) e, quando cabe, um destaque animado
   (data, citação, número, lista) ou uma tarja com nome de lugar/pessoa.
3. **Mídia** (`app/media.py`): Wikimedia Commons (fotos históricas, mapas, retratos; a IA confere se cada resultado
   mostra mesmo o assunto) e Pexels/Pixabay (vídeos e fotos de banco). Cenas longas ganham várias tomadas (~6 s cada).
4. **Narração** (`app/voice.py` + `app/tts_worker.py`): Chatterbox Multilingual V3 na GPU, com o fine-tune pt-BR da
   Resemble AI para português. Frases seguidas são narradas juntas; cada trecho fica salvo, então editar o roteiro só
   renarra o que mudou. Cada trecho é conferido com o Whisper (large-v3-turbo): se a fala não bate com o texto
   (palavra engolida, repetição, som inventado no fim), é gerado de novo. O tempo das palavras ouvidas sincroniza as
   legendas e os cortes entre as frases.
5. **Animação** (`motion/`, Remotion): Ken Burns nas fotos, dissolve entre cenas, cartelas, datas, citações palavra por
   palavra, números que contam, listas, tarjas e legendas com a palavra atual destacada. O render é feito em partes de
   2 min (retomáveis) e a narração é mixada com a trilha (ffmpeg, -16 LUFS).

Para mexer no visual, rode `npm run studio` dentro de `motion/` e edite `motion/src/` com a prévia ao vivo.

## Configuração
- Chaves gratuitas do [Pexels](https://www.pexels.com/api/) e do [Pixabay](https://pixabay.com/api/docs/): cole na
  tela (**Configurações**) ou use as variáveis `PEXELS_API_KEY` / `PIXABAY_API_KEY`. Sem elas, só o Wikimedia Commons.
  O Pexels permite 200 buscas por hora; as buscas ficam em cache no projeto, então retomar não gasta cota.
- `OLLAMA_MODEL` (padrão `qwen3:14b`; `qwen2.5:7b` é mais rápido e mais simples), `OLLAMA_URL`.

Tempo aproximado de um vídeo de 10 min numa RTX 3050: roteiro 5–10 min, mídia 5–10 min, narração ~15 min, render 20–40 min.

## Publicar no YouTube (configuração única)
1. Acesse https://console.cloud.google.com e crie um projeto.
2. Em **APIs e serviços > Biblioteca**, ative a **YouTube Data API v3**.
3. Em **APIs e serviços > Tela de consentimento OAuth** (Google Auth Platform): tipo **Externo**, preencha nome e e-mail,
   e em **Público-alvo > Usuários de teste** adicione o e-mail da conta dona do canal.
4. Em **Credenciais > Criar credenciais > ID do cliente OAuth**, tipo **App para computador**. Baixe o JSON.
5. Salve o arquivo na pasta do projeto como `client_secret.json` (ou mantenha o nome `client_secret_....json`).
6. Abra o app e clique em **Conectar YouTube**; autorize no navegador (aviso "app não verificado" → Avançado → Continuar).

O vídeo pronto tem **Publicar no YouTube** (título e descrição editáveis; a descrição já vem com capítulos e créditos),
e o formulário tem a opção de publicar automaticamente ao terminar.

Limitações:
- Projetos não auditados pelo Google só sobem vídeos **privados**; publique manualmente no YouTube Studio ou peça a auditoria.
- No modo "Teste" o login expira em 7 dias; é só clicar em Conectar de novo.
- Cota padrão de 10.000 unidades/dia: poucos uploads por dia.

## Direitos de imagem
Commons: cada arquivo tem sua licença (domínio público, CC BY, CC BY-SA...); as que exigem atribuição estão em
`creditos.txt` e na descrição do vídeo. Pexels e Pixabay permitem uso comercial sem atribuição. Confira antes de
monetizar, principalmente fotos recentes de pessoas.

## Reinstalar do zero
```
winget install Python.Python.3.12 Gyan.FFmpeg Ollama.Ollama OpenJS.NodeJS.LTS
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
ollama pull qwen3:14b

python -m venv .venv-tts
.venv-tts\Scripts\pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
.venv-tts\Scripts\pip install -r requirements-tts.txt
.venv-tts\Scripts\pip install --no-deps "chatterbox-tts @ git+https://github.com/resemble-ai/chatterbox.git@5de7a54aa4"

cd motion && npm install
```
Os modelos do Chatterbox (~4 GB) e do Whisper (~1,6 GB) são baixados na primeira narração.

Remotion: gratuito para pessoas físicas e empresas de até 3 funcionários (veja https://remotion.dev/license).
