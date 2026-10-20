# 原生 Episode 记录包

数据与回放页及运行详情的 Episode 卡片增加“下载原生记录包”：展开后生成 ZIP，再下载。默认保留 `meta.json`、存在的 `steps.npz` 和 `cameras/*.mp4`。勾选“包含原始仿真状态与 capture 文件”后，另加入存在的 `privileged.npz`、`capture.npz`、`capture_manifest.json`、`contacts.jsonl`、`events.jsonl`、`spans.jsonl`。

这是原生记录快照，保留源文件字节、时钟、NaN padding 和原始结果；不转换成 LeRobot/RLDS，不认证任务成功或训练资格。即使不勾选，meta/steps 中仍可能存在仿真观测或对象真值，不能直接视为策略可用字段。场景和机器人资产需另行保留。

## 结构与接口

```text
manifest.json
README.txt
episodes/<原始 Episode 名>/meta.json
episodes/<原始 Episode 名>/steps.npz       # 存在时
episodes/<原始 Episode 名>/cameras/*.mp4   # 存在时
episodes/<原始 Episode 名>/...            # 可选原始状态文件
provenance/run_meta.json                  # 存在时，原始整次运行信息
provenance/episode_results.jsonl          # 存在时，原始整次运行结果
```

解压后使用 RoboSimStudio 原生 `EpisodeStore(<解压目录>/episodes)` 读取。整次运行的元信息和结果移到 `provenance/`，原字节不变，避免其中的总数被误认为单 Episode 子集总数。因此该子集的原生 `results()` 为空；原始结果可从 provenance 查看。

清单记录来源 run/Episode、每个源文件的 SHA-256 和大小、未纳入的条目，以及 `exported_episode_count=1`、`training_eligibility=not_evaluated`。未识别的诊断目录和非 MP4 相机条目列入遗漏清单，不递归打包任意文件。

```text
POST /api/workbench/v1/runs/{run_id}/episodes/{episode_id}/bundle
  {"include_simulator_state": false}
GET /api/workbench/v1/episode-bundles/{bundle_id}/download
```

生成返回下载地址、整个 ZIP 的 SHA-256、源文件数和遗漏条目；`file_count` 不含新增的 manifest/README。包保存在 `.workbench/episode_bundles/<SHA-256>.zip`，相同内容复用同一地址。没有新增队列或数据库模型，缓存目前需手动清理。

## 一致性与限制

- 已知活动执行、源报告仍为 running/waiting、其他打包正在进行时返回 409。源文件集合、大小、inode、时间等在生成中变化时拒绝该次快照并清理临时文件。
- 拒绝符号链接、跨运行文件、非普通文件和不可移植文件名；打开后核对文件身份，使用同一文件描述符校验和复制。
- JSON/JSONL 校验结构；NPZ 校验成员、NPY 1.0/2.0 头、dtype、声明字节数和 CRC，流式读取数组负载，不按不可信 shape 分配数组。允许原生 NaN padding、不同长度的 capture 和静态 anchor。
- 单包源文件最多 1,024 个、2 GiB；单个 NPZ 解压总量最多 512 MiB；JSON 或单行 JSONL 最多 32 MiB。超过限制或损坏输入返回明确错误。
- 历史记录缺少 steps 或可选侧文件仍可打包，不补造数据。成功解包不代表相机/action/observation 已对齐，也不代表仿真可独立重放。

## 验证与部署

2026-10-20 共享工作树回归为 **137 passed**；最终代码在独立已提交基线加本次文件的副本中为 **135 passed、2 skipped（31.67 秒）**。两项跳过为隔离副本缺少可选 Light Editor 环境，先前共享工作树已通过。Ruff、TypeScript/Vite 构建通过；隔离后端重新生成 OpenAPI 客户端与工作树逐字节一致。保留既有 SeriesPlot 大包提示。

新增后端测试覆盖字节保真、清单和 ZIP 哈希、活动执行、损坏输入、符号链接、并发变化、NPY 巨大 shape 声明及文件身份变化。Playwright 下载与规划诊断契约测试 **2 passed（4.4 秒）**，覆盖错误提示、请求选项、错误 Episode 绑定阻止、选项变化清除旧下载和手机布局。

真实记录分别打包普通包和含状态包，再解压并调用原生 EpisodeStore 回读：

| 原始运行 | 步数 | 普通包字节数 | 含状态包字节数 |
| --- | ---: | ---: | ---: |
| `rss_reference_cpu_final_20261016` | 3,925 | 1,053,906 | 12,117,413 |
| `studio_candidate_20261020_180423_121977` | 750 | 113,599 | 1,337,720 |

回读的 meta、steps 及选入的 privileged 数组与源记录一致，前后核对两次运行共 156 个源文件 SHA 不变。第二条仍为任务成功未评估。当前真实记录未发现原生 `episodes/*/cameras/*.mp4`；相机路径只做了测试样本字节保真验证，尚无真实相机视频随包验证。

此前阻止更新的清理任务 `0ff6b4934c4348a2868251aa` 已在分割阶段失败并结束，日志和中间产物保留。确认服务无活动执行、编辑会话和子进程后，前后端已一起更新到原 **8765**，未新增端口。实际浏览器直接访问在线页面（无 API 或静态资源替换），生成并下载候选记录的两种 ZIP；文件 SHA 与上述原生回读产物一致。桌面 1512×1100、手机 390×844 均无 page error、无横向溢出。

验证入口：`http://127.0.0.1:8765/datasets?run_id=28a4a0af940919d8bed182c0`。

本机证据位于 `.workbench/previews/episode-bundle-native-verification.json`、`episode-bundle-browser-verification.json`、`episode-bundles-{desktop,mobile}-20261020.png`；真实下载为同目录 `episode-bundle-browser-{base,raw}.zip`。本轮不修改 SimFoundry 重建、分割或任务规划算法。下一阶段仍需基于原生时序与字段契约完成训练格式转换及独立 loader 验收。
