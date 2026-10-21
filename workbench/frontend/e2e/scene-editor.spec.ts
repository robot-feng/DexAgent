import { expect, test } from "@playwright/test";

const prefix = "/api/workbench/v1";

test("native scene editing keeps textured surroundings, guards navigation and saves a candidate", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(420_000);
  const scenes = await (await request.get(`${prefix}/scenes`)).json();
  const scene = scenes.items.find(
    (s: { name: string }) =>
      s.name === "droid_desk_throw_away_trash_scene_state_latest",
  );
  test.skip(
    !scene,
    "official SimFoundry example scene and its native assets must be installed for this browser check",
  );
  const created = await request.post(`${prefix}/editors`, {
    data: { scene_id: scene.id },
  });
  expect(created.ok()).toBeTruthy();
  const editor = await created.json();
  const ownedEditors = [editor.id];
  try {
    await expect
      .poll(
        async () =>
          (await (await request.get(`${prefix}/editors/${editor.id}`)).json())
            .status,
        { timeout: 120_000 },
      )
      .toBe("ready");
    await page.goto(`/scenes?scene=${scene.id}&editor=${editor.id}`);
    const frame = page.frameLocator('iframe[title="原生场景布局编辑器"]');
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await expect(frame.locator("#objlist")).toContainText("blue_cup_0", {
      timeout: 90_000,
    });
    await expect(
      frame.locator('canvas[data-engine^="three.js"]'),
    ).toBeVisible();
    if (
      await frame.getByRole("button", { name: "Skip", exact: true }).isVisible()
    )
      await frame.getByRole("button", { name: "Skip", exact: true }).click();
    const nativeUrl = `${prefix}/editors/${editor.id}/view/`;
    const manifest = await (
      await request.get(nativeUrl + "data/manifest.json")
    ).json();
    expect(manifest.complete).toBe(true);
    expect(
      manifest.objects.find(
        (o: { name: string }) => o.name === "mesh_background_0",
      ).textured,
    ).toBe(true);
    await page.getByRole("button", { name: "仅看背景", exact: true }).click();
    await expect(page.getByRole("button", { name: "仅看背景", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText(/当前显示：仅背景/)).toBeVisible();
    const robotRow = frame.locator("#objlist .obj").filter({ hasText: "robot0" });
    const roomRow = frame.locator("#objlist .obj").filter({ hasText: "mesh_background_0" });
    await expect(robotRow.locator("button.eye")).toHaveAttribute("title", "Show robot0 in the viewport");
    await expect(roomRow.locator("button.eye")).toHaveAttribute("title", "Hide mesh_background_0 from the viewport");
    await page.screenshot({ path: testInfo.outputPath("background-isolated.png") });
    await page.getByRole("button", { name: "仅看机器人与物体", exact: true }).click();
    await expect(page.getByRole("button", { name: "仅看机器人与物体", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText(/当前显示：仅机器人与物体/)).toBeVisible();
    await expect(robotRow.locator("button.eye")).toHaveAttribute("title", "Hide robot0 from the viewport");
    await expect(roomRow.locator("button.eye")).toHaveAttribute("title", "Show mesh_background_0 in the viewport");
    await page.screenshot({ path: testInfo.outputPath("entities-isolated.png") });
    await page.getByRole("button", { name: "显示全部", exact: true }).click();
    const before = await (
      await request.get(nativeUrl + "api/scene_state")
    ).json();
    await frame
      .locator("#objlist .obj")
      .filter({ hasText: "blue_cup_0" })
      .click();
    const position = frame.getByRole("spinbutton", {
      name: "Position X, metres",
      exact: true,
    });
    await expect(position).toBeEnabled();
    const target = Number(await position.inputValue()) + 0.005;
    await position.fill(String(target));
    await position.press("Tab");
    await expect(frame.locator("#btn-save")).toBeEnabled();
    await page.getByRole("link", { name: "项目", exact: true }).click();
    await expect(
      frame.getByText("Unsaved changes", { exact: true }),
    ).toBeVisible();
    await frame.getByRole("button", { name: "Stay here", exact: true }).click();
    await expect(page).toHaveURL(/\/scenes\?/);
    expect(Number(await position.inputValue())).toBeCloseTo(target, 5);
    await page.getByRole("button", { name: "仅看机器人与物体", exact: true }).click();
    const saved = page.waitForResponse(
      (response) =>
        response.url().endsWith(nativeUrl + "api/save") &&
        response.request().method() === "POST",
    );
    await frame.locator("#btn-save").click();
    const response = await saved;
    expect(response.status()).toBe(200);
    const save = await response.json();
    expect(save.promoted).toBe(false);
    await expect(
      page.locator(".editor-revisions > summary"),
    ).toBeVisible({ timeout: 15_000 });
    const state = await (
      await request.get(`${prefix}/editors/${editor.id}`)
    ).json();
    const candidate = await (await request.get(state.revisions[0].url)).json();
    expect(candidate.objects_info.init_info).toHaveProperty("mesh_background_0");
    const root = candidate.state.registry.object_registry.blue_cup_0.root_link;
    expect(root.pos[0]).toBeCloseTo(target, 5);
    expect(root.lin_vel).toEqual([0, 0, 0]);
    expect(
      candidate.state.registry.object_registry.robot0.root_link.pos,
    ).toEqual(before.objects.robot0.position);
    expect(
      (await (await request.get(`${prefix}/scenes`)).json()).items.find(
        (s: { id: string }) => s.id === scene.id,
      ).sha256,
    ).toBe(scene.sha256);
    await page.getByRole("button", { name: "显示全部", exact: true }).click();
    const version = state.revisions[0];
    await page.locator(".editor-revisions > summary").click();
    const candidateRow = page.locator(".revision-row").filter({ hasText: version.name });
    await expect(candidateRow).toBeVisible({ timeout: 15_000 });
    await expect(candidateRow).toContainText(version.sha256);
    await position.fill(String(target + 0.003));
    await position.press("Tab");
    await candidateRow.getByRole("button", { name: "继续编辑", exact: true }).click();
    await expect(frame.getByText("Unsaved changes", { exact: true })).toBeVisible();
    await expect(frame.getByRole("button", { name: "Stay here", exact: true })).toBeInViewport();
    await frame.getByRole("button", { name: "Stay here", exact: true }).click();
    expect(Number(await position.inputValue())).toBeCloseTo(target + 0.003, 5);
    expect((await (await request.get(`${prefix}/editors/${editor.id}`)).json()).status).toBe("ready");
    await candidateRow.getByRole("button", { name: "继续编辑", exact: true }).click();
    await frame.getByRole("button", { name: "Discard and continue", exact: true }).click();
    await expect.poll(async () => new URL(page.url()).searchParams.get("editor"), { timeout: 20_000 }).not.toBe(editor.id);
    const resumedId = new URL(page.url()).searchParams.get("editor")!;
    ownedEditors.push(resumedId);
    await expect.poll(async () => (await (await request.get(`${prefix}/editors/${resumedId}`)).json()).status,
      { timeout: 120_000 }).toBe("ready");
    const resumed = await (await request.get(`${prefix}/editors/${resumedId}`)).json();
    expect(resumed.parent_revision).toEqual({ editor_id: editor.id, revision_id: version.id, sha256: version.sha256 });
    const resumedFrame = page.frameLocator('iframe[title="原生场景布局编辑器"]');
    await expect(resumedFrame.locator("#objlist")).toContainText("blue_cup_0", { timeout: 90_000 });
    await expect(resumedFrame.locator('canvas[data-engine^="three.js"]')).toBeVisible();
    const reopened = await (await request.get(resumed.url + "api/scene_state")).json();
    expect(reopened.objects.blue_cup_0.position[0]).toBeCloseTo(target, 5);
    expect((await (await request.get(`${prefix}/editors/${editor.id}`)).json()).status).toBe("stopped");
    await expect(page.getByText("当前从已保存候选继续编辑。", { exact: false })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("scene-editor.png") });
    await page.getByRole("link", { name: "项目", exact: true }).click();
    await expect(page).toHaveURL(/\/projects$/);
    expect(errors).toEqual([]);
  } finally {
    for (const id of ownedEditors) await request.post(`${prefix}/editors/${id}/stop`, { data: {} });
  }
});
