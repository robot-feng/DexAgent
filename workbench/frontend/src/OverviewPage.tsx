import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  Boxes,
  CircleCheck,
  Database,
  ExternalLink,
  FolderOpen,
  PlayCircle,
  Radio,
  Settings2,
  WifiOff,
  Wrench,
} from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "./api";
import { Badge, ErrorNotice, Loading, formatTime } from "./components";
import { SimulationRuntimePanel } from "./SimulationRuntime";

const destinations = [
  {
    to: "/projects",
    title: "项目与运行",
    description: "查看原始输入、各阶段报告、失败原因和验收证据。",
    icon: FolderOpen,
    query: "runs",
  },
  {
    to: "/scenes",
    title: "场景与资产",
    description: "检查原生场景，编辑独立副本并追踪候选版本。",
    icon: Boxes,
    query: "scenes",
  },
  {
    to: "/datasets",
    title: "数据与回放",
    description: "浏览已有 Episode、视频、轨迹曲线和 3D 记录。",
    icon: Database,
    query: "episodes",
  },
  {
    to: "/tools",
    title: "工具库",
    description: "查找当前工作树已有工具及其输入、输出说明。",
    icon: Wrench,
    query: "tools",
  },
];

const capabilityLabels: Record<string, string> = {
  reports: "运行报告",
  media: "视频与产物",
  episodes: "原生数据记录",
  recorded_3d: "记录式 3D 回放",
  studio_feedback: "RSS 实时状态反馈",
  studio: "实时物理会话",
  scene_editor: "场景副本编辑",
  execution: "任务执行",
  training_export: "训练格式导出",
};

const capabilityStatus = (level: string, available: boolean) => {
  if (level === "configured") return "已配置";
  if (level === "integrated" && available) return "已接入";
  if (available) return level === "implemented" ? "已实现" : "环境可用";
  if (level === "not_verified") return "未验收";
  return level === "out_of_scope" ? "未接入此工作台" : "尚未接入";
};

const nativeViewerUrl = (value: string | null) => {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (!["localhost", "127.0.0.1", "0.0.0.0", "::1"].includes(url.hostname)) return null;
    url.hostname = window.location.hostname;
    return url.toString();
  } catch {
    return null;
  }
};

export function OverviewPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const scenes = useQuery({ queryKey: ["scenes"], queryFn: api.scenes });
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: api.capabilities,
  });
  const recent = useQuery({
    queryKey: ["overview-runs"],
    queryFn: () => api.runs("limit=5&offset=0"),
  });
  const editors = useQuery({ queryKey: ["editors"], queryFn: api.editors });
  const studioLive = useQuery({
    queryKey: ["studio-live"],
    queryFn: api.studioLive,
    refetchInterval: 2000,
    retry: false,
  });
  const activeEditor = editors.data?.find((editor) =>
    ["starting", "ready", "unresponsive"].includes(editor.status),
  );
  const activeScene = scenes.data?.items.find(
    (scene) => scene.id === activeEditor?.scene_id,
  );
  const editorHref = activeEditor
    ? `/scenes?${new URLSearchParams({
        scene: activeEditor.scene_id,
        editor: activeEditor.id,
      }).toString()}`
    : undefined;
  const studioViewerUrl = nativeViewerUrl(studioLive.data?.url ?? null);
  const availableCapabilities = capabilities.data?.filter(
    (item) => item.available,
  ).length;

  return (
    <main className="page overview-page">
      <div className="page-title">
        <div>
          <p className="eyebrow">EMBODIFORGE STUDIO</p>
          <h1>工作空间总览</h1>
          <p className="muted">
            启动任务、检查场景、追踪执行与验收，浏览生成的数据。
          </p>
        </div>
        <Link className="button primary" to="/executions">
          <PlayCircle size={16} />
          新建运行
        </Link>
      </div>

      <ErrorNotice error={health.error ?? scenes.error ?? editors.error} />
      {health.data?.index_error && (
        <div className="notice danger" role="alert">
          运行索引暂不可用：{health.data.index_error}
        </div>
      )}

      <section className="overview-stats" aria-label="工作空间状态">
        <div className="overview-stat">
          <FolderOpen size={18} />
          <span>已索引运行</span>
          <strong>{health.data?.run_count ?? "—"}</strong>
        </div>
        <div className="overview-stat">
          <Boxes size={18} />
          <span>可查看场景</span>
          <strong>{scenes.data?.items.length ?? "—"}</strong>
        </div>
        <div className="overview-stat">
          <Settings2 size={18} />
          <span>当前可用能力</span>
          <strong>
            {availableCapabilities === undefined
              ? "—"
              : `${availableCapabilities}/${capabilities.data?.length ?? 0}`}
          </strong>
        </div>
      </section>

      <SimulationRuntimePanel overview />

      <section className="overview-section">
        <div className="section-heading">
          <div>
            <h2>现有工作入口</h2>
            <p className="muted">直接进入已有功能，不重复创建另一套流程。</p>
          </div>
        </div>
        <div className="overview-destinations">
          {destinations.map(({ to, title, description, icon: Icon, query }) => {
            const count =
              query === "runs"
                ? health.data?.run_count
                : query === "scenes"
                  ? scenes.data?.items.length
                  : undefined;
            return (
              <Link className="overview-destination" key={to} to={to}>
                <span className="overview-icon">
                  <Icon size={18} />
                </span>
                <span className="overview-destination-copy">
                  <strong>{title}</strong>
                  <small>{description}</small>
                </span>
                {count !== undefined && <span className="counter">{count}</span>}
                <ArrowRight size={16} />
              </Link>
            );
          })}
        </div>
      </section>

      <div className="overview-lower">
        <section className="panel overview-recent">
          <div className="section-heading">
            <div>
              <h2>最近运行</h2>
              <p className="muted">按本机原始报告更新时间排序</p>
            </div>
            <Link className="link-button" to="/projects">
              全部运行 <ArrowRight size={14} />
            </Link>
          </div>
          {activeEditor && editorHref && (
            <Link
              className="overview-destination overview-editor-resume"
              to={editorHref}
            >
              <span className="overview-icon">
                <Boxes size={18} />
              </span>
              <span className="overview-destination-copy">
                <strong>
                  继续场景编辑 · {activeScene?.name ?? "场景副本"}
                </strong>
                <small>
                  {activeEditor.status === "starting"
                    ? "编辑器正在启动"
                    : activeEditor.status === "unresponsive"
                      ? "编辑器暂未响应，打开后可继续检查"
                      : "已有未关闭的场景编辑会话"}
                </small>
              </span>
              <ArrowRight size={16} />
            </Link>
          )}
          {recent.isLoading ? (
            <Loading />
          ) : recent.error ? (
            <ErrorNotice error={recent.error} />
          ) : recent.data?.items.length ? (
            <div className="overview-run-list">
              {recent.data.items.map((run) => (
                <Link className="overview-run" key={run.id} to={`/runs/${run.id}`}>
                  <span className="overview-run-name">
                    <strong>{run.name}</strong>
                    <small>
                      {formatTime(run.updated_at)} · {run.stage_count} 个阶段
                    </small>
                  </span>
                  <Badge value={run.validation} />
                  <ArrowRight size={15} />
                </Link>
              ))}
            </div>
          ) : (
            <div className="overview-empty">
              <CircleCheck size={18} />
              <span>尚无已索引运行；可先检查配置的产物目录。</span>
            </div>
          )}
        </section>

        <section className="panel overview-capabilities">
          <div className="section-heading">
            <div>
              <h2>能力状态</h2>
              <p className="muted">按当前服务检测结果显示</p>
            </div>
            <Link
              className="icon-button"
              aria-label="打开环境与能力"
              to="/settings/environment"
            >
              <Settings2 size={16} />
            </Link>
          </div>
          {capabilities.isLoading ? (
            <Loading />
          ) : capabilities.error ? (
            <ErrorNotice error={capabilities.error} />
          ) : (
            <ul className="capability-list">
              {capabilities.data?.map((capability) => (
                <li key={capability.id}>
                  <span
                    className={
                      capability.available
                        ? "capability-dot ready"
                        : "capability-dot pending"
                    }
                  />
                  <span className="capability-copy">
                    <strong>{capabilityLabels[capability.id] ?? capability.title}</strong>
                    <small>{capabilityStatus(capability.level, capability.available)}</small>
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className="overview-boundary">
            任务执行已接入既有 CLI；实时物理交互和训练格式导出仍需单独验收。
          </p>
        </section>
      </div>

      <section className="panel studio-live-panel" aria-labelledby="studio-live-title">
        <div className="section-heading">
          <div>
            <h2 id="studio-live-title">RoboSimStudio 实时状态</h2>
            <p className="muted">直接读取仿真主线程状态，每 2 秒刷新；EmbodiForge Studio 不发送控制命令。</p>
          </div>
          <span className={`studio-live-badge ${studioLive.data?.connected ? "online" : "offline"}`}>
            {studioLive.data?.connected ? <Radio size={14} /> : <WifiOff size={14} />}
            {studioLive.data?.connected ? "已连接" : studioLive.data?.configured ? "未连接" : "未配置"}
          </span>
        </div>
        {studioLive.isLoading ? (
          <Loading />
        ) : studioLive.error ? (
          <ErrorNotice error={studioLive.error} />
        ) : studioLive.data?.connected ? (
          <>
            <div className="studio-live-metrics">
              <div><small>仿真步</small><strong>{studioLive.data.step ?? "—"}</strong></div>
              <div><small>仿真时间</small><strong>{studioLive.data.sim_time?.toFixed(3) ?? "—"} s</strong></div>
              <div><small>状态</small><strong>{studioLive.data.paused ? "已暂停" : "运行中"}</strong></div>
              <div><small>数值状态</small><strong>{studioLive.data.finite ? "有限" : "需检查"}</strong></div>
              <div><small>场景实体</small><strong>{studioLive.data.names.length}</strong></div>
            </div>
            {Object.keys(studioLive.data.positions).length > 0 && (
              <div className="studio-live-entities">
                {Object.entries(studioLive.data.positions).slice(0, 8).map(([name, position]) => (
                  <div className="studio-live-entity" key={name}>
                    <span>{name}</span>
                    <code>{Array.isArray(position) ? position.map((value) => value.toFixed(3)).join(", ") : position}</code>
                  </div>
                ))}
              </div>
            )}
            <div className="studio-live-footer">
              <span className="muted">最近读取 {formatTime(studioLive.data.checked_at)}</span>
              {studioViewerUrl && (
                <a className="link-button" href={studioViewerUrl} target="_blank" rel="noreferrer">
                  打开 RSS 原生 Viewer <ExternalLink size={14} />
                </a>
              )}
            </div>
          </>
        ) : (
          <div className="studio-live-empty">
            <p>{studioLive.data?.error ?? "当前没有已连接的 RSS live 会话。"}</p>
            <small>
              配置环境变量 <code>WORKBENCH_RSS_RUN_DIR</code>，指向运行 RSS live 服务时使用的 run 目录；连接后这里只读取状态，不会启动或驱动仿真。
            </small>
          </div>
        )}
      </section>
    </main>
  );
}
