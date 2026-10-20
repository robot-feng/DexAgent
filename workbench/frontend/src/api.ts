import type { components } from "./generated/api";

export type Artifact = components["schemas"]["Artifact"];
export type RunSummary = components["schemas"]["RunSummary"];
export type RunDetail = components["schemas"]["RunDetail"];
export type Report = components["schemas"]["Report"];
export type Stage = components["schemas"]["Stage"];
export type Project = components["schemas"]["Project"];
export type Tool = components["schemas"]["Tool"];
export type Episode = components["schemas"]["Episode"];
export type EpisodeCatalogItem = components["schemas"]["EpisodeCatalogItem"];
export type Capability = components["schemas"]["Capability"];
export type Preview = components["schemas"]["Preview"];
export type Health = components["schemas"]["Health"];
export type StudioLiveStatus = components["schemas"]["StudioLiveStatus"];
export type EditorSession = components["schemas"]["EditorSession"];
export type SceneRevision = components["schemas"]["SceneRevision"];
export type CleanupCatalog = components["schemas"]["CleanupCatalog"];
export type CleanupCreate = components["schemas"]["CleanupCreate"];
export type CleanupJob = components["schemas"]["CleanupJob"];
export type Execution = components["schemas"]["Execution"];
export type ExecutionCreate = components["schemas"]["ExecutionCreate"];
export const prefix = "/api/workbench/v1";

export async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    prefix + path,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(
      typeof data?.detail === "string"
        ? data.detail
        : `请求失败 (${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}

export const contentUrl = (artifact: Artifact, download = false) =>
  `${prefix}/artifacts/${artifact.id}/content${download ? "?download=true" : ""}`;

export const api = {
  executionCatalog: () =>
    request<components["schemas"]["ExecutionCatalog"]>("/executions/catalog"),
  executionScene: (runId: string, configId: string) =>
    request<components["schemas"]["CandidateScene"]>(
      `/executions/scenes/${runId}?config_id=${configId}`,
    ),
  executions: () => request<Execution[]>("/executions"),
  createExecution: (body: ExecutionCreate) =>
    request<Execution>("/executions", body),
  stopExecution: (id: string) =>
    request<Execution>(`/executions/${id}/stop`, {}),
  retryExecution: (id: string) =>
    request<Execution>(`/executions/${id}/retry`, {}),
  executionLog: (id: string) =>
    request<components["schemas"]["ExecutionLog"]>(`/executions/${id}/log`),
  scenes: () => request<components["schemas"]["ScenePage"]>("/scenes"),
  sceneRevisions: (id: string, offset: number) =>
    request<components["schemas"]["SceneRevisionPage"]>(
      `/scenes/${id}/revisions?offset=${offset}&limit=10`,
    ),
  editors: () => request<EditorSession[]>("/editors"),
  editor: (id: string) => request<EditorSession>(`/editors/${id}`),
  createEditor: (scene_id: string, revision?: SceneRevision) =>
    request<EditorSession>("/editors", {
      scene_id,
      revision: revision
        ? {
            editor_id: revision.editor_id,
            revision_id: revision.id,
            sha256: revision.sha256,
          }
        : null,
    }),
  stopEditor: (id: string) => request<EditorSession>(`/editors/${id}/stop`, {}),
  health: () => request<Health>("/health"),
  capabilities: () => request<Capability[]>("/capabilities"),
  studioLive: () => request<StudioLiveStatus>("/studio/live"),
  cleanupCatalog: () => request<CleanupCatalog>("/background-cleanups/catalog"),
  cleanupJobs: () => request<CleanupJob[]>("/background-cleanups/jobs"),
  createCleanup: (body: CleanupCreate) =>
    request<CleanupJob>("/background-cleanups", body),
  cleanupJob: (id: string) => request<CleanupJob>(`/background-cleanups/${id}`),
  approveCleanupMask: (id: string) =>
    request<CleanupJob>(`/background-cleanups/${id}/approve-mask`, {}),
  buildCleanupScene: (id: string) =>
    request<CleanupJob>(`/background-cleanups/${id}/build-scene`, {}),
  stopCleanup: (id: string) =>
    request<CleanupJob>(`/background-cleanups/${id}/stop`, {}),
  runs: (search: string) =>
    request<components["schemas"]["RunPage"]>(`/runs?${search}`),
  run: (id: string) => request<RunDetail>(`/runs/${id}`),
  projects: () => request<Project[]>("/projects"),
  createProject: (name: string) => request<Project>("/projects", { name }),
  assign: (directory: string, project_id: string) =>
    request<RunDetail>("/imports", { directory, project_id }),
  refresh: () => request<Health>("/refresh", {}),
  createPreview: (run: string) => request<Preview>(`/runs/${run}/preview`, {}),
  preview: (id: string) => request<Preview>(`/previews/${id}`),
  artifacts: (run: string, search: string) =>
    request<components["schemas"]["ArtifactPage"]>(
      `/runs/${run}/artifacts?${search}`,
    ),
  episodes: (run: string) =>
    request<components["schemas"]["EpisodePage"]>(`/runs/${run}/episodes`),
  episodeCatalog: (search: string) =>
    request<components["schemas"]["EpisodeCatalogPage"]>(
      `/episode-catalog?${search}`,
    ),
  logs: (run: string) =>
    request<components["schemas"]["ArtifactPage"]>(`/runs/${run}/logs`),
  tools: (search: string) =>
    request<components["schemas"]["ToolPage"]>(`/tools?${search}`),
  text: (id: string, cursor = 0) =>
    request<components["schemas"]["LogChunk"]>(
      `/artifacts/${id}/text?cursor=${cursor}`,
    ),
  series: (id: string, field: string, channel: number) =>
    request<components["schemas"]["Series"]>(
      `/artifacts/${id}/series?field=${field}&channel=${channel}`,
    ),
};
