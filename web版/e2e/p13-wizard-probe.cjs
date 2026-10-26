/**
 * P13 真机探针（方案 §4.7 P13 验收门：向导 5 步与 ConfirmGate 真机断言）。
 *
 * ① 向导五步：/tasks/new 走完 基础信息 → 备份源（手动加入路径）→ 存储目标
 *    （测试连接 stub 通过，过 G3/E1 强门禁）→ 调度 → 保留与清除；第⑤步
 *    跑 dry-run（stub 无 error 未截断）→ 断言「确认并立即首跑」从禁用变可用。
 * ② ConfirmGate：/repo 上对非默认仓库点「删除」→ 三门确认门（keyword 输入）
 *    弹出可见（默认仓库删除禁用逻辑不在本探针范围，单测已覆盖）。
 * 不真正提交任务 / 不真正删仓库——断言到「门放行 / 门弹出」为止。
 *
 * 用法：node p13-wizard-probe.cjs [base]
 */
const path = require('node:path');
const { launch, outDir } = require('./harness.cjs');

const BASE = process.argv[2] || 'http://localhost:5173';
const OUT = outDir('p13-probe');
const NOW = '2026-10-22T03:00:00Z';

const REPO_DEFAULT = {
  id: 'default', name: '默认仓库', type: 'local', url: '/repo', host_tag: null,
  url_source: 'manual', host_tag_source: 'manual', passfile_status: 'ready',
  created_at: '2026-10-18T00:00:00Z',
};
const REPO_PROBE = {
  id: 'probe-repo', name: '探针仓库', type: 'local', url: '/probe', host_tag: null,
  url_source: 'manual', host_tag_source: 'manual', passfile_status: 'ready',
  created_at: '2026-10-20T00:00:00Z',
};

const STUBS = {
  '/api/auth/me': { authenticated: true, must_change_password: false, user: 'root' },
  '/api/repos/default/status': {
    configured: true, connected: true, locked: false,
    capacity: { size: 343_597_383_680, snaps: 142 }, last_error: null,
  },
  '/api/repos/probe-repo/status': {
    configured: true, connected: true, locked: false,
    capacity: { size: 107_374_182_400, snaps: 12 }, last_error: null,
  },
  '/api/repos/default/test': { ok: true, latency_ms: 12, reason: null, failure_kind: null },
  '/api/repos/default': REPO_DEFAULT,
  '/api/repos': [REPO_DEFAULT, REPO_PROBE],
  '/api/sources/dry-run': { items: [], warnings: [], truncated: false, error: null },
  '/api/sources': {
    path: '/sources',
    items: [
      { name: 'etc', type: 'dir', display_path: '/sources/etc' },
      { name: 'data', type: 'dir', display_path: '/sources/data' },
    ],
    next_cursor: null,
  },
  '/api/fs/browse': { path: '/sources', mounts: [], items: [], next_cursor: null },
  '/api/config': {
    passfile_status: 'configured',
    items: [
      { key: 'dump.enabled', value: '1', source: 'ui', has_value: true },
      { key: 'security.cred_encryption', value: '1', source: 'ui', has_value: true },
      { key: 'timezone', value: 'Asia/Shanghai', source: 'ui', has_value: true },
    ],
  },
  '/api/system/time': { now: NOW, timezone: 'Asia/Shanghai', drift_seconds: 0 },
  '/api/cache': { path: '/cache', size_bytes: 1_048_576, items_count: 3, truncated: false, measured_at: NOW },
};

(async () => {
  const browser = await launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 });
  await ctx.addInitScript(
    ({ stubs }) => {
      try {
        localStorage.setItem('rw.theme', 'light');
        localStorage.setItem('rw.locale', 'zh-CN');
      } catch {}
      const orig = window.fetch;
      const entries = Object.entries(stubs).sort((a, b) => b[0].length - a[0].length);
      window.fetch = async (input, init) => {
        const url = typeof input === 'string' ? input : input.url;
        if (!url.includes('/api/')) {
          return orig(input, init);
        }
        for (const [prefix, data] of entries) {
          if (url.startsWith(prefix) || url.includes(prefix)) {
            return new Response(JSON.stringify(data), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            });
          }
        }
        return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
      };
      const style = document.createElement('style');
      style.textContent = '*,*::before,*::after{animation:none !important;transition:none !important}';
      document.addEventListener('DOMContentLoaded', () => document.head.appendChild(style));
    },
    { stubs: STUBS },
  );

  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(page.url() + ' ' + e.message));
  const step = async (n) => page.screenshot({ path: path.join(OUT, `wizard-step${n}.png`), fullPage: true });

  // ============ ① 向导五步 ============
  await page.goto(BASE + '/tasks/new', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);

  // 步骤 1：基础信息——填任务名
  await page.getByPlaceholder('如：每日全量').fill('探针任务');
  await step(1);
  await page.getByRole('button', { name: '下一步' }).click();
  await page.waitForTimeout(400);

  // 步骤 2：备份源——手动加入路径（不依赖树交互）
  // P19/P20 把「chips + 单行输入」换成右栏行内输入行（.sources-picker__row），
  // 旧的 `.sources-picker__input` 已从源码消失（2026-10-26 修）。现在：末行输入框
  // 键入路径 → 点该行唯一的按钮（+，aria-label=加入）→ 已加入的源以只读输入行回显。
  const manualInput = page.locator('.sources-picker__row-input input').last();
  await manualInput.fill('/sources/data');
  await page.locator('.sources-picker__row').last().getByRole('button').click();
  await page.waitForTimeout(300);
  const rowValues = await page
    .locator('.sources-picker__row-input input')
    .evaluateAll((els) => els.map((el) => el.value));
  const sourceAdded = rowValues.includes('/sources/data');
  await step(2);
  await page.getByRole('button', { name: '下一步' }).click();
  await page.waitForTimeout(400);

  // 步骤 3：存储目标——测试连接（stub 立即 success），过 G3/E1 强门禁
  await page.getByRole('button', { name: '测试连接' }).click();
  await page.waitForTimeout(600);
  const testOk = await page.getByText('连接正常（12 ms）').count();
  await step(3);
  await page.getByRole('button', { name: '下一步' }).click();
  await page.waitForTimeout(400);

  // 步骤 4：调度（默认值直接过）
  await step(4);
  await page.getByRole('button', { name: '下一步' }).click();
  await page.waitForTimeout(400);

  // 步骤 5：保留与清除——dry-run 前确认键禁用，跑完放行
  const confirmBtn = page.getByRole('button', { name: '确认并立即首跑' });
  const beforeDryRun = await confirmBtn.count() > 0 && !(await confirmBtn.first().isDisabled().catch(() => null));
  await page.getByRole('button', { name: '预览扫描范围（dry-run）' }).click();
  await page.waitForTimeout(800);
  const confirmEnabled = await confirmBtn.count() > 0 && !(await confirmBtn.first().isDisabled().catch(() => null));
  await step(5);

  console.log('[向导] 源路径已加入:', sourceAdded, '| 测试连接:', testOk > 0,
    '| dry-run 前确认键可用:', beforeDryRun, '| dry-run 后确认键可用:', confirmEnabled);

  // ============ ② ConfirmGate（repo 删除三门）：预览 → 输入 DELETE → 二次确认 ============
  await page.goto(BASE + '/repo', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);
  const probeRow = page.locator('.repo__dest', { hasText: '探针仓库' });
  await probeRow.getByRole('button', { name: '删除', exact: true }).click();
  await page.waitForTimeout(500);
  const gate = page.locator('.n-modal').first();
  const gateVisible = await gate.count() > 0 && await gate.getByText('确认影响').count() > 0;
  // 第 1→2 步：预览后下一步，出现关键词输入（placeholder=DELETE）
  await gate.getByRole('button', { name: '下一步' }).click();
  await page.waitForTimeout(300);
  const keywordInput = gate.locator('input[placeholder="DELETE"]');
  const keywordShown = await keywordInput.count() > 0;
  // 第 2→3 步：填错关键词时下一步禁用，填对后放行
  await keywordInput.fill('WRONG');
  await page.waitForTimeout(200);
  const nextDisabledOnWrong = await gate.getByRole('button', { name: '下一步' }).isDisabled();
  await keywordInput.fill('DELETE');
  await page.waitForTimeout(200);
  const nextEnabledOnRight = !(await gate.getByRole('button', { name: '下一步' }).isDisabled());
  await gate.getByRole('button', { name: '下一步' }).click();
  await page.waitForTimeout(300);
  const finalShown = await gate.getByRole('button', { name: '确认执行' }).count() > 0;
  await page.screenshot({ path: path.join(OUT, 'repo-delete-gate.png'), fullPage: true });
  console.log('[repo] ConfirmGate 弹出:', gateVisible, '| 关键词步出现:', keywordShown,
    '| 错词禁用:', nextDisabledOnWrong, '| 对词放行:', nextEnabledOnRight, '| 三门终态按钮:', finalShown);

  console.log('pageerror:', errors.length ? errors.join(' | ') : '(无)');

  const pass =
    sourceAdded === true && testOk > 0 && beforeDryRun === false && confirmEnabled === true &&
    gateVisible === true && keywordShown === true && nextDisabledOnWrong === true &&
    nextEnabledOnRight === true && finalShown === true && errors.length === 0;
  console.log(pass ? 'P13 PROBE PASS' : 'P13 PROBE FAIL');
  await browser.close();
  process.exit(pass ? 0 : 1);
})().catch((e) => {
  console.error('FATAL', e);
  process.exit(1);
});
