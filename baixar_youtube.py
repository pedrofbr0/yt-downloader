"""
baixar_youtube.py — CLI do downloader
=====================================

Suporta:
  • Um ou vários vídeos:      python baixar_youtube.py URL1 URL2 URL3
  • Playlist inteira:         python baixar_youtube.py https://youtube.com/playlist?list=...
  • Itens específicos:        python baixar_youtube.py URL_PLAYLIST --items "1,3,5-8"
  • Cortes:                   python baixar_youtube.py URL --trim 1:30 3:45
  • Só áudio (MP3 etc.):      python baixar_youtube.py URL --audio-only
  • Leitura de arquivo:       python baixar_youtube.py --urls-file links.txt

Pré-requisitos: ver docstring do youtube_downloader.py

Exemplos:
  python baixar_youtube.py https://youtu.be/XXX https://youtu.be/YYY
  python baixar_youtube.py https://www.youtube.com/playlist?list=PL... --quality 1080
  python baixar_youtube.py https://youtu.be/XXX --audio-only --audio-format mp3
  python baixar_youtube.py https://youtu.be/XXX --trim 0:15 2:30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import youtube_downloader as core


def _print_preflight() -> None:
    print("┌─ Pré-requisitos ──────────────────────────────────────────")
    deno = core.deno_version()
    print(f"│ Deno:    {deno or '❌ NÃO INSTALADO (winget install DenoLand.Deno)'}")
    print(f"│ ffmpeg:  {'✅ OK' if core.ffmpeg_available() else '❌ NÃO INSTALADO (winget install Gyan.FFmpeg)'}")
    print(f"│ Firefox: {'✅ detectado' if core.firefox_profile_exists() else '⚠️  não detectado'}")
    print(f"│ yt-dlp:  {core.yt_dlp_version()}")
    print("└───────────────────────────────────────────────────────────")


def _ler_arquivo_urls(p: Path) -> list[str]:
    urls: list[str] = []
    for linha in p.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#"):
            urls.append(linha)
    return urls


def _progress_hook(d: dict) -> None:
    if d["status"] == "downloading":
        percent = d.get("_percent_str", "").strip()
        speed = d.get("_speed_str", "").strip()
        eta = d.get("_eta_str", "").strip()
        fn = Path(d.get("filename", "")).name
        sys.stdout.write(
            f"\r  ⬇  {fn[:50]:<50}  {percent:>7}  {speed:>12}  ETA {eta:>6}"
        )
        sys.stdout.flush()
    elif d["status"] == "finished":
        sys.stdout.write("\n  ✅ finalizado, processando...\n")
    elif d["status"] == "error":
        sys.stdout.write(f"\n  ❌ erro em {d.get('filename')}\n")


def _construir_format_spec(qualidade: str, container: str) -> str:
    """'auto', '2160', '1440', '1080', '720', '480', '360' → format spec."""
    if qualidade == "auto":
        return core.format_spec_by_quality(None, container)
    try:
        max_h = int(qualidade)
    except ValueError:
        return qualidade  # já é um format spec custom
    return core.format_spec_by_quality(max_h, container)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="baixar_youtube",
        description="Baixa vídeos / playlists do YouTube (usa yt-dlp).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("urls", nargs="*",
                   help="Uma ou mais URLs (vídeos ou playlists).")
    p.add_argument("--urls-file", type=Path,
                   help="Arquivo de texto com uma URL por linha.")
    p.add_argument("-o", "--output", default="./downloads",
                   help="Pasta de saída (default: ./downloads).")
    p.add_argument("-q", "--quality",
                   default="auto",
                   choices=["auto", "2160", "1440", "1080", "720", "480", "360"],
                   help="Altura máxima do vídeo (default: auto).")
    p.add_argument("--container", default="mp4", choices=["mp4", "mkv", "webm"],
                   help="Container de saída (default: mp4).")
    p.add_argument("-f", "--format",
                   help="Format spec custom do yt-dlp (sobrescreve --quality).")

    # Áudio
    p.add_argument("--audio-only", action="store_true",
                   help="Extrai apenas o áudio.")
    p.add_argument("--audio-format",
                   default="mp3",
                   choices=["mp3", "m4a", "opus", "wav", "flac", "aac"],
                   help="Formato do áudio extraído (default: mp3).")
    p.add_argument("--audio-quality", default="0",
                   help="Qualidade 0 (best) a 9 (worst), default 0.")

    # Playlist
    p.add_argument("--no-playlist", action="store_true",
                   help="Ignora o parâmetro de playlist e baixa só o vídeo.")
    p.add_argument("--items",
                   help='Itens da playlist (ex: "1,3,5-7").')

    # Cortes
    p.add_argument("--trim", nargs=2, metavar=("INICIO", "FIM"),
                   help='Corte do vídeo. Ex: --trim 1:30 3:45 (usa "end" pro fim).')
    p.add_argument("--no-keyframes", action="store_true",
                   help="Não força re-encode nos cortes (mais rápido, menos preciso).")

    # Extras
    p.add_argument("--subs", action="store_true",
                   help="Também baixa legendas.")
    p.add_argument("--subs-only", action="store_true",
                   help="Baixa APENAS legendas (sem vídeo/áudio).")
    p.add_argument("--embed-thumbnail", action="store_true",
                   help="Embute a thumbnail no arquivo final.")

    # Cookies / navegador
    p.add_argument("--cookies-file", type=Path,
                   help="Caminho para um cookies.txt (Netscape format).")
    p.add_argument("--browser", default="firefox",
                   choices=["firefox", "chrome", "edge", "brave", "opera",
                            "vivaldi", "safari", "chromium"],
                   help="Navegador de onde extrair cookies (default: firefox).")

    # Manutenção
    p.add_argument("--no-update", action="store_true",
                   help="Não tenta atualizar o yt-dlp no início.")
    p.add_argument("-v", "--verbose", action="store_true")

    args = p.parse_args(argv)

    # --- Montagem da lista de URLs ---
    urls = list(args.urls or [])
    if args.urls_file:
        if not args.urls_file.exists():
            print(f"Arquivo não encontrado: {args.urls_file}", file=sys.stderr)
            return 2
        urls.extend(_ler_arquivo_urls(args.urls_file))
    if not urls:
        p.print_help()
        return 2

    # --- Pré-flight ---
    if not args.no_update:
        core.atualizar_yt_dlp()
    _print_preflight()
    if not core.check_deno():
        print("\n⚠️  SEM DENO, o YouTube provavelmente não vai liberar formatos de vídeo.")
        print("   Instale com: winget install DenoLand.Deno  (e reabra o terminal)\n")

    # --- Format spec ---
    format_spec = (args.format
                   if args.format
                   else _construir_format_spec(args.quality, args.container))

    # --- Trim ---
    trim_start = trim_end = None
    if args.trim:
        try:
            trim_start = core.parse_time_to_seconds(args.trim[0])
            trim_end = (None if args.trim[1].lower() in ("end", "fim")
                        else core.parse_time_to_seconds(args.trim[1]))
        except ValueError as e:
            print(f"Erro em --trim: {e}", file=sys.stderr)
            return 2

    # --- Opções ---
    opts = core.build_options(
        output_dir=Path(args.output),
        format_spec=format_spec,
        merge_format=args.container,
        audio_only=args.audio_only,
        audio_format=args.audio_format,
        audio_quality=args.audio_quality,
        cookies_browser=args.browser,
        cookies_file=args.cookies_file,
        playlist=not args.no_playlist,
        playlist_items=args.items,
        trim_start=trim_start,
        trim_end=trim_end,
        force_keyframes_at_cuts=not args.no_keyframes,
        write_subtitles=args.subs or args.subs_only,
        subtitles_only=args.subs_only,
        embed_thumbnail=args.embed_thumbnail,
        progress_hook=_progress_hook,
        quiet=False,
        verbose=args.verbose,
    )

    print(f"\n▶  Baixando {len(urls)} URL(s) para {Path(args.output).resolve()}\n")
    try:
        rc = core.download(urls, opts)
    except Exception as e:
        print(f"\n❌ Falha: {e}", file=sys.stderr)
        return 1
    print(f"\n{'✅ Concluído' if rc == 0 else '⚠️  Finalizado com erros'} "
          f"(retcode={rc})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
