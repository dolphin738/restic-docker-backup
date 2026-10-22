/**
 * P5 像素 diff 截图器（方案 §4.7 P5 验收门：改前基线存档 → 改后逐页 diff + 亮/暗双模式）。
 *
 * 用法：
 *   node p5-pixdiff.cjs --base http://localhost:5174 --out p5-base --theme light
 *   node p5-pixdiff.cjs --base http://localhost:5174 --out p5-base --theme dark
 *
 * 全站 /api 走 addInitScript 注入的 deterministic stub（同一份种子数据 → 两版截图可比），
 * 不依赖真实后端与登录态；因此可以在 P4 与 P5 两个 worktree 上跑出可对齐的截图。
 *
 * 页面清单覆盖 P5 令牌层影响面（全站）：仪表盘 / 仓库 / 设置 / 任务历史 / 快照 / 恢复 / 审计 / 通知。
 */
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require(
  'D:/Program Files/Huawei/DevEco Studio/tools/node/node_modules/@playwright/cli/node_modules/playwright-core',
);

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : fallback;
}

const BASE = arg('base', 'http://localhost:5173');
// 脚本随仓库入库于 scripts/，产物统一落 .e2e/（gitignore，避免截图污染源码目录）
const OUT = path.resolve(__dirname, '..', '.e2e', arg('out', 'p5-shots'));
const THEME = arg('theme', 'light');

const PAGES = [
  ['dashboard', '/'],
  ['repo', '/repo'],
  ['settings', '/settings'],
  ['jobs', '/jobs'],
  ['snapshots', '/snapshots'],
  ['restore', '/restore'],
  ['audit', '/audit'],
  ['notifications', '/notifications'],
];

const NOW = '2026-10-22T03:00:00Z';

function task(id, name, lastRunAt, status, extra = {}) {
  return {
    id,
    name,
    enabled: true,
    tag: `task-${id}`,
    cron: '0 3 * * *',
    status,
    last_run_at: lastRunAt,
    last_result: status === 'never_run' ? null : status,
    last_duration_ms: 93_000,
    snapshot_count: 12,
    limit_up: 0,
    limit_down: 0,
    next_run_at: '2026-10-23T03:00:00Z',
    running_job_id: null,
    dry_run_pending: false,
    prune_due: false,
    updated_at: lastRunAt,
    ...extra,
  };
}

const TASKS = [
  task(1, '每日全量备份', '2026-10-10T03:00:00Z', 'success'),
  task(2, '文档增量同步', '2026-10-21T23:00:00Z', 'success'),
  task(3, '归档冷数据', null, 'never_run', { next_run_at: null }),
];

const SNAPSHOTS = Array.from({ length: 8 }, (_, i) => ({
  id: `${'abcdef12'.repeat(8)}`.slice(0, 64),
  short_id: `abcdef${i}${i}`,
  time: `2026-10-${String(10 + i).padStart(2, '0')}T03:00:00Z`,
  hostname: 'host-01',
  username: 'backup',
  tags: ['task-1'],
  paths: ['/sources/etc'],
}));

const STUBS = {
  '/api/auth/me': { authenticated: true, must_change_password: false, user: 'root' },
  '/api/repos/default/status': {
    configured: true,
    connected: true,
    locked: false,
    capacity: { size: 343_597_383_680, snaps: 142 },
    last_error: null,
  },
  '/api/repos/default/backup-tasks': { items: TASKS, total: TASKS.length, limit: 50, offset: 0 },
  '/api/repos/default': {
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
  '/api/jobs': {
    items: [
      {
        id: 'job-0001',
        type: 'backup',
        status: 'success',
        trigger: 'manual',
        policy_id: null,
        task_id: 1,
        snapshot_id: 'abcdef12',
        bytes_done: 1024,
        files_done: 12,
        skipped_count: 0,
        last_event_id: 40,
        log_path: '/logs/job-0001.log',
        summary: '完成',
        started_at: '2026-10-21T03:00:00Z',
        finished_at: '2026-10-21T03:01:33Z',
        exit_code: 0,
        created_at: '2026-10-21T03:00:00Z',
      },
    ],
    total: 1,
    limit: 20,
    offset: 0,
  },
  '/api/snapshots': { items: SNAPSHOTS, total: SNAPSHOTS.length, next_cursor: null },
  '/api/config': {
    passfile_status: 'configured',
    items: [
      { key: 'dump.enabled', value: '1', source: 'ui', has_value: true },
      { key: 'dump.type', value: 'pg', source: 'ui', has_value: true },
      { key: 'dump.host', value: 'postgres', source: 'ui', has_value: true },
      { key: 'dump.port', value: '5432', source: 'ui', has_value: true },
      { key: 'dump.user', value: 'restic', source: 'ui', has_value: true },
      { key: 'dump.databases', value: '["app","audit"]', source: 'ui', has_value: true },
      { key: 'dump.retention_days', value: '7', source: 'ui', has_value: true },
      { key: 'dump.password', value: null, source: 'ui', has_value: true },
      { key: 'behavior.auto_init', value: '0', source: 'ui', has_value: true },
      { key: 'behavior.auto_unlock', value: '0', source: 'ui', has_value: true },
      { key: 'behavior.check_enabled', value: '1', source: 'ui', has_value: true },
      { key: 'behavior.check_sample_ratio', value: '0.02', source: 'ui', has_value: true },
      { key: 'behavior.monthly_full_day', value: '1', source: 'ui', has_value: true },
      { key: 'security.cred_encryption', value: '1', source: 'ui', has_value: true },
      { key: 'metrics.token', value: null, source: 'ui', has_value: true },
      { key: 'timezone', value: 'Asia/Shanghai', source: 'ui', has_value: true },
    ],
  },
  '/api/cache': {
    path: '/cache',
    size_bytes: 1_048_576,
    items_count: 3,
    truncated: false,
    measured_at: NOW,
  },
  '/api/audit': { items: [], total: 0, limit: 50, offset: 0 },
  '/api/fs/browse': { path: '/sources', mounts: [], items: [], next_cursor: null },
  '/api/system/time': { now: NOW, timezone: 'Asia/Shanghai', drift_seconds: 0 },
};

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 });

  await ctx.addInitScript(
    ({ stubs, theme }) => {
      try {
        localStorage.setItem('rw.theme', theme);
        localStorage.setItem('rw.locale', 'zh-CN');
        localStorage.setItem('dashboard:viewMode', 'list');
        localStorage.setItem('dashboard:timeType', 'actual');
      } catch {
        /* ignore */
      }
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
      // 关掉动画与过渡：截图只比静态形态（count-up / fade 会让两次截图不可比）
      const style = document.createElement('style');
      style.textContent = '*,*::before,*::after{animation:none !important;transition:none !important}';
      document.addEventListener('DOMContentLoaded', () => document.head.appendChild(style));
    },
    { stubs: STUBS, theme: THEME },
  );

  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e.message)));

  const manifest = [];
  for (const [name, route] of PAGES) {
    await page.goto(BASE + route, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500); // 等 count-up 收敛 + 数据渲染
    const file = path.join(OUT, `${name}.png`);
    await page.screenshot({ path: file, fullPage: true });
    const size = fs.statSync(file).size;
    manifest.push({ page: name, route, file: `${name}.png`, bytes: size });
    console.log(`  ✓ ${name.padEnd(14)} ${route.padEnd(16)} ${(size / 1024).toFixed(1)} KiB`);
  }

  fs.writeFileSync(
    path.join(OUT, 'manifest.json'),
    JSON.stringify({ base: BASE, theme: THEME, capturedAt: new Date().toISOString(), pages: manifest, pageErrors: errors }, null, 2),
  );
  if (errors.length) {
    console.log(`\n  ⚠ pageerror ${errors.length} 条：${errors.slice(0, 3).join(' | ')}`);
  }
  await browser.close();
})();
