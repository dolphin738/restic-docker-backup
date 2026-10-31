<script setup lang="ts">
/**
 * 系统设置页（页面 11）——**纯外壳**：分段导航 + 深链 + 分区挂载。
 *
 * 布局（2026-10-21 改版）：顶部分段 Tab 切换分区 + 单面板渲染当前分区
 * （内容全宽，与全站单栏形态统一）。曾试过「左侧 NMenu 分区导航」，因与主导航
 * 并排成双竖导航被 owner 否决——勿回退该形态。深链保留：切换即
 * #sec-<id> replaceState，刷新 / 分享链接仍直达对应分区。
 *
 * 候选 6 拆分（2026-10-31）：八个分区各自成为 sections/SectionXxx.vue，
 * 每个分区自包含配置读取 / 回填 / 校验 / 保存（配置类走 useConfigPatch）。
 * 本文件从 1578 行收敛到约 200 行，只保留导航职责。
 *
 * 分区：DB dump → 行为开关 → 凭据加密 → Prometheus 指标 → 系统域
 * → 时区/NTP/时钟/对时 → 管理员改密 → 配置迁移。
 */

import { NEl } from 'naive-ui'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
// P6 形态层：分段控件为共用组件（S-4 单一实现）；深链（#sec-<id>）与未知 id
// 防御已内置于组件（hashPrefix=sec）。
import SegmentedTabs from '@/components/dashboard/SegmentedTabs.vue'
// 八个分区各自自包含（候选 6 拆分）：配置读取/回填/校验/保存都在分区内部，
// 本文件只负责「显示哪个分区」与深链。
import SectionBehavior from '@/pages/settings/sections/SectionBehavior.vue'
import SectionCredential from '@/pages/settings/sections/SectionCredential.vue'
import SectionDump from '@/pages/settings/sections/SectionDump.vue'
import SectionMetrics from '@/pages/settings/sections/SectionMetrics.vue'
import SectionPassword from '@/pages/settings/sections/SectionPassword.vue'
import SectionSystem from '@/pages/settings/sections/SectionSystem.vue'
import SectionTimezone from '@/pages/settings/sections/SectionTimezone.vue'
import SectionTransfer from '@/pages/settings/sections/SectionTransfer.vue'

const { t } = useI18n()




// ---------------------------------------------------------------- 分区导航（2026-10-21 改版：点选切换替代锚点滚动）

/** 区块 id 与卡片标题键一一对应（复用既有 title 键 → 语言切换自动跟随，零新增目录文案）。
 *  缓存维护（E25）已并入仓库页维护区，不再作为本页区块。 */
const SECTIONS = [
  { id: 'dump', title: 'settings.dump.title' },
  { id: 'behavior', title: 'settings.behavior.title' },
  { id: 'credential', title: 'settings.credential.title' },
  { id: 'metrics', title: 'settings.metrics.title' },
  { id: 'timezone', title: 'settings.timezone.title' },
  { id: 'system', title: 'settings.system.title' },
  { id: 'password', title: 'settings.password.title' },
  { id: 'transfer', title: 'settings.transfer.title' },
] as const

type SectionId = (typeof SECTIONS)[number]['id']

/** 目录项：文案经 computed 派生，保证切换语言后即时刷新。 */
const sectionLinks = computed(() =>
  SECTIONS.map((item) => ({ id: item.id, label: t(item.title) })),
)

const activeSection = ref<SectionId>('dump')

/** 分段切换（P6：SegmentedTabs 受控 emit；未知 id 防御与 #sec-<id> 深链
 *  均已内置于组件——这里只做联合类型收窄后赋值）。 */
function onSectionChange(id: string): void {
  activeSection.value = id as SectionId
}
</script>

<template>
  <n-el class="settings">
    <!-- 分区切换：顶部分段条（P6 提升为共用 SegmentedTabs；全断点统一形态；
         吸顶常驻，滚长表单时仍在视口）。按钮主/次态走 naive 主题内部配色（NButton），
         亮暗双主题一致；深链 #sec-<id> 与未知 id 防御内置（hashPrefix='sec'）。
         吸顶背景用 --n-color（根 n-el 提供 bodyColor，暗色不回落白条）。 -->
    <div class="settings__segments">
      <SegmentedTabs
        :items="sectionLinks"
        :model-value="activeSection"
        hash-prefix="sec"
        :aria-label="t('settings.sectionsAria')"
        @update:model-value="onSectionChange"
      />
    </div>

    <!-- DB dump（批次 B 实装：dump.* 八键 + 密码三态） -->
    <!-- DB dump（候选 6 拆分：SectionDump 自包含） -->
    <SectionDump v-if="activeSection === 'dump'" />

    <!-- 行为开关（候选 6 拆分：SectionBehavior 自包含） -->
    <SectionBehavior v-if="activeSection === 'behavior'" />

    <!-- 凭据加密（候选 6 拆分：SectionCredential 自包含） -->
    <SectionCredential v-if="activeSection === 'credential'" />

    <!-- Prometheus 指标（候选 6 拆分：SectionMetrics 自包含） -->
    <SectionMetrics v-if="activeSection === 'metrics'" />

    <!-- 系统域（候选 6 拆分：SectionSystem 自包含） -->
    <SectionSystem v-if="activeSection === 'system'" />

    <!-- 时区 / NTP / 时钟 / 对时（候选 6 拆分：SectionTimezone 自包含） -->
    <SectionTimezone v-if="activeSection === 'timezone'" />

    <!-- 管理员改密（候选 6 拆分：SectionPassword 自包含） -->
    <SectionPassword v-if="activeSection === 'password'" />

    <!-- 配置迁移（候选 6 拆分：SectionTransfer 自包含） -->
    <SectionTransfer v-if="activeSection === 'transfer'" />


    <!-- 批次 E 配置迁移：导出（口令加密凭据段）+ 导入（跳过/覆盖 + 报告） -->


  </n-el>
</template>

<style scoped>
/* 布局（2026-10-21 改版，替代 P4 锚点长页）：顶部分段 Tab + 单面板，内容全宽。
   曾用「左 NMenu 分区导航 + 右内容」双栏，因与主导航并排成双竖导航被否决
   （见文件头）——勿回退双栏形态。根 n-el 提供 --n-color（naive 把 bodyColor
   暴露在自渲染元素上），分段条吸顶背景随主题，暗色不回落白条。 */

/* 分区切换分段条：全断点统一形态；吸顶常驻（滚长表单时仍在视口），
   窄屏横向滚动不换行（P6 起由共用 SegmentedTabs 的 .rw-segmented 承担）。
   按钮主/次态由 NButton 主题内部配色。 */
.settings__segments {
  position: sticky;
  top: 0;
  z-index: 3;
  margin: 0 0 16px;
  padding: 8px 0;
  background: var(--n-color, #fff);
}

/* 内容面板：仅渲染当前分区；分区卡片带该类，切换（v-if 换卡）即重放入场动画。
   尊重系统减少动效偏好（WCAG 2.3.3）。 */
.settings__panel {
  animation: settings-panel-in 0.16s ease-out;
}

@keyframes settings-panel-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .settings__panel {
    animation: none;
  }
}

/* P4：dump 吸底保存条——表单最长（8 键 + 密码三态），保存动作常驻视口底部。
   背景继承所在 NCard 的 --n-color（= cardColor），故此处不能包 n-el（会把变量重置成 bodyColor）。 */
.settings__savebar {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 0 4px;
  background: var(--n-color, #fff);
  border-top: 1px solid var(--n-border-color, rgba(125, 133, 152, 0.28));
}

.settings__form {
  max-width: 560px;
}

.settings__password-form {
  max-width: 420px;
}

.settings__grid {
  flex-wrap: wrap;
}

.settings__cell {
  width: 220px;
}

.settings__cell--narrow {
  width: 120px;
}

.settings__full {
  width: 100%;
}

.settings__dump-type {
  max-width: 220px;
}

/* E39：时区下拉（IANA 清单可搜索，留足宽度避免长名截断） */
.settings__tz-select {
  max-width: 360px;
}

/* E39：服务器实时时钟（等宽数字，避免秒位抖动导致横向窜动） */
.settings__clock {
  font-variant-numeric: tabular-nums;
}

.settings__number {
  width: 120px;
}

.settings__ratio-item {
  max-width: 480px;
}

.settings__hint {
  font-size: 12px;
}

/* G4 强度条：四段等宽，颜色由 JS 按分档给出（语义色沿用主题基准值） */
.settings__strength {
  flex-wrap: wrap;
}

.settings__strength-bar {
  display: flex;
  gap: 4px;
  width: 160px;
}

.settings__strength-seg {
  flex: 1 1 0;
  height: 4px;
  border-radius: 2px;
  transition: background 0.15s;
}

.settings__file-input {
  display: none;
}
</style>
