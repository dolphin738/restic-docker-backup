/**
 * P20 步骤条对比度探针（暗/亮双主题实测）。
 *
 * 目的：取证「暗色主题下恢复向导步骤文案看不清」的真实成因——不读代码猜，直接量
 * `getComputedStyle` 的 color / opacity / background-color，并按 WCAG 2.1 算对比度
 * （文字色按自身 alpha 合成到最近的不透明祖先背景上，再把元素 opacity 计入）。
 *
 * API 全 stub（`/api/` 前缀谓词，勿用 **\/api\/** 通配——会误伤 Vite 模块）。
 * 用法：node p20-stepper-contrast.cjs [base]
 */
const path = require('node:path');
const fs = require('node:fs');
const { launch, outDir } = require('./harness.cjs');

const BASE = process.argv[2] || 'http://localhost:5173';
const OUT = outDir('p20-contrast');

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
  '/api/system/runtime': {
    deploy_mode: 'host',
    browse_root: '/',
    sources_root: '/sources',
    restore_root: '/',
    inplace_root: '/',
    deny_prefixes: ['/repo'],
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
  '/api/fs/browse': { path: '/', mounts: ['/home', '/srv'], items: [], next_cursor: null },
  '/api/restore/preview': {
    items: [], overwrite_count: 0, total_bytes: 0, truncated: false, next_cursor: null,
  },
};

/** 注入页内的对比度计算（在浏览器上下文执行）。 */
const MEASURE = `(() => {
  function parse(c) {
    const m = String(c).match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const p = m[1].split(',').map((s) => parseFloat(s.trim()));
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  }
  function over(fg, bg) {
    const a = fg.a;
    return { r: fg.r * a + bg.r * (1 - a), g: fg.g * a + bg.g * (1 - a), b: fg.b * a + bg.b * (1 - a), a: 1 };
  }
  function lum(c) {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  }
  function ratio(a, b) {
    const l1 = lum(a), l2 = lum(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  }
  function effBg(el) {
    let node = el;
    while (node && node !== document.documentElement) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg && bg.a > 0.99) return bg;
      node = node.parentElement;
    }
    return parse(getComputedStyle(document.body).backgroundColor) || { r: 20, g: 22, b: 28, a: 1 };
  }
  /** 元素链上的累计 opacity（祖先 opacity 会再次压低实际对比度）。 */
  function effOpacity(el) {
    let o = 1, node = el;
    while (node && node !== document.documentElement) {
      o *= parseFloat(getComputedStyle(node).opacity || '1');
      node = node.parentElement;
    }
    return o;
  }

  const btns = [...document.querySelectorAll('.wizard-stepper__btn')];
  const out = btns.map((btn) => {
    const title = btn.querySelector('.wizard-stepper__title');
    const index = btn.querySelector('.wizard-stepper__index');
    if (!title || !index) return { text: '<no title/index>', skipped: true };
    const bg = effBg(btn);
    const tColor = parse(getComputedStyle(title).color);
    const iColor = parse(getComputedStyle(index).color);
    const iBg = parse(getComputedStyle(index).backgroundColor);
    const op = effOpacity(btn);
    // 文字合成：color(带 alpha) over 祖先底色 → 再按 opacity 压向底色
    const tSolid = over(tColor, bg);
    const tFinal = { r: tSolid.r * op + bg.r * (1 - op), g: tSolid.g * op + bg.g * (1 - op), b: tSolid.b * op + bg.b * (1 - op), a: 1 };
    const iSolid = over(iColor, over(iBg, bg));
    const iFinal = { r: iSolid.r * op + bg.r * (1 - op), g: iSolid.g * op + bg.g * (1 - op), b: iSolid.b * op + bg.b * (1 - op), a: 1 };
    return {
      text: title.textContent.trim(),
      cls: btn.className.replace('restore-stepper__btn', '').trim(),
      opacity: Number(op.toFixed(2)),
      titleColor: getComputedStyle(title).color,
      titleContrast: Number(ratio(tFinal, bg).toFixed(2)),
      indexColor: getComputedStyle(index).color,
      indexBg: getComputedStyle(index).backgroundColor,
      indexContrast: Number(ratio(iFinal, over(iBg, bg)).toFixed(2)),
      pageBg: 'rgb(' + Math.round(bg.r) + ',' + Math.round(bg.g) + ',' + Math.round(bg.b) + ')',
    };
  });
  return { bodyColor: getComputedStyle(document.body).color, steps: out };
})()`;

(async () => {
  const browser = await launch();
  const errors = [];
  const page = await browser.newPage();
  page.on('pageerror', (err) => errors.push(String(err)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const stub = STUBS[new URL(route.request().url()).pathname];
    await route.fulfill(stub ? { json: stub } : { json: {} });
  });

  const report = {};
  // 四个向导面：恢复向导 / 任务向导 / 首启引导 / 三步确认门（P20 统一后应同款）
  const TARGETS = [
    { key: 'restore', url: `/restore/${SNAPSHOT_ID}/select-files`, sel: '.wizard-stepper__btn' },
    { key: 'taskWizard', url: '/tasks/new', sel: '.wizard-stepper__btn' },
    { key: 'bootstrap', url: '/bootstrap', sel: '.wizard-stepper__btn' },
  ];
  for (const theme of ['dark', 'light']) {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
    await page.evaluate((t) => localStorage.setItem('rw.theme', t), theme);
    report[theme] = {};
    for (const target of TARGETS) {
      await page.goto(`${BASE}${target.url}`, { waitUntil: 'networkidle' });
      const found = await page
        .waitForSelector(target.sel, { timeout: 8000 })
        .then(() => true)
        .catch(() => false);
      if (!found) {
        report[theme][target.key] = { skipped: `未渲染 ${target.sel}（路由守卫重定向或页面未就绪）` };
        continue;
      }
      await page.waitForTimeout(400);
      report[theme][target.key] = await page.evaluate(MEASURE);
      await page.screenshot({ path: path.join(OUT, `${target.key}-${theme}.png`) });
    }
  }

  await browser.close();
  console.log(JSON.stringify({ errors, ...report }, null, 2));
  fs.writeFileSync(path.join(OUT, 'p20-contrast.json'), JSON.stringify({ errors, ...report }, null, 2));

  // 结论口径：WCAG AA 普通文本 ≥ 4.5；步骤条徽标数字属小号文本，同样按 4.5 判定。
  const bad = [];
  for (const theme of ['dark', 'light']) {
    for (const [page_, data] of Object.entries(report[theme])) {
      if (data.skipped) {
        console.log(`[skip] ${theme}/${page_}: ${data.skipped}`);
        continue;
      }
      for (const s of data.steps) {
        if (s.titleContrast < 4.5) bad.push(`${theme}/${page_} 标题「${s.text}」= ${s.titleContrast}`);
        if (s.indexContrast < 4.5) bad.push(`${theme}/${page_} 徽标「${s.text}」= ${s.indexContrast}`);
      }
    }
  }
  console.log('\n低于 AA 4.5 的项：', bad.length ? bad : '无');
})();
