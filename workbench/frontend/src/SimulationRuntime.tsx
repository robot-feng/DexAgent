import { useQuery } from "@tanstack/react-query";
import { ArrowRight, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { api, type Execution, type SimulationRuntime } from "./api";
import { ErrorNotice, formatTime } from "./components";

const statusLabels: Record<SimulationRuntime["status"], string> = {
  environment_detected: "环境已检测",
  missing_dependencies: "缺少依赖",
  incompatible: "版本不兼容",
  error: "检测失败",
};

export function SimulationRuntimePanel({
  configId,
  mode,
  overview = false,
}: {
  configId?: string;
  mode?: Execution["mode"];
  overview?: boolean;
}) {
  const runtime = useQuery({
    queryKey: ["simulation-runtime", configId ?? "default"],
    queryFn: () => api.simulationRuntime(configId),
    enabled: overview || !!configId,
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const data = runtime.data;
  const profile = data?.profiles.find((item) => item.mode === mode);
  const multiStage = mode === "pipeline" || mode === "replan";

  return (
    <section
      className={`simulation-runtime${overview ? " panel" : ""}`}
      aria-label="仿真运行环境"
    >
      <div className="simulation-runtime-heading">
        <h2>仿真运行环境</h2>
        {(overview || configId) && (
          <button
            className="link-button"
            type="button"
            disabled={runtime.isFetching}
            onClick={() => void runtime.refetch()}
            aria-label="重新检测仿真运行环境"
          >
            <RefreshCw size={14} />
            {runtime.isFetching ? "检测中…" : "重新检测"}
          </button>
        )}
      </div>
      {!overview && !configId ? (
        <p className="muted">选择运行配置后检测其仿真环境。</p>
      ) : runtime.isPending ? (
        <p className="muted" role="status">正在检测配置对应的仿真环境…</p>
      ) : runtime.isError ? (
        <ErrorNotice error={runtime.error} />
      ) : data ? (
        <>
          <div className="simulation-runtime-summary">
            <strong>{data.engine} · {data.integration}</strong>
            <span
              className={`badge ${data.status === "environment_detected" ? "info" : "warning"}`}
            >
              {statusLabels[data.status]}
            </span>
            {data.config_name && <span className="muted">{data.config_name}</span>}
          </div>
          <p className="small muted">{data.detail}</p>
          {mode && (
            <div className="simulation-runtime-profile" aria-label="所选任务仿真路径">
              {multiStage ? (
                <p>
                  多阶段流程：各阶段使用各自配置的执行后端，以下 RSS
                  环境信息不代表整个流程采用同一个求解器。
                </p>
              ) : profile ? (
                <>
                  <strong>{profile.solver}</strong>
                  <span className="simulation-runtime-device">
                    物理计算：{["cpu", "gpu"].includes(profile.physics_device)
                      ? profile.physics_device.toUpperCase() : profile.physics_device}
                  </span>
                  <p className="small muted">{profile.detail}</p>
                </>
              ) : (
                <p className="muted">当前模式没有可用的求解器配置说明。</p>
              )}
            </div>
          )}
          <details className="simulation-runtime-details">
            <summary>环境版本与检测时间</summary>
            <dl>
              <dt>Python</dt><dd>{data.python ?? "未配置"}</dd>
              <dt>检测时间</dt><dd>{formatTime(data.checked_at)}</dd>
              {data.packages.map((item) => (
                <div className="simulation-runtime-package" key={item.name}>
                  <dt>{item.name}</dt>
                  <dd>{item.available ? (item.version ?? "已检测，版本未知") : "未检测到"}</dd>
                </div>
              ))}
            </dl>
          </details>
          <p className="small muted simulation-runtime-boundary">
            环境检测不等于任务通过；物理执行结果以每次运行的验收报告为准。
          </p>
        </>
      ) : null}
      {overview && (
        <Link className="link-button" to="/executions?mode=rigid">
          运行刚体基础验收 <ArrowRight size={14} />
        </Link>
      )}
    </section>
  );
}
