import { test, expect } from '@playwright/test';
const prefix = '/api/workbench/v1';

const artifact = (id: string, path: string, kind = 'image') => ({
  id, path, name: path.split('/').at(-1), kind, size: 1, modified: '2026-10-21T08:00:00Z', privileged: false,
});
const report = (id: string, status: string) => ({
  artifact: artifact(id, `runs/fixture/${id}/stage_report.json`, 'text'),
  validation: status, source_status: status, checks: [{ id: `${id}_quality`, status, required: true }],
  task_success_evaluated: false, evaluation_scope: 'component_check',
});

test('workflow isolates stage media and checks, preserves missing evidence and deep links', async ({ page }) => {
  const stages = ['stage1', 'stage2', 'stage3', 'training'].map(id => ({
    id, title: id, report: report(id, id === 'stage2' ? 'failed' : 'passed'), steps: [], history: [],
  }));
  await page.route(`**${prefix}/runs/fixture`, route => route.fulfill({ json: {
    id: 'fixture', name: 'Workflow fixture', project_id: 'imported', source: 'dexagent',
    validation: 'failed', execution: 'failed', updated_at: '2026-10-21T08:00:00Z',
    scope: { requested_stages: [] }, stages, reports: [], media: [], sessions: [], issues: [], available_actions: [],
    input_video: null, task: 'fixture task',
  } }));
  const assets = [artifact('scene', 'runs/fixture/stage2/frame0_compare.png'),
    artifact('reference', 'runs/fixture/reference/hand.png'),
    artifact('motion', 'runs/fixture/stage3/current.png'),
    artifact('historical', 'runs/fixture/stage3/previous-attempt/old.png'),
    artifact('downstream', 'runs/fixture/stage3/stage4_task/outputs/randomized.png')];
  await page.route(`**${prefix}/runs/fixture/artifacts?*`, route => {
    const params = new URL(route.request().url()).searchParams;
    const items = assets.filter(a => a.path.startsWith(`runs/fixture/${params.get('folder') || ''}`) && (!params.get('kind') || a.kind === params.get('kind')));
    return route.fulfill({ json: { items, total: items.length } });
  });
  await page.route(`**${prefix}/artifacts/*/content*`, route => route.fulfill({
    contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200"><rect width="300" height="200" fill="gray"/></svg>',
  }));
  await page.goto('/runs/fixture?phase=scene');
  await expect(page.getByRole('tab', { name: '完整流程' })).toHaveAttribute('aria-selected', 'true');
  const flow = page.getByRole('region', { name: '完整流程工作区' });
  const nav = flow.getByRole('navigation', { name: '流程环节' });
  await expect(nav.getByRole('button')).toHaveCount(9);
  await expect(flow.getByRole('img', { name: 'frame0_compare.png' })).toBeVisible();
  await flow.getByRole('button', { name: '验收证据', exact: true }).click();
  await expect(flow).toContainText('stage2_quality');
  await nav.getByRole('button', { name: /重定向与动作生成/ }).click();
  await expect(flow.getByRole('img', { name: 'current.png' })).toBeVisible();
  await expect(flow.getByLabel('当前环节预览').locator('option')).toHaveCount(1);
  await expect(flow.getByRole('img', { name: 'frame0_compare.png' })).toHaveCount(0);
  await nav.getByRole('button', { name: /轨迹提取/ }).click();
  await expect(flow.getByRole('img', { name: 'hand.png' })).toBeVisible();
  await expect(flow).toContainText('尚无独立验收证据');
  await flow.getByRole('button', { name: '验收证据', exact: true }).click();
  await expect(flow.getByRole('heading', { name: '未记录独立验收' })).toBeVisible();
  await nav.getByRole('button', { name: /物理执行与验证/ }).click();
  // A passed stage3 report is not independently verified execution evidence.
  await expect(flow).toContainText('尚无独立验收证据');
  await nav.getByRole('button', { name: /策略训练/ }).click();
  await flow.getByRole('button', { name: '验收证据', exact: true }).click();
  await expect(flow).toContainText('training_quality');
  await expect(flow).toContainText('任务成功已评估：未确认');
  await page.reload();
  await expect(nav.getByRole('button', { name: /策略训练/ })).toHaveAttribute('aria-current', 'step');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test('real scene preview and backend replay remain reachable in the default workspace', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/runs/20fa33af189e626f743f7afd?phase=scene');
  const flow = page.getByRole('region', { name: '完整流程工作区' });
  await expect(flow.getByRole('img', { name: 'frame0_compare.png' })).toBeVisible({ timeout: 30000 });
  await flow.getByRole('button', { name: '查看本环节验收' }).click();
  await expect(flow.getByRole('heading', { name: '检查与证据', exact: true })).toBeVisible();
  await page.goto('/runs/9c8268e5ce354d38401e4ead');
  await expect(flow.locator('video')).toBeVisible({ timeout: 30000 });
  await expect(flow).toContainText('任务成功已评估：未确认');
  await flow.getByRole('button', { name: /历史尝试与完整报告/ }).click();
  await expect(page.getByRole('tab', { name: '验收', exact: true })).toHaveAttribute('aria-selected', 'true');
  expect(errors).toEqual([]);
});

test('background separation belongs to scene reconstruction and retains the legacy link', async ({ page }) => {
  await page.goto('/scenes');
  const navigation = page.getByRole('navigation', { name: '主导航' });
  await expect(navigation.getByRole('link', { name: '背景清理', exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: '背景与对象分离', exact: true }).click();
  await expect(page).toHaveURL('/scenes/separation');
  await expect(navigation.getByRole('link', { name: '场景与资产', exact: true })).toHaveClass(/active/);
  await expect(page.getByRole('heading', { name: '背景与对象分离', exact: true })).toBeVisible();
  await expect(page.getByText('场景重建的一步：', { exact: false })).toBeVisible();
  await page.getByRole('link', { name: '← 场景与资产', exact: true }).click();
  await expect(page).toHaveURL('/scenes');
  await page.goto('/background-cleanup');
  await expect(page.getByRole('heading', { name: '背景与对象分离', exact: true })).toBeVisible();
});

test('a new execution report replaces the backend preview scope without stale media', async ({ page }) => {
  let explicit = false;
  await page.route(`**${prefix}/runs/scope-switch`, route => route.fulfill({ json: {
    id: 'scope-switch', name: 'Scope switch', source: 'robosimstudio', validation: 'warning',
    execution: 'unknown', updated_at: '2026-10-21T08:00:00Z', scope: { requested_stages: [] },
    reports: [], media: [], sessions: [], issues: [], available_actions: [],
    stages: [{ id: explicit ? 'execution' : 'backend', title: 'Execution', steps: [], history: [],
      report: { ...report(explicit ? 'execution' : 'backend', 'warning'), sha256: explicit ? 'new' : 'old' } }],
  } }));
  await page.route(`**${prefix}/runs/scope-switch/artifacts?*`, route => {
    const p = new URL(route.request().url()).searchParams;
    const a = p.get('folder') === '' ? artifact('old', 'runs/scope-switch/old.png') :
      artifact('new', 'runs/scope-switch/execution/new.png');
    return route.fulfill({ json: { items: p.get('kind') === 'video' ? [] : [a], total: 1 } });
  });
  await page.route(`**${prefix}/artifacts/*/content*`, route => route.fulfill({
    contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200"/>',
  }));
  await page.goto('/runs/scope-switch');
  const flow = page.getByRole('region', { name: '完整流程工作区' });
  await expect(flow.getByRole('img', { name: 'old.png' })).toBeVisible();
  explicit = true;
  // Existing run polling receives the new report while the component remains mounted.
  await expect(flow.getByRole('img', { name: 'new.png' })).toBeVisible({ timeout: 16000 });
  await expect(flow.getByRole('img', { name: 'old.png' })).toHaveCount(0);
});
