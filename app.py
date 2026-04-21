"""
app.py — GUI Streamlit para o YouTube Downloader
================================================

Para rodar:
    pip install -U streamlit "yt-dlp[default]"
    streamlit run app.py

Abre em http://localhost:8501

Pré-requisitos do sistema (ver sidebar "Status do ambiente"):
  • Deno: winget install DenoLand.Deno
  • ffmpeg: winget install Gyan.FFmpeg
  • Firefox logado no YouTube (para extração de cookies)
"""

from __future__ import annotations

import queue
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import streamlit as st

import youtube_downloader as core


# ================================================================
# Config básica da página
# ================================================================

st.set_page_config(
    page_title="YouTube Downloader",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ================================================================
# Session state
# ================================================================

def _init_state() -> None:
    defaults = {
        "single_info": None,
        "multi_infos": [],
        "playlist_info": None,
        "download_log": [],
        "is_downloading": False,
        "output_dir": str(Path.cwd() / "downloads"),
        "output_dir_input": str(Path.cwd() / "downloads"),
        "browser": "firefox",
        "cookies_upload": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()


# ================================================================
# Helpers
# ================================================================

QUALIDADES = [
    ("Auto (melhor disponível)", None),
    ("4K (2160p)", 2160),
    ("1440p",      1440),
    ("1080p",      1080),
    ("720p",       720),
    ("480p",       480),
    ("360p",       360),
]

# Mapa altura → label bonito
_RES_LABELS = {2160: "4K (2160p)", 1440: "1440p", 1080: "1080p",
               720: "720p", 480: "480p", 360: "360p", 240: "240p", 144: "144p"}

FORMATOS_AUDIO = ["mp3", "m4a", "opus", "wav", "flac", "aac"]
CONTAINERS     = ["mp4", "mkv", "webm"]
NAVEGADORES    = ["firefox", "chrome", "edge", "brave", "opera",
                  "vivaldi", "safari", "chromium"]

AUDIO_QUALITIES = [
    ("Auto (melhor disponível)", "0"),
    ("Alta (≈ 190 kbps)", "2"),
    ("Média (≈ 130 kbps)", "5"),
    ("Baixa (≈ 100 kbps)", "7"),
    ("128 kbps", "128K"),
    ("192 kbps", "192K"),
    ("256 kbps", "256K"),
    ("320 kbps", "320K"),
]


def _cookies_config() -> dict:
    """Retorna o dict com cookies_browser e cookies_file do session state."""
    cfg = {"cookies_browser": st.session_state.get("browser", "firefox"),
           "cookies_file": None}
    uploaded = st.session_state.get("cookies_upload")
    if uploaded is not None:
        tmp = Path(tempfile.gettempdir()) / "yt_cookies.txt"
        tmp.write_bytes(uploaded.getvalue())
        cfg["cookies_file"] = tmp
    return cfg


@st.cache_data(ttl=600, show_spinner=False)
def _extract_info_cached(url: str, browser: str, process_playlist: bool) -> dict:
    return core.extract_info(url, cookies_browser=browser,
                             process_playlist=process_playlist)


@st.cache_data(ttl=600, show_spinner=False)
def _extract_playlist_flat_cached(url: str, browser: str) -> dict:
    return core.extract_playlist_flat(url, cookies_browser=browser)


def _format_spec_for(quality_height: int | None, container: str) -> str:
    return core.format_spec_by_quality(quality_height, container)


def _ask_directory_popup() -> str | None:
    """Abre seletor nativo de pasta no Windows via PowerShell."""
    script = (
        "$shell = New-Object -ComObject Shell.Application; "
        "$flags = 0x0051; "
        "$folder = $shell.BrowseForFolder(0, 'Escolha a pasta de downloads', $flags, 17); "
        "if ($folder) { [Console]::WriteLine($folder.Self.Path) }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception:
        return None

    selected = (out.stdout or "").strip()
    return selected or None


# ================================================================
# SIDEBAR — Status & configuração global
# ================================================================

@st.cache_data(ttl=30, show_spinner=False)
def _env_status() -> dict:
    """Cache das verificações de ambiente para evitar subprocess a cada rerun."""
    return {
        "deno": core.deno_version(),
        "ffmpeg": core.ffmpeg_available(),
        "firefox": core.firefox_profile_exists(),
        "ytdlp": core.yt_dlp_version(),
    }


def render_sidebar() -> None:
    st.sidebar.title("⚙️ Configuração")

    # ---- Status ----
    st.sidebar.subheader("Status do ambiente")
    env = _env_status()
    deno_v = env["deno"]
    ff_ok = env["ffmpeg"]
    fx_ok = env["firefox"]

    st.sidebar.markdown(
        f"- **Deno:** {'✅ `' + deno_v + '`' if deno_v else '❌ não instalado'}\n"
        f"- **ffmpeg:** {'✅ OK' if ff_ok else '❌ não instalado'}\n"
        f"- **Firefox:** {'✅ detectado' if fx_ok else '⚠️ não detectado'}\n"
        f"- **yt-dlp:** `{env['ytdlp']}`"
    )

    if not deno_v:
        st.sidebar.error(
            "Deno é **obrigatório** para o YouTube. Instale no PowerShell:\n\n"
            "`winget install DenoLand.Deno`\n\n"
            "Depois feche e reabra o terminal."
        )
    if not ff_ok:
        st.sidebar.error("Instale ffmpeg: `winget install Gyan.FFmpeg`")
    if not fx_ok:
        st.sidebar.warning(
            "Firefox não detectado. Instale em firefox.com, logue no "
            "YouTube em aba privada, assista uns segundos de um vídeo e "
            "**não feche a aba** antes de baixar."
        )

    if st.sidebar.button("🔄 Atualizar yt-dlp"):
        with st.spinner("Atualizando..."):
            core.atualizar_yt_dlp(silent=True)
        st.cache_data.clear()
        st.rerun()

    st.sidebar.divider()

    # ---- Config geral ----
    st.sidebar.subheader("Opções gerais")
    if st.sidebar.button("📂 Escolher pasta"):
        selected_dir = _ask_directory_popup()
        if selected_dir:
            st.session_state["output_dir"] = selected_dir
            st.session_state["output_dir_input"] = selected_dir
            st.rerun()
        else:
            st.sidebar.info("Nenhuma pasta foi selecionada.")

    st.sidebar.text_input(
        "Pasta de saída",
        key="output_dir_input",
        help="Onde os arquivos baixados serão salvos.",
    )
    st.session_state["output_dir"] = st.session_state["output_dir_input"]

    st.session_state["browser"] = st.sidebar.selectbox(
        "Navegador (cookies)",
        NAVEGADORES,
        index=NAVEGADORES.index(st.session_state.get("browser", "firefox")),
        help="Firefox é o mais confiável em 2026.",
    )
    st.session_state["cookies_upload"] = st.sidebar.file_uploader(
        "...ou suba um cookies.txt",
        type=["txt"],
        help="Alternativa à extração do navegador.",
    )

    st.sidebar.divider()

    # ---- Guia ----
    with st.sidebar.expander("📖 Guia de uso — LEIA ANTES"):
        st.markdown(GUIA_MD)


GUIA_MD = """
### Por que preciso do Firefox?

O YouTube bloqueia downloads automatizados com o erro *"Sign in to confirm
you're not a bot"*. Para contornar, o yt-dlp precisa de cookies de uma sessão
real e logada. Em 2026 o **Chrome encripta seus cookies** de um jeito que
impede extração externa. **Use Firefox.**

### O ritual dos cookies (ordem importa!)

1. Abra o **Firefox** em janela privada (`Ctrl+Shift+P`).
2. Faça **login no YouTube** dentro dessa janela.
3. Abra um vídeo qualquer e deixe tocar **5–10 segundos**.
4. **NÃO feche a janela, não deslogue**.
5. Volte aqui e clique em **Analisar / Baixar**.
6. Só **DEPOIS** feche a janela privada.

Se fechar a janela ou deslogar antes, o YouTube rotaciona os cookies na hora.

### Por que preciso do Deno?

Desde nov/2025 o YouTube obriga execução de JavaScript para liberar URLs
dos vídeos. O Deno é o runtime JS que o yt-dlp usa para resolver esse
desafio. Sem ele, só dá para baixar thumbnails.

### Meu IP pode estar flagueado?

Se nada funciona mesmo com tudo certo, teste:
- **Dados móveis do celular** (IP diferente)
- Um **perfil antigo** do Firefox (com histórico, parece mais humano)

### Erros comuns

| Erro | Causa provável |
|------|---------------|
| `n challenge solving failed` | Deno não instalado |
| `Sign in to confirm you're not a bot` | Cookies rotacionados — refaça o ritual |
| `Requested format is not available` | Sem Deno, só vêm thumbnails |
| `HTTP Error 429` | Rate limit — espere uns minutos ou troque de IP |
"""


# ================================================================
# Progress display
# ================================================================

# Strips ANSI/VT100 colour codes that yt-dlp may embed in error messages.
# Matches both  \x1b[0;31m  (with ESC byte)  and  [0;31m  (ESC stripped by terminal).
_ANSI_RE = re.compile(r"(?:\x1b|\033)?\[[\d;]*[mKHJA-Za-z]")

_PP_LABELS: dict[str, str] = {
    "FFmpegMergerPP":            "Unindo vídeo e áudio",
    "FFmpegEmbedSubtitle":       "Embutindo legendas",
    "FFmpegSubtitlesConvertor":  "Convertendo legendas",
    "FFmpegExtractAudio":        "Extraindo áudio",
    "FFmpegMetadata":            "Adicionando metadados",
    "EmbedThumbnail":            "Embutindo capa",
    "FFmpegFixupM3u8":           "Corrigindo formato HLS",
    "FFmpegFixupM4a":            "Corrigindo formato M4A",
    "FFmpegFixupTimestamp":      "Corrigindo timestamps",
    "FFmpegFixupDuration":       "Corrigindo duração",
    "FFmpegFixupStretchedAudio": "Corrigindo áudio",
    "FFmpegCopyStream":          "Copiando stream",
    "FFmpegVideoConvertorPP":    "Convertendo vídeo",
    "FFmpegVideoRemuxerPP":      "Remuxando vídeo",
    "MoveFilesAfterDownload":    "Organizando arquivos",
    "FFmpegThumbnailsConvertor": "Convertendo thumbnail",
    "FFmpegSplitChapters":       "Dividindo por capítulos",
}


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


class StreamlitProgress:
    """Encapsula os placeholders do Streamlit para mostrar progresso."""

    def __init__(self, container: Any):
        self.container = container
        self.bar = container.progress(0.0)
        self.status = container.empty()
        self.log_display = container.empty()
        self._log: list[str] = []
        self._current_pp: str | None = None
        self._pp_start: float | None = None
        self._dl_start: float | None = None  # start of current file download

    def _push_log(self, msg: str) -> None:
        self._log.append(msg)
        self.log_display.markdown("\n\n".join(self._log[-12:]))

    def hook(self, d: dict) -> None:
        status = d.get("status")
        if status == "downloading":
            if self._dl_start is None:
                self._dl_start = time.monotonic()
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            frac = (done / total) if total else 0.0
            self.bar.progress(min(frac, 1.0))
            speed = core.format_bytes(d.get("speed") or 0) + "/s"
            eta = d.get("eta") or 0
            elapsed = _fmt_elapsed(time.monotonic() - self._dl_start)
            fn = Path(d.get("filename", "")).name
            self.status.markdown(
                f"⬇️ **Baixando:** `{fn}`  \n"
                f"{core.format_bytes(done)} / {core.format_bytes(total)}"
                f" • {speed} • ETA {core.format_duration(eta)} • ⏱ {elapsed}"
            )
        elif status == "finished":
            fn = Path(d.get("filename", "")).name
            elapsed_str = (
                f" — ⏱ {_fmt_elapsed(time.monotonic() - self._dl_start)}"
                if self._dl_start else ""
            )
            self._push_log(f"✅ **{fn}** baixado{elapsed_str}")
            self.bar.progress(1.0)
            self.status.empty()
            self._dl_start = None
        elif status == "error":
            fn = d.get("filename", "") or ""
            self._push_log(f"❌ Erro: `{Path(fn).name if fn else '?'}`")

    def postprocessor_hook(self, d: dict) -> None:
        pp = d.get("postprocessor", "")
        label = _PP_LABELS.get(pp)
        if label is None:
            return  # ignore internal/unlisted postprocessors silently
        status = d.get("status")

        if status == "started":
            self._current_pp = label
            self._pp_start = time.monotonic()
            fn = Path(d.get("info_dict", {}).get("filepath", "") or "").name
            text = f"⚙️ **{label}{'…' if not fn else f': {fn}…'}**"
            self.status.markdown(text)
            self.bar.progress(1.0)
        elif status == "finished":
            if self._current_pp:
                elapsed_str = (
                    f" — ⏱ {_fmt_elapsed(time.monotonic() - self._pp_start)}"
                    if self._pp_start else ""
                )
                self._push_log(f"✅ {self._current_pp} concluído{elapsed_str}")
                self._current_pp = None
                self._pp_start = None
            self.status.empty()

    def notify(self, msg: str) -> None:
        self.bar.progress(0.0)  # reset bar for new download phase
        self.status.markdown(msg)


def _run_download(urls: list[str], opts_without_hook: dict,
                  display_container: Any) -> tuple[int, str | None]:
    """Executa o download com barra de progresso. Retorna (retcode, erro)."""
    progress = StreamlitProgress(display_container)

    _ydl_errors: list[str] = []

    class _Logger:
        def debug(self, msg: str) -> None: pass
        def info(self, msg: str) -> None: pass
        def warning(self, msg: str) -> None: pass
        def error(self, msg: str) -> None:
            _ydl_errors.append(_ANSI_RE.sub("", msg))

    opts = {
        **opts_without_hook,
        "progress_hooks": [progress.hook],
        "postprocessor_hooks": [progress.postprocessor_hook],
        "_notify": progress.notify,
        "logger": _Logger(),
    }
    try:
        rc = core.download(urls, opts)
        err = _ydl_errors[-1] if _ydl_errors and rc != 0 else None
        return rc, err
    except Exception as e:
        return 1, _ANSI_RE.sub("", str(e))


# ================================================================
# Bloco de opções de download (reutilizado por todas as abas)
# ================================================================

def _format_hms(seconds: float | int | None) -> str:
    """Formata segundos como HH:MM:SS (sempre com 2 dígitos de hora)."""
    if seconds is None or seconds < 0:
        return "00:00:00"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _available_resolutions(info: dict | None) -> list[tuple[str, int | None]]:
    """Extrai resoluções de vídeo realmente disponíveis a partir de info['formats']."""
    if not info or "formats" not in info:
        return QUALIDADES  # fallback estático

    heights: set[int] = set()
    for fmt in info["formats"]:
        h = fmt.get("height")
        if h and isinstance(h, (int, float)) and h > 0 and fmt.get("vcodec", "none") != "none":
            heights.add(int(h))

    if not heights:
        return QUALIDADES

    # Ordena do maior para o menor, monta labels
    result: list[tuple[str, int | None]] = [("Auto (melhor disponível)", None)]
    for h in sorted(heights, reverse=True):
        label = _RES_LABELS.get(h, f"{h}p")
        result.append((label, h))
    return result


# Chaves em info["subtitles"] que NÃO são legendas de verdade
_NON_SUBTITLE_KEYS = {"live_chat"}


def _available_subtitles(info: dict | None) -> tuple[
    list[tuple[str, str]], list[tuple[str, str]]
]:
    """Retorna (manuais, automáticas) — cada uma como lista de (label, lang_code).

    Ordena pt-BR/pt/en com prioridade.  Usa o campo ``name`` do yt-dlp
    para exibir nomes legíveis (ex. "English (en)").
    """
    if not info:
        return [], []

    priority = {"pt-BR": 0, "pt": 1, "en": 2}

    def _sorted(langs: list[str]) -> list[str]:
        return sorted(langs, key=lambda l: (priority.get(l, 99), l))

    def _lang_label(section: dict, lang: str) -> str:
        entries = section.get(lang, [])
        if entries and isinstance(entries, list):
            name = entries[0].get("name") if isinstance(entries[0], dict) else None
            if name:
                return f"{name} ({lang})"
        return lang

    subs_dict = info.get("subtitles") or {}
    auto_dict = info.get("automatic_captions") or {}

    manual: list[tuple[str, str]] = []
    for lang in _sorted(list(subs_dict.keys())):
        if lang in _NON_SUBTITLE_KEYS:
            continue
        manual.append((_lang_label(subs_dict, lang), lang))

    manual_codes = {l for _, l in manual}
    auto: list[tuple[str, str]] = []
    for lang in _sorted(list(auto_dict.keys())):
        if lang not in manual_codes:
            auto.append((_lang_label(auto_dict, lang), lang))

    return manual, auto


def render_download_options(key_prefix: str,
                            duration_for_trim: float | None = None,
                            info: dict | None = None,
                            ) -> dict:
    """Renderiza os controles de qualidade/áudio/cortes. Retorna dict kwargs."""

    mode = st.radio(
        "Modo de download",
        ["Vídeo + áudio", "Apenas vídeo", "Apenas áudio", "Apenas legendas"],
        horizontal=True,
        key=f"{key_prefix}_mode",
    )

    col_a, col_b = st.columns(2)

    # ---- Coluna A: opções específicas por modo ----
    with col_a:
        # Defaults
        quality_h = None
        container = "mp4"
        audio_fmt = "mp3"
        audio_quality_val = "0"
        selected_sub_langs: list[str] = []

        if mode in ("Vídeo + áudio", "Apenas vídeo"):
            st.markdown("**🎞️ Vídeo**")

            available_q = _available_resolutions(info)
            quality_label = st.selectbox(
                "Qualidade máxima do vídeo",
                [lbl for lbl, _ in available_q],
                index=0,
                key=f"{key_prefix}_quality",
            )
            quality_h = dict(available_q)[quality_label]

            container = st.selectbox(
                "Formato do vídeo",
                CONTAINERS, index=0,
                key=f"{key_prefix}_container",
                help="Formato final do arquivo de vídeo (mp4 é o mais compatível).",
            )

            if quality_label == "Auto (melhor disponível)":
                st.caption("⚡ Qualidade automática — download mais rápido (sem re-encode).")

        if mode in ("Vídeo + áudio", "Apenas áudio"):
            st.markdown("**🔊 Áudio**")
            if mode == "Apenas áudio":
                audio_fmt = st.selectbox(
                    "Formato do áudio",
                    FORMATOS_AUDIO, index=0,
                    key=f"{key_prefix}_audio_fmt",
                )
            audio_quality_label = st.selectbox(
                "Qualidade do áudio",
                [lbl for lbl, _ in AUDIO_QUALITIES],
                index=0,
                key=f"{key_prefix}_audio_quality",
            )
            audio_quality_val = dict(AUDIO_QUALITIES)[audio_quality_label]

            if audio_quality_label == "Auto (melhor disponível)":
                st.caption("⚡ Qualidade automática — download mais rápido (sem re-encode do áudio).")

        # ---- Legendas: picker por idioma ----
        show_subs_picker = mode in ("Vídeo + áudio", "Apenas vídeo", "Apenas legendas")
        if show_subs_picker:
            st.markdown("**📝 Legendas**")
            manual_subs, auto_subs = _available_subtitles(info)

            if manual_subs or auto_subs:
                want_subs = st.checkbox(
                    "Baixar legendas",
                    value=(mode == "Apenas legendas"),
                    key=f"{key_prefix}_subs",
                    disabled=(mode == "Apenas legendas"),
                )

                if want_subs or mode == "Apenas legendas":
                    # Legendas do autor (manuais)
                    if manual_subs:
                        all_labels = [s[0] for s in manual_subs]
                        label_to_lang = {s[0]: s[1] for s in manual_subs}

                        # Default: pt-BR > pt > en (o primeiro encontrado)
                        default_labels: list[str] = []
                        for pref in ("pt-BR", "pt", "en"):
                            match = [lbl for lbl, lang in manual_subs if lang == pref]
                            if match:
                                default_labels = match
                                break

                        selected_labels = st.multiselect(
                            "Legendas do autor",
                            all_labels,
                            default=default_labels,
                            key=f"{key_prefix}_sub_langs",
                        )
                        selected_sub_langs = [label_to_lang[lbl]
                                              for lbl in selected_labels]
                    else:
                        st.caption("Este vídeo não possui legendas feitas pelo autor.")

                    # Legendas automáticas (toggle)
                    if auto_subs:
                        show_auto = st.checkbox(
                            "Incluir legendas geradas automaticamente",
                            value=(not manual_subs),
                            key=f"{key_prefix}_show_auto_subs",
                            help="Legendas geradas por IA — podem conter erros.",
                        )
                        if show_auto:
                            auto_labels = [s[0] for s in auto_subs]
                            auto_label_to_lang = {s[0]: s[1] for s in auto_subs}

                            # Default auto: pt-BR > pt > en
                            auto_default: list[str] = []
                            for pref in ("pt-BR", "pt", "en"):
                                match = [lbl for lbl, lang in auto_subs
                                         if lang == pref]
                                if match:
                                    auto_default = match
                                    break

                            selected_auto_labels = st.multiselect(
                                "Legendas automáticas",
                                auto_labels,
                                default=auto_default,
                                key=f"{key_prefix}_auto_sub_langs",
                            )
                            selected_sub_langs += [
                                auto_label_to_lang[lbl]
                                for lbl in selected_auto_labels
                            ]

                    if not selected_sub_langs:
                        st.warning("Selecione ao menos um idioma de legenda.")
            else:
                want_subs = st.checkbox(
                    "Baixar legendas",
                    value=(mode == "Apenas legendas"),
                    key=f"{key_prefix}_subs",
                    disabled=(mode == "Apenas legendas"),
                )
                if want_subs or mode == "Apenas legendas":
                    st.info("Nenhuma legenda encontrada para este vídeo. "
                            "Serão buscadas pt, pt-BR e en automaticamente.")
                    selected_sub_langs = ["pt", "pt-BR", "en"]

    # ---- Coluna B: cortes & extras ----
    with col_b:
        st.markdown("**✂️ Cortes & extras**")
        trim_enabled = st.checkbox(
            "Baixar apenas um trecho",
            value=False,
            key=f"{key_prefix}_trim_en",
        )

        trim_start = trim_end = None
        trim_error = False
        if trim_enabled:
            default_start = "00:00:00"
            default_end = _format_hms(duration_for_trim) if duration_for_trim else "00:00:00"

            c1, c2 = st.columns(2)
            with c1:
                start_txt = st.text_input(
                    "Início (HH:MM:SS)", value=default_start,
                    placeholder="00:00:00",
                    key=f"{key_prefix}_trim_start",
                )
            with c2:
                end_txt = st.text_input(
                    "Fim (HH:MM:SS)", value=default_end,
                    placeholder="00:05:30",
                    key=f"{key_prefix}_trim_end",
                )

            start_str = start_txt.strip() if start_txt.strip() else "00:00:00"
            end_str = end_txt.strip() if end_txt.strip() else None

            try:
                trim_start = core.parse_time_to_seconds(start_str)
                if end_str is None:
                    trim_end = None
                else:
                    trim_end = core.parse_time_to_seconds(end_str)
                if trim_end is not None and trim_start >= trim_end:
                    st.error("O tempo de início deve ser menor que o de fim.")
                    trim_error = True
            except ValueError as e:
                st.error(f"Tempo inválido: {e}. Use o formato HH:MM:SS (ex: 01:30:00).")
                trim_error = True

            if mode not in ("Apenas áudio", "Apenas legendas"):
                keyframes = st.checkbox(
                    "Cortes precisos (re-encode)",
                    value=True,
                    key=f"{key_prefix}_kf",
                    help="Mais lento, mas o corte é exato no frame pedido.",
                )
            else:
                keyframes = False
        else:
            keyframes = False

        if mode != "Apenas legendas":
            embed_thumb = st.checkbox(
                "Embutir thumbnail no arquivo", value=False,
                key=f"{key_prefix}_thumb",
            )
        else:
            embed_thumb = False

    # Monta dict de kwargs para build_options
    audio_only = (mode == "Apenas áudio")
    subtitles_only = (mode == "Apenas legendas")
    has_subs = bool(selected_sub_langs)

    if mode == "Apenas vídeo":
        if quality_h:
            format_spec = f"bestvideo[height<={quality_h}]/bestvideo"
        else:
            format_spec = "bestvideo"
    else:
        format_spec = _format_spec_for(quality_h, container)

    result: dict[str, Any] = {
        "format_spec": format_spec,
        "merge_format": container,
        "audio_only": audio_only,
        "audio_format": audio_fmt,
        "audio_quality": audio_quality_val,
        "trim_start": trim_start if not trim_error else None,
        "trim_end": trim_end if not trim_error else None,
        "force_keyframes_at_cuts": keyframes,
        "embed_thumbnail": embed_thumb,
        "write_subtitles": has_subs or subtitles_only,
        "subtitles_only": subtitles_only,
        "_trim_error": trim_error,
    }
    if selected_sub_langs:
        result["subtitles_langs"] = selected_sub_langs
    return result


# ================================================================
# Preview de vídeo
# ================================================================

def render_video_preview(info: dict) -> None:
    col1, col2 = st.columns([1, 2])
    with col1:
        thumb = info.get("thumbnail")
        if thumb:
            st.image(thumb, width="stretch")
    with col2:
        st.markdown(f"### {info.get('title', '(sem título)')}")
        uploader = info.get("uploader") or info.get("channel") or "?"
        duration = core.format_duration(info.get("duration"))
        views = info.get("view_count")
        views_s = f"{views:,}".replace(",", ".") if views else "?"
        st.markdown(
            f"- **Canal:** {uploader}\n"
            f"- **Duração:** {duration}\n"
            f"- **Visualizações:** {views_s}\n"
            f"- **ID:** `{info.get('id', '?')}`"
        )
        if info.get("description"):
            with st.expander("Descrição"):
                st.caption(info["description"][:2000])


# ================================================================
# TAB 1 — Link único
# ================================================================

def tab_single() -> None:
    st.markdown("Cole a URL de **um vídeo** do YouTube.")

    col_url, col_btn = st.columns([4, 1])
    with col_url:
        url = st.text_input("URL", key="single_url",
                            placeholder="https://youtu.be/...")
    with col_btn:
        st.write("")  # espaçador
        st.write("")
        if st.button("🔍 Analisar", width="stretch",
                     key="single_analyze"):
            if url.strip():
                with st.spinner("Buscando informações (pode levar alguns segundos)..."):
                    try:
                        info = _extract_info_cached(
                            url.strip(),
                            st.session_state["browser"],
                            process_playlist=False,
                        )
                        st.session_state.single_info = info
                    except Exception as e:
                        st.error(f"Não consegui analisar: {e}")
                        st.session_state.single_info = None

    info = st.session_state.single_info
    if not info:
        st.info("Cole uma URL e clique em **Analisar** para ver o preview.")
        return

    render_video_preview(info)
    st.divider()

    opts_kwargs = render_download_options(
        "single",
        duration_for_trim=info.get("duration"),
        info=info,
    )

    if st.button("⬇️  Baixar", type="primary", key="single_download",
                 disabled=st.session_state.is_downloading):
        _dispatch_download([info.get("webpage_url") or url.strip()],
                           opts_kwargs, playlist=False)


# ================================================================
# TAB 2 — Múltiplos links
# ================================================================

def tab_multi() -> None:
    st.markdown("Cole **várias URLs** (uma por linha) de vídeos individuais.")

    urls_txt = st.text_area(
        "URLs (uma por linha)",
        height=160,
        key="multi_urls_txt",
        placeholder="https://youtu.be/AAA\nhttps://youtu.be/BBB\nhttps://youtu.be/CCC",
    )
    urls = [u.strip() for u in urls_txt.splitlines() if u.strip()]

    if st.button("🔍 Analisar todos", key="multi_analyze",
                 disabled=not urls):
        infos: list[dict] = []
        progress = st.progress(0.0)
        for i, u in enumerate(urls, 1):
            try:
                infos.append(_extract_info_cached(
                    u, st.session_state["browser"], process_playlist=False))
            except Exception as e:
                st.warning(f"Falha em `{u}`: {e}")
            progress.progress(i / len(urls))
        st.session_state.multi_infos = infos
        progress.empty()

    infos = st.session_state.multi_infos
    if infos:
        st.success(f"{len(infos)} vídeo(s) analisados")
        with st.expander("Ver lista", expanded=True):
            for i, info in enumerate(infos, 1):
                c1, c2 = st.columns([1, 5])
                with c1:
                    if info.get("thumbnail"):
                        st.image(info["thumbnail"], width=120)
                with c2:
                    st.markdown(
                        f"**{i}. {info.get('title', '?')}**  \n"
                        f"{info.get('uploader', '?')} · "
                        f"{core.format_duration(info.get('duration'))}"
                    )

        st.divider()
        # Para múltiplos vídeos, usa o primeiro para resoluções/legendas disponíveis
        opts_kwargs = render_download_options(
            "multi",
            info=infos[0] if infos else None,
        )

        if st.button("⬇️  Baixar todos", type="primary", key="multi_download",
                     disabled=st.session_state.is_downloading):
            urls_to_dl = [i.get("webpage_url") for i in infos if i.get("webpage_url")]
            _dispatch_download(urls_to_dl, opts_kwargs, playlist=False)
    else:
        st.info("Cole URLs acima e clique em **Analisar todos**.")


# ================================================================
# TAB 3 — Playlist
# ================================================================

def tab_playlist() -> None:
    st.markdown("Cole a URL de uma **playlist** do YouTube.")

    col_url, col_btn = st.columns([4, 1])
    with col_url:
        pl_url = st.text_input("URL da playlist", key="pl_url",
                               placeholder="https://youtube.com/playlist?list=...")
    with col_btn:
        st.write("")
        st.write("")
        if st.button("🔍 Listar", key="pl_analyze", width="stretch"):
            if pl_url.strip():
                with st.spinner("Buscando playlist..."):
                    try:
                        info = _extract_playlist_flat_cached(
                            pl_url.strip(), st.session_state["browser"])
                        st.session_state.playlist_info = info
                    except Exception as e:
                        st.error(f"Não consegui listar: {e}")
                        st.session_state.playlist_info = None

    info = st.session_state.playlist_info
    if not info:
        st.info("Cole uma URL de playlist e clique em **Listar**.")
        return

    entries: list[dict] = info.get("entries", []) or []
    st.markdown(f"### 📁 {info.get('title', 'Playlist')}")
    st.caption(f"{len(entries)} vídeo(s) · Canal: {info.get('uploader', '?')}")

    # Seleção
    col_sel1, col_sel2, _ = st.columns([1, 1, 3])
    with col_sel1:
        if st.button("✅ Marcar tudo", key="pl_all"):
            for i in range(len(entries)):
                st.session_state[f"pl_chk_{i}"] = True
    with col_sel2:
        if st.button("⬜ Desmarcar tudo", key="pl_none"):
            for i in range(len(entries)):
                st.session_state[f"pl_chk_{i}"] = False

    # Lista com checkboxes
    selected_indices: list[int] = []
    with st.container(height=400):
        for i, entry in enumerate(entries):
            c1, c2 = st.columns([1, 10])
            with c1:
                checked = st.checkbox(
                    "", value=st.session_state.get(f"pl_chk_{i}", True),
                    key=f"pl_chk_{i}", label_visibility="collapsed",
                )
            with c2:
                title = entry.get("title", "(sem título)")
                dur = core.format_duration(entry.get("duration"))
                st.markdown(f"**{i+1}.** {title}  \n<small>⏱ {dur}</small>",
                            unsafe_allow_html=True)
            if checked:
                selected_indices.append(i + 1)  # yt-dlp: 1-indexed

    st.caption(f"Selecionados: **{len(selected_indices)}** de {len(entries)}")

    st.divider()
    opts_kwargs = render_download_options("pl")

    if st.button(f"⬇️  Baixar {len(selected_indices)} selecionado(s)",
                 type="primary", key="pl_download",
                 disabled=(not selected_indices or
                           st.session_state.is_downloading)):
        # Converte lista de ints para spec do yt-dlp ("1,3,5-7")
        items_spec = _ints_to_spec(selected_indices)
        opts_kwargs["playlist_items"] = items_spec
        _dispatch_download(
            [info.get("webpage_url") or pl_url.strip()],
            opts_kwargs,
            playlist=True,
        )


def _ints_to_spec(ints: list[int]) -> str:
    """[1,2,3,5,7,8,9] -> '1-3,5,7-9'."""
    if not ints:
        return ""
    ints = sorted(set(ints))
    ranges: list[str] = []
    start = prev = ints[0]
    for n in ints[1:]:
        if n == prev + 1:
            prev = n
        else:
            ranges.append(f"{start}" if start == prev else f"{start}-{prev}")
            start = prev = n
    ranges.append(f"{start}" if start == prev else f"{start}-{prev}")
    return ",".join(ranges)


# ================================================================
# Dispatch do download
# ================================================================

def _dispatch_download(urls: list[str], opts_kwargs: dict,
                       playlist: bool) -> None:
    # Validação de pasta de saída
    output_dir = st.session_state["output_dir"].strip()
    if not output_dir:
        st.error("❌ Pasta de saída não pode ser vazia.")
        return
    try:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as e:
        st.error(f"❌ Pasta de saída inválida: {e}")
        return

    # Remove flag interna de trim error
    trim_error = opts_kwargs.pop("_trim_error", False)
    if trim_error:
        st.error("❌ Corrija os tempos de corte antes de baixar.")
        return

    cookies = _cookies_config()
    opts = core.build_options(
        output_dir=output_path,
        playlist=playlist,
        cookies_browser=cookies["cookies_browser"],
        cookies_file=cookies["cookies_file"],
        quiet=True,
        **opts_kwargs,
    )

    st.session_state.is_downloading = True
    display_area = st.container(border=True)
    display_area.markdown("### ⏳ Baixando...")
    progress_slot = display_area.container()

    _t0 = time.monotonic()
    try:
        rc, err = _run_download(urls, opts, progress_slot)
    except Exception as e:
        rc, err = 1, str(e)
    finally:
        st.session_state.is_downloading = False

    total_elapsed = _fmt_elapsed(time.monotonic() - _t0)
    if rc == 0:
        display_area.success(
            f"✅ Download concluído em: `{st.session_state['output_dir']}` "
            f"— ⏱ tempo total: **{total_elapsed}**"
        )
        st.balloons()
    else:
        err_msg = err or f"retcode={rc}"
        display_area.error(f"❌ Falhou após {total_elapsed}: {err_msg}")
        _is_file_lock = any(k in (err_msg or "") for k in
                            ("WinError 32", "sendo usado", "being used by another process",
                             "Unable to rename"))
        if _is_file_lock:
            display_area.warning(
                "🛡️ **Windows Defender / Indexação** está bloqueando a renomeação do arquivo.\n\n"
                "**Solução permanente:** adicione a pasta de downloads às exclusões do Defender:\n"
                "Segurança do Windows → Proteção contra vírus e ameaças → "
                "Configurações de proteção → Exclusões → Adicionar pasta."
            )
        else:
            display_area.info(
                "Cheque se o Deno está instalado, se os cookies do Firefox "
                "estão válidos (refaça o ritual) e se o yt-dlp está atualizado."
            )


# ================================================================
# MAIN
# ================================================================

def main() -> None:
    render_sidebar()

    st.title("🎬 YouTube Downloader")
    st.caption(
        "Baixe vídeos, playlists e áudios do YouTube com controle fino de "
        "qualidade, formato e cortes. Use o **Firefox logado** no YouTube."
    )

    # Aviso crítico no topo se faltar Deno
    if not core.check_deno():
        st.error(
            "🚨 **Deno não encontrado.** Sem ele, o YouTube só libera "
            "thumbnails. Instale pelo PowerShell: "
            "`winget install DenoLand.Deno` e reabra o terminal."
        )

    tab1, tab2, tab3 = st.tabs([
        "🔗 Link único",
        "📋 Múltiplos links",
        "📁 Playlist",
    ])

    with tab1:
        tab_single()
    with tab2:
        tab_multi()
    with tab3:
        tab_playlist()


if __name__ == "__main__":
    main()
