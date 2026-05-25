#!/usr/bin/env python3
"""
Approach-Stall Synthesis Prepare
================================
从健康成功样本中提取 head 观测片段，并为 API 生成最小化请求：
  1. system prompt
  2. 20 张均匀采样帧（obs 段 + future 段，附原始视频帧号）
  3. 输出格式约束

API 需要基于这些采样帧，判断：
  - 停滞发生在哪一帧（通过重复该帧合成 stall）
  - 回溯帧是哪一帧（better local state）
  - 正确恢复视频片段的起止帧
  - question / answer / cot

默认只使用旧 packet 模式下的：
  - task_family in {identify_primary_subtask, predict_next_primary_subtask}
  - quality_tier == annotation_strong

这样做的目的，是从相对高质量的“成功执行窗口”出发，再合成故障世界模型数据。
如果传入全库 candidate jsonl，则直接使用 candidate 里的 obs/future window。
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


DEFAULT_FAULT_TYPE = "approach_stall"
SUPPORTED_FAULT_TYPES = ("approach_stall", "hesitation_loop")
OBS_SAMPLE_FRAME_COUNT = 8
FUTURE_SAMPLE_FRAME_COUNT = 4
SAMPLE_FRAME_COUNT = OBS_SAMPLE_FRAME_COUNT + FUTURE_SAMPLE_FRAME_COUNT
DEFAULT_TASK_FAMILIES = ("identify_primary_subtask", "predict_next_primary_subtask")
DEFAULT_QUALITY_TIERS = ("annotation_strong",)

SYSTEM_PROMPTS = {
    "approach_stall": """You are authoring one synthetic robot-fault training sample of type "approach_stall".

The source clip comes from a successful robot manipulation video. Your job is to imagine a realistic "approach stall" fault by freezing one frame from the observed approach phase.

You will receive:
- 12 sampled head-camera frames from one successful source video
- 8 frames from obs_range: these are the pre-stall context frames
- 4 frames from future_range: these show the successful continuation after the observation window
- each frame is labeled by its original frame index in the full source video

Return exactly one JSON object in this schema:
{
  "input_video": {
    "start_frame": <int>,
    "end_frame": <int>,
    "freeze_frame": <int>,
    "freeze_repeat_count": <int>
  },
  "input_image": null,
  "output_video": [<int>, <int>],
  "output_image": [<int>],
  "question": <string>,
  "answer": <string>,
  "cot": [<string>, ...]
}

Field meaning:
- input_video.start_frame: must equal obs_start
- input_video.end_frame: must equal input_video.freeze_frame
- input_video.freeze_frame: choose one frame strictly inside obs_range where the end-effector is still approaching the target and has not finished the approach
- input_video.freeze_repeat_count: how many times to repeat freeze_frame to synthesize the stall; prefer 15-45
- input_image: always null for this task
- output_image: exactly one recall frame index, representing a slightly better local state before the stall; it must be inside obs_range and <= freeze_frame. This image is the visual target referenced by <image_recall></image_recall> in cot.
- output_video: [start_frame, end_frame] for the correct continuation clip; start_frame must be <= recall frame, and end_frame should be supported by the visible future_range frames. This video is the visual target referenced by <video_gen></video_gen> in answer.
- question: one natural English question about the current stalled situation
- answer: concise English diagnosis + recovery instruction, and it must contain <video_gen></video_gen> exactly once. That placeholder refers to output_video.
- cot: a short list of concise English reasoning strings
  - keep cot brief
  - at least one cot string must contain <image_recall></image_recall>, and that placeholder refers to output_image
  - do not use <video_gen></video_gen> inside cot; keep that placeholder only in answer

Reasoning style requirements:
- Write question, answer, and cot as training supervision for embodied fault diagnosis and recovery, not as dataset annotation commentary.
- Reason from the current visible situation with limited observation. The text should sound like diagnosing what is happening now and deciding what to do next.
- Do not mention frame numbers, frame ranges, sampled-frame counts, obs_range, future_range, or phrases like "future frames show".
- Do not narrate the clip chronologically or summarize the whole video from an outside annotator viewpoint.
- Use future_range only to choose output_video boundaries; do not mention those future frames directly in question, answer, or cot.
- Good cot should focus on:
  - what visible symptom indicates the stall
  - why this is a fault rather than normal progress
  - which earlier better local state should be recalled via <image_recall></image_recall>
  - what corrective continuation should happen next

Rules:
- Use only visible evidence from the sampled frames.
- Use obs_range frames to choose freeze_frame and recall_frame.
- Use future_range frames to decide how far the correct continuation should extend.
- Do not mention any hidden labels, annotations, or packet metadata.
- All frame numbers must refer to the original full video.
- Return only the JSON object. No markdown fences. No extra text.""",
    "hesitation_loop": """You are authoring one synthetic robot-fault training sample of type "hesitation_loop".

The source clip comes from a successful robot manipulation video. Your job is to imagine a realistic hesitation-loop fault by replaying one short micro-motion back and forth near the target from the observed approach phase.

You will receive:
- 12 sampled head-camera frames from one successful source video
- 8 frames from obs_range: these are the pre-fault context frames
- 4 frames from future_range: these show the successful continuation after the observation window
- each frame is labeled by its original frame index in the full source video

Return exactly one JSON object in this schema:
{
  "input_video": {
    "start_frame": <int>,
    "end_frame": <int>,
    "loop_start_frame": <int>,
    "loop_end_frame": <int>,
    "loop_repeat_count": <int>
  },
  "input_image": null,
  "output_video": [<int>, <int>],
  "output_image": [<int>],
  "question": <string>,
  "answer": <string>,
  "cot": [<string>, ...]
}

Field meaning:
- input_video.start_frame: must equal obs_start
- input_video.end_frame: must equal input_video.loop_end_frame
- input_video.loop_start_frame and input_video.loop_end_frame: choose a short segment strictly inside obs_range where the end-effector is close to the target but appears to hesitate or oscillate instead of making clean progress. Prefer a segment length of roughly 8-12 frames when possible.
- input_video.loop_repeat_count: how many extra back-and-forth hesitation cycles to append after the first natural occurrence of that segment; prefer 1-2
- input_image: always null for this task
- output_image: exactly one recall frame index, representing a slightly better local state before the hesitation loop; it must be inside obs_range and <= loop_start_frame. This image is the visual target referenced by <image_recall></image_recall> in cot.
- output_video: [start_frame, end_frame] for the correct continuation clip; start_frame must be <= recall frame, and end_frame should be supported by the visible future_range frames. This video is the visual target referenced by <video_gen></video_gen> in answer.
- question: one natural English question about the robot repeatedly hesitating in place
- answer: concise English diagnosis + recovery instruction, and it must contain <video_gen></video_gen> exactly once. That placeholder refers to output_video.
- cot: a short list of concise English reasoning strings
  - keep cot brief
  - at least one cot string must contain <image_recall></image_recall>, and that placeholder refers to output_image
  - do not use <video_gen></video_gen> inside cot; keep that placeholder only in answer

Reasoning style requirements:
- Write question, answer, and cot as training supervision for embodied fault diagnosis and recovery, not as dataset annotation commentary.
- Reason from the current visible situation with limited observation. The text should sound like diagnosing what is happening now and deciding what to do next.
- Do not mention frame numbers, frame ranges, sampled-frame counts, obs_range, future_range, or phrases like "future frames show".
- Do not narrate the clip chronologically or summarize the whole video from an outside annotator viewpoint.
- Use future_range only to choose output_video boundaries; do not mention those future frames directly in question, answer, or cot.
- For hesitation_loop specifically, focus on the local symptom: repeated micro-adjustment, oscillation, or no net progress near the target.
- Good cot should focus on:
  - what visible symptom indicates the hesitation loop
  - why this is a fault rather than productive fine adjustment
  - which earlier better local state should be recalled via <image_recall></image_recall>
  - what corrective continuation should happen next

Rules:
- Use only visible evidence from the sampled frames.
- Use obs_range frames to choose the hesitation loop segment and recall frame.
- Use future_range frames to decide how far the correct continuation should extend.
- Do not mention any hidden labels, annotations, or packet metadata.
- All frame numbers must refer to the original full video.
- Return only the JSON object. No markdown fences. No extra text.""",
}

EXPECTED_OUTPUT_SCHEMAS = {
    "approach_stall": {
        "input_video": {
            "start_frame": "int, must equal obs_start",
            "end_frame": "int, must equal freeze_frame",
            "freeze_frame": "int, strictly inside obs_range",
            "freeze_repeat_count": "int, recommend 15-45",
        },
        "input_image": None,
        "output_video": "[start_frame, end_frame] with end near future completion",
        "output_image": "[recall_frame]",
        "question": "English string",
        "answer": "English string containing <video_gen></video_gen>",
        "cot": [
            "short English reasoning",
            "one item includes <image_recall></image_recall>",
        ],
    },
    "hesitation_loop": {
        "input_video": {
            "start_frame": "int, must equal obs_start",
            "end_frame": "int, must equal loop_end_frame",
            "loop_start_frame": "int, strictly inside obs_range",
            "loop_end_frame": "int, strictly inside obs_range and > loop_start_frame",
            "loop_repeat_count": "int, extra back-and-forth hesitation cycles after first natural loop occurrence; recommend 1-2",
        },
        "input_image": None,
        "output_video": "[start_frame, end_frame] with end near future completion",
        "output_image": "[recall_frame]",
        "question": "English string",
        "answer": "English string containing <video_gen></video_gen>",
        "cot": [
            "short English reasoning",
            "one item includes <image_recall></image_recall>",
        ],
    },
}

FAULT_USER_REMINDERS = {
    "approach_stall": (
        "Return the JSON object only. "
        "Remember: input_video.start_frame must equal obs_start, "
        "input_video.end_frame must equal freeze_frame, "
        "input_image must be null, output_image must contain one recall frame, "
        "and output_video must be supported by the visible future frames."
    ),
    "hesitation_loop": (
        "Return the JSON object only. "
        "Remember: input_video.start_frame must equal obs_start, "
        "input_video.end_frame must equal loop_end_frame, "
        "loop_start_frame and loop_end_frame must define one short hesitation segment inside obs_range, "
        "loop_repeat_count means extra back-and-forth hesitation cycles after the first natural occurrence, "
        "input_image must be null, output_image must contain one recall frame, "
        "and output_video must be supported by the visible future frames."
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare approach-stall synthesis API requests.")
    parser.add_argument(
        "--fault-type",
        default=DEFAULT_FAULT_TYPE,
        choices=SUPPORTED_FAULT_TYPES,
        help="Synthetic fault type to prepare",
    )
    parser.add_argument(
        "--packets-jsonl",
        default="/mnt/nas-data-6/lijiangyang.ljy/preapi_media_health_v2/global_packets.jsonl",
        help="Path to legacy global_packets.jsonl",
    )
    parser.add_argument(
        "--candidates-jsonl",
        default="",
        help="Optional path to mined full-library fault candidates jsonl",
    )
    parser.add_argument("--output-root", default="./fault_synthesis", help="Output directory")
    parser.add_argument("--limit", default=None, type=int, help="Only process first N samples")
    parser.add_argument(
        "--task-families",
        default=None,
        help="Comma-separated task families to keep",
    )
    parser.add_argument(
        "--quality-tiers",
        default=None,
        help="Comma-separated quality tiers to keep",
    )
    parser.add_argument(
        "--packet-id-contains",
        default="",
        help="Optional substring filter for packet_id",
    )
    parser.add_argument(
        "--ffmpeg-bin",
        default="",
        help="Optional ffmpeg binary path. If empty, auto-detect an AV1-capable ffmpeg.",
    )
    parser.add_argument("--shuffle", action="store_true", help="Shuffle candidate packets before applying --limit")
    parser.add_argument("--seed", default=0, type=int, help="Random seed for --shuffle")
    return parser.parse_args()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def supports_av1_decode(ffmpeg_bin: str) -> bool:
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-decoders"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        text = (result.stdout or "") + "\n" + (result.stderr or "")
        text = text.lower()
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


def parse_csv_set(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def even_sample_indices(total_count: int, sample_count: int) -> list[int]:
    if total_count <= 0 or sample_count <= 0:
        return []
    if sample_count >= total_count:
        return list(range(total_count))
    return [round(i * (total_count - 1) / (sample_count - 1)) for i in range(sample_count)]


def extract_frame_jpeg_from_video(
    video_path: str,
    frame_offset: int,
    fps: int,
    ffmpeg_bin: str,
    max_side: int = 768,
) -> bytes | None:
    start_sec = frame_offset / float(fps)
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


def sample_frames_from_obs_video(
    obs_video_path: str,
    obs_range: list[int],
    fps: int,
    sample_count: int,
    phase: str,
    ffmpeg_bin: str,
) -> list[dict[str, Any]]:
    obs_start, obs_end = obs_range
    total_frames_in_obs = obs_end - obs_start + 1
    relative_offsets = even_sample_indices(total_frames_in_obs, sample_count)

    sampled_frames = []
    for rel_offset in relative_offsets:
        original_frame_index = obs_start + rel_offset
        jpeg_bytes = extract_frame_jpeg_from_video(obs_video_path, rel_offset, fps, ffmpeg_bin)
        if jpeg_bytes is None:
            continue
        sampled_frames.append(
            {
                "phase": phase,
                "frame_index": original_frame_index,
                "data_url": jpeg_to_data_url(jpeg_bytes),
                "payload_bytes": len(jpeg_bytes),
            }
        )
    return sampled_frames


def sample_frames_from_source_video(
    source_video_path: str,
    frame_range: list[int],
    fps: int,
    sample_count: int,
    phase: str,
    ffmpeg_bin: str,
) -> list[dict[str, Any]]:
    range_start, range_end = frame_range
    total_frames = range_end - range_start + 1
    relative_offsets = even_sample_indices(total_frames, sample_count)

    sampled_frames = []
    for rel_offset in relative_offsets:
        original_frame_index = range_start + rel_offset
        jpeg_bytes = extract_frame_jpeg_from_video(
            source_video_path,
            original_frame_index,
            fps,
            ffmpeg_bin,
        )
        if jpeg_bytes is None:
            continue
        sampled_frames.append(
            {
                "phase": phase,
                "frame_index": original_frame_index,
                "data_url": jpeg_to_data_url(jpeg_bytes),
                "payload_bytes": len(jpeg_bytes),
            }
        )
    return sampled_frames


def scan_local_packet_pool(
    packets_jsonl: Path,
) -> list[dict[str, Any]]:
    results = []
    with open(packets_jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                pkt = json.loads(line)
            except Exception:
                continue

            packet_id = pkt.get("packet_id", "")
            validation = pkt.get("validation", {})
            if not validation.get("media_ok", False):
                continue

            student_ctx = pkt.get("student_context", {})
            teacher_ctx = pkt.get("teacher_context", {})
            obs_range = student_ctx.get("obs_range")
            future_range = teacher_ctx.get("future_range")
            obs_video_path = student_ctx.get("observation_clip_paths", {}).get("head", "")
            source_video_head = student_ctx.get("source_video_paths", {}).get("head", "")
            fps = pkt.get("fps") or student_ctx.get("fps") or 30

            if not obs_range or not future_range:
                continue
            if not obs_video_path or not source_video_head:
                continue
            if not os.path.exists(obs_video_path):
                continue

            results.append(
                {
                    "packet_id": packet_id,
                    "dataset_name": pkt.get("dataset_name", ""),
                    "episode_index": pkt.get("episode_index", -1),
                    "task_family": pkt.get("task_family", ""),
                    "quality_tier": pkt.get("quality_tier", ""),
                    "obs_video_path": obs_video_path,
                    "obs_video_is_clip": True,
                    "source_video_head": source_video_head,
                    "obs_range": obs_range,
                    "future_range": future_range,
                    "fps": fps,
                    "fps_source": "packet_metadata",
                }
            )

    return results


def scan_candidate_pool(
    candidates_jsonl: Path,
    fault_type: str,
) -> list[dict[str, Any]]:
    results = []
    with open(candidates_jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue

            source_video_head = row.get("source_video_head") or row.get("source_video_paths", {}).get("head", "")
            obs_range = row.get("obs_range")
            future_range = row.get("future_range")
            fps = row.get("fps")
            if not source_video_head or not obs_range or not future_range:
                continue
            if not os.path.exists(source_video_head):
                continue
            packet_id = row.get("packet_id") or row.get("candidate_id") or ""
            if fault_type != DEFAULT_FAULT_TYPE:
                packet_id = packet_id.replace(f"{DEFAULT_FAULT_TYPE}_candidate", f"{fault_type}_candidate")
            task_family = row.get("task_family", f"{fault_type}_candidate")
            if fault_type != DEFAULT_FAULT_TYPE:
                task_family = task_family.replace(f"{DEFAULT_FAULT_TYPE}_candidate", f"{fault_type}_candidate")
            results.append(
                {
                    "packet_id": packet_id,
                    "dataset_name": row.get("dataset_name", ""),
                    "episode_index": row.get("episode_index", -1),
                    "task_family": task_family,
                    "quality_tier": row.get("quality_tier", "video_probe_ok"),
                    "obs_video_path": source_video_head,
                    "obs_video_is_clip": False,
                    "source_video_head": source_video_head,
                    "obs_range": obs_range,
                    "future_range": future_range,
                    "fps": fps or 30,
                    "fps_source": row.get("fps_source", "candidate_metadata"),
                }
            )
    return results


def filter_candidate_packets(
    packet_pool: list[dict[str, Any]],
    task_families: set[str],
    quality_tiers: set[str],
    packet_id_contains: str,
) -> list[dict[str, Any]]:
    results = []
    for packet in packet_pool:
        if task_families and packet["task_family"] not in task_families:
            continue
        if quality_tiers and packet["quality_tier"] not in quality_tiers:
            continue
        if packet_id_contains and packet_id_contains not in packet["packet_id"]:
            continue
        results.append(packet)
    return results


def select_donor_packet_for_frame(
    target_packet: dict[str, Any],
    packet_pool: list[dict[str, Any]],
    frame_index: int,
) -> dict[str, Any] | None:
    donors = []
    for packet in packet_pool:
        if packet["dataset_name"] != target_packet["dataset_name"]:
            continue
        if packet["episode_index"] != target_packet["episode_index"]:
            continue
        obs_start, obs_end = packet["obs_range"]
        if obs_start <= frame_index <= obs_end:
            center = (obs_start + obs_end) / 2.0
            donors.append((abs(frame_index - center), obs_end - obs_start, packet))
    if not donors:
        return None
    donors.sort(key=lambda item: (item[0], item[1], item[2]["packet_id"]))
    return donors[0][2]


def sample_future_frames_from_packet_pool(
    target_packet: dict[str, Any],
    packet_pool: list[dict[str, Any]],
    fps: int,
    sample_count: int,
    ffmpeg_bin: str,
) -> list[dict[str, Any]]:
    future_start, future_end = target_packet["future_range"]
    total_frames = future_end - future_start + 1
    relative_offsets = even_sample_indices(total_frames, sample_count)

    sampled_frames = []
    for rel_offset in relative_offsets:
        original_frame_index = future_start + rel_offset
        donor_packet = select_donor_packet_for_frame(target_packet, packet_pool, original_frame_index)
        if donor_packet is None:
            continue
        donor_offset = original_frame_index - donor_packet["obs_range"][0]
        jpeg_bytes = extract_frame_jpeg_from_video(
            donor_packet["obs_video_path"],
            donor_offset,
            fps,
            ffmpeg_bin,
        )
        if jpeg_bytes is None:
            continue
        sampled_frames.append(
            {
                "phase": "future",
                "frame_index": original_frame_index,
                "data_url": jpeg_to_data_url(jpeg_bytes),
                "payload_bytes": len(jpeg_bytes),
                "donor_packet_id": donor_packet["packet_id"],
            }
        )
    return sampled_frames


def build_user_message_content(
    packet_info: dict[str, Any],
    sampled_frames: list[dict[str, Any]],
    fault_type: str,
) -> list[dict[str, Any]]:
    obs_range = packet_info["obs_range"]
    future_range = packet_info["future_range"]
    fps = packet_info["fps"]

    intro_text = (
        f"OBS_RANGE: [{obs_range[0]}, {obs_range[1]}]\n"
        f"FUTURE_RANGE: [{future_range[0]}, {future_range[1]}]\n"
        f"FPS: {fps}\n"
        f"SAMPLED_FRAMES_TOTAL: {len(sampled_frames)}\n"
        f"SAMPLED_OBS_FRAMES: {OBS_SAMPLE_FRAME_COUNT}\n"
        f"SAMPLED_FUTURE_FRAMES: {FUTURE_SAMPLE_FRAME_COUNT}\n"
        "Below are sampled head-camera frames from two segments of the same successful source video. "
        "OBS frames are the pre-fault context. FUTURE frames are the successful continuation used to choose the recovery target clip. "
        "Each label is the frame index in the original full video."
    )

    content_parts: list[dict[str, Any]] = [{"type": "text", "text": intro_text}]
    for frame_info in sampled_frames:
        content_parts.append(
            {
                "type": "text",
                "text": f"{frame_info['phase'].upper()}_FRAME_INDEX={frame_info['frame_index']}",
            }
        )
        content_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": frame_info["data_url"], "detail": "low"},
            }
        )

    content_parts.append(
        {
            "type": "text",
            "text": FAULT_USER_REMINDERS[fault_type],
        }
    )
    return content_parts


def build_request(
    packet_info: dict[str, Any],
    sampled_obs_frames: list[dict[str, Any]],
    sampled_future_frames: list[dict[str, Any]],
    fault_type: str,
) -> dict[str, Any]:
    packet_id = packet_info["packet_id"]
    request_id = f"fault_synthesis__{fault_type}__{packet_id}"
    sampled_frames = sampled_obs_frames + sampled_future_frames
    user_content_full = build_user_message_content(packet_info, sampled_frames, fault_type)

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
        {"role": "system", "content": SYSTEM_PROMPTS[fault_type]},
        {"role": "user", "content": user_content_full},
    ]

    return {
        "request_id": request_id,
        "packet_id": packet_id,
        "fault_type": fault_type,
        "source_info": {
            "task_family": packet_info["task_family"],
            "quality_tier": packet_info["quality_tier"],
            "obs_video_path": packet_info["obs_video_path"],
            "obs_video_is_clip": bool(packet_info.get("obs_video_is_clip", False)),
            "obs_range": packet_info["obs_range"],
            "future_range": packet_info["future_range"],
            "fps": packet_info["fps"],
            "fps_source": packet_info.get("fps_source", ""),
            "source_video_head": packet_info["source_video_head"],
        },
        "sampled_frame_indices": [frame["frame_index"] for frame in sampled_frames],
        "sampled_frame_count": len(sampled_frames),
        "sampled_obs_frame_indices": [frame["frame_index"] for frame in sampled_obs_frames],
        "sampled_future_frame_indices": [frame["frame_index"] for frame in sampled_future_frames],
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
                {"role": "system", "content": SYSTEM_PROMPTS[fault_type]},
                {"role": "user", "content": user_content_redacted},
            ],
        },
        "expected_output_schema": EXPECTED_OUTPUT_SCHEMAS[fault_type],
    }


def main() -> None:
    args = parse_args()
    fault_type = args.fault_type
    packets_jsonl = Path(args.packets_jsonl).resolve()
    candidates_jsonl = Path(args.candidates_jsonl).resolve() if args.candidates_jsonl else None
    output_root = Path(args.output_root).resolve()
    ensure_dir(output_root)
    ffmpeg_bin = resolve_ffmpeg_bin(args.ffmpeg_bin)

    if candidates_jsonl is None:
        task_families = parse_csv_set(args.task_families or ",".join(DEFAULT_TASK_FAMILIES))
        quality_tiers = parse_csv_set(args.quality_tiers or ",".join(DEFAULT_QUALITY_TIERS))
    else:
        task_families = parse_csv_set(args.task_families or "")
        quality_tiers = parse_csv_set(args.quality_tiers or "")
    output_path = output_root / "fault_synthesis_requests.jsonl"

    if candidates_jsonl is None:
        print(f"Scanning packets from: {packets_jsonl}")
    else:
        print(f"Scanning candidates from: {candidates_jsonl}")
    print(f"Using ffmpeg: {ffmpeg_bin}")
    print(f"Fault type: {fault_type}")
    print(f"Task families: {sorted(task_families) if task_families else 'ALL'}")
    print(f"Quality tiers: {sorted(quality_tiers) if quality_tiers else 'ALL'}")

    if candidates_jsonl is None:
        packet_pool = scan_local_packet_pool(packets_jsonl=packets_jsonl)
    else:
        packet_pool = scan_candidate_pool(candidates_jsonl=candidates_jsonl, fault_type=fault_type)
    all_packets = filter_candidate_packets(
        packet_pool=packet_pool,
        task_families=task_families,
        quality_tiers=quality_tiers,
        packet_id_contains=args.packet_id_contains,
    )
    print(f"Found {len(packet_pool)} local packet clips")
    print(f"Found {len(all_packets)} candidate packets")

    if args.shuffle:
        rng = random.Random(args.seed)
        rng.shuffle(all_packets)
        print(f"Shuffled candidate packets with seed={args.seed}")

    if args.limit:
        all_packets = all_packets[:args.limit]

    written = 0
    skipped = 0

    with open(output_path, "w", encoding="utf-8") as out_f:
        for index, packet_info in enumerate(all_packets):
            packet_id = packet_info["packet_id"]
            obs_video = packet_info["obs_video_path"]
            obs_range = packet_info["obs_range"]
            future_range = packet_info["future_range"]
            fps = packet_info["fps"]

            print(f"[{index + 1}/{len(all_packets)}] {packet_id}")
            print(
                f"  task_family={packet_info['task_family']} "
                f"quality_tier={packet_info['quality_tier']}"
            )
            print(f"  obs_range={obs_range}, future_range={future_range}, fps={fps}")

            sampled_obs_frames = sample_frames_from_source_video(
                source_video_path=packet_info["source_video_head"],
                frame_range=obs_range,
                fps=fps,
                sample_count=OBS_SAMPLE_FRAME_COUNT,
                phase="obs",
                ffmpeg_bin=ffmpeg_bin,
            )
            sampled_future_frames = sample_frames_from_source_video(
                source_video_path=packet_info["source_video_head"],
                frame_range=future_range,
                fps=fps,
                sample_count=FUTURE_SAMPLE_FRAME_COUNT,
                phase="future",
                ffmpeg_bin=ffmpeg_bin,
            )
            if len(sampled_future_frames) != FUTURE_SAMPLE_FRAME_COUNT:
                sampled_future_frames = sample_future_frames_from_packet_pool(
                    target_packet=packet_info,
                    packet_pool=packet_pool,
                    fps=fps,
                    sample_count=FUTURE_SAMPLE_FRAME_COUNT,
                    ffmpeg_bin=ffmpeg_bin,
                )
            if not sampled_obs_frames or not sampled_future_frames:
                print(f"  [SKIP] Failed to extract sampled frames from {obs_video}")
                skipped += 1
                continue

            print(
                "  sampled_obs_frame_indices="
                f"{[frame['frame_index'] for frame in sampled_obs_frames]}"
            )
            print(
                "  sampled_future_frame_indices="
                f"{[frame['frame_index'] for frame in sampled_future_frames]}"
            )
            print(
                "  sampled_future_donors="
                f"{[frame.get('donor_packet_id', '') for frame in sampled_future_frames]}"
            )
            request = build_request(packet_info, sampled_obs_frames, sampled_future_frames, fault_type=fault_type)
            out_f.write(json.dumps(request, ensure_ascii=False) + "\n")
            written += 1

    print(f"\nDone. Written {written} requests, skipped {skipped}.")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
