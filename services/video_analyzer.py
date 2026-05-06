import asyncio
import json
import os
import re
from pathlib import Path

from models.schemas import AnalysisResult, Shot, VideoInfo
from services.claude_client import claude_with_skill

BASE_DIR = Path(__file__).parent.parent
FRAMES_DIR = BASE_DIR / "frames"
SKILL_PATH = BASE_DIR / ".claude" / "skills" / "video-analyze" / "SKILL.md"


def _parse_duration(val: str) -> float:
    """Parse various duration formats to seconds."""
    val = val.strip()
    # "78.04s" or "4.00s"
    m = re.search(r"([\d.]+)\s*s", val)
    if m:
        return float(m.group(1))
    # "00:01:30.5" (HH:MM:SS)
    m = re.match(r"(\d+):(\d+):(\d+\.?\d*)", val)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    # "1:30.5" or "0:04.00" (M:SS.ms)
    m = re.match(r"(\d+):(\d+\.?\d*)", val)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    # bare number "78.04"
    m = re.search(r"[\d.]+", val)
    if m:
        try:
            return float(m.group())
        except ValueError:
            pass
    return 0.0


def parse_video_info_table(md: str) -> VideoInfo:
    """Parse the video info table from analysis markdown."""
    info = {}
    for line in md.split("\n"):
        if "|" not in line or "---" in line or "属性" in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            key, val = parts[0], parts[1]
            if "文件名" in key:
                info["filename"] = val
            elif "分辨率" in key:
                info["resolution"] = val
            elif "帧率" in key:
                info["fps"] = float(re.search(r"[\d.]+", val).group()) if re.search(r"[\d.]+", val) else 0
            elif "总时长" in key or "时长" in key:
                # Parse duration - could be "78.04s", "4.00s", "00:01:30", "0:04.00" etc.
                info["duration"] = _parse_duration(val)
            elif "编码" in key:
                info["codec"] = val
            elif "音频" in key:
                info["audio"] = val
            elif "镜头数" in key:
                m = re.search(r"\d+", val)
                info["shot_count"] = int(m.group()) if m else 0

    # Fallback: parse list format (new skill outputs "- 分辨率：720×1280")
    if not info.get("resolution"):
        m = re.search(r'分辨率[：:]\s*([^\s（(]+)', md)
        if m:
            info["resolution"] = m.group(1)
    if not info.get("fps"):
        m = re.search(r'帧率[：:]\s*([\d.]+)', md)
        if m:
            info["fps"] = float(m.group(1))
    if not info.get("duration") or info.get("duration") == 0:
        m = re.search(r'时长[：:]\s*([\d.]+)s', md)
        if m:
            info["duration"] = float(m.group(1))
    if not info.get("codec"):
        m = re.search(r'编码[：:格式]\s*(\S+)', md)
        if m:
            info["codec"] = m.group(1)
    if not info.get("filename"):
        m = re.search(r'文件名[：:]\s*(\S+)', md)
        if m:
            info["filename"] = m.group(1)

    return VideoInfo(
        filename=info.get("filename", ""),
        resolution=info.get("resolution", ""),
        fps=info.get("fps", 0),
        duration=info.get("duration", 0),
        codec=info.get("codec", ""),
        audio=info.get("audio", ""),
        shot_count=info.get("shot_count", 0),
    )


def parse_time(time_str: str) -> float:
    """Parse time string like '0:03.88' or '1:02.83' to seconds."""
    time_str = time_str.strip()
    m = re.match(r"(\d+):(\d+\.?\d*)", time_str)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    m = re.match(r"(\d+\.?\d*)", time_str)
    if m:
        return float(m.group(1))
    return 0.0


def parse_shots_table(md: str) -> list[Shot]:
    """Parse the shot analysis summary table from markdown."""
    shots = []
    # Find the summary table section
    table_section = ""
    in_table = False
    for line in md.split("\n"):
        if "分镜" in line and "总表" in line:
            in_table = True
            continue
        if in_table:
            if line.startswith("|"):
                table_section += line + "\n"
            elif table_section and not line.startswith("|") and line.strip() and not line.startswith("#"):
                break

    # Auto-detect column mapping from header row
    col_map = {}
    header_found = False
    data_lines = []

    for line in table_section.split("\n"):
        if not line.startswith("|") or "---" in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        parts = [p for p in parts if p]

        if not header_found:
            # Try to detect header
            line_text = "|".join(parts).lower()
            if "#" in parts[0] or "镜头" in line_text or "时间" in line_text:
                for i, col in enumerate(parts):
                    col_l = col.lower().strip()
                    if "构图" in col_l or "景别" in col_l:
                        col_map["composition"] = i
                    elif "运镜" in col_l or "角度" in col_l:
                        col_map["camera"] = i
                    elif "光" in col_l or "氛围" in col_l:
                        col_map["lighting"] = i
                    elif "焦" in col_l or "景深" in col_l:
                        col_map["focal"] = i
                    elif "内容" in col_l or "概述" in col_l:
                        col_map["content"] = i
                    elif "描述" in col_l or "语言" in col_l:
                        col_map["description"] = i
                    elif "变速" in col_l:
                        col_map["speed"] = i
                    elif "文字" in col_l:
                        col_map["text"] = i
                header_found = True
                continue

        data_lines.append(parts)

    for parts in data_lines:
        if len(parts) < 3:
            continue
        try:
            shot_num = int(parts[0])
        except ValueError:
            continue

        time_range = parts[1]
        time_parts = re.split(r"[-–—]", time_range)
        start_time = parse_time(time_parts[0]) if len(time_parts) >= 1 else 0
        end_time = parse_time(time_parts[1]) if len(time_parts) >= 2 else 0

        duration_str = parts[2]
        dur_m = re.search(r"([\d.]+)", duration_str)
        duration = float(dur_m.group(1)) if dur_m else end_time - start_time

        def get_col(name, default=""):
            idx = col_map.get(name)
            if idx is not None and idx < len(parts):
                return parts[idx]
            return default

        shot = Shot(
            number=shot_num,
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            composition=get_col("composition", parts[3] if len(parts) > 3 else ""),
            camera_movement=get_col("camera", parts[4] if len(parts) > 4 else ""),
            lighting=get_col("lighting", parts[5] if len(parts) > 5 else ""),
            focal_length=get_col("focal"),
            content=get_col("content", parts[-1] if len(parts) > 5 else ""),
            visual_description=get_col("description"),
        )
        shots.append(shot)

    return shots


def parse_shot_details(md: str, shots: list[Shot]) -> list[Shot]:
    """Enrich shots with data from the detailed per-shot analysis sections."""
    # Split by various shot header formats:
    # "### 镜头 #1" / "**#1｜..." / "**#1**" / "### #1"
    shot_sections = re.split(r"(?:###\s*(?:镜头\s*)?#|(?:^|\n)\*\*#)(\d+)", md)

    for i in range(1, len(shot_sections), 2):
        try:
            shot_num = int(shot_sections[i])
        except ValueError:
            continue
        section = shot_sections[i + 1] if i + 1 < len(shot_sections) else ""

        shot = next((s for s in shots if s.number == shot_num), None)
        if not shot:
            continue

        # Store full detail text (truncated to first section)
        detail = section.split("\n**#")[0].strip()  # Stop at next shot
        if detail:
            shot.detail_text = detail[:500]

        # Extract keyframe filename
        kf_match = re.search(r"f_(\d+)\.jpg", section)
        if kf_match:
            shot.keyframe_path = f"f_{kf_match.group(1)}.jpg"

        # Extract emotion
        emo_match = re.search(r"\*\*情绪[^*]*\*\*[：:]\s*(.+)", section)
        if emo_match:
            shot.emotion = emo_match.group(1).strip()

        # Extract transition
        trans_match = re.search(r"\*\*与上一镜头[^*]*\*\*[：:]\s*(.+)", section)
        if trans_match:
            shot.transition_from_prev = trans_match.group(1).strip()

        # Extract function
        func_match = re.search(r"\*\*镜头功能\*\*[：:]\s*(.+)", section)
        if func_match:
            shot.function = func_match.group(1).strip()

        # Extract visual description if not already set from table
        if not shot.visual_description:
            vis_match = re.search(r"\*\*画面语言描述\*\*[：:]\s*\n?([\s\S]*?)(?=\n\*\*|\n###|\Z)", section)
            if vis_match:
                shot.visual_description = vis_match.group(1).strip()

    return shots


def find_and_copy_frames(md: str, job_id: str) -> str:
    """Find the temp directory used by the skill and copy/link frames to our frames dir."""
    # Look for temp dir path in the markdown or common patterns
    tmp_match = re.search(r"/tmp/video_analyze_[^\s/\"']+", md)
    tmp_dir = tmp_match.group() if tmp_match else None

    frames_dir = FRAMES_DIR / job_id
    frames_dir.mkdir(parents=True, exist_ok=True)

    if tmp_dir and os.path.isdir(tmp_dir):
        # Symlink the temp frames dir contents
        for f in os.listdir(tmp_dir):
            if f.endswith(".jpg"):
                src = os.path.join(tmp_dir, f)
                dst = frames_dir / f
                if not dst.exists():
                    os.symlink(src, str(dst))
        return tmp_dir
    return ""


def set_keyframe_urls(shots: list[Shot], job_id: str) -> list[Shot]:
    """Set API-accessible keyframe URLs for each shot."""
    frames_dir = FRAMES_DIR / job_id
    for shot in shots:
        if shot.keyframe_path and not shot.keyframe_path.startswith("/api/"):
            # keyframe_path is like "f_0012.jpg"
            fname = os.path.basename(shot.keyframe_path)
            if (frames_dir / fname).exists():
                shot.keyframe_path = f"/api/frames/{job_id}/{fname}"
            else:
                # Try to find nearest frame by shot midpoint at 4fps
                mid = (shot.start_time + shot.end_time) / 2
                frame_num = int(mid * 4) + 1
                fname = f"f_{frame_num:04d}.jpg"
                if (frames_dir / fname).exists():
                    shot.keyframe_path = f"/api/frames/{job_id}/{fname}"
                else:
                    shot.keyframe_path = ""
        elif not shot.keyframe_path:
            # Estimate keyframe from midpoint at 4fps
            mid = (shot.start_time + shot.end_time) / 2
            frame_num = int(mid * 4) + 1
            fname = f"f_{frame_num:04d}.jpg"
            if (frames_dir / fname).exists():
                shot.keyframe_path = f"/api/frames/{job_id}/{fname}"
    return shots


async def analyze_video(video_path: str, job_id: str, progress_callback=None) -> AnalysisResult:
    """Full video analysis using the video-analyze skill via Claude CLI."""

    skill_path = SKILL_PATH

    if progress_callback:
        await progress_callback(0.05, "正在启动 video-analyze skill 分析...")

    # Simulate progress with a timer while Claude works
    progress_messages = [
        (10, 0.10, "Claude 正在检测场景切换..."),
        (25, 0.20, "正在提取视频关键帧 (4fps)..."),
        (45, 0.35, "正在逐帧视觉验证镜头边界..."),
        (70, 0.50, "正在分析镜头构图与运镜..."),
        (100, 0.60, "正在分析光影、焦段、情绪..."),
        (140, 0.70, "正在生成场景结构与总结..."),
    ]

    stop_timer = asyncio.Event()

    async def tick_progress():
        start = asyncio.get_event_loop().time()
        msg_idx = 0
        while not stop_timer.is_set():
            elapsed = asyncio.get_event_loop().time() - start
            while msg_idx < len(progress_messages) and elapsed >= progress_messages[msg_idx][0]:
                _, pct, msg = progress_messages[msg_idx]
                if progress_callback:
                    await progress_callback(pct, msg)
                msg_idx += 1
            try:
                await asyncio.wait_for(stop_timer.wait(), timeout=5)
                break
            except asyncio.TimeoutError:
                pass

    timer_task = asyncio.create_task(tick_progress())

    try:
        analysis_md = await claude_with_skill(
            skill_path=str(skill_path),
            prompt=video_path,
            allowed_tools="Bash,Read,Glob,Grep",
            model="sonnet",
        )
    finally:
        stop_timer.set()
        await timer_task

    if progress_callback:
        await progress_callback(0.8, "分析完成，正在解析结果...")

    # Save raw markdown output
    output_dir = BASE_DIR / "outputs"
    output_dir.mkdir(exist_ok=True)
    md_path = output_dir / f"{job_id}_analysis.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(analysis_md)

    # Parse structured data from markdown
    video_info = parse_video_info_table(analysis_md)
    if not video_info.filename:
        video_info.filename = os.path.basename(video_path)

    shots = parse_shots_table(analysis_md)
    shots = parse_shot_details(analysis_md, shots)

    if not video_info.shot_count:
        video_info.shot_count = len(shots)

    # Find and link frames extracted by the skill
    find_and_copy_frames(analysis_md, job_id)
    shots = set_keyframe_urls(shots, job_id)

    # Extract overview
    overview = ""
    overview_match = re.search(r"## 视频概述\s*\n([\s\S]*?)(?=\n---|\n##)", analysis_md)
    if overview_match:
        overview = overview_match.group(1).strip()

    if progress_callback:
        await progress_callback(1.0, "分析完成")

    return AnalysisResult(
        job_id=job_id,
        video_info=video_info,
        shots=shots,
        overview=overview,
        status="completed",
        progress=1.0,
    )
