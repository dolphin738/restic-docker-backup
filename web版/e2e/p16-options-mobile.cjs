/**
 * P16 恢复选项页移动端/桌面走查（UI全量改造方案 §4.9 P16-4 验收门）。
 *
 * 断言（口径沿用 §4.3 / P14）：
 *   A. 逐视口横向溢出：documentElement.scrollWidth <= innerWidth + 1（390 重点）；
 *   B. 全程 pageerror 为零；
 *   C. 三卡片 + 两端页脚 + raised 单选卡在 DOM 落地（原位置默认）；
 *   D. 桌面下列宽 500px 居中（.restore-flow__section 宽度 <= 500）；
 *   E. 切「选择位置」后路径选择器（树 + 二模开关）渲染且仍零溢出零报错。
 *
 * API 全 stub（page.route），无需后端；dev server 打 localhost:5173。
 * 用法：node p16-options-mobile.cjs [base]
 */
const path = require('node:path');
const fs = require('node:fs');
const { launch, outDir } = require('./harness.cjs');

const BASE = process.argv[2] || 'http://localhost:5173';
const OUT = outDir('p16-options');

const SNAPSHOT_ID = 'a'.repeat(64);
const SNAPSHOTS = [
  {
    id: SNAPSHOT_ID,
    short_id: 'aaaaaaa1',
    time: '2026-10-24T03:00:00Z',
    hostname: 'host-01',
    username: 'backup',
    tags: ['task-1'],
    paths: ['/sources/etc', '/sources/data'],
    size: 1048576,
  },
];

const STUBS = {
  '/api/auth/me': { authenticated: true, must_change_password: false, user: 'root' },
  '/api/repos': [
    {
      id: 'default', name: '默认仓库', type: 'local', url: '/repo', host_tag: null,
      url_source: 'manual', host_tag_source: 'manual', passfile_status: 'ready',
      created_at: '2026-10-18T00:00:00Z',
    },
  ],
  '/api/system/runtime': {
    deploy_mode: 'docker',
    browse_root: '/sources',
    sources_root: '/sources',
    restore_root: '/restore',
    deny_prefixes: [],
  },
  '/api/snapshots': { items: SNAPSHOTS, total: SNAPSHOTS.length, next_cursor: null },
  '/api/fs/browse': {
    path: '/restore',
    mounts: [],
    items: [
      { name: 'backups', type: 'dir', path: '/restore/backups', is_mountpoint: false },
      { name: 'media', type: 'dir', path: '/restore/media', is_mountpoint: false },
    ],
    next_cursor: null,
  },
  '/api/restore/preview': {
    items: [],
    overwrite_count: 0,
    total_bytes: 0,
    truncated: false,
    next_cursor: null,
  },
};

const VIEWPORTS = [1440, 960, 768, 390];

(async () => {
  const browser = await launch();
  const errors = [];
  const overflow = [];
  const checks = [];

  const page = await browser.newPage();
  page.on('pageerror', (err) => errors.push(String(err)));
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text());
  });

  // API stub：只拦 /api/ 前缀的真实端点（勿用 **/api/** 通配——会误伤
  // Vite 的 /src/api/*.ts 模块脚本，MIME 严格校验下页面直接白屏）。
  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const stub = STUBS[new URL(route.request().url()).pathname];
    if (stub) {
      await route.fulfill({ json: stub });
      return
    }
    // 未 stub 的 API：回空对象，避免 502 噪声（本探针只走选项页）
    await route.fulfill({ json: {} })
  })

  for (const width of VIEWPORTS) {
    await page.setViewportSize({ width, height: 900 });
    // 2026-10-28 恢复流程并入快照浏览页：`/restore/:id/options` 已删除，
    // 面板总从步骤 1 起步，进选项页必须点一次「下一步」。
    await page.goto(`${BASE}/snapshots?restore=${SNAPSHOT_ID}`, { waitUntil: 'networkidle' });
    await page.waitForSelector('.restore-flow__section', { timeout: 15000 });
    await page.locator('button', { hasText: '下一步' }).first().click();
    await page.waitForSelector('.restore-options', { timeout: 15000 });
    await page.waitForTimeout(600);

    const metrics = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      sectionWidth: document.querySelector('.restore-flow__section')?.getBoundingClientRect().width ?? -1,
      // 折叠卡（n-card 根）与 raised 单选卡（button 根）同用 .restore-opt-card 类名，
      // 按标签名区分计数
      optionCards: document.querySelectorAll('.n-card.restore-opt-card').length,
      radioCards: document.querySelectorAll('button.restore-opt-card').length,
      radios: document.querySelectorAll('[role="radio"]').length,
      footerBtns: Array.from(document.querySelectorAll('.restore-options__footer button')).map((b) => b.textContent.trim()),
    }));
    checks.push({ viewport: width, ...metrics });
    if (metrics.scrollWidth > metrics.innerWidth + 1) {
      overflow.push({ viewport: width, ...metrics });
    }
    await page.screenshot({ path: path.join(OUT, `options-${width}.png`), fullPage: true });

    // 切「选择位置」→ 路径选择器渲染 + 复测溢出
    await page.locator('[role="radio"]').nth(1).click();
    await page.waitForSelector('.restore-picker', { timeout: 10000 });
    await page.waitForTimeout(400);
    const after = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      picker: !!document.querySelector('.restore-picker'),
      tree: !!document.querySelector('.vtree'),
      segmented: document.querySelectorAll('.rw-segmented .n-button').length,
    }));
    if (after.scrollWidth > after.innerWidth + 1) {
      overflow.push({ viewport: width, phase: 'pickLocation', ...after });
    }
  }

  await browser.close();

  const report = { base: BASE, checks, overflow, errors };
  fs.writeFileSync(path.join(OUT, 'p16-options.json'), JSON.stringify(report, null, 2));

  const desk = checks.find((c) => c.viewport === 1440);
  const pass =
    overflow.length === 0 &&
    errors.length === 0 &&
    desk &&
    desk.optionCards === 2 &&
    desk.radioCards === 2 &&
    desk.radios === 2 &&
    desk.sectionWidth <= 500 &&
    desk.footerBtns.length === 2;

  console.log(JSON.stringify({ pass, overflow, errors, checks }, null, 2));
  process.exit(pass ? 0 : 1);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
