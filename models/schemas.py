from pydantic import BaseModel, Field
from typing import Optional


class Shot(BaseModel):
    number: int
    start_time: float
    end_time: float
    duration: float
    keyframe_path: str = ""
    composition: str = ""
    camera_movement: str = ""
    lighting: str = ""
    focal_length: str = ""
    content: str = ""
    visual_description: str = ""
    emotion: str = ""
    transition_from_prev: str = ""
    function: str = ""
    detail_text: str = ""  # Full detail text from Step B analysis


class VideoInfo(BaseModel):
    filename: str
    resolution: str = ""
    fps: float = 0
    duration: float = 0
    codec: str = ""
    audio: str = ""
    shot_count: int = 0


class AnalysisResult(BaseModel):
    job_id: str
    display_name: str = ""  # user-editable name
    video_info: VideoInfo
    shots: list[Shot] = []
    overview: str = ""
    status: str = "pending"  # pending, processing, completed, error
    progress: float = 0
    error: Optional[str] = None


class AutoEditJob(BaseModel):
    job_id: str
    template_job_id: str
    status: str = "pending"
    progress: float = 0
    stage: str = "pending"
    output_path: Optional[str] = None
    error: Optional[str] = None
    warning: Optional[str] = None
    draft_dir: Optional[str] = None
    draft_name: Optional[str] = None
    delivery_mode: str = "host_draft_dir"
    open_mode: str = "auto_open"
    last_mcp_endpoint: Optional[str] = None
    last_material: Optional[str] = None
    diagnostics: dict = Field(default_factory=dict)
    match_results: list[dict] = []
