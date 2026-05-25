#!/usr/bin/env python3
"""
RoboCOIN Understanding Run
==========================
读取 understanding_requests.jsonl，调用 API 生成 3 类理解/教程数据，
然后：
  1. 规范化 canonical 输出
  2. 物化 input_video clip / output_image 帧到 artifacts
  3. 输出干净的 training_samples.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DATA_TYPE = "embodied_robot_understanding"
TASK_FAMILIES = (
    "describe_current_subtask",
    "predict_next_primary_subtask",
    "interleaved_tutorial",
)
IMAGE_PLACEHOLDER = "<image>"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RoboCOIN understanding/tutorial requests.")
    parser.add_argument("--requests", required=True, help="Path to understanding_requests.jsonl")
    parser.add_argument("--output-root", default="./understanding_v1", help="Output directory")
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
    parser.add_argument("--sleep-sec", "--sleep_sec", dest="sleep_sec", default=2.0, type=float, help="Sleep between API calls")
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


def resolve_api_key(args: argparse.Namespace) -> str:
    if args.api_key:
        return args.api_key
    for env_name in [args.api_key_env, "OPENAI_API_KEY", "IDEALAB_API_KEY"]:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return ""


def normalize_api_url(url: str) -> str:
    url = url.strip()
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


def extract_model_output(response_json: dict[str, Any]) -> Any:
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


def coerce_model_output(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    return None


def parse_range_value(value: Any, full_start: int, full_end: int) -> tuple[int, int] | None:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.lower() == "all":
            return (full_start, full_end)
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", text)
        if not match:
            return None
        start_frame = int(match.group(1))
        end_frame = int(match.group(2))
    elif isinstance(value, list) and len(value) == 2:
        start_frame, end_frame = value
        if not isinstance(start_frame, int) or not isinstance(end_frame, int):
            return None
    elif isinstance(value, dict):
        start_frame = value.get("start_frame")
        end_frame = value.get("end_frame")
        if not isinstance(start_frame, int) or not isinstance(end_frame, int):
            return None
    else:
        return None

    if start_frame < full_start or end_frame > full_end or end_frame <= start_frame:
        return None
    return (start_frame, end_frame)


def canonical_range_list(frame_range: tuple[int, int]) -> list[int]:
    return [frame_range[0], frame_range[1]]


def canonicalize_image_placeholder_text(text: str) -> str:
    text = re.sub(r"<image\s*/?>", IMAGE_PLACEHOLDER, text, flags=re.IGNORECASE)
    return text


def count_image_placeholders(text: str) -> int:
    return len(re.findall(re.escape(IMAGE_PLACEHOLDER), text or ""))


def normalize_int_list(value: Any, *, unique: bool = False) -> list[int]:
    if value is None:
        return []
    if isinstance(value, int):
        items = [value]
    elif isinstance(value, list):
        items = [item for item in value if isinstance(item, int)]
    else:
        items = []
    if unique:
        items = sorted(set(items))
    return items


def validate_output(
    output: dict[str, Any],
    task_family: str,
    full_range: list[int],
) -> list[str]:
    errors = []
    full_start, full_end = full_range

    input_video = output.get("input_video")
    input_image = output.get("input_image")
    output_video = output.get("output_video")
    output_image = output.get("output_image")
    question = output.get("question")
    answer = output.get("answer")
    cot = output.get("cot")

    input_video_range = parse_range_value(input_video, full_start, full_end)
    if input_video_range is None:
        errors.append("input_video must be a valid range inside full_range")

    input_images = normalize_int_list(input_image, unique=False)
    if input_image is not None and len(input_images) == 0:
        errors.append("input_image must be null or a list of ints")
    for frame_index in input_images:
        if frame_index < full_start or frame_index > full_end:
            errors.append(f"input_image frame {frame_index} must stay inside full_range")

    if output_video is not None:
        errors.append("output_video must be null for current understanding tasks")

    if not isinstance(question, str) or not question.strip():
        errors.append("question must be a non-empty string")
    if not isinstance(answer, str) or not answer.strip():
        errors.append("answer must be a non-empty string")

    if not isinstance(cot, list) or not cot or not all(isinstance(item, str) and item.strip() for item in cot):
        errors.append("cot must be a non-empty list of strings")

    answer_placeholder_count = count_image_placeholders(answer if isinstance(answer, str) else "")
    output_images = normalize_int_list(output_image, unique=False)

    if task_family == "interleaved_tutorial":
        if not output_images:
            errors.append("interleaved_tutorial requires non-empty output_image")
        if len(output_images) < 3 or len(output_images) > 6:
            errors.append("interleaved_tutorial output_image should contain 3-6 key frames")
        if answer_placeholder_count != len(output_images):
            errors.append("answer must contain exactly one <image> placeholder per output_image item")
        if input_video_range is not None:
            for frame_index in output_images:
                if frame_index < input_video_range[0] or frame_index > input_video_range[1]:
                    errors.append("every output_image frame must stay inside input_video")
        if output_images != sorted(output_images):
            errors.append("output_image must be chronological")
    else:
        if output_image is not None:
            errors.append(f"{task_family} requires output_image to be null")
        if answer_placeholder_count > 0:
            errors.append(f"{task_family} answer must not contain <image>")

    return errors


def normalize_output(
    output: dict[str, Any],
    task_family: str,
    full_range: list[int],
) -> dict[str, Any]:
    full_start, full_end = full_range
    normalized = dict(output)

    default_short_range = (
        full_start + max(1, (full_end - full_start) // 3),
        min(full_end, full_start + max(12, (full_end - full_start) * 2 // 3)),
    )
    if default_short_range[1] <= default_short_range[0]:
        default_short_range = (full_start, min(full_end, full_start + 24))
    default_tutorial_range = (full_start, full_end)

    input_video_range = parse_range_value(
        normalized.get("input_video"),
        full_start,
        full_end,
    )
    if input_video_range is None:
        input_video_range = default_tutorial_range if task_family == "interleaved_tutorial" else default_short_range
    normalized["input_video"] = canonical_range_list(input_video_range)

    input_images = normalize_int_list(normalized.get("input_image"), unique=False)
    input_images = [item for item in input_images if full_start <= item <= full_end]
    normalized["input_image"] = input_images or None

    normalized["output_video"] = None

    if task_family == "interleaved_tutorial":
        output_images = normalize_int_list(normalized.get("output_image"), unique=False)
        output_images = [item for item in output_images if input_video_range[0] <= item <= input_video_range[1]]
        if len(output_images) < 3:
            span = input_video_range[1] - input_video_range[0]
            step = max(1, span // 3)
            output_images = [
                input_video_range[0] + step // 2,
                min(input_video_range[1], input_video_range[0] + step + step // 2),
                input_video_range[1],
            ]
        output_images = sorted(output_images[:6])
        normalized["output_image"] = output_images
    else:
        normalized["output_image"] = None

    question = normalized.get("question")
    if not isinstance(question, str) or not question.strip():
        if task_family == "describe_current_subtask":
            question = "What is the robot doing in this segment?"
        elif task_family == "predict_next_primary_subtask":
            question = "What should the robot do next?"
        else:
            question = "What is the overall task here, and can you explain it step by step?"
    normalized["question"] = question.strip()

    answer = normalized.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        if task_family == "describe_current_subtask":
            answer = "The robot is carrying out the current primary manipulation step shown in this segment."
        elif task_family == "predict_next_primary_subtask":
            answer = "The robot should continue to the next primary manipulation step after finishing the current one."
        else:
            placeholder_count = len(normalized["output_image"] or [])
            steps = []
            for idx in range(placeholder_count):
                steps.append(f"Step {idx + 1}: Complete the next visible manipulation stage. {IMAGE_PLACEHOLDER}")
            answer = "Overall task: complete the manipulation sequence. " + " ".join(steps)
    answer = canonicalize_image_placeholder_text(answer.strip())
    if task_family == "interleaved_tutorial":
        needed = len(normalized["output_image"] or [])
        current = count_image_placeholders(answer)
        if current < needed:
            extra_steps = [
                f"Step {idx + current + 1}: Continue with the next key stage. {IMAGE_PLACEHOLDER}"
                for idx in range(needed - current)
            ]
            answer = (answer + " " + " ".join(extra_steps)).strip()
        elif current > needed and needed > 0:
            kept = 0
            parts = []
            tokens = answer.split(IMAGE_PLACEHOLDER)
            for idx, chunk in enumerate(tokens[:-1]):
                if kept >= needed:
                    break
                parts.append(chunk + IMAGE_PLACEHOLDER)
                kept += 1
            if tokens:
                answer = "".join(parts).strip()
    normalized["answer"] = answer

    cot = normalized.get("cot")
    if isinstance(cot, str):
        cot = [cot]
    if not isinstance(cot, list) or not cot:
        if task_family == "describe_current_subtask":
            cot = [
                "The selected segment shows one coherent manipulation stage.",
                "The visible arm and object interaction indicate the current primary subtask.",
            ]
        elif task_family == "predict_next_primary_subtask":
            cot = [
                "The current context shows the robot in the middle of execution, not at task completion.",
                "The next primary subtask should follow the current manipulation state.",
            ]
        else:
            cot = [
                "The episode can be summarized by a few visually distinct key stages.",
                "The tutorial should present those stages in chronological order.",
            ]
    else:
        cot = [item.strip() for item in cot if isinstance(item, str) and item.strip()]
        cot = [canonicalize_image_placeholder_text(item) for item in cot]
    normalized["cot"] = cot

    return normalized


def run_ffmpeg(cmd: list[str], ffmpeg_bin: str, ffmpeg_threads: int = 2) -> subprocess.CompletedProcess:
    full_cmd = [ffmpeg_bin, "-y", "-threads", str(ffmpeg_threads)] + cmd
    return subprocess.run(full_cmd, capture_output=True, timeout=120)


def extract_video_clip(
    source_video: str,
    frame_range: tuple[int, int],
    fps: float,
    out_path: Path,
    ffmpeg_bin: str,
    ffmpeg_threads: int = 2,
) -> bool:
    ensure_dir(out_path.parent)
    if path_exists_nonempty(out_path):
        return True
    if out_path.exists() and not path_exists_nonempty(out_path):
        out_path.unlink()
    start_sec = frame_range[0] / float(fps)
    duration = (frame_range[1] - frame_range[0] + 1) / float(fps)
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
    fps: float,
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


def build_mock_output(full_range: list[int], task_family: str) -> dict[str, Any]:
    full_start, full_end = full_range
    if task_family == "describe_current_subtask":
        return {
            "input_video": [full_start + 12, min(full_end, full_start + 72)],
            "input_image": None,
            "output_video": None,
            "output_image": None,
            "question": "What is the robot doing in this segment?",
            "answer": "The robot is carrying out the current primary manipulation step shown in this segment.",
            "cot": [
                "The selected segment shows one coherent manipulation stage.",
                "The visible arm and object interaction indicate the current primary subtask.",
            ],
        }
    if task_family == "predict_next_primary_subtask":
        return {
            "input_video": [full_start + 12, min(full_end - 12, full_start + 60)],
            "input_image": None,
            "output_video": None,
            "output_image": None,
            "question": "What should the robot do next?",
            "answer": "The robot should continue to the next primary manipulation step after finishing the current one.",
            "cot": [
                "The current context shows the robot in the middle of execution, not at task completion.",
                "The next primary subtask should follow the current manipulation state.",
            ],
        }
    tutorial_images = [
        full_start + max(1, (full_end - full_start) // 5),
        full_start + max(2, (full_end - full_start) * 2 // 5),
        full_start + max(3, (full_end - full_start) * 4 // 5),
    ]
    return {
        "input_video": [full_start, full_end],
        "input_image": None,
        "output_video": None,
        "output_image": tutorial_images,
        "question": "What is the overall task here, and can you explain it step by step?",
        "answer": (
            f"Overall task: complete the manipulation sequence. "
            f"Step 1: Prepare the first manipulation stage. {IMAGE_PLACEHOLDER} "
            f"Step 2: Carry out the main interaction. {IMAGE_PLACEHOLDER} "
            f"Step 3: Finish and transition to completion. {IMAGE_PLACEHOLDER}"
        ),
        "cot": [
            "The episode can be summarized by a few visually distinct key stages.",
            "The tutorial should present those stages in chronological order.",
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
    training_output_path = output_root / "training_samples.jsonl"
    artifacts_output_path = output_root / "artifacts.jsonl"
    api_responses_path = output_root / "api_responses.jsonl"

    ensure_dir(videos_dir)
    ensure_dir(frames_dir)

    api_key = resolve_api_key(args)
    if not api_key and not args.dry_run:
        print("[WARN] No API key found. Set --api-key, OPENAI_API_KEY, or IDEALAB_API_KEY.")
    print(f"Using ffmpeg: {ffmpeg_bin}")
    print(f"Using api_url: {api_url}")

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
        all_requests = all_requests[: args.limit]

    print(f"Loaded {len(all_requests)} requests from {requests_path}")

    success_count = 0
    error_count = 0

    with (
        open(training_output_path, "a", encoding="utf-8") as train_f,
        open(artifacts_output_path, "a", encoding="utf-8") as artifact_f,
        open(api_responses_path, "a", encoding="utf-8") as resp_f,
    ):
        for request_index, request in enumerate(all_requests):
            episode_id = request["episode_id"]
            request_id = request["request_id"]
            task_family = request["task_family"]
            sample_id = f"{task_family}__{episode_id}"

            if sample_id in done_sample_ids:
                print(f"[{request_index + 1}/{len(all_requests)}] SKIP: {sample_id}")
                continue

            print(f"[{request_index + 1}/{len(all_requests)}] Processing: {episode_id} ({task_family})")

            source_info = request["source_info"]
            source_video = source_info["source_video_head"]
            fps = source_info["fps"]
            full_range = source_info["full_range"]

            messages = request["request_payload"]["messages"]
            if args.dry_run:
                model_output = build_mock_output(full_range, task_family)
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
                    model_output = coerce_model_output(extract_model_output(response_json))
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
                model_output = normalize_output(
                    model_output,
                    task_family=task_family,
                    full_range=full_range,
                )

            validation_errors = (
                validate_output(
                    model_output,
                    task_family=task_family,
                    full_range=full_range,
                )
                if model_output is not None
                else ["model_output is null"]
            )

            resp_record = {
                "request_id": request_id,
                "episode_id": episode_id,
                "sample_id": sample_id,
                "task_family": task_family,
                "status": api_status,
                "attempts": attempt_logs,
                "validation_errors": validation_errors,
                "model_output": model_output,
                "response_text": response_text[:4000] if len(response_text) > 4000 else response_text,
            }
            resp_f.write(json.dumps(resp_record, ensure_ascii=False) + "\n")
            resp_f.flush()

            if model_output is None:
                print("  [ERROR] API returned no valid JSON output")
                error_count += 1
                if args.sleep_sec > 0 and not args.dry_run:
                    time.sleep(args.sleep_sec)
                continue

            if validation_errors:
                print(f"  [WARN] validation corrected/remaining issues: {validation_errors}")

            input_video_range = parse_range_value(model_output["input_video"], full_range[0], full_range[1])
            assert input_video_range is not None
            input_images = normalize_int_list(model_output["input_image"], unique=False)
            output_images = normalize_int_list(model_output["output_image"], unique=False)

            safe_id = episode_id.replace("/", "_").replace(" ", "_")
            input_video_path = videos_dir / f"{safe_id}__{task_family}__input.mp4"
            input_image_paths: list[str] = []
            output_image_paths: list[str] = []

            input_ok = extract_video_clip(
                source_video=source_video,
                frame_range=input_video_range,
                fps=fps,
                out_path=input_video_path,
                ffmpeg_bin=ffmpeg_bin,
                ffmpeg_threads=args.ffmpeg_threads,
            )
            if not input_ok:
                print("  [WARN] Failed to materialize input_video artifact.")

            for idx, frame_index in enumerate(input_images):
                image_path = frames_dir / f"{safe_id}__{task_family}__input_f{idx:02d}_{frame_index:06d}.jpg"
                ok = extract_frame_as_jpeg(
                    source_video=source_video,
                    frame_index=frame_index,
                    fps=fps,
                    out_path=image_path,
                    ffmpeg_bin=ffmpeg_bin,
                )
                if ok:
                    input_image_paths.append(str(image_path.resolve()))

            for idx, frame_index in enumerate(output_images):
                image_path = frames_dir / f"{safe_id}__{task_family}__output_f{idx:02d}_{frame_index:06d}.jpg"
                ok = extract_frame_as_jpeg(
                    source_video=source_video,
                    frame_index=frame_index,
                    fps=fps,
                    out_path=image_path,
                    ffmpeg_bin=ffmpeg_bin,
                )
                if ok:
                    output_image_paths.append(str(image_path.resolve()))

            training_sample = {
                "sample_id": sample_id,
                "data_type": DATA_TYPE,
                "task_family": task_family,
                "source_view": source_info.get("source_view", "head"),
                "source_video": source_video,
                "fps": fps,
                "fps_source": source_info.get("fps_source", ""),
                "input_video": model_output["input_video"],
                "input_image": model_output["input_image"],
                "output_video": None,
                "output_image": model_output["output_image"],
                "question": model_output["question"],
                "answer": model_output["answer"],
                "cot": model_output["cot"],
            }
            artifact_record = {
                "sample_id": sample_id,
                "episode_id": episode_id,
                "request_id": request_id,
                "task_family": task_family,
                "input_video_path": str(input_video_path.resolve()) if input_ok else None,
                "input_image_paths": input_image_paths,
                "output_video_path": None,
                "output_image_paths": output_image_paths,
            }

            train_f.write(json.dumps(training_sample, ensure_ascii=False) + "\n")
            train_f.flush()
            artifact_f.write(json.dumps(artifact_record, ensure_ascii=False) + "\n")
            artifact_f.flush()

            success_count += 1
            print(
                f"  [OK] sample_id={sample_id} "
                f"input_video={model_output['input_video']} "
                f"output_images={model_output['output_image']}"
            )

            if args.sleep_sec > 0 and not args.dry_run:
                time.sleep(args.sleep_sec)

    print()
    print("=" * 60)
    print(f"Done. success={success_count}, error={error_count}")
    print(f"Training samples: {training_output_path}")
    print(f"Artifacts:        {artifacts_output_path}")
    print(f"API responses:    {api_responses_path}")


if __name__ == "__main__":
    main()
