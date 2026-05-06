"""
Auto editor: skill 做素材分析和匹配，MCP 生成剪映工程，自动打开剪映。
不再使用 ffmpeg 渲染。
"""
import asyncio
import contextlib
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class StageTimer:
    """Records durations of pipeline stages and writes them to a JSON file.

    Usage:
        timer = StageTimer(job_id)
        async with timer.stage("claude_pass1"):
            ...
        timer.save()  # writes outputs/<job_id>_timing.json and logs summary
    """

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.stages: list[dict] = []
        self.t0 = time.perf_counter()

    @contextlib.asynccontextmanager
    async def stage(self, name: str, meta: dict | None = None):
        t_start = time.perf_counter()
        try:
            yield
        finally:
            duration = time.perf_counter() - t_start
            entry = {"name": name, "duration": round(duration, 2)}
            if meta:
                entry.update(meta)
            self.stages.append(entry)
            logger.info("[timing] %s = %.2fs", name, duration)
            print(f"[timing] {name} = {duration:.2f}s", flush=True)

    def mark(self, name: str, duration: float, meta: dict | None = None):
        """Record a stage whose timing was measured outside the context manager."""
        entry = {"name": name, "duration": round(duration, 2)}
        if meta:
            entry.update(meta)
        self.stages.append(entry)
        logger.info("[timing] %s = %.2fs", name, duration)
        print(f"[timing] {name} = {duration:.2f}s", flush=True)

    def save(self):
        total = time.perf_counter() - self.t0
        data = {
            "job_id": self.job_id,
            "total_duration": round(total, 2),
            "stages": self.stages,
        }
        path = OUTPUTS_DIR / f"{self.job_id}_timing.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning("Failed to save timing file: %s", e)
        # Print summary table
        print(f"\n[timing] ===== {self.job_id} total = {total:.2f}s =====", flush=True)
        for s in self.stages:
            pct = (s["duration"] / total * 100) if total > 0 else 0
            print(f"[timing]   {s['name']:40s} {s['duration']:7.2f}s  ({pct:5.1f}%)", flush=True)
        print(f"[timing] ================================================\n", flush=True)

import aiohttp

from models.schemas import AnalysisResult
from services.claude_client import claude_with_skill

BASE_DIR = Path(__file__).parent.parent
OUTPUTS_DIR = BASE_DIR / "outputs"
UPLOADS_DIR = BASE_DIR / "uploads"
EDIT_SKILL_PATH = BASE_DIR / ".claude" / "skills" / "video-edit" / "SKILL.md"
MCP_URL = os.getenv("MCP_URL", "http://localhost:9001")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


_mcp_session = None


class AutoEditError(Exception):
    def __init__(
        self,
        message: str,
        *,
        stage: str,
        endpoint: Optional[str] = None,
        material: Optional[str] = None,
        diagnostics: Optional[dict] = None,
        raw_error: Optional[str] = None,
        warning: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.stage = stage
        self.endpoint = endpoint
        self.material = material
        self.diagnostics = diagnostics or {}
        self.raw_error = raw_error or message
        self.warning = warning

    def to_result(self, *, matches: Optional[list[dict]] = None, draft_dir: Optional[str] = None, draft_name: Optional[str] = None) -> dict:
        diagnostics = dict(self.diagnostics)
        diagnostics.setdefault("raw_error", self.raw_error)
        return {
            "status": "error",
            "stage": self.stage,
            "error": self.message,
            "warning": self.warning,
            "output_path": None,
            "draft_dir": draft_dir,
            "draft_name": draft_name,
            "download_url": None,
            "delivery_mode": "host_draft_dir",
            "open_mode": "auto_open",
            "last_mcp_endpoint": self.endpoint,
            "last_material": self.material,
            "diagnostics": diagnostics,
            "matches": matches or [],
        }


async def _get_mcp_session():
    global _mcp_session
    if _mcp_session is None or _mcp_session.closed:
        _mcp_session = aiohttp.ClientSession()
    return _mcp_session


async def _mcp_get(endpoint: str) -> dict:
    session = await _get_mcp_session()
    async with session.get(f"{MCP_URL}{endpoint}",
                           timeout=aiohttp.ClientTimeout(total=30)) as r:
        return await r.json()


async def _mcp_post(endpoint: str, data: dict) -> dict:
    session = await _get_mcp_session()
    async with session.post(f"{MCP_URL}{endpoint}", json=data,
                            timeout=aiohttp.ClientTimeout(total=30)) as r:
        return await r.json()


def _summarize_mcp_error(resp: dict) -> str:
    message = resp.get("error") or resp.get("message") or "未知 MCP 错误"
    details = resp.get("details")
    if isinstance(details, dict) and details:
        compact = ", ".join(f"{k}={v}" for k, v in details.items())
        return f"{message} ({compact})"
    return str(message)


async def _mcp_post_checked(
    endpoint: str,
    data: dict,
    *,
    stage: str,
    diagnostics: dict,
    material: Optional[str] = None,
    progress_callback=None,
) -> dict:
    diagnostics["last_mcp_endpoint"] = endpoint
    if material:
        diagnostics["last_material"] = material
    if progress_callback and diagnostics.get("message"):
        await progress_callback(
            diagnostics.get("progress", 0),
            diagnostics.get("message", ""),
            stage=stage,
            last_mcp_endpoint=endpoint,
            last_material=material,
            diagnostics=diagnostics,
        )
    try:
        resp = await _mcp_post(endpoint, data)
    except Exception as e:
        raise AutoEditError(
            f"MCP 请求失败: {endpoint}: {e}",
            stage=stage,
            endpoint=endpoint,
            material=material,
            diagnostics=diagnostics,
            raw_error=repr(e),
        ) from e
    if not resp.get("success", False):
        raise AutoEditError(
            _summarize_mcp_error(resp),
            stage=stage,
            endpoint=endpoint,
            material=material,
            diagnostics={**diagnostics, "mcp_response": resp},
            raw_error=json.dumps(resp, ensure_ascii=False),
        )
    return resp


def _find_template_video(job_id: str) -> Optional[str]:
    for f in UPLOADS_DIR.iterdir():
        if f.name.startswith(job_id) and f.suffix in (".mp4", ".mov", ".avi", ".mkv"):
            return str(f)
    return None


def _validate_material_path(path: str):
    if not path:
        raise AutoEditError("素材路径为空", stage="material_validation")
    if not os.path.exists(path):
        raise AutoEditError("素材文件不存在", stage="material_validation", material=path)
    if not os.path.isfile(path):
        raise AutoEditError("素材路径不是文件", stage="material_validation", material=path)
    if os.path.getsize(path) <= 0:
        raise AutoEditError("素材文件为空", stage="material_validation", material=path)



def _probe_video_material(path: str):
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        raise AutoEditError(
            f"视频素材预检失败: {e}",
            stage="material_validation",
            material=path,
            raw_error=repr(e),
        ) from e
    if result.returncode != 0:
        raise AutoEditError(
            f"视频素材不可读: {result.stderr.strip() or result.stdout.strip() or 'ffprobe failed'}",
            stage="material_validation",
            material=path,
        )


def _validate_material_file(path: str):
    _validate_material_path(path)
    if Path(path).suffix.lower() in VIDEO_EXTS:
        _probe_video_material(path)


async def _check_draft_output(progress_callback=None) -> dict:
    try:
        health = await _mcp_get("/health")
    except Exception as e:
        raise AutoEditError(
            f"无法连接剪映 MCP 服务: {e}",
            stage="mcp_preflight",
            endpoint="/health",
            raw_error=repr(e),
        ) from e
    diagnostics = {
        "mcp_health": health,
        "draft_output_dir": health.get("draft_output_dir"),
        "draft_host_dir": health.get("draft_host_dir"),
    }
    if progress_callback:
        await progress_callback(
            0.58,
            "正在检查剪映草稿目录与 MCP 服务...",
            stage="mcp_preflight",
            diagnostics=diagnostics,
        )
    if not health.get("success", False):
        raise AutoEditError(
            health.get("error", "剪映草稿目录检查失败"),
            stage="mcp_preflight",
            endpoint="/health",
            diagnostics=diagnostics,
            raw_error=json.dumps(health, ensure_ascii=False),
        )
    return diagnostics


async def auto_edit(
    template: AnalysisResult,
    material_paths: list[str],
    job_id: str,
    progress_callback=None,
    user_prompt: str = "",
) -> dict:
    """
    Phase 1: skill 做素材分析、分组、择优、匹配（完整智能流程）
    Phase 2: 解析 skill 匹配结果
    Phase 3: MCP 生成剪映工程 + 自动打开剪映
    """
    template_md_path = OUTPUTS_DIR / f"{template.job_id}_analysis.md"
    materials_dir = str(Path(material_paths[0]).parent) if material_paths else ""
    template_video = _find_template_video(template.job_id)
    draft_name = f"edit_{job_id}"
    timer = StageTimer(job_id)
    diagnostics: dict = {
        "job_id": job_id,
        "template_job_id": template.job_id,
        "materials_dir": materials_dir,
    }

    if not template_md_path.exists():
        return AutoEditError(
            "模板分析文件不存在",
            stage="template_validation",
            diagnostics=diagnostics,
        ).to_result(matches=[], draft_name=draft_name)

    try:
        if progress_callback:
            await progress_callback(0.02, "正在校验素材文件...", stage="material_validation")
        if not material_paths:
            raise AutoEditError(
                "未上传任何素材",
                stage="material_validation",
                diagnostics=diagnostics,
            )
        for path in material_paths:
            _validate_material_file(path)
        diagnostics.update(await _check_draft_output(progress_callback))
    except AutoEditError as e:
        return e.to_result(matches=[], draft_name=draft_name)

    # ============================================================
    # Phase 1: Skill 做素材分析 + 匹配
    # ============================================================
    if progress_callback:
        await progress_callback(0.05, "正在启动 AI 素材分析与匹配...", stage="ai_matching")

    # Skill prompt — 只做分析匹配，不做 ffmpeg 渲染
    prompt = (
        f"{template_md_path} {materials_dir} --output none --audio silent\n\n"
        f"注意：只需要完成 Step 1-5（分析、节拍检测、素材分析分组、匹配、转场选择），"
        f"不需要执行 Step 6-9 的渲染和拼接。直接输出匹配报告即可。"
    )

    if user_prompt:
        prompt += f"\n\n## 用户额外指令（优先级最高，必须遵守）：\n{user_prompt}"

    skill_path = EDIT_SKILL_PATH

    stop_timer = asyncio.Event()
    msgs = [
        (8, 0.08, "正在解析模板分镜结构..."),
        (15, 0.15, "正在检测音频节拍..."),
        (30, 0.22, "正在分析素材内容与稳定性..."),
        (50, 0.32, "正在素材分组与择优..."),
        (70, 0.42, "正在智能匹配素材到镜头..."),
        (90, 0.52, "正在选择转场效果..."),
    ]

    async def tick():
        start = asyncio.get_event_loop().time()
        idx = 0
        while not stop_timer.is_set():
            elapsed = asyncio.get_event_loop().time() - start
            while idx < len(msgs) and elapsed >= msgs[idx][0]:
                if progress_callback:
                    await progress_callback(msgs[idx][1], msgs[idx][2], stage="ai_matching")
                idx += 1
            try:
                await asyncio.wait_for(stop_timer.wait(), timeout=5)
                break
            except asyncio.TimeoutError:
                pass

    tick_task = asyncio.create_task(tick())
    try:
        async with timer.stage("claude_pass1"):
            report = await claude_with_skill(
                skill_path=str(skill_path), prompt=prompt,
                allowed_tools="Bash,Read,Glob,Grep", model="sonnet",
            )
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[auto_edit] Phase 1 failed: {repr(e)}", flush=True)
        stop_timer.set()
        await tick_task
        return AutoEditError(
            str(e) or repr(e),
            stage="ai_matching",
            diagnostics=diagnostics,
            raw_error=repr(e),
        ).to_result(matches=[], draft_name=draft_name)

    stop_timer.set()
    await tick_task
    print(f"[auto_edit] Phase 1 done, report length: {len(report)}", flush=True)

    # Save report
    report_path = OUTPUTS_DIR / f"{job_id}_edit_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    diagnostics["report_path"] = str(report_path)

    # Parse matches: priority 1 = EDIT_CONFIG JSON, priority 2 = markdown table, priority 3 = AI quick match
    matches = _parse_edit_config_full(report).get("matches", [])
    if not matches:
        matches = _parse_report_matches(report)
    # Only fallback if significantly incomplete (missing >2 shots)
    # Small differences (1-2) may be intentional merges by skill
    if len(matches) < len(template.shots) - 2:
        if progress_callback:
            await progress_callback(
                0.55,
                f"匹配不完整（{len(matches)}/{len(template.shots)}），快速补充...",
                stage="match_fallback",
            )
        fallback = await _ai_quick_match(template, material_paths, materials_dir)
        matched_shots = {m["shot_number"] for m in matches}
        for fb in fallback:
            if fb["shot_number"] not in matched_shots:
                matches.append(fb)

    print(f"[auto_edit] Matches: {len(matches)}, shots: {len(template.shots)}", flush=True)

    # Diagnostic: count material reuse (helpful for spotting accidental repetition)
    if matches:
        from collections import Counter
        _mat_counts = Counter(m.get("material", "") for m in matches if m.get("material"))
        _reused = [(k, v) for k, v in _mat_counts.most_common() if v >= 2]
        if _reused:
            _preview = ", ".join(f"{os.path.basename(k)}×{v}" for k, v in _reused[:5])
            print(
                f"[auto_edit] material reuse: {len(_reused)} clips reused "
                f"(unique={len(_mat_counts)}, total_refs={sum(_mat_counts.values())}) | {_preview}",
                flush=True,
            )

    if progress_callback:
        await progress_callback(0.60, "素材匹配完成，正在生成剪映工程...", stage="draft_generation")

    # ============================================================
    # Phase 2: MCP 生成剪映工程
    # ============================================================
    try:
        async with timer.stage("draft_generation"):
            project = await _create_capcut_project(
                template, matches, material_paths, materials_dir,
                template_video, job_id, report,
                draft_name=draft_name,
                progress_callback=progress_callback,
                user_prompt=user_prompt,
                diagnostics=diagnostics,
                timer=timer,
            )
        draft_dir = project["draft_dir"]
    except Exception as e:
        import traceback
        traceback.print_exc()
        timer.save()
        if isinstance(e, AutoEditError):
            return e.to_result(matches=matches, draft_name=draft_name)
        return AutoEditError(
            f"剪映工程生成失败: {e}",
            stage="draft_generation",
            diagnostics=diagnostics,
            raw_error=repr(e),
        ).to_result(matches=matches, draft_name=draft_name)
    timer.save()

    if progress_callback:
        await progress_callback(
            0.90,
            "剪映工程已生成，正在打开剪映...",
            stage="opening_capcut",
            draft_dir=draft_dir,
            diagnostics=diagnostics,
        )

    # ============================================================
    # Phase 3: 自动打开剪映
    # ============================================================
    warning_parts = []
    if project.get("warning"):
        warning_parts.append(project["warning"])
    open_warning = _open_capcut(draft_dir)
    if open_warning:
        warning_parts.append(open_warning)
    warning = "；".join(warning_parts) if warning_parts else None
    status = "completed_with_warning" if warning else "completed"
    open_mode = "manual_open" if warning else "auto_open"

    if progress_callback:
        final_message = "草稿已生成，但未能自动打开剪映，请手动打开" if warning else "完成！请在剪映中查看并精调"
        await progress_callback(
            1.0,
            final_message,
            stage="completed",
            draft_dir=draft_dir,
            diagnostics=diagnostics,
        )

    return {
        "status": status,
        "stage": "completed",
        "output_path": None,
        "draft_dir": draft_dir,
        "draft_name": project["draft_name"],
        "download_url": None,
        "warning": warning,
        "delivery_mode": "host_draft_dir",
        "open_mode": open_mode,
        "last_mcp_endpoint": diagnostics.get("last_mcp_endpoint"),
        "last_material": diagnostics.get("last_material"),
        "diagnostics": diagnostics,
        "matches": matches,
    }


# ============================================================
# MCP: 生成剪映工程
# ============================================================

async def _apply_segment_effects(draft_id: str, seg_idx: int, match: dict, duration: float):
    """Apply keyframes, masks, effects, filters, fade from EDIT_CONFIG match."""
    # Keyframes (camera movement simulation)
    keyframes = match.get("keyframes", [])
    for kf in keyframes:
        try:
            await _mcp_post("/add_keyframe", {
                "draft_id": draft_id,
                "segment_index": seg_idx,
                "property": kf.get("property", "scale"),
                "time": kf.get("time", 0),
                "value": kf.get("value", 1.0),
            })
        except Exception:
            pass

    # Mask
    mask = match.get("mask")
    if mask and isinstance(mask, dict):
        try:
            await _mcp_post("/add_mask", {
                "draft_id": draft_id,
                "segment_index": seg_idx,
                **mask,
            })
        except Exception:
            pass

    # Effect
    effect = match.get("effect")
    if effect and isinstance(effect, str):
        try:
            await _mcp_post("/add_effect", {
                "draft_id": draft_id,
                "segment_index": seg_idx,
                "effect_name": effect,
                "effect_category": match.get("effect_category", "scene"),
            })
        except Exception:
            pass

    # Filter
    filt = match.get("filter")
    if filt and isinstance(filt, str):
        try:
            await _mcp_post("/add_filter", {
                "draft_id": draft_id,
                "segment_index": seg_idx,
                "filter_name": filt,
                "intensity": match.get("filter_intensity", 100),
            })
        except Exception:
            pass

    # Fade
    fade_in = match.get("fade_in", 0)
    fade_out = match.get("fade_out", 0)
    if fade_in or fade_out:
        try:
            await _mcp_post("/add_fade", {
                "draft_id": draft_id,
                "segment_index": seg_idx,
                "fade_in": fade_in,
                "fade_out": fade_out,
            })
        except Exception:
            pass


async def _create_capcut_project(
    template: AnalysisResult,
    matches: list[dict],
    material_paths: list[str],
    materials_dir: str,
    template_video: Optional[str],
    job_id: str,
    report: str = "",
    draft_name: Optional[str] = None,
    progress_callback=None,
    user_prompt: str = "",
    diagnostics: Optional[dict] = None,
    timer=None,  # StageTimer; passed separately to avoid JSON-serialization of diagnostics
) -> dict:
    diagnostics = diagnostics or {}
    draft_name = draft_name or f"edit_{job_id}"

    info = template.video_info
    width, height, fps = 720, 1280, 30
    if info.resolution:
        parts = re.split(r"[x×]", info.resolution)
        if len(parts) == 2:
            try:
                width, height = int(parts[0]), int(parts[1])
            except ValueError:
                pass
    if info.fps:
        fps = int(info.fps)

    diagnostics.update({
        "canvas_width": width,
        "canvas_height": height,
        "canvas_fps": fps,
    })
    resp = await _mcp_post_checked(
        "/create_draft",
        {"name": draft_name, "width": width, "height": height, "fps": fps},
        stage="draft_generation",
        diagnostics=diagnostics,
        progress_callback=progress_callback,
    )

    draft_id = resp["draft_id"]
    draft_dir = resp["draft_dir"]
    diagnostics["draft_dir"] = draft_dir

    current_time = 0.0
    shots = sorted(template.shots, key=lambda s: s.number)

    _t_shots = time.perf_counter()
    for shot in shots:
        mat_file = _find_match_material(shot.number, matches, material_paths, materials_dir)
        duration = shot.duration if shot.duration > 0 else 3.0

        # Fallback chain: if match failed, try harder
        if not mat_file or not os.path.exists(mat_file):
            # Fallback 1: search all materials for any file containing the match name
            m = next((x for x in matches if x.get("shot_number") == shot.number), {})
            mat_name = m.get("material", "")
            if mat_name:
                for f in _listdir_media(materials_dir):
                    if any(d in f for d in re.findall(r'\d{4}', mat_name)):
                        mat_file = os.path.join(materials_dir, f)
                        break

        if not mat_file or not os.path.exists(mat_file):
            # Fallback 2: use the longest unused video material
            used_files = set()
            for s in shots:
                mf = _find_match_material(s.number, matches, material_paths, materials_dir)
                if mf:
                    used_files.add(os.path.basename(mf))
            media_files = _listdir_media(materials_dir)
            for f in sorted(media_files, key=lambda x: os.path.getsize(os.path.join(materials_dir, x)), reverse=True):
                if f not in used_files and not f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    mat_file = os.path.join(materials_dir, f)
                    break

        if not mat_file or not os.path.exists(mat_file):
            # Fallback 3: use ANY video file (never show black)
            for f in _listdir_media(materials_dir):
                if f.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                    mat_file = os.path.join(materials_dir, f)
                    break

        if not mat_file or not os.path.exists(mat_file):
            raise AutoEditError(
                f"镜头 {shot.number} 没有可用素材",
                stage="material_selection",
                material=next((x.get("material") for x in matches if x.get("shot_number") == shot.number), None),
                diagnostics={**diagnostics, "shot_number": shot.number},
            )

        _validate_material_file(mat_file)
        m = next((x for x in matches if x.get("shot_number") == shot.number), {})
        trim_start = 0
        ts = m.get("trim_start", 0)
        if isinstance(ts, (int, float)):
            trim_start = ts
        else:
            try:
                trim_start = float(ts)
            except (ValueError, TypeError):
                pass

        # Get transition from EDIT_CONFIG match (most reliable)
        transition = None
        trans_dur = 0.5
        match_transition = m.get("transition", "")
        match_trans_dur = m.get("transition_duration", 0)
        if match_transition and match_transition != "硬切":
            transition = match_transition
            trans_dur = match_trans_dur if match_trans_dur else 0.5
        elif hasattr(shot, 'transition_from_prev') and shot.transition_from_prev:
            # Fallback: parse from template analysis
            t = shot.transition_from_prev
            jy_match = re.search(r'\(([^,]+)', t)
            if jy_match:
                transition = jy_match.group(1).strip()
                dur_match = re.search(r'约?([\d.]+)s', t)
                if dur_match:
                    trans_dur = float(dur_match.group(1))

        is_img = os.path.splitext(mat_file)[1].lower() in IMAGE_EXTS
        endpoint = "/add_image" if is_img else "/add_video"
        diagnostics.update({
            "shot_number": shot.number,
            "progress": min(0.88, 0.60 + (shot.number / max(1, len(shots))) * 0.22),
            "message": f"正在写入镜头 {shot.number}/{len(shots)}...",
        })

        data = {
            "draft_id": draft_id,
            "start": current_time,
            "duration": duration,
        }
        if is_img:
            data["image_path"] = os.path.abspath(mat_file)
        else:
            data["video_path"] = os.path.abspath(mat_file)
            data["trim_start"] = trim_start
            if transition:
                data["transition"] = transition
                data["transition_duration"] = trans_dur

        intro = m.get("intro_animation")
        intro_dur = m.get("intro_duration", 0.5)
        if intro:
            data["intro_animation"] = intro
            data["intro_duration"] = intro_dur
        outro = m.get("outro_animation")
        if outro:
            data["outro_animation"] = outro
            data["outro_duration"] = m.get("outro_duration", 0.5)

        seg_resp = await _mcp_post_checked(
            endpoint,
            data,
            stage="draft_generation",
            diagnostics=diagnostics,
            material=os.path.abspath(mat_file),
            progress_callback=progress_callback,
        )
        seg_idx = seg_resp.get("segment_index", shot.number - 1)

        # Apply post-effects from EDIT_CONFIG match
        await _apply_segment_effects(draft_id, seg_idx, m, duration)

        current_time += duration

    if timer:
        timer.mark("main_track_insertion", time.perf_counter() - _t_shots,
                   {"segments": len(shots)})

    # Text overlays — from template analysis TEXT_CONFIG
    analysis_md_path = str(OUTPUTS_DIR / f"{template.job_id}_analysis.md")
    text_info = _extract_text_info_from_analysis(analysis_md_path)

    # Calculate cumulative time offsets for each shot
    shot_timeline = {}
    t = 0.0
    for shot in shots:
        shot_timeline[shot.number] = {"start": t, "end": t + shot.duration}
        t += shot.duration

    # Find which shots use image materials (they already have text/logo baked in)
    image_shot_numbers = set()
    for m_item in matches:
        mat_name = m_item.get("material", "")
        if mat_name and mat_name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            image_shot_numbers.add(m_item.get("shot_number"))

    # Text overlays — Priority 1: from EDIT_CONFIG's text_overlays (skill judged needs_overlay)
    edit_config_full = _parse_edit_config_full(report)
    text_overlays_from_edit = edit_config_full.get("text_overlays", [])
    text_added = False

    for bt in text_overlays_from_edit:
        if not bt.get("needs_overlay", True):
            continue
        sn = bt.get("shot_numbers", [])
        valid_sn = [n for n in sn if n in shot_timeline]
        if not valid_sn:
            continue

        t_start = shot_timeline[valid_sn[0]]["start"]
        t_end = shot_timeline[valid_sn[-1]]["end"]
        t_dur = t_end - t_start
        if t_dur <= 0:
            continue

        overlay_type = bt.get("type", "text")

        if overlay_type == "image_overlay":
            img_file = bt.get("image_file", "")
            img_path = _find_match_material_by_name(img_file, material_paths, materials_dir)
            if not img_path:
                continue

            # Skip if the overlay image is the same as the shot's main material
            # (e.g. LOGO shot already uses this image, no need to overlay again)
            skip = False
            for sn_num in valid_sn:
                m = next((x for x in matches if x.get("shot_number") == sn_num), {})
                mat_name = m.get("material", "")
                if img_file and mat_name and (img_file in mat_name or mat_name in img_file):
                    skip = True
                    break
            if skip:
                continue

            # Use start_offset/duration_override from EDIT_CONFIG if provided
            start_offset = bt.get("start_offset") or 0
            overlay_start = t_start + start_offset
            overlay_dur = bt.get("overlay_duration") or (t_dur - start_offset)

            await _mcp_post_checked(
                "/add_image_overlay",
                {
                    "draft_id": draft_id,
                    "image_path": os.path.abspath(img_path),
                    "start": overlay_start,
                    "duration": overlay_dur,
                    "alpha": bt.get("alpha", 0.8),
                    "scale": bt.get("scale", 0.75),
                    "position_x": bt.get("position_x", 0),
                    "position_y": bt.get("position_y", 0),
                },
                stage="draft_generation",
                diagnostics=diagnostics,
                material=os.path.abspath(img_path),
                progress_callback=progress_callback,
            )
            text_added = True
        else:
            # Generate text via add_text
            text = bt.get("text", "")
            if not text:
                continue
            pct = bt.get("font_size_percent", 8)
            font_size = max(8, round(pct * height / 100 / 7.2))
            anim = bt.get("animation", "none")
            anim_dur = bt.get("animation_duration", 0.5)

            await _mcp_post_checked(
                "/add_text",
                {
                    "draft_id": draft_id,
                    "text": text,
                    "start": t_start + (0.3 if anim == "fade_in" else 0),
                    "duration": t_dur - (0.3 if anim == "fade_in" else 0),
                    "font_size": font_size,
                    "color": bt.get("color_hex", "#FFFFFF"),
                    "position_y": bt.get("position_y", 0),
                    "animation_in": "渐显" if anim == "fade_in" else "",
                    "animation_in_duration": anim_dur,
                },
                stage="draft_generation",
                diagnostics=diagnostics,
                progress_callback=progress_callback,
            )
            text_added = True

    # Priority 2: fallback to analyze TEXT_CONFIG (only if EDIT_CONFIG had nothing)
    if not text_added and text_info.get("all_brand_texts"):
        for bt in text_info["all_brand_texts"]:
            text = bt.get("text", "")
            if not text:
                continue
            sn = bt.get("shot_numbers", [])
            valid_sn = [n for n in sn if n in shot_timeline and n not in image_shot_numbers]
            if not valid_sn:
                continue
            t_start = shot_timeline[valid_sn[0]]["start"]
            t_end = shot_timeline[valid_sn[-1]]["end"]
            t_dur = t_end - t_start
            if t_dur <= 0:
                continue
            pct = bt.get("font_size_percent", 8)
            font_size = max(8, round(pct * 1280 / 100 / 7.2))
            anim = bt.get("animation", "none")
            anim_dur = bt.get("animation_duration", 0.5)
            await _mcp_post_checked(
                "/add_text",
                {
                    "draft_id": draft_id, "text": text,
                    "start": t_start + 0.3, "duration": t_dur - 0.3,
                    "font_size": font_size,
                    "color": bt.get("color_hex", "#FFFFFF"),
                    "position_y": bt.get("position_y", 0),
                    "animation_in": "渐显" if anim == "fade_in" else "",
                    "animation_in_duration": anim_dur,
                },
                stage="draft_generation",
                diagnostics=diagnostics,
                progress_callback=progress_callback,
            )
            text_added = True

    # Priority 3: basic brand text extraction (also used for auto-sourced story title card)
    if not text_added and text_info.get("text"):
        brand_text = text_info["text"]
        text_shots = [s for s in shots if s.number in text_info.get("shot_numbers", [1, 2])]
        if text_shots:
            t_start = shot_timeline[text_shots[0].number]["start"]
            t_end = shot_timeline[text_shots[-1].number]["end"]
            _payload = {
                "draft_id": draft_id,
                "text": brand_text,
                "start": t_start + 0.3,
                "duration": t_end - t_start - 0.3,
                "font_size": text_info.get("font_size", 10),
                "color": text_info.get("color", "#5EEDC7"),
                "position_y": text_info.get("position_y", 0),
                "animation_in": "渐显",
                "animation_in_duration": text_info.get("animation_duration", 0.75),
            }
            # Flower text effect (for story title cards — big centered flower text)
            if text_info.get("flower_text_effect_id"):
                _payload["flower_text_effect_id"] = text_info["flower_text_effect_id"]
            await _mcp_post_checked(
                "/add_text",
                _payload,
                stage="draft_generation",
                diagnostics=diagnostics,
                progress_callback=progress_callback,
            )

    # Stabilization — default all video segments to recommended (level=2)
    await _mcp_post_checked(
        "/set_stabilization",
        {
            "draft_id": draft_id,
            "stable_level": 2,
        },
        stage="draft_generation",
        diagnostics=diagnostics,
        progress_callback=progress_callback,
    )

    # Color adjustments — from EDIT_CONFIG per-shot color_adjust
    for i, shot in enumerate(shots):
        m = next((x for x in matches if x.get("shot_number") == shot.number), {})
        ca = m.get("color_adjust")
        if ca and isinstance(ca, dict):
            await _mcp_post_checked(
                "/set_color_adjust",
                {
                    "draft_id": draft_id,
                    "segment_index": i,
                    **{k: v for k, v in ca.items() if v is not None},
                },
                stage="draft_generation",
                diagnostics=diagnostics,
                progress_callback=progress_callback,
            )

    # Vocal separation — based on template analysis audio characteristics
    # Only apply when template shot has no voice but material might have unwanted voice
    for i, shot in enumerate(shots):
        # Skip image materials
        m = next((x for x in matches if x.get("shot_number") == shot.number), {})
        mat_name = m.get("material", "")
        if mat_name and mat_name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            continue

        # Check template shot's audio type from analysis
        # If template is BGM/环境音/静音 (no voice), separate vocals from material
        content = (shot.content or "").lower()
        has_voice_in_template = any(k in content for k in ["对话", "采访", "主持", "旁白", "讲解", "说话", "口播"])

        if not has_voice_in_template:
            # Template has no voice for this shot — remove any unwanted voice from material
            await _mcp_post_checked(
                "/set_vocal_separation",
                {
                    "draft_id": draft_id,
                    "segment_index": i,
                    "choice": 1,
                },
                stage="draft_generation",
                diagnostics=diagnostics,
                progress_callback=progress_callback,
            )

    # Audio — check EDIT_CONFIG and user prompt for audio preferences
    edit_config = _parse_edit_config_full(report)
    skip_extra_audio = edit_config.get("keep_original_audio", False)
    if any(kw in user_prompt.lower() for kw in ["保留原声", "原声", "口播", "保留声音", "keep audio", "keep voice", "原视频音频"]):
        skip_extra_audio = True

    bgm_file = None
    if not skip_extra_audio:
        for p in material_paths:
            if os.path.basename(p).startswith("bgm_") and p.lower().endswith((".mp3", ".wav", ".aac")):
                bgm_file = p
                break
        if not bgm_file:
            for p in material_paths:
                if p.lower().endswith((".mp3", ".wav", ".aac", ".m4a")):
                    bgm_file = p
                    break

    audio_path = bgm_file or (template_video if template_video and not skip_extra_audio else None)
    if audio_path and os.path.exists(audio_path):
        _validate_material_file(audio_path)
        # Get audio duration to pick best segment
        audio_trim_start = 0
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", audio_path],
                capture_output=True, text=True, timeout=10,
            )
            audio_dur = float(probe.stdout.strip())
            video_dur = current_time
            if audio_dur > video_dur * 1.5:
                # Audio is much longer than video — pick a good starting point
                # Strategy: start from 10-20% into the song (skip intro)
                audio_trim_start = min(audio_dur * 0.15, audio_dur - video_dur)
                audio_trim_start = max(0, audio_trim_start)
                print(f"[auto_edit] BGM trimmed: start={audio_trim_start:.1f}s, "
                      f"audio_dur={audio_dur:.1f}s, video_dur={video_dur:.1f}s", flush=True)
        except Exception:
            pass

        await _mcp_post_checked(
            "/add_audio",
            {
                "draft_id": draft_id,
                "audio_path": os.path.abspath(audio_path),
                "start": 0,
                "duration": current_time,
                "trim_start": audio_trim_start,
                "fade_in": 0.5,
                "fade_out": 1.0,
            },
            stage="draft_generation",
            diagnostics=diagnostics,
            material=os.path.abspath(audio_path),
            progress_callback=progress_callback,
        )

    _t_save = time.perf_counter()
    save_resp = await _mcp_post_checked(
        "/save_draft",
        {"draft_id": draft_id, "name": draft_name},
        stage="saving_draft",
        diagnostics=diagnostics,
        progress_callback=progress_callback,
    )
    if timer:
        timer.mark("save_draft", time.perf_counter() - _t_save)
    diagnostics["saved_draft_file"] = save_resp.get("draft_file")
    diagnostics["draft_open_path"] = save_resp.get("draft_open_path", save_resp.get("draft_dir"))
    return {
        "draft_dir": save_resp.get("draft_open_path", save_resp.get("draft_dir", draft_dir)),
        "draft_name": save_resp.get("draft_name", draft_name),
        "warning": save_resp.get("warning"),
    }


# ============================================================
# 自动打开剪映
# ============================================================

def _open_capcut(draft_dir: str):
    """Open JianYing/CapCut. Draft is already in its drafts folder."""
    import platform
    if platform.system() != "Darwin":
        return "当前系统不支持自动打开剪映，请手动打开草稿目录"
    for app in ["VideoFusion-macOS", "剪映专业版", "JianyingPro", "CapCut"]:
        result = subprocess.run(
            ["open", "-a", app],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return None
    try:
        subprocess.Popen(["open", draft_dir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return f"未找到可自动打开的剪映应用，请手动打开草稿目录: {draft_dir}"
    return f"未找到可自动打开的剪映应用，已打开草稿目录: {draft_dir}"


# ============================================================
# 解析 skill 报告
# ============================================================

async def _ai_quick_match(
    template: AnalysisResult, material_paths: list[str], materials_dir: str,
) -> list[dict]:
    """Fallback: use Claude haiku for quick material matching when report parsing fails."""
    from services.claude_client import claude_query

    # Build shot descriptions
    shot_lines = []
    for s in template.shots:
        shot_lines.append(
            f"镜头{s.number}: {s.duration:.1f}s | {s.composition} | {s.camera_movement} | {s.content}"
        )

    # Build material list
    mat_lines = []
    for i, p in enumerate(material_paths):
        name = os.path.basename(p)
        ext = os.path.splitext(p)[1].lower()
        is_img = ext in (".jpg", ".jpeg", ".png", ".webp")
        dur = 0
        if not is_img:
            try:
                r = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "csv=p=0", p],
                    capture_output=True, text=True, timeout=5)
                dur = float(r.stdout.strip())
            except Exception:
                pass
        mat_lines.append(f"素材{i}: [{name}] {'图片' if is_img else f'视频{dur:.1f}s'}")

    prompt = f"""为每个镜头匹配最合适的素材。不同镜头用不同素材，LOGO镜头用图片素材。

镜头：
{chr(10).join(shot_lines)}

素材：
{chr(10).join(mat_lines)}

返回JSON数组：[{{"shot_number":1,"material_index":0,"trim_start":0.0}}]
只返回JSON。"""

    try:
        text = await claude_query(prompt, model="haiku", allowed_tools="")
        json_match = re.search(r'\[.*\]', text, re.DOTALL)
        if json_match:
            ai_matches = json.loads(json_match.group())
            result = []
            for m in ai_matches:
                idx = m.get("material_index", 0)
                mat_name = os.path.basename(material_paths[idx]) if idx < len(material_paths) else ""
                result.append({
                    "shot_number": m.get("shot_number", 0),
                    "material": mat_name,
                    "trim_start": m.get("trim_start", 0),
                    "reason": "AI quick match",
                })
            return result
    except Exception as e:
        print(f"AI quick match failed: {e}")

    # Last resort: round-robin assign
    result = []
    video_mats = [p for p in material_paths if not os.path.splitext(p)[1].lower() in (".jpg", ".jpeg", ".png", ".webp")]
    img_mats = [p for p in material_paths if os.path.splitext(p)[1].lower() in (".jpg", ".jpeg", ".png", ".webp")]

    for i, shot in enumerate(sorted(template.shots, key=lambda s: s.number)):
        if any(k in (shot.content or "").lower() for k in ["logo", "品牌"]) and img_mats:
            mat = img_mats[0]
        else:
            mat = video_mats[i % len(video_mats)] if video_mats else material_paths[i % len(material_paths)]
        result.append({
            "shot_number": shot.number,
            "material": os.path.basename(mat),
            "trim_start": 0,
            "reason": "fallback round-robin",
        })
    return result


def _extract_text_info_from_analysis(md_path: str) -> dict:
    """Extract text overlay info from analysis markdown.
    Tries structured JSON first (TEXT_CONFIG), falls back to heuristic parsing."""
    info = {"text": "", "font_size": 10, "color": "#5EEDC7", "position_y": -0.06, "shot_numbers": []}

    if not os.path.exists(md_path):
        return info

    try:
        with open(md_path) as f:
            md = f.read()
    except Exception:
        return info

    # === Priority 1: Parse structured JSON (TEXT_CONFIG_START/END) ===
    # Support both with and without ```json code fence
    json_match = re.search(
        r'<!-- TEXT_CONFIG_START -->\s*(?:```json\s*)?(\{[\s\S]*?\})\s*(?:```\s*)?<!-- TEXT_CONFIG_END -->',
        md
    )
    if json_match:
        try:
            raw = json_match.group(1)
            try:
                config = json.loads(raw)
            except json.JSONDecodeError:
                from json_repair import repair_json
                config = json.loads(repair_json(raw))
            # Return full config for _create_capcut_project to use
            info["_full_config"] = config

            if config.get("brand_texts"):
                bt = config["brand_texts"][0]
                info["text"] = bt.get("text", "")
                info["shot_numbers"] = bt.get("shot_numbers", [1, 2])
                pct = bt.get("font_size_percent", 8)
                # jianying size: 8%→10, 10%→12, 15%→18
                info["font_size"] = max(8, min(int(pct * 1.25), 18))
                info["color"] = bt.get("color_hex", "#5EEDC7")
                info["position_y"] = bt.get("position_y", -0.06)
                info["all_brand_texts"] = config.get("brand_texts", [])
                info["logo"] = config.get("logo")
                return info
        except (json.JSONDecodeError, KeyError, IndexError):
            pass

    # === Priority 2: Heuristic parsing (fallback for old analyses) ===

    # Extract brand text
    m = re.search(r'[""「]([A-Z]+\s+[\u4e00-\u9fff]+[^""」]*)[""」]', md)
    if m:
        info["text"] = m.group(1)

    # Find which shots have text
    for line in md.split("\n"):
        if not line.startswith("|") or "---" in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        parts = [p for p in parts if p]
        if len(parts) < 3:
            continue
        try:
            shot_num = int(re.search(r"\d+", parts[0]).group())
        except (ValueError, AttributeError):
            continue
        line_text = "|".join(parts)
        if info["text"] and info["text"][:5] in line_text:
            info["shot_numbers"].append(shot_num)

    info["shot_numbers"] = sorted(set(info["shot_numbers"]))
    if info["shot_numbers"]:
        mid = max(info["shot_numbers"]) // 2 + 1
        info["shot_numbers"] = [n for n in info["shot_numbers"] if n <= mid]
    if not info["shot_numbers"] and info["text"]:
        info["shot_numbers"] = [1, 2]

    # Font size from "约8%高度"
    size_match = re.search(r'约?(\d+)%.*?高度', md)
    if size_match:
        pct = int(size_match.group(1))
        info["font_size"] = max(8, round(pct * 1280 / 100 / 7.2))

    # Color
    color_match = re.search(r'#([0-9A-Fa-f]{6})', md)
    if color_match:
        info["color"] = f"#{color_match.group(1)}"
    elif "青绿" in md or "teal" in md.lower():
        info["color"] = "#5EEDC7"

    # Position
    if "左中" in md:
        info["position_y"] = -0.06
    elif "正中" in md:
        info["position_y"] = 0

    return info



_MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS


def _listdir_media(materials_dir: str) -> list[str]:
    """List only playable video/image files. Skips hidden files (dot-prefixed),
    metadata (.json), and anything outside the media extension allowlist."""
    try:
        out = []
        for f in os.listdir(materials_dir):
            if f.startswith("."):
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext in _MEDIA_EXTS:
                out.append(f)
        return out
    except FileNotFoundError:
        return []


def _find_match_material_by_name(name: str, material_paths: list[str], materials_dir: str) -> Optional[str]:
    """Find a material file by name."""
    if not name:
        return None
    for f in _listdir_media(materials_dir):
        if name in f or f in name:
            return os.path.join(materials_dir, f)
    return None


def _find_match_material(
    shot_number: int, matches: list[dict],
    material_paths: list[str], materials_dir: str,
) -> Optional[str]:
    m = next((x for x in matches if x.get("shot_number") == shot_number), None)
    if not m:
        return None

    # A match may carry an explicit pre-resolved path (overrides name lookup)
    explicit = m.get("_material_path")
    if explicit and os.path.exists(explicit):
        return explicit

    mat_name = m.get("material", "")
    if not mat_name:
        return None

    media_files = _listdir_media(materials_dir)

    # Direct match
    for f in media_files:
        if mat_name in f or f in mat_name:
            return os.path.join(materials_dir, f)

    # Match by numbers (e.g. "1529" in "1529_xxx.mp4")
    digits = re.findall(r'\d{4}', mat_name)
    if digits:
        for f in media_files:
            if digits[0] in f:
                return os.path.join(materials_dir, f)

    # Match image files for LOGO
    if any(k in mat_name.lower() for k in ['logo', 'pillow', '生成', 'wechat', 'img']):
        for f in media_files:
            if os.path.splitext(f)[1].lower() in ('.jpg', '.jpeg', '.png'):
                return os.path.join(materials_dir, f)

    # Filter material_paths[0] too, since it could be anything
    for p in material_paths:
        if os.path.splitext(p)[1].lower() in _MEDIA_EXTS:
            return p
    return None


def _parse_edit_config_full(report: str) -> dict:
    """Parse the full EDIT_CONFIG JSON (matches + text_overlays).

    Handles several output formats:
    1. <!-- EDIT_CONFIG_START -->...<!-- EDIT_CONFIG_END --> (standard)
    2. Only <!-- EDIT_CONFIG_END --> marker (start got dropped)
    3. ```json ... ``` code block with "matches" field (fallback)
    """
    json_text = None

    # Format 1: standard both markers with complete JSON object
    m = re.search(r'<!-- EDIT_CONFIG_START -->\s*(?:```(?:json)?\s*)?(\{[\s\S]*\})\s*(?:```\s*)?<!-- EDIT_CONFIG_END -->', report)
    if m:
        json_text = m.group(1)

    # Format 2: ```json code block with "matches" field (Claude may skip markers)
    if not json_text:
        for m in re.finditer(r'```(?:json)?\s*([\s\S]*?)\s*```', report):
            block = m.group(1).strip()
            if '"matches"' in block or '"subtitles"' in block:
                json_text = block
                break

    # Format 3: fallback — greedy match before END marker
    if not json_text:
        m = re.search(r'(\{[\s\S]*\})\s*(?:```\s*)?<!-- EDIT_CONFIG_END -->', report)
        if m:
            json_text = m.group(1)

    if not json_text:
        return {}

    def try_parse(text):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            try:
                from json_repair import repair_json
                return json.loads(repair_json(text))
            except Exception:
                return None

    def has_valid_matches(d):
        if not isinstance(d, dict):
            return False
        ms = d.get("matches")
        if not isinstance(ms, list) or not ms:
            return False
        # Items must be dicts with shot_number
        return all(isinstance(m, dict) and "shot_number" in m for m in ms)

    # Try direct/wrapped parsing first (for well-formed output)
    data = try_parse(json_text)
    if not has_valid_matches(data):
        wrapped = "{\n" + json_text.strip().rstrip(",") + "\n}"
        tmp = try_parse(wrapped)
        if has_valid_matches(tmp):
            data = tmp

    # If still no valid matches, extract each top-level array field individually
    if not has_valid_matches(data):
        data = {}
        for field in ("matches", "subtitles", "overlays", "sound_effects", "text_overlays"):
            m = re.search(rf'"{field}"\s*:\s*(\[[\s\S]*?\])\s*(?:,\s*"|\}}|$)', json_text, re.MULTILINE)
            if m:
                arr = try_parse(m.group(1))
                if isinstance(arr, list):
                    data[field] = arr
        for field in ("keep_original_audio",):
            m = re.search(rf'"{field}"\s*:\s*(true|false)', json_text)
            if m:
                data[field] = m.group(1) == "true"

    if isinstance(data, list):
        data = {"matches": data}
    if not isinstance(data, dict):
        return {}
    return data



def _parse_report_matches(report: str) -> list[dict]:
    matches = []
    in_table = False
    header_cols = []
    mat_col = -1
    trim_col = -1

    for line in report.split("\n"):
        if any(k in line for k in ["匹配表", "素材匹配", "最终匹配", "镜头匹配", "匹配结果"]):
            in_table = True
            header_cols = []
            continue

        if not in_table:
            continue

        if not line.startswith("|"):
            if line.startswith("---") or (line.strip() and line.startswith("#")):
                in_table = False
            continue

        parts = [p.strip() for p in line.split("|")]
        parts = [p for p in parts if p]

        if "---" in line:
            continue

        # Detect header row to find column indexes
        if not header_cols and any(k in "".join(parts) for k in ["镜头", "匹配素材", "截取"]):
            header_cols = parts
            for i, col in enumerate(header_cols):
                if "匹配素材" in col:
                    mat_col = i
                elif "截取" in col or "起点" in col:
                    trim_col = i
            # If no exact "匹配素材", find first column with backtick content in data rows
            continue

        if len(parts) < 3:
            continue

        try:
            num_match = re.search(r"\d+", parts[0])
            if not num_match:
                continue
            shot_num = int(num_match.group())

            # Find material name — use detected column or search for backtick-wrapped name
            material = ""
            if mat_col >= 0 and mat_col < len(parts):
                material = parts[mat_col].strip("`* ")
            else:
                # Search all columns for a backtick-wrapped filename
                for p in parts:
                    if "`" in p:
                        m = re.search(r"`([^`]+)`", p)
                        if m:
                            material = m.group(1).split("(")[0].strip()
                            break
                if not material:
                    # Fallback: parts[2] or parts[3]
                    for idx in [3, 2]:
                        if idx < len(parts) and re.search(r"\d{4}", parts[idx]):
                            material = parts[idx].strip("`* ")
                            break

            # Clean up: remove parenthetical duration, backticks, asterisks
            material = re.sub(r"\s*\([\d.]+s\)", "", material)
            material = material.strip("`* \t")

            # Find trim start
            trim_start = 0
            if trim_col >= 0 and trim_col < len(parts):
                ts = re.search(r"([\d.]+)s", parts[trim_col])
                if ts:
                    trim_start = float(ts.group(1))
            else:
                # Search columns after material for time
                for p in parts[3:]:
                    ts = re.search(r"^([\d.]+)s$", p.strip())
                    if ts:
                        trim_start = float(ts.group(1))
                        break

            reason = parts[-1].strip() if len(parts) > 5 else ""

            matches.append({
                "shot_number": shot_num,
                "material": material,
                "trim_start": trim_start,
                "reason": reason,
            })
        except (ValueError, AttributeError):
            continue

    return matches
