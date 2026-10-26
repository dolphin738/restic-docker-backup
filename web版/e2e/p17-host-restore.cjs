/**
 * P17 host 形态恢复链路真机探针（UI全量改造方案 §4.9.8，owner 2026-10-25 Q1=A/Q2）。
 *
 * 走真实用户旅程 select-files → 下一步 → options（原位流进步骤 2 会触发 E20 预览），
 * 断言（浏览器级，非单测）：
 *   A. **Q2 反证**：host 模式下 E20 预览请求的 `target` 必须是展示形态本身
 *      （`/srv/data`），**不得**是旧实现的双前缀 `/sources/srv/data`；
 *   B. **Q1**：「选择位置」树根 = runtime 下发的 `restore_root`（host → `/`），
 *      地址栏根段文案为 `/`（双模式树生效）；挂载点收在根段旁的三角菜单里
 *      （P19 起 chips 退役，改用 D 形态地址栏）；
 *   C. host deny：受保护目录（`/repo`）点选不生效 + 内联错误可见；
 *   D. 桌面 1440 与移动 390 零横向溢出、零 pageerror。
 *
 * API 全 stub（`/api/` 前缀谓词，勿用 **\/api\/** 通配——会误伤 Vite 模块）。
 * 用法：node p17-host-restore.cjs [base]
 */
const path = require('node:path');
const fs = require('node:fs');
const { launch, outDir } = require('./harness.cjs');

// API 全量 route stub（无需后端，也无需 host 形态的真实实例）→ 与其余探针同用 :5173。
// 历史上默认 :5174 是当时开双前端实例的遗留，收编入库时统一（owner 2026-10-26）。
const BASE = process.argv[2] || 'http://localhost:5173';
const OUT = outDir('p17-host');

const SNAPSHOT_ID = 'c'.repeat(64);
const HOST_ROOT = '/srv/data';

const STUBS = {
  '/api/auth/me': { authenticated: true, must_change_password: false, user: 'root' },
  '/api/repos': [
    {
      id: 'default', name: '默认仓库', type: 'local', url: '/repo', host_tag: null,
      url_source: 'manual', host_tag_source: 'manual', passfile_status: 'ready',
      created_at: '2026-10-18T00:00:00Z',
    },
  ],
  // host 形态：三条根均为 /，deny 含仓库与数据目录
  '/api/system/runtime': {
    deploy_mode: 'host',
    browse_root: '/',
    sources_root: '/sources',
    restore_root: '/',
    inplace_root: '/',
    deny_prefixes: ['/repo', '/data', '/proc', '/sys', '/dev', '/run'],
  },
  '/api/snapshots': {
    items: [
      {
        id: SNAPSHOT_ID, short_id: 'ccccccc1', time: '2026-10-24T03:00:00Z',
        hostname: 'host-01', username: 'backup', tags: [], paths: [HOST_ROOT], size: 1048576,
      },
    ],
    total: 1,
    next_cursor: null,
  },
  ['/api/snapshots/' + SNAPSHOT_ID + '/files']: { items: [], next_cursor: null },
  '/api/fs/browse': {
    path: '/',
    mounts: ['/home', '/repo', '/srv'],
    items: [
      { name: 'repo', type: 'dir', path: '/repo', is_mountpoint: true },
      { name: 'srv', type: 'dir', path: '/srv', is_mountpoint: true },
      { name: 'home', type: 'dir', path: '/home', is_mountpoint: true },
    ],
    next_cursor: null,
  },
  '/api/restore/preview': {
    items: [], overwrite_count: 0, total_bytes: 0, truncated: false, next_cursor: null,
  },
};

(async () => {
  const browser = await launch();
  const errors = [];
  const previewTargets = [];
  const overflow = [];

  const page = await browser.newPage();
  page.on('pageerror', (err) => errors.push(String(err)));
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text());
  });
  page.on('request', (req) => {
    if (req.url().includes('/api/restore/preview') && req.method() === 'POST') {
      try {
        previewTargets.push(JSON.parse(req.postData() || '{}').target);
      } catch {
        previewTargets.push('<unparsable>');
      }
    }
  });

  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const stub = STUBS[new URL(route.request().url()).pathname];
    await route.fulfill(stub ? { json: stub } : { json: {} });
  });

  const results = {};
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    // 真实旅程：select-files → 下一步（原位流 goConfirm 会触发 loadPreview）
    await page.goto(`${BASE}/restore/${SNAPSHOT_ID}/select-files`, { waitUntil: 'networkidle' });
    await page.waitForSelector('.restore-flow__section', { timeout: 15000 });
    await page.waitForTimeout(800);
    await page.locator('button', { hasText: '下一步' }).first().click();
    await page.waitForSelector('.restore-options', { timeout: 15000 });
    await page.waitForTimeout(800);

    const metrics = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      inplaceValue: document.querySelector('.n-base-selection-input__content')?.textContent?.trim() ?? '',
    }));
    if (metrics.scrollWidth > metrics.innerWidth + 1) {
      overflow.push({ width, phase: 'options', ...metrics });
    }

    // 切「选择位置」→ 树根应为 host 的 `/`
    await page.locator('[role="radio"]').nth(1).click();
    await page.waitForSelector('.restore-picker', { timeout: 10000 });
    await page.waitForTimeout(500);
    // P19：快跳区 = D 形态地址栏（根段 + 三角菜单 + 就地编辑），不再是平铺 chips
    const picker = await page.evaluate(() => ({
      rootSeg: document.querySelector('.pcrumb__seg')?.textContent?.trim() ?? '',
      hasCaret: !!document.querySelector('.pcrumb__caret'),
      hasTree: !!document.querySelector('.vtree'),
    }));
    // 展开三角 → 挂载点菜单（host 下发 /home /repo /srv）
    await page.locator('.pcrumb__caret').click();
    await page.waitForTimeout(200);
    picker.mounts = await page.evaluate(() =>
      Array.from(document.querySelectorAll('.pcrumb__menu-item')).map((b) => b.textContent.trim()),
    );
    await page.keyboard.press('Escape');
    await page.locator('.vtree').click({ position: { x: 2, y: 2 } }).catch(() => {});

    // 点末段右侧空白 → 就地变地址栏（真机验证 D 形态编辑热区）
    await page.locator('.pcrumb__tail').click();
    await page.waitForTimeout(200);
    const editor = await page.evaluate(() => {
      const input = document.querySelector('.pcrumb input');
      return { hasInput: !!input, value: input?.value ?? '' };
    });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(150);
    picker.editor = editor;

    // 点 deny 目录 /repo（树内第一行）→ 不选中 + 错误可见
    let denyBlocked = null;
    const repoRow = page.locator('.vtree__row').first();
    if (await repoRow.count()) {
      await repoRow.click();
      await page.waitForTimeout(300);
      denyBlocked = await page.evaluate(() => {
        const alert = document.querySelector('.restore-picker .n-alert');
        const picked = document.querySelector('.restore-picker__path')?.textContent?.trim() ?? '';
        return { alertText: alert?.textContent?.trim() ?? '', picked };
      });
    }

    const pickerOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    if (pickerOverflow > 1) {
      overflow.push({ width, phase: 'pickLocation', overflowPx: pickerOverflow });
    }

    await page.screenshot({ path: path.join(OUT, `host-${width}.png`), fullPage: true });
    results[width] = { options: metrics, picker, denyBlocked };
  }

  await browser.close();

  const noDoublePrefix = previewTargets.length > 0 && previewTargets.every((t) => t === HOST_ROOT);
  const rootsAreHost = Object.values(results).every((r) => r.picker.rootSeg === '/');
  // P19：挂载点必须在三角菜单里，且整条地址可就地编辑
  const mountsInMenu = Object.values(results).every(
    (r) => r.picker.hasCaret && ['/home', '/repo', '/srv'].every((m) => r.picker.mounts.includes(m)),
  );
  const addressEditable = Object.values(results).every(
    (r) => r.picker.editor.hasInput && r.picker.editor.value === '/',
  );
  const denyWorks = Object.values(results).every(
    (r) => r.denyBlocked && r.denyBlocked.alertText.includes('受保护') && r.denyBlocked.picked === '/',
  );
  const pass =
    noDoublePrefix &&
    rootsAreHost &&
    mountsInMenu &&
    addressEditable &&
    denyWorks &&
    overflow.length === 0 &&
    errors.length === 0;

  const report = { pass, previewTargets, overflow, errors, results };
  fs.writeFileSync(path.join(OUT, 'p17-host.json'), JSON.stringify(report, null, 2));
  console.log(
    JSON.stringify(
      { pass, noDoublePrefix, rootsAreHost, mountsInMenu, addressEditable, denyWorks, overflow, errors, previewTargets },
      null,
      2,
    ),
  );
  process.exit(pass ? 0 : 1);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
