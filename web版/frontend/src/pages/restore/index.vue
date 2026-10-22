<script setup lang="ts">
/** 恢复入口页（P8 / 方案 §4.8.5 R1）。
 *
 * 形态来源：Duplicati `restore.component.html:1-76` —— 标题「从哪里恢复？」
 * + 备份列表行动卡 + 分隔线「或者不使用本地配置」 + 备选卡。
 *
 * 兼容约束（V-1）：快照页「恢复此快照」走 `{ name: 'restore', query: { snapshot } }`，
 * 本页负责把该直达语义**原样转发**到向导首步 `/restore/:id/select-files`；
 * 预选语义不变——向导读 `route.params.id` 作为预选源（见 flow/index.vue）。
 * 直达场景不再拉取快照列表（`enabled: !requested`），避免无谓请求与一帧空渲染。
 *
 * `:id` 语义 = **快照 id**（不是仓库 id）：后端 E17 列表走 `current_repo_id`
 * 激活仓库模型、不支持按仓库过滤，故入口页直接列当前激活仓库的快照。
 */

import { useQuery } from '@tanstack/vue-query'
import { NAlert, NSpin, NText } from 'naive-ui'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { listSnapshots } from '@/api/snapshots'
import type { SnapshotOut } from '@/api/types'
import ActionCard from '@/components/dashboard/ActionCard.vue'
import { IconDownload, IconServer, IconUpload } from '@/components/icons/HomeIcons'
import { formatDateTimeOr } from '@/utils/datetime'

/** 入口页最多展示的快照数（更多请走快照页）。 */
const ENTRY_LIMIT = 12

const { t } = useI18n()
const router = useRouter()
const route = useRoute()

/** V-1 直达源：快照页 ?snapshot=<id>；缺省 null（正常浏览入口页）。 */
const requested = typeof route.query.snapshot === 'string' && route.query.snapshot ? route.query.snapshot : null

if (requested) {
  void router.replace({ name: 'restore-select-files', params: { id: requested } })
}

const snapshotsQuery = useQuery({
  queryKey: ['snapshots', 'restore-entry'],
  queryFn: () => listSnapshots(ENTRY_LIMIT),
  enabled: !requested,
})

/** 按 time 降序：后端 E17 已保证最新在前，前端再兜底排序一次。 */
const snapshotItems = computed(() =>
  [...(snapshotsQuery.data.value?.items ?? [])].sort((a, b) => (b.time ?? '').localeCompare(a.time ?? '')),
)

/** 卡片标题：快照时间 + 备份路径（无路径时回落短 id）。 */
function describe(snapshot: SnapshotOut): string {
  const time = formatDateTimeOr(snapshot.time, t('common.none'))
  const paths = snapshot.paths.join(', ')
  return paths ? `${time} · ${paths}` : `${time} · ${snapshot.id.slice(0, 8)}`
}

function openWizard(snapshotId: string): void {
  void router.push({ name: 'restore-select-files', params: { id: snapshotId } })
}

/** 配置导入复用设置页既有实现（importConfig），不复制一份上传 UI。 */
function goSettingsTransfer(): void {
  void router.push({ path: '/settings', hash: '#sec-transfer' })
}

/** 单激活仓库模型：换仓库在仓库页进行，回本页再选快照。 */
function goRepo(): void {
  void router.push({ name: 'repo' })
}
</script>

<template>
  <div v-if="!requested" class="restore-entry">
    <header class="restore-entry__head">
      <h1 class="restore-entry__title">{{ t('restore.entry.title') }}</h1>
      <n-text depth="3">{{ t('restore.entry.subtitle') }}</n-text>
    </header>

    <section>
      <h2 class="restore-entry__h2">{{ t('restore.entry.snapshotTitle') }}</h2>
      <n-spin v-if="snapshotsQuery.isLoading.value" size="small" />
      <n-alert v-else-if="!snapshotItems.length" type="default" :bordered="false">
        {{ t('restore.entry.empty') }}
      </n-alert>
      <div v-else class="restore-entry__grid">
        <ActionCard
          v-for="snapshot in snapshotItems"
          :key="snapshot.id"
          :icon="IconDownload"
          :title="describe(snapshot)"
          :action-label="t('restore.entry.restore')"
          @click="openWizard(snapshot.id)"
        />
      </div>
    </section>

    <div class="restore-entry__divider">
      <span>{{ t('restore.entry.divider') }}</span>
    </div>

    <div class="restore-entry__grid">
      <ActionCard
        :icon="IconUpload"
        :title="t('restore.entry.fromConfig')"
        :description="t('restore.entry.fromConfigDesc')"
        @click="goSettingsTransfer"
      />
      <ActionCard
        :icon="IconServer"
        :title="t('restore.entry.fromOtherRepo')"
        :description="t('restore.entry.fromOtherRepoDesc')"
        @click="goRepo"
      />
    </div>
  </div>
</template>

<style scoped>
.restore-entry {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.restore-entry__head {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* 标题不设 color：继承当前主题文本色（暗色皮肤挂 documentElement，继承有效）。 */
.restore-entry__title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}

.restore-entry__h2 {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 600;
}

.restore-entry__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}

.restore-entry__divider {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--n-text-color-3, #8a8a8a);
  font-size: 13px;
}

.restore-entry__divider::before,
.restore-entry__divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--n-border-color, rgba(128, 128, 128, 0.35));
}
</style>
