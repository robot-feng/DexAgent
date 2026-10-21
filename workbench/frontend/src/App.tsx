import { Fragment } from "react";
import {
  NavLink,
  Outlet,
  Route,
  createBrowserRouter,
  createRoutesFromElements,
  useLocation,
} from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Box,
  Boxes,
  ChevronRight,
  Database,
  FolderOpen,
  LayoutDashboard,
  Layers3,
  RefreshCw,
  Settings2,
  Wrench,
  PlayCircle,
} from "lucide-react";
import { api } from "./api";
import { ErrorNotice } from "./components";
import { EnvironmentPage, LibraryPage, ProjectsPage, ToolsPage } from "./pages";
import { BackgroundCleanupPage } from "./BackgroundCleanupPage";
import { OverviewPage } from "./OverviewPage";
import { RunWorkbench } from "./RunWorkbench";
import { ExecutionsPage } from "./ExecutionsPage";

function Shell() {
  const client = useQueryClient();
  const location = useLocation();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 15_000,
  });
  const refresh = useMutation({
    mutationFn: api.refresh,
    onSuccess: () => client.invalidateQueries(),
  });
  const lastRun =
    location.pathname.match(/^\/runs\/([^/]+)/)?.[1] ||
    localStorage.getItem("lastRun");
  const navigation = [
    { path: "/", title: "总览", icon: LayoutDashboard },
    { path: "/projects", title: "项目", icon: FolderOpen },
    { path: "/executions", title: "任务执行", icon: PlayCircle },
    ...(lastRun
      ? [{ path: `/runs/${lastRun}`, title: "工作台", icon: Layers3 }]
      : []),
    { path: "/scenes", title: "场景与资产", icon: Boxes },
    { path: "/datasets", title: "数据与回放", icon: Database },
    { path: "/tools", title: "工具库", icon: Wrench },
    { path: "/settings/environment", title: "环境与能力", icon: Settings2 },
  ];
  return (
    <div className="app-shell">
      <aside className="global-sidebar">
        <NavLink to="/" className="brand" aria-label="EmbodiForge Studio 总览">
          <span className="brand-mark">
            <Box size={25} />
          </span>
          <span>
            <strong>EMBODIFORGE</strong>
            <small>STUDIO</small>
          </span>
        </NavLink>

        <nav aria-label="主导航">
          {navigation.map(({ path, title, icon: Icon }) => (
            <Fragment key={title}>
              {["总览", "场景与资产", "工具库"].includes(title) && (
                <span className="nav-group-label">
                  {title === "总览"
                    ? "工作空间"
                    : title === "场景与资产"
                      ? "资产与数据"
                      : "系统"}
                </span>
              )}
              <NavLink
                to={path}
                aria-label={title}
                title={title}
                className={({ isActive }) =>
                  `nav-item ${(isActive || (path === "/scenes" && location.pathname === "/background-cleanup")) && (title !== "工作台" || location.pathname.startsWith("/runs/")) ? "active" : ""}`
                }
              >
                <Icon size={18} />
                <span>{title}</span>
                {title === "工作台" && <ChevronRight size={13} />}
              </NavLink>
            </Fragment>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span className="connection-dot" />
          <span>本机产物 · 原生格式</span>
          <p>理解 · 重建 · 仿真 · 验收</p>
        </div>
      </aside>
      <div className="app-body">
        <header className="topbar">
          <div className="breadcrumb">
            <span>工作空间</span>
            <ChevronRight size={13} />
            <span>
              {location.pathname.startsWith("/runs/")
                ? "运行工作台"
                : location.pathname === "/scenes/separation" || location.pathname === "/background-cleanup"
                  ? "场景重建 / 背景与对象分离"
                : (navigation.find((n) => n.path === location.pathname)
                    ?.title ?? "项目")}
            </span>
          </div>
          <div className="topbar-right">
            <span
              className={`connection ${health.error || health.data?.index_error ? "bad" : ""}`}
            >
              <Activity size={13} />
              {health.isLoading
                ? "连接中"
                : health.error
                  ? "连接失败"
                  : `${health.data?.run_count ?? 0} 个已索引运行`}
            </span>
            <button
              className="icon-button"
              title="刷新本机索引"
              aria-label="刷新本机索引"
              onClick={() => refresh.mutate()}
              disabled={refresh.isPending}
            >
              <RefreshCw
                size={16}
                className={refresh.isPending ? "spin" : ""}
              />
            </button>
            <span className="local-label">本地工作空间</span>
          </div>
        </header>
        <ErrorNotice error={refresh.error ?? health.error} />
        {health.data?.index_error && (
          <div className="notice danger" role="alert">
            索引更新失败：{health.data.index_error}
          </div>
        )}
        <Outlet />
      </div>
    </div>
  );
}

export const router = createBrowserRouter(
  createRoutesFromElements(
    <Route element={<Shell />}>
      <Route path="/" element={<OverviewPage />} />
      <Route path="/projects" element={<ProjectsPage />} />
      <Route path="/executions" element={<ExecutionsPage />} />
      <Route path="/runs/:runId" element={<RunWorkbench />} />
      <Route path="/scenes" element={<LibraryPage mode="scenes" />} />
      <Route path="/datasets" element={<LibraryPage mode="episodes" />} />
      <Route path="/scenes/separation" element={<BackgroundCleanupPage />} />
      <Route path="/background-cleanup" element={<BackgroundCleanupPage />} />
      <Route path="/tools" element={<ToolsPage />} />
      <Route path="/settings/environment" element={<EnvironmentPage />} />
      <Route
        path="*"
        element={
          <div className="page">
            <h1>页面不存在</h1>
            <NavLink to="/projects">返回项目</NavLink>
          </div>
        }
      />
    </Route>,
  ),
);
