import { expect, test } from "@playwright/test";

const prefix = "/api/workbench/v1";
const configA = "a".repeat(24), configB = "b".repeat(24), configBad = "c".repeat(24);

test("resolved defaults, manual overrides and configuration errors govern pipeline launch", async ({ page }) => {
  let repaired = false;
  const submitted: Record<string, unknown>[] = [];
  await page.route(`**${prefix}/executions/catalog`, (route) => route.fulfill({ json: {
    available: true, modes: ["pipeline", "rigid"], detail: "UI fixture only",
    configs: [
      { id: configA, name: "template.yaml", input_video: "/fixture/missing.mp4", input_video_exists: repaired, task: "" },
      { id: configB, name: "recording.yaml", input_video: "/fixture/recording.mp4", input_video_exists: true, task: "Pick up the cup" },
      { id: configBad, name: "broken.yaml", error: "配置无法读取或不是有效的任务配置" },
    ],
  } }));
  await page.route(`**${prefix}/executions`, async (route) => {
    if (route.request().method() === "POST") {
      submitted.push(route.request().postDataJSON());
      return route.fulfill({ status: 422, json: { detail: "输入文件不存在：/fixture/user-choice.mp4" } });
    }
    await route.fulfill({ json: [] });
  });
  await page.goto("/executions");
  const video = page.getByLabel("输入视频路径", { exact: true });
  const start = page.getByRole("button", { name: "启动运行", exact: true });
  await expect(video).toHaveAttribute("placeholder", "/fixture/missing.mp4");
  await expect(video).toHaveAttribute("aria-invalid", "true");
  await expect(start).toBeDisabled();
  await expect(page.getByText("配置中的视频不存在，请填写有效路径后再启动。", { exact: true })).toBeVisible();
  await video.fill("   ");
  await expect(start).toBeDisabled();
  expect(submitted).toHaveLength(0);
  await page.getByLabel("运行配置").selectOption(configB);
  await expect(start).toBeEnabled();
  await expect(video).toHaveAttribute("placeholder", "/fixture/recording.mp4");
  await expect(page.getByLabel("任务描述", { exact: true })).toHaveAttribute("placeholder", "Pick up the cup");
  // Defaults are displayed, not silently copied into overrides for another config.
  await expect(page.getByLabel("任务描述", { exact: true })).toHaveValue("");
  await page.getByLabel("运行配置").selectOption(configA);
  await video.fill("/fixture/user-choice.mp4");
  await expect(start).toBeEnabled();
  await start.click();
  await expect(page.getByRole("alert").filter({ hasText: "输入文件不存在" })).toBeVisible();
  expect(submitted).toHaveLength(1);
  expect(submitted[0].video).toBe("/fixture/user-choice.mp4");
  await video.fill("");
  await expect(start).toBeDisabled();
  await page.getByLabel("任务类型").selectOption("rigid");
  await expect(start).toBeEnabled();
  await page.getByLabel("运行配置").selectOption(configBad);
  await expect(start).toBeDisabled();
  await expect(page.getByText("配置无法读取或不是有效的任务配置", { exact: true })).toBeVisible();
  await page.getByLabel("运行配置").selectOption(configA);
  await page.getByLabel("任务类型").selectOption("pipeline");
  await expect(start).toBeDisabled();
  repaired = true;
  await page.getByRole("button", { name: "刷新配置", exact: true }).click();
  await expect(start).toBeEnabled();
});
