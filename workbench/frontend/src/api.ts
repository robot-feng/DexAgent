import type { components } from "./generated/api";

export type Artifact = components["schemas"]["Artifact"];
export type RunSummary = components["schemas"]["RunSummary"];
export type RunDetail = components["schemas"]["RunDetail"];
export type Report = components["schemas"]["Report"];
export type Stage = components["schemas"]["Stage"];
export type Project = components["schemas"]["Project"];
export type Tool = components["schemas"]["Tool"];
export type Episode = components["schemas"]["Episode"];
export type Capability = components["schemas"]["Capability"];
export type Preview = components["schemas"]["Preview"];
export type Health = components["schemas"]["Health"];
export type StudioLiveStatus = components["schemas"]["StudioLiveStatus"];
export type EditorSession = components["schemas"]["EditorSession"];
export type SceneRevision = components["schemas"]["SceneRevision"];
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
  scenes: () => request<components["schemas"]["ScenePage"]>("/scenes"),
  sceneRevisions: (id: string, offset: number) =>
    request<components["schemas"]["SceneRevisionPage"]>(`/scenes/${id}/revisions?offset=${offset}&limit=10`),
  editors: () => request<EditorSession[]>("/editors"),
  editor: (id: string) => request<EditorSession>(`/editors/${id}`),
  createEditor: (scene_id: string, revision?: SceneRevision) =>
    request<EditorSession>("/editors", {
      scene_id,
      revision: revision ? { editor_id: revision.editor_id, revision_id: revision.id, sha256: revision.sha256 } : null,
    }),
  stopEditor: (id: string) => request<EditorSession>(`/editors/${id}/stop`, {}),
  health: () => request<Health>("/health"),
  capabilities: () => request<Capability[]>("/capabilities"),
  studioLive: () => request<StudioLiveStatus>("/studio/live"),
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
