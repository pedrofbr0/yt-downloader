# YouTube Downloader — CLI + GUI (Streamlit)

Downloader robusto do YouTube com GUI web em Streamlit, baseado em `yt-dlp`.

## 📦 Estrutura

```md
youtube_downloader.py   ← núcleo (compartilhado entre CLI e GUI)
baixar_youtube.py       ← CLI (linha de comando)
app.py                  ← GUI Streamlit
requirements.txt
```

## 🚀 Setup (Windows — execute no PowerShell)

### 1. Instale os pré-requisitos do sistema

```powershell
# Deno (runtime JavaScript — OBRIGATÓRIO desde nov/2025)
winget install DenoLand.Deno

# ffmpeg (para merge e conversão de áudio)
winget install Gyan.FFmpeg

# Firefox (se ainda não tiver)
winget install Mozilla.Firefox
```

**Depois de instalar o Deno, FECHE e REABRA o PowerShell** para o PATH ser recarregado.

Verifique:

```powershell
deno --version
ffmpeg -version
```

### 2. Instale as dependências Python

```powershell
pip install -U -r requirements.txt
```

### 3. O ritual dos cookies do YouTube (CRÍTICO)

O YouTube bloqueia downloads com *"Sign in to confirm you're not a bot"*. Para contornar:

1. Abra o **Firefox** em janela **privada** (`Ctrl+Shift+P`)
2. Faça **login no YouTube** nessa janela
3. Abra um vídeo qualquer, deixe tocar **5–10 segundos**
4. **NÃO feche a janela, NÃO deslogue**
5. Rode o downloader (CLI ou GUI)
6. **Depois** do download, aí sim feche a janela

Se fechar a janela/deslogar antes, o YouTube invalida os cookies na hora.

---

## 🖱️ Uso — GUI (Streamlit)

```powershell
streamlit run app.py
```

Abre automaticamente em `http://localhost:8501`.

Funcionalidades:

- 🔗 **Link único** — preview com thumbnail, duração, views → escolha qualidade → baixa
- 📋 **Múltiplos links** — cole várias URLs, analise todas → baixa em lote
- 📁 **Playlist** — lista todos os vídeos, marque quais quer → baixa só os selecionados
- Seleção de qualidade: Auto / 4K / 1440p / 1080p / 720p / 480p / 360p
- Modo: vídeo+áudio, só vídeo, só áudio (MP3/M4A/Opus/WAV/FLAC/AAC)
- Cortes precisos (trim) por timestamp
- Embed de thumbnail
- Download de legendas
- Status do ambiente e orientações na sidebar

---

## ⌨️ Uso — CLI

### Exemplos rápidos

```powershell
# Um vídeo
python baixar_youtube.py https://youtu.be/D0UuxSLRGH4

# Múltiplos vídeos
python baixar_youtube.py https://youtu.be/AAA https://youtu.be/BBB

# Playlist inteira
python baixar_youtube.py "https://youtube.com/playlist?list=PLxxx"

# Itens específicos de uma playlist
python baixar_youtube.py "https://youtube.com/playlist?list=PLxxx" --items "1,3,5-8"

# Forçar qualidade 1080p
python baixar_youtube.py URL --quality 1080

# Só áudio em MP3
python baixar_youtube.py URL --audio-only --audio-format mp3

# Cortar trecho (1:30 até 3:45)
python baixar_youtube.py URL --trim 1:30 3:45

# A partir de arquivo com URLs
python baixar_youtube.py --urls-file links.txt

# Pasta de saída custom + legendas
python baixar_youtube.py URL -o "D:\Videos" --subs

# Ajuda completa
python baixar_youtube.py --help
```

### Todas as opções

```md
-q, --quality {auto,2160,1440,1080,720,480,360}
    --container {mp4,mkv,webm}
-f, --format SPEC              format spec custom do yt-dlp
    --audio-only
    --audio-format {mp3,m4a,opus,wav,flac,aac}
    --audio-quality 0..9       0 = melhor
    --no-playlist              baixa só o vídeo, ignora playlist
    --items "1,3,5-7"          itens da playlist
    --trim INICIO FIM          ex: --trim 1:30 3:45  ou  --trim 90 end
    --no-keyframes             trim sem re-encode (mais rápido, menos preciso)
    --subs
    --embed-thumbnail
    --cookies-file PATH
    --browser {firefox,chrome,edge,brave,opera,vivaldi,safari,chromium}
    --no-update
-v, --verbose
```

---

## 🛠️ Troubleshooting

| Erro | Causa | Solução |
|------|-------|---------|
| `n challenge solving failed` | Deno não instalado ou fora do PATH | `winget install DenoLand.Deno` e reabrir terminal |
| `Sign in to confirm you're not a bot` | Cookies rotacionados | Refaça o ritual do Firefox (janela privada, não deslogue) |
| `Requested format is not available` | Sem JS runtime, só thumbnails disponíveis | Instalar Deno |
| `HTTP Error 429` | Rate limit (IP flagueado) | Tentar outro IP (4G do celular), aguardar alguns minutos |
| `ffmpeg not found` | ffmpeg fora do PATH | `winget install Gyan.FFmpeg` |

---

## 🔒 Segurança & privacidade

- Os cookies **nunca** saem da sua máquina — são lidos do SQLite local do Firefox.
- **Nunca** instale extensões tipo *"Get cookies.txt"* — várias foram flagradas como malware que exfiltra sessões bancárias e tokens.
- Se quiser usar um arquivo `cookies.txt` (opção no sidebar do app), gere você mesmo via uma extensão **confiável e open-source** dentro de uma janela privada dedicada e apague depois do uso.
