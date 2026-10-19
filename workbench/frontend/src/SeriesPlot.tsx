import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { api } from "./api";
import type { Artifact } from "./api";
import { ErrorNotice, Loading } from "./components";

echarts.use([
  LineChart,
  DataZoomComponent,
  GridComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export default function SeriesPlot({ artifact }: { artifact: Artifact }) {
  const fields =
    artifact.name === "trajectory.npz"
      ? ["qpos", "qvel", "ctrl"]
      : ["joint_q", "joint_qd", "action", "reward"];
  const [field, setField] = useState(fields[0]);
  const [channel, setChannel] = useState(0);
  const container = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["series", artifact.id, field, channel],
    queryFn: () => api.series(artifact.id, field, channel),
  });
  useEffect(() => {
    if (!query.data || !container.current) return;
    const chart = echarts.init(container.current);
    const data = query.data;
    chart.setOption({
      backgroundColor: "transparent",
      animation: false,
      grid: { top: 30, left: 55, right: 28, bottom: 75 },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#17273b",
        borderColor: "#405876",
        textStyle: { color: "#c1d7f5" },
      },
      xAxis: {
        type: "value",
        name: "仿真时间 / s",
        nameLocation: "center",
        nameGap: 28,
        axisLabel: { color: "#91a9c7" },
        nameTextStyle: { color: "#91a9c7" },
        splitLine: { lineStyle: { color: "#23344a" } },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { color: "#91a9c7" },
        splitLine: { lineStyle: { color: "#23344a" } },
      },
      dataZoom: [
        { type: "inside" },
        {
          type: "slider",
          height: 14,
          bottom: 8,
          borderColor: "#304663",
          textStyle: { color: "#8ca6c8" },
        },
      ],
      series: [
        {
          name: `${data.field}[${data.channel}]`,
          type: "line",
          showSymbol: false,
          lineStyle: { color: "#80adff", width: 1.5 },
          data: data.times.map((time, i) => [time, data.values[i]]),
        },
      ],
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [query.data]);
  return (
    <section className="series-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">NATIVE CONTROL RECORDING</p>
          <h2>轨迹曲线</h2>
        </div>
        <div className="filters">
          <select
            aria-label="轨迹字段"
            value={field}
            onChange={(e) => {
              setField(e.target.value);
              setChannel(0);
            }}
          >
            {fields.map((value) => (
              <option
                value={value}
                key={value}
                disabled={
                  !!query.data && !query.data.available_fields.includes(value)
                }
              >
                {value}
              </option>
            ))}
          </select>
          <label className="small muted">
            通道{" "}
            <input
              type="number"
              aria-label="轨迹通道"
              value={channel}
              min={0}
              max={(query.data?.channels || 1) - 1}
              onChange={(e) => {
                const value = Number(e.target.value);
                if (Number.isInteger(value) && value >= 0) setChannel(value);
              }}
            />
          </label>
        </div>
      </div>
      <ErrorNotice error={query.error} />
      {query.isLoading && <Loading />}
      <div
        className="series-chart"
        ref={container}
        role="img"
        aria-label={`${field} 通道 ${channel} 的仿真时间曲线`}
      />
      {query.data && (
        <p className="small muted">
          {query.data.channel_name && (
            <span>当前通道：{query.data.channel_name} · </span>
          )}
          {query.data.value_unit} · 原始 {query.data.source_points} 点 / 显示{" "}
          {query.data.times.length} 点。
          均匀采样用于交互查看，完整验收以原生报告为准。
        </p>
      )}
    </section>
  );
}
