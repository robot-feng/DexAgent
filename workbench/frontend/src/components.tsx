import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Download,
  FileText,
  LoaderCircle,
  TriangleAlert,
  X,
} from "lucide-react";
import { api, contentUrl } from "./api";
import type { Artifact, Report } from "./api";

const labels: Record<string, string> = {
  passed: "通过",
  warning: "有警告",
  failed: "失败",
  incomplete: "证据不完整",
  not_evaluated: "未评估",
  completed: "已完成",
  running: "运行中",
  unknown: "存活状态未知",
  blocked: "受阻",
  interrupted: "已中断",
  cancelled: "已取消",
  skipped: "已跳过",
  waiting_agent: "等待 Agent",
  ready: "具备发布资格",
  reused: "复用结果",
};

export const label = (value: string | null | undefined) =>
  value ? (labels[value] ?? value) : "未知";
export const formatBytes = (size: number) =>
  size >= 1024 ** 3
    ? `${(size / 1024 ** 3).toFixed(1)} GB`
    : size >= 1024 ** 2
      ? `${(size / 1024 ** 2).toFixed(1)} MB`
      : `${Math.max(1, Math.round(size / 1024))} KB`;
export const formatTime = (date: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(date));
export const pretty = (value: unknown) =>
  typeof value === "string" ? value : (JSON.stringify(value, null, 2) ?? "—");

export function Badge({ value }: { value: string | null | undefined }) {
  return (
    <span className={`badge ${value ?? "unknown"}`}>
      <i />
      {label(value)}
    </span>
  );
}

export function Loading() {
  return (
    <div className="empty" role="status">
      <LoaderCircle className="spin" size={22} />
      <p>正在读取本机数据…</p>
    </div>
  );
}

export function ErrorNotice({ error }: { error: Error | null }) {
  return error ? (
    <div className="notice danger" role="alert">
      <TriangleAlert size={18} />
      {error.message}
    </div>
  ) : null;
}

export function Empty({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="empty">
      <FileText size={27} />
      <h3>{title}</h3>
      {detail && <p>{detail}</p>}
    </div>
  );
}

export function Media({ artifact }: { artifact: Artifact }) {
  const query = useQuery({
    queryKey: ["text", artifact.id],
    queryFn: () => api.text(artifact.id),
    enabled:
      artifact.kind === "text" || /\.(xml|urdf|usda)$/.test(artifact.name),
  });
  if (artifact.kind === "video")
    return <VideoPlayer key={artifact.id} artifact={artifact} />;
  if (artifact.kind === "image")
    return <img src={contentUrl(artifact)} alt={artifact.name} />;
  if (query.error) return <ErrorNotice error={query.error} />;
  if (query.isLoading) return <Loading />;
  if (query.data)
    return (
      <>
        <pre className="text-preview">{query.data.text}</pre>
        {!query.data.eof && (
          <p className="muted">预览前 64 KiB，下载可查看完整内容。</p>
        )}
      </>
    );
  return (
    <Empty
      title={artifact.privileged ? "仿真真值 · 仅供诊断" : "原生文件"}
      detail={
        artifact.kind === "model"
          ? "支持的记录式 3D 可在运行“场景”页打开。此处保留原生文件，可下载并查看关联验收报告。"
          : "保留原始格式，可下载后使用对应工具读取。"
      }
    />
  );
}

function VideoPlayer({ artifact }: { artifact: Artifact }) {
  const [failed, setFailed] = useState(false);
  if (failed)
    return (
      <div className="empty" role="alert">
        <TriangleAlert size={24} />
        <p>视频加载或解码失败</p>
        <a className="button" href={contentUrl(artifact, true)}>
          下载原视频
        </a>
      </div>
    );
  return (
    <video
      controls
      preload="metadata"
      src={contentUrl(artifact)}
      onLoadedMetadata={(event) => {
        const saved = Number(sessionStorage.getItem(`playback:${artifact.id}`));
        if (saved > 0 && saved < event.currentTarget.duration)
          event.currentTarget.currentTime = saved;
      }}
      onTimeUpdate={(event) =>
        sessionStorage.setItem(
          `playback:${artifact.id}`,
          String(event.currentTarget.currentTime),
        )
      }
      onError={() => setFailed(true)}
      aria-label={artifact.name}
    />
  );
}

export function ArtifactDialog({
  artifact,
  close,
}: {
  artifact: Artifact;
  close: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="artifact-dialog"
      onCancel={close}
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div className="dialog-head">
        <div>
          <h3>{artifact.name}</h3>
          <p className="mono muted">{artifact.path}</p>
        </div>
        <a className="button" href={contentUrl(artifact, true)}>
          <Download size={15} />
          下载
        </a>
        <button className="icon-button" aria-label="关闭预览" onClick={close}>
          <X size={19} />
        </button>
      </div>
      <div className="dialog-content">
        <Media artifact={artifact} />
      </div>
    </dialog>
  );
}

export function GateReport({
  report,
  open,
}: {
  report: Report;
  open: (a: Artifact) => void;
}) {
  const sorted = [...report.checks].sort((a, b) => {
    const score = (c: typeof a) =>
      c.status === "failed"
        ? 0
        : c.status === "warning"
          ? 1
          : c.status === "passed"
            ? 3
            : 2;
    return score(a) - score(b);
  });
  return (
    <section className="report-view">
      <div className="section-heading">
        <div>
          <h2>检查与证据</h2>
          <p className="muted">保留原生检查结论与阈值</p>
        </div>
        <Badge value={report.validation} />
      </div>
      {report.evaluation_scope === "embodiment_import_qualification" ? (
        <div className="notice">
          本体导入检查：此报告仅用于检查模型接入，不能作为三本体 demo 通过证据。
          {report.task_success_evaluated === false && " 任务成功未评估。"}
        </div>
      ) : (
        (report.evaluation_scope ||
          report.task_success_evaluated === false) && (
          <div className="notice">
            {report.evaluation_scope && (
              <span>检查范围：{report.evaluation_scope}。</span>
            )}
            {report.task_success_evaluated === false && (
              <span> 任务成功未评估；检查通过不代表任务完成。</span>
            )}
          </div>
        )
      )}
      {report.failure && <div className="notice danger">{report.failure}</div>}
      {report.next_action && (
        <div className="notice">下一步：{report.next_action}</div>
      )}
      <div className="check-summary">
        <span>{report.checks.filter((c) => c.required).length} 项必需检查</span>
        <span>
          {report.checks.filter((c) => c.status === "passed").length} 项通过
        </span>
        <button className="link-button" onClick={() => open(report.artifact)}>
          打开原始报告 ↗
        </button>
      </div>
      {sorted.length === 0 && (
        <Empty
          title="没有逐项检查记录"
          detail="不会根据文件存在或进程退出推断验收通过。"
        />
      )}
      {sorted.map((check, i) => (
        <details className={`check ${check.status}`} key={`${check.id}:${i}`}>
          <summary>
            <Badge value={check.status} />
            <span className="mono">{check.id}</span>
            <span className="muted">{check.required ? "必需" : "可选"}</span>
          </summary>
          <div className="check-details">
            {(["observed", "threshold", "evidence"] as const).map(
              (key) =>
                check[key] != null && (
                  <div key={key}>
                    <h4>
                      {
                        {
                          observed: "实际结果",
                          threshold: "阈值",
                          evidence: "证据",
                        }[key]
                      }
                    </h4>
                    <pre>{pretty(check[key])}</pre>
                  </div>
                ),
            )}
          </div>
        </details>
      ))}
    </section>
  );
}
