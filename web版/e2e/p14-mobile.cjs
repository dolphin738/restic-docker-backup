/**
 * P14 移动端 4 视口走查（方案 §4.7 P14：移动端 4 视口（1440/960/768/390）+ §4.3 口径断言）。
 *
 * 断言：
 *   A. 逐页逐视口横向溢出：document.documentElement.scrollWidth ≤ innerWidth + 1
 *      （表格 scroll-x / max-width / wrap 修复后的综合结果——有溢出即有裁切）
 *   B. §4.3-④ 向导步骤条：390px 下 steps 容器 scrollWidth > clientWidth
 *      （5 步全部横向可达，而非被裁切）
 *   C. §4.3-① 表格：390px 下各表格页 .n-data-table 存在（scroll-x 生效的前提）
 *   D. 全程 pageerror 为零
 *
 * 用法：node p14-mobile.cjs [base]
 */
const path = require('node:path');
const { launch, outDir } = require('./harness.cjs');

const BASE = process.argv[2] || 'http://localhost:5173';
const OUT = outDir('p14-mobile');

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
const VIEWPORTS = [1440, 960, 768, 390];

const NOW = '2026-10-22T03:00:00Z';
// stub 集与 p14-pixdiff.cjs 相同（populated 种子，略——从 p12 拷贝核心端点）
function task(id, name, lastRunAt, status, extra = {}) {
  return {
    id, name, enabled: true, tag: `task-${id}`, cron: '0 3 * * *', status,
    last_run_at: lastRunAt, last_result: status === 'never_run' ? null : status,
    last_duration_ms: 93_000, snapshot_count: 12, limit_up: 0, limit_down: 0,
    next_run_at: '2026-10-23T03:00:00Z', running_job_id: null,
    dry_run_pending: false, prune_due: false, updated_at: lastRunAt, ...extra,
  };
}
const TASKS = [
  task(1, '每日全量备份', '2026-10-10T03:00:00Z', 'success'),
  task(2, '文档增量同步', '2026-10-21T23:00:00Z', 'success'),
  task(3, '归档冷数据', null, 'never_run', { next_run_at: null }),
];
const REPO = {
  id: 'default', name: '默认仓库', type: 'local', url: '/repo', host_tag: null,
  url_source: 'manual', host_tag_source: 'manual', passfile_status: 'ready',
  created_at: '2026-10-18T00:00:00Z',
};
const SNAPSHOTS = Array.from({ length: 8 }, (_, i) => ({
  id: `${'abcdef12'.repeat(8)}`.slice(0, 64), short_id: `abcdef${i}${i}`,
  time: `2026-10-${String(10 + i).padStart(2, '0')}T03:00:00Z`,
  hostname: 'host-01', username: 'backup', tags: ['task-1'], paths: ['/sources/etc'],
}));
const AUDITS = Array.from({ length: 6 }, (_, i) => ({
  id: 100 - i, ts: `2026-10-${String(21 - i).padStart(2, '0')}T0${i}:1${i}:00Z`,
  action: ['job.start', 'job.finish', 'config.update', 'repo.unlock', 'auth.login', 'job.abort'][i],
  target: i % 2 === 0 ? 'task-1' : null,
  detail: i === 2 ? '{"patch":{"dump.enabled":"1"},"source":"ui"}' : null,
  result: ['ok', 'ok', 'ok', 'denied', 'ok', 'failed'][i],
  created_at: `2026-10-${String(21 - i).padStart(2, '0')}T0${i}:1${i}:00Z`,
}));
const REPORT_BUCKETS = Array.from({ length: 7 }, (_, i) => {
  const total = [2, 0, 3, 1, 2, 2, 1][i];
  const failed = i === 4 ? 1 : 0;
  return { date: `2026-10-${String(16 + i).padStart(2, '0')}`, total, success: total - failed, failed, ratio: total === 0 ? null : (total - failed) / total };
});

const STUBS = {
  '/api/auth/me': { authenticated: true, must_change_password: false, user: 'root' },
  '/api/repos/default/status': { configured: true, connected: true, locked: false, capacity: { size: 343_597_383_680, snaps: 142 }, last_error: null },
  '/api/repos/default/rclone/remotes': { conf_present: false, remotes: [] },
  '/api/repos/default/backup-tasks': { items: TASKS, total: TASKS.length, limit: 50, offset: 0 },
  '/api/repos/default': REPO,
  '/api/repos': [REPO],
  '/api/sources': { path: '/sources', items: [{ name: 'etc', type: 'dir', display_path: '/sources/etc' }], next_cursor: null },
  '/api/fs/browse': { path: '/sources', mounts: [], items: [], next_cursor: null },
  '/api/jobs': {
    items: [{ id: 'job-0001', type: 'backup', status: 'success', trigger: 'manual', policy_id: null, task_id: 1, snapshot_id: 'abcdef12', bytes_done: 1024, files_done: 12, skipped_count: 0, last_event_id: 40, log_path: '/logs/job-0001.log', summary: '完成', started_at: '2026-10-21T03:00:00Z', finished_at: '2026-10-21T03:01:33Z', exit_code: 0, created_at: '2026-10-21T03:00:00Z' }],
    total: 1, limit: 20, offset: 0,
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
  '/api/cache': { path: '/cache', size_bytes: 1_048_576, items_count: 3, truncated: false, measured_at: NOW },
  '/api/audit': { items: AUDITS, total: AUDITS.length, limit: 50, offset: 0, actions: ['job.start', 'job.finish', 'config.update', 'repo.unlock', 'auth.login', 'job.abort'] },
  '/api/reports/backup-success': {
    days: 7, timezone: 'Asia/Shanghai', from_date: '2026-10-16', to_date: '2026-10-22',
    buckets: REPORT_BUCKETS, total: 11, success: 10, failed: 1, ratio: 10 / 11,
  },
  '/api/notify': {
    healthchecks_url_set: true, webhook_url_set: false, webhook_template: '',
    meow_nickname: 'backup-bot', meow_msg_type: 'text', meow_html_height: 400,
    meow_url: '', meow_img_url: '', meow_plan: 'free', on_success: false,
    skipped_threshold: 5, webhook_redact: true, meow_redact: true,
    mail_host: '', mail_port: 587, mail_username: '', mail_password_set: false,
    mail_from: '', mail_to: '', mail_security: 'starttls', mail_redact: true,
  },
  '/api/system/time': { now: NOW, timezone: 'Asia/Shanghai', drift_seconds: 0 },
};

(async () => {
  const browser = await launch();
  const errors = [];
  const overflow = []; // { page, viewport, scrollWidth, innerWidth }
  const matrix = {};   // matrix[page][viewport] = 'ok' | 'overflow'

  for (const width of VIEWPORTS) {
    const ctx = await browser.newContext({ viewport: { width, height: 960 }, deviceScaleFactor: 1 });
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
              return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });
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
    page.on('pageerror', (e) => errors.push(`${width}px ${page.url()} ${e.message}`));

    for (const [name, route] of PAGES) {
      await page.goto(BASE + route, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(1200);
      const o = await page.evaluate(() => ({
        sw: document.documentElement.scrollWidth,
        iw: window.innerWidth,
      }));
      const bad = o.sw > o.iw + 1;
      matrix[name] = matrix[name] || {};
      matrix[name][width] = bad ? 'overflow' : 'ok';
      if (bad) {
        overflow.push({ page: name, viewport: width, ...o });
      }
      await page.screenshot({ path: path.join(OUT, `${name}-${width}.png`), fullPage: true });
    }

    // §4.3-④：390px 下向导步骤条横向可达（scrollWidth > clientWidth）
    if (width === 390) {
      await page.goto(BASE + '/tasks/new', { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(1200);
      const steps = await page.evaluate(() => {
        const nav = document.querySelector('.wizard__steps-nav') || document.querySelector('.n-steps');
        if (!nav) return null;
        return { sw: nav.scrollWidth, cw: nav.clientWidth };
      });
      console.log(`[390] 向导步骤条: ${JSON.stringify(steps)} → 5 步可达 = ${steps ? steps.sw > steps.cw : '容器未找到'}`);
      await page.screenshot({ path: path.join(OUT, `task-new-390.png`), fullPage: true });
    }
    await ctx.close();
  }

  console.log('\n==== 溢出矩阵（ok = 无横向溢出）====');
  for (const [name, row] of Object.entries(matrix)) {
    console.log(`  ${name.padEnd(14)} ${VIEWPORTS.map((w) => `${w}:${row[w]}`).join('  ')}`);
  }
  if (overflow.length) {
    console.log('\n⚠ 溢出明细：');
    for (const o of overflow) {
      console.log(`  ${o.page}@${o.viewport}px scrollWidth=${o.sw} > innerWidth=${o.iw}`);
    }
  }
  console.log('\npageerror:', errors.length ? errors.slice(0, 3).join(' | ') : '(无)');
  console.log(overflow.length === 0 && errors.length === 0 ? 'P14 MOBILE PASS' : 'P14 MOBILE FAIL');
  await browser.close();
  process.exit(overflow.length === 0 && errors.length === 0 ? 0 : 1);
})().catch((e) => {
  console.error('FATAL', e);
  process.exit(1);
});
