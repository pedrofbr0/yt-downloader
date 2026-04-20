"""
youtube_downloader.py — Núcleo reutilizável do downloader
==========================================================

Módulo standalone (sem Streamlit) usado pela CLI (`baixar_youtube.py`)
e pela GUI (`app.py`). Expõe:

  - check_deno(), deno_version(), yt_dlp_version(), ffmpeg_available(),
    firefox_profile_exists()               → verificação de pré-requisitos
  - build_options(...)                     → monta o dict do yt-dlp
  - extract_info(...)                      → metadata de um vídeo
  - extract_playlist_flat(...)             → lista rápida de playlist
  - download(urls, opts)                   → dispara o download
  - parse_time_to_seconds(...)             → "1:23:45" → 5025.0
  - format_duration(seconds)               → 5025 → "1:23:45"
  - format_bytes(bytes)                    → 1234567 → "1.2 MB"
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import yt_dlp
from yt_dlp.utils import download_range_func


# ================================================================
# Verificação de ambiente
# ================================================================

def check_deno() -> Optional[str]:
    """Retorna o caminho do deno se disponível e funcional, senão None."""
    deno = shutil.which("deno")
    if not deno:
        return None
    try:
        subprocess.run(
            [deno, "--version"],
            capture_output=True, timeout=5, check=True,
        )
        return deno
    except Exception:
        return None


def deno_version() -> Optional[str]:
    deno = shutil.which("deno")
    if not deno:
        return None
    try:
        out = subprocess.run(
            [deno, "--version"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        first = out.stdout.splitlines()[0] if out.stdout else "deno"
        return first
    except Exception:
        return None


def yt_dlp_version() -> str:
    try:
        from yt_dlp.version import __version__
        return __version__
    except Exception:
        return "desconhecida"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def firefox_profile_exists() -> bool:
    """Checa se há instalação do Firefox com pelo menos um perfil."""
    if sys.platform.startswith("win"):
        p = Path.home() / "AppData" / "Roaming" / "Mozilla" / "Firefox" / "Profiles"
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles"
    else:
        p = Path.home() / ".mozilla" / "firefox"
    return p.exists() and any(p.iterdir()) if p.exists() else False


def atualizar_yt_dlp(silent: bool = False) -> bool:
    """Atualiza o yt-dlp com o extra [default] (inclui yt-dlp-ejs)."""
    if not silent:
        print("[INFO] Atualizando yt-dlp...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "--pre",
             "yt-dlp[default]", "--quiet"],
            check=False, timeout=180,
        )
        return True
    except Exception as e:
        if not silent:
            print(f"[AVISO] Falha ao atualizar yt-dlp: {e}")
        return False


# ================================================================
# Utilitários de formato
# ================================================================

_TIME_RE = re.compile(r"^\s*(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)\s*$")


def parse_time_to_seconds(s: str | int | float) -> float:
    """Converte '1:23:45', '5:30', '90', 90 etc. em segundos (float)."""
    if isinstance(s, (int, float)):
        return float(s)
    m = _TIME_RE.match(str(s))
    if not m:
        raise ValueError(f"Formato de tempo inválido: {s!r}")
    g1, g2, g3 = m.groups()
    parts = [float(g) for g in (g1, g2, g3) if g is not None]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def format_duration(seconds: float | int | None) -> str:
    if not seconds or seconds < 0:
        return "?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_bytes(n: float | int | None) -> str:
    if not n:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ================================================================
# Helpers de format spec (yt-dlp)
# ================================================================

def format_spec_by_quality(
    max_height: Optional[int] = None,
    container: str = "mp4",
) -> str:
    """
    Gera format spec com fallback robusto.
      - max_height=None   → melhor disponível
      - max_height=1080   → melhor até 1080p
    """
    height_filter = f"[height<={max_height}]" if max_height else ""
    ext_filter = f"[ext={container}]" if container else ""
    # Tenta: mp4+m4a → qualquer vídeo+áudio → best single
    return (
        f"bestvideo{height_filter}{ext_filter}+bestaudio[ext=m4a]"
        f"/bestvideo{height_filter}+bestaudio"
        f"/best{height_filter}"
        f"/best"
    )


# ================================================================
# Options builder
# ================================================================

BASE_OPTS: dict[str, Any] = {
    "windowsfilenames": True,
    "retries": 10,
    "fragment_retries": 10,
    "extractor_retries": 5,
    "file_access_retries": 5,
    "concurrent_fragment_downloads": 4,
    "http_chunk_size": 10 * 1024 * 1024,
    "quiet": True,
    "no_warnings": False,
    "ignoreerrors": False,

    # Runtime JS + EJS (essencial desde yt-dlp 2025.11.12)
    "js_runtimes": {"deno": {}},
    "remote_components": {"ejs:github"},

    # Clientes InnerTube estáveis
    "extractor_args": {
        "youtube": {
            "player_client": ["tv", "web_safari", "mweb"],
        }
    },
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
            "Gecko/20100101 Firefox/128.0"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    },
}


def build_options(
    output_dir: Path | str,
    *,
    format_spec: str = "bestvideo*+bestaudio/best",
    merge_format: str = "mp4",
    audio_only: bool = False,
    audio_format: str = "mp3",
    audio_quality: str = "0",  # 0 (best) ... 9 (worst) — ffmpeg VBR
    cookies_browser: Optional[str] = "firefox",
    cookies_file: Optional[Path | str] = None,
    playlist: bool = True,
    playlist_items: Optional[str] = None,
    trim_start: Optional[float] = None,
    trim_end: Optional[float] = None,
    force_keyframes_at_cuts: bool = True,
    write_subtitles: bool = False,
    subtitles_langs: Optional[list[str]] = None,
    embed_thumbnail: bool = False,
    embed_metadata: bool = True,
    progress_hook: Optional[Callable[[dict], None]] = None,
    postprocessor_hook: Optional[Callable[[dict], None]] = None,
    outtmpl: Optional[str] = None,
    quiet: bool = True,
    verbose: bool = False,
) -> dict:
    """Monta o dicionário de opções do yt-dlp."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    opts: dict[str, Any] = {**BASE_OPTS, "quiet": quiet, "verbose": verbose}
    opts["noplaylist"] = not playlist

    # Template de nome de arquivo
    if outtmpl is None:
        if playlist:
            outtmpl = "%(playlist_title&{}/|)s%(title).180B [%(id)s].%(ext)s"
        else:
            outtmpl = "%(title).200B [%(id)s].%(ext)s"
    opts["outtmpl"] = str(output_dir / outtmpl)

    # Format
    postprocessors: list[dict] = []
    if audio_only:
        opts["format"] = "bestaudio/best"
        postprocessors.append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": audio_format,
            "preferredquality": audio_quality,
        })
    else:
        opts["format"] = format_spec
        opts["merge_output_format"] = merge_format

    # Metadata / thumbnail
    if embed_metadata:
        postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True})
    if embed_thumbnail:
        opts["writethumbnail"] = True
        postprocessors.append({
            "key": "EmbedThumbnail",
            "already_have_thumbnail": False,
        })

    # Legendas
    if write_subtitles:
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = subtitles_langs or ["pt", "pt-BR", "en"]
        opts["subtitlesformat"] = "best"

    if postprocessors:
        opts["postprocessors"] = postprocessors

    # Cookies
    if cookies_file and Path(cookies_file).exists():
        opts["cookiefile"] = str(cookies_file)
    elif cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser, None, None, None)

    # Playlist items (ex: "1,3,5-7")
    if playlist_items:
        opts["playlist_items"] = playlist_items

    # Cortes (trim)
    if trim_start is not None or trim_end is not None:
        start = float(trim_start) if trim_start is not None else 0.0
        end = float(trim_end) if trim_end is not None else None
        # end=None → yt-dlp interpreta como até o fim
        ranges = [(start, end if end is not None else float("inf"))]
        opts["download_ranges"] = download_range_func(None, ranges)
        opts["force_keyframes_at_cuts"] = force_keyframes_at_cuts

    # Hooks
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    if postprocessor_hook:
        opts["postprocessor_hooks"] = [postprocessor_hook]

    return opts


# ================================================================
# Extração de metadata (sem baixar)
# ================================================================

def _base_info_opts(cookies_browser: Optional[str],
                    cookies_file: Optional[Path | str]) -> dict:
    opts = {**BASE_OPTS, "quiet": True, "skip_download": True}
    if cookies_file and Path(cookies_file).exists():
        opts["cookiefile"] = str(cookies_file)
    elif cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser, None, None, None)
    return opts


def extract_info(
    url: str,
    *,
    cookies_browser: Optional[str] = "firefox",
    cookies_file: Optional[Path | str] = None,
    process_playlist: bool = False,
) -> dict:
    """Metadata completa (inclui lista de formatos)."""
    opts = _base_info_opts(cookies_browser, cookies_file)
    opts["noplaylist"] = not process_playlist
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.sanitize_info(ydl.extract_info(url, download=False))


def extract_playlist_flat(
    url: str,
    *,
    cookies_browser: Optional[str] = "firefox",
    cookies_file: Optional[Path | str] = None,
) -> dict:
    """Lista a playlist rapidamente (sem processar cada vídeo)."""
    opts = _base_info_opts(cookies_browser, cookies_file)
    opts["extract_flat"] = "in_playlist"
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.sanitize_info(ydl.extract_info(url, download=False))


# ================================================================
# Download
# ================================================================

def download(urls: Iterable[str], opts: dict) -> int:
    """Dispara o download. Retorna o retcode do yt-dlp (0 = sucesso)."""
    urls = list(urls)
    if not urls:
        raise ValueError("Nenhuma URL informada.")
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.download(urls)
