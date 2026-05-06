"""
CapCut/剪映 MCP Server — 基于 pyJianYingDraft 的本地版本
通过 HTTP API 提供剪映工程文件生成能力。

启动: python3 services/capcut_mcp.py
端口: 9001
"""
import json
import os
import uuid
from pathlib import Path

from flask import Flask, request, jsonify
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

from pyJianYingDraft import (
    Script_file, Track_type,
    Video_material, Audio_material,
    Video_segment, Audio_segment, Text_segment,
    Text_style, Clip_settings, trange, tim,
    Keyframe_property,
    IntroType, OutroType, TextIntro, TextOutro, TextLoopAnim,
    TransitionType,
    MaskType, FilterType,
    VideoSceneEffectType, VideoCharacterEffectType,
)
try:
    from pyJianYingDraft import AudioSceneEffectType
except ImportError:
    AudioSceneEffectType = None  # Older pyJianYingDraft versions may lack this enum

app = Flask(__name__)

# In-memory draft storage
drafts: dict[str, Script_file] = {}
draft_paths: dict[str, str] = {}
# Per-draft segment references for post-creation modifications (keyframes, masks, effects)
draft_segments: dict[str, list] = {}  # draft_id -> [segment, segment, ...]
draft_text_segments: dict[str, list] = {}  # draft_id -> [text_segment, ...]
draft_names: dict[str, str] = {}  # draft_id -> display name


def _error_response(message: str, *, code: str, stage: str, details: dict | None = None, status: int = 200):
    payload = {
        "success": False,
        "error": message,
        "code": code,
        "stage": stage,
    }
    if details:
        payload["details"] = details
    return jsonify(payload), status

def _apply_emphasis_markup(draft_data: dict):
    """Parse {重点词} markup in text materials → split into multi-range styles.

    Text like "普通老师教不了{C加加}" becomes:
    - "普通老师教不了C加加" (curly braces removed)
    - styles[0]: range=[0,7], normal size/color
    - styles[1]: range=[7,10], bigger size + yellow color
    """
    import re
    emphasis_color = [0.9, 0.7, 0.1]  # 棕黄色
    emphasis_size_ratio = 1.5  # 1.5x normal size

    for mat in draft_data.get("materials", {}).get("texts", []):
        content_str = mat.get("content", "")
        if not content_str or "{" not in content_str:
            continue
        try:
            content = json.loads(content_str)
            raw_text = content.get("text", "")
            if "{" not in raw_text:
                continue

            orig_style = content.get("styles", [{}])[0]
            base_size = orig_style.get("size", 8.0)
            base_fill = orig_style.get("fill", {
                "alpha": 1.0,
                "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]}}
            })
            base_bold = orig_style.get("bold", False)
            effect_style = orig_style.get("effectStyle")

            # Parse {emphasis} segments
            clean_text = ""
            new_styles = []
            pos = 0
            normal_start = 0

            for m in re.finditer(r'\{([^}]+)\}', raw_text):
                # Normal text before this match
                before = raw_text[pos:m.start()]
                if before:
                    clean_text += before

                em_text = m.group(1)
                em_start = len(clean_text)
                clean_text += em_text
                em_end = len(clean_text)

                pos = m.end()

            # Remaining normal text
            remaining = raw_text[pos:]
            if remaining:
                clean_text += remaining

            if clean_text == raw_text:
                continue  # No markup found

            # Build style ranges
            pos = 0
            new_styles = []
            for m in re.finditer(r'\{([^}]+)\}', raw_text):
                before = raw_text[pos:m.start()]
                before_clean_start = sum(len(x) for x in re.sub(r'\{([^}]+)\}', r'\1', raw_text[:pos]))

                pos = m.end()

            # Simpler approach: rebuild styles by walking through clean_text
            pos_raw = 0
            pos_clean = 0
            segments = []  # (start, end, is_emphasis)

            for m in re.finditer(r'\{([^}]+)\}', raw_text):
                # Normal text before
                before = raw_text[pos_raw:m.start()]
                if before:
                    segments.append((pos_clean, pos_clean + len(before), False))
                    pos_clean += len(before)
                # Emphasis text
                em = m.group(1)
                segments.append((pos_clean, pos_clean + len(em), True))
                pos_clean += len(em)
                pos_raw = m.end()

            # Remaining
            remaining = raw_text[pos_raw:]
            if remaining:
                segments.append((pos_clean, pos_clean + len(remaining), False))

            # Merge consecutive normal segments
            merged = []
            for seg in segments:
                if merged and merged[-1][2] == seg[2] == False:
                    merged[-1] = (merged[-1][0], seg[1], False)
                else:
                    merged.append(list(seg))

            # Build styles array
            new_styles = []
            for start, end, is_em in merged:
                style_entry = {
                    "fill": json.loads(json.dumps(base_fill)),  # deep copy
                    "range": [start, end],
                    "size": round(base_size * emphasis_size_ratio, 1) if is_em else base_size,
                    "bold": True if is_em else base_bold,
                    "italic": False,
                    "underline": False,
                    "strokes": [],
                }
                if is_em:
                    # Emphasis: yellow color, NO effectStyle (flower text overrides color)
                    style_entry["fill"] = {
                        "alpha": 1.0,
                        "content": {
                            "render_type": "solid",
                            "solid": {"alpha": 1.0, "color": emphasis_color}
                        }
                    }
                else:
                    # Normal text keeps flower text effect
                    if effect_style:
                        style_entry["effectStyle"] = effect_style
                new_styles.append(style_entry)

            content["text"] = clean_text
            content["styles"] = new_styles
            mat["content"] = json.dumps(content, ensure_ascii=False)
            print(f"Applied emphasis markup: '{raw_text}' -> '{clean_text}' ({len(new_styles)} styles)")

        except (json.JSONDecodeError, KeyError, IndexError) as e:
            print(f"Warning: emphasis markup parse failed: {e}")


def _fix_flower_text_paths(draft_data: dict):
    """Fix flower text effectStyle paths from pyJianYingDraft placeholder 'C:' to real local cache.

    JianYing stores flower text resources at:
      <JianYing>/User Data/Cache/artistEffect/<effect_id>/<hash>/
    pyJianYingDraft hardcodes path='C:' which doesn't work on macOS.
    """
    # Container path (for listing files) vs host path (for final JSON)
    container_cache = os.environ.get("ARTIST_EFFECT_CACHE", "")
    host_cache = os.environ.get("ARTIST_EFFECT_HOST_DIR", "")

    if not container_cache or not os.path.isdir(container_cache):
        # Not Docker — use local path directly
        local_cache = str(Path.home() / "Movies" / "JianyingPro" / "User Data" / "Cache" / "artistEffect")
        if os.path.isdir(local_cache):
            container_cache = local_cache
            host_cache = local_cache
        else:
            return  # No cache available

    if not host_cache:
        host_cache = container_cache  # Same path if not Docker

    def _resolve_effect_path(effect_id: str) -> str | None:
        """Find the resource subfolder for an effect_id, return HOST path."""
        effect_dir = os.path.join(container_cache, effect_id)
        if not os.path.isdir(effect_dir):
            return None
        subdirs = [d for d in os.listdir(effect_dir)
                   if os.path.isdir(os.path.join(effect_dir, d))]
        if not subdirs:
            return None
        # Return host path for JianYing to find
        return os.path.join(host_cache, effect_id, subdirs[0])

    # Scan text materials for effectStyle with placeholder path
    for mat in draft_data.get("materials", {}).get("texts", []):
        content_str = mat.get("content", "")
        if not content_str or "effectStyle" not in content_str:
            continue
        try:
            content = json.loads(content_str)
            changed = False
            for style in content.get("styles", []):
                es = style.get("effectStyle")
                if not es or not es.get("id"):
                    continue
                eid = es["id"]
                if es.get("path", "") in ("C:", "D:", ""):
                    resolved = _resolve_effect_path(eid)
                    if resolved:
                        es["path"] = resolved
                        changed = True
                        print(f"Fixed flower text path: {eid} -> {resolved}")
            if changed:
                mat["content"] = json.dumps(content, ensure_ascii=False)
        except (json.JSONDecodeError, KeyError):
            pass

    # Also fix the effect materials references (in both "effects" and "material_animations")
    for key in ("effects", "material_animations"):
        for mat in draft_data.get("materials", {}).get(key, []):
            if mat.get("type") == "text_effect":
                eid = mat.get("effect_id", "")
                if eid:
                    resolved = _resolve_effect_path(eid)
                    if resolved:
                        mat["path"] = resolved
                        print(f"Fixed effect material path: {eid} -> {resolved}")


def _rewrite_paths(data, container_prefix: str, host_prefix: str):
    """Recursively replace container paths with host paths in draft JSON."""
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, str) and v.startswith(container_prefix):
                data[k] = v.replace(container_prefix, host_prefix, 1)
            elif isinstance(v, (dict, list)):
                _rewrite_paths(v, container_prefix, host_prefix)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, str) and item.startswith(container_prefix):
                data[i] = item.replace(container_prefix, host_prefix, 1)
            elif isinstance(item, (dict, list)):
                _rewrite_paths(item, container_prefix, host_prefix)


# Draft output directory
# In Docker: /app/drafts (mapped to host JianYing directory via docker-compose volume)
# Local: auto-detect JianYing/CapCut directory
_ENV_DRAFT_DIR = os.environ.get("DRAFT_OUTPUT_DIR")
_HOST_DRAFT_DIR = os.environ.get("DRAFT_HOST_DIR", "")  # Host path for rewriting material paths
_JIANYING_DRAFT_DIR = Path.home() / "Movies" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"
_CAPCUT_DRAFT_DIR = Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
_FALLBACK_DIR = Path(__file__).parent.parent / "outputs" / "capcut_drafts"

if _ENV_DRAFT_DIR:
    BASE_OUTPUT = Path(_ENV_DRAFT_DIR)
elif _JIANYING_DRAFT_DIR.exists():
    BASE_OUTPUT = _JIANYING_DRAFT_DIR
elif _CAPCUT_DRAFT_DIR.exists():
    BASE_OUTPUT = _CAPCUT_DRAFT_DIR
else:
    BASE_OUTPUT = _FALLBACK_DIR

BASE_OUTPUT.mkdir(parents=True, exist_ok=True)
print(f"CapCut draft output: {BASE_OUTPUT}")


def _draft_health() -> dict:
    info = {
        "success": True,
        "draft_output_dir": str(BASE_OUTPUT),
        "draft_host_dir": _HOST_DRAFT_DIR or None,
        "writable": False,
        "exists": BASE_OUTPUT.exists(),
        "is_dir": BASE_OUTPUT.is_dir(),
    }
    try:
        BASE_OUTPUT.mkdir(parents=True, exist_ok=True)
        probe = BASE_OUTPUT / ".codex_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        info["writable"] = True
    except Exception as e:
        info["success"] = False
        info["error"] = f"草稿输出目录不可写: {e}"
    return info

# ============================================================
# Transition mapping
# ============================================================
def _get_transition(name: str):
    """Get TransitionType by Chinese name. Supports all 453 JianYing transitions."""
    if not name or name == "硬切":
        return None
    # Try direct attribute access
    try:
        return getattr(TransitionType, name)
    except AttributeError:
        pass
    # Fallback aliases
    aliases = {
        "dissolve": "叠化", "fadeblack": "闪黑", "fadewhite": "闪白",
        "fade": "叠化", "hblur": "模糊", "blur": "模糊",
        "黑场": "闪黑", "白场": "闪白",
    }
    mapped = aliases.get(name)
    if mapped:
        try:
            return getattr(TransitionType, mapped)
        except AttributeError:
            pass
    return None


# ============================================================
# API Endpoints
# ============================================================

@app.route("/create_draft", methods=["POST"])
def create_draft():
    """创建新的剪映工程"""
    health = _draft_health()
    if not health.get("success"):
        return _error_response(
            health.get("error", "草稿目录不可用"),
            code="draft_output_unavailable",
            stage="create_draft",
            details=health,
        )
    data = request.get_json()
    name = data.get("draft_name") or data.get("name") or f"draft_{uuid.uuid4().hex[:8]}"
    width = data.get("width", 1080)
    height = data.get("height", 1920)
    fps = data.get("fps", 30)

    script = Script_file(width=width, height=height, fps=fps, maintrack_adsorb=True)
    script.add_track(Track_type.video, "主视频")
    script.add_track(Track_type.text, "文字")
    script.add_track(Track_type.video, "叠加层")
    script.add_track(Track_type.audio)

    draft_id = f"dfd_{name}_{uuid.uuid4().hex[:8]}"
    drafts[draft_id] = script
    draft_names[draft_id] = name  # Store original display name

    draft_dir = str(BASE_OUTPUT / draft_id)
    draft_paths[draft_id] = draft_dir

    return jsonify({
        "success": True,
        "draft_id": draft_id,
        "draft_dir": draft_dir,
    })


@app.route("/health", methods=["GET"])
def health():
    """MCP health check for draft output and host mapping."""
    return jsonify(_draft_health())


@app.route("/add_video", methods=["POST"])
def add_video():
    """添加视频素材到时间线"""
    data = request.get_json()
    draft_id = data.get("draft_id")
    video_path = data.get("video_path")
    start_s = data.get("start", 0)  # 在时间线上的起始位置（秒）
    duration_s = data.get("duration", 5)  # 时长（秒）
    trim_start_s = data.get("trim_start", 0)  # 素材内的起始裁切点（秒）
    speed = data.get("speed", 1.0)
    volume = data.get("volume", 1.0)
    transition = data.get("transition")  # 与前一个片段的转场
    transition_duration_s = data.get("transition_duration", 0.5)

    if draft_id not in drafts:
        return jsonify({"success": False, "error": "Draft not found"})

    script = drafts[draft_id]

    # Create material
    try:
        mat = Video_material(path=video_path)
    except Exception as e:
        return jsonify({"success": False, "error": f"Material error: {e}"})

    # Clamp source range to material duration
    mat_dur_s = mat.duration / 1_000_000  # microseconds to seconds
    source_dur = duration_s * speed
    if trim_start_s + source_dur > mat_dur_s:
        # Adjust: try shifting trim_start back, or shorten source
        if source_dur <= mat_dur_s:
            trim_start_s = max(0, mat_dur_s - source_dur)
        else:
            # Source too short, use entire material
            trim_start_s = 0
            source_dur = mat_dur_s

    # Create segment
    target_tr = trange(f"{start_s}s", f"{duration_s}s")
    source_tr = trange(f"{trim_start_s}s", f"{source_dur}s")

    try:
        seg = Video_segment(mat, target_timerange=target_tr,
                            source_timerange=source_tr,
                            speed=speed, volume=volume)
    except ValueError as e:
        # Last resort: use full material without source range
        seg = Video_segment(mat, target_timerange=target_tr)

    # Add transition
    if transition:
        trans = _get_transition(transition)
        if trans:
            try:
                seg.add_transition(trans, duration=f"{transition_duration_s}s")
            except Exception:
                pass

    # Add intro/outro animations (separate from transitions)
    intro_anim = data.get("intro_animation")
    intro_dur = data.get("intro_duration", 0.5)
    if intro_anim:
        try:
            anim = getattr(IntroType, intro_anim, None)
            if anim:
                seg.add_animation(anim, duration=f"{intro_dur}s")
        except Exception:
            pass

    outro_anim = data.get("outro_animation")
    outro_dur = data.get("outro_duration", 0.5)
    if outro_anim:
        try:
            anim = getattr(OutroType, outro_anim, None)
            if anim:
                seg.add_animation(anim, duration=f"{outro_dur}s")
        except Exception:
            pass

    script.add_segment(seg, "主视频")
    draft_segments.setdefault(draft_id, []).append(seg)
    seg_index = len(draft_segments[draft_id]) - 1

    return jsonify({"success": True, "draft_id": draft_id, "segment_index": seg_index})


@app.route("/add_image", methods=["POST"])
def add_image():
    """添加图片素材（用于 LOGO 等静态画面）
    scale_mode: "cover"(填满,裁切) / "contain"(完整显示,可能留黑) / "auto"(默认,根据宽高比自动判断)
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    image_path = data.get("image_path")
    start_s = data.get("start", 0)
    duration_s = data.get("duration", 3)
    scale_mode = data.get("scale_mode", "auto")

    if draft_id not in drafts:
        return jsonify({"success": False, "error": "Draft not found"})

    script = drafts[draft_id]

    mat = Video_material(path=image_path)

    canvas_w = script.width
    canvas_h = script.height
    img_w = mat.width or canvas_w
    img_h = mat.height or canvas_h

    # Auto mode: decide based on aspect ratio
    if scale_mode == "auto" and img_w > 0 and img_h > 0:
        img_ratio = img_w / img_h
        canvas_ratio = canvas_w / canvas_h
        # If image aspect ratio is very different from canvas (e.g. wide banner on portrait)
        # use contain (show full image, don't crop). Otherwise cover (fill screen).
        if img_ratio / canvas_ratio > 2.0 or canvas_ratio / img_ratio > 2.0:
            scale_mode = "contain"  # Very different ratio → show complete
        else:
            scale_mode = "cover"  # Similar ratio → fill screen

    extra_scale = 1.0
    if img_w > 0 and img_h > 0:
        contain_scale = min(canvas_w / img_w, canvas_h / img_h)
        fitted_w = img_w * contain_scale
        fitted_h = img_h * contain_scale

        if scale_mode == "cover":
            cover_x = canvas_w / fitted_w if fitted_w > 0 else 1.0
            cover_y = canvas_h / fitted_h if fitted_h > 0 else 1.0
            extra_scale = max(cover_x, cover_y)
        else:
            # contain: already fitted, scale=1.0 shows full image
            extra_scale = 1.0

    clip = Clip_settings(scale_x=extra_scale, scale_y=extra_scale)
    seg = Video_segment(mat, target_timerange=trange(f"{start_s}s", f"{duration_s}s"),
                        clip_settings=clip)

    script.add_segment(seg, "主视频")
    draft_segments.setdefault(draft_id, []).append(seg)
    seg_index = len(draft_segments[draft_id]) - 1

    return jsonify({"success": True, "draft_id": draft_id, "segment_index": seg_index})


@app.route("/add_image_overlay", methods=["POST"])
def add_image_overlay():
    """添加图片叠加层（如品牌文字图片叠加到视频上方）
    用于将设计好的文字/LOGO 图片以指定透明度叠加到视频画面上。
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    image_path = data.get("image_path")
    start_s = data.get("start", 0)
    duration_s = data.get("duration", 5)
    alpha = data.get("alpha", 1.0)  # 透明度 0-1
    scale = data.get("scale", 1.0)  # 缩放比例
    position_x = data.get("position_x", 0)
    position_y = data.get("position_y", 0)

    if draft_id not in drafts:
        return jsonify({"success": False, "error": "Draft not found"})

    script = drafts[draft_id]

    mat = Video_material(path=image_path)

    # Convert UI coords (0=top/left, 1=bottom/right) to CapCut (-1..1, with y flipped)
    transform_x = position_x * 2 - 1
    transform_y = 1 - position_y * 2

    clip = Clip_settings(
        alpha=alpha,
        scale_x=scale,
        scale_y=scale,
        transform_x=transform_x,
        transform_y=transform_y,
    )

    seg = Video_segment(mat, target_timerange=trange(f"{start_s}s", f"{duration_s}s"),
                        clip_settings=clip)

    # Add to a separate overlay track (not main video track)
    try:
        script.add_segment(seg, "叠加层")
    except Exception:
        track_name = f"叠加层_{uuid.uuid4().hex[:4]}"
        script.add_track(Track_type.video, track_name)
        script.add_segment(seg, track_name)

    return jsonify({"success": True, "draft_id": draft_id})


@app.route("/add_video_overlay", methods=["POST"])
def add_video_overlay():
    """叠加视频到画中画轨道（不替换主轨音频）
    适用于 B-roll 遮盖硬切：覆盖主画面但保留主音频。
    volume=0 让覆盖视频静音，主音频继续。
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    video_path = data.get("video_path")
    start_s = data.get("start", 0)
    duration_s = data.get("duration", 2.0)
    trim_start_s = data.get("trim_start", 0)
    alpha = data.get("alpha", 1.0)
    scale_mode = data.get("scale_mode", "contain")  # "contain"(保持比例，黑边)/"cover"(填满,裁切)/manual
    scale = data.get("scale", 1.0)
    position_x = data.get("position_x", 0.5)
    position_y = data.get("position_y", 0.5)
    volume = data.get("volume", 0)  # 默认静音

    if draft_id not in drafts:
        return jsonify({"success": False, "error": "Draft not found"})

    script = drafts[draft_id]

    try:
        mat = Video_material(path=video_path)
    except Exception as e:
        return jsonify({"success": False, "error": f"Material error: {e}"})

    # Clamp source range
    mat_dur_s = mat.duration / 1_000_000
    src_dur = min(duration_s, mat_dur_s - trim_start_s)
    if src_dur <= 0:
        trim_start_s = 0
        src_dur = min(duration_s, mat_dur_s)

    # Convert UI coords to CapCut transform
    transform_x = position_x * 2 - 1
    transform_y = 1 - position_y * 2

    # Auto-scale: B-roll fills canvas fully (cover mode) regardless of aspect ratio
    if scale_mode == "cover":
        try:
            bw, bh = getattr(mat, "width", 0) or 0, getattr(mat, "height", 0) or 0
            canvas_w = script.width
            canvas_h = script.height
            if bw > 0 and bh > 0 and canvas_w > 0 and canvas_h > 0:
                # Base fit ratio (pyJianYingDraft auto-fits to canvas)
                fit = min(canvas_w / bw, canvas_h / bh)
                # Extra scale to cover
                cover_x = (canvas_w / bw) / fit
                cover_y = (canvas_h / bh) / fit
                scale = max(cover_x, cover_y)
        except Exception:
            pass

    clip = Clip_settings(
        alpha=alpha, scale_x=scale, scale_y=scale,
        transform_x=transform_x, transform_y=transform_y,
    )

    target_tr = trange(f"{start_s}s", f"{duration_s}s")
    source_tr = trange(f"{trim_start_s}s", f"{src_dur}s")

    try:
        seg = Video_segment(mat, target_timerange=target_tr,
                            source_timerange=source_tr,
                            volume=volume, clip_settings=clip)
    except ValueError:
        seg = Video_segment(mat, target_timerange=target_tr,
                            volume=volume, clip_settings=clip)

    # Add to overlay track (separate from main video)
    try:
        script.add_segment(seg, "叠加层")
    except Exception:
        track_name = f"叠加层_{uuid.uuid4().hex[:4]}"
        script.add_track(Track_type.video, track_name)
        script.add_segment(seg, track_name)

    draft_segments.setdefault(draft_id, []).append(seg)
    return jsonify({"success": True, "draft_id": draft_id, "segment_index": len(draft_segments[draft_id]) - 1})


@app.route("/add_text", methods=["POST"])
def add_text():
    """添加文字层"""
    data = request.get_json()
    draft_id = data.get("draft_id")
    text = data.get("text", "")
    start_s = data.get("start", 0)
    duration_s = data.get("duration", 5)
    font_size = data.get("font_size", 8)
    color = data.get("color", [1.0, 1.0, 1.0])  # RGB 0-1
    alpha = data.get("alpha", 1.0)
    align = data.get("align", 1)  # 0=left, 1=center, 2=right
    position_y = data.get("position_y", 0)  # transform_y offset
    bold = data.get("bold", False)
    animation_in = data.get("animation_in", "渐显")  # 入场动画
    animation_in_duration_s = data.get("animation_in_duration", 0.5)
    animation_loop = data.get("animation_loop")  # 循环动画（如 "扭动", "摇摆", "颤抖"）

    if draft_id not in drafts:
        return jsonify({"success": False, "error": "Draft not found"})

    script = drafts[draft_id]

    if isinstance(color, str) and color.startswith("#"):
        # Convert hex to RGB 0-1
        h = color.lstrip("#")
        color = [int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4)]

    style = Text_style(
        size=font_size,
        bold=bold,
        color=tuple(color),
        alpha=alpha,
        align=align,
    )

    # Convert UI convention (0=top, 1=bottom) to CapCut transform_y (1=top, -1=bottom)
    transform_y = 1 - position_y * 2
    clip = Clip_settings(transform_y=transform_y)

    seg = Text_segment(
        text, trange(f"{start_s}s", f"{duration_s}s"),
        style=style, clip_settings=clip,
    )

    # Add text animations (intro first, then loop — order matters for pyJianYingDraft)
    if animation_in:
        try:
            anim = getattr(TextIntro, animation_in, None)
            if anim:
                seg.add_animation(anim, duration=f"{animation_in_duration_s}s")
        except Exception:
            pass
    if animation_loop:
        try:
            loop_anim = getattr(TextLoopAnim, animation_loop, None)
            if loop_anim:
                seg.add_animation(loop_anim)
        except Exception:
            pass

    # Apply flower text effect BEFORE add_segment (pyJianYingDraft snapshots at add_segment time)
    flower_id = data.get("flower_text_effect_id")
    if flower_id:
        try:
            seg.add_effect(flower_id)
        except Exception as e:
            print(f"Warning: flower text effect {flower_id} failed: {e}")

    # Try adding to existing text track, if overlap create a new track
    try:
        script.add_segment(seg, "文字")
    except Exception:
        # Overlap — create a new text track
        track_name = f"文字_{uuid.uuid4().hex[:4]}"
        script.add_track(Track_type.text, track_name)
        script.add_segment(seg, track_name)

    # Track text segments for later flower-text application
    draft_text_segments.setdefault(draft_id, []).append(seg)
    seg_idx = len(draft_text_segments[draft_id]) - 1

    return jsonify({"success": True, "draft_id": draft_id, "segment_index": seg_idx})


@app.route("/add_audio", methods=["POST"])
def add_audio():
    """添加音频轨道
    trim_start: 音频裁剪起点（秒），用于跳过BGM前奏
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    audio_path = data.get("audio_path")
    start_s = data.get("start", 0)
    duration_s = data.get("duration")
    trim_start_s = data.get("trim_start", 0)
    volume = data.get("volume", 1.0)
    fade_in_s = data.get("fade_in", 0)
    fade_out_s = data.get("fade_out", 0)

    if draft_id not in drafts:
        return jsonify({"success": False, "error": "Draft not found"})

    script = drafts[draft_id]

    # If the audio source is a video file, extract audio first
    ext = os.path.splitext(audio_path)[1].lower()
    if ext in (".mp4", ".mov", ".avi", ".mkv", ".webm"):
        import subprocess
        extracted = os.path.join(str(BASE_OUTPUT), f"audio_{uuid.uuid4().hex[:8]}.mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-i", audio_path, "-vn", "-c:a", "libmp3lame", "-b:a", "192k", extracted],
            capture_output=True,
        )
        audio_path = extracted

    try:
        mat = Audio_material(path=audio_path)
    except Exception as e:
        return jsonify({"success": False, "error": f"Audio material error: {e}"})

    # Clamp duration to material length (accounting for trim)
    mat_dur_s = mat.duration / 1_000_000
    available = mat_dur_s - trim_start_s
    if available <= 0:
        trim_start_s = 0
        available = mat_dur_s
    if duration_s and duration_s > available:
        duration_s = available

    target_tr = trange(f"{start_s}s", f"{duration_s}s") if duration_s else trange(f"{start_s}s", f"{available}s")
    source_dur = duration_s or available
    source_tr = trange(f"{trim_start_s}s", f"{source_dur}s")

    try:
        seg = Audio_segment(mat, target_timerange=target_tr,
                            source_timerange=source_tr, volume=volume)
    except Exception:
        # Fallback without source_timerange
        seg = Audio_segment(mat, target_timerange=target_tr, volume=volume)

    if fade_in_s or fade_out_s:
        try:
            seg.add_fade(
                in_duration=f"{fade_in_s}s" if fade_in_s else None,
                out_duration=f"{fade_out_s}s" if fade_out_s else None,
            )
        except Exception:
            pass

    # Allow explicit track_name (e.g. "BGM"); auto-create a new audio track on overlap
    track_name = data.get("track_name")
    try:
        if track_name:
            # Create the track if it doesn't exist yet
            if track_name not in getattr(script, "tracks", {}):
                script.add_track(Track_type.audio, track_name)
            script.add_segment(seg, track_name)
        else:
            script.add_segment(seg)
    except Exception as e:
        # Likely SegmentOverlap on default track — create a new numbered audio track
        overflow_name = track_name or f"audio_{uuid.uuid4().hex[:4]}"
        try:
            if overflow_name not in getattr(script, "tracks", {}):
                script.add_track(Track_type.audio, overflow_name)
            script.add_segment(seg, overflow_name)
        except Exception as e2:
            return jsonify({"success": False, "error": f"add_audio failed: {e} / retry: {e2}"})

    return jsonify({"success": True, "draft_id": draft_id})


@app.route("/add_keyframe", methods=["POST"])
def add_keyframe():
    """添加关键帧 — 实现推拉摇移等运镜效果
    property: scale/position_x/position_y/rotation/alpha
    time: 关键帧时间点（秒，相对于片段开头）
    value: 属性值
    segment_index: 片段索引（-1=最后一个）

    运镜示例：
    - 慢推：在0s设scale=1.0，在duration设scale=1.2
    - 横摇：在0s设position_x=-0.1，在duration设position_x=0.1
    - 旋转：在0s设rotation=0，在duration设rotation=5
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    segment_index = data.get("segment_index", -1)
    property_name = data.get("property", "scale")
    time_s = data.get("time", 0)
    value = data.get("value", 1.0)

    if draft_id not in draft_segments or not draft_segments[draft_id]:
        return jsonify({"success": False, "error": "No segments in draft"})

    segs = draft_segments[draft_id]
    if segment_index == -1:
        segment_index = len(segs) - 1
    if segment_index >= len(segs):
        return jsonify({"success": False, "error": f"Segment index {segment_index} out of range"})

    prop_map = {
        "scale": Keyframe_property.uniform_scale,
        "uniform_scale": Keyframe_property.uniform_scale,
        "scale_x": Keyframe_property.scale_x,
        "scale_y": Keyframe_property.scale_y,
        "alpha": Keyframe_property.alpha,
        "position_x": Keyframe_property.position_x,
        "position_y": Keyframe_property.position_y,
        "rotation": Keyframe_property.rotation,
    }
    prop = prop_map.get(property_name)
    if not prop:
        return jsonify({"success": False, "error": f"Unknown property: {property_name}. Available: {list(prop_map.keys())}"})

    try:
        segs[segment_index].add_keyframe(prop, f"{time_s}s", value)
        return jsonify({"success": True, "draft_id": draft_id, "segment_index": segment_index})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/add_mask", methods=["POST"])
def add_mask():
    """添加蒙版
    mask_type: 线性/镜面/圆形/矩形/爱心/星形
    center_x/center_y: 中心位置 (-1.0 到 1.0)
    size: 大小 (0-1)
    rotation: 旋转角度
    feather: 羽化 (0-1)
    invert: 是否反转
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    segment_index = data.get("segment_index", -1)
    mask_type_name = data.get("mask_type", "矩形")

    if draft_id not in draft_segments or not draft_segments[draft_id]:
        return jsonify({"success": False, "error": "No segments in draft"})

    segs = draft_segments[draft_id]
    if segment_index == -1:
        segment_index = len(segs) - 1
    if segment_index >= len(segs):
        return jsonify({"success": False, "error": f"Segment index {segment_index} out of range"})

    mask_type = getattr(MaskType, mask_type_name, None)
    if not mask_type:
        return jsonify({"success": False, "error": f"Unknown mask: {mask_type_name}. Available: {_enum_names(MaskType)}"})

    try:
        segs[segment_index].add_mask(
            mask_type,
            center_x=data.get("center_x", 0.0),
            center_y=data.get("center_y", 0.0),
            size=data.get("size", 0.5),
            rotation=data.get("rotation", 0.0),
            feather=data.get("feather", 0.0),
            invert=data.get("invert", False),
        )
        return jsonify({"success": True, "draft_id": draft_id, "segment_index": segment_index})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/add_effect", methods=["POST"])
def add_effect():
    """添加特效（1097种场景特效 + 240种人物特效）
    effect_name: 特效名（如 "胶片闪烁", "VHS", "光效"）
    effect_category: scene(场景) 或 character(人物)，默认scene
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    segment_index = data.get("segment_index", -1)
    effect_name = data.get("effect_name")
    category = data.get("effect_category", "scene")

    if draft_id not in draft_segments or not draft_segments[draft_id]:
        return jsonify({"success": False, "error": "No segments in draft"})

    segs = draft_segments[draft_id]
    if segment_index == -1:
        segment_index = len(segs) - 1
    if segment_index >= len(segs):
        return jsonify({"success": False, "error": f"Segment index {segment_index} out of range"})

    if category == "character":
        effect_type = getattr(VideoCharacterEffectType, effect_name, None)
    else:
        effect_type = getattr(VideoSceneEffectType, effect_name, None)

    if not effect_type:
        return jsonify({"success": False, "error": f"Unknown effect: {effect_name}"})

    try:
        segs[segment_index].add_effect(effect_type)
        return jsonify({"success": True, "draft_id": draft_id, "segment_index": segment_index})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/add_filter", methods=["POST"])
def add_filter():
    """添加滤镜（1052种）
    filter_name: 滤镜名
    intensity: 强度 0-100，默认100
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    segment_index = data.get("segment_index", -1)
    filter_name = data.get("filter_name")
    intensity = data.get("intensity", 100.0)

    if draft_id not in draft_segments or not draft_segments[draft_id]:
        return jsonify({"success": False, "error": "No segments in draft"})

    segs = draft_segments[draft_id]
    if segment_index == -1:
        segment_index = len(segs) - 1
    if segment_index >= len(segs):
        return jsonify({"success": False, "error": f"Segment index {segment_index} out of range"})

    filter_type = getattr(FilterType, filter_name, None)
    if not filter_type:
        return jsonify({"success": False, "error": f"Unknown filter: {filter_name}"})

    try:
        segs[segment_index].add_filter(filter_type, intensity=intensity)
        return jsonify({"success": True, "draft_id": draft_id, "segment_index": segment_index})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/add_fade", methods=["POST"])
def add_fade():
    """添加淡入淡出
    fade_in: 淡入时长（秒），0=无淡入
    fade_out: 淡出时长（秒），0=无淡出
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    segment_index = data.get("segment_index", -1)
    fade_in = data.get("fade_in", 0)
    fade_out = data.get("fade_out", 0)

    if draft_id not in draft_segments or not draft_segments[draft_id]:
        return jsonify({"success": False, "error": "No segments in draft"})

    segs = draft_segments[draft_id]
    if segment_index == -1:
        segment_index = len(segs) - 1
    if segment_index >= len(segs):
        return jsonify({"success": False, "error": f"Segment index {segment_index} out of range"})

    try:
        segs[segment_index].add_fade(f"{fade_in}s", f"{fade_out}s")
        return jsonify({"success": True, "draft_id": draft_id, "segment_index": segment_index})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/list_masks", methods=["GET"])
def list_masks():
    return jsonify(_enum_names(MaskType))


@app.route("/list_effects", methods=["GET"])
def list_effects():
    return jsonify({
        "scene": _enum_names(VideoSceneEffectType),
        "character": _enum_names(VideoCharacterEffectType),
        "audio": _enum_names(AudioSceneEffectType),
    })


@app.route("/list_filters", methods=["GET"])
def list_filters_endpoint():
    return jsonify(_enum_names(FilterType))


@app.route("/add_audio_effect", methods=["POST"])
def add_audio_effect():
    """给音频轨道加音效（如"麦霸"、"人声增强"等 AudioSceneEffectType）

    注意：AudioSceneEffectType 是应用于整个音频片段的效果。
    short one-shot 音效请用 add_audio 加音频素材。

    effect_name: AudioSceneEffectType 名称
    segment_index: 片段索引（默认 -1 = 最后）
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    segment_index = data.get("segment_index", -1)
    effect_name = data.get("effect_name")

    if draft_id not in drafts:
        return jsonify({"success": False, "error": "Draft not found"})

    if AudioSceneEffectType is None:
        return jsonify({"success": False, "error": "AudioSceneEffectType not available in this pyJianYingDraft"})
    effect_type = getattr(AudioSceneEffectType, effect_name, None)
    if not effect_type:
        return jsonify({"success": False, "error": f"Unknown audio effect: {effect_name}"})

    # Find audio segments
    script = drafts[draft_id]
    audio_segs = []
    for track in script.tracks.values():
        if getattr(track, "track_type", None) == Track_type.audio or "audio" in str(type(track)).lower():
            segs = getattr(track, "segments", [])
            audio_segs.extend(segs)

    if not audio_segs:
        return jsonify({"success": False, "error": "No audio segments in draft"})

    target_idx = segment_index if segment_index >= 0 else len(audio_segs) + segment_index
    if not (0 <= target_idx < len(audio_segs)):
        return jsonify({"success": False, "error": f"Segment index {segment_index} out of range (0-{len(audio_segs)-1})"})

    try:
        audio_segs[target_idx].add_effect(effect_type)
        return jsonify({"success": True, "draft_id": draft_id, "segment_index": target_idx})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/add_text_effect", methods=["POST"])
def add_text_effect():
    """给文字加花字效果（需要剪映花字库的 effect_id）

    effect_id: 剪映花字资源 ID（如 "7108195580023343885"）
    resource_id: 剪映资源 ID（气泡用，花字不需要）
    type: "flower"（花字，默认）或 "bubble"（气泡）
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    segment_index = data.get("segment_index", -1)
    effect_id = data.get("effect_id")
    resource_id = data.get("resource_id", "")
    fx_type = data.get("type", "flower")

    if not effect_id:
        return jsonify({"success": False, "error": "effect_id 必须提供"})

    if draft_id not in drafts:
        return jsonify({"success": False, "error": "Draft not found"})

    # Prefer tracked text segments (from add_text)
    text_segs = draft_text_segments.get(draft_id, [])
    if not text_segs:
        # Fallback: scan draft tracks
        script = drafts[draft_id]
        for track in script.tracks.values():
            if "text" in str(type(track)).lower():
                text_segs.extend(getattr(track, "segments", []))

    if not text_segs:
        return jsonify({"success": False, "error": "No text segments in draft"})

    target_idx = segment_index if segment_index >= 0 else len(text_segs) + segment_index
    if not (0 <= target_idx < len(text_segs)):
        return jsonify({"success": False, "error": f"Text segment index {segment_index} out of range (0-{len(text_segs)-1})"})

    try:
        seg = text_segs[target_idx]
        if fx_type == "bubble":
            seg.add_bubble(effect_id, resource_id)
        else:
            seg.add_effect(effect_id)
        return jsonify({"success": True, "draft_id": draft_id, "segment_index": target_idx})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# Per-draft post-processing settings (applied at save time)
draft_post_settings: dict[str, dict] = {}


@app.route("/set_color_adjust", methods=["POST"])
def set_color_adjust():
    """设置色彩校正（亮度/对比度/饱和度/色温/色调）
    值范围：-50 到 50（或 -1.0 到 1.0，会自动换算）
    segment_index: None=所有片段, 0/1/2...=指定片段
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    segment_index = data.get("segment_index")  # None=所有

    if draft_id not in drafts:
        return jsonify({"success": False, "error": "Draft not found"})

    settings = draft_post_settings.setdefault(draft_id, {})
    settings.setdefault("color_adjust", []).append({
        "segment_index": segment_index,
        "brightness": data.get("brightness"),      # -100~100 或 -1.0~1.0
        "contrast": data.get("contrast"),           # -100~100 或 -1.0~1.0
        "saturation": data.get("saturation"),       # -100~100 或 -1.0~1.0
        "temperature": data.get("temperature"),     # -100~100 或 -1.0~1.0 (负=冷 正=暖)
        "tint": data.get("tint"),                   # -100~100 或 -1.0~1.0 (色调)
    })
    return jsonify({"success": True, "draft_id": draft_id})


@app.route("/set_stabilization", methods=["POST"])
def set_stabilization():
    """设置防抖（应用到所有视频素材或指定素材）
    stable_level: 0=关闭, 1=剪裁最少, 2=推荐, 3=最稳定
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    level = data.get("stable_level", 2)  # 默认"推荐"
    segment_index = data.get("segment_index")  # None=所有

    if draft_id not in drafts:
        return jsonify({"success": False, "error": "Draft not found"})

    settings = draft_post_settings.setdefault(draft_id, {})
    settings.setdefault("stabilization", []).append({
        "level": level,
        "segment_index": segment_index,
    })
    return jsonify({"success": True, "draft_id": draft_id, "stable_level": level})


@app.route("/set_vocal_separation", methods=["POST"])
def set_vocal_separation():
    """设置人声分离
    choice: 0=关闭, 1=保留背景音, 2=保留人声
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    choice = data.get("choice", 1)
    segment_index = data.get("segment_index")

    if draft_id not in drafts:
        return jsonify({"success": False, "error": "Draft not found"})

    settings = draft_post_settings.setdefault(draft_id, {})
    settings.setdefault("vocal_separation", []).append({
        "choice": choice,
        "segment_index": segment_index,
    })
    return jsonify({"success": True, "draft_id": draft_id, "choice": choice})


@app.route("/set_speed", methods=["POST"])
def set_speed():
    """设置变速
    mode: 0=常规, 1=曲线变速
    speed: 倍率（如 0.5, 1.0, 2.0）
    curve_speed: 曲线变速数据（mode=1 时使用）
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    segment_index = data.get("segment_index", 0)
    speed = data.get("speed", 1.0)
    mode = data.get("mode", 0)

    if draft_id not in drafts:
        return jsonify({"success": False, "error": "Draft not found"})

    settings = draft_post_settings.setdefault(draft_id, {})
    settings.setdefault("speed", []).append({
        "segment_index": segment_index,
        "speed": speed,
        "mode": mode,
    })
    return jsonify({"success": True, "draft_id": draft_id, "speed": speed})


@app.route("/set_ai_lip_sync", methods=["POST"])
def set_ai_lip_sync():
    """设置AI对口型
    segment_index: 视频片段索引
    audio_path: 音频文件路径（对口型的目标音频）
    """
    data = request.get_json()
    draft_id = data.get("draft_id")
    segment_index = data.get("segment_index", 0)

    if draft_id not in drafts:
        return jsonify({"success": False, "error": "Draft not found"})

    settings = draft_post_settings.setdefault(draft_id, {})
    settings.setdefault("ai_lip_sync", []).append({
        "segment_index": segment_index,
    })
    return jsonify({"success": True, "draft_id": draft_id,
                    "note": "AI对口型需要剪映在线处理，已标记启用"})


def _apply_post_settings(draft_id: str, d: dict) -> dict:
    """Apply post-processing settings to the draft JSON."""
    settings = draft_post_settings.get(draft_id, {})
    if not settings:
        return d

    # First: ensure each video material has vocal_separation + sound_channel_mapping
    # and they are linked via extra_material_refs (required for vocal separation to work)
    _ensure_material_refs(d)

    # Apply stabilization
    for stab in settings.get("stabilization", []):
        level = stab["level"]
        seg_idx = stab.get("segment_index")
        for i, v in enumerate(d.get("materials", {}).get("videos", [])):
            if seg_idx is not None and i != seg_idx:
                continue
            v["stable"] = {
                "stable_level": level,
                "matrix_path": "",
                "time_range": {"start": 0, "duration": 0},
            }

    # Apply vocal separation (choice: 0=off, 1=keep background, 2=keep voice)
    for vs in settings.get("vocal_separation", []):
        choice = vs["choice"]
        seg_idx = vs.get("segment_index")
        videos = d.get("materials", {}).get("videos", [])
        video_track_segs = []
        for track in d.get("tracks", []):
            if track.get("type") == "video":
                video_track_segs = track.get("segments", [])
                break

        for i, v in enumerate(videos):
            if seg_idx is not None and i != seg_idx:
                continue
            v["has_sound_separated"] = True

            # Find the vocal_separation linked to this segment
            if i < len(video_track_segs):
                refs = video_track_segs[i].get("extra_material_refs", [])
                for vsep in d["materials"]["vocal_separations"]:
                    if vsep["id"] in refs:
                        vsep["choice"] = choice
                        break

    # Apply speed changes
    for sp in settings.get("speed", []):
        seg_idx = sp["segment_index"]
        speed_val = sp["speed"]
        mode = sp["mode"]

        for track in d.get("tracks", []):
            if track.get("type") != "video":
                continue
            segs = track.get("segments", [])
            if seg_idx >= len(segs):
                continue
            seg = segs[seg_idx]

            # Update segment speed
            seg["speed"] = speed_val

            # Update source/target timerange for correct speed
            src = seg.get("source_timerange", {})
            tgt = seg.get("target_timerange", {})
            if src and tgt and speed_val != 0:
                # target_duration = source_duration / speed
                src_dur = src.get("duration", 0)
                new_tgt_dur = int(src_dur / speed_val)
                tgt["duration"] = new_tgt_dur

            # Update linked speed material
            refs = seg.get("extra_material_refs", [])
            for sm in d.get("materials", {}).get("speeds", []):
                if sm["id"] in refs:
                    sm["speed"] = speed_val
                    sm["mode"] = mode
                    break

    # Apply AI lip sync
    for lip in settings.get("ai_lip_sync", []):
        seg_idx = lip["segment_index"]
        for i, v in enumerate(d.get("materials", {}).get("videos", [])):
            if i != seg_idx:
                continue
            v.setdefault("video_algorithm", {})
            v["video_algorithm"]["mouth_shape_driver"] = {"enabled": True}

    # Apply color adjustments via common_keyframes
    for ca in settings.get("color_adjust", []):
        seg_idx = ca.get("segment_index")
        video_track_segs = []
        for track in d.get("tracks", []):
            if track.get("type") == "video":
                video_track_segs = track.get("segments", [])
                break

        for i, seg in enumerate(video_track_segs):
            if seg_idx is not None and i != seg_idx:
                continue

            kf_list = seg.setdefault("common_keyframes", [])

            # Map: param name → JianYing property type
            # Values: -1.0 to 1.0, display = value × 50
            param_map = {
                "brightness": "KFTypeBrightness",
                "contrast": "KFTypeContrast",
                "saturation": "KFTypeSaturation",
                "temperature": "KFTypeTemperature",
                "tint": "KFTypeColorTint",
            }

            for param, prop_type in param_map.items():
                value = ca.get(param)
                if value is None:
                    continue
                # Convert display value (-50~50) to internal (-1.0~1.0)
                if abs(value) > 1.0:
                    value = value / 50.0

                # Remove existing keyframe for this property
                kf_list[:] = [k for k in kf_list if k.get("property_type") != prop_type]

                kf_list.append({
                    "id": str(uuid.uuid4().hex[:32]),
                    "keyframe_list": [{
                        "curveType": "Line", "graphID": "",
                        "left_control": {"x": 0.0, "y": 0.0},
                        "right_control": {"x": 0.0, "y": 0.0},
                        "id": str(uuid.uuid4().hex[:32]),
                        "time_offset": 0,
                        "values": [value]
                    }],
                    "material_id": "",
                    "property_type": prop_type
                })

    return d


def _ensure_material_refs(d: dict):
    """Ensure each video segment has vocal_separation + sound_channel_mapping materials
    linked via extra_material_refs. Required for features like vocal separation to work."""
    videos = d.get("materials", {}).get("videos", [])
    vs_list = d.get("materials", {}).setdefault("vocal_separations", [])
    scm_list = d.get("materials", {}).setdefault("sound_channel_mappings", [])

    video_track_segs = []
    for track in d.get("tracks", []):
        if track.get("type") == "video":
            video_track_segs = track.get("segments", [])
            break

    # Collect existing vocal_separation ids already linked
    existing_vs_ids = set()
    for vs in vs_list:
        existing_vs_ids.add(vs["id"])

    for i in range(len(videos)):
        if i >= len(video_track_segs):
            break
        seg = video_track_segs[i]
        refs = seg.setdefault("extra_material_refs", [])

        # Check if vocal_separation is already linked
        has_vs = any(vs["id"] in refs for vs in vs_list)
        if not has_vs:
            vs_id = str(uuid.uuid4())
            vs_list.append({
                "id": vs_id, "type": "vocal_separation",
                "choice": 0, "production_path": "",
                "time_range": None, "removed_sounds": [],
            })
            refs.append(vs_id)

        # Check if sound_channel_mapping is already linked
        has_scm = any(scm["id"] in refs for scm in scm_list)
        if not has_scm:
            scm_id = str(uuid.uuid4())
            scm_list.append({
                "id": scm_id, "type": "sound_channel_mapping",
                "audio_channel_mapping": 0, "is_config_open": False,
            })
            refs.append(scm_id)


@app.route("/save_draft", methods=["POST"])
def save_draft():
    """保存工程到剪映草稿目录，生成完整的草稿文件结构"""
    import time as _time

    data = request.get_json()
    draft_id = data.get("draft_id")
    draft_name = data.get("name") or draft_names.get(draft_id) or draft_id or "AI剪辑"

    if draft_id not in drafts:
        return _error_response("Draft not found", code="draft_not_found", stage="save_draft")

    script = drafts[draft_id]
    draft_dir = str(BASE_OUTPUT / draft_name)

    try:
        os.makedirs(draft_dir, exist_ok=True)
    except Exception as e:
        return _error_response(
            f"创建草稿目录失败: {e}",
            code="draft_dir_create_failed",
            stage="save_draft",
            details={"draft_dir": draft_dir},
        )

    # Create required subdirectories
    try:
        for d in ["audio", "video", "image", "common_attachment", "cover"]:
            p = os.path.join(draft_dir, d)
            if os.path.isfile(p):
                os.remove(p)
            os.makedirs(p, exist_ok=True)
    except Exception as e:
        return _error_response(
            f"初始化草稿子目录失败: {e}",
            code="draft_subdirs_create_failed",
            stage="save_draft",
            details={"draft_dir": draft_dir},
        )

    # Apply color adjustments (filters) before dumping JSON
    color_settings = draft_post_settings.get(draft_id, {}).get("color_adjust", [])
    if color_settings:
        from pyJianYingDraft import FilterType
        # Get all video segments from the main track
        main_track = None
        for track_name, track_obj in script.tracks.items():
            if hasattr(track_obj, 'type') and str(track_obj.type) == 'video':
                main_track = track_obj
                break

        for ca in color_settings:
            filter_name = ca.get("filter")
            if filter_name:
                try:
                    ft = getattr(FilterType, filter_name, None)
                    if ft:
                        intensity = ca.get("filter_intensity", 80)
                        seg_idx = ca.get("segment_index")
                        # Apply to segments via script's stored segments
                        # Since we can't easily index segments, apply filter via post-processing JSON
                        # Store for JSON post-processing instead
                except Exception as e:
                    print(f"Warning: filter apply failed: {e}")

    # Save draft_info.json
    draft_file = os.path.join(draft_dir, "draft_info.json")
    try:
        # Use dumps() for plain JSON (dump() produces encrypted format)
        with open(draft_file, "w", encoding="utf-8") as df:
            df.write(script.dumps())
    except Exception as e:
        return _error_response(
            f"写入 draft_info.json 失败: {e}",
            code="draft_dump_failed",
            stage="save_draft",
            details={"draft_file": draft_file},
        )

    # Apply post-processing settings (stabilization, vocal separation, speed, lip sync)
    post_processing_warning = None
    try:
        with open(draft_file) as f:
            draft_data = json.load(f)
        draft_data = _apply_post_settings(draft_id, draft_data)
        with open(draft_file, "w", encoding="utf-8") as f:
            json.dump(draft_data, f, ensure_ascii=False)
    except Exception as e:
        post_processing_warning = f"post-processing failed: {e}"
        print(f"Warning: {post_processing_warning}")
        with open(draft_file) as f:
            draft_data = json.load(f)

    # Parse {emphasis} markup in text content → split into multi-range styles
    _apply_emphasis_markup(draft_data)

    # Fix flower text effectStyle paths (pyJianYingDraft hardcodes "C:" placeholder)
    _fix_flower_text_paths(draft_data)

    # Fix version to match current JianYing (pyJianYingDraft may generate old version)
    draft_data["version"] = 400000
    draft_data["new_version"] = "127.0.0"
    draft_data.setdefault("lyrics_effects", [])
    draft_data.setdefault("is_drop_frame_timecode", False)
    try:
        with open(draft_file, "w", encoding="utf-8") as f:
            json.dump(draft_data, f, ensure_ascii=False)
    except Exception as e:
        return _error_response(
            f"更新草稿版本信息失败: {e}",
            code="draft_version_write_failed",
            stage="save_draft",
            details={"draft_file": draft_file},
        )

    # Copy all referenced materials into draft folder (so JianYing can access them)
    import shutil
    material_copy_warning = None
    try:
        for v in draft_data.get("materials", {}).get("videos", []):
            old_path = v.get("path", "")
            if old_path and os.path.exists(old_path) and draft_dir not in old_path:
                fname = os.path.basename(old_path)
                ext = os.path.splitext(fname)[1].lower()
                sub = "image" if ext in (".jpg", ".jpeg", ".png", ".webp") else "video"
                new_path = os.path.join(draft_dir, sub, fname)
                if not os.path.exists(new_path):
                    shutil.copy2(old_path, new_path)
                v["path"] = new_path

        for a in draft_data.get("materials", {}).get("audios", []):
            old_path = a.get("path", "")
            if old_path and os.path.exists(old_path) and draft_dir not in old_path:
                fname = os.path.basename(old_path)
                new_path = os.path.join(draft_dir, "audio", fname)
                if not os.path.exists(new_path):
                    shutil.copy2(old_path, new_path)
                a["path"] = new_path

        # Rewrite container paths to host paths (for Docker volume mapping)
        if _HOST_DRAFT_DIR and _ENV_DRAFT_DIR:
            _rewrite_paths(draft_data, _ENV_DRAFT_DIR, _HOST_DRAFT_DIR)

        # Re-save with updated paths
        with open(draft_file, "w", encoding="utf-8") as f:
            json.dump(draft_data, f, ensure_ascii=False)
    except Exception as e:
        material_copy_warning = f"material copy failed: {e}"
        print(f"Warning: {material_copy_warning}")

    # Read back duration
    try:
        duration = draft_data.get("duration", 0)
    except Exception:
        duration = 0

    # Generate draft_meta_info.json
    now_us = int(_time.time() * 1000000)
    meta = {
        "cloud_draft_cover": False,
        "cloud_draft_sync": False,
        "draft_cover": "draft_cover.jpg",
        "draft_fold_path": draft_dir.replace(_ENV_DRAFT_DIR, _HOST_DRAFT_DIR) if _HOST_DRAFT_DIR and _ENV_DRAFT_DIR else draft_dir,
        "draft_id": str(uuid.uuid4()).upper(),
        "draft_is_invisible": False,
        "draft_materials": [{"type": 0, "value": []}, {"type": 1, "value": []}, {"type": 3, "value": []}],
        "draft_name": draft_name,
        "draft_root_path": _HOST_DRAFT_DIR if _HOST_DRAFT_DIR else str(BASE_OUTPUT),
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_draft_removed": 0,
        "tm_duration": duration,
    }
    meta_path = os.path.join(draft_dir, "draft_meta_info.json")
    try:
        with open(meta_path, "w") as f:
            json.dump(meta, f, ensure_ascii=False)
    except Exception as e:
        return _error_response(
            f"写入 draft_meta_info.json 失败: {e}",
            code="draft_meta_write_failed",
            stage="save_draft",
            details={"draft_meta_info": meta_path},
        )

    # Generate draft_cover.jpg (black placeholder)
    cover_path = os.path.join(draft_dir, "draft_cover.jpg")
    if not os.path.exists(cover_path):
        try:
            from PIL import Image
            Image.new("RGB", (1920, 1080), (0, 0, 0)).save(cover_path)
        except Exception:
            pass

    # Register draft in root_meta_info.json so JianYing can discover it
    root_meta_path = os.path.join(str(BASE_OUTPUT), "root_meta_info.json")
    try:
        if os.path.exists(root_meta_path):
            with open(root_meta_path) as f:
                root_meta = json.load(f)
        else:
            root_meta = {"all_draft_store": [], "draft_ids": [], "root_path": str(BASE_OUTPUT)}

        # Build entry with all fields JianYing expects
        host_draft_dir = meta["draft_fold_path"]
        host_cover = os.path.join(host_draft_dir, "draft_cover.jpg")
        entry = {
            "cloud_draft_cover": False,
            "cloud_draft_sync": False,
            "draft_cloud_last_action_download": False,
            "draft_cloud_purchase_info": "",
            "draft_cloud_template_id": "",
            "draft_cloud_tutorial_info": "",
            "draft_cloud_videocut_purchase_info": "",
            "draft_cover": host_cover,
            "draft_fold_path": host_draft_dir,
            "draft_id": meta["draft_id"],
            "draft_is_ai_shorts": False,
            "draft_is_cloud_temp_draft": False,
            "draft_is_invisible": False,
            "draft_is_web_article_video": False,
            "draft_json_file": os.path.join(host_draft_dir, "draft_info.json"),
            "draft_name": draft_name,
            "draft_new_version": "",
            "draft_root_path": meta["draft_root_path"],
            "draft_timeline_materials_size": 0,
            "draft_type": "",
            "draft_web_article_video_enter_from": "",
            "streaming_edit_draft_ready": False,
            "tm_draft_cloud_completed": 0,
            "tm_draft_cloud_entry_id": "",
            "tm_draft_cloud_modified": 0,
            "tm_draft_cloud_parent_entry_id": "",
            "tm_draft_cloud_space_id": "",
            "tm_draft_cloud_user_id": "",
            "tm_draft_create": now_us,
            "tm_draft_modified": now_us,
            "tm_draft_removed": 0,
            "tm_duration": duration,
        }

        # Remove existing entry with same name
        root_meta["all_draft_store"] = [
            d for d in root_meta.get("all_draft_store", [])
            if d.get("draft_name") != draft_name
        ]
        # Add new entry at the front (newest first)
        root_meta["all_draft_store"].insert(0, entry)

        with open(root_meta_path, "w") as f:
            json.dump(root_meta, f, ensure_ascii=False)
        print(f"Registered draft '{draft_name}' in root_meta_info.json")
    except Exception as e:
        print(f"Warning: failed to register draft in root_meta_info.json: {e}")

    # Cleanup in-memory draft data
    drafts.pop(draft_id, None)
    draft_paths.pop(draft_id, None)
    draft_post_settings.pop(draft_id, None)
    draft_segments.pop(draft_id, None)
    draft_text_segments.pop(draft_id, None)

    return jsonify({
        "success": True,
        "draft_id": draft_id,
        "draft_dir": draft_dir,
        "draft_file": draft_file,
        "draft_name": draft_name,
        "draft_open_path": draft_dir.replace(_ENV_DRAFT_DIR, _HOST_DRAFT_DIR) if _HOST_DRAFT_DIR and _ENV_DRAFT_DIR else draft_dir,
        "warning": "；".join([w for w in [post_processing_warning, material_copy_warning] if w]) or None,
    })


def _enum_names(enum_cls):
    """Get all member names from pyJianYingDraft enum."""
    try:
        return list(enum_cls._member_map_.keys())
    except AttributeError:
        return [k for k in dir(enum_cls) if not k.startswith("_")]


@app.route("/list_transitions", methods=["GET"])
def list_transitions():
    """列出所有可用的转场效果"""
    names = _enum_names(TransitionType)
    return jsonify({"transitions": names, "count": len(names)})


@app.route("/list_animations", methods=["GET"])
def list_animations():
    """列出所有可用的动画效果"""
    return jsonify({
        "intro": _enum_names(IntroType),
        "outro": _enum_names(OutroType),
        "text_intro": _enum_names(TextIntro),
        "text_outro": _enum_names(TextOutro),
    })


@app.route("/status", methods=["GET"])
def status():
    return jsonify({"status": "ok", "drafts": len(drafts)})


if __name__ == "__main__":
    print("CapCut MCP Server starting on port 9001...")
    print(f"Drafts output: {BASE_OUTPUT}")
    app.run(host="0.0.0.0", port=9001, debug=False)
