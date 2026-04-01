import { test, expect } from '@playwright/test';

const BASE = process.env.BASE_URL || 'http://170.106.36.6:20002';

test.describe('T2V Generation Form', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto(BASE + '/');
    // Wait for video module to load
    await expect(page.locator('#panel-t2v')).toBeVisible({ timeout: 15000 });
    // Ensure t2v sub-tab is active
    const t2vTab = page.locator('.tab[data-tab="t2v"]');
    if (!(await t2vTab.getAttribute('class'))?.includes('active')) {
      await t2vTab.click();
    }
  });

  test('T2V form fields are present and interactive', async ({ page }) => {
    await expect(page.locator('#t2v-prompt')).toBeVisible();
    await expect(page.locator('#t2v-model')).toBeVisible();
    await expect(page.locator('#t2v-seed')).toBeVisible();
    await expect(page.locator('#t2v-fps')).toBeVisible();
    await expect(page.locator('#t2v-duration')).toBeVisible();
  });

  test('T2V model selector has expected options', async ({ page }) => {
    const modelSelect = page.locator('#t2v-model');
    await expect(modelSelect).toBeVisible();
    const options = await modelSelect.locator('option').allTextContents();
    expect(options.some(o => o.includes('A14B') || o.includes('a14b'))).toBe(true);
    expect(options.some(o => o.includes('5B') || o.includes('5b'))).toBe(true);
  });

  test('T2V generate button is present and clickable', async ({ page }) => {
    await expect(page.locator('#t2v-prompt')).toBeVisible();
    await page.locator('#t2v-prompt').fill('a beautiful sunset over the ocean, cinematic');
    await page.locator('#t2v-model').selectOption('5b');
    // Verify generate button is visible and enabled
    const btn = page.locator('#panel-t2v button:has-text("生成视频")');
    await expect(btn).toBeVisible();
    await expect(btn).toBeEnabled();
  });

  test('T2V generate API (multipart/form-data) returns task_id', async ({ request }) => {
    // The /api/v1/generate endpoint accepts multipart/form-data (confirmed via OpenAPI schema)
    const resp = await request.post(`${BASE}/api/v1/generate`, {
      multipart: {
        prompt: 'a beautiful sunset over the ocean, cinematic',
        model: '5b',
        seed: '-1',
        fps: '16',
        duration: '3.0',
      },
    });
    expect([200, 201, 202].includes(resp.status())).toBe(true);
    const body = await resp.json();
    expect(body).toHaveProperty('task_id');
    expect(typeof body.task_id).toBe('string');
    expect(body.task_id.length).toBeGreaterThan(0);
  });

  test('submitted task appears in task status endpoint', async ({ request }) => {
    const genResp = await request.post(`${BASE}/api/v1/generate`, {
      multipart: {
        prompt: 'a red sports car driving fast, cinematic',
        model: '5b',
        seed: '-1',
        fps: '16',
        duration: '3.0',
      },
    });
    expect([200, 201, 202].includes(genResp.status())).toBe(true);
    const genBody = await genResp.json();
    const taskId = genBody.task_id;

    const statusResp = await request.get(`${BASE}/api/v1/tasks/${taskId}`);
    expect(statusResp.status()).toBe(200);
    const statusBody = await statusResp.json();
    expect(statusBody).toHaveProperty('status');
    expect(['queued', 'running', 'completed', 'failed'].includes(statusBody.status)).toBe(true);
  });

});
