<script setup lang="ts">
/** E31 dry-run 扫描范围预览（任务向导第⑥步）：汇总栏 + 目录树（owner 2026-11-03 裁决）。
 *
 * 为什么改：原来把 `items` 的前 200 条渲染成一墙 `n-tag`（只渲染 200、计数却写全量），
 * 读不出「我到底备进去了什么」。现在：
 *   ① 汇总栏 4 个数字（备份源 / 目录 / 文件 / 是否截断）——第一屏回答「范围选对没」；
 *   ② 目录树逐条可核对具体路径，单链目录压缩成一行（`X11/Xsession.d`），展开由用户点。
 *
 * 复用而非重造：
 *   - 树的交互 / 虚拟滚动 / 加载态 / 错误态 / 空态一律走共用件 `VirtualTree`
 *     （快照页、恢复选文件、源选择器同款），本组件只供数据；
 *   - 图标取 `components/icons` 既有资产；
 *   - 数字排版走 `n-statistic`，不在本文件手写大字号类；
 *   - 树视口**只给上限**（复用 `sourceTreeViewMaxHeight`，与源选择器同档）：内容驱动高度，
 *     只备了 2 个文件时不会留下 350px 的空白盒子；超过上限才内部滚动。
 *
 * ⚠️ 首跑确认门（`dryRunGate`）与本组件无关：这里只换展示形状，error/truncated/stale 判定不变。
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { NAlert, NIcon, NStatistic, NSpace, NTag, NText } from 'naive-ui'
import type { DryRunResponse } from '@/api/types'
import VirtualTree, { type TreePage } from '@/components/VirtualTree.vue'
import { IconSearch } from '@/components/icons/icons'
import { sourceTreeViewMaxHeight } from '@/theme/tokens'
import {
  buildScanTree,
  findScanNode,
  scanScopeStats,
  scanTreePage,
  scanTreeRootPath,
} from './scanScopeTree'

const props = defineProps<{
  /** E31 / E36 dry-run 响应。 */
  result: DryRunResponse
  /** 本次预览的备份源（展示形态）——汇总栏与树根都按它收窄。 */
  sources: string[]
}>()

const { t } = useI18n()

const root = computed(() => buildScanTree(props.result.items))
const stats = computed(() => scanScopeStats(root.value, props.sources))

/** 树根：单源直接用该源；源在条目里不存在（空目录 / 已被排除干净）时退回 '/'，
 *  否则 VirtualTree 拿一个取不到的根，界面是一片空白且没有任何提示。 */
const treeRootPath = computed(() => {
  const preferred = scanTreeRootPath(props.sources)
  return findScanNode(root.value, preferred) ? preferred : '/'
})

function treeLoader(path: string): Promise<TreePage> {
  return Promise.resolve(
    scanTreePage(root.value, path, ({ dirs, files }) => t('scanScope.nodeMeta', { dirs, files })),
  )
}
</script>

<template>
  <div class="scan-scope">
    <!-- ① 汇总栏：先给结论，再看细节 -->
    <n-space class="scan-scope__stats" :size="32">
      <n-statistic :label="t('scanScope.sources')" :value="stats.sources" />
      <n-statistic :label="t('scanScope.dirs')" :value="stats.dirs" />
      <n-statistic :label="t('scanScope.files')" :value="stats.files" />
      <n-statistic
        :label="t('scanScope.truncated')"
        :value="result.truncated ? t('scanScope.truncatedYes') : t('scanScope.truncatedNo')"
      />
    </n-space>

    <!-- ② 异常态排在树之前：避免用户在残缺范围上做判断 -->
    <n-alert v-if="result.error" type="error" :show-icon="false">{{ result.error }}</n-alert>
    <n-alert v-if="result.truncated" type="warning" :show-icon="false">
      {{ t('wizard.dry_run_truncated') }}
    </n-alert>
    <n-alert
      v-if="result.warnings.length"
      type="warning"
      :title="t('wizard.dry_run_pseudo')"
      :show-icon="false"
    >
      <n-space vertical :size="4">
        <n-text v-for="warning in result.warnings" :key="warning.path" depth="3">
          {{ warning.path }}
        </n-text>
      </n-space>
    </n-alert>

    <!-- ③ 目录树：单链压缩 + 虚拟滚动 + 逐层加载（均在共用件内）。
         单源时树根即源本身，树里不再出现源那一行——此处补出源路径，否则看不出在看哪棵树。 -->
    <n-text depth="3" class="scan-scope__root">{{ t('scanScope.root', { path: treeRootPath }) }}</n-text>
    <virtual-tree
      v-if="result.items.length > 0"
      :root-path="treeRootPath"
      :load-children="treeLoader"
      :max-height="sourceTreeViewMaxHeight"
    />
    <n-text v-else depth="3" class="scan-scope__empty">{{ t('scanScope.empty') }}</n-text>

    <n-space align="center" :size="8" class="scan-scope__foot">
      <n-icon :size="16"><IconSearch /></n-icon>
      <n-text depth="3">{{ t('wizard.dry_run_count', { n: stats.files + stats.dirs }) }}</n-text>
      <n-tag v-if="result.truncated" size="small" type="warning">{{ t('scanScope.partial') }}</n-tag>
    </n-space>
  </div>
</template>

<style scoped>
/* 颜色/间距/圆角一律走 theme/skin.css 令牌（--rw-*，亮暗双套），本文件零硬编码色值。 */
.scan-scope {
  display: flex;
  flex-direction: column;
  gap: var(--rw-space-2);
}

/* 汇总栏：数字等宽，数值变化时列宽不跳动 */
.scan-scope__stats {
  font-variant-numeric: tabular-nums;
}

.scan-scope__root {
  font-family: var(--font-family-mono);
}

.scan-scope__empty {
  display: block;
  padding: var(--rw-space-4);
  border: 1px dashed var(--rw-border);
  border-radius: var(--rw-radius-control);
}

.scan-scope__foot {
  margin-top: var(--rw-space-1);
}
</style>
