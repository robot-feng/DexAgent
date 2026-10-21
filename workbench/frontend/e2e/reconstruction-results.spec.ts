import { test, expect } from '@playwright/test';

const prefix = '/api/workbench/v1';
const runId = 'reconstruction-fixture';
const report = {
  artifact: { id: 'scene-report', name: 'stage_report.json', path: 'runs/fixture/stage2/stage_report.json', kind: 'text' },
  validation: 'warning', checks: [], sha256: 'scene-version', task_success_evaluated: false,
};
const run = {
  id: runId, name: 'Reconstruction result fixture', project_id: 'imported', source: 'dexagent',
  validation: 'warning', execution: 'completed', updated_at: '2026-10-21T08:00:00Z',
  scope: { requested_stages: [] }, stages: [{ id: 'stage2', title: '场景重建', report, steps: [], history: [] }],
  reports: [], issues: [], sessions: [], media: [], available_actions: [],
};
const makeJob = (id: string, bound: string | null = runId) => ({
  id, run_id: bound, scene_name: id, source_video: { id: 'capture', name: `${id}.mp4`, path: 'capture.mp4', size: 2 },
  capture_type: 'scene_scan', status: 'review_mask', current_step: '检查 mask', message: '等待复核',
  include_objects: ['robot'], skip_objects: [], detected_objects: ['robot'], proposed_objects: [],
  propagation_model: 'litetext', proposal_backend: 'manual', mask_summary: null,
  source_video_url: '/original-capture.mp4', review_video_url: `/processed-${id}.mp4`,
  mask_video_url: `/mask-${id}.mp4`, mask_preview_url: null, cleaned_video_url: null,
  can_build_scene: false, scene_path: null, log_url: '/log', updated_at: '2026-10-21T08:00:00Z',
});

test('scene substeps use explicit job ownership and the processed source clock', async ({ page }) => {
  const linked = makeJob('linked');
  const unrelated = makeJob('unrelated', 'another-run');
  await page.route(`**${prefix}/runs/${runId}`, route => route.fulfill({ json: run }));
  await page.route(`**${prefix}/background-cleanups/jobs*`, route => route.fulfill({ json: [linked, unrelated] }));
  await page.route(`**${prefix}/background-cleanups/unrelated`, route => route.fulfill({ json: unrelated }));
  await page.route(`**${prefix}/runs/${runId}/artifacts?*`, route => {
    const folder = new URL(route.request().url()).searchParams.get('folder');
    return route.fulfill({ json: folder === 'stage2/assets' ? { items: [{
      id: 'object', name: 'cup.glb', path: 'runs/fixture/stage2/assets/cup.glb', size: 1200, kind: 'model',
    }], total: 1 } : { items: [], total: 0 } });
  });
  await page.goto(`/runs/${runId}?phase=scene`);
  const section = page.getByRole('region', { name: '场景重建子步骤' });
  await section.getByRole('button', { name: '背景与对象分离', exact: true }).click();
  await expect(section.getByLabel('关联的分离任务').locator('option')).toHaveCount(1);
  const pair = section.getByTestId('reconstruction-mask-pair');
  await expect(pair.getByLabel('同源抽帧视频')).toHaveAttribute('src', '/processed-linked.mp4');
  await expect(pair.getByLabel('移除区域 mask')).toHaveAttribute('src', '/mask-linked.mp4');
  await expect(section.getByRole('link', { name: '查看原始视频' })).toHaveAttribute('href', '/original-capture.mp4');
  await section.getByRole('button', { name: '背景补全', exact: true }).click();
  await expect(section.getByRole('heading', { name: '尚无背景补全结果' })).toBeVisible();
  await expect(section.getByTestId('reconstruction-mask-pair')).toHaveCount(0);
  await section.getByRole('button', { name: '对象资产', exact: true }).click();
  await expect(section.getByRole('button', { name: /cup.glb/ })).toBeVisible();
  await page.goto(`/runs/${runId}?phase=scene&sceneStep=separation&cleanup=unrelated`);
  await expect(section.getByRole('heading', { name: '此任务未关联到当前运行' })).toBeVisible();
  await expect(section.locator('video')).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test('cleanup without a processed source never presents raw capture as synchronized review', async ({ page }) => {
  const job = { ...makeJob('legacy'), review_video_url: null };
  await page.route(`**${prefix}/background-cleanups/jobs`, route => route.fulfill({ json: [job] }));
  await page.goto('/scenes/separation');
  await expect(page.getByText('缺少同源处理视频', { exact: false })).toBeVisible();
  await expect(page.getByTestId('cleanup-synchronized-review')).toHaveCount(0);
  await expect(page.getByLabel('原始视频（独立播放）')).toHaveAttribute('src', '/original-capture.mp4');
});

test('explicit association and new-job context preserve the selected run without approving masks', async ({ page }) => {
  let job = makeJob('selected', null);
  let links = 0;
  let creates = 0;
  let approvals = 0;
  await page.route(`**${prefix}/runs/${runId}`, route => route.fulfill({ json: run }));
  await page.route(`**${prefix}/background-cleanups/jobs`, route => route.fulfill({ json: [job] }));
  await page.route(`**${prefix}/background-cleanups/selected`, route => route.fulfill({ json: job }));
  await page.route(`**${prefix}/background-cleanups/selected/run`, route => {
    expect(route.request().postDataJSON()).toEqual({ run_id: runId });
    links += 1;
    job = { ...job, run_id: runId };
    return route.fulfill({ json: job });
  });
  await page.route(`**${prefix}/background-cleanups/*/approve-mask`, route => {
    approvals += 1;
    return route.fulfill({ status: 500 });
  });
  await page.route(`**${prefix}/background-cleanups`, route => {
    const body = route.request().postDataJSON();
    expect(body.run_id).toEqual(runId);
    creates += 1;
    return route.fulfill({ json: job, status: 202 });
  });
  await page.goto(`/scenes/separation?run=${runId}&job=selected`);
  await expect(page.getByText('Reconstruction result fixture', { exact: false }).first()).toBeVisible();
  await page.getByRole('button', { name: '关联到当前重建运行' }).click();
  await expect(page.getByRole('link', { name: '返回重建结果' })).toHaveAttribute('href', new RegExp(`runs/${runId}.*cleanup=selected`));
  await expect(page.getByRole('button', { name: '关联到当前重建运行' })).toHaveCount(0);
  expect(links).toBe(1);
  expect(approvals).toBe(0);
  await page.reload();
  await expect(page.getByLabel('选择分离任务')).toHaveValue('selected');
  await page.getByLabel('对象提案 provider').selectOption('manual');
  await page.getByRole('button', { name: '生成 mask 预览' }).click();
  await expect.poll(() => creates).toBe(1);
  expect(approvals).toBe(0);
});
