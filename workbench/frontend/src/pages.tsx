import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { FormEvent } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  Box,
  Check,
  Film,
  FolderPlus,
  Search,
  Wrench,
} from "lucide-react";
import { api, contentUrl } from "./api";
import { SimulationRuntimePanel } from "./SimulationRuntime";
import SceneEditor from "./SceneEditor";
import EpisodeDownload from "./EpisodeDownload";
import type { Artifact, Tool } from "./api";
import type { Episode, EpisodeCatalogItem } from "./api";
import {
  ArtifactDialog,
  Badge,
  Empty,
  ErrorNotice,
  formatBytes,
  formatTime,
  Loading,
  pretty,
} from "./components";

const SeriesPlot = lazy(() => import("./SeriesPlot"));

export function ProjectsPage() {
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const client = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const query = new URLSearchParams(params);
  query.set("limit", "50");
  const runs = useQuery({
    queryKey: ["runs", query.toString()],
    queryFn: () => api.runs(query.toString()),
    refetchInterval: 15_000,
  });
  const create = useMutation({
    mutationFn: () => api.createProject(name),
    onSuccess: (project) => {
      setCreating(false);
      setName("");
      setParams({ project_id: project.id });
      client.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    value ? next.set(key, value) : next.delete(key);
    next.delete("offset");
    setParams(next);
  };
  const offset = Number(params.get("offset") || 0);
  return (
    <main className="page">
      <div className="page-title">
        <div>
          <p className="eyebrow">PROJECTS / RUNS</p>
          <h1>从一次运行，看到完整过程</h1>
          <p className="muted">
            输入、阶段、验收与产物，保留在同一个工作空间。
          </p>
        </div>
        <button
          className="button primary"
          onClick={() => setCreating(!creating)}
        >
          <FolderPlus size={16} />
          新建项目
        </button>
      </div>
      {creating && (
        <form
          className="inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <input
            aria-label="项目名称"
            placeholder="项目名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={100}
            required
            autoFocus
          />
          <button
            className="button primary"
            disabled={create.isPending || !name.trim()}
          >
            创建
          </button>
          <button
            type="button"
            className="button"
            onClick={() => setCreating(false)}
          >
            取消
          </button>
          <ErrorNotice error={create.error} />
        </form>
      )}
      <div className="project-strip">
        <button
          className={
            !params.get("project_id") ? "project-chip selected" : "project-chip"
          }
          onClick={() => update("project_id", "")}
        >
          <Box size={17} />
          <span>全部项目</span>
        </button>
        {projects.data?.map((project) => (
          <button
            key={project.id}
            className={`project-chip ${params.get("project_id") === project.id ? "selected" : ""}`}
            onClick={() => update("project_id", project.id)}
          >
            <span>{project.name}</span>
            <b>{project.run_count}</b>
          </button>
        ))}
      </div>
      <section className="panel">
        <div className="list-toolbar">
          <div>
            <h2>
              运行记录{" "}
              <span className="counter">{runs.data?.total ?? "—"}</span>
            </h2>
            <p className="muted small">读取已配置目录中的真实报告</p>
          </div>
          <div className="filters">
            <label className="search">
              <Search size={16} />
              <input
                aria-label="搜索运行"
                placeholder="搜索运行名称…"
                value={params.get("q") || ""}
                onChange={(e) => update("q", e.target.value)}
              />
            </label>
            <select
              aria-label="按验收状态筛选"
              value={params.get("validation") || ""}
              onChange={(e) => update("validation", e.target.value)}
            >
              <option value="">全部验收状态</option>
              <option value="passed">通过</option>
              <option value="warning">有警告</option>
              <option value="failed">失败</option>
              <option value="incomplete">证据不完整</option>
              <option value="not_evaluated">未评估</option>
            </select>
          </div>
        </div>
        <ErrorNotice error={runs.error ?? projects.error} />
        {runs.isLoading ? (
          <Loading />
        ) : runs.data?.items.length === 0 ? (
          <Empty
            title="当前筛选没有运行记录"
            detail="已有报告会自动建立只读索引，新建项目后可在运行详情中归档。"
          />
        ) : (
          <div className="table-scroll">
            <table className="run-table">
              <thead>
                <tr>
                  <th>任务 / 运行</th>
                  <th>来源</th>
                  <th>验收</th>
                  <th>执行范围</th>
                  <th>更新 · 上海时间</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {runs.data?.items.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <Link className="run-name" to={`/runs/${run.id}`}>
                        <span className={`run-icon ${run.validation}`}>
                          <Box size={18} />
                        </span>
                        <span>
                          <strong>{run.name}</strong>
                          <small>
                            {run.profile ?? "profile 未记录"} ·{" "}
                            {run.stage_count} 个阶段
                            {run.issue_count > 0
                              ? ` · ${run.issue_count} 个读取问题`
                              : ""}
                          </small>
                        </span>
                      </Link>
                    </td>
                    <td>
                      <span className="source-tag">
                        {run.source === "robosimstudio"
                          ? "RoboSimStudio"
                          : "DexAgent"}
                      </span>
                    </td>
                    <td>
                      <Badge value={run.validation} />
                    </td>
                    <td className="small muted">
                      {run.scope.requested_stages?.join(" → ") ||
                        "原生后端验收"}
                      <small className="scope-note">
                        {run.scope.single_invocation_full_pipeline_complete ===
                        true
                          ? "单次全流程完成"
                          : run.scope.scope_complete
                            ? "本次范围完成"
                            : "范围以报告为准"}
                      </small>
                    </td>
                    <td className="mono small muted">
                      {formatTime(run.updated_at)}
                    </td>
                    <td>
                      <Link
                        className="icon-button"
                        aria-label={`打开 ${run.name}`}
                        to={`/runs/${run.id}`}
                      >
                        <ArrowRight size={16} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {runs.data && runs.data.total > 50 && (
          <div className="pagination">
            <button
              className="button"
              disabled={!offset}
              onClick={() => {
                const n = new URLSearchParams(params);
                n.set("offset", String(Math.max(0, offset - 50)));
                setParams(n);
              }}
            >
              上一页
            </button>
            <span>
              {offset + 1}–{Math.min(offset + 50, runs.data.total)} /{" "}
              {runs.data.total}
            </span>
            <button
              className="button"
              disabled={offset + 50 >= runs.data.total}
              onClick={() => {
                const n = new URLSearchParams(params);
                n.set("offset", String(offset + 50));
                setParams(n);
              }}
            >
              下一页
            </button>
          </div>
        )}
      </section>
      <div className="workflow-note">
        <span className="eyebrow">ONE WORKSPACE</span>
        <span>演示理解</span>
        <ArrowRight size={14} />
        <span>场景与轨迹</span>
        <ArrowRight size={14} />
        <span>运行时验收</span>
        <ArrowRight size={14} />
        <span>数据与回放</span>
      </div>
    </main>
  );
}

export function ArtifactList({
  runId,
  kind,
}: {
  runId: string;
  kind?: string;
}) {
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Artifact | null>(null);
  const query = new URLSearchParams({
    q: search,
    offset: String(offset),
    limit: "60",
  });
  if (kind) query.set("kind", kind);
  const artifacts = useQuery({
    queryKey: ["artifacts", runId, query.toString()],
    queryFn: () => api.artifacts(runId, query.toString()),
  });
  return (
    <div>
      <div className="list-toolbar">
        <h2>
          {kind === "model" ? "场景与模型" : "原生产物"}{" "}
          <span className="counter">{artifacts.data?.total ?? "—"}</span>
        </h2>
        <label className="search">
          <Search size={15} />
          <input
            placeholder="搜索产物路径…"
            aria-label="搜索产物"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setOffset(0);
            }}
          />
        </label>
      </div>
      <ErrorNotice error={artifacts.error} />
      {artifacts.isLoading ? (
        <Loading />
      ) : artifacts.data?.total === 0 ? (
        <Empty title="没有匹配的产物" />
      ) : (
        <div className="artifact-list">
          {artifacts.data?.items.map((artifact) => (
            <div className="artifact-row" key={artifact.id}>
              <button
                onClick={() => setSelected(artifact)}
                className="artifact-name"
              >
                <span className={`file-icon ${artifact.kind}`}>
                  {artifact.kind === "video" ? (
                    <Film size={17} />
                  ) : (
                    <Box size={17} />
                  )}
                </span>
                <span>
                  <strong>{artifact.name}</strong>
                  <small className="mono">{artifact.path}</small>
                </span>
              </button>
              <span className="mono small muted">
                {formatBytes(artifact.size)}
              </span>
              {artifact.privileged && (
                <span className="source-tag">诊断真值</span>
              )}
              <a
                className="icon-button"
                href={contentUrl(artifact, true)}
                aria-label={`下载 ${artifact.name}`}
              >
                <ArrowDownToLine size={16} />
              </a>
            </div>
          ))}
        </div>
      )}
      {artifacts.data && artifacts.data.total > 60 && (
        <div className="pagination">
          <button
            className="button"
            disabled={offset === 0}
            onClick={() => setOffset(offset - 60)}
          >
            上一页
          </button>
          <span>
            {offset + 1}–{Math.min(offset + 60, artifacts.data.total)} /{" "}
            {artifacts.data.total}
          </span>
          <button
            className="button"
            disabled={offset + 60 >= artifacts.data.total}
            onClick={() => setOffset(offset + 60)}
          >
            下一页
          </button>
        </div>
      )}
      {selected && (
        <ArtifactDialog artifact={selected} close={() => setSelected(null)} />
      )}
    </div>
  );
}

export function EpisodeList({ runId }: { runId: string }) {
  const episodes = useQuery({
    queryKey: ["episodes", runId],
    queryFn: () => api.episodes(runId),
  });
  const trajectory = episodes.data?.recorded_trajectory;
  const [trajectorySeries, setTrajectorySeries] = useState<Artifact | null>(null);
  return (
    <div>
      <ErrorNotice error={episodes.error} />
      {trajectory && (
        <article className="episode-card">
          <div className="section-heading">
            <div>
              <p className="eyebrow">DEXAGENT TRAJECTORY</p>
              <h3>原生机器人轨迹</h3>
            </div>
            <span className="source-tag">只读记录</span>
          </div>
          <p className="small muted">
            按记录中的 time_s 查看 qpos / qvel /
            ctrl，不重新执行动作。坐标含位置、旋转和关节等不同语义，保留原生数值。
          </p>
          <div className="actions">
            <button className="button" onClick={() => setTrajectorySeries(trajectory)}>
              查看轨迹
            </button>
            <a className="button" href={contentUrl(trajectory, true)}>
              原生 NPZ
            </a>
          </div>
        </article>
      )}
      {episodes.isLoading ? (
        <Loading />
      ) : episodes.data?.total === 0 && !trajectory ? (
        <Empty
          title="该运行没有原生 EpisodeStore 记录"
          detail="DexAgent 轨迹和合成视频可在产物中查看；不会将它们自动标为训练数据集。"
        />
      ) : (
        <EpisodeCards items={episodes.data?.items ?? []} />
      )}
      {trajectorySeries && (
        <Suspense fallback={<Loading />}>
          <SeriesPlot artifact={trajectorySeries} key={trajectorySeries.id} />
        </Suspense>
      )}
    </div>
  );
}

function EpisodeCards({
  items,
}: {
  items: Array<Episode | EpisodeCatalogItem>;
}) {
  const [selected, setSelected] = useState<Artifact | null>(null);
  const [series, setSeries] = useState<Artifact | null>(null);
  return (
    <>
      <div className="episode-grid">
        {items.map((episode) => (
          <article className="episode-card" key={episode.id}>
            <div className="section-heading">
              <span className="eyebrow">EPISODE {episode.name}</span>
              <Badge
                value={
                  episode.success === null
                    ? "not_evaluated"
                    : episode.success
                      ? "passed"
                      : "failed"
                }
              />
            </div>
            {"run_name" in episode && (
              <div className="episode-source">
                <span>{episode.project_name} · {episode.run_name}</span>
                <Link to={`/runs/${episode.run_id}?view=trajectory`}>
                  查看来源运行 ↗
                </Link>
              </div>
            )}
            <h3>{episode.task ?? "任务名称未记录"}</h3>
            {episode.issues.map((issue, index) => (
              <div className="notice danger" role="alert" key={index}>
                元信息读取异常：{issue.message}
              </div>
            ))}
            <dl>
              <dt>机器人</dt>
              <dd>{episode.embodiment ?? "未知"}</dd>
              <dt>仿真时长</dt>
              <dd>
                {episode.sim_s == null
                  ? "未知"
                  : `${episode.sim_s.toFixed(2)} s`}
              </dd>
              <dt>记录步数</dt>
              <dd>{episode.recorded_steps ?? "未知"}</dd>
            </dl>
            {episode.missing_fields.length > 0 && (
              <p className="small amber">
                元信息不完整：{episode.missing_fields.join("、")}
              </p>
            )}
            <div className="actions">
              <button className="button" onClick={() => setSelected(episode.meta)}>
                元信息
              </button>
              {episode.steps && (
                <button className="button" onClick={() => setSeries(episode.steps!)}>
                  查看轨迹
                </button>
              )}
              {episode.steps && (
                <a className="button" href={contentUrl(episode.steps, true)}>
                  原生 NPZ
                </a>
              )}
              {episode.videos.map((video) => (
                <button
                  key={video.id}
                  className="button"
                  onClick={() => setSelected(video)}
                >
                  <Film size={14} />
                  {video.name}
                </button>
              ))}
            </div>
            <EpisodeDownload runId={episode.run_id} episodeId={episode.id} />
          </article>
        ))}
      </div>
      {series && (
        <Suspense fallback={<Loading />}>
          <SeriesPlot artifact={series} key={series.id} />
        </Suspense>
      )}
      {selected && (
        <ArtifactDialog artifact={selected} close={() => setSelected(null)} />
      )}
    </>
  );
}

function DatasetBrowser() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const pageSize = 24;
  const query = new URLSearchParams(params);
  query.set("limit", String(pageSize));
  const episodes = useQuery({
    queryKey: ["episode-catalog", query.toString()],
    queryFn: () => api.episodeCatalog(query.toString()),
    refetchInterval: 30_000,
  });
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  useEffect(() => setSearch(params.get("q") ?? ""), [params]);
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    value ? next.set(key, value) : next.delete(key);
    if (key !== "offset") next.delete("offset");
    setParams(next);
  };
  const offset = Math.max(0, Number(params.get("offset") || 0));
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    update("q", search.trim());
  };
  return (
    <>
      <section className="panel dataset-browser">
        <form className="dataset-filters" onSubmit={submit}>
          <label className="dataset-search">
            <span>搜索任务、Episode 或机器人</span>
            <div className="search">
              <Search size={16} />
              <input
                aria-label="搜索数据记录"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="输入关键词"
              />
            </div>
          </label>
          <label>
            项目
            <select
              aria-label="按项目筛选"
              value={params.get("project_id") ?? ""}
              onChange={(event) => update("project_id", event.target.value)}
            >
              <option value="">全部项目</option>
              {projects.data?.map((project) => (
                <option value={project.id} key={project.id}>{project.name}</option>
              ))}
            </select>
          </label>
          <label>
            Episode 结果
            <select
              aria-label="按 Episode 结果筛选"
              value={params.get("success") ?? ""}
              onChange={(event) => update("success", event.target.value)}
            >
              <option value="">全部结果</option>
              <option value="passed">原始记录为成功</option>
              <option value="failed">原始记录为失败</option>
              <option value="unknown">未记录结果</option>
            </select>
          </label>
          <label>
            元信息
            <select
              aria-label="按元信息完整度筛选"
              value={params.get("metadata") ?? ""}
              onChange={(event) => update("metadata", event.target.value)}
            >
              <option value="">全部元信息</option>
              <option value="complete">字段完整</option>
              <option value="incomplete">缺失或异常</option>
            </select>
          </label>
          <div className="actions">
            <button className="button primary" type="submit">筛选</button>
            <button
              className="button"
              type="button"
              onClick={() => {
                setSearch("");
                setParams({});
              }}
            >
              清除
            </button>
          </div>
        </form>
        <div className="dataset-summary">
          <strong>{episodes.data?.total ?? "—"} 条原生 Episode 记录</strong>
          <span>按原始元信息筛选；不代表通过物理验收或具备训练集发布资格。</span>
        </div>
      </section>
      <ErrorNotice error={episodes.error ?? projects.error} />
      {episodes.isLoading ? (
        <Loading />
      ) : episodes.data?.items.length ? (
        <EpisodeCards items={episodes.data.items} />
      ) : (
        <Empty
          title={episodes.data?.total ? "当前页没有记录" : "没有匹配的原生 Episode"}
          detail="可调整关键词或筛选条件；未生成的 Episode 不会由轨迹或视频推断补造。"
        />
      )}
      {episodes.data && episodes.data.total > pageSize && (
        <div className="pagination">
          <button
            className="button"
            disabled={offset === 0}
            onClick={() => update("offset", String(Math.max(0, offset - pageSize)))}
          >
            上一页
          </button>
          <span>{offset + 1}–{Math.min(offset + pageSize, episodes.data.total)} / {episodes.data.total}</span>
          <button
            className="button"
            disabled={offset + pageSize >= episodes.data.total}
            onClick={() => update("offset", String(offset + pageSize))}
          >
            下一页
          </button>
        </div>
      )}
    </>
  );
}

export function LibraryPage({ mode }: { mode: "scenes" | "episodes" }) {
  const [params, setParams] = useSearchParams();
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: api.capabilities,
  });
  const editorCapability = capabilities.data?.find(
    (c) => c.id === "scene_editor",
  );
  const runs = useQuery({
    queryKey: ["runs", "limit=200"],
    queryFn: () => api.runs("limit=200"),
    enabled: mode === "scenes",
  });
  const selected =
    params.get("run") ??
    localStorage.getItem("lastRun") ??
    runs.data?.items[0]?.id;
  return (
    <main className="page">
      <div className="page-title">
        <div>
          <p className="eyebrow">
            {mode === "scenes" ? "SCENES / ASSETS" : "DATA / REPLAY"}
          </p>
          <h1>{mode === "scenes" ? "场景与资产" : "数据与回放"}</h1>
          <p className="muted">
            {mode === "scenes"
              ? "从同一份运行记录，追溯场景、资产和验收依据。"
              : "跨项目查看原生 Episode 记录，并回到来源运行检查上下文。"}
          </p>
        </div>
      </div>
      {mode === "scenes" && <section className="scene-reconstruction-entry">
        <div><p className="eyebrow">场景重建</p><h2>背景与操作对象，各自成为可复用资产</h2>
          <p className="muted">先分离扫描中的机器人、手与可移动对象，再补全背景；对象独立重建后与场景组合。</p></div>
        <Link className="button" to="/scenes/separation">背景与对象分离 <ArrowRight size={14} /></Link>
      </section>}
      {mode === "scenes" && editorCapability && (
        <SceneEditor
          available={editorCapability.available}
          reason={editorCapability.reason}
        />
      )}
      {mode === "episodes" ? (
        <DatasetBrowser />
      ) : (
        <>
      <div className="context-bar">
        <label>
          关联运行{" "}
          <select
            aria-label="关联运行"
            value={selected || ""}
            onChange={(e) => {
              const next = new URLSearchParams(params);
              next.set("run", e.target.value);
              setParams(next);
            }}
          >
            {runs.data?.items.map((run) => (
              <option value={run.id} key={run.id}>
                {run.name}
              </option>
            ))}
          </select>
        </label>
        {selected && (
          <Link
            className="link-button"
            to={`/runs/${selected}?view=${mode === "scenes" ? "scene" : "trajectory"}`}
          >
            返回运行工作台 ↗
          </Link>
        )}
      </div>
      <ErrorNotice error={runs.error} />
      {runs.isLoading ? (
        <Loading />
      ) : (
        selected && (
          <section className="panel library-panel">
            {mode === "scenes" ? (
              <>
                <div className="notice">
                  下方展示关联运行的原生场景文件。上方布局编辑适用于已配置的
                  scene JSON；几何文件存在不代表物理验收通过。
                </div>
                <ArtifactList runId={selected} key={selected} kind="model" />
              </>
            ) : null}
          </section>
        )
      )}
        </>
      )}
    </main>
  );
}

export function ToolsPage() {
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<Tool | null>(null);
  const query = new URLSearchParams(params);
  query.set("limit", "60");
  const tools = useQuery({
    queryKey: ["tools", query.toString()],
    queryFn: () => api.tools(query.toString()),
  });
  const update = (key: string, value: string) => {
    const n = new URLSearchParams(params);
    value ? n.set(key, value) : n.delete(key);
    n.delete("offset");
    setParams(n);
  };
  const offset = Number(params.get("offset") || 0);
  return (
    <main className="page">
      <div className="page-title">
        <div>
          <p className="eyebrow">CAPABILITIES / TOOL LIBRARY</p>
          <h1>技能与验证器</h1>
          <p className="muted">
            来自当前工作树的工具声明，状态表示工具作者的声明。
          </p>
        </div>
        <span className="source-tag">本机索引</span>
      </div>
      <div className="list-toolbar">
        <div className="segments">
          {[
            ["", "全部"],
            ["skill", "技能"],
            ["verifier", "验证器"],
          ].map(([value, title]) => (
            <button
              key={value}
              className={
                (params.get("tool_type") || "") === value ? "selected" : ""
              }
              onClick={() => update("tool_type", value)}
            >
              {title}
            </button>
          ))}
        </div>
        <label className="search">
          <Search size={16} />
          <input
            aria-label="搜索工具"
            placeholder="搜索工具名称、分类或描述…"
            value={params.get("q") || ""}
            onChange={(e) => update("q", e.target.value)}
          />
        </label>
      </div>
      <ErrorNotice error={tools.error} />
      {tools.isLoading ? (
        <Loading />
      ) : tools.data?.total === 0 ? (
        <Empty title="没有匹配的工具" />
      ) : (
        <div className="tool-grid">
          {tools.data?.items.map((tool) => (
            <button
              className="tool-card"
              key={`${tool.type}:${tool.name}`}
              onClick={() => setSelected(tool)}
            >
              <div className="section-heading">
                <span className={`tool-type ${tool.type}`}>
                  <Wrench size={13} />
                  {tool.type === "skill" ? "SKILL" : "VERIFIER"}
                </span>
                <span className="small muted">{tool.category}</span>
              </div>
              <h3>{tool.title || tool.name}</h3>
              <div className="mono small muted">{tool.name}</div>
              <p>{tool.summary}</p>
              <span className="small muted">声明状态 · {tool.status}</span>
            </button>
          ))}
        </div>
      )}
      {tools.data && tools.data.total > 60 && (
        <div className="pagination">
          <button
            className="button"
            disabled={!offset}
            onClick={() => {
              const n = new URLSearchParams(params);
              n.set("offset", String(offset - 60));
              setParams(n);
            }}
          >
            上一页
          </button>
          <span>
            {offset + 1}–{Math.min(offset + 60, tools.data.total)} /{" "}
            {tools.data.total}
          </span>
          <button
            className="button"
            disabled={offset + 60 >= tools.data.total}
            onClick={() => {
              const n = new URLSearchParams(params);
              n.set("offset", String(offset + 60));
              setParams(n);
            }}
          >
            下一页
          </button>
        </div>
      )}
      {selected && (
        <ToolDetails tool={selected} close={() => setSelected(null)} />
      )}
    </main>
  );
}

function ToolDetails({ tool, close }: { tool: Tool; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="tool-drawer"
      aria-label="工具详情"
      onCancel={close}
      onClick={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <button className="button" onClick={close}>
        关闭
      </button>
      <p className="eyebrow">{tool.type}</p>
      <h2>{tool.title || tool.name}</h2>
      <p className="mono small muted">{tool.name}</p>
      <p>{tool.description || tool.summary}</p>
      <h3>输入</h3>
      <pre>{pretty(tool.inputs)}</pre>
      <h3>输出</h3>
      <pre>{pretty(tool.outputs)}</pre>
      <div className="notice">
        工具声明仅供浏览。本界面不会运行任意 Python 或修改 pipeline。
      </div>
    </dialog>
  );
}

export function EnvironmentPage() {
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: api.capabilities,
  });
  return (
    <main className="page">
      <div className="page-title">
        <div>
          <p className="eyebrow">ENVIRONMENT / CAPABILITIES</p>
          <h1>环境与能力</h1>
          <p className="muted">
            区分工作台已接入能力、已有源码和待验证的集成。
          </p>
        </div>
      </div>
      <SimulationRuntimePanel overview />
      <ErrorNotice error={capabilities.error} />
      {capabilities.isLoading ? (
        <Loading />
      ) : (
        <div className="capability-grid">
          {capabilities.data?.map((capability) => (
            <article className="panel capability-card" key={capability.id}>
              <div className="section-heading">
                <Box size={20} />
                <span className={capability.available ? "green" : "muted"}>
                  {capability.available ? <Check size={18} /> : "待接入"}
                </span>
              </div>
              <h2>{capability.title}</h2>
              <p>{capability.reason}</p>
              <span className="mono small muted">{capability.level}</span>
            </article>
          ))}
        </div>
      )}
    </main>
  );
}
