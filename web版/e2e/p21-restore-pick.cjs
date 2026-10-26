/**
 * P21 恢复向导步骤 1「勾选反馈」真机取证（UI全量改造方案 v1.39）。
 *
 * 存在理由：本次改动是**纯 CSS 补齐**（`vtree__row--checked` / `--selected` 两个类名
 * 此前只挂不写规则）。jsdom 不算样式、vue-tsc 不看 CSS、vitest 只能断言类名存在——
 * 三道门全放行也照样可能「点了没反应」。故用真浏览器取**计算样式**实证，
 * 且**亮/暗两套主题各跑一遍**（颜色全走 --rw-* 令牌，两套值不同，必须分别确认可辨）：
 *
 *   A. 点文件名后该行拿到 `--checked` 类，且 **computed background-color 与未选行不同**
 *      （只看类名不够，必须看颜色真的变了）；
 *   B. 下方「已选清单」出现，条数/文本与所选项一致（路径为展示形态口路径）；
 *   C. 清单逐条取消（× 按钮）→ 该条消失；清空后回到「将恢复整个快照」提示；
 *   D. 多选两条 → 两行都高亮、清单两条（多选语义）；
 *   E. 全程 pageerror 为零。
 *
 * API 全 stub（page.route），无需后端；dev server 打 localhost:5173。
 * 用法：node p21-restore-pick.cjs [base]
 */
const path = require('node:path');
const { launch, outDir } = require('./harness.cjs');

const BASE = process.argv[2] || 'http://localhost:5173';
const OUT = outDir('p21-restore-pick');

const SNAPSHOT_ID = 'a'.repeat(64);

const SNAPSHOTS = [
  {
    id: SNAPSHOT_ID,
    short_id: 'aaaaaaa1',
    time: '2026-10-26T03:00:00Z',
    hostname: 'host-01',
    username: 'backup',
    tags: ['task-1'],
    paths: ['/sources/etc', '/sources/data'],
    size: 1048576,
  },
];

/** 根目录一层：两个目录（勾选目录 = 含子树，与真实语义一致）。 */
const ROOT_FILES = [
  { name: 'etc', type: 'dir', path: '/sources/etc', display_path: '/etc', size: null, mtime: null },
  { name: 'data', type: 'dir', path: '/sources/data', display_path: '/data', size: null, mtime: null },
];

const STUBS = {
  '/api/auth/me': { authenticated: true, must_change_password: false, user: 'root' },
  '/api/repos': [
    {
      id: 'default',
      name: '默认仓库',
      type: 'local',
      url: '/repo',
      host_tag: null,
      url_source: 'manual',
      host_tag_source: 'manual',
      passfile_status: 'ready',
      created_at: '2026-10-18T00:00:00Z',
    },
  ],
  '/api/system/runtime': {
    deploy_mode: 'docker',
    browse_root: '/sources',
    sources_root: '/sources',
    restore_root: '/restore',
    inplace_root: '/sources',
    deny_prefixes: [],
  },
  '/api/snapshots': { items: SNAPSHOTS, total: SNAPSHOTS.length, next_cursor: null },
};

async function runTheme(browser, theme) {
  const errors = [];
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  page.on('pageerror', (err) => errors.push(String(err)));
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text());
  });
  // 主题偏好落 localStorage（rw.theme，缺省 dark）——亮色必须显式设置
  await page.addInitScript((value) => localStorage.setItem('rw.theme', value), theme);

  // 只拦 /api/ 前缀（勿用 **/api/** 通配——会误伤 Vite 的 /src/api/*.ts 模块脚本）
  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (STUBS[pathname]) {
      await route.fulfill({ json: STUBS[pathname] });
      return;
    }
    // E18 文件树：任意快照的 /files
    if (/^\/api\/snapshots\/[^/]+\/files$/.test(pathname)) {
      await route.fulfill({ json: { snapshot_id: SNAPSHOT_ID, path: '/', items: ROOT_FILES, next_cursor: null } });
      return;
    }
    // 搜索端点（本探针不输入关键词，返回空集即可）
    if (/^\/api\/snapshots\/[^/]+\/search$/.test(pathname)) {
      await route.fulfill({
        json: { snapshot_id: SNAPSHOT_ID, query: '', items: [], total: 0, next_cursor: null },
      });
      return;
    }
    await route.fulfill({ json: {} });
  });

  await page.goto(`${BASE}/restore/${SNAPSHOT_ID}/select-files`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.vtree__row', { timeout: 15000 });
  await page.waitForTimeout(500);

  const checks = { theme };

  /** 行背景与左侧竖线（computed）：只看类名不够，必须看颜色真的变了。 */
  const styleOf = (index) =>
    page.evaluate((i) => {
      const rows = document.querySelectorAll('.vtree__row');
      if (!rows[i]) return { bg: 'missing', shadow: 'missing' };
      const cs = getComputedStyle(rows[i]);
      return { bg: cs.backgroundColor, shadow: cs.boxShadow };
    }, index);

  const before = await styleOf(0);
  // 点第 1 行名称（etc）→ 勾选
  await page.locator('.vtree__row').nth(0).locator('.vtree__name').click();
  await page.waitForTimeout(200);

  checks.checkedClass = await page.evaluate(
    () => document.querySelectorAll('.vtree__row')[0]?.className.includes('vtree__row--checked') ?? false,
  );
  const after = await styleOf(0);
  const other = await styleOf(1);
  checks.bgBefore = before.bg;
  checks.bgAfter = after.bg;
  checks.bgUnselected = other.bg;
  checks.accentBar = after.shadow;
  checks.bgChanged = before.bg !== after.bg;
  checks.bgDiffersFromUnselected = after.bg !== other.bg;
  checks.hasAccentBar = after.shadow !== 'none';
  checks.themeApplied = await page.evaluate(() => document.documentElement.classList.contains('rw-dark'));

  checks.picked = await page.evaluate(() => ({
    boxes: document.querySelectorAll('.restore__picked').length,
    items: Array.from(document.querySelectorAll('.restore__picked-path')).map((el) => el.textContent.trim()),
  }));
  await page.screenshot({ path: path.join(OUT, `one-selected-${theme}.png`), fullPage: true });

  // ---- 多选第二条 ----
  await page.locator('.vtree__row').nth(1).locator('.vtree__name').click();
  await page.waitForTimeout(200);
  checks.multi = await page.evaluate(() => ({
    checkedRows: document.querySelectorAll('.vtree__row--checked').length,
    items: Array.from(document.querySelectorAll('.restore__picked-path')).map((el) => el.textContent.trim()),
  }));
  await page.screenshot({ path: path.join(OUT, `two-selected-${theme}.png`), fullPage: true });

  // ---- 清单逐条取消：点第一条的 × ----
  await page.locator('.restore__picked-item').nth(0).locator('button').click();
  await page.waitForTimeout(200);
  checks.afterRemoveOne = await page.evaluate(() => ({
    items: Array.from(document.querySelectorAll('.restore__picked-path')).map((el) => el.textContent.trim()),
    checkedRows: document.querySelectorAll('.vtree__row--checked').length,
  }));

  // ---- 一键清空 ----
  await page.locator('.restore__picked button', { hasText: '清空' }).click();
  await page.waitForTimeout(200);
  checks.afterClear = await page.evaluate(() => ({
    pickedBoxes: document.querySelectorAll('.restore__picked').length,
    checkedRows: document.querySelectorAll('.vtree__row--checked').length,
    wholeHint: (document.querySelector('.restore__body')?.textContent ?? '').includes('将恢复整个快照'),
  }));
  await page.screenshot({ path: path.join(OUT, `cleared-${theme}.png`), fullPage: true });

  await page.close();

  const pass =
    checks.checkedClass === true &&
    checks.bgChanged === true &&
    checks.bgDiffersFromUnselected === true &&
    checks.hasAccentBar === true &&
    checks.themeApplied === (theme === 'dark') &&
    checks.picked.boxes === 1 &&
    checks.picked.items.length === 1 &&
    checks.picked.items[0] === '/etc' &&
    checks.multi.checkedRows === 2 &&
    checks.multi.items.length === 2 &&
    checks.afterRemoveOne.items.length === 1 &&
    checks.afterRemoveOne.checkedRows === 1 &&
    checks.afterClear.pickedBoxes === 0 &&
    checks.afterClear.checkedRows === 0 &&
    checks.afterClear.wholeHint === true &&
    errors.length === 0;

  return { theme, pass, checks, errors };
}

(async () => {
  const browser = await launch();
  const results = [];
  for (const theme of ['dark', 'light']) {
    results.push(await runTheme(browser, theme));
  }
  await browser.close();

  const pass = results.every((r) => r.pass);
  console.log(JSON.stringify({ base: BASE, pass, results }, null, 2));
  process.exit(pass ? 0 : 1);
})();
