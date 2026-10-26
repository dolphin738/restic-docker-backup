/**
 * 真机验收探针公共启动器（owner 2026-10-26 决定：验收探针收编入版本管理）。
 *
 * 为什么需要它：探针原先各自 `require` 一条本机绝对路径
 * （`D:/Program Files/Huawei/DevEco Studio/.../playwright-core`）——103 个旧脚本里
 * 81 个这么写，换机器/换人必挂。且它们不在版本库、不在任何清单里，坏了没人知道
 * （实证：`p13-wizard-probe` 在 P19 改掉备份源形态后失效一个多月才被发现）。
 *
 * playwright-core 解析顺序（第一个能用者生效）：
 *   1. 环境变量 `PLAYWRIGHT_CORE`（目录或包入口路径）
 *   2. `frontend/node_modules/playwright-core`（若将来加为 devDependency）
 *   3. 本机已知安装：DevEco Studio 自带 node 里的那份（Windows 开发机实测）
 *   4. `require('playwright-core')` 兜底（全局安装 / 其他宿主）
 *
 * 浏览器 channel 解析顺序：`E2E_CHANNEL` 环境变量 → msedge → chrome →
 * playwright 自带 chromium。探针里因此**不要再写死** `channel: 'msedge'`。
 *
 * 用法：
 *   const { launch, outDir } = require('./harness.cjs');
 *   const OUT = outDir('p13-probe');        // → web版/.e2e/p13-probe（产物不进库）
 *   const browser = await launch();
 *
 * 前置条件：前端 dev server 已起（默认 http://localhost:5173，探针支持 argv[2] 覆盖）。
 */
'use strict';

const fs = require('node:fs');
const path = require('node:path');

/** 本文件目录 = web版/e2e */
const E2E_DIR = __dirname;
/** web版/ */
const WEB_DIR = path.resolve(E2E_DIR, '..');
/** 截图/JSON 产物根：web版/.e2e（.gitignore:104 忽略；探针源码进库、产物不进库） */
const ARTIFACT_ROOT = path.join(WEB_DIR, '.e2e');

const KNOWN_INSTALLS = [
  // Windows 开发机：DevEco Studio 自带 node 里有一份 playwright-core
  'D:/Program Files/Huawei/DevEco Studio/tools/node/node_modules/@playwright/cli/node_modules/playwright-core',
  'C:/Program Files/Huawei/DevEco Studio/tools/node/node_modules/@playwright/cli/node_modules/playwright-core',
];

function candidatePaths() {
  const list = [];
  if (process.env.PLAYWRIGHT_CORE) {
    list.push(process.env.PLAYWRIGHT_CORE);
  }
  list.push(path.join(WEB_DIR, 'frontend', 'node_modules', 'playwright-core'));
  list.push(...KNOWN_INSTALLS);
  list.push('playwright-core');
  return list;
}

function loadChromium() {
  const tried = [];
  for (const candidate of candidatePaths()) {
    try {
      // eslint-disable-next-line global-require, import/no-dynamic-require
      const mod = require(candidate);
      const chromium = mod.chromium || (mod.default && mod.default.chromium);
      if (chromium) {
        return chromium;
      }
      tried.push(`${candidate} → 无 chromium 导出`);
    } catch (error) {
      tried.push(`${candidate} → ${error.code || error.message}`);
    }
  }
  throw new Error(
    [
      'playwright-core 未找到。任选其一修复：',
      '  1) export PLAYWRIGHT_CORE=/path/to/playwright-core',
      '  2) 在 web版/frontend 下 pnpm add -D playwright-core',
      '',
      '已尝试：',
      ...tried.map((line) => `  - ${line}`),
    ].join('\n'),
  );
}

const chromium = loadChromium();

/** 产物目录（自动创建）。name 建议与探针同名，如 'p13-probe'。 */
function outDir(name) {
  const dir = path.join(ARTIFACT_ROOT, name);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

/**
 * 启动浏览器（headless）。不写死 channel：优先 msedge/chrome，
 * 都不可用时退回 playwright 自带 chromium，避免 Linux/macOS 直接失败。
 */
async function launch(options = {}) {
  const { channel, ...rest } = options;
  const wanted = channel || process.env.E2E_CHANNEL;
  const channels = wanted ? [wanted] : ['msedge', 'chrome', null];

  const failures = [];
  for (const candidate of channels) {
    try {
      return await chromium.launch({
        ...(candidate ? { channel: candidate } : {}),
        headless: true,
        ...rest,
      });
    } catch (error) {
      failures.push(`${candidate || 'bundled chromium'} → ${error.message.split('\n')[0]}`);
    }
  }
  throw new Error(['浏览器启动失败。', ...failures.map((line) => `  - ${line}`)].join('\n'));
}

module.exports = { chromium, launch, outDir, ARTIFACT_ROOT, WEB_DIR, E2E_DIR };
