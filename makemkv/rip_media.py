#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


RIPDONE_SUFFIX = ".ripdone"
LEGACY_TITLE_PATTERN = re.compile(r"_t\d+$", re.IGNORECASE)

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


def run_command(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    log("$ " + " ".join(command))
    return subprocess.run(command, check=True, text=True, capture_output=capture)


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def file_duration(path: Path) -> float:
    result = run_command(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture=True,
    )
    payload = json.loads(result.stdout or "{}")
    duration = payload.get("format", {}).get("duration")
    try:
        return float(duration)
    except (TypeError, ValueError):
        return 0.0


def ffprobe_streams(path: Path) -> list[dict]:
    result = run_command(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture=True,
    )
    payload = json.loads(result.stdout or "{}")
    return payload.get("streams", [])


def normalize_language(value: str | None) -> str:
    if not value:
        return "und"
    value = value.strip().lower()
    aliases = {
        "en": "eng",
        "english": "eng",
        "zh": "zho",
        "chi": "zho",
        "cn": "zho",
        "chinese": "zho",
        "jp": "jpn",
        "ja": "jpn",
        "kr": "kor",
        "ko": "kor",
        "es": "spa",
        "fr": "fra",
        "de": "deu",
        "it": "ita",
    }
    return aliases.get(value, value)


def subtitle_streams(path: Path) -> list[dict]:
    tracks = []
    for stream in ffprobe_streams(path):
        if stream.get("codec_type") != "subtitle":
            continue
        language = normalize_language(stream.get("tags", {}).get("language"))
        tracks.append({"index": stream["index"], "language": language, "title": stream.get("tags", {}).get("title", "")})
    return tracks


def pick_best_feature(candidates: Iterable[Path], *, feature_min_seconds: int) -> Path:
    scored: list[tuple[int, float, int, Path]] = []
    for candidate in candidates:
        duration = file_duration(candidate)
        size = candidate.stat().st_size
        meets_threshold = 1 if duration >= feature_min_seconds else 0
        scored.append((meets_threshold, duration, size, candidate))
        log(f"Candidate feature: {candidate.name} duration={duration:.1f}s size={size}")

    if not scored:
        raise RuntimeError("No MKV files were generated to choose from")

    scored.sort(reverse=True)
    best = scored[0][3]
    log(f"Selected feature title: {best.name}")
    return best


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


def discover_candidates(paths: list[Path]) -> list[Candidate]:
    discovered: list[Candidate] = []
    seen: set[Path] = set()

    for raw_path in paths:
        path = raw_path.resolve()
        direct = detect_candidate(path)
        if direct:
            if direct.path not in seen:
                discovered.append(direct)
                seen.add(direct.path)
            continue

        if not path.is_dir():
            continue

        for root, dirs, files in os.walk(path):
            current = Path(root)

            if "BDMV" in dirs:
                if current not in seen:
                    discovered.append(Candidate("bluray_root", current))
                    seen.add(current)
                dirs[:] = []
                continue

            if current.name == "STREAM" and current.parent.name == "BDMV":
                dirs[:] = []
                continue

            if any(file_name.lower().endswith(".iso") and not should_skip_iso(current / file_name) for file_name in files):
                for file_name in sorted(files):
                    candidate_file = current / file_name
                    if candidate_file.suffix.lower() != ".iso" or should_skip_iso(candidate_file):
                        continue
                    if candidate_file not in seen:
                        discovered.append(Candidate("iso", candidate_file))
                        seen.add(candidate_file)

            if any(file_name.lower().endswith(".m2ts") for file_name in files):
                if current not in seen:
                    discovered.append(Candidate("m2ts_dir", current))
                    seen.add(current)
                dirs[:] = []

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


def ripdone_path(candidate: Candidate) -> Path | None:
    if candidate.kind == "iso":
        return candidate.path.with_suffix(RIPDONE_SUFFIX)
    if candidate.kind == "bluray_root":
        return candidate.path / RIPDONE_SUFFIX
    if candidate.kind == "bluray_stream":
        return candidate.path.parent.parent / RIPDONE_SUFFIX
    if candidate.kind == "m2ts_dir":
        return candidate.path / ".m2ts.ripdone"
    return None


def legacy_title_prefixes(candidate: Candidate) -> list[str]:
    if candidate.kind != "iso":
        return []

    base_name = candidate.path.stem
    prefixes = [base_name]
    if base_name.lower().endswith(" dvd"):
        prefixes.append(base_name[:-4])
    return [prefix for prefix in prefixes if prefix]


def is_legacy_title_file(path: Path, prefixes: list[str]) -> bool:
    stem = path.stem
    suffixes = path.suffixes
    if suffixes and suffixes[-1].lower() == ".srt" and len(suffixes) >= 2:
        stem = Path(stem).stem
    if suffixes and suffixes[-1].lower() in {".nfo", ".xml"}:
        stem = Path(stem).stem
    if not LEGACY_TITLE_PATTERN.search(stem):
        return False
    return any(stem.startswith(prefix) for prefix in prefixes)


def cleanup_legacy_iso_outputs(candidate: Candidate, keep_path: Path) -> list[Path]:
    if candidate.kind != "iso":
        return []
    removed: list[Path] = []
    prefixes = legacy_title_prefixes(candidate)
    if not prefixes:
        return removed
    allowed_suffixes = {".mkv", ".srt", ".nfo", ".xml"}
    for child in sorted(candidate.path.parent.iterdir()):
        if child == keep_path or not child.is_file():
            continue
        if child.suffix.lower() not in allowed_suffixes:
            continue
        if not is_legacy_title_file(child, prefixes):
            continue
        child.unlink(missing_ok=True)
        removed.append(child)
    return removed


def select_longest_bluray_playlist(bluray_root: Path) -> str:
    playlist_dir = bluray_root / "BDMV" / "PLAYLIST"
    best_playlist = ""
    best_duration = 0.0
    for playlist_file in sorted(playlist_dir.glob("*.mpls")):
        playlist_number = playlist_file.stem
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-playlist", playlist_number, "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", f"bluray:{bluray_root}"],
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            continue
        try:
            duration = float((result.stdout or "0").strip())
        except ValueError:
            continue
        if duration > best_duration:
            best_duration = duration
            best_playlist = playlist_number
    if not best_playlist:
        raise RuntimeError(f"Could not determine a playable Blu-ray playlist for {bluray_root}")
    log(f"Selected Blu-ray playlist {best_playlist} ({best_duration:.1f}s)")
    return best_playlist


def ffmpeg_copy(input_args: list[str], output_path_value: Path) -> None:
    first_try = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *input_args, "-map", "0", "-c", "copy", str(output_path_value)]
    try:
        run_command(first_try)
        return
    except subprocess.CalledProcessError:
        if output_path_value.exists():
            output_path_value.unlink()
    second_try = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *input_args, "-map", "0", "-c:v", "copy", "-c:a", "flac", "-c:s", "copy", str(output_path_value)]
    run_command(second_try)


def process_iso(candidate: Candidate, *, rip_min_length: int, feature_min_seconds: int) -> Path:
    source = candidate.path
    final_output = output_path(candidate)
    temp_dir = Path(tempfile.mkdtemp(prefix="makemkv-", dir=str(final_output.parent)))
    try:
        run_command(["makemkvcon", "mkv", f"file:{source}", "all", str(temp_dir), f"--minlength={rip_min_length}"])
        generated = sorted(temp_dir.glob("*.mkv"))
        selected = pick_best_feature(generated, feature_min_seconds=feature_min_seconds)
        if final_output.exists():
            final_output.unlink()
        shutil.move(str(selected), str(final_output))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    log(f"Final feature MKV: {final_output}")
    return final_output


def process_bluray(candidate: Candidate) -> Path:
    if candidate.kind == "bluray_root":
        bluray_root = candidate.path
    else:
        bluray_root = candidate.path.parent.parent

    final_output = output_path(candidate)
    playlist = select_longest_bluray_playlist(bluray_root)
    ffmpeg_copy(["-playlist", playlist, "-i", f"bluray:{bluray_root}"], final_output)
    return final_output


def process_m2ts_directory(candidate: Candidate) -> Path:
    source_dir = candidate.path
    if candidate.kind == "bluray_stream":
        return process_bluray(candidate)

    segments = sorted(source_dir.glob("*.m2ts"))
    if not segments:
        raise RuntimeError(f"No M2TS files found in {source_dir}")

    final_output = output_path(candidate)
    if len(segments) == 1:
        ffmpeg_copy(["-i", str(segments[0])], final_output)
        return final_output

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        concat_file = Path(handle.name)
        for segment in segments:
            escaped_segment = segment.as_posix().replace("'", "'\\''")
            handle.write(f"file '{escaped_segment}'\n")

    try:
        ffmpeg_copy(["-f", "concat", "-safe", "0", "-i", str(concat_file)], final_output)
    finally:
        concat_file.unlink(missing_ok=True)

    return final_output


def choose_clone_source(tracks: list[dict], preferred_languages: list[str]) -> dict | None:
    non_english = [track for track in tracks if track["language"] != "eng"]
    if not non_english:
        return None
    for language in preferred_languages:
        for track in non_english:
            if track["language"] == language:
                return track
    return non_english[0]


def clone_subtitle_to_english(path: Path, *, clone_policy: str, preferred_languages: list[str]) -> None:
    tracks = subtitle_streams(path)
    if not tracks:
        log(f"No subtitle tracks found in {path}")
        return
    english_tracks = [track for track in tracks if track["language"] == "eng"]
    source_track = choose_clone_source(tracks, preferred_languages)
    if clone_policy == "never" or source_track is None:
        return
    if clone_policy == "missing" and english_tracks:
        return
    subtitle_output_index = len(tracks)
    source_language = source_track["language"]
    temp_output = path.with_name(f"{path.stem}.subtitlefix{path.suffix}")
    disposition = "0" if english_tracks else "default"
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0", "-map", f"0:{source_track['index']}", "-c", "copy",
        f"-metadata:s:s:{subtitle_output_index}", "language=eng",
        f"-metadata:s:s:{subtitle_output_index}", f"title=English (fallback clone from {source_language})",
        f"-disposition:s:{subtitle_output_index}", disposition, str(temp_output),
    ])
    temp_output.replace(path)
    log(f"Cloned subtitle track {source_track['index']} ({source_language}) to English in {path}")


def process_candidate(candidate: Candidate, *, rip_min_length: int, feature_min_seconds: int, clone_policy: str, preferred_languages: list[str], force: bool, cleanup_legacy_titles: bool) -> Path:
    marker = ripdone_path(candidate)
    if marker and marker.exists() and not force:
        log(f"Skipping {candidate.path}; marker exists at {marker}")
        return output_path(candidate)
    if force and marker and marker.exists():
        log(f"Force reprocessing {candidate.path}; ignoring marker {marker}")
    if candidate.kind == "iso":
        result = process_iso(candidate, rip_min_length=rip_min_length, feature_min_seconds=feature_min_seconds)
    elif candidate.kind in {"bluray_root", "bluray_stream"}:
        result = process_bluray(candidate)
    elif candidate.kind == "m2ts_dir":
        result = process_m2ts_directory(candidate)
    elif candidate.kind == "mkv":
        result = candidate.path
    else:
        raise RuntimeError(f"Unsupported candidate type: {candidate.kind}")
    clone_subtitle_to_english(result, clone_policy=clone_policy, preferred_languages=preferred_languages)
    if cleanup_legacy_titles:
        removed = cleanup_legacy_iso_outputs(candidate, result)
        if removed:
            log("Removed legacy ISO outputs: " + ", ".join(str(path.name) for path in removed))
    if marker:
        marker.touch()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rip ISO, Blu-ray, or M2TS sources into a single feature MKV")
    parser.add_argument("mode", choices=["process", "scan"], help="Process explicit paths or recursively scan folders for candidates")
    parser.add_argument("paths", nargs="+", help="Input files or directories to process")
    parser.add_argument("--rip-min-length", type=int, default=env_int("RIP_MIN_LENGTH", 1200))
    parser.add_argument("--feature-min-seconds", type=int, default=env_int("FEATURE_MIN_SECONDS", 2400))
    parser.add_argument("--subtitle-clone-policy", choices=["always", "missing", "never"], default=os.getenv("SUBTITLE_CLONE_POLICY", "always").strip().lower() or "always")
    parser.add_argument("--subtitle-clone-langs", default=os.getenv("SUBTITLE_CLONE_LANGS", "zho,chi,cmn,yue,jpn,kor,spa,fre,fra,deu,ger,ita,und"))
    parser.add_argument("--force", action="store_true", help="Ignore rip markers and reprocess matching sources")
    parser.add_argument("--cleanup-legacy-titles", action="store_true", help="Remove old MakeMKV-style title outputs like *_t00.mkv after a successful ISO re-rip")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    requested_paths = [Path(item) for item in args.paths]
    if args.mode == "scan":
        candidates = discover_candidates(requested_paths)
    else:
        candidates = []
        for requested_path in requested_paths:
            candidate = detect_candidate(requested_path.resolve())
            if candidate is None:
                parser.error(f"Unsupported input: {requested_path}")
            candidates.append(candidate)
    if not candidates:
        log("No candidate media sources found")
        return 0
    preferred_languages = [item.strip().lower() for item in args.subtitle_clone_langs.split(",") if item.strip()]
    exit_code = 0
    for candidate in candidates:
        log(f"Processing {candidate.kind}: {candidate.path}")
        try:
            result = process_candidate(candidate, rip_min_length=args.rip_min_length, feature_min_seconds=args.feature_min_seconds, clone_policy=args.subtitle_clone_policy, preferred_languages=preferred_languages, force=args.force, cleanup_legacy_titles=args.cleanup_legacy_titles)
            log(f"Completed: {result}")
        except Exception as exc:
            exit_code = 1
            log(f"Failed: {candidate.path}: {exc}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())