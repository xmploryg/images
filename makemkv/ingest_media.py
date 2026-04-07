#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


TEXT_SUBTITLE_SUFFIXES = {".srt", ".ass", ".ssa", ".vtt"}
TEXT_SUBTITLE_CODECS = {"subrip", "ass", "ssa", "webvtt", "mov_text"}
IMAGE_SUBTITLE_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "xsub"}
ENGLISH_HINT_PATTERN = re.compile(r"(^|[._ -])(en|eng|english)([._ -]|$)", re.IGNORECASE)
ENGLISH_COMMON_WORDS = {
    "the", "and", "you", "that", "have", "for", "not", "with", "this", "but", "from", "they",
    "say", "her", "she", "him", "his", "are", "was", "were", "what", "when", "where", "who", "why", "how",
}
UNKNOWN_LANGUAGE_CODES = {"und", "unknown", ""}
DEFAULT_SKIP_HINTS = (
    "game",
    "retro",
    "rom",
    "roms",
    "arcade",
    "emulator",
    "mame",
    "3do",
    "amiga",
    "atari",
    "dreamcast",
    "saturn",
    "sega",
    "megacd",
    "gamecube",
    "nintendo",
    "switch",
    "wii",
    "wiiu",
    "nds",
    "n64",
    "gameboy",
    "gba",
    "ps1",
    "ps2",
    "ps3",
    "ps4",
    "ps5",
    "psp",
    "vita",
    "xbox",
    "xbox360",
    "x360",
    "xboxdvd",
    "windows.iso",
    "linux.iso",
)


@dataclass(frozen=True)
class Candidate:
    kind: str
    path: Path


def log(message: str) -> None:
    print(message, flush=True)


def emit_result(status: str, source_path: Path, video_path: Path, aux_path: Path | None = None) -> None:
    aux_value = str(aux_path) if aux_path is not None else ""
    print(
        "\t".join(
            [
                "INGEST_RESULT",
                status,
                str(source_path),
                str(video_path),
                aux_value,
                str(video_path.parent),
            ]
        ),
        flush=True,
    )


def resolve_helper_command(*names: str) -> str:
    search_paths = [Path("/usr/local/bin")]
    script_path = Path(__file__).resolve()
    if script_path.parent not in search_paths:
        search_paths.append(script_path.parent)

    for directory in search_paths:
        for name in names:
            candidate = directory / name
            if candidate.exists():
                return str(candidate)
    raise RuntimeError(f"Unable to locate helper command. Tried: {', '.join(names)}")


def run_command(command: list[str], *, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    log("$ " + " ".join(command))
    return subprocess.run(command, text=True, capture_output=capture, check=check)


def normalize_language(value: str | None) -> str:
    if not value:
        return "und"
    aliases = {
        "en": "eng",
        "eng": "eng",
        "english": "eng",
        "zh": "zho",
        "chi": "zho",
        "cn": "zho",
        "chinese": "zho",
        "ja": "jpn",
        "jp": "jpn",
    }
    return aliases.get(value.strip().lower(), value.strip().lower())


def should_skip_iso(path: Path) -> bool:
    lowered = str(path).lower()
    if "/misc/" in lowered or "/_failed_" in lowered:
        return True
    return any(hint in lowered for hint in DEFAULT_SKIP_HINTS)


def detect_candidate(path: Path) -> Candidate | None:
    if path.is_file():
        if path.suffix.lower() == ".iso" and not should_skip_iso(path):
            return Candidate("iso", path)
        if path.suffix.lower() == ".mkv":
            return Candidate("mkv", path)
        return None
    if not path.is_dir():
        return None
    if (path / "BDMV").is_dir():
        return Candidate("bluray_root", path)
    if path.name == "STREAM" and path.parent.name == "BDMV":
        return Candidate("bluray_stream", path)
    if any(child.suffix.lower() == ".m2ts" for child in path.iterdir() if child.is_file()):
        return Candidate("m2ts_dir", path)
    return None


def add_candidate(discovered: list[Candidate], seen: set[Path], candidate: Candidate) -> None:
    if candidate.path in seen:
        return
    discovered.append(candidate)
    seen.add(candidate.path)


def discover_in_directory(path: Path, discovered: list[Candidate], seen: set[Path]) -> None:
    for root, dirs, files in os.walk(path):
        current = Path(root)
        if "BDMV" in dirs:
            add_candidate(discovered, seen, Candidate("bluray_root", current))
            dirs[:] = []
            continue
        if current.name == "STREAM" and current.parent.name == "BDMV":
            dirs[:] = []
            continue
        for file_name in sorted(files):
            file_path = current / file_name
            if file_path.suffix.lower() == ".iso" and not should_skip_iso(file_path):
                add_candidate(discovered, seen, Candidate("iso", file_path))
        if any(file_name.lower().endswith(".m2ts") for file_name in files):
            add_candidate(discovered, seen, Candidate("m2ts_dir", current))
            dirs[:] = []


def discover_candidates(paths: list[Path]) -> list[Candidate]:
    discovered: list[Candidate] = []
    seen: set[Path] = set()
    for raw_path in paths:
        path = raw_path.resolve()
        direct = detect_candidate(path)
        if direct:
            add_candidate(discovered, seen, direct)
            continue
        if not path.is_dir():
            continue
        discover_in_directory(path, discovered, seen)
    return discovered


def output_path(candidate: Candidate) -> Path:
    if candidate.kind == "iso":
        return candidate.path.with_suffix(".mkv")
    if candidate.kind == "bluray_root":
        return candidate.path / f"{candidate.path.name}.mkv"
    if candidate.kind == "bluray_stream":
        root = candidate.path.parent.parent
        return root / f"{root.name}.mkv"
    if candidate.kind == "m2ts_dir":
        return candidate.path / f"{candidate.path.name}.mkv"
    if candidate.kind == "mkv":
        return candidate.path
    raise RuntimeError(f"Unsupported candidate type: {candidate.kind}")


def file_duration(path: Path) -> float:
    result = run_command(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)], capture=True)
    payload = json.loads(result.stdout or "{}")
    duration = payload.get("format", {}).get("duration")
    try:
        return float(duration)
    except (TypeError, ValueError):
        return 0.0


def existing_outputs(candidate: Candidate) -> list[Path]:
    expected = output_path(candidate)
    if expected.exists():
        return [expected]

    search_dir: Path | None = None
    if candidate.kind == "iso":
        search_dir = candidate.path.parent
    elif candidate.kind == "bluray_root":
        search_dir = candidate.path
    elif candidate.kind == "bluray_stream":
        search_dir = candidate.path.parent.parent
    elif candidate.kind == "m2ts_dir":
        search_dir = candidate.path

    if search_dir is not None:
        return sorted(path for path in search_dir.glob("*.mkv") if path.is_file() and ".subtitlefix" not in path.name.lower())
    return []


def resolve_existing_output(candidate: Candidate) -> Path | None:
    candidates = existing_outputs(candidate)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    ranked = sorted(candidates, key=lambda path: (file_duration(path), path.stat().st_size, path.name.lower()), reverse=True)
    return ranked[0]


def rip_source(candidate: Candidate, *, force: bool = False, cleanup_legacy_titles: bool = False) -> Path:
    result_path = output_path(candidate)
    if candidate.kind == "mkv":
        return result_path
    command = [resolve_helper_command("rip-media", "rip_media.py"), "process"]
    if force:
        command.append("--force")
    if cleanup_legacy_titles:
        command.append("--cleanup-legacy-titles")
    command.append(str(candidate.path))
    run_command(command)
    if not result_path.exists():
        existing_output = resolve_existing_output(candidate)
        if existing_output is None:
            raise RuntimeError(f"Expected MKV was not created: {result_path}")
        log(f"Using existing MKV for {candidate.path}: {existing_output}")
        return existing_output
    return result_path


def ffprobe_streams(video_path: Path) -> list[dict]:
    result = run_command(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(video_path)], capture=True)
    payload = json.loads(result.stdout or "{}")
    return payload.get("streams", [])


def english_internal_tracks(video_path: Path) -> list[dict]:
    tracks: list[dict] = []
    for stream in ffprobe_streams(video_path):
        if stream.get("codec_type") != "subtitle":
            continue
        language = normalize_language(stream.get("tags", {}).get("language"))
        if language == "eng":
            tracks.append({"index": stream["index"], "codec_name": stream.get("codec_name", "unknown"), "language": language})
    return tracks


def unknown_internal_tracks(video_path: Path) -> list[dict]:
    tracks: list[dict] = []
    for stream in ffprobe_streams(video_path):
        if stream.get("codec_type") != "subtitle":
            continue
        codec_name = stream.get("codec_name", "unknown")
        if codec_name not in TEXT_SUBTITLE_CODECS and codec_name not in IMAGE_SUBTITLE_CODECS:
            continue
        language = normalize_language(stream.get("tags", {}).get("language"))
        if language in UNKNOWN_LANGUAGE_CODES:
            tracks.append({"index": stream["index"], "codec_name": codec_name, "language": language})
    return tracks


def external_subtitle_candidates(video_path: Path) -> list[Path]:
    parent = video_path.parent
    candidates: list[tuple[int, Path]] = []
    for child in sorted(parent.iterdir()):
        if not is_external_subtitle_candidate(video_path, child):
            continue
        candidates.append((external_subtitle_score(video_path, child), child))
    if len(candidates) == 1 and candidates[0][0] == 0:
        score, path = candidates[0]
        candidates[0] = (score + 50, path)
    candidates.sort(key=lambda item: (-item[0], item[1].name.lower()))
    return [path for _, path in candidates if _ > 0]


def is_external_subtitle_candidate(video_path: Path, subtitle_path: Path) -> bool:
    if not subtitle_path.is_file() or subtitle_path.suffix.lower() not in TEXT_SUBTITLE_SUFFIXES:
        return False
    if subtitle_path.name.startswith(video_path.stem) and subtitle_path.name.endswith(".synced.srt"):
        return False
    lowered_name = subtitle_path.name.lower()
    return ".eng.clone." not in lowered_name and ".eng.2." not in lowered_name and ".translated" not in lowered_name


def external_subtitle_score(video_path: Path, subtitle_path: Path) -> int:
    score = 0
    if ENGLISH_HINT_PATTERN.search(subtitle_path.name):
        score += 100
    if subtitle_path.stem.startswith(video_path.stem):
        score += 20
    if subtitle_path.suffix.lower() == ".srt":
        score += 10
    return score


def english_sidecar_path(video_path: Path) -> Path:
    return video_path.with_name(f"{video_path.stem}.en.srt")


def review_marker_path(video_path: Path) -> Path:
    return video_path.with_name(f"{video_path.stem}.needs-english-subs-review.txt")


def sync_external_subtitle(video_path: Path, subtitle_path: Path, output_path: Path) -> Path:
    temp_output = output_path.with_name(f"{output_path.stem}.synctmp{output_path.suffix}")
    run_command(["ffsubsync", str(video_path), "-i", str(subtitle_path), "-o", str(temp_output)])
    temp_output.replace(output_path)
    return output_path


def extract_text_subtitle(video_path: Path, stream_index: int, output_path: Path) -> Path:
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_path), "-map", f"0:{stream_index}", str(output_path)])
    return output_path


def extract_pgs_subtitle(video_path: Path, stream_index: int, output_path: Path) -> Path:
    run_command(["mkvextract", "tracks", str(video_path), f"{stream_index}:{output_path}"])
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


def extract_internal_track_text(video_path: Path, track: dict, workdir: Path) -> Path:
    base_name = f"stream-{track['index']}.{track['language'] or 'und'}"
    if track["codec_name"] in IMAGE_SUBTITLE_CODECS:
        sup_path = workdir / f"{base_name}.sup"
        raw_srt = workdir / f"{base_name}.ocr.srt"
        extract_pgs_subtitle(video_path, track["index"], sup_path)
        return ocr_pgs_subtitle(sup_path, raw_srt)
    if track["codec_name"] in TEXT_SUBTITLE_CODECS:
        return extract_text_subtitle(video_path, track["index"], workdir / f"{base_name}.srt")
    raise RuntimeError(f"Unsupported subtitle codec: {track['codec_name']}")


def subtitle_text_lines(subtitle_path: Path, *, max_lines: int = 40) -> list[str]:
    lines: list[str] = []
    with subtitle_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line.isdigit() or "-->" in line:
                continue
            lines.append(line)
            if len(lines) >= max_lines:
                break
    return lines


def looks_like_english_subtitle(subtitle_path: Path) -> bool:
    lines = subtitle_text_lines(subtitle_path)
    if len(lines) < 3:
        return False
    sample = " ".join(lines)
    letters = sum(1 for character in sample if character.isalpha())
    ascii_letters = sum(1 for character in sample if character.isascii() and character.isalpha())
    if letters == 0 or ascii_letters / letters < 0.85:
        return False
    words = re.findall(r"[A-Za-z']+", sample.lower())
    english_hits = sum(1 for word in words if word in ENGLISH_COMMON_WORDS)
    return english_hits >= 4


def sync_internal_track(video_path: Path, track: dict, output_path: Path) -> Path:
    workdir = video_path.parent / f"{video_path.stem}.subs"
    workdir.mkdir(parents=True, exist_ok=True)
    source_text = extract_internal_track_text(video_path, track, workdir)
    return sync_external_subtitle(video_path, source_text, output_path)


def sync_internal_english(video_path: Path, track: dict, output_path: Path) -> Path:
    return sync_internal_track(video_path, track, output_path)


def sync_unknown_english_track(video_path: Path, output_path: Path) -> Path | None:
    workdir = video_path.parent / f"{video_path.stem}.subs"
    workdir.mkdir(parents=True, exist_ok=True)
    for track in unknown_internal_tracks(video_path):
        source_text = extract_internal_track_text(video_path, track, workdir)
        if looks_like_english_subtitle(source_text):
            log(f"Using OCR/text-detected English subtitle track: {track['index']} ({track['codec_name']}, {track['language']})")
            return sync_external_subtitle(video_path, source_text, output_path)
    return None


def ensure_english_subtitle(video_path: Path) -> Path | None:
    final_sidecar = english_sidecar_path(video_path)
    review_marker = review_marker_path(video_path)
    external_candidates = external_subtitle_candidates(video_path)
    if external_candidates:
        source_subtitle = external_candidates[0]
        log(f"Using external English subtitle candidate: {source_subtitle}")
        result = sync_external_subtitle(video_path, source_subtitle, final_sidecar)
        review_marker.unlink(missing_ok=True)
        return result
    internal_tracks = english_internal_tracks(video_path)
    if internal_tracks:
        source_track = internal_tracks[0]
        log(f"Using internal English subtitle track: {source_track['index']} ({source_track['codec_name']})")
        result = sync_internal_english(video_path, source_track, final_sidecar)
        review_marker.unlink(missing_ok=True)
        return result
    detected_english = sync_unknown_english_track(video_path, final_sidecar)
    if detected_english is not None:
        review_marker.unlink(missing_ok=True)
        return detected_english
    review_marker.write_text(
        "No syncable English subtitle was found automatically.\nLook for an English-subtitled release or handle this title manually.\n",
        encoding="utf-8",
    )
    log(f"No English subtitle source found for {video_path}; wrote review marker {review_marker}")
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create feature MKVs from raw disc sources and generate a clean synced English sidecar when possible")
    parser.add_argument("mode", choices=["process", "scan"], help="Process explicit targets or recursively scan watch folders")
    parser.add_argument("paths", nargs="+", help="Input files or directories to process")
    parser.add_argument("--force", action="store_true", help="Ignore rip markers and reprocess matching sources")
    parser.add_argument("--cleanup-legacy-titles", action="store_true", help="Remove old MakeMKV-style title outputs like *_t00.mkv after a successful ISO re-rip")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    requested_paths = [Path(item).resolve() for item in args.paths]
    if args.mode == "scan":
        candidates = discover_candidates(requested_paths)
    else:
        candidates = []
        for path in requested_paths:
            candidate = detect_candidate(path)
            if candidate is None:
                raise RuntimeError(f"Unsupported input: {path}")
            candidates.append(candidate)
    if not candidates:
        log("No candidate media sources found")
        return 0
    exit_code = 0
    for candidate in candidates:
        try:
            log(f"Ingesting {candidate.kind}: {candidate.path}")
            video_path = rip_source(candidate, force=args.force, cleanup_legacy_titles=args.cleanup_legacy_titles)
            subtitle_path = ensure_english_subtitle(video_path)
            if subtitle_path is not None:
                log(f"Ready: {video_path} + {subtitle_path}")
                emit_result("ready", candidate.path, video_path, subtitle_path)
            else:
                log(f"Ready for manual subtitle review: {video_path}")
                emit_result("review", candidate.path, video_path, review_marker_path(video_path))
        except Exception as exc:
            exit_code = 1
            log(f"Failed ingest for {candidate.path}: {exc}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())