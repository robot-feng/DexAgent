import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "./api";
import { ErrorNotice, formatBytes } from "./components";

export default function EpisodeDownload({
  runId,
  episodeId,
}: {
  runId: string;
  episodeId: string;
}) {
  const [includeSimulatorState, setIncludeSimulatorState] = useState(false);
  const create = useMutation({
    mutationFn: (include: boolean) =>
      api.createEpisodeBundle(runId, episodeId, include),
  });
  const result = create.data;
  const resultMatchesContext = Boolean(
    result &&
      result.run_id === runId &&
      result.episode_id === episodeId &&
      result.includes_simulator_state === includeSimulatorState,
  );

  return (
    <details>
      <summary>下载原生记录包</summary>
      <p className="small muted">
        ZIP 保留原生 meta、steps 和 cameras 记录；不转换为
        LeRobot/RLDS，也不代表训练数据通过验收。meta/steps
        本身可能包含仿真观测；原始仿真状态与 capture 文件可选加入。
      </p>
      <label
        className="episode-bundle-option"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          margin: "8px 0",
        }}
      >
        <input
          type="checkbox"
          style={{ width: 16, minHeight: 16, padding: 0, margin: 0 }}
          checked={includeSimulatorState}
          disabled={create.isPending}
          onChange={(event) => {
            setIncludeSimulatorState(event.target.checked);
            create.reset();
          }}
        />
        包含原始仿真状态与 capture 文件
      </label>
      <button
        className="button primary"
        disabled={create.isPending}
        onClick={() => {
          create.reset();
          create.mutate(includeSimulatorState);
        }}
      >
        {create.isPending ? "正在准备 ZIP…" : "生成 ZIP"}
      </button>
      <ErrorNotice error={create.error} />
      {result && !resultMatchesContext && (
        <p className="small amber" role="alert">
          服务返回的记录包与当前 Episode 或选项不匹配，已隐藏下载链接。
        </p>
      )}
      {result && resultMatchesContext && (
        <>
          <p className="small muted">
            {result.file_count} 个文件 · {formatBytes(result.size)}
          </p>
          <p className="small muted">
            包内清单记录来源、文件校验值和未纳入的文件；场景资产需单独保留。
          </p>
          <a className="button" href={result.url}>
            下载 ZIP
          </a>
        </>
      )}
    </details>
  );
}
