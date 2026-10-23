<script setup lang="ts">
/**
 * 仓库页「密钥身份」小节（《云端仓库安全基线》§5）。
 *
 * 存在理由：密钥中性化是「静默失败」设计（失败只记日志、绝不翻转 init 终态），
 * 没有可见入口就无法确认它到底生效没有；历史仓库（旧版本/手工 init 建库）与
 * 多 key 接入（别的工具/别的机器）也只有看 key list 才发现。
 *
 * 安全边界：只展示 id / user / host / created / current / neutral 六个字段——后端
 * 不返回 `data`（master key 密文）与 KDF 参数，本组件也不请求、不推断。
 */

import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { NText } from 'naive-ui'
import { useI18n } from 'vue-i18n'

import { getRepoKeys } from '@/api/repo'
import type { RepoKeyOut, RepoKeysOverall } from '@/api/types'
import StatusTag from '@/components/StatusTag.vue'

const props = defineProps<{ repoId: string }>()
const { t } = useI18n()

const keysQuery = useQuery({
  // 与 E30 状态分桶：安全判定不吃 /status 的 30s 缓存（必须实时）
  queryKey: ['repo', 'keys', computed(() => props.repoId)],
  queryFn: () => getRepoKeys(props.repoId),
  retry: false,
})

const data = computed(() => keysQuery.data.value ?? null)
const keys = computed<RepoKeyOut[]>(() => data.value?.keys ?? [])
const overall = computed<RepoKeysOverall>(() => data.value?.overall ?? 'unknown')
const probeError = computed(() => data.value?.probe_error ?? '')
const loading = computed(() => keysQuery.isPending.value)

/** 顶部结论标签：四态与后端 `overall` 一一对应。 */
const badge = computed((): { kind: 'success' | 'warning' | 'default'; label: string } => {
  if (overall.value === 'neutralized') {
    return { kind: 'success', label: t('repo.keysNeutralized') }
  }
  if (overall.value === 'exposing') {
    return { kind: 'warning', label: t('repo.keysExposing', { n: keys.value.length }) }
  }
  if (overall.value === 'mixed') {
    return { kind: 'warning', label: t('repo.keysMixed', { n: keys.value.length }) }
  }
  return { kind: 'default', label: t('repo.keysUnknown') }
})

/** 底部说明：这条信息意味着什么（unknown 时无说明，原因已在 probeError 行显示）。 */
const hint = computed(() => {
  if (overall.value === 'neutralized') {
    return t('repo.keysHintNeutralized')
  }
  if (overall.value === 'mixed') {
    return t('repo.keysHintMixed')
  }
  if (overall.value === 'exposing') {
    return t('repo.keysHintExposing')
  }
  return ''
})
</script>

<template>
  <div class="repo__keys">
    <div class="repo__keys-head">
      <n-text depth="3" class="repo__keys-title">{{ t('repo.keysTitle') }}</n-text>
      <StatusTag :kind="badge.kind" :label="badge.label" size="tiny" />
    </div>

    <n-text v-if="loading" depth="3" class="repo__keys-note">
      {{ t('common.loading') }}
    </n-text>
    <n-text v-else-if="probeError" depth="3" class="repo__keys-note">
      {{ t('repo.keysProbeFailed', { reason: probeError }) }}
    </n-text>
    <template v-else>
      <div v-for="item in keys" :key="item.id_full" class="repo__key-row">
        <code class="repo__key-id">{{ item.id }}</code>
        <span class="repo__key-identity">{{ item.user }} @ {{ item.host }}</span>
        <span class="repo__key-created tabular-nums">
          {{ item.created }} {{ item.created_tz }}
        </span>
        <StatusTag v-if="item.current" kind="info" :label="t('repo.keysCurrent')" size="tiny" />
        <StatusTag
          :kind="item.neutral ? 'success' : 'warning'"
          :label="item.neutral ? t('repo.keysNeutralShort') : t('repo.keysExposingShort')"
          size="tiny"
        />
      </div>
      <n-text v-if="hint" depth="3" class="repo__keys-note">{{ hint }}</n-text>
    </template>
  </div>
</template>

<style scoped>
/* 分组间距由宿主容器提供（仓库页 = 状态卡右侧栏，靠 gap 与左侧状态行分组，无分隔线）：
   组件只负责自身内容排布，既可用于左右并排，也可在窄屏回落成上下堆叠。 */
.repo__keys {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.repo__keys-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.repo__keys-title,
.repo__keys-note,
.repo__key-created {
  font-size: 13px;
}

.repo__key-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.repo__key-id {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.repo__key-identity {
  font-size: 13px;
}
</style>
