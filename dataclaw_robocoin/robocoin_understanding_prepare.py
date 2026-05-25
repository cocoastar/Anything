#!/usr/bin/env python3
"""
RoboCOIN Understanding Prepare
==============================
从 episode 级别的 head 视频中均匀采样全局帧，并为 3 类理解任务构造 API 请求：
  1. describe_current_subtask
  2. predict_next_primary_subtask
  3. interleaved_tutorial

当前实现沿用 OpenAI-compatible image_url 输入格式，不直接上传原始视频文件，
而是对整段 episode 采样 20 张左右关键帧，交给模型自行选择 input/output 段与关键帧。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_EPISODES_JSONL = "/mnt/nas-data-6/lijiangyang.ljy/stall_batch_v2/fault_candidate_windows.jsonl"
TASK_FAMILIES = (
    "describe_current_subtask",
    "predict_next_primary_subtask",
    "interleaved_tutorial",
)
DEFAULT_TASK_FAMILIES = TASK_FAMILIES
DEFAULT_SAMPLE_FRAME_COUNT = 20


SYSTEM_PROMPTS = {
    "describe_current_subtask": """You are authoring one embodied-video understanding training sample of type "describe_current_subtask".

You will receive evenly sampled head-camera frames from one successful robot manipulation episode.
Your job is to choose one short contiguous segment that clearly represents the robot's current primary subtask,
then write a concise question, answer, and short chain-of-thought style reasoning for that segment.

Return exactly one JSON object in this schema:
{
  "input_video": [<int>, <int>],
  "input_image": null,
  "output_video": null,
  "output_image": null,
  "question": <string>,
  "answer": <string>,
  "cot": [<string>, ...]
}

Field meaning:
- input_video: [start_frame, end_frame], a short contiguous frame range inside the full episode that clearly captures the current subtask; prefer roughly 24-150 frames when possible
- input_image: always null for this task
- output_video: always null for this task
- output_image: always null for this task
- question: one natural English question asking what the robot is doing in the chosen segment
- answer: concise English description of the current primary subtask
- cot: a short list of concise English reasoning strings grounded in visible evidence from the chosen segment

Reasoning style requirements:
- Write question, answer, and cot as training supervision for embodied video understanding, not as dataset annotation commentary.
- Reason from visible evidence. The text should sound like describing what the robot is currently doing, not narrating the full episode from an outside annotator viewpoint.
- Do not mention frame numbers, timestamps, sampled-frame counts, or hidden metadata.
- Keep cot brief and useful. It should explain why the chosen segment corresponds to the current subtask.

Rules:
- Use only visible evidence from the sampled frames.
- The chosen input_video must stay inside the full episode range.
- Return only the JSON object. No markdown fences. No extra text.""",
    "predict_next_primary_subtask": """You are authoring one embodied-video understanding training sample of type "predict_next_primary_subtask".

You will receive evenly sampled head-camera frames from one successful robot manipulation episode.
Your job is to choose one context segment where the robot is still in the middle of execution and a clear next primary subtask remains later in the episode.
Then write a natural question, a pure-text answer describing the next primary subtask, and a short reasoning trace.

Return exactly one JSON object in this schema:
{
  "input_video": [<int>, <int>],
  "input_image": null,
  "output_video": null,
  "output_image": null,
  "question": <string>,
  "answer": <string>,
  "cot": [<string>, ...]
}

Field meaning:
- input_video: [start_frame, end_frame], a short contiguous context segment inside the full episode; it must end before the next primary subtask is completed; prefer roughly 24-150 frames when possible
- input_image: always null for this task
- output_video: always null for this task
- output_image: always null for this task
- question: one natural English question asking what the robot should do next
- answer: pure-text English description of the next primary subtask only; do not output image placeholders
- cot: a short list of concise English reasoning strings

Reasoning style requirements:
- The final training sample should only expose the chosen context segment as input.
- You may use the later visible sampled frames to infer the correct next subtask, but do not mention those later frames explicitly in question, answer, or cot.
- Do not mention frame numbers, timestamps, sampled-frame counts, or hidden metadata.
- Keep cot brief. It should explain why the current context implies that next subtask.

Rules:
- Use only visible evidence from the sampled frames.
- Do not choose a segment from the very end of the episode where no meaningful next primary subtask remains.
- The chosen input_video must stay inside the full episode range.
- output_video and output_image must both be null.
- Return only the JSON object. No markdown fences. No extra text.""",
    "interleaved_tutorial": """You are authoring one embodied-video tutorial training sample of type "interleaved_tutorial".

You will receive evenly sampled head-camera frames from one successful robot manipulation episode.
Your job is to identify the overall task and produce a concise illustrated tutorial with interleaved text and images.

Return exactly one JSON object in this schema:
{
  "input_video": [<int>, <int>],
  "input_image": null,
  "output_video": null,
  "output_image": [<int>, <int>, ...],
  "question": <string>,
  "answer": <string>,
  "cot": [<string>, ...]
}

Field meaning:
- input_video: [start_frame, end_frame], the main frame range that the tutorial is based on; this can cover most or all of the episode
- input_image: always null for this task
- output_video: always null for this task
- output_image: 3 to 6 key frame indices inside input_video, in chronological order
- question: one natural English request for an illustrated step-by-step explanation of the robot task
- answer: concise English tutorial text containing exactly one <image> placeholder for each element in output_image, in the same order
- cot: a short list of concise English reasoning strings explaining the step structure

Reasoning style requirements:
- The answer should read like a short illustrated guide.
- Prefer step-wise text such as "Overall task: ... Step 1 ... <image> Step 2 ... <image>".
- Do not mention frame numbers, timestamps, sampled-frame counts, or hidden metadata.
- Keep cot brief. It should explain how the overall task is decomposed into a few key visual steps.

Rules:
- Use only visible evidence from the sampled frames.
- output_image must be in chronological order and every frame must lie inside input_video.
- output_video must be null.
- Return only the JSON object. No markdown fences. No extra text.""",
}


EXPECTED_OUTPUT_SCHEMAS = {
    "describe_current_subtask": {
        "input_video": "[start_frame, end_frame]",
        "input_image": None,
        "output_video": None,
        "output_image": None,
        "question": "English string",
        "answer": "English string",
        "cot": ["short English reasoning"],
    },
    "predict_next_primary_subtask": {
        "input_video": "[start_frame, end_frame]",
        "input_image": None,
        "output_video": None,
        "output_image": None,
        "question": "English string",
        "answer": "English string, pure text only",
        "cot": ["short English reasoning"],
    },
    "interleaved_tutorial": {
        "input_video": "[start_frame, end_frame]",
        "input_image": None,
        "output_video": None,
        "output_image": "[frame_index, ...] chronological",
        "question": "English string",
        "answer": "English string containing <image> placeholders",
        "cot": ["short English reasoning"],
    },
}


USER_REMINDERS = {
    "describe_current_subtask": (
        "Return the JSON object only. "
        "Remember: input_video must be a valid [start_frame, end_frame] list inside the full episode, "
        "all image/video output fields must be null, "
        "and the answer should describe the current primary subtask in that segment."
    ),
    "predict_next_primary_subtask": (
        "Return the JSON object only. "
        "Remember: input_video must be a valid [start_frame, end_frame] list inside the full episode, "
        "all image/video output fields must be null, "
        "and the answer must be pure-text English describing the next primary subtask only."
    ),
    "interleaved_tutorial": (
        "Return the JSON object only. "
        "Remember: input_video must be a valid [start_frame, end_frame] list inside the full episode, "
        "output_video must be null, "
        "output_image must contain 3-6 chronological key frame indices inside input_video, "
        "and the answer must contain exactly one <image> placeholder per output image."
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare RoboCOIN understanding/tutorial API requests.")
    parser.add_argument("--episodes-jsonl", default=DEFAULT_EPISODES_JSONL, help="Episode pool jsonl path")
    parser.add_argument("--output-root", default="./understanding_v1", help="Output directory")
    parser.add_argument("--limit", default=None, type=int, help="Only process first N unique episodes")
    parser.add_argument(
        "--task-families",
        default=",".join(DEFAULT_TASK_FAMILIES),
        help="Comma-separated task families to generate",
    )
    parser.add_argument("--packet-id-contains", default="", help="Optional substring filter for episode_id")
    parser.add_argument("--sample-frame-count", default=DEFAULT_SAMPLE_FRAME_COUNT, type=int, help="Sampled frames per episode")
    parser.add_argument(
        "--ffmpeg-bin",
        default="",
        help="Optional ffmpeg binary path. If empty, auto-detect an AV1-capable ffmpeg.",
    )
    parser.add_argument("--shuffle", action="store_true", help="Shuffle episodes before applying --limit")
    parser.add_argument("--seed", default=0, type=int, help="Random seed for --shuffle")
    return parser.parse_args()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def supports_av1_decode(ffmpeg_bin: str) -> bool:
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-decoders"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        text = ((result.stdout or "") + "\n" + (result.stderr or "")).lower()
        return any(token in text for token in ["libdav1d", "libaom-av1", " av1 "])
    except Exception:
        return False


def resolve_ffmpeg_bin(preferred: str = "") -> str:
    candidates = []
    if preferred:
        candidates.append(preferred)
    env_ffmpeg = os.environ.get("ROBOCOIN_FFMPEG_BIN", "").strip()
    if env_ffmpeg:
        candidates.append(env_ffmpeg)
    candidates.extend(
        [
            "/usr/bin/ffmpeg",
            shutil.which("ffmpeg") or "",
            "/opt/conda/bin/ffmpeg",
        ]
    )

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if os.path.exists(candidate) and supports_av1_decode(candidate):
            return candidate

    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if os.path.exists(candidate):
            return candidate

    raise RuntimeError("No usable ffmpeg binary found")


def even_sample_indices(total_count: int, sample_count: int) -> list[int]:
    if total_count <= 0 or sample_count <= 0:
        return []
    if sample_count >= total_count:
        return list(range(total_count))
    return [round(i * (total_count - 1) / (sample_count - 1)) for i in range(sample_count)]


def extract_frame_jpeg_from_video(
    video_path: str,
    frame_index: int,
    fps: float,
    ffmpeg_bin: str,
    max_side: int = 768,
) -> bytes | None:
    start_sec = frame_index / float(fps)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            [
                ffmpeg_bin, "-y",
                "-ss", f"{start_sec:.6f}",
                "-i", video_path,
                "-vframes", "1",
                "-vf", f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'",
                "-q:v", "3",
                tmp_path,
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0 or not os.path.exists(tmp_path):
            return None
        with open(tmp_path, "rb") as f:
            return f.read()
    except Exception:
        return None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def jpeg_to_data_url(jpeg_bytes: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode("ascii")


def sample_episode_frames(
    source_video_path: str,
    total_frames: int,
    fps: float,
    sample_count: int,
    ffmpeg_bin: str,
) -> list[dict[str, Any]]:
    frame_indices = even_sample_indices(total_frames, sample_count)
    sampled_frames = []
    for frame_index in frame_indices:
        jpeg_bytes = extract_frame_jpeg_from_video(
            source_video_path,
            frame_index,
            fps,
            ffmpeg_bin,
        )
        if jpeg_bytes is None:
            continue
        sampled_frames.append(
            {
                "frame_index": frame_index,
                "data_url": jpeg_to_data_url(jpeg_bytes),
                "payload_bytes": len(jpeg_bytes),
            }
        )
    return sampled_frames


def scan_episode_pool(episodes_jsonl: Path) -> list[dict[str, Any]]:
    episodes_by_video: dict[str, dict[str, Any]] = {}
    with open(episodes_jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue

            source_video_head = row.get("source_video_head") or row.get("source_video_paths", {}).get("head", "")
            if not source_video_head or not os.path.exists(source_video_head):
                continue

            total_frames = row.get("total_frames")
            fps = row.get("fps")
            if not isinstance(total_frames, int) or total_frames <= 2:
                continue
            if not isinstance(fps, (int, float)) or fps <= 0:
                continue

            dataset_name = row.get("dataset_name", "")
            episode_index = row.get("episode_index", -1)
            episode_id = f"{dataset_name}__ep{episode_index:06d}" if episode_index >= 0 else Path(source_video_head).stem

            record = {
                "episode_id": episode_id,
                "dataset_name": dataset_name,
                "episode_index": episode_index,
                "source_video_head": source_video_head,
                "fps": float(fps),
                "fps_source": row.get("fps_source", ""),
                "total_frames": int(total_frames),
                "total_frames_source": row.get("total_frames_source", ""),
                "source_view": row.get("source_view", "head"),
            }

            prev = episodes_by_video.get(source_video_head)
            if prev is None or record["episode_id"] < prev["episode_id"]:
                episodes_by_video[source_video_head] = record

    return sorted(episodes_by_video.values(), key=lambda item: item["episode_id"])


def build_user_message_content(
    episode_info: dict[str, Any],
    sampled_frames: list[dict[str, Any]],
    task_family: str,
) -> list[dict[str, Any]]:
    full_range = [0, episode_info["total_frames"] - 1]
    intro_text = (
        f"FULL_RANGE: [{full_range[0]}, {full_range[1]}]\n"
        f"FPS: {episode_info['fps']}\n"
        f"TOTAL_FRAMES: {episode_info['total_frames']}\n"
        f"SAMPLED_FRAMES_TOTAL: {len(sampled_frames)}\n"
        "Below are evenly sampled head-camera frames from one successful robot manipulation episode. "
        "Each label is the original frame index in the full episode."
    )

    content_parts: list[dict[str, Any]] = [{"type": "text", "text": intro_text}]
    for frame_info in sampled_frames:
        content_parts.append({"type": "text", "text": f"FRAME_INDEX={frame_info['frame_index']}"})
        content_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": frame_info["data_url"], "detail": "low"},
            }
        )

    content_parts.append({"type": "text", "text": USER_REMINDERS[task_family]})
    return content_parts


def build_request(
    episode_info: dict[str, Any],
    sampled_frames: list[dict[str, Any]],
    task_family: str,
) -> dict[str, Any]:
    request_id = f"understanding__{task_family}__{episode_info['episode_id']}"
    user_content_full = build_user_message_content(episode_info, sampled_frames, task_family)

    user_content_redacted = []
    for part in user_content_full:
        if part.get("type") == "image_url":
            url = part.get("image_url", {}).get("url", "")
            user_content_redacted.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"<redacted_data_url chars={len(url)}>",
                        "detail": part.get("image_url", {}).get("detail", "low"),
                    },
                }
            )
        else:
            user_content_redacted.append(part)

    messages_full = [
        {"role": "system", "content": SYSTEM_PROMPTS[task_family]},
        {"role": "user", "content": user_content_full},
    ]

    return {
        "request_id": request_id,
        "episode_id": episode_info["episode_id"],
        "task_family": task_family,
        "source_info": {
            "dataset_name": episode_info["dataset_name"],
            "episode_index": episode_info["episode_index"],
            "source_view": episode_info["source_view"],
            "source_video_head": episode_info["source_video_head"],
            "fps": episode_info["fps"],
            "fps_source": episode_info.get("fps_source", ""),
            "total_frames": episode_info["total_frames"],
            "total_frames_source": episode_info.get("total_frames_source", ""),
            "full_range": [0, episode_info["total_frames"] - 1],
        },
        "sampled_frame_indices": [frame["frame_index"] for frame in sampled_frames],
        "sampled_frame_count": len(sampled_frames),
        "request_payload": {
            "model": "",
            "messages": messages_full,
            "temperature": 0.2,
            "top_p": 1.0,
            "max_tokens": 8192,
            "response_format": {"type": "json_object"},
        },
        "request_payload_redacted": {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPTS[task_family]},
                {"role": "user", "content": user_content_redacted},
            ],
        },
        "expected_output_schema": EXPECTED_OUTPUT_SCHEMAS[task_family],
    }


def main() -> None:
    args = parse_args()
    episodes_jsonl = Path(args.episodes_jsonl).resolve()
    output_root = Path(args.output_root).resolve()
    ensure_dir(output_root)
    ffmpeg_bin = resolve_ffmpeg_bin(args.ffmpeg_bin)
    task_families = parse_csv_list(args.task_families)
    invalid = [item for item in task_families if item not in TASK_FAMILIES]
    if invalid:
        raise ValueError(f"Unsupported task families: {invalid}")

    output_path = output_root / "understanding_requests.jsonl"

    print(f"Scanning episode pool from: {episodes_jsonl}")
    print(f"Using ffmpeg: {ffmpeg_bin}")
    print(f"Task families: {task_families}")
    print(f"Sample frame count: {args.sample_frame_count}")

    all_episodes = scan_episode_pool(episodes_jsonl)
    if args.packet_id_contains:
        all_episodes = [item for item in all_episodes if args.packet_id_contains in item["episode_id"]]
    print(f"Found {len(all_episodes)} unique episodes")

    if args.shuffle:
        rng = random.Random(args.seed)
        rng.shuffle(all_episodes)
        print(f"Shuffled episodes with seed={args.seed}")

    if args.limit:
        all_episodes = all_episodes[: args.limit]

    written = 0
    skipped = 0

    with open(output_path, "w", encoding="utf-8") as out_f:
        for index, episode_info in enumerate(all_episodes):
            print(f"[{index + 1}/{len(all_episodes)}] {episode_info['episode_id']}")
            print(
                f"  total_frames={episode_info['total_frames']} "
                f"fps={episode_info['fps']} source={episode_info['source_video_head']}"
            )

            sampled_frames = sample_episode_frames(
                source_video_path=episode_info["source_video_head"],
                total_frames=episode_info["total_frames"],
                fps=episode_info["fps"],
                sample_count=args.sample_frame_count,
                ffmpeg_bin=ffmpeg_bin,
            )
            if len(sampled_frames) < max(8, args.sample_frame_count // 2):
                print("  [WARN] too few sampled frames, skip")
                skipped += 1
                continue

            print(f"  sampled_frame_indices={[frame['frame_index'] for frame in sampled_frames]}")

            for task_family in task_families:
                request = build_request(
                    episode_info=episode_info,
                    sampled_frames=sampled_frames,
                    task_family=task_family,
                )
                out_f.write(json.dumps(request, ensure_ascii=False) + "\n")
                written += 1

    print()
    print("=" * 60)
    print(f"Done. Written {written} requests, skipped {skipped} episodes.")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
