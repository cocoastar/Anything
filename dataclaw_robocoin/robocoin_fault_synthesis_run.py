#!/usr/bin/env python3
"""
Approach-Stall Synthesis Run
============================
读取 fault_synthesis_requests.jsonl，调用 API 获取停滞帧/回溯帧/恢复片段，
然后：
  1. 用 freeze_frame 合成停滞输入视频
  2. 从原始视频截取正确恢复视频
  3. 从 obs 视频提取 recall 帧
  4. 输出干净的 canonical 训练样本
  5. 把路径类中间产物写到独立 artifacts 文件
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DATA_TYPE = "embodied_robot_fault_decision_world_model"
DEFAULT_FAULT_TYPE = "approach_stall"
SUPPORTED_FAULT_TYPES = ("approach_stall", "hesitation_loop")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run approach-stall synthesis requests.")
    parser.add_argument(
        "--fault-type",
        default=DEFAULT_FAULT_TYPE,
        choices=SUPPORTED_FAULT_TYPES,
        help="Synthetic fault type to run",
    )
    parser.add_argument("--requests", required=True, help="Path to fault_synthesis_requests.jsonl")
    parser.add_argument("--output-root", default="./fault_synthesis", help="Output directory")
    parser.add_argument("--api-url", default="https://api.openai.com/v1/chat/completions")
    parser.add_argument("--api-key", default="", help="API key (or set env vars)")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Primary API key env var")
    parser.add_argument("--model", default="gpt-4.1")
    parser.add_argument("--temperature", default=0.2, type=float)
    parser.add_argument(
        "--top-p",
        "--top_p",
        dest="top_p",
        default=None,
        type=float,
        help="Optional top_p. If omitted, the request does not send top_p.",
    )
    parser.add_argument("--max-tokens", "--max_tokens", dest="max_tokens", default=8192, type=int)
    parser.add_argument("--timeout-sec", "--timeout_sec", dest="timeout_sec", default=120, type=int)
    parser.add_argument(
        "--sleep-sec",
        "--sleep_sec",
        dest="sleep_sec",
        default=2.0,
        type=float,
        help="Sleep between API calls",
    )
    parser.add_argument("--max-attempts", default=3, type=int, help="Max API attempts per request")
    parser.add_argument("--limit", default=None, type=int, help="Only process first N requests")
    parser.add_argument("--resume", action="store_true", help="Skip already written sample_ids")
    parser.add_argument("--dry-run", action="store_true", help="Skip API call and use mock output")
    parser.add_argument("--ffmpeg-threads", default=2, type=int)
    parser.add_argument(
        "--ffmpeg-bin",
        default="",
        help="Optional ffmpeg binary path. If empty, auto-detect an AV1-capable ffmpeg.",
    )
    return parser.parse_args()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def path_exists_nonempty(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


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


def resolve_api_key(args: argparse.Namespace) -> str:
    if args.api_key:
        return args.api_key
    for env_name in [args.api_key_env, "OPENAI_API_KEY", "IDEALAB_API_KEY"]:
        env_key = os.environ.get(env_name, "")
        if env_key:
            return env_key
    return ""


def normalize_api_url(api_url: str) -> str:
    url = api_url.strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return url + "/chat/completions"
    if url.endswith("/openai/v1"):
        return url + "/chat/completions"
    return url


def call_api(
    messages: list[dict[str, Any]],
    model: str,
    api_url: str,
    api_key: str,
    temperature: float,
    top_p: float | None,
    max_tokens: int,
    timeout_sec: int,
) -> tuple[str, dict[str, Any]]:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    if top_p is not None:
        payload["top_p"] = top_p
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = urllib.request.Request(api_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            response_text = resp.read().decode("utf-8")
        response_json = json.loads(response_text)
        return response_text, response_json
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        return error_body, {}
    except Exception as exc:
        return str(exc), {}


def strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def coerce_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif item.get("type") == "text" and isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "\n".join(part for part in parts if part).strip()
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"].strip()
    return ""


def extract_model_output(response_json: dict[str, Any]) -> dict[str, Any] | None:
    choices = response_json.get("choices", [])
    if not choices:
        return None
    content = choices[0].get("message", {}).get("content", "")
    content = coerce_content_to_text(content)
    if not content:
        return None
    content = strip_code_fences(content)
    try:
        return json.loads(content)
    except Exception:
        return None


def count_placeholder(text: str, placeholder: str) -> int:
    return text.count(placeholder) if isinstance(text, str) else 0


def total_placeholder_count(items: list[str], placeholder: str) -> int:
    return sum(count_placeholder(item, placeholder) for item in items if isinstance(item, str))


def canonicalize_image_recall_placeholders(text: str) -> str:
    text = re.sub(
        r"<image_recall>\s*.*?\s*</image_recall>",
        "<image_recall></image_recall>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<image_recall>(?!</image_recall>)",
        "<image_recall></image_recall>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?<!<image_recall>)</image_recall>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text


def validate_model_output(
    output: dict[str, Any],
    obs_range: list[int],
    future_range: list[int],
    fault_type: str = DEFAULT_FAULT_TYPE,
) -> list[str]:
    errors = []
    obs_start, obs_end = obs_range
    future_end = future_range[1]

    input_video = output.get("input_video")
    input_image = output.get("input_image")
    output_video = output.get("output_video")
    output_image = output.get("output_image")
    question = output.get("question")
    answer = output.get("answer")
    cot = output.get("cot")

    fault_anchor_frame = None
    if not isinstance(input_video, dict):
        errors.append("input_video must be an object")
    else:
        start_frame = input_video.get("start_frame")
        end_frame = input_video.get("end_frame")

        if not isinstance(start_frame, int):
            errors.append("input_video.start_frame must be int")
        elif start_frame != obs_start:
            errors.append(f"input_video.start_frame={start_frame} must equal obs_start={obs_start}")

        if not isinstance(end_frame, int):
            errors.append("input_video.end_frame must be int")

        if fault_type == "hesitation_loop":
            loop_start_frame = input_video.get("loop_start_frame")
            loop_end_frame = input_video.get("loop_end_frame")
            loop_repeat_count = input_video.get("loop_repeat_count")

            if not isinstance(loop_start_frame, int):
                errors.append("input_video.loop_start_frame must be int")
            elif not (obs_start < loop_start_frame < obs_end):
                errors.append(
                    f"input_video.loop_start_frame={loop_start_frame} must be strictly inside [{obs_start},{obs_end}]"
                )

            if not isinstance(loop_end_frame, int):
                errors.append("input_video.loop_end_frame must be int")
            elif not (obs_start < loop_end_frame < obs_end):
                errors.append(
                    f"input_video.loop_end_frame={loop_end_frame} must be strictly inside [{obs_start},{obs_end}]"
                )

            if isinstance(loop_start_frame, int) and isinstance(loop_end_frame, int) and loop_end_frame <= loop_start_frame:
                errors.append("input_video.loop_end_frame must be > input_video.loop_start_frame")

            if isinstance(end_frame, int) and isinstance(loop_end_frame, int) and end_frame != loop_end_frame:
                errors.append("input_video.end_frame must equal input_video.loop_end_frame")

            if not isinstance(loop_repeat_count, int) or loop_repeat_count < 1:
                errors.append("input_video.loop_repeat_count must be positive int")

            if isinstance(loop_start_frame, int):
                fault_anchor_frame = loop_start_frame
        else:
            freeze_frame = input_video.get("freeze_frame")
            freeze_repeat_count = input_video.get("freeze_repeat_count")

            if not isinstance(freeze_frame, int):
                errors.append("input_video.freeze_frame must be int")
            elif not (obs_start < freeze_frame < obs_end):
                errors.append(
                    f"input_video.freeze_frame={freeze_frame} must be strictly inside [{obs_start},{obs_end}]"
                )

            if isinstance(end_frame, int) and isinstance(freeze_frame, int) and end_frame != freeze_frame:
                errors.append("input_video.end_frame must equal input_video.freeze_frame")

            if not isinstance(freeze_repeat_count, int) or freeze_repeat_count < 1:
                errors.append("input_video.freeze_repeat_count must be positive int")

            if isinstance(freeze_frame, int):
                fault_anchor_frame = freeze_frame

    if input_image is not None:
        errors.append("input_image must be null")

    recall_frame = None
    if not isinstance(output_image, list) or len(output_image) != 1:
        errors.append("output_image must be a list with exactly one recall frame")
    else:
        recall_frame = output_image[0]
        if not isinstance(recall_frame, int):
            errors.append("output_image[0] must be int")

    if not isinstance(output_video, list) or len(output_video) != 2:
        errors.append("output_video must be [start_frame, end_frame]")
    else:
        out_start, out_end = output_video
        if not isinstance(out_start, int) or not isinstance(out_end, int):
            errors.append("output_video values must be int")
        else:
            if out_start < obs_start:
                errors.append(f"output_video.start={out_start} must be >= obs_start={obs_start}")
            if out_end <= out_start:
                errors.append(f"output_video.end={out_end} must be > start={out_start}")
            if out_end > future_end:
                errors.append(f"output_video.end={out_end} must be <= future_end={future_end}")
            if isinstance(recall_frame, int) and out_start > recall_frame:
                errors.append(f"output_video.start={out_start} must be <= recall_frame={recall_frame}")

    if isinstance(recall_frame, int):
        if not (obs_start <= recall_frame <= obs_end):
            errors.append(f"output_image[0]={recall_frame} must be inside obs_range")
        if isinstance(fault_anchor_frame, int) and recall_frame > fault_anchor_frame:
            if fault_type == "hesitation_loop":
                errors.append(f"output_image[0]={recall_frame} must be <= loop_start_frame={fault_anchor_frame}")
            else:
                errors.append(f"output_image[0]={recall_frame} must be <= freeze_frame={fault_anchor_frame}")

    if not isinstance(question, str) or not question.strip():
        errors.append("question must be a non-empty string")
    if not isinstance(answer, str) or not answer.strip():
        errors.append("answer must be a non-empty string")
    elif count_placeholder(answer, "<video_gen></video_gen>") != 1:
        errors.append("answer must contain <video_gen></video_gen> exactly once")

    if not isinstance(cot, list) or not cot or not all(isinstance(item, str) and item.strip() for item in cot):
        errors.append("cot must be a non-empty list of strings")
    else:
        if total_placeholder_count(cot, "<image_recall></image_recall>") < 1:
            errors.append("cot must contain <image_recall></image_recall> at least once")
        if total_placeholder_count(cot, "<video_gen></video_gen>") > 0:
            errors.append("cot must not contain <video_gen></video_gen>")

    return errors


def normalize_model_output(
    output: dict[str, Any],
    obs_range: list[int],
    future_range: list[int],
    fault_type: str = DEFAULT_FAULT_TYPE,
) -> dict[str, Any]:
    obs_start, obs_end = obs_range
    future_end = future_range[1]
    normalized = dict(output)

    input_video = normalized.get("input_video")
    if not isinstance(input_video, dict):
        input_video = {}

    if fault_type == "hesitation_loop":
        loop_start_frame = input_video.get("loop_start_frame")
        if not isinstance(loop_start_frame, int):
            loop_start_frame = obs_start + max(2, (obs_end - obs_start) * 3 // 5)
        loop_start_frame = max(obs_start + 1, min(obs_end - 2, loop_start_frame))

        loop_end_frame = input_video.get("loop_end_frame")
        if not isinstance(loop_end_frame, int):
            loop_span = min(9, max(7, (obs_end - obs_start) // 6))
            loop_end_frame = loop_start_frame + loop_span
        loop_end_frame = max(loop_start_frame + 1, min(obs_end - 1, loop_end_frame))

        loop_repeat_count = input_video.get("loop_repeat_count")
        if not isinstance(loop_repeat_count, int):
            loop_repeat_count = 2
        loop_repeat_count = max(1, loop_repeat_count)

        fault_anchor_frame = loop_start_frame
        input_video_normalized = {
            "start_frame": obs_start,
            "end_frame": loop_end_frame,
            "loop_start_frame": loop_start_frame,
            "loop_end_frame": loop_end_frame,
            "loop_repeat_count": loop_repeat_count,
        }
    else:
        freeze_frame = input_video.get("freeze_frame")
        if not isinstance(freeze_frame, int):
            freeze_frame = obs_start + max(1, (obs_end - obs_start) // 2)
        freeze_frame = max(obs_start + 1, min(obs_end - 1, freeze_frame))

        freeze_repeat_count = input_video.get("freeze_repeat_count")
        if not isinstance(freeze_repeat_count, int):
            freeze_repeat_count = 24
        freeze_repeat_count = max(1, freeze_repeat_count)

        fault_anchor_frame = freeze_frame
        input_video_normalized = {
            "start_frame": obs_start,
            "end_frame": freeze_frame,
            "freeze_frame": freeze_frame,
            "freeze_repeat_count": freeze_repeat_count,
        }

    output_image = normalized.get("output_image")
    if not isinstance(output_image, list) or not output_image:
        recall_frame = max(obs_start, fault_anchor_frame - 5)
    else:
        recall_frame = output_image[0]
        if not isinstance(recall_frame, int):
            recall_frame = max(obs_start, fault_anchor_frame - 5)
    recall_frame = max(obs_start, min(fault_anchor_frame, recall_frame))

    output_video = normalized.get("output_video")
    if not isinstance(output_video, list) or len(output_video) != 2:
        out_start = max(obs_start, recall_frame - 3)
        out_end = future_end
    else:
        out_start, out_end = output_video
        if not isinstance(out_start, int):
            out_start = max(obs_start, recall_frame - 3)
        if not isinstance(out_end, int):
            out_end = future_end
    out_start = max(obs_start, min(recall_frame, out_start))
    out_end = max(out_start + 1, min(future_end, out_end))

    question = normalized.get("question")
    if not isinstance(question, str) or not question.strip():
        if fault_type == "hesitation_loop":
            question = "Why is the robot repeating the same local motion, and what should it do next?"
        else:
            question = "This is my current situation. What should I do?"

    answer = normalized.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        if fault_type == "hesitation_loop":
            answer = (
                "The robot is stuck in a hesitation loop near the target instead of making net progress. "
                "Return to a slightly better local state and resume the intended motion decisively. "
                "<video_gen></video_gen>"
            )
        else:
            answer = (
                "The robot has stalled before reaching the target. "
                "Return to a slightly better local state and continue the approach. "
                "<video_gen></video_gen>"
            )
    elif "<video_gen></video_gen>" not in answer:
        answer = answer.strip() + " <video_gen></video_gen>"

    cot = normalized.get("cot")
    if isinstance(cot, str):
        cot = [cot]
    if not isinstance(cot, list) or not cot:
        if fault_type == "hesitation_loop":
            cot = [
                "The end-effector is repeating a short local correction near the target without making net progress.",
                "This suggests a hesitation loop rather than productive fine adjustment.",
                "I should return to the slightly better local state in <image_recall></image_recall> and continue from there.",
            ]
        else:
            cot = [
                "The end-effector has stalled before reaching the target, so the current motion is no longer progressing.",
                "The safer recovery is to return to the slightly better local state in <image_recall></image_recall>.",
                "From there, the robot should resume the intended approach and finish the action cleanly.",
            ]
    else:
        cot = [item.strip() for item in cot if isinstance(item, str) and item.strip()]
        cot = [item.replace("<video_gen></video_gen>", "").strip() for item in cot]
        cot = [canonicalize_image_recall_placeholders(item) for item in cot]
        cot = [item for item in cot if item]
        if not cot:
            if fault_type == "hesitation_loop":
                cot = [
                    "The end-effector is repeating a short local correction near the target without making net progress.",
                    "This suggests a hesitation loop rather than productive fine adjustment.",
                    "I should return to the slightly better local state in <image_recall></image_recall> and continue from there.",
                ]
            else:
                cot = [
                    "The end-effector has stalled before reaching the target, so the current motion is no longer progressing.",
                    "The safer recovery is to return to the slightly better local state in <image_recall></image_recall>.",
                    "From there, the robot should resume the intended approach and finish the action cleanly.",
                ]
        if total_placeholder_count(cot, "<image_recall></image_recall>") < 1:
            cot.append("I should recall a slightly better local state. <image_recall></image_recall>")

    normalized["input_video"] = input_video_normalized
    normalized["input_image"] = None
    normalized["output_video"] = [out_start, out_end]
    normalized["output_image"] = [recall_frame]
    normalized["question"] = question.strip()
    normalized["answer"] = answer.strip()
    normalized["cot"] = cot
    return normalized


def run_ffmpeg(cmd: list[str], ffmpeg_bin: str, ffmpeg_threads: int = 2) -> subprocess.CompletedProcess:
    full_cmd = [ffmpeg_bin, "-y", "-threads", str(ffmpeg_threads)] + cmd
    return subprocess.run(full_cmd, capture_output=True, timeout=120)


def synthesize_stall_video(
    source_video: str,
    obs_start: int,
    freeze_start_frame: int,
    freeze_repeat_count: int,
    fps: int,
    out_path: Path,
    ffmpeg_bin: str,
    ffmpeg_threads: int = 2,
) -> bool:
    ensure_dir(out_path.parent)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_dir = Path(tmpdir)
        obs_clip = tmp_dir / "obs_clip.mp4"
        freeze_clip = tmp_dir / "freeze_clip.mp4"
        freeze_frame_jpg = tmp_dir / "freeze_frame.jpg"
        concat_list = tmp_dir / "concat.txt"

        obs_start_sec = obs_start / float(fps)
        obs_duration = (freeze_start_frame - obs_start + 1) / float(fps)
        result = run_ffmpeg(
            [
                "-ss", f"{obs_start_sec:.6f}",
                "-i", source_video,
                "-t", f"{obs_duration:.6f}",
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                str(obs_clip),
            ],
            ffmpeg_bin,
            ffmpeg_threads,
        )
        if result.returncode != 0 or not obs_clip.exists():
            return False

        freeze_start_sec = freeze_start_frame / float(fps)
        result = subprocess.run(
            [
                ffmpeg_bin, "-y",
                "-ss", f"{freeze_start_sec:.6f}",
                "-i", source_video,
                "-vframes", "1",
                str(freeze_frame_jpg),
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0 or not freeze_frame_jpg.exists():
            return False

        result = run_ffmpeg(
            [
                "-loop", "1",
                "-framerate", str(fps),
                "-i", str(freeze_frame_jpg),
                "-frames:v", str(freeze_repeat_count),
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                str(freeze_clip),
            ],
            ffmpeg_bin,
            ffmpeg_threads,
        )
        if result.returncode != 0 or not freeze_clip.exists():
            return False

        concat_list.write_text(
            f"file '{obs_clip.resolve()}'\nfile '{freeze_clip.resolve()}'\n",
            encoding="utf-8",
        )
        result = run_ffmpeg(
            [
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                str(out_path),
            ],
            ffmpeg_bin,
            ffmpeg_threads,
        )
        if result.returncode != 0 or not out_path.exists():
            return False

    return True


def synthesize_hesitation_loop_video(
    source_video: str,
    obs_start: int,
    loop_start_frame: int,
    loop_end_frame: int,
    loop_repeat_count: int,
    fps: int,
    out_path: Path,
    ffmpeg_bin: str,
    ffmpeg_threads: int = 2,
) -> bool:
    ensure_dir(out_path.parent)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_dir = Path(tmpdir)
        prefix_clip = tmp_dir / "prefix_clip.mp4"
        loop_clip = tmp_dir / "loop_clip.mp4"
        reverse_clip = tmp_dir / "reverse_clip.mp4"
        concat_list = tmp_dir / "concat.txt"

        prefix_start_sec = obs_start / float(fps)
        prefix_duration = (loop_end_frame - obs_start + 1) / float(fps)
        result = run_ffmpeg(
            [
                "-ss", f"{prefix_start_sec:.6f}",
                "-i", source_video,
                "-t", f"{prefix_duration:.6f}",
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                str(prefix_clip),
            ],
            ffmpeg_bin,
            ffmpeg_threads,
        )
        if result.returncode != 0 or not prefix_clip.exists():
            return False

        loop_start_sec = loop_start_frame / float(fps)
        loop_duration = (loop_end_frame - loop_start_frame + 1) / float(fps)
        result = run_ffmpeg(
            [
                "-ss", f"{loop_start_sec:.6f}",
                "-i", source_video,
                "-t", f"{loop_duration:.6f}",
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                str(loop_clip),
            ],
            ffmpeg_bin,
            ffmpeg_threads,
        )
        if result.returncode != 0 or not loop_clip.exists():
            return False

        result = run_ffmpeg(
            [
                "-i", str(loop_clip),
                "-vf", "reverse",
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                str(reverse_clip),
            ],
            ffmpeg_bin,
            ffmpeg_threads,
        )
        if result.returncode != 0 or not reverse_clip.exists():
            return False

        concat_lines = [f"file '{prefix_clip.resolve()}'"]
        for _ in range(loop_repeat_count):
            concat_lines.append(f"file '{reverse_clip.resolve()}'")
            concat_lines.append(f"file '{loop_clip.resolve()}'")
        concat_list.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

        result = run_ffmpeg(
            [
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                str(out_path),
            ],
            ffmpeg_bin,
            ffmpeg_threads,
        )
        if result.returncode != 0 or not out_path.exists():
            return False

    return True


def extract_video_clip(
    source_video: str,
    start_frame: int,
    end_frame: int,
    fps: int,
    out_path: Path,
    ffmpeg_bin: str,
    ffmpeg_threads: int = 2,
) -> bool:
    ensure_dir(out_path.parent)
    if path_exists_nonempty(out_path):
        return True
    if out_path.exists() and not path_exists_nonempty(out_path):
        out_path.unlink()
    start_sec = start_frame / float(fps)
    duration = (end_frame - start_frame + 1) / float(fps)
    result = run_ffmpeg(
        [
            "-ss", f"{start_sec:.6f}",
            "-i", source_video,
            "-t", f"{duration:.6f}",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            str(out_path),
        ],
        ffmpeg_bin,
        ffmpeg_threads,
    )
    if result.returncode != 0:
        if out_path.exists() and not path_exists_nonempty(out_path):
            out_path.unlink()
        return False
    return path_exists_nonempty(out_path)


def extract_frame_as_jpeg(
    source_video: str,
    frame_index: int,
    fps: int,
    out_path: Path,
    ffmpeg_bin: str,
) -> bool:
    ensure_dir(out_path.parent)
    if path_exists_nonempty(out_path):
        return True
    if out_path.exists() and not path_exists_nonempty(out_path):
        out_path.unlink()
    start_sec = frame_index / float(fps)
    result = subprocess.run(
        [
            ffmpeg_bin, "-y",
            "-ss", f"{start_sec:.6f}",
            "-i", source_video,
            "-vframes", "1",
            "-q:v", "3",
            str(out_path),
        ],
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        if out_path.exists() and not path_exists_nonempty(out_path):
            out_path.unlink()
        return False
    return path_exists_nonempty(out_path)


def build_mock_output(
    obs_range: list[int],
    future_range: list[int],
    fault_type: str = DEFAULT_FAULT_TYPE,
) -> dict[str, Any]:
    obs_start, obs_end = obs_range
    if fault_type == "hesitation_loop":
        loop_start_frame = obs_start + max(2, (obs_end - obs_start) * 3 // 5)
        loop_start_frame = min(obs_end - 2, loop_start_frame)
        loop_end_frame = min(obs_end - 1, loop_start_frame + 8)
        recall_frame = max(obs_start, loop_start_frame - 6)
        return {
            "input_video": {
                "start_frame": obs_start,
                "end_frame": loop_end_frame,
                "loop_start_frame": loop_start_frame,
                "loop_end_frame": loop_end_frame,
                "loop_repeat_count": 2,
            },
            "input_image": None,
            "output_video": [max(obs_start, recall_frame - 3), future_range[1]],
            "output_image": [recall_frame],
            "question": "Why is the robot repeating the same local motion, and what should it do next?",
            "answer": (
                "The robot is stuck in a hesitation loop near the target instead of making clean progress. "
                "Return to a slightly better local state and continue the approach decisively. "
                "<video_gen></video_gen>"
            ),
            "cot": [
                "The end-effector is repeating a short local motion instead of progressing toward the target.",
                "I recall a slightly better local state before the hesitation loop. <image_recall></image_recall>",
            ],
        }
    freeze_frame = obs_start + max(1, (obs_end - obs_start) * 3 // 5)
    freeze_frame = min(obs_end - 1, freeze_frame)
    recall_frame = max(obs_start, freeze_frame - 6)
    return {
        "input_video": {
            "start_frame": obs_start,
            "end_frame": freeze_frame,
            "freeze_frame": freeze_frame,
            "freeze_repeat_count": 24,
        },
        "input_image": None,
        "output_video": [max(obs_start, recall_frame - 3), future_range[1]],
        "output_image": [recall_frame],
        "question": "This is my current situation. What should I do?",
        "answer": (
            "The robot has stalled before reaching the target. "
            "Return to a slightly better local state and continue the approach. "
            "<video_gen></video_gen>"
        ),
        "cot": [
            "The end-effector has stalled before reaching the target.",
            "I recall a slightly better local state just before the stall. <image_recall></image_recall>",
        ],
    }


def main() -> None:
    args = parse_args()
    requests_path = Path(args.requests).resolve()
    output_root = Path(args.output_root).resolve()
    ensure_dir(output_root)
    ffmpeg_bin = resolve_ffmpeg_bin(args.ffmpeg_bin)
    api_url = normalize_api_url(args.api_url)

    videos_dir = output_root / "videos"
    frames_dir = output_root / "frames"
    training_output_path = output_root / "fault_training_samples.jsonl"
    artifacts_output_path = output_root / "fault_artifacts.jsonl"
    api_responses_path = output_root / "fault_api_responses.jsonl"

    ensure_dir(videos_dir)
    ensure_dir(frames_dir)

    api_key = resolve_api_key(args)
    if not api_key and not args.dry_run:
        print("[WARN] No API key found. Set --api-key, OPENAI_API_KEY, or IDEALAB_API_KEY.")
    print(f"Using ffmpeg: {ffmpeg_bin}")
    print(f"Using api_url: {api_url}")
    print(f"Default fault_type: {args.fault_type}")

    done_sample_ids: set[str] = set()
    if args.resume and training_output_path.exists():
        with open(training_output_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    done_sample_ids.add(json.loads(line)["sample_id"])
                except Exception:
                    continue
        print(f"Resume mode: {len(done_sample_ids)} samples already done.")

    all_requests = []
    with open(requests_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                all_requests.append(json.loads(line))

    if args.limit:
        all_requests = all_requests[:args.limit]

    print(f"Loaded {len(all_requests)} requests from {requests_path}")

    success_count = 0
    error_count = 0

    with (
        open(training_output_path, "a", encoding="utf-8") as train_f,
        open(artifacts_output_path, "a", encoding="utf-8") as artifact_f,
        open(api_responses_path, "a", encoding="utf-8") as resp_f,
    ):
        for request_index, request in enumerate(all_requests):
            packet_id = request["packet_id"]
            request_id = request["request_id"]
            request_fault_type = request.get("fault_type", args.fault_type)
            sample_id = f"{request_fault_type}__{packet_id}"

            if sample_id in done_sample_ids:
                print(f"[{request_index + 1}/{len(all_requests)}] SKIP: {sample_id}")
                continue

            print(f"[{request_index + 1}/{len(all_requests)}] Processing: {packet_id}")

            source_info = request["source_info"]
            source_video = source_info["source_video_head"]
            obs_range = source_info["obs_range"]
            future_range = source_info["future_range"]
            fps = source_info["fps"]
            obs_video_path = source_info.get("obs_video_path") or source_video
            obs_video_is_clip = bool(source_info.get("obs_video_is_clip", True))

            messages = request["request_payload"]["messages"]
            if args.dry_run:
                model_output = build_mock_output(obs_range, future_range, fault_type=request_fault_type)
                response_text = json.dumps(
                    {"choices": [{"message": {"content": json.dumps(model_output, ensure_ascii=False)}}]},
                    ensure_ascii=False,
                )
                response_json = json.loads(response_text)
                api_status = "dry_run"
                attempt_logs = [{"attempt": 1, "status": api_status, "parsed": True}]
            else:
                response_text = ""
                response_json = {}
                model_output = None
                attempt_logs = []
                max_attempts = max(1, args.max_attempts)
                for attempt in range(1, max_attempts + 1):
                    response_text, response_json = call_api(
                        messages=messages,
                        model=args.model,
                        api_url=api_url,
                        api_key=api_key,
                        temperature=args.temperature,
                        top_p=args.top_p,
                        max_tokens=args.max_tokens,
                        timeout_sec=args.timeout_sec,
                    )
                    model_output = extract_model_output(response_json)
                    parsed = model_output is not None
                    attempt_logs.append(
                        {
                            "attempt": attempt,
                            "parsed": parsed,
                            "response_preview": response_text[:500] if response_text else "",
                        }
                    )
                    if parsed:
                        break
                    if attempt < max_attempts:
                        retry_sleep = args.sleep_sec if args.sleep_sec > 0 else 1.0
                        print(f"  [WARN] Attempt {attempt}/{max_attempts} failed, retrying in {retry_sleep:.1f}s")
                        time.sleep(retry_sleep)
                api_status = "ok" if model_output else "error"

            if model_output is not None:
                model_output = normalize_model_output(
                    model_output,
                    obs_range,
                    future_range,
                    fault_type=request_fault_type,
                )

            validation_errors = (
                validate_model_output(
                    model_output,
                    obs_range,
                    future_range,
                    fault_type=request_fault_type,
                )
                if model_output is not None
                else ["model_output is null"]
            )

            resp_record = {
                "request_id": request_id,
                "packet_id": packet_id,
                "sample_id": sample_id,
                "status": api_status,
                "attempts": attempt_logs,
                "validation_errors": validation_errors,
                "model_output": model_output,
                "response_text": response_text[:4000] if len(response_text) > 4000 else response_text,
            }
            resp_f.write(json.dumps(resp_record, ensure_ascii=False) + "\n")
            resp_f.flush()

            if model_output is None:
                print(f"  [ERROR] API returned no valid JSON output")
                error_count += 1
                if args.sleep_sec > 0 and not args.dry_run:
                    time.sleep(args.sleep_sec)
                continue

            if validation_errors:
                print(f"  [WARN] validation corrected/remaining issues: {validation_errors}")

            input_video = model_output["input_video"]
            output_video = model_output["output_video"]
            recall_frame = model_output["output_image"][0]

            safe_id = packet_id.replace("/", "_").replace(" ", "_")
            input_video_suffix = "input_stall" if request_fault_type == "approach_stall" else f"input_{request_fault_type}"
            synthesized_video_path = videos_dir / f"{safe_id}__{input_video_suffix}.mp4"
            output_video_path = videos_dir / f"{safe_id}__output_correct.mp4"
            recall_frame_path = frames_dir / f"{safe_id}__recall_f{recall_frame:06d}.jpg"

            has_source_video = bool(source_video and os.path.exists(source_video))
            input_source_video = source_video if has_source_video else obs_video_path
            input_source_is_clip = (input_source_video == obs_video_path and obs_video_is_clip and not has_source_video)

            if request_fault_type == "hesitation_loop":
                segment_start_frame = input_video["loop_start_frame"]
                segment_end_frame = input_video["loop_end_frame"]
                repeat_count = input_video["loop_repeat_count"]
                if input_source_is_clip:
                    synth_obs_start = 0
                    synth_segment_start = segment_start_frame - obs_range[0]
                    synth_segment_end = segment_end_frame - obs_range[0]
                else:
                    synth_obs_start = obs_range[0]
                    synth_segment_start = segment_start_frame
                    synth_segment_end = segment_end_frame

                print(
                    f"  synth input_video: obs -> loop[{segment_start_frame},{segment_end_frame}] "
                    f"+ back-and-forth cycles x {repeat_count}"
                )
                input_ok = synthesize_hesitation_loop_video(
                    source_video=input_source_video,
                    obs_start=synth_obs_start,
                    loop_start_frame=synth_segment_start,
                    loop_end_frame=synth_segment_end,
                    loop_repeat_count=repeat_count,
                    fps=fps,
                    out_path=synthesized_video_path,
                    ffmpeg_bin=ffmpeg_bin,
                    ffmpeg_threads=args.ffmpeg_threads,
                )
            else:
                freeze_frame = input_video["freeze_frame"]
                freeze_repeat_count = input_video["freeze_repeat_count"]
                if input_source_is_clip:
                    synth_obs_start = 0
                    synth_freeze_frame = freeze_frame - obs_range[0]
                else:
                    synth_obs_start = obs_range[0]
                    synth_freeze_frame = freeze_frame

                freeze_offset_in_obs = freeze_frame - obs_range[0]
                print(
                    f"  synth input_video: obs[0,{freeze_offset_in_obs}] + "
                    f"repeat frame {freeze_frame} x {freeze_repeat_count}"
                )
                input_ok = synthesize_stall_video(
                    source_video=input_source_video,
                    obs_start=synth_obs_start,
                    freeze_start_frame=synth_freeze_frame,
                    freeze_repeat_count=freeze_repeat_count,
                    fps=fps,
                    out_path=synthesized_video_path,
                    ffmpeg_bin=ffmpeg_bin,
                    ffmpeg_threads=args.ffmpeg_threads,
                )

            if not input_ok:
                print(f"  [ERROR] Failed to synthesize input {request_fault_type} video")
                error_count += 1
                continue

            correct_ok = False
            if has_source_video:
                correct_ok = extract_video_clip(
                    source_video=source_video,
                    start_frame=output_video[0],
                    end_frame=output_video[1],
                    fps=fps,
                    out_path=output_video_path,
                    ffmpeg_bin=ffmpeg_bin,
                    ffmpeg_threads=args.ffmpeg_threads,
                )
            if not correct_ok:
                print("  [WARN] Failed to materialize output_video artifact, but canonical sample will still be written.")

            recall_source_video = source_video if has_source_video else obs_video_path
            recall_source_is_clip = (recall_source_video == obs_video_path and obs_video_is_clip and not has_source_video)
            recall_source_frame = recall_frame - obs_range[0] if recall_source_is_clip else recall_frame
            recall_ok = extract_frame_as_jpeg(
                source_video=recall_source_video,
                frame_index=recall_source_frame,
                fps=fps,
                out_path=recall_frame_path,
                ffmpeg_bin=ffmpeg_bin,
            )
            if not recall_ok:
                print("  [WARN] Failed to materialize recall frame artifact.")

            training_sample = {
                "sample_id": sample_id,
                "data_type": DATA_TYPE,
                "fault_type": request_fault_type,
                "source_view": "head",
                "source_video": source_video,
                "fps": fps,
                "fps_source": source_info.get("fps_source", ""),
                "input_video": input_video,
                "input_image": None,
                "output_video": output_video,
                "output_image": model_output["output_image"],
                "question": model_output["question"],
                "answer": model_output["answer"],
                "cot": model_output["cot"],
            }
            artifact_record = {
                "sample_id": sample_id,
                "packet_id": packet_id,
                "request_id": request_id,
                "input_video_path": str(synthesized_video_path.resolve()),
                "output_video_path": str(output_video_path.resolve()) if correct_ok else None,
                "output_image_paths": [str(recall_frame_path.resolve())] if recall_ok else [],
            }

            train_f.write(json.dumps(training_sample, ensure_ascii=False) + "\n")
            train_f.flush()
            artifact_f.write(json.dumps(artifact_record, ensure_ascii=False) + "\n")
            artifact_f.flush()

            success_count += 1
            if request_fault_type == "hesitation_loop":
                print(
                    f"  [OK] sample_id={sample_id} "
                    f"loop=[{input_video['loop_start_frame']},{input_video['loop_end_frame']}] "
                    f"recall_frame={recall_frame} output_video={output_video}"
                )
            else:
                print(
                    f"  [OK] sample_id={sample_id} "
                    f"freeze_frame={input_video['freeze_frame']} recall_frame={recall_frame} "
                    f"output_video={output_video}"
                )

            if args.sleep_sec > 0 and not args.dry_run:
                time.sleep(args.sleep_sec)

    print("\n" + "=" * 60)
    print(f"Done. success={success_count}, error={error_count}")
    print(f"Training samples: {training_output_path}")
    print(f"Artifacts:        {artifacts_output_path}")
    print(f"API responses:    {api_responses_path}")


if __name__ == "__main__":
    main()
