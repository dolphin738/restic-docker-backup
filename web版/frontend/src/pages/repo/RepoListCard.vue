<script setup lang="ts">
/**
 * 仓库页 · 仓库列表卡（候选 4 批 3）。
 *
 * 列表行卡（对齐 Duplicati destination-list-item）：整行点击 = 设为当前，
 * 行内动作 = 管理 / 密码（设或重设）/ 初始化 / 删除，全部按该行状态判定。
 *
 * **只管展示与行内判定，不管确认门与切换副作用**：所有动作 emit 给父页，
 * 父页由此打开确认门或切当前仓库——门的渲染位置与生命周期不变。
 */

import { computed } from 'vue'
import { useQueries, useQueryClient } from '@tanstack/vue-query'
import { NAlert, NButton, NCard, NIcon, NSpace, NText, NTooltip } from 'naive-ui'
import { useI18n } from 'vue-i18n'

import { getRepoStatus } from '@/api/repo'
import type { RepoOut, RepoStatusResponse } from '@/api/types'
import ActionCard from '@/components/dashboard/ActionCard.vue'
import JobProgress from '@/components/JobProgress.vue'
import StatusTag from '@/components/StatusTag.vue'
import { IconPlusCircle, backendIcon } from '@/components/icons/icons'

const { t } = useI18n()
const queryClient = useQueryClient()

const props = defineProps<{
  /** 仓库列表。 */
  repos: RepoOut[]
  /** 当前选中仓库 id（与顶栏切换器同一份）。 */
  currentRepoId: string | undefined
  /** init 进度映射（repoId → jobId）。 */
  initJobMap: Record<string, string>
  /** 正在初始化的仓库 id（按钮 loading）。 */
  initTargetId: string | null
}>()

/** 动作请求：父页据此开门 / 切仓（门与副作用都不在本组件内）。 */
const emit = defineEmits<{
  (e: 'select', id: string): void
  (e: 'manage', id: string): void
  (e: 'create'): void
  (e: 'rclone-drawer'): void
  (e: 'password', id: string, force: boolean): void
  (e: 'init', id: string): void
  (e: 'rebuild', id: string): void
  (e: 'delete', id: string): void
  (e: 'init-done', id: string): void
}>()

/** 逐行状态查询（每仓一条，30s staleTime）。 */
const rowStatusQueries = useQueries({
  queries: computed(() =>
    props.repos.map((r) => ({
      queryKey: ['repo', 'status', 'row', r.id],
      queryFn: () => getRepoStatus(r.id),
      staleTime: 30_000,
    })),
  ),
})

/** 按 queryKey 精确取行状态（不能按下标：长度变化同帧内数组可能未重排）。 */
const rowStatusById = computed<Record<string, RepoStatusResponse | undefined>>(() => {
  void rowStatusQueries.value.map((q) => q.dataUpdatedAt)
  const out: Record<string, RepoStatusResponse | undefined> = {}
  for (const r of props.repos) {
    out[r.id] = queryClient.getQueryData<RepoStatusResponse>(['repo', 'status', 'row', r.id])
  }
  return out
})

/** 类型展示名（i18n 键按类型首字母大写拼）。 */
function typeLabel(ty: string): string {
  const key = `repo.type${ty.charAt(0).toUpperCase()}${ty.slice(1)}`
  return t(key)
}

const INIT_FAILURE_KINDS = new Set(['not_initialized', 'unknown'])

/** 需要初始化 = 已填地址 + 探测结论「未连接」且分类落在上述集合；无地址（未配置）与已连接均不显示。 */
function needsInit(r: RepoOut): boolean {
  if (!r.url) {
    return false
  }
  const st = rowStatusById.value[r.id]
  if (!st || st.connected) {
    return false
  }
  return INIT_FAILURE_KINDS.has(st.failure_kind ?? 'unknown')
}

/** 行内密码动作：'set' = 密码文件缺失（首次写入）；'reset' = 密码文件在但仓库打不开
 *  （failure_kind=password，旧密码无从找回 → 走 force 覆盖）；null = 不需要入口。
 *  判定用列表自带的 passfile_status + 行状态分类，两者都不依赖编辑态。 */
function passwordAction(r: RepoOut): 'set' | 'reset' | null {
  if (r.passfile_status !== 'configured') {
    return 'set'
  }
  const st = rowStatusById.value[r.id]
  if (st && !st.connected && st.failure_kind === 'password') {
    return 'reset'
  }
  return null
}

/** 行级可达性（删除保护方案 P1-3）：已填地址 + 探测结论为不可达。
 *  「不可达」= 快照暂时打不开，用户不必点进快照页才吃 500——列表就把它标出来。
 *  探测结论缺失（加载中 / 请求失败）**不**判为不可达：宁可不报，也不误报红。 */
function isUnreachable(r: RepoOut): boolean {
  if (!r.url) {
    return false
  }
  const st = rowStatusById.value[r.id]
  return Boolean(st && !st.connected)
}

/** 当前不可达仓库清单（页面级告警用；随行状态查询自动刷新，无额外请求）。 */
const unreachableRepos = computed(() => props.repos.filter((r) => isUnreachable(r)))

</script>

<template>
    <n-card :title="t('repo.listTitle')" size="small">
      <template #header-extra>
        <n-button size="tiny" secondary @click="emit('rclone-drawer')">
          {{ t('repo.rcloneManageBtn') }}
        </n-button>
      </template>
      <!-- 不可达仓库告警（删除保护方案 P1-3）：让「快照暂时打不开」在列表就可见，
           不必点进快照页才吃 500。刻意**放在 n-space 之外**：n-space 展平插槽子节点
           按索引发 key，多一个条件兄弟会让索引相撞（本项目既有纪律）。 -->
      <div v-if="unreachableRepos.length > 0" class="repo__unreachable">
        <n-alert type="warning" :show-icon="false">
          {{ t('repo.unreachableBanner', { n: unreachableRepos.length }) }}
          <span class="repo__del-names">{{ unreachableRepos.map((r) => r.name).join(' / ') }}</span>
          <div class="repo__del-hint">{{ t('repo.unreachableBannerHint') }}</div>
        </n-alert>
      </div>
      <n-space vertical :size="12">
        <ActionCard
          :icon="IconPlusCircle"
          :title="t('repo.listAddTitle')"
          :description="t('repo.listAddDesc')"
          @click="emit('create')"
        />
        <!-- 分界线：新建仓库操作卡 ↔ 仓库列表（用户 2026-10-24 指令）。
             与列表行同生同灭（props.repos 为空时不渲染），不会单独抖动——避免 n-space
             展平插槽子节点按索引发 key 时索引相撞（本项目既有纪律）。 -->
        <div v-if="props.repos.length > 0" class="repo__list-divider"></div>
        <!-- 仓库行卡：整行点击 = 设为当前；当前行高亮且点击空转（已是选中态）。
             交互形态照搬 Duplicati destination.component.html：div + role=button + tabindex=0
             + keydown.enter/space（避免 button 嵌 button 的非法 HTML——行内还有删除按钮）。 -->
        <div
          v-for="r in props.repos"
          :key="r.id"
          role="button"
          tabindex="0"
          class="repo__dest"
          :class="{ 'repo__dest--current': r.id === props.currentRepoId }"
          :aria-current="r.id === props.currentRepoId ? 'true' : undefined"
          @click="emit('select', r.id)"
          @keydown.enter="emit('select', r.id)"
          @keydown.space.prevent="emit('select', r.id)"
        >
          <n-icon :size="20" class="repo__dest-icon"><component :is="backendIcon(r.type)" /></n-icon>
          <span class="repo__dest-body">
            <span class="repo__dest-name">
              <n-text strong>{{ r.name }}</n-text>
              <!-- 仓库 ID（改版前的表格有 ID 列，行卡化后曾在列表态丢失；ID 是凭据键
                   cred.{id}.* 与 RESTIC_REPOSITORY 寻址的锚点，必须可见可核对） -->
              <n-text depth="3" class="repo__dest-id">#{{ r.id }}</n-text>
              <StatusTag v-if="r.id === 'default'" size="tiny" kind="warning" :label="t('repo.defaultTag')" />
              <StatusTag v-if="r.id === props.currentRepoId" size="tiny" kind="success" :label="t('repo.currentTag')" />
              <StatusTag
                size="tiny"
                :kind="r.passfile_status === 'configured' ? 'success' : 'warning'"
                :label="r.passfile_status === 'configured' ? t('repo.passfileConfigured') : t('repo.passfileMissingShort')"
              />
              <!-- 行级不可达标记（P1-3）：只做提示，不阻断任何动作（用户仍可编辑/修复）。 -->
              <n-tooltip v-if="isUnreachable(r)" trigger="hover">
                <template #trigger>
                  <StatusTag size="tiny" kind="error" :label="t('repo.unreachableTag')" />
                </template>
                {{ t('repo.unreachableHint') }}
              </n-tooltip>
            </span>
            <n-text depth="3" class="repo__dest-desc">
              {{ r.url || '—' }}<template v-if="r.host_tag"> · {{ r.host_tag }}</template>
            </n-text>
          </span>
          <n-text depth="3" class="repo__dest-type">{{ typeLabel(r.type) }}</n-text>
          <!-- 行内动作（对齐 Duplicati manage-actions）：管理（整页切编辑态）+ 初始化 + 删除；均须 stop 冒泡。
               初始化只在「已填地址且探测未连接」时出现（已初始化的仓库不再显示），
               密码文件缺失时禁用并给原因——与维护区原门禁同口径。 -->
          <n-button size="tiny" secondary @click.stop="emit('manage', r.id)">
            {{ t('repo.manage') }}
          </n-button>
          <!-- 密码入口（与「初始化」同位置、同口径：逐行判定）：
               密码文件缺失 → 设置；仓库打不开且分类是密码错误 → 重设（force 覆盖，
               后端仍会先探测，仓库能打开时拒绝）。 -->
          <template v-if="passwordAction(r) === 'reset'">
            <n-tooltip trigger="hover">
              <template #trigger>
                <n-button size="tiny" secondary type="warning" @click.stop="emit('password', r.id, true)">
                  {{ t('repo.passwordResetBtn') }}
                </n-button>
              </template>
              {{ t('repo.passwordWrongHint') }}
            </n-tooltip>
            <!-- 密码错（文件在但打不开）→ 重设（force 覆盖）+ 重建。
                 重建 2026-10-21 起对所有本地 FS 仓库开放：删除仓库不触碰磁盘数据，
                 同址新建会撞上打不开的旧仓库（cat config 报「密码错误」），
                 此前非默认仓库只能「删除后换 URL 新建」——同址即死循环，无路可走。 -->
            <n-button
              v-if="r.type === 'local'"
              size="tiny"
              secondary
              type="error"
              @click.stop="emit('rebuild', r.id)"
            >
              {{ t('repo.rebuildBtn') }}
            </n-button>
            <!-- 远程后端：无本地数据可自动清空——手动清空后端存储后删除、换 URL 新建 -->
            <n-text v-else depth="3" class="repo__note">{{ t('repo.rebuildRemoteHint') }}</n-text>
          </template>
          <n-button
            v-else-if="passwordAction(r) === 'set'"
            size="tiny"
            secondary
            type="primary"
            @click.stop="emit('password', r.id, false)"
          >
            {{ t('repo.passwordSetBtn') }}
          </n-button>
          <n-tooltip v-if="needsInit(r) && r.passfile_status !== 'configured'" trigger="hover">
            <template #trigger>
              <n-button size="tiny" secondary type="warning" disabled>{{ t('repo.initBtn') }}</n-button>
            </template>
            {{ t('repo.initBlockedByPassfile') }}
          </n-tooltip>
          <n-button
            v-else-if="needsInit(r)"
            size="tiny"
            secondary
            type="warning"
            :loading="props.initTargetId === r.id"
            @click.stop="emit('init', r.id)"
          >
            {{ t('repo.initBtn') }}
          </n-button>
          <!-- A 方案：init 进度行级化——挂在被初始化的仓库行内，done/failed 回调清理对应卡。
               抽屉（日志）是覆盖层，不占行布局；非当前仓库也能在它自己的行看到进度。 -->
          <JobProgress
            v-if="props.initJobMap[r.id]"
            :job-id="props.initJobMap[r.id]"
            :title="`${r.name} · ${t('repo.initJobTitle')}`"
            hide-progress-bar
            class="repo__init-progress"
            @done="emit('init-done', r.id)"
            @failed="emit('init-done', r.id)"
          />
          <n-button
            size="tiny"
            secondary
            type="error"
            :disabled="r.id === 'default'"
            @click.stop="emit('delete', r.id)"
          >
            {{ t('repo.delete') }}
          </n-button>
        </div>
      </n-space>
    </n-card>
</template>
