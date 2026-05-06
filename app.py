import asyncio
import json
import os
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from models.schemas import AnalysisResult, AutoEditJob, VideoInfo
from services.auto_editor import auto_edit
from services.claude_client import get_status
from services.claude_auth import start_oauth_flow, exchange_code
from services.video_analyzer import (
    analyze_video, parse_video_info_table, parse_shots_table,
    parse_shot_details,
)

app = FastAPI(title="AI 视频分析 & 自动剪辑")

BASE_DIR = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
FRAMES_DIR = BASE_DIR / "frames"

# In-memory job storage
analysis_jobs: dict[str, AnalysisResult] = {}
edit_jobs: dict[str, AutoEditJob] = {}
# SSE event queues per job
job_events: dict[str, asyncio.Queue] = {}


def _apply_auto_edit_result_to_job(job: AutoEditJob, result: dict):
    job.status = result.get("status", job.status)
    job.stage = result.get("stage", job.stage)
    job.output_path = result.get("output_path")
    job.error = result.get("error")
    job.warning = result.get("warning")
    job.draft_dir = result.get("draft_dir")
    job.draft_name = result.get("draft_name")
    job.delivery_mode = result.get("delivery_mode", job.delivery_mode)
    job.open_mode = result.get("open_mode", job.open_mode)
    job.last_mcp_endpoint = result.get("last_mcp_endpoint")
    job.last_material = result.get("last_material")
    job.diagnostics = result.get("diagnostics", job.diagnostics)
    job.match_results = result.get("matches", job.match_results)


def load_existing_analyses():
    """Load previously completed analyses from outputs/ on startup."""
    for md_path in OUTPUTS_DIR.glob("*_analysis.md"):
        job_id = md_path.stem.replace("_analysis", "")
        if job_id in analysis_jobs:
            continue
        try:
            md = md_path.read_text(encoding="utf-8")
            video_info = parse_video_info_table(md)
            if not video_info.filename:
                video_info.filename = job_id
            shots = parse_shots_table(md)
            shots = parse_shot_details(md, shots)
            video_info.shot_count = len(shots)
            overview_match = re.search(r"## 视频概述\s*\n([\s\S]*?)(?=\n---|\n##)", md)
            overview = overview_match.group(1).strip() if overview_match else ""
            analysis_jobs[job_id] = AnalysisResult(
                job_id=job_id,
                video_info=video_info,
                shots=shots,
                overview=overview,
                status="completed",
                progress=1.0,
            )
        except Exception as e:
            print(f"Warning: failed to load {md_path.name}: {e}")


_NAMES_FILE = OUTPUTS_DIR / "display_names.json"


def _save_display_names():
    names = {k: v.display_name for k, v in analysis_jobs.items() if v.display_name}
    with open(_NAMES_FILE, "w", encoding="utf-8") as f:
        json.dump(names, f, ensure_ascii=False)


def _load_display_names():
    if _NAMES_FILE.exists():
        try:
            with open(_NAMES_FILE) as f:
                names = json.load(f)
            for job_id, name in names.items():
                if job_id in analysis_jobs:
                    analysis_jobs[job_id].display_name = name
        except Exception:
            pass


load_existing_analyses()
_load_display_names()


# --- Analysis endpoints ---

@app.post("/api/analyze")
async def start_analysis(
    video: UploadFile = File(...),
    threshold: float = Form(0.3),
):
    job_id = str(uuid.uuid4())[:8]
    video_path = str(UPLOADS_DIR / f"{job_id}_{video.filename}")

    with open(video_path, "wb") as f:
        while chunk := await video.read(1024 * 1024):
            f.write(chunk)

    analysis_jobs[job_id] = AnalysisResult(
        job_id=job_id,
        video_info={"filename": video.filename},
        status="processing",
    )
    job_events[job_id] = asyncio.Queue()

    asyncio.create_task(_run_analysis(job_id, video_path, threshold))

    return {"job_id": job_id, "status": "processing"}


async def _run_analysis(job_id: str, video_path: str, threshold: float):
    queue = job_events[job_id]

    async def progress_cb(progress: float, message: str):
        analysis_jobs[job_id].progress = progress
        await queue.put({"progress": progress, "message": message})

    try:
        result = await analyze_video(video_path, job_id, progress_cb)
        analysis_jobs[job_id] = result
        await queue.put({"progress": 1.0, "message": "完成", "done": True})
    except Exception as e:
        analysis_jobs[job_id].status = "error"
        analysis_jobs[job_id].error = str(e)
        await queue.put({"progress": 0, "message": f"错误: {e}", "error": True})


@app.get("/api/analyze/{job_id}/status")
async def analysis_status_stream(job_id: str):
    if job_id not in job_events:
        return JSONResponse({"error": "Job not found"}, 404)

    async def event_generator():
        queue = job_events[job_id]
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30)
                yield {"event": "progress", "data": json.dumps(event, ensure_ascii=False)}
                if event.get("done") or event.get("error"):
                    break
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": "{}"}
        job_events.pop(job_id, None)

    return EventSourceResponse(event_generator())


@app.get("/api/analyze/{job_id}")
async def get_analysis(job_id: str):
    if job_id not in analysis_jobs:
        return JSONResponse({"error": "Job not found"}, 404)
    return analysis_jobs[job_id].model_dump()


@app.get("/api/analyses")
async def list_analyses():
    return [
        {"job_id": k,
         "filename": v.video_info.filename,
         "display_name": v.display_name or v.video_info.filename,
         "status": v.status,
         "shot_count": v.video_info.shot_count}
        for k, v in analysis_jobs.items()
    ]


@app.post("/api/analyze/{job_id}/rename")
async def rename_analysis(job_id: str, request: Request):
    """Rename an analysis template."""
    if job_id not in analysis_jobs:
        return JSONResponse({"error": "Job not found"}, 404)
    data = await request.json()
    new_name = data.get("name", "").strip()
    if not new_name:
        return JSONResponse({"error": "Name is empty"}, 400)
    analysis_jobs[job_id].display_name = new_name
    _save_display_names()
    return {"success": True, "display_name": new_name}


@app.post("/api/analyze/{job_id}/merge-shots")
async def merge_shots(job_id: str, request: Request):
    """Merge a shot with the next one."""
    if job_id not in analysis_jobs:
        return JSONResponse({"error": "Job not found"}, 404)

    data = await request.json()
    shot_number = data.get("shot_number")

    template = analysis_jobs[job_id]
    shots = sorted(template.shots, key=lambda s: s.number)

    # Find the two shots to merge
    shot_a = next((s for s in shots if s.number == shot_number), None)
    shot_b = next((s for s in shots if s.number == shot_number + 1), None)

    if not shot_a or not shot_b:
        return JSONResponse({"error": "找不到要合并的镜头"}, 400)

    # Find original video for re-analysis
    video_path = None
    for f in UPLOADS_DIR.iterdir():
        if f.name.startswith(job_id) and f.suffix in (".mp4", ".mov", ".avi", ".mkv"):
            video_path = str(f)
            break
    if not video_path:
        return JSONResponse({"error": "原视频未找到"}, 404)

    # Run re-analysis on merged range, telling skill it's ONE shot
    asyncio.create_task(_run_merge_reanalysis(
        job_id, video_path, shot_a, shot_b, shot_number, shots
    ))
    return {"success": True, "message": f"正在重新分析 {shot_a.start_time:.1f}s-{shot_b.end_time:.1f}s 为一个镜头"}


async def _run_merge_reanalysis(job_id, video_path, shot_a, shot_b, shot_number, all_shots):
    """Re-analyze merged time range as a single shot."""
    from services.claude_client import claude_with_skill
    from services.video_analyzer import parse_shots_table, parse_shot_details, SKILL_PATH

    start_s = shot_a.start_time
    end_s = shot_b.end_time
    template = analysis_jobs[job_id]

    prompt = (
        f"{video_path}\n\n"
        f"只分析 {start_s:.2f}s 到 {end_s:.2f}s 这个时间范围。"
        f"这个范围是**一个完整镜头**（不是多个镜头），不要拆分。"
        f"请对这一个镜头进行完整的视觉分析（构图、运镜、光影、焦段、变速、文字、音频、人物等），"
        f"输出一个镜头的分析结果。"
    )

    try:
        result_md = await claude_with_skill(
            skill_path=str(SKILL_PATH), prompt=prompt,
            allowed_tools="Bash,Read,Glob,Grep", model="sonnet",
        )
    except Exception as e:
        print(f"Merge reanalysis failed: {e}")
        return

    # Parse the single new shot
    new_shots = parse_shots_table(result_md)
    new_shots = parse_shot_details(result_md, new_shots)

    if new_shots:
        merged = new_shots[0]
        # Ensure correct time range
        merged.start_time = start_s
        merged.end_time = end_s
        merged.duration = end_s - start_s
    else:
        # Fallback: use shot_a's properties with extended time
        from models.schemas import Shot
        merged = Shot(
            number=1, start_time=start_s, end_time=end_s,
            duration=end_s - start_s,
            composition=shot_a.composition, camera_movement=shot_a.camera_movement,
            lighting=shot_a.lighting, focal_length=shot_a.focal_length,
            content=shot_a.content, visual_description=shot_a.visual_description,
            emotion=shot_a.emotion, transition_from_prev=shot_a.transition_from_prev,
        )

    # Replace in shots list
    result_shots = []
    for s in all_shots:
        if s.number == shot_number:
            result_shots.append(merged)
        elif s.number == shot_number + 1:
            continue
        else:
            result_shots.append(s)

    for i, s in enumerate(result_shots):
        s.number = i + 1

    template.shots = result_shots
    template.video_info.shot_count = len(result_shots)
    _persist_shots_to_markdown(job_id, result_shots)
    print(f"Merge done: #{shot_number}+#{shot_number+1} -> 1 shot, total {len(result_shots)}")


def _persist_shots_to_markdown(job_id: str, shots):
    """Rewrite the analysis markdown with updated shots."""
    md_path = OUTPUTS_DIR / f"{job_id}_analysis.md"
    try:
        # Keep original content if exists
        original = ""
        if md_path.exists():
            original = md_path.read_text(encoding="utf-8")

        lines = ["# 分镜分析（已更新）\n"]
        lines.append("## 分镜分析总表\n")
        lines.append("| # | 时间范围 | 时长 | 构图/景别 | 角度/运镜 | 内容 |")
        lines.append("|---|----------|------|-----------|-----------|------|")
        for s in shots:
            lines.append(
                f"| {s.number} | {s.start_time:.2f}-{s.end_time:.2f} | {s.duration:.1f}s "
                f"| {s.composition} | {s.camera_movement} | {s.content} |"
            )

        # Append original analysis below
        if original and not original.startswith("# 分镜分析（已更新）"):
            lines.append("\n\n---\n\n## 原始分析\n\n")
            lines.append(original)

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        print(f"Warning: markdown persist failed: {e}")


@app.post("/api/analyze/{job_id}/reanalyze-range")
async def reanalyze_range(job_id: str, request: Request):
    """Re-analyze a time range within an existing analysis.
    Replaces shots in that range with new analysis results.
    """
    if job_id not in analysis_jobs:
        return JSONResponse({"error": "Job not found"}, 404)

    data = await request.json()
    start_s = data.get("start", 0)
    end_s = data.get("end", 0)

    if end_s <= start_s:
        return JSONResponse({"error": "Invalid time range"}, 400)

    template = analysis_jobs[job_id]

    # Find original video
    video_path = None
    for f in UPLOADS_DIR.iterdir():
        if f.name.startswith(job_id) and f.suffix in (".mp4", ".mov", ".avi", ".mkv"):
            video_path = str(f)
            break
    if not video_path:
        return JSONResponse({"error": "Original video not found"}, 404)

    # Run partial analysis in background
    asyncio.create_task(_run_partial_reanalysis(job_id, video_path, start_s, end_s))
    return {"success": True, "message": f"正在重新分析 {start_s:.1f}s-{end_s:.1f}s"}


async def _run_partial_reanalysis(job_id: str, video_path: str, start_s: float, end_s: float):
    """Re-analyze a specific time range and merge results."""
    from services.claude_client import claude_with_skill
    from services.video_analyzer import (
        parse_shots_table, parse_shot_details, SKILL_PATH,
    )

    template = analysis_jobs[job_id]

    # Ask skill to analyze only the specific time range
    prompt = (
        f"{video_path}\n\n"
        f"只分析 {start_s:.2f}s 到 {end_s:.2f}s 这个时间范围内的镜头。"
        f"其他时间范围不需要分析。按照完整的分析流程（scene detection、4fps抽帧、视觉验证）"
        f"检测这个范围内是否有多个镜头，并输出分镜分析结果。"
    )

    try:
        result_md = await claude_with_skill(
            skill_path=str(SKILL_PATH),
            prompt=prompt,
            allowed_tools="Bash,Read,Glob,Grep",
            model="sonnet",
        )
    except Exception as e:
        print(f"Partial reanalysis failed: {e}")
        return

    # Parse new shots from result
    new_shots = parse_shots_table(result_md)
    new_shots = parse_shot_details(result_md, new_shots)

    if not new_shots:
        print("Partial reanalysis returned no shots")
        return

    # Remove old shots in the time range
    old_shots = [s for s in template.shots
                 if not (s.start_time >= start_s - 0.1 and s.end_time <= end_s + 0.1)]

    # Merge: old shots before range + new shots + old shots after range
    before = [s for s in old_shots if s.end_time <= start_s + 0.1]
    after = [s for s in old_shots if s.start_time >= end_s - 0.1]

    merged = before + new_shots + after

    # Renumber
    for i, shot in enumerate(merged):
        shot.number = i + 1

    template.shots = merged
    template.video_info.shot_count = len(merged)

    # Regenerate markdown from merged shots
    md_path = OUTPUTS_DIR / f"{job_id}_analysis.md"
    try:
        lines = [f"# 分镜分析（已更新）\n"]
        lines.append(f"\n## 分镜分析总表\n")
        lines.append(f"| # | 时间范围 | 时长 | 构图/景别 | 角度/运镜 | 内容 |")
        lines.append(f"|---|----------|------|-----------|-----------|------|")
        for s in merged:
            lines.append(
                f"| {s.number} | {s.start_time:.2f}-{s.end_time:.2f} | {s.duration:.1f}s "
                f"| {s.composition} | {s.camera_movement} | {s.content} |"
            )
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        print(f"Warning: markdown update failed: {e}")

    print(f"Partial reanalysis done: {len(new_shots)} new shots in {start_s}-{end_s}s, total now {len(merged)}")


@app.get("/api/analyze/{job_id}/markdown")
async def get_analysis_markdown(job_id: str):
    """Return the raw markdown analysis output."""
    md_path = OUTPUTS_DIR / f"{job_id}_analysis.md"
    if not md_path.exists():
        return JSONResponse({"error": "Markdown not found"}, 404)
    return FileResponse(str(md_path), media_type="text/markdown")


# --- Auto-edit endpoints ---

@app.post("/api/auto-edit")
async def start_auto_edit(
    template_job_id: str = Form(""),
    materials: list[UploadFile] = File(default=[]),
    user_prompt: str = Form(""),
):
    if not template_job_id or template_job_id not in analysis_jobs:
        return JSONResponse({"error": "Template analysis not found"}, 404)
    template = analysis_jobs[template_job_id]
    if template.status != "completed":
        return JSONResponse({"error": "Template analysis not completed"}, 400)

    job_id = str(uuid.uuid4())[:8]
    material_paths = []

    mat_dir = UPLOADS_DIR / f"{job_id}_materials"
    mat_dir.mkdir(parents=True, exist_ok=True)

    for mat in materials:
        # Flatten folder structure - use only the base filename
        safe_name = os.path.basename(mat.filename)
        mat_path = str(mat_dir / safe_name)
        # Handle duplicate filenames
        if os.path.exists(mat_path):
            name, ext = os.path.splitext(safe_name)
            i = 1
            while os.path.exists(mat_path):
                mat_path = str(mat_dir / f"{name}_{i}{ext}")
                i += 1
        with open(mat_path, "wb") as f:
            while chunk := await mat.read(1024 * 1024):
                f.write(chunk)
        material_paths.append(mat_path)

    edit_jobs[job_id] = AutoEditJob(
        job_id=job_id,
        template_job_id=template_job_id,
        status="processing",
        stage="uploading_materials",
    )
    job_events[job_id] = asyncio.Queue()

    asyncio.create_task(_run_auto_edit(job_id, template, material_paths, user_prompt))

    return {"job_id": job_id, "status": "processing"}


def _cleanup_uploads(job_id: str, material_paths: list[str]):
    """Remove uploaded materials after draft is saved to free disk space."""
    import shutil
    mat_dir = UPLOADS_DIR / f"{job_id}_materials"
    if mat_dir.is_dir():
        try:
            shutil.rmtree(mat_dir)
            print(f"Cleaned up materials: {mat_dir}")
        except Exception as e:
            print(f"Warning: failed to clean up {mat_dir}: {e}")
    # Also remove single uploaded video files for this job
    for p in material_paths:
        try:
            if os.path.isfile(p) and UPLOADS_DIR in Path(p).parents:
                os.remove(p)
        except Exception:
            pass


async def _run_auto_edit(job_id: str, template: AnalysisResult, material_paths: list[str], user_prompt: str = ""):
    queue = job_events[job_id]

    async def progress_cb(progress: float, message: str, stage: str | None = None, **extra):
        edit_jobs[job_id].progress = progress
        if stage:
            edit_jobs[job_id].stage = stage
        if "last_mcp_endpoint" in extra:
            edit_jobs[job_id].last_mcp_endpoint = extra["last_mcp_endpoint"]
        if "last_material" in extra:
            edit_jobs[job_id].last_material = extra["last_material"]
        if "draft_dir" in extra:
            edit_jobs[job_id].draft_dir = extra["draft_dir"]
        if "diagnostics" in extra and isinstance(extra["diagnostics"], dict):
            edit_jobs[job_id].diagnostics = extra["diagnostics"]
        payload = {"progress": progress, "message": message, "stage": edit_jobs[job_id].stage}
        payload.update({k: v for k, v in extra.items() if v is not None})
        await queue.put(payload)

    try:
        result = await auto_edit(template, material_paths, job_id, progress_cb, user_prompt)
        _apply_auto_edit_result_to_job(edit_jobs[job_id], result)
        if result.get("status") in {"completed", "completed_with_warning"}:
            # Clean up uploaded materials — they've been copied into the draft
            _cleanup_uploads(job_id, material_paths)
            await queue.put({"progress": 1.0, "message": "完成", "done": True, "result": result})
        else:
            await queue.put({
                "progress": edit_jobs[job_id].progress,
                "message": result.get("error", "Unknown error"),
                "stage": edit_jobs[job_id].stage,
                "error": True,
                "result": result,
            })
    except Exception as e:
        import traceback
        traceback.print_exc()
        err_msg = str(e) or repr(e)
        edit_jobs[job_id].status = "error"
        edit_jobs[job_id].stage = "unexpected_error"
        edit_jobs[job_id].error = err_msg
        await queue.put({"progress": 0, "message": f"错误: {err_msg}", "stage": "unexpected_error", "error": True})


@app.get("/api/auto-edit/{job_id}/status")
async def edit_status_stream(job_id: str):
    if job_id not in job_events:
        return JSONResponse({"error": "Job not found"}, 404)

    async def event_generator():
        queue = job_events[job_id]
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30)
                yield {"event": "progress", "data": json.dumps(event, ensure_ascii=False)}
                if event.get("done") or event.get("error"):
                    break
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": "{}"}
        job_events.pop(job_id, None)

    return EventSourceResponse(event_generator())


@app.get("/api/auto-edits")
async def list_edit_jobs():
    """List all edit jobs with their status."""
    return [
        {
            "job_id": k,
            "template_job_id": v.template_job_id,
            "status": v.status,
            "stage": v.stage,
            "progress": v.progress,
            "error": v.error,
            "warning": v.warning,
            "draft_dir": v.draft_dir,
            "delivery_mode": v.delivery_mode,
            "has_output": v.output_path is not None and os.path.exists(v.output_path) if v.output_path else False,
            "has_draft": (
                os.path.exists(v.diagnostics.get("draft_dir"))
                if isinstance(v.diagnostics, dict) and v.diagnostics.get("draft_dir")
                else (v.draft_dir is not None and os.path.exists(v.draft_dir) if v.draft_dir else False)
            ),
        }
        for k, v in edit_jobs.items()
    ]


@app.get("/api/auto-edit/{job_id}")
async def get_edit_job(job_id: str):
    if job_id not in edit_jobs:
        return JSONResponse({"error": "Job not found"}, 404)
    return edit_jobs[job_id].model_dump()


@app.get("/api/auto-edit/{job_id}/download")
async def download_output(job_id: str):
    if job_id not in edit_jobs:
        return JSONResponse({"error": "Job not found"}, 404)
    return JSONResponse({"error": "Direct MP4 export is not implemented. This workflow generates a CapCut/JianYing draft instead."}, 501)


@app.get("/api/auto-edit/{job_id}/capcut")
async def download_capcut(job_id: str):
    """Download CapCut project as zip.

    Resolution order (so restart-induced edit_jobs loss doesn't break downloads):
      1. diagnostics.saved_draft_file's parent  — final post-save location
      2. /app/drafts/{draft_name}               — naming convention
      3. diagnostics.draft_dir                  — MCP's creation path (may be gone)
      4. job.draft_dir                          — HOST path (invisible in container)
      5. /app/drafts/edit_{job_id}              — filesystem fallback when job not in memory
    """
    candidates: list[Path] = []

    job = edit_jobs.get(job_id)
    if job:
        diag = job.diagnostics if isinstance(job.diagnostics, dict) else {}
        saved_file = diag.get("saved_draft_file")
        if saved_file:
            candidates.append(Path(saved_file).parent)
        draft_output_dir = diag.get("draft_output_dir") or "/app/drafts"
        if job.draft_name:
            candidates.append(Path(draft_output_dir) / job.draft_name)
        diag_draft_dir = diag.get("draft_dir")
        if diag_draft_dir:
            candidates.append(Path(diag_draft_dir))
        if job.draft_dir:
            candidates.append(Path(job.draft_dir))

    # Filesystem fallback (works even after server restart)
    candidates.append(Path("/app/drafts") / f"edit_{job_id}")

    capcut_dir = next((p for p in candidates if p.exists()), None)
    zip_path = OUTPUTS_DIR / f"{job_id}_capcut.zip"
    if not zip_path.exists() and not capcut_dir:
        return JSONResponse(
            {"error": "CapCut project not found",
             "tried": [str(p) for p in candidates]},
            404,
        )
    if not zip_path.exists() and capcut_dir:
        from services.template_exporter import zip_directory
        zip_directory(str(capcut_dir), str(zip_path))
    return FileResponse(str(zip_path), filename=f"capcut_{job_id}.zip",
                       media_type="application/zip")


@app.get("/api/auto-edit/{job_id}/fcpxml")
async def download_fcpxml(job_id: str):
    """Download FCPXML project file."""
    fcpxml_path = OUTPUTS_DIR / f"{job_id}.fcpxml"
    if not fcpxml_path.exists():
        return JSONResponse({"error": "FCPXML not found"}, 404)
    return FileResponse(str(fcpxml_path), filename=f"auto_edit_{job_id}.fcpxml",
                       media_type="application/xml")


@app.get("/api/auto-edit/{job_id}/materials")
async def download_materials_pack(job_id: str):
    """Download materials pack as zip."""
    zip_path = OUTPUTS_DIR / f"{job_id}_materials.zip"
    mat_dir = OUTPUTS_DIR / f"{job_id}_materials_pack"
    if not zip_path.exists() and not mat_dir.exists():
        return JSONResponse({"error": "Materials pack not found"}, 404)
    if not zip_path.exists() and mat_dir.exists():
        from services.template_exporter import zip_directory
        zip_directory(str(mat_dir), str(zip_path))
    return FileResponse(str(zip_path), filename=f"materials_{job_id}.zip",
                       media_type="application/zip")


@app.post("/api/cleanup-uploads")
async def cleanup_all_uploads():
    """Remove all uploaded materials to free disk space."""
    import shutil
    removed = 0
    freed = 0
    for item in UPLOADS_DIR.iterdir():
        try:
            if item.is_dir():
                size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                shutil.rmtree(item)
            elif item.is_file():
                size = item.stat().st_size
                item.unlink()
            else:
                continue
            freed += size
            removed += 1
        except Exception as e:
            print(f"Warning: failed to remove {item}: {e}")
    return {"removed": removed, "freed_mb": round(freed / 1048576, 1)}


# --- Claude Login ---

@app.get("/api/config/status")
async def config_status():
    return JSONResponse(await get_status())


@app.post("/api/config/login")
async def claude_login():
    """Generate OAuth URL."""
    return JSONResponse(start_oauth_flow())


@app.post("/api/config/login-code")
async def claude_login_code(request: Request):
    """Exchange OAuth code for API key."""
    body = await request.json()
    code = body.get("code", "").strip()
    if not code:
        return JSONResponse({"error": "请输入授权码"}, 400)
    return JSONResponse(await exchange_code(code))


# --- Static files ---

@app.get("/api/frames/{job_id}/{filename}")
async def get_frame(job_id: str, filename: str):
    path = FRAMES_DIR / job_id / filename
    if not path.exists():
        return JSONResponse({"error": "Frame not found"}, 404)
    return FileResponse(str(path))


# Serve static files (must be last)
app.mount("/", StaticFiles(directory=str(BASE_DIR / "static"), html=True))


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
