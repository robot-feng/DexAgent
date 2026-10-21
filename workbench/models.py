"""Public Studio contracts, shared by the API and its generated TypeScript client."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Validation = Literal['not_evaluated', 'passed', 'warning', 'failed', 'incomplete']


class ReadModel(BaseModel):
    # Defaults are always serialized. Keep OpenAPI and generated clients truthful.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class Issue(ReadModel):
    path: str
    message: str


class Check(ReadModel):
    id: str
    status: str
    required: bool = True
    observed: Any = None
    threshold: Any = None
    evidence: Any = None


class Artifact(ReadModel):
    id: str
    name: str
    path: str
    kind: str
    size: int
    modified: str
    privileged: bool = False


class Report(ReadModel):
    artifact: Artifact
    schema_id: str | None = None
    source_status: str | None = None
    evaluation_scope: str | None = None
    task_success_evaluated: bool | None = None
    validation: Validation
    checks: list[Check] = Field(default_factory=list)
    elapsed_s: float | None = None
    failure: str | None = None
    next_action: str | None = None
    attempt_id: str | None = None
    sha256: str


class Stage(ReadModel):
    id: str
    title: str
    report: Report | None = None
    progress: dict[str, Any] | None = None
    progress_stale: bool = False
    history: list[Report] = Field(default_factory=list)
    steps: list[Report] = Field(default_factory=list)


class Scope(ReadModel):
    requested_stages: list[str] = Field(default_factory=list)
    scope_complete: bool | None = None
    single_invocation_full_pipeline_complete: bool | None = None
    evidence_complete_across_invocations: bool | None = None
    explanation: str | None = None


class RunSummary(ReadModel):
    id: str
    name: str
    project_id: str = 'imported'
    source: str
    profile: str | None = None
    source_status: str | None = None
    execution: str
    validation: Validation
    release: str = 'not_evaluated'
    scope: Scope
    updated_at: str
    stage_count: int
    issue_count: int


class RunDetail(RunSummary):
    stages: list[Stage]
    reports: list[Artifact]
    issues: list[Issue]
    task: str | None = None
    input_video: Artifact | None = None
    semantics: dict[str, Any] | None = None
    sessions: list[dict[str, Any]] = Field(default_factory=list)
    media: list[Artifact] = Field(default_factory=list)
    available_actions: list[str] = Field(default_factory=list)
    recorded_3d_available: bool = False


class RunPage(ReadModel):
    items: list[RunSummary]
    total: int


class ArtifactPage(ReadModel):
    items: list[Artifact]
    total: int


class PlanningTrial(ReadModel):
    report: Artifact
    sha256: str
    outcome: Literal['passed', 'failed', 'incomplete']
    dry_run: bool
    expected_subgoals: int
    recorded_subgoals: int
    passed_subgoals: int
    failed_checks: list[str]
    duration_s: float | None = None
    n_frames: int | None = None


class PlanningStep(ReadModel):
    index: int
    motion: str
    side: str | None = None
    object: str | None = None
    ok: bool
    failed: list[str]
    frames: list[int] | None = None
    measured: dict[str, Any]
    error: str | None = None


class PlanningTrialDetail(PlanningTrial):
    subgoals: list[PlanningStep]


class PlanningTrials(ReadModel):
    items: list[PlanningTrial]
    issues: list[Issue]
    truncated: bool = False


class Episode(ReadModel):
    id: str
    run_id: str
    name: str
    task: str | None = None
    embodiment: str | None = None
    success: bool | None = None
    sim_s: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    recorded_steps: int | None = Field(default=None, ge=0)
    meta: Artifact
    steps: Artifact | None = None
    videos: list[Artifact] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class EpisodePage(ReadModel):
    items: list[Episode]
    total: int
    recorded_trajectory: Artifact | None = None


class EpisodeCatalogItem(Episode):
    run_name: str
    project_id: str
    project_name: str


class EpisodeCatalogPage(ReadModel):
    items: list[EpisodeCatalogItem]
    total: int


class EpisodeBundleRequest(BaseModel):
    include_simulator_state: bool = False


class EpisodeBundle(ReadModel):
    id: str
    run_id: str
    episode_id: str
    url: str
    size: int
    file_count: int
    sha256: str
    includes_simulator_state: bool
    omitted_episode_entries: list[str]


class Project(ReadModel):
    id: str
    name: str
    created_at: str
    run_count: int = 0


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ImportRequest(BaseModel):
    directory: str = Field(min_length=1, max_length=500)
    project_id: str = 'imported'


class LogChunk(ReadModel):
    text: str
    cursor: int
    identity: str
    reset: bool
    eof: bool


class Health(ReadModel):
    status: str
    index_error: str | None = None
    run_count: int


class StudioLiveStatus(ReadModel):
    configured: bool = False
    connected: bool = False
    checked_at: str
    run_dir: str | None = None
    url: str | None = None
    step: int | None = None
    sim_time: float | None = None
    paused: bool | None = None
    finite: bool | None = None
    playing: int | None = None
    generation: int | None = None
    names: list[str] = Field(default_factory=list)
    cameras: list[str] = Field(default_factory=list)
    positions: dict[str, list[float] | str] = Field(default_factory=dict)
    error: str | None = None


class Capability(ReadModel):
    id: str
    title: str
    available: bool
    level: str
    reason: str


class CleanupRequirement(ReadModel):
    id: str
    title: str
    available: bool
    detail: str


class CleanupVideo(ReadModel):
    id: str
    name: str
    path: str
    size: int
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    capture_type: Literal['scene_scan', 'operation_demo', 'unknown'] = 'unknown'


class CleanupCatalog(ReadModel):
    videos: list[CleanupVideo]
    requirements: list[CleanupRequirement]
    preview_available: bool
    build_available: bool


class CleanupCreate(BaseModel):
    video_id: str
    capture_type: Literal['scene_scan', 'operation_demo', 'unknown'] = 'unknown'
    floor_category: str = Field(default='desk', min_length=1, max_length=120)
    include_objects: list[str] = Field(default_factory=list, max_length=20)
    skip_objects: list[str] = Field(default_factory=list, max_length=50)
    propagation_model: Literal['sam2', 'sam3', 'litetext'] = 'litetext'
    proposal_backend: Literal['codex_external', 'gemini', 'manual'] = 'codex_external'


class CleanupMaskSummary(ReadModel):
    segmentation_model: str | None = None
    propagation_model: str | None = None
    device: str | None = None
    frame_count: int | None = None
    resolution_hw: list[int] = Field(default_factory=list)
    prompt_resolutions: dict[str, str] = Field(default_factory=dict)
    seed_frame_indices: dict[str, int] = Field(default_factory=dict)
    seed_scores: dict[str, float] = Field(default_factory=dict)


class CleanupJob(ReadModel):
    id: str
    scene_name: str
    source_video: CleanupVideo
    capture_type: Literal['scene_scan', 'operation_demo', 'unknown']
    status: Literal['preparing', 'review_mask', 'inpainting', 'review_clean', 'building',
                    'ready', 'failed', 'stopped']
    current_step: str
    message: str
    include_objects: list[str] = Field(default_factory=list)
    skip_objects: list[str] = Field(default_factory=list)
    propagation_model: Literal['sam2', 'sam3', 'litetext'] = 'litetext'
    proposal_backend: Literal['codex_external', 'gemini', 'manual'] = 'codex_external'
    proposal_transport_dir: str | None = None
    proposed_objects: list[str] = Field(default_factory=list)
    detected_objects: list[str] = Field(default_factory=list)
    mask_summary: CleanupMaskSummary | None = None
    mask_preview_url: str | None = None
    mask_video_url: str | None = None
    source_video_url: str
    cleaned_video_url: str | None = None
    scene_path: str | None = None
    can_build_scene: bool = False
    log_url: str
    updated_at: str


class Tool(ReadModel):
    name: str
    title: str = ''
    type: str
    category: str = ''
    status: str = 'unknown'
    summary: str = ''
    description: str = ''
    inputs: list[dict[str, Any]] = Field(default_factory=list)
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ToolPage(ReadModel):
    items: list[Tool]
    total: int


class Series(ReadModel):
    field: str
    channel: int
    channels: int
    available_fields: list[str]
    source_points: int
    times: list[float]
    values: list[float]
    value_unit: str
    channel_name: str | None = None


class Preview(ReadModel):
    id: str
    run_id: str
    status: Literal['generating', 'ready', 'failed']
    message: str
    url: str | None = None
    source_frames: int | None = None
    preview_frames: int | None = None
    duration_s: float | None = None
    state_source: str | None = None
    stale: bool = False
    trial_report_id: str | None = None


ExecutionMode = Literal['pipeline', 'rigid', 'towel', 'sharpa', 'grasp', 'reference', 'simfoundry', 'candidate', 'replan']


class TaskVariantInputs(ReadModel):
    sha256: str
    candidate_sha256: str
    source_run_id: str
    task: str


class SceneOffset(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    translation_m: tuple[Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)],
                         Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)],
                         Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]]


class CandidateObject(ReadModel):
    name: str
    joint: str
    position_m: tuple[float, float, float]
    quaternion_wxyz: tuple[float, float, float, float]


class CandidateScene(ReadModel):
    sha256: str
    objects: list[CandidateObject]


class ExecutionConfig(ReadModel):
    id: str
    name: str
    input_video: str | None = None
    input_video_exists: bool | None = None
    task: str | None = None
    error: str | None = None


class ExecutionCatalog(ReadModel):
    available: bool
    configs: list[ExecutionConfig]
    modes: list[ExecutionMode]
    detail: str


class RuntimePackage(ReadModel):
    name: str
    version: str | None = None
    available: bool


class SimulationProfile(ReadModel):
    mode: str
    solver: str
    physics_device: str
    detail: str


class SimulationRuntime(ReadModel):
    config_id: str | None = None
    config_name: str | None = None
    engine: str = 'Newton'
    integration: str = 'RoboSimStudio'
    python: str | None = None
    status: Literal['environment_detected', 'missing_dependencies', 'incompatible', 'error']
    checked_at: str
    detail: str
    packages: list[RuntimePackage] = Field(default_factory=list)
    profiles: list[SimulationProfile] = Field(default_factory=list)


class ExecutionCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    config_id: str = Field(pattern='^[a-f0-9]{24}$')
    mode: ExecutionMode = 'pipeline'
    profile: Literal['smoke', 'pipeline_eval', 'dataset_release'] = 'pipeline_eval'
    stop_at: Literal['stage1', 'stage2', 'stage3', 'stage4'] = 'stage4'
    video: str = Field(default='', max_length=1000)
    prompt: str = Field(default='', max_length=4000)
    reference_run_id: str | None = None
    scene_sha256: str | None = Field(default=None, pattern='^[a-f0-9]{64}$')
    task_inputs_sha256: str | None = Field(default=None, pattern='^[a-f0-9]{64}$')
    scene_offsets: list[SceneOffset] = Field(default_factory=list, max_length=100)
    stage8: str = Field(default='', max_length=1000)
    stage11: str = Field(default='', max_length=1000)
    stage12: str = Field(default='', max_length=1000)
    no_render: bool = False


class ExecutionAgentWait(ReadModel):
    request_id: str
    turn: int
    request_path: str


class Execution(ReadModel):
    id: str
    mode: ExecutionMode
    config_name: str
    profile: str | None = None
    status: Literal['running', 'completed', 'failed', 'stopped', 'interrupted']
    message: str
    output_name: str
    run_id: str | None = None
    returncode: int | None = None
    created_at: str
    finished_at: str | None = None
    retry_of: str | None = None
    agent_wait: ExecutionAgentWait | None = None
    candidate_sha256: str | None = None
    task_inputs_sha256: str | None = None


class ExecutionLog(ReadModel):
    text: str


class SceneFile(ReadModel):
    id: str
    name: str
    path: str
    sha256: str
    objects: int
    issue: str | None = None


class ScenePage(ReadModel):
    items: list[SceneFile]
    total: int


class SceneRevisionRef(ReadModel):
    editor_id: str = Field(pattern='^[a-f0-9]{24}$')
    revision_id: str = Field(pattern='^[a-f0-9]{24}$')
    sha256: str = Field(pattern='^[a-f0-9]{64}$')


class SceneRevision(ReadModel):
    id: str
    editor_id: str
    name: str
    sha256: str
    source_sha256: str
    parent_revision: SceneRevisionRef | None = None
    modified: str
    url: str


class SceneRevisionPage(ReadModel):
    items: list[SceneRevision]
    total: int


class EditorSession(ReadModel):
    id: str
    scene_id: str
    source_sha256: str
    parent_revision: SceneRevisionRef | None = None
    status: Literal['starting', 'ready', 'unresponsive', 'stopped', 'failed']
    message: str
    url: str | None = None
    source_changed: bool = False
    revisions: list[SceneRevision] = Field(default_factory=list)


class EditorCreate(BaseModel):
    scene_id: str
    revision: SceneRevisionRef | None = None


class PreviewRequest(BaseModel):
    episode_id: str | None = None
    trial_report_id: str | None = None
