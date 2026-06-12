#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from deep_translator import GoogleTranslator


TEXT_SUBTITLE_CODECS = {"subrip", "ass", "ssa", "webvtt", "mov_text"}
IMAGE_SUBTITLE_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "xsub"}
SOURCE_LANGUAGE_PRIORITY = ("zho", "chi", "cmn", "yue", "jpn", "kor", "spa", "fra", "deu", "ita")


def log(message: str) -> None:
    print(message, flush=True)


def run_command(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    log("$ " + " ".join(command))
    return subprocess.run(command, check=True, text=True, capture_output=capture)


def ffprobe_streams(video_path: Path) -> list[dict]:
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-of",
            "json",
            str(video_path),
        ],
        capture=True,
    )
    payload = json.loads(result.stdout or "{}")
    return payload.get("streams", [])


def normalize_language(value: str | None) -> str:
    if not value:
        return "und"
    aliases = {
        "en": "eng",
        "english": "eng",
        "zh": "zho",
        "chi": "zho",
        "cn": "zho",
        "chinese": "zho",
        "ja": "jpn",
        "jp": "jpn",
    }
    return aliases.get(value.strip().lower(), value.strip().lower())


def subtitle_streams(video_path: Path) -> list[dict]:
    tracks: list[dict] = []
    for stream in ffprobe_streams(video_path):
        if stream.get("codec_type") != "subtitle":
            continue
        tracks.append(
            {
                "index": stream["index"],
                "codec_name": stream.get("codec_name", "unknown"),
                "language": normalize_language(stream.get("tags", {}).get("language")),
                "title": stream.get("tags", {}).get("title", ""),
            }
        )
    return tracks


def english_tracks(tracks: list[dict]) -> list[dict]:
    return [track for track in tracks if track["language"] == "eng"]


def non_english_tracks(tracks: list[dict]) -> list[dict]:
    return [track for track in tracks if track["language"] != "eng"]


def find_track_by_index(tracks: list[dict], stream_index: int) -> dict:
    for track in tracks:
        if track["index"] == stream_index:
            return track
    raise RuntimeError(f"Subtitle stream index {stream_index} not found")


def find_track_by_language(tracks: list[dict], language: str) -> dict | None:
    normalized = normalize_language(language)
    for track in tracks:
        if track["language"] == normalized:
            return track
    return None


def sort_tracks_by_priority(tracks: list[dict]) -> list[dict]:
    def score(track: dict) -> tuple[int, int]:
        try:
            return (0, SOURCE_LANGUAGE_PRIORITY.index(track["language"]))
        except ValueError:
            return (1, track["index"])

    return sorted(tracks, key=score)


def resolve_tracks(video_path: Path, language: str | None, stream_index: int | None) -> tuple[list[dict], list[dict]]:
    tracks = subtitle_streams(video_path)
    if not tracks:
        raise RuntimeError(f"No subtitle streams found in {video_path}")

    english = english_tracks(tracks)
    non_english = non_english_tracks(tracks)

    if stream_index is not None:
        return [find_track_by_index(tracks, stream_index)], english

    if language is not None:
        selected = find_track_by_language(tracks, language)
        if selected is None:
            raise RuntimeError(f"No subtitle stream found for language {language}")
        return [selected], english

    if not non_english:
        return [], english

    return sort_tracks_by_priority(non_english), english


def extract_text_subtitle(video_path: Path, track: dict, output_path: Path) -> Path:
    codec_name = track["codec_name"]
    if codec_name in IMAGE_SUBTITLE_CODECS:
        raise RuntimeError(
            f"Subtitle stream {track['index']} uses {codec_name}, which is image-based. OCR is required before translation."
        )
    if codec_name not in TEXT_SUBTITLE_CODECS:
        raise RuntimeError(f"Subtitle stream {track['index']} codec {codec_name} is not supported for direct translation")

    run_command(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-map",
            f"0:{track['index']}",
            str(output_path),
        ]
    )
    return output_path


def extract_pgs_subtitle(video_path: Path, track: dict, output_path: Path) -> Path:
    run_command([
        "mkvextract",
        "tracks",
        str(video_path),
        f"{track['index']}:{output_path}",
    ])
    return output_path


def ocr_pgs_subtitle(input_path: Path, output_path: Path) -> Path:
    run_command(["pgsrip", str(input_path)])
    generated = input_path.with_suffix(".srt")
    if not generated.exists():
        matches = sorted(input_path.parent.glob(f"{input_path.stem}*.srt"))
        if not matches:
            raise RuntimeError(f"PGS OCR did not produce an SRT for {input_path}")
        generated = matches[0]
    if generated != output_path:
        shutil.move(str(generated), str(output_path))
    return output_path


def translate_srt(input_path: Path, output_path: Path, *, target_language: str = "en") -> Path:
    translator = GoogleTranslator(source="auto", target=target_language)
    translated_lines: list[str] = []
    pending_text: list[str] = []

    def flush_pending() -> None:
        nonlocal pending_text
        if not pending_text:
            return
        translated = translator.translate_batch(pending_text) or pending_text
        translated_lines.extend(translated)
        pending_text = []

    for raw_line in input_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            flush_pending()
            translated_lines.append("")
            continue
        if stripped.isdigit() or "-->" in raw_line:
            flush_pending()
            translated_lines.append(raw_line)
            continue
        pending_text.append(raw_line)

    flush_pending()
    output_path.write_text("\n".join(translated_lines) + "\n", encoding="utf-8")
    return output_path


def sync_subtitle(video_path: Path, subtitle_path: Path, output_path: Path) -> Path:
    run_command(
        [
            "ffsubsync",
            str(video_path),
            "-i",
            str(subtitle_path),
            "-o",
            str(output_path),
        ]
    )
    return output_path


def mux_subtitle(video_path: Path, subtitle_path: Path, output_path: Path, *, title: str, language: str) -> Path:
    existing_subtitle_count = sum(1 for stream in ffprobe_streams(video_path) if stream.get("codec_type") == "subtitle")
    with tempfile.NamedTemporaryFile(suffix=video_path.suffix, delete=False, dir=str(output_path.parent)) as handle:
        temp_output = Path(handle.name)

    try:
        run_command(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(video_path),
                "-i",
                str(subtitle_path),
                "-map",
                "0",
                "-map",
                "1:0",
                "-c",
                "copy",
                f"-metadata:s:s:{existing_subtitle_count}",
                f"language={language}",
                f"-metadata:s:s:{existing_subtitle_count}",
                f"title={title}",
                str(temp_output),
            ]
        )
        temp_output.replace(output_path)
        return output_path
    finally:
        temp_output.unlink(missing_ok=True)


def output_label(has_english: bool) -> str:
    return "eng.2" if has_english else "eng.clone"


def sidecar_path(video_path: Path, track: dict, *, has_english: bool, extension: str) -> Path:
    label = output_label(has_english)
    return video_path.with_name(f"{video_path.stem}.{label}.{track['language']}.{track['index']}.{extension}")


def mux_title(track: dict, *, has_english: bool) -> str:
    prefix = "English 2" if has_english else "English"
    return f"{prefix} (translated from {track['language']})"


def process_track(
    video_path: Path,
    track: dict,
    *,
    workdir: Path,
    has_english: bool,
    translate_provider: str,
    target_language: str,
    sync_enabled: bool,
    mux_enabled: bool,
) -> Path:
    track_prefix = workdir / f"stream-{track['index']}.{track['language']}"
    extracted_text_path = track_prefix.with_suffix(".srt")
    extracted_pgs_path = track_prefix.with_suffix(".sup")
    ocr_text_path = track_prefix.with_name(f"{track_prefix.name}.ocr.srt")
    translated_sidecar = sidecar_path(video_path, track, has_english=has_english, extension="srt")
    synced_sidecar = translated_sidecar.with_name(f"{translated_sidecar.stem}.synced{translated_sidecar.suffix}")

    if track["codec_name"] in IMAGE_SUBTITLE_CODECS:
        extract_pgs_subtitle(video_path, track, extracted_pgs_path)
        source_text = ocr_pgs_subtitle(extracted_pgs_path, ocr_text_path)
    else:
        source_text = extract_text_subtitle(video_path, track, extracted_text_path)

    if translate_provider == "google":
        translate_srt(source_text, translated_sidecar, target_language=target_language)
    else:
        shutil.copy2(source_text, translated_sidecar)

    final_sidecar = translated_sidecar
    if sync_enabled:
        final_sidecar = sync_subtitle(video_path, translated_sidecar, synced_sidecar)
        shutil.move(str(final_sidecar), str(translated_sidecar))
        final_sidecar = translated_sidecar

    if mux_enabled:
        mux_subtitle(
            video_path,
            final_sidecar,
            video_path,
            title=mux_title(track, has_english=has_english),
            language=normalize_language(target_language),
        )

    return final_sidecar


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Translate non-English subtitle tracks into labeled English sidecars and optionally mux them")
    parser.add_argument("video", help="Path to the MKV file")
    parser.add_argument("--subtitle-language", help="Preferred source subtitle language, for example zho or jpn")
    parser.add_argument("--subtitle-index", type=int, help="Exact subtitle stream index from ffprobe")
    parser.add_argument("--workdir", help="Optional working directory for extracted subtitle files")
    parser.add_argument("--translate-provider", choices=["google", "none"], default="google")
    parser.add_argument("--target-language", default="en")
    parser.add_argument("--sync", action="store_true", help="Run ffsubsync against the video audio before remuxing")
    parser.add_argument("--mux", action="store_true", help="Mux the translated subtitle back into the source MKV")
    parser.add_argument("--title", default="English (translated fallback)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    video_path = Path(args.video).resolve()
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    selected_tracks, english = resolve_tracks(video_path, args.subtitle_language, args.subtitle_index)
    workdir = Path(args.workdir).resolve() if args.workdir else video_path.parent / f"{video_path.stem}.subs"
    workdir.mkdir(parents=True, exist_ok=True)

    if not selected_tracks:
        log(f"Only English subtitles were found in {video_path}; nothing to translate or clone")
        return 0

    has_english = bool(english)
    exit_code = 0
    for track in selected_tracks:
        try:
            output = process_track(
                video_path,
                track,
                workdir=workdir,
                has_english=has_english,
                translate_provider=args.translate_provider,
                target_language=args.target_language,
                sync_enabled=args.sync,
                mux_enabled=args.mux,
            )
            log(f"Prepared subtitle file: {output}")
        except Exception as exc:
            exit_code = 1
            log(f"Failed subtitle track {track['index']} ({track['language']}): {exc}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())