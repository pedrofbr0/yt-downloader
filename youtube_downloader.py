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
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import yt_dlp
from yt_dlp.utils import download_range_func
from yt_dlp.postprocessor.ffmpeg import (
    FFmpegPostProcessor, FFmpegPostProcessorError,
)


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


def _format_hms_dash(seconds: float | int | None) -> str:
    # Hífen em vez de ':' porque ':' é inválido em nomes de arquivo no Windows.
    if seconds is None or seconds < 0:
        return "00-00-00"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}-{m:02d}-{sec:02d}"


def format_duration(seconds: float | int | None) -> str:
    if seconds is None or seconds < 0:
        return "?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_bytes(n: float | int | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ================================================================
# Utilitários de legendas (trimming)
# ================================================================

_SUB_TS_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})")

# Captura blocos VTT (sem número de sequência) e SRT (com número).
# Grupo 1: timestamp início, grupo 2: timestamp fim, grupo 3: texto.
_CUE_RE = re.compile(
    r"(?:^\d+\s*\n)?"  # número de sequência opcional (SRT)
    r"(\d{1,2}:\d{2}:\d{2}[,.]\d{3})"  # timestamp início
    r"\s*-->\s*"
    r"(\d{1,2}:\d{2}:\d{2}[,.]\d{3})"  # timestamp fim
    r"[^\n]*\n"                          # resto da linha (align:start etc.)
    r"((?:(?!\n\n|\n\r\n).+(?:\n|$))+)",  # texto (até bloco vazio)
    re.MULTILINE,
)

# Tags inline do VTT: <00:00:03.439>, <c>, </c>, etc.
_VTT_TAG_RE = re.compile(r"<[^>]+>")


def _parse_sub_time(s: str) -> float:
    """'00:01:30,500' ou '00:01:30.500' → 90.5"""
    m = _SUB_TS_RE.match(s.strip())
    if not m:
        return 0.0
    h, mn, sec, ms = m.groups()
    return int(h) * 3600 + int(mn) * 60 + int(sec) + int(ms) / 1000


def _format_srt_time(seconds: float) -> str:
    """90.5 → '00:01:30,500'"""
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000)) % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _clean_vtt_text(text: str) -> str:
    """Remove tags inline do VTT e linhas em branco extras."""
    text = _VTT_TAG_RE.sub("", text)
    # Colapsa linhas duplicadas consecutivas (YouTube repete texto)
    lines: list[str] = []
    for line in text.strip().splitlines():
        stripped = line.strip()
        if stripped and (not lines or stripped != lines[-1]):
            lines.append(stripped)
    return "\n".join(lines)


def _trim_subtitle_file(
    filepath: Path,
    trim_start: float,
    trim_end: float | None,
) -> None:
    """Filtra e re-sincroniza um arquivo .srt/.vtt para o intervalo de corte."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    is_vtt = filepath.suffix.lower() == ".vtt"
    sep = "." if is_vtt else ","

    cues = _CUE_RE.findall(content)
    new_blocks: list[str] = []
    idx = 1

    for start_ts, end_ts, text in cues:
        start = _parse_sub_time(start_ts)
        end = _parse_sub_time(end_ts)

        if end <= trim_start:
            continue
        if trim_end is not None and start >= trim_end:
            continue

        # Pula cues fantasma do YouTube (duração ~0.01s, texto em branco)
        clean = _clean_vtt_text(text)
        if not clean:
            continue

        new_start = max(0.0, start - trim_start)
        new_end = end - trim_start
        if trim_end is not None:
            new_end = min(new_end, trim_end - trim_start)

        ts_s = _format_srt_time(new_start).replace(",", sep)
        ts_e = _format_srt_time(new_end).replace(",", sep)

        if is_vtt:
            new_blocks.append(f"{ts_s} --> {ts_e}\n{clean}\n")
        else:
            new_blocks.append(f"{idx}\n{ts_s} --> {ts_e}\n{clean}\n")
        idx += 1

    if is_vtt:
        header = "WEBVTT\n\n"
    else:
        header = ""
    result = header + "\n".join(new_blocks) + "\n" if new_blocks else header
    filepath.write_text(result, encoding="utf-8")


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

    Não filtra o vídeo por ext: bestvideo[ext=mp4] seleciona streams
    progressivos do YouTube que só funcionam com range requests (trim).
    Sem o filtro, yt-dlp usa DASH/VP9 que funciona em downloads completos
    e parciais igualmente. O container de saída é controlado por
    merge_output_format no build_options.
    """
    height_filter = f"[height<={max_height}]" if max_height else ""
    return (
        f"bestvideo{height_filter}+bestaudio[ext=m4a]"
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
    "file_access_retries": 30,  # Windows Defender pode segurar o arquivo por vários segundos
    "concurrent_fragment_downloads": 4,
    "http_chunk_size": 10 * 1024 * 1024,
    "quiet": True,
    "no_warnings": False,
    "ignoreerrors": False,
    "keeppartialfiles": False,  # limpa .part e .ytdl em caso de falha

    # Runtime JS + EJS (essencial desde yt-dlp 2025.11.12)
    "js_runtimes": {"deno": {}},
    "remote_components": {"ejs:github"},

    # android_vr: fornece DASH completo (vídeo-only + áudio-only) sem DRM
    # e sem exigir PO Token ou JS challenge.  tv e mweb removidos por
    # experimentação DRM do YouTube que bloqueia segmentos no meio do download.
    "extractor_args": {
        "youtube": {
            "player_client": ["android_vr", "web_safari"],
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
    video_only: bool = False,
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
    duration: Optional[float] = None,
    write_subtitles: bool = False,
    subtitles_only: bool = False,
    subtitles_langs: Optional[list[str]] = None,
    embed_subtitles: bool = False,
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
    opts["overwrites"] = False  # auto-increment handled by app before download

    # Sufixo de trim no nome (calculado aqui para entrar no outtmpl).
    # O sufixo permanece mesmo quando o trim acaba sendo pulado por cobrir
    # toda a duração — o usuário marcou a opção, então o nome reflete isso.
    has_trim = trim_start is not None or trim_end is not None
    trim_covers_full = False
    trim_suffix = ""
    if has_trim:
        start_s = float(trim_start) if trim_start is not None else 0.0
        if trim_end is not None:
            end_s: Optional[float] = float(trim_end)
        elif duration is not None:
            end_s = float(duration)
        else:
            end_s = None
        if end_s is not None:
            trim_suffix = (
                f" ({_format_hms_dash(start_s)} - {_format_hms_dash(end_s)})"
            )
        else:
            trim_suffix = f" ({_format_hms_dash(start_s)} - fim)"
        if duration is not None and end_s is not None:
            trim_covers_full = (start_s <= 1.0 and (float(duration) - end_s) <= 1.0)

    # Template de nome de arquivo
    if outtmpl is None:
        if playlist:
            outtmpl = f"%(playlist_title&{{}}/|)s%(title).180B [%(id)s]{trim_suffix}.%(ext)s"
        else:
            outtmpl = f"%(title).200B [%(id)s]{trim_suffix}.%(ext)s"
    opts["outtmpl"] = str(output_dir / outtmpl)

    # Format
    postprocessors: list[dict] = []
    if subtitles_only:
        opts["skip_download"] = True
    elif audio_only:
        opts["format"] = "bestaudio/best"
        postprocessors.append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": audio_format,
            "preferredquality": audio_quality,
        })
    elif video_only:
        opts["format"] = format_spec
        opts["remux_video"] = merge_format  # remux single stream (no merge)
    else:
        opts["format"] = format_spec
        opts["merge_output_format"] = merge_format
        # Re-encode áudio com qualidade específica (preserva vídeo)
        if audio_quality != "0":
            # Converte VBR (0-9) para bitrate aproximado para o merge
            _vbr_to_kbps = {"2": "192", "5": "128", "7": "100", "9": "64"}
            if audio_quality.upper().endswith("K"):
                abr = audio_quality.upper()  # ex: "192K"
            else:
                abr = _vbr_to_kbps.get(audio_quality, "192") + "K"
            opts.setdefault("postprocessor_args", {})
            opts["postprocessor_args"]["merger"] = [
                "-c:v", "copy", "-b:a", abr.lower(),
            ]

    # Metadata / thumbnail (apenas quando baixa mídia)
    if not subtitles_only:
        if embed_metadata:
            postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True})
        if embed_thumbnail:
            opts["writethumbnail"] = True
            postprocessors.append({
                "key": "EmbedThumbnail",
                "already_have_thumbnail": False,
            })

    # Legendas — configuração base (langs, etc.)
    _subs_langs = subtitles_langs or ["pt", "pt-BR", "en"]

    # Quando há trim + legendas juntos, yt-dlp pode corromper o vídeo.
    # Solução: separar em dois passes — vídeo trimado sem legendas,
    # depois legendas baixadas à parte e trimadas por nós.
    # Se o trim cobre o vídeo todo, tratamos como sem-trim para evitar
    # re-encode e dois-passes desnecessários.
    trim_active = has_trim and not trim_covers_full
    _need_separate_subs = (write_subtitles and trim_active and not subtitles_only)

    if (write_subtitles or subtitles_only) and not _need_separate_subs:
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = _subs_langs
        # YouTube lista json3 por último; "best" pegava json3, que o convertor
        # se recusa a transformar em srt. "srt/vtt/best" prefere srt nativo,
        # cai para vtt (convertível) e só por último aceita qualquer formato.
        opts["subtitlesformat"] = "srt/vtt/best"
        postprocessors.append({
            # when='before_dl' garante que o convertor rode entre o download
            # da legenda e do vídeo, antes do MoveFilesAfterDownloadPP — caso
            # contrário a referência em __files_to_move pode ficar incorreta.
            "key": "FFmpegSubtitlesConvertor",
            "format": "srt",
            "when": "before_dl",
        })
        # Embutir legendas no container (opt-in pelo usuário)
        if embed_subtitles and not audio_only and not subtitles_only:
            postprocessors.append({
                "key": "FFmpegEmbedSubtitle",
                # já temos as legendas em arquivo separado: não apagar após
                # embutir, para o usuário manter ambas as cópias.
                "already_have_subtitle": True,
            })

    # Cookies
    if cookies_file and Path(cookies_file).exists():
        opts["cookiefile"] = str(cookies_file)
    elif cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser, None, None, None)

    # Playlist items (ex: "1,3,5-7")
    if playlist_items:
        opts["playlist_items"] = playlist_items

    # Cortes (trim) — só aplica se trim_active (não coberto pela duração total)
    if trim_active:
        start = float(trim_start) if trim_start is not None else 0.0
        end = float(trim_end) if trim_end is not None else None
        if not subtitles_only:
            # external_downloader=ffmpeg com -ss/-to trava em DASH do YouTube
            # (android_vr/SABR exige o downloader de fragmentos nativo do yt-dlp).
            # Sempre usar download_ranges; só alternar keyframes vs. re-encode.
            ranges = [(start, end if end is not None else float("inf"))]
            opts["download_ranges"] = download_range_func(None, ranges)
            opts["force_keyframes_at_cuts"] = bool(force_keyframes_at_cuts)
        # Marca legendas para trimming pós-download
        if write_subtitles or subtitles_only:
            opts["_subtitle_trim"] = {
                "trim_start": start,
                "trim_end": end,
            }

    # Sinaliza que legendas precisam ser baixadas separadamente
    if _need_separate_subs:
        opts["_separate_subs"] = {
            "langs": _subs_langs,
        }
        # Embutir legendas no container após o trimming (opt-in)
        if embed_subtitles and not audio_only:
            opts["_embed_subs"] = True

    if postprocessors:
        opts["postprocessors"] = postprocessors

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
# Embutir legendas no container
# ================================================================

def _embed_subs_in_video(srt_files: Iterable[Path]) -> None:
    """Embutem arquivos SRT no container de vídeo correspondente.

    Agrupa por base do nome (``video.lang.srt`` → ``video``) e localiza
    o arquivo de vídeo com mesmo stem.  Para cada grupo, executa ffmpeg
    para adicionar as trilhas de legenda com metadado ``language``.
    """
    from collections import defaultdict

    groups: dict[Path, list[tuple[str, Path]]] = defaultdict(list)
    for srt in srt_files:
        parts = srt.stem.rsplit(".", 1)
        if len(parts) != 2:
            continue
        base, lang = parts
        groups[srt.parent / base].append((lang, srt))

    for base_path, langs in groups.items():
        video_path: Path | None = None
        for ext in (".mp4", ".mkv", ".webm", ".mov"):
            candidate = base_path.with_suffix(ext)
            if candidate.exists():
                video_path = candidate
                break
        if not video_path:
            continue

        sub_codec = "mov_text" if video_path.suffix.lower() == ".mp4" else "srt"

        cmd: list[str] = ["ffmpeg", "-y", "-i", str(video_path)]
        maps = ["-map", "0"]
        metadata: list[str] = []
        for i, (lang, srt_path) in enumerate(langs):
            cmd += ["-i", str(srt_path)]
            maps += ["-map", str(i + 1)]
            metadata += [f"-metadata:s:s:{i}", f"language={lang}"]

        tmp_path = video_path.with_name(video_path.stem + ".tmp" + video_path.suffix)
        cmd += maps + ["-c", "copy", "-c:s", sub_codec] + metadata + [str(tmp_path)]

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            video_path.unlink()
            tmp_path.rename(video_path)
        except subprocess.CalledProcessError:
            if tmp_path.exists():
                tmp_path.unlink()


# ================================================================
# Progresso ao vivo dos postprocessors ffmpeg
# ================================================================
#
# yt-dlp roda os PPs ffmpeg via subprocess.Popen bloqueante: nenhum hook
# emite progresso em tempo real. Para a UI mostrar bar/ETA durante merge,
# trim re-encode, embed etc., monkey-patcheamos `real_run_ffmpeg`:
#  - injeta `-progress pipe:1 -nostats` para o ffmpeg emitir métricas
#  - lê stdout linha-a-linha em foreground (out_time_us=, progress=)
#  - lê stderr em thread (parse de Duration: HH:MM:SS.mm + buffer p/ erros)
#  - chama _PP_PROGRESS_CALLBACK(frac, elapsed_us, total_us)

_PP_PROGRESS_CALLBACK: Optional[Callable[[float, int, int], None]] = None
_FFPROGRESS_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")


def set_pp_progress_callback(fn: Optional[Callable[[float, int, int], None]]) -> None:
    """Registra callback chamado durante PPs ffmpeg. Thread-safe (1 download/vez)."""
    global _PP_PROGRESS_CALLBACK
    _PP_PROGRESS_CALLBACK = fn


_orig_real_run_ffmpeg = FFmpegPostProcessor.real_run_ffmpeg


def _patched_real_run_ffmpeg(self, input_path_opts, output_path_opts,
                             *, expected_retcodes=(0,)):
    """Drop-in replacement de real_run_ffmpeg que emite progresso."""
    # Sem callback configurado → fallback transparente para o original
    if _PP_PROGRESS_CALLBACK is None:
        return _orig_real_run_ffmpeg(
            self, input_path_opts, output_path_opts,
            expected_retcodes=expected_retcodes,
        )

    import itertools as _it
    import os as _os

    self.check_version()
    oldest_mtime = min(
        _os.stat(path).st_mtime for path, _ in input_path_opts if path)

    cmd = [self.executable, "-y"]
    if self.basename == "ffmpeg":
        cmd += ["-loglevel", "repeat+info", "-progress", "pipe:1", "-nostats"]

    def make_args(file, args, name, number):
        keys = [f"_{name}{number}", f"_{name}"]
        if name == "o":
            args += ["-movflags", "+faststart"]
            if number == 1:
                keys.append("")
        args += self._configuration_args(self.basename, keys)
        if name == "i":
            args.append("-i")
        return [*args, self._ffmpeg_filename_argument(file)]

    for arg_type, path_opts in (("i", input_path_opts), ("o", output_path_opts)):
        cmd += list(_it.chain.from_iterable(
            make_args(path, list(opts), arg_type, i + 1)
            for i, (path, opts) in enumerate(path_opts) if path))

    self.write_debug(f"ffmpeg command line: {cmd}")

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.PIPE, text=True, bufsize=1,
    )

    duration_us = {"value": 0}  # mutável p/ thread reader
    stderr_buf: list[str] = []

    def _read_stderr() -> None:
        for line in proc.stderr:
            stderr_buf.append(line)
            if not duration_us["value"]:
                m = _FFPROGRESS_DURATION_RE.search(line)
                if m:
                    h, mn, s = m.groups()
                    duration_us["value"] = int(
                        (int(h) * 3600 + int(mn) * 60 + float(s)) * 1_000_000
                    )

    t = threading.Thread(target=_read_stderr, daemon=True)
    t.start()

    last_emit = 0.0
    last_out_us = 0
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("out_time_us="):
                continue
            try:
                cur_us = int(line.split("=", 1)[1])
            except ValueError:
                continue
            last_out_us = cur_us
            now = time.monotonic()
            # throttle a 4 Hz para não inundar a queue da UI
            if now - last_emit < 0.25:
                continue
            last_emit = now
            total = duration_us["value"]
            frac = (cur_us / total) if total else 0.0
            try:
                _PP_PROGRESS_CALLBACK(min(frac, 1.0), cur_us, total)
            except Exception:
                pass
    finally:
        proc.wait()
        t.join(timeout=2)

    stderr = "".join(stderr_buf)
    if proc.returncode not in (expected_retcodes if isinstance(expected_retcodes, tuple) else (expected_retcodes,)):
        last = (stderr.strip().splitlines() or [""])[-1]
        raise FFmpegPostProcessorError(last)

    # Tick final em 100% para garantir bar cheia ao concluir
    try:
        _PP_PROGRESS_CALLBACK(1.0, last_out_us, duration_us["value"])
    except Exception:
        pass

    for out_path, _ in output_path_opts:
        if out_path:
            self.try_utime(out_path, oldest_mtime, oldest_mtime)
    return stderr


# Aplica o patch uma única vez no import. Os PPs do yt-dlp todos chamam
# real_run_ffmpeg via run_ffmpeg/run_ffmpeg_multiple_files.
FFmpegPostProcessor.real_run_ffmpeg = _patched_real_run_ffmpeg


# ================================================================
# Download
# ================================================================

def download(urls: Iterable[str], opts: dict) -> int:
    """Dispara o download. Retorna o retcode do yt-dlp (0 = sucesso)."""
    urls = list(urls)
    if not urls:
        raise ValueError("Nenhuma URL informada.")

    opts = dict(opts)  # não muta o dict do chamador
    subtitle_trim = opts.pop("_subtitle_trim", None)
    separate_subs = opts.pop("_separate_subs", None)
    embed_subs = opts.pop("_embed_subs", False)
    notify = opts.pop("_notify", None)  # Optional[Callable[[str], None]]
    reset_phase = opts.pop("_reset_phase", None)  # Optional[Callable[[], None]]

    # Rastreia legendas existentes para trimming/embedding posterior
    outtmpl = opts.get("outtmpl", "%(title)s.%(ext)s")
    output_dir = Path(outtmpl).parent
    pre_subs: set[Path] = set()
    if (subtitle_trim or embed_subs) and output_dir.exists():
        pre_subs = set(output_dir.rglob("*.srt")) | set(output_dir.rglob("*.vtt"))

    # Mensagem inicial — o progress_hook só dispara depois que yt-dlp resolve
    # a URL e baixa o primeiro fragmento; até lá o status ficaria em "Iniciando…".
    if notify:
        if opts.get("download_ranges"):
            if opts.get("force_keyframes_at_cuts"):
                notify(
                    "✂️ Trim com re-encode — baixando o intervalo e "
                    "re-codificando para corte frame-accurate (pode demorar)…"
                )
            else:
                notify(
                    "✂️ Trim rápido — baixando só o intervalo (corte no "
                    "keyframe mais próximo)…"
                )
        else:
            notify("🔗 Resolvendo URL do YouTube…")

    # ---- Passo 1: download principal (vídeo/áudio, SEM legendas se trim ativo) ----
    with yt_dlp.YoutubeDL(opts) as ydl:
        rc = ydl.download(urls)

    # ---- Passo 2: legendas separadas (quando trim + legendas juntos) ----
    if separate_subs is not None:
        if reset_phase:
            reset_phase()
        if notify:
            notify("⬇️ Baixando legendas…")
        subs_opts = {**BASE_OPTS, "quiet": opts.get("quiet", True)}
        subs_opts["skip_download"] = True
        subs_opts["writesubtitles"] = True
        subs_opts["writeautomaticsub"] = True
        subs_opts["subtitleslangs"] = separate_subs["langs"]
        # Mesma lógica do passo 1: prefere srt, fallback vtt → convertor pega.
        subs_opts["subtitlesformat"] = "srt/vtt/best"
        subs_opts["outtmpl"] = outtmpl
        subs_opts["noplaylist"] = opts.get("noplaylist", True)
        subs_opts["postprocessors"] = [{
            "key": "FFmpegSubtitlesConvertor",
            "format": "srt",
            "when": "before_dl",
        }]
        # Propagar hooks, cookies e logger do opts original
        for key in ("cookiefile", "cookiesfrombrowser", "playlist_items",
                    "progress_hooks", "postprocessor_hooks", "logger"):
            if key in opts:
                subs_opts[key] = opts[key]

        with yt_dlp.YoutubeDL(subs_opts) as ydl:
            ydl.download(urls)  # ignora retcode das legendas

    # ---- Passo 3: identifica legendas novas e recorta se necessário ----
    new_subs: set[Path] = set()
    if (subtitle_trim or embed_subs) and output_dir.exists():
        post_subs = set(output_dir.rglob("*.srt")) | set(output_dir.rglob("*.vtt"))
        new_subs = post_subs - pre_subs
        if subtitle_trim:
            if notify:
                notify("✂️ Ajustando tempo das legendas…")
            for sub_file in new_subs:
                _trim_subtitle_file(
                    sub_file,
                    subtitle_trim["trim_start"],
                    subtitle_trim["trim_end"],
                )

    # ---- Passo 4: embutir legendas no container (trim + legendas) ----
    if embed_subs and new_subs:
        srt_only = [f for f in new_subs if f.suffix == ".srt"]
        if srt_only:
            if notify:
                notify("📦 Embutindo legendas no vídeo…")
            _embed_subs_in_video(srt_only)

    return rc
