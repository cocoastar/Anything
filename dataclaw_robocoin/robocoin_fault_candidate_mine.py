#!/usr/bin/env python3
"""
Mine full-library RoboCOIN candidates for synthetic robot-fault generation.

This script does not decide the semantic fault frame. It only builds safe,
diverse source-video windows from the canonical head camera. The API later
chooses freeze / recall / recovery frames from sampled evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


DEFAULT_INPUT_ROOT = "/mnt/nas-data-1/yangyandan/lerobot_data/RoboCOIN"
HEAD_VIEW_DIR = "observation.images.cam_high_rgb"
FAULT_TYPE = "approach_stall"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mine approach-stall candidate windows from RoboCOIN videos.")
    parser.add_argument("--input-root", default=DEFAULT_INPUT_ROOT, help="RoboCOIN root or one dataset root")
    parser.add_argument("--output-root", default="./fault_candidate_pool", help="Output directory")
    parser.add_argument("--output-jsonl", default="", help="Optional explicit candidate jsonl path")
    parser.add_argument("--view-dir", default=HEAD_VIEW_DIR, help="Camera directory name to scan")
    parser.add_argument("--obs-sec", default=2.0, type=float, help="Observed pre-stall context length")
    parser.add_argument("--future-sec", default=2.0, type=float, help="Visible successful continuation length")
    parser.add_argument(
        "--anchor-fractions",
        default="0.50,0.35,0.65",
        help="Comma-separated episode positions used as candidate t_query anchors",
    )
    parser.add_argument("--samples-per-episode", default=1, type=int, help="How many anchors to keep per episode")
    parser.add_argument("--min-total-sec", default=4.0, type=float, help="Skip videos shorter than this")
    parser.add_argument("--limit-datasets", default=0, type=int, help="Only process first N datasets")
    parser.add_argument("--limit-episodes-per-dataset", default=0, type=int, help="Cap episodes per dataset")
    parser.add_argument("--max-candidates-per-dataset", default=0, type=int, help="Cap candidates per dataset")
    parser.add_argument("--target-count", default=0, type=int, help="Global cap after mining and optional shuffle")
    parser.add_argument(
        "--selection-strategy",
        default="round_robin",
        choices=["round_robin", "shuffle"],
        help="How to select candidates when --target-count is set",
    )
    parser.add_argument("--shuffle", action="store_true", help="Shuffle episodes/candidates before caps")
    parser.add_argument("--seed", default=0, type=int, help="Random seed for --shuffle")
    parser.add_argument("--workers", default=8, type=int, help="Parallel video probe workers")
    parser.add_argument("--ffprobe-bin", default="", help="Optional ffprobe path")
    parser.add_argument(
        "--count-frames-if-missing",
        action="store_true",
        help="Use slower ffprobe -count_frames when metadata lacks nb_frames",
    )
    return parser.parse_args()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def resolve_ffprobe_bin(preferred: str = "") -> str:
    candidates = []
    if preferred:
        candidates.append(preferred)
    env_ffprobe = os.environ.get("ROBOCOIN_FFPROBE_BIN", "").strip()
    if env_ffprobe:
        candidates.append(env_ffprobe)
    candidates.extend(
        [
            "/usr/bin/ffprobe",
            shutil.which("ffprobe") or "",
            "/opt/conda/bin/ffprobe",
        ]
    )
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if os.path.exists(candidate):
            return candidate
    raise RuntimeError("No usable ffprobe binary found")


def parse_rate(rate: Any) -> float | None:
    text = str(rate or "").strip()
    if not text or text == "0/0":
        return None
    if "/" in text:
        num_text, den_text = text.split("/", 1)
        try:
            num = float(num_text)
            den = float(den_text)
        except Exception:
            return None
        if den == 0:
            return None
        return num / den
    try:
        value = float(text)
    except Exception:
        return None
    return value if value > 0 else None


def probe_video_fast(video_path: Path, ffprobe_bin: str) -> dict[str, Any]:
    result = subprocess.run(
        [
            ffprobe_bin,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=avg_frame_rate,r_frame_rate,nb_frames,duration",
            "-show_entries", "format=duration",
            "-of", "json",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "ffprobe failed").strip())
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError("no_video_stream")
    stream = streams[0]
    fmt = payload.get("format") or {}

    fps = parse_rate(stream.get("avg_frame_rate")) or parse_rate(stream.get("r_frame_rate"))
    fps_source = "ffprobe:avg_frame_rate" if parse_rate(stream.get("avg_frame_rate")) else "ffprobe:r_frame_rate"
    if not fps or fps <= 0:
        raise RuntimeError("missing_fps")

    duration = None
    for raw_duration in (stream.get("duration"), fmt.get("duration")):
        try:
            duration = float(raw_duration)
        except Exception:
            continue
        if duration and duration > 0:
            break

    total_frames = None
    total_frames_source = ""
    raw_nb_frames = stream.get("nb_frames")
    if raw_nb_frames not in (None, "", "N/A"):
        try:
            total_frames = int(raw_nb_frames)
            total_frames_source = "ffprobe:nb_frames"
        except Exception:
            total_frames = None
    if total_frames is None and duration and duration > 0:
        total_frames = int(math.floor(duration * fps + 0.5))
        total_frames_source = "ffprobe:duration_x_fps"
    if not total_frames or total_frames <= 0:
        raise RuntimeError("missing_total_frames")

    return {
        "fps": round(float(fps), 6),
        "fps_source": fps_source,
        "duration_sec": round(float(duration), 6) if duration else None,
        "total_frames": int(total_frames),
        "total_frames_source": total_frames_source,
    }


def probe_frame_count_slow(video_path: Path, ffprobe_bin: str) -> int | None:
    result = subprocess.run(
        [
            ffprobe_bin,
            "-v", "error",
            "-select_streams", "v:0",
            "-count_frames",
            "-show_entries", "stream=nb_read_frames",
            "-of", "json",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
        streams = payload.get("streams") or []
        if not streams:
            return None
        raw = streams[0].get("nb_read_frames")
        return int(raw) if raw not in (None, "", "N/A") else None
    except Exception:
        return None


def probe_video(video_path: Path, ffprobe_bin: str, count_frames_if_missing: bool) -> dict[str, Any]:
    info = probe_video_fast(video_path, ffprobe_bin)
    if count_frames_if_missing and info.get("total_frames_source") == "ffprobe:duration_x_fps":
        counted = probe_frame_count_slow(video_path, ffprobe_bin)
        if counted:
            info["total_frames"] = counted
            info["total_frames_source"] = "ffprobe:nb_read_frames"
    return info


def parse_anchor_fractions(value: str) -> list[float]:
    fractions = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            fraction = float(part)
        except Exception:
            continue
        if 0.0 < fraction < 1.0:
            fractions.append(fraction)
    if not fractions:
        raise RuntimeError("No valid --anchor-fractions")
    return fractions


def discover_head_videos(input_root: Path, view_dir: str) -> list[Path]:
    if input_root.name == view_dir:
        return sorted(input_root.glob("episode_*.mp4"))
    videos = sorted(input_root.glob(f"videos/chunk-*/{view_dir}/episode_*.mp4"))
    if videos:
        return videos
    return sorted(input_root.glob(f"*/videos/chunk-*/{view_dir}/episode_*.mp4"))


def dataset_name_from_video(video_path: Path) -> str:
    try:
        return video_path.parents[3].name
    except Exception:
        return ""


def dataset_root_from_video(video_path: Path) -> str:
    try:
        return str(video_path.parents[3].resolve())
    except Exception:
        return ""


def episode_index_from_video(video_path: Path) -> int:
    match = re.search(r"episode_(\d+)\.mp4$", video_path.name)
    return int(match.group(1)) if match else -1


def episode_chunk_from_video(video_path: Path) -> int:
    chunk_name = video_path.parents[1].name
    match = re.search(r"chunk-(\d+)$", chunk_name)
    return int(match.group(1)) if match else -1


def select_videos(video_paths: list[Path], args: argparse.Namespace) -> list[Path]:
    grouped: dict[str, list[Path]] = defaultdict(list)
    for video_path in video_paths:
        grouped[dataset_name_from_video(video_path)].append(video_path)

    dataset_names = sorted(grouped)
    if args.limit_datasets > 0:
        dataset_names = dataset_names[: args.limit_datasets]

    rng = random.Random(args.seed)
    selected = []
    for dataset_name in dataset_names:
        paths = sorted(grouped[dataset_name])
        if args.shuffle:
            rng.shuffle(paths)
        if args.limit_episodes_per_dataset > 0:
            paths = paths[: args.limit_episodes_per_dataset]
        selected.extend(paths)
    return selected


def build_candidates_for_video(
    video_path: Path,
    args: argparse.Namespace,
    anchor_fractions: list[float],
    ffprobe_bin: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        probe = probe_video(video_path, ffprobe_bin, bool(args.count_frames_if_missing))
    except Exception as exc:
        return [], {
            "video_path": str(video_path.resolve()),
            "status": "probe_error",
            "error": str(exc),
        }

    fps = float(probe["fps"])
    total_frames = int(probe["total_frames"])
    min_total_frames = max(1, int(round(float(args.min_total_sec) * fps)))
    obs_frames = max(2, int(round(float(args.obs_sec) * fps)))
    future_frames = max(2, int(round(float(args.future_sec) * fps)))
    if total_frames < min_total_frames or total_frames < obs_frames + future_frames + 2:
        return [], {
            "video_path": str(video_path.resolve()),
            "status": "too_short",
            "total_frames": total_frames,
            "fps": fps,
        }

    dataset_name = dataset_name_from_video(video_path)
    dataset_root = dataset_root_from_video(video_path)
    episode_index = episode_index_from_video(video_path)
    episode_chunk = episode_chunk_from_video(video_path)

    anchors = []
    for fraction in anchor_fractions:
        t_query = int(round((total_frames - 1) * fraction))
        obs_start = t_query - obs_frames + 1
        obs_end = t_query
        future_start = t_query + 1
        future_end = t_query + future_frames
        if obs_start < 0 or future_end >= total_frames:
            continue
        anchors.append((fraction, t_query, obs_start, obs_end, future_start, future_end))

    if args.samples_per_episode > 0:
        anchors = anchors[: args.samples_per_episode]

    if not anchors:
        return [], {
            "video_path": str(video_path.resolve()),
            "status": "no_valid_anchor",
            "total_frames": total_frames,
            "fps": fps,
            "fps_source": probe["fps_source"],
            "total_frames_source": probe["total_frames_source"],
        }

    candidates = []
    for anchor_idx, (fraction, t_query, obs_start, obs_end, future_start, future_end) in enumerate(anchors):
        candidate_id = (
            f"{dataset_name}__ep{episode_index:06d}__{FAULT_TYPE}_candidate"
            f"__a{anchor_idx:02d}__t{t_query:06d}"
        )
        candidates.append(
            {
                "candidate_schema_version": "robocoin_fault_candidate_v1",
                "candidate_id": candidate_id,
                "packet_id": candidate_id,
                "fault_type": FAULT_TYPE,
                "dataset_name": dataset_name,
                "dataset_root": dataset_root,
                "episode_index": episode_index,
                "episode_chunk": episode_chunk,
                "source_view": "head",
                "source_video_head": str(video_path.resolve()),
                "source_video_paths": {"head": str(video_path.resolve())},
                "fps": fps,
                "fps_source": probe["fps_source"],
                "duration_sec": probe.get("duration_sec"),
                "total_frames": total_frames,
                "total_frames_source": probe["total_frames_source"],
                "anchor_fraction": fraction,
                "t_query": t_query,
                "obs_range": [obs_start, obs_end],
                "future_range": [future_start, future_end],
                "task_family": f"{FAULT_TYPE}_candidate",
                "quality_tier": "video_probe_ok",
            }
        )

    return candidates, {
        "video_path": str(video_path.resolve()),
        "status": "ok",
        "candidate_count": len(candidates),
        "total_frames": total_frames,
        "fps": fps,
        "fps_source": probe["fps_source"],
        "total_frames_source": probe["total_frames_source"],
    }


def cap_candidates_per_dataset(candidates: list[dict[str, Any]], cap: int, shuffle: bool, seed: int) -> list[dict[str, Any]]:
    if cap <= 0:
        return candidates
    rng = random.Random(seed)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate["dataset_name"]].append(candidate)
    selected = []
    for dataset_name in sorted(grouped):
        rows = sorted(grouped[dataset_name], key=lambda row: row["candidate_id"])
        if shuffle:
            rng.shuffle(rows)
        selected.extend(rows[:cap])
    return selected


def select_candidates_round_robin(
    candidates: list[dict[str, Any]],
    target_count: int,
    shuffle: bool,
    seed: int,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate["dataset_name"]].append(candidate)

    dataset_names = sorted(grouped)
    for dataset_name in dataset_names:
        grouped[dataset_name] = sorted(grouped[dataset_name], key=lambda row: row["candidate_id"])
        if shuffle:
            rng.shuffle(grouped[dataset_name])
    if shuffle:
        rng.shuffle(dataset_names)

    selected = []
    while dataset_names:
        next_dataset_names = []
        for dataset_name in dataset_names:
            rows = grouped[dataset_name]
            if not rows:
                continue
            selected.append(rows.pop(0))
            if rows:
                next_dataset_names.append(dataset_name)
            if target_count > 0 and len(selected) >= target_count:
                return selected
        dataset_names = next_dataset_names
    return selected


def main() -> None:
    args = parse_args()
    input_root = Path(args.input_root).resolve()
    output_root = Path(args.output_root).resolve()
    ensure_dir(output_root)
    output_jsonl = Path(args.output_jsonl).resolve() if args.output_jsonl else output_root / "fault_candidate_windows.jsonl"
    summary_path = output_root / "fault_candidate_summary.json"
    ffprobe_bin = resolve_ffprobe_bin(args.ffprobe_bin)
    anchor_fractions = parse_anchor_fractions(args.anchor_fractions)

    all_videos = discover_head_videos(input_root, args.view_dir)
    selected_videos = select_videos(all_videos, args)

    print("=" * 100)
    print("RoboCOIN Fault Candidate Mine")
    print("input_root                 :", input_root)
    print("output_jsonl               :", output_jsonl)
    print("view_dir                   :", args.view_dir)
    print("ffprobe                    :", ffprobe_bin)
    print("videos_discovered          :", len(all_videos))
    print("videos_selected            :", len(selected_videos))
    print("obs_sec                    :", args.obs_sec)
    print("future_sec                 :", args.future_sec)
    print("anchor_fractions           :", anchor_fractions)
    print("samples_per_episode        :", args.samples_per_episode)
    print("shuffle                    :", args.shuffle)
    print("seed                       :", args.seed)
    print("=" * 100)

    candidates: list[dict[str, Any]] = []
    probe_summaries: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
        futures = [
            executor.submit(build_candidates_for_video, video_path, args, anchor_fractions, ffprobe_bin)
            for video_path in selected_videos
        ]
        for index, future in enumerate(as_completed(futures), start=1):
            rows, summary = future.result()
            candidates.extend(rows)
            probe_summaries.append(summary)
            if index % 500 == 0:
                print(f"  probed {index}/{len(selected_videos)} videos, candidates={len(candidates)}")

    candidates_before_caps = len(candidates)
    candidates = cap_candidates_per_dataset(
        candidates,
        cap=int(args.max_candidates_per_dataset),
        shuffle=bool(args.shuffle),
        seed=int(args.seed),
    )
    candidates_after_dataset_cap = len(candidates)

    if args.selection_strategy == "round_robin":
        candidates = select_candidates_round_robin(
            candidates,
            target_count=int(args.target_count),
            shuffle=bool(args.shuffle),
            seed=int(args.seed),
        )
    else:
        candidates = sorted(candidates, key=lambda row: row["candidate_id"])
        if args.shuffle:
            rng = random.Random(args.seed)
            rng.shuffle(candidates)
        if args.target_count > 0:
            candidates = candidates[: args.target_count]

    ensure_dir(output_jsonl.parent)
    with open(output_jsonl, "w", encoding="utf-8") as f:
        for row in candidates:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    status_counts = Counter(row.get("status", "unknown") for row in probe_summaries)
    dataset_counts = Counter(row["dataset_name"] for row in candidates)
    probe_error_examples = []
    for row in probe_summaries:
        if row.get("status") == "probe_error":
            probe_error_examples.append({
                "video_path": row.get("video_path", ""),
                "error": row.get("error", ""),
            })
            if len(probe_error_examples) >= 10:
                break
    summary = {
        "input_root": str(input_root),
        "output_jsonl": str(output_jsonl),
        "videos_discovered": len(all_videos),
        "videos_selected": len(selected_videos),
        "candidates_before_dataset_cap": candidates_before_caps,
        "candidates_after_dataset_cap": candidates_after_dataset_cap,
        "candidate_count": len(candidates),
        "dataset_count": len(dataset_counts),
        "probe_status_counts": dict(sorted(status_counts.items())),
        "probe_error_examples": probe_error_examples,
        "top_dataset_candidate_counts": dict(dataset_counts.most_common(20)),
        "settings": {
            "view_dir": args.view_dir,
            "obs_sec": args.obs_sec,
            "future_sec": args.future_sec,
            "anchor_fractions": anchor_fractions,
            "samples_per_episode": args.samples_per_episode,
            "min_total_sec": args.min_total_sec,
            "limit_datasets": args.limit_datasets,
            "limit_episodes_per_dataset": args.limit_episodes_per_dataset,
            "max_candidates_per_dataset": args.max_candidates_per_dataset,
            "target_count": args.target_count,
            "selection_strategy": args.selection_strategy,
            "shuffle": bool(args.shuffle),
            "seed": args.seed,
        },
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("done")
    print("candidate_count            :", len(candidates))
    print("dataset_count              :", len(dataset_counts))
    print("probe_status_counts        :", dict(sorted(status_counts.items())))
    print("output_jsonl               :", output_jsonl)
    print("summary                    :", summary_path)


if __name__ == "__main__":
    main()
