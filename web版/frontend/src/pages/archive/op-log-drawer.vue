<script setup lang="ts">
/** 归档页「操作日志」抽屉（2026-10-29）：释放 ID / 销毁数据 / 恢复写回的可查询结果。
 *
 * **不另造一份流水**：这三个动作后端**早已写入审计日志**（S6：append-only，只增不改），
 * 之前只是没有面向用户的入口，用户点了按钮看不到下文。本抽屉 = 审计日志按动作名
 * 清单的查询视图，与审计页共用 utils/audit 的展示格式化（单一事实来源）。
 *
 * 三个动作名（repo.id_release / repo.purge / rclone.config_restore）不共享单一前缀，
 * 逐个查要发 N 个请求且翻页失效 → 服务端 `GET /api/audit?action=a,b,c` 取并集
 * （见后端 ops._split_action_prefixes）。
 *
 * 只读：无任何写/删入口（S6：审计代码面只增不改，本组件只消费不加料）。
 */
import { useQuery } from '@tanstack/vue-query'
import { NAlert, NDrawer, NDrawerContent, NEmpty, NTag, NText } from 'naive-ui'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { queryAudit } from '@/api/audit'
import type { AuditResult } from '@/api/types'
import StatusTag from '@/components/StatusTag.vue'
import { auditActionLabel, auditDetailKeyLabel, auditDetailValue, parseAuditDetail } from '@/utils/audit'
import { formatDateTime } from '@/utils/datetime'

const props = defineProps<{
  show: boolean
  /** 抽屉标题（调用方给，已本地化）。 */
  title: string
  /** 要查的动作名清单（服务端按前缀取并集；空清单 = 不查询）。 */
  actions: string[]
}>()

const emit = defineEmits<{ (e: 'update:show', value: boolean): void }>()

const { t, te } = useI18n()

const showModel = computed({
  get: () => props.show,
  set: (value: boolean) => emit('update:show', value),
})

/** 缓存键整条用 computed 包——不能只在数组里塞一个 ref：两个抽屉除末段外
 *  键完全一致（['archive','oplog',X]），X 是同形状的 ref 时会被算成同一个 hash，
 *  两个 observer 撞进同一条缓存 → 只有一个 queryFn 真的跑，本页两个抽屉会串味
 *  （实拍：仓库抽屉点了却拉回 rclone 的日志）。 */
const queryKey = computed(() => ['archive', 'oplog', props.actions.join(',')])

/** 打开时才拉（默认不打开 = 零额外请求）。 */
const query = useQuery({
  queryKey,
  queryFn: () => queryAudit({ actions: props.actions, limit: 50 }),
  enabled: computed(() => props.show && props.actions.length > 0),
})

type ResultKind = 'success' | 'warning' | 'error'

function resultKind(result: AuditResult): ResultKind {
  switch (result) {
    case 'ok':
      return 'success'
    case 'denied':
      return 'warning'
    case 'failed':
      return 'error'
  }
}

interface DetailRow {
  key: string
  value: string
}

interface OpLogRow {
  id: number
  timeText: string
  actionLabel: string
  resultKind: ResultKind
  resultLabel: string
  target: string
  /** detail 不是 JSON（契约不保证）→ 原样透传，不当键值行解析。 */
  rawDetail: string
  detailRows: DetailRow[]
}

/** 后端行 → 展示模型：格式化一律走 utils/audit，与审计页同一套译文。 */
const rows = computed<OpLogRow[]>(() =>
  (query.data.value?.items ?? []).map((row) => {
    const parsed = parseAuditDetail(row.detail ?? '')
    return {
      id: row.id,
      timeText: formatDateTime(row.ts),
      actionLabel: auditActionLabel(row.action, t, te),
      resultKind: resultKind(row.result),
      resultLabel: t(`audit.result.${row.result}`),
      target: row.target ?? '',
      rawDetail: parsed ? '' : (row.detail ?? ''),
      detailRows: parsed
        ? Object.entries(parsed).map(([key, value]) => ({
            key: auditDetailKeyLabel(key, t, te),
            value: auditDetailValue(key, value, t),
          }))
        : [],
    }
  }),
)

const errorText = computed(() => (query.error.value ? String(query.error.value) : ''))
</script>

<template>
  <n-drawer v-model:show="showModel" width="560">
    <n-drawer-content :title="title" closable>
      <div class="op-log">
        <n-text depth="3" class="op-log__hint">{{ t('archive.opLog.hint') }}</n-text>

        <n-alert v-if="errorText" type="error" :show-icon="false">{{ errorText }}</n-alert>

        <n-empty v-else-if="query.isLoading.value" :description="t('common.loading')" size="small" />
        <n-empty v-else-if="rows.length === 0" :description="t('archive.opLog.empty')" size="small" />

        <div v-else class="op-log__list">
          <div v-for="row in rows" :key="row.id" class="op-log__item">
            <div class="op-log__head">
              <span class="op-log__time">{{ row.timeText }}</span>
              <n-tag size="small" :bordered="false">{{ row.actionLabel }}</n-tag>
              <StatusTag :kind="row.resultKind" :label="row.resultLabel" />
            </div>
            <div v-if="row.target" class="op-log__pair">
              <span class="op-log__key">{{ t('archive.opLog.target') }}</span>
              <span class="op-log__val">{{ row.target }}</span>
            </div>
            <div v-for="entry in row.detailRows" :key="entry.key" class="op-log__pair">
              <span class="op-log__key">{{ entry.key }}</span>
              <span class="op-log__val">{{ entry.value }}</span>
            </div>
            <span v-if="row.rawDetail" class="op-log__raw">{{ row.rawDetail }}</span>
          </div>
        </div>
      </div>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.op-log {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.op-log__hint {
  font-size: 12px;
}

.op-log__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.op-log__item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--rw-border);
  border-radius: var(--rw-radius-control);
  background: var(--rw-surface);
}

.op-log__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

/* 时间居左撑开，两个标签自然贴右；窄屏标签换行不溢出 */
.op-log__time {
  flex: 1 1 auto;
  min-width: 0;
  font-family: var(--font-family-mono);
  font-size: 12px;
  opacity: 0.75;
}

.op-log__pair {
  display: grid;
  grid-template-columns: 140px 1fr;
  gap: 8px;
  font-size: 12px;
}

.op-log__key {
  color: var(--n-text-color-depth-3, inherit);
}

.op-log__val {
  word-break: break-all;
}

.op-log__raw {
  font-family: var(--font-family-mono);
  font-size: 12px;
  word-break: break-all;
}
</style>
