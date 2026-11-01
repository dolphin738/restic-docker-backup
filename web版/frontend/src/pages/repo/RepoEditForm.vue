
<script setup lang="ts">
/**
 * 仓库页 · 编辑态表单（候选 4 批 4）。
 *
 * 整块编辑态（类型下拉 → 按类型地址字段 → 凭据 → 内联测试区 → 底栏动作）
 * 自 index.vue 平移而来。**只管展示与字段录入，不管业务**：
 *
 * - 确认门仍在父页（useRepoGates 持有），本组件只 emit 请求；
 * - repoForm / credInput 由父页以 prop 传入（reactive 对象，v-model 仍双向生效），
 *   保存/创建/测试这些写路径留在父页——因此不存在「父子各持一份状态」的分叉。
 * - 测试态（repoTestState / testResult）由父页计算后传入，本组件只读渲染。
 */

import { computed, h, type FunctionalComponent } from 'vue'
import {
  NAlert,
  NAutoComplete,
  NButton,
  NCard,
  NDropdown,
  NForm,
  NFormItem,
  NGi,
  NGrid,
  NIcon,
  NInput,
  NSelect,
  NSpace,
  NText,
  NTooltip,
} from 'naive-ui'
import { useI18n } from 'vue-i18n'

import DirPickerInline from '@/components/DirPickerInline.vue'
import JobProgress from '@/components/JobProgress.vue'
import PasswordInput from '@/components/PasswordInput.vue'
import StatusTag from '@/components/StatusTag.vue'
import { backendIcon } from '@/components/icons/icons'
import type { JobDonePayload, RepoOut, RepoTestResponse, RcloneRemotesOut } from '@/api/types'
import type { CredField } from '@/utils/repoCredentials'
import { credOptions } from '@/utils/repoCredentials'
import type { RepoTypeKey } from '@/utils/repoUrl'

const { t } = useI18n()

/** 凭据字段形状由 utils/repoCredentials.ts 单一定义（父页与向导共用），此处只做再导出。 */
export type { CredField } from '@/utils/repoCredentials'

/** 编辑态表单字段集（父页 repoForm 的形状）。 */
export interface RepoFormFields {
  type: RepoTypeKey | ''
  name: string
  id: string
  hostTag: string
  path: string
  bucket: string
  endpoint: string
  server: string
  user: string
  url: string
  remote: string
  raw: string
}

const props = defineProps<{
  /** 表单字段（父页 reactive 对象；v-model 直接写入，父子同一份）。 */
  repoForm: RepoFormFields
  /** 凭据输入（父页 reactive 对象，键 = 后缀）。 */
  credInput: Record<string, string>
  /** 新建态（区别于编辑态）：类型未选前不渲染地址/凭据区。 */
  creatingNew: boolean
  /** 当前仓库 id（编辑态卡头展示 + init 进度寻址）。 */
  currentRepoId: string
  /** 当前仓库（null = 未取到）；密码状态行与重建入口判定用。 */
  repo: RepoOut | null
  /** 已存仓库类型归一后的类型键（formType 的回落基准）。 */
  repoType: RepoTypeKey
  /** 类型下拉选项。 */
  typeSelectOptions: Array<{ label: string; value: string }>
  /** Bucket/容器名建议值。 */
  bucketSuggestions: string[]
  /** Endpoint 建议值。 */
  endpointSuggestions: string[]
  /** 凭据字段表（按当前所选类型）。 */
  credFields: CredField[]
  /** 凭据项查询（has_value / source）；新建态传 null。 */
  itemOf: ((suffix: string) => { has_value?: boolean; source?: string } | undefined) | null
  /** 地址由 env 覆盖 → 整段只读。 */
  envLocked: boolean
  /** rclone 远端探测结果（null = 未探测）。 */
  rcloneRemotes: RcloneRemotesOut | null
  /** rclone 探测是否出错。 */
  rcloneProbeError: unknown
  /** rclone 远端名候选（下拉回填）。 */
  rcloneRemoteMenu: Array<{ label: string; key: string }>
  /** rclone 实际落盘路径推断（null = 未填或未探测到）。 */
  rcloneResolved: { kind: 'crypt' | 'remote' | 'unknown'; target: string } | null
  /** 同一远端下已有的仓库（首项用于「路径被占」提示）。 */
  rcloneRemoteUsers: Array<{ id: string; name: string; path: string; samePath: boolean }>
  /** 测试态。 */
  repoTestState: 'idle' | 'success' | 'error'
  /** 测试结论（null = 未测/已作废）。 */
  testResult: RepoTestResponse | null
  /** 是否正在测试。 */
  testing: boolean
  /** 是否正在保存。 */
  saving: boolean
  /** 是否正在创建。 */
  creating: boolean
  /** 是否正在生成仓库 id。 */
  generatingId: boolean
  /** 测试失败且结论为「未初始化」。 */
  testNotInitialized: boolean
  /** 测试失败且后端判定为密码错误。 */
  testPasswordWrong: boolean
  /** 密码文件是否缺失。 */
  passfileMissing: boolean
  /** 已保存但未复验 → 挡住不可逆动作。 */
  verifyRequired: boolean
  /** 是否正在初始化。 */
  initializing: boolean
  /** init 目标仓库 id（与 currentRepoId 相等时本卡按钮转 loading）。 */
  initTargetId: string | null
  /** 仓库 id → init 作业 id（编辑态就地展示进度）。 */
  initJobMap: Record<string, string>
  /** 智能主按钮（父页计算：新建 = 创建并测试，脏且未验 = 保存并测试，否则保存）。 */
  saveButton: { label: string; type: 'primary' | 'warning' | 'error'; run: () => Promise<void> }
}>()

const emit = defineEmits<{
  /** 返回列表态。 */
  (e: 'cancel'): void
  /** 类型下拉切换。 */
  (e: 'type-change', value: string | null): void
  /** 重新生成仓库 id。 */
  (e: 'regenerate-id'): void
  /** 打开 rclone 远端管理抽屉。 */
  (e: 'rclone-drawer'): void
  /** 就地连接测试。 */
  (e: 'test'): void
  /** 设置/重设仓库密码（force = 覆盖已有密码文件）。 */
  (e: 'password', force: boolean): void
  /** 初始化仓库。 */
  (e: 'init'): void
  /** 重建仓库（本地 FS）。 */
  (e: 'rebuild'): void
  /** init 作业终态。 */
  (e: 'init-done', payload?: JobDonePayload): void
}>()

/** 必填字段标签（对齐 Duplicati 字段标签的红色 * + tooltip「This field is mandatory」）。 */
const ReqLabel: FunctionalComponent<{ label: string }> = (p) =>
  h('span', { class: 'repo__req-label' }, [
    p.label,
    h('span', { class: 'repo__req', title: t('repo.fieldMandatory') }, '*'),
  ])

/** 表单所选类型（新建流 = 下拉选择；编辑流 = 已存类型，切换下拉即时生效）。 */
const formType = computed<RepoTypeKey>(() => (props.repoForm.type || props.repoType) as RepoTypeKey)

/** 下拉项渲染：类型图标 + 名称（零依赖内联 SVG，与列表行卡同源）。 */
function renderTypeLabel(option: { label: string; value: string }) {
  return h(NSpace, { align: 'center', size: 8 }, () => [
    h(NIcon, { size: 18 }, () => h(backendIcon(option.value))),
    option.label,
  ])
}

/** 本地 FS 类型（重建入口仅本地可见：远程后端无法自动清空）。 */
const isLocalRepo = computed(() => props.repo?.type === 'local')
</script>

<template>
  <div class="repo__edit-header">
    <n-button quaternary size="small" @click="emit('cancel')">{{ t('repo.backToList') }}</n-button>
  </div>
  <n-card size="small">
    <!-- 卡头：左侧标题 + 仓库 ID（紧跟标题）；右侧放本卡的动作入口（rclone 远端管理） -->
    <template #header>
      <div class="repo__edit-title">
        <span>{{ creatingNew ? t('repo.newRepoTitle') : t('repo.editRepoTitle') }}</span>
        <n-text v-if="!creatingNew" depth="3" class="repo__id-chip">
          {{ t('repo.colId') }}：#{{ currentRepoId }}
        </n-text>
      </div>
    </template>
    <template #header-extra>
      <n-button
        v-if="formType === 'rclone'"
        size="small"
        class="repo__manage-remote-head"
        @click="emit('rclone-drawer')"
      >
        {{ t('repo.rcloneManageBtn') }}
      </n-button>
    </template>
    <!-- 编辑卡体用普通 flex 容器而非 n-space：naive Space 对「v-if 注释 + HTML 注释」相邻子节点
         存在 vnode 克隆缺陷（页脚会被挂载两次，且每轮列表/编辑切换泄漏一份，2026-10-19 实证），
         回归护栏见 __tests__/page-render-smoke.spec.ts 的循环用例。 -->
    <div class="repo__edit-body">
      <n-form label-placement="top" size="small">
        <!-- 存储类型下拉（用户裁决：本项目无后端类型列表端点，用下拉替代 Duplicati 类型选择器；
             选中后才渲染地址/凭据区——对齐 Duplicati editBaseUrl=null 时卡内只出类型选择器。
             :value 传 null 而非 ''——naive 把空串当「已选中空值」不显示占位符（实测）。 -->
        <n-form-item :label="t('repo.formType')">
          <n-select
            :value="repoForm.type || null"
            :options="typeSelectOptions"
            :placeholder="t('repo.formTypePlaceholder')"
            :render-label="renderTypeLabel"
            @update:value="emit('type-change', $event)"
          />
          <n-text depth="3" class="repo__note">
            {{ creatingNew ? t('repo.dest.hint') : t('repo.formTypeChangeHint') }}
          </n-text>
        </n-form-item>

        <!-- 地址区：选中类型后按类型渲染（Duplicati customFields 逐类型字段集 ↔ restic URL 段） -->
        <template v-if="repoForm.type">
          <n-form-item :label="t('repo.formName')">
            <n-input v-model:value="repoForm.name" :placeholder="t('repo.nameRequired')" />
          </n-form-item>

          <!-- env 覆盖：地址整段只读（对齐 Duplicati readOnlyTest） -->
          <n-alert v-if="envLocked" type="warning" :show-icon="false">{{ t('repo.urlFromEnv') }}</n-alert>

          <!-- local：目录树选择 + 路径展示（P20；形态与仪表盘「编辑任务」第②步一致：
               地址栏 → 目录树 → 提示，点目录名即选为仓库路径）。
               手输一并保留在树下方：新仓库目录往往**尚不存在**，纯树选无法表达
               「我要建在 /srv/repo-new」，故树不是唯一入口（与向导「直接添加路径」同理由）。
               env 覆盖时整段只读，树隐去（地址不可改，给了就是假开关）。 -->
          <n-form-item v-if="repoForm.type === 'local'">
            <template #label><ReqLabel :label="t('repo.fieldPath')" /></template>
            <!-- 目录选择器已抽为共用组件（S-4：与 rclone crypt 本地目录同一实现），
                 组件自带双栏布局容器，解决 .n-form-item-blank 平分问题。 -->
            <DirPickerInline
              v-model:path="repoForm.path"
              :placeholder="t('repo.dest.localPlaceholder')"
              :hint="t('repo.pickPathHint')"
              :value-label="t('repo.pickedPath')"
              :value-hint="t('repo.pickPathValueHint')"
              :height="260"
              :disabled="envLocked"
            />
          </n-form-item>

          <!-- s3：服务器地址（Endpoint）+ Bucket + 文件夹路径。
               URL 组译 = restic 0.19.1 原生斜杠方言 s3:<endpoint>/<bucket>[/<path>]（ParseConfig
               源码核验：endpoint/bucket 以 / 分隔；旧 s3:region:bucket:/path 冒号方言会被
               minio-go 域名校验拒绝，已退役——区域只留凭据里的 AWS 默认区域，管签名）。 -->
          <n-grid v-else-if="repoForm.type === 's3'" cols="1 m:2" :x-gap="12" responsive="screen">
            <n-gi>
              <n-form-item>
                <template #label><ReqLabel :label="t('repo.fieldBucket')" /></template>
                <!-- get-show 恒真：naive-ui AutoComplete 默认仅「有输入值」才展开
                     （mergedShowOptions = !!value），聚焦空框不弹；常用值要一点就出 -->
                <n-auto-complete
                  v-model:value="repoForm.bucket"
                  :options="bucketSuggestions"
                  :get-show="() => true"
                  placeholder="mybucket"
                  :disabled="envLocked"
                />
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item>
                <template #label><ReqLabel :label="t('repo.fieldEndpoint')" /></template>
                <!-- 建议值 = 常用云厂商端点（MinIO/OSS/COS/AWS）+ 既有 s3 仓库提取值 -->
                <n-auto-complete
                  v-model:value="repoForm.endpoint"
                  :options="endpointSuggestions"
                  :get-show="() => true"
                  :placeholder="t('repo.fieldEndpointPlaceholder')"
                  :disabled="envLocked"
                />
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item :label="t('repo.fieldFolderPath')">
                <n-input
                  v-model:value="repoForm.path"
                  :placeholder="t('repo.fieldFolderPathPlaceholder')"
                  :disabled="envLocked"
                />
              </n-form-item>
            </n-gi>
          </n-grid>

          <!-- b2 / gcs / azure：Bucket（或容器）+ 文件夹路径（Duplicati Bucketname + Path 双字段） -->
          <n-grid
            v-else-if="repoForm.type === 'b2' || repoForm.type === 'gcs' || repoForm.type === 'azure'"
            cols="1 m:2"
            :x-gap="12"
            responsive="screen"
          >
            <n-gi>
              <n-form-item>
                <template #label>
                  <ReqLabel :label="repoForm.type === 'azure' ? t('repo.fieldContainer') : t('repo.fieldBucket')" />
                </template>
                <!-- get-show 恒真：naive-ui AutoComplete 默认仅「有输入值」才展开
                     （mergedShowOptions = !!value），聚焦空框不弹；常用值要一点就出 -->
                <n-auto-complete
                  v-model:value="repoForm.bucket"
                  :options="bucketSuggestions"
                  :get-show="() => true"
                  :placeholder="repoForm.type === 'azure' ? 'mycontainer' : 'mybucket'"
                  :disabled="envLocked"
                />
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item :label="t('repo.fieldFolderPath')">
                <n-input
                  v-model:value="repoForm.path"
                  :placeholder="t('repo.fieldFolderPathPlaceholder')"
                  :disabled="envLocked"
                />
              </n-form-item>
            </n-gi>
          </n-grid>

          <!-- sftp：服务器 + 用户名 + 文件夹路径（Duplicati ssh 条目 server/user/path；restic URL 无端口段） -->
          <n-grid v-else-if="repoForm.type === 'sftp'" cols="1 m:2" :x-gap="12" responsive="screen">
            <n-gi>
              <n-form-item>
                <template #label><ReqLabel :label="t('repo.fieldServer')" /></template>
                <n-input
                  v-model:value="repoForm.server"
                  :placeholder="t('repo.fieldServerPlaceholder')"
                  :disabled="envLocked"
                />
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item>
                <template #label><ReqLabel :label="t('repo.fieldUser')" /></template>
                <n-input
                  v-model:value="repoForm.user"
                  :placeholder="t('repo.fieldUserPlaceholder')"
                  :disabled="envLocked"
                />
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item :label="t('repo.fieldFolderPath')">
                <n-input
                  v-model:value="repoForm.path"
                  :placeholder="t('repo.fieldFolderPathPlaceholder')"
                  :disabled="envLocked"
                />
              </n-form-item>
            </n-gi>
          </n-grid>
          <n-text v-if="repoForm.type === 'sftp'" depth="3" class="repo__note">{{ t('repo.urlHintSftp') }}</n-text>
          <n-text v-if="repoForm.type === 's3'" depth="3" class="repo__note">{{ t('repo.urlHintS3') }}</n-text>

          <!-- rest：REST 服务器完整地址 -->
          <n-form-item v-else-if="repoForm.type === 'rest'">
            <template #label><ReqLabel :label="t('repo.fieldRestUrl')" /></template>
            <n-input
              v-model:value="repoForm.url"
              :placeholder="t('repo.dest.restPlaceholder')"
              :disabled="envLocked"
            />
          </n-form-item>

          <!-- rclone：远端名 + 远端路径（无凭据字段；conf 宿主机托管，Q17 只读探测） -->
          <n-grid v-else-if="repoForm.type === 'rclone'" cols="1 m:2" :x-gap="12" responsive="screen">
            <n-gi>
              <n-form-item>
                <template #label>
                  <ReqLabel :label="t('repo.fieldRemote')" />
                </template>
                <!-- 与目录树快捷路径同一套路：输入框可手输，旁边「选择远端」展开已探测的段名，
                     点一项回填到输入框；段不在列表里就先去右上角「管理远端」新建 -->
                <div class="repo__pick-row">
                  <n-dropdown
                    trigger="click"
                    :options="rcloneRemoteMenu"
                    :disabled="envLocked || !rcloneRemoteMenu.length"
                    @select="(key: string) => (repoForm.remote = key)"
                  >
                    <n-button size="small" :disabled="envLocked || !rcloneRemoteMenu.length">
                      {{ t('repo.rcloneRemotePick') }}
                    </n-button>
                  </n-dropdown>
                  <n-input
                    v-model:value="repoForm.remote"
                    :placeholder="t('repo.fieldRemotePlaceholder')"
                    :disabled="envLocked"
                    class="repo__pick-input"
                  />
                </div>
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item>
                <template #label>
                  <ReqLabel :label="t('repo.fieldRemotePath')" />
                </template>
                <n-input
                  v-model:value="repoForm.path"
                  :placeholder="t('repo.fieldRemotePathPlaceholder')"
                  :disabled="envLocked"
                />
              </n-form-item>
            </n-gi>
          </n-grid>
          <!-- conf 异常/空列表提示：正常时不渲染（候选已由远端下拉表达） -->
          <n-alert v-if="rcloneRemotes && !rcloneRemotes.conf_present" type="warning" :show-icon="false">
            {{ t('repo.rcloneConfMissing') }}
          </n-alert>
          <n-alert
            v-else-if="rcloneRemotes && rcloneRemotes.conf_present && !rcloneRemotes.remotes.length"
            type="warning"
            :show-icon="false"
          >
            {{ t('repo.rcloneConfEmpty') }}
          </n-alert>
          <n-alert v-else-if="rcloneProbeError" type="error" :show-icon="false">
            {{ t('repo.rcloneProbeFailed', { reason: String(rcloneProbeError) }) }}
          </n-alert>
          <!-- 实际落盘路径：远端名 + 路径都填好后显示（crypt 段按其底层目标拼）。
               放在 n-grid 之外：n-gi 上的 v-if 会留下注释节点，n-grid 展平子节点时会炸。 -->
          <div v-if="rcloneResolved" class="repo__note">
            <div>
              <n-text depth="2">
                {{
                  rcloneResolved.kind === 'crypt'
                    ? t('repo.rcloneResolvedCrypt', {
                        target: rcloneResolved.target,
                        remote: repoForm.remote.trim(),
                      })
                    : rcloneResolved.kind === 'remote'
                      ? t('repo.rcloneResolvedRemote', { target: rcloneResolved.target })
                      : t('repo.rcloneResolvedUnknown', { target: rcloneResolved.target })
                }}
              </n-text>
            </div>
            <div v-if="rcloneRemoteUsers.length > 0">
              <n-text type="warning">
                {{
                  rcloneRemoteUsers[0].samePath
                    ? t('repo.rcloneRemotePathTaken', {
                        name: rcloneRemoteUsers[0].name,
                        path: rcloneRemoteUsers[0].path || '—',
                      })
                    : t('repo.rcloneRemoteUsed', {
                        name: rcloneRemoteUsers[0].name,
                        path: rcloneRemoteUsers[0].path || '—',
                      })
                }}
              </n-text>
            </div>
          </div>
          <n-text v-if="repoForm.type === 'rclone'" depth="3" class="repo__note">{{ t('repo.urlHintRclone') }}</n-text>

          <!-- 已存地址解析失败：原文回退编辑 -->
          <n-form-item v-if="repoForm.raw" :label="t('repo.url')">
            <n-input v-model:value="repoForm.raw" :disabled="envLocked" />
            <n-text depth="3" class="repo__note">{{ t('repo.rawUrlFallback') }}</n-text>
          </n-form-item>

          <!-- 主机标识（restic host tag，全类型通用） -->
          <n-form-item :label="t('repo.hostTag')">
            <n-input v-model:value="repoForm.hostTag" :placeholder="t('repo.hostTagPlaceholder')" />
          </n-form-item>

          <!-- 仓库 ID（仅新建态；留空服务端自动生成 r<YYMMDD>-xxxxxx） -->
          <n-form-item v-if="creatingNew" :label="t('repo.colId')">
            <n-space vertical :size="8" class="repo__cred">
              <n-space :size="8" align="center">
                <n-input v-model:value="repoForm.id" :placeholder="t('repo.idAutoHint')" class="repo__id-input" />
                <n-button size="small" secondary :loading="generatingId" @click="emit('regenerate-id')">
                  {{ t('repo.regenerate') }}
                </n-button>
              </n-space>
              <n-text depth="3" class="repo__note">{{ t('repo.idAutoNote') }}</n-text>
            </n-space>
          </n-form-item>

          <!-- 凭据（脱敏：只回 has_value + source；env 覆盖的键置灰并给原因）。
               按 formType（下拉所选类型）渲染；新建态为纯输入（不带状态标签——
               itemOf 读的是当前仓库命名空间，新建时无意义），凭据随创建以
               cred.{created_id}.* 落盘（见 runCreate），无需二次进编辑态补配。 -->
          <template v-if="credFields.length">
            <n-text strong>{{ t('repo.credentials') }}</n-text>
            <n-form-item v-for="field in credFields" :key="field.suffix" :label="t(field.labelKey)">
              <n-space vertical :size="4" class="repo__cred">
                <!-- 编辑态才显示已配置/未配置与 env 覆盖（itemOf 读当前仓库命名空间，新建态无意义） -->
                <n-space v-if="!creatingNew" align="center" :size="8">
                  <StatusTag
                    size="tiny"
                    :kind="itemOf?.(field.suffix)?.has_value ? 'success' : 'default'"
                    :label="itemOf?.(field.suffix)?.has_value ? t('repo.credSet') : t('repo.credUnset')"
                  />
                  <StatusTag
                    v-if="itemOf?.(field.suffix)?.source === 'env'"
                    size="tiny"
                    kind="warning"
                    :label="t('repo.credSourceEnv')"
                  />
                </n-space>
                <n-tooltip v-if="!creatingNew && itemOf?.(field.suffix)?.source === 'env'" trigger="hover">
                  <template #trigger>
                    <PasswordInput
                      :model-value="''"
                      width="100%"
                      :placeholder="t('repo.credEnvNote')"
                      disabled
                    />
                  </template>
                  {{ t('repo.credEnvNote') }}
                </n-tooltip>
                <!-- 常用值建议（可下拉选择也可手动输入）：AWS 默认区域。
                     get-show 恒真：聚焦即展开（默认要输入才展开） -->
                <n-auto-complete
                  v-else-if="credOptions(field.kind)"
                  v-model:value="credInput[field.suffix]"
                  :options="credOptions(field.kind)!"
                  :get-show="() => true"
                  :placeholder="t('repo.credNewPlaceholder')"
                />
                <PasswordInput
                  v-else-if="field.kind === 'secret'"
                  v-model="credInput[field.suffix]"
                  width="100%"
                  :placeholder="t('repo.credNewPlaceholder')"
                />
                <n-input
                  v-else
                  v-model:value="credInput[field.suffix]"
                  :placeholder="t('repo.credNewPlaceholder')"
                />
              </n-space>
            </n-form-item>
          </template>
        </template>
      </n-form>

      <!-- 内联连接测试区（对齐 Duplicati app-test-url 状态机；源码中 editBaseUrl 为空时
           整块不渲染）：
           未测 → 提示条 + 立即测试；testing → 进行中文案；结果 → 分类 alert + 内联动作按钮。
           URL/凭据变更后结论由写路径作废回 idle（E1），与 Duplicati 的 URL 变更重置 testSignal 同构。
           仅编辑态：新建流无已存仓库，后端 testRepo 读的是已落库配置，测不了未创建的仓库。 -->
      <template v-if="repoForm.type && !creatingNew">
        <div class="repo__test-block">
          <n-alert v-if="testing" type="info" :show-icon="false">{{ t('repo.testing') }}</n-alert>
          <n-alert v-else-if="repoTestState === 'success'" type="success" :show-icon="false">
            {{ t('repo.testOk', { n: testResult?.latency_ms ?? 0 }) }}
          </n-alert>
          <n-alert v-else-if="repoTestState === 'error'" type="error" :show-icon="false">
            <n-space align="center" justify="space-between">
              <span>{{ testResult?.reason || t('repo.testFail') }}</span>
              <n-button size="tiny" type="primary" :loading="testing" @click="emit('test')">
                {{ t('repo.testAgain') }}
              </n-button>
            </n-space>
            <!-- 地址可达但仓库尚未初始化（新建后首次测试的典型结论）：就地给「设密码 → 初始化」闭环。
                 建仓必经两步（密码 + init），此前编辑态只禁用 init 却不给密码出口，
                 用户须退回列表行内设密码再回来——一屏内补齐后全流程不离开本页。 -->
            <div v-if="testNotInitialized" class="repo__test-init">
              <n-text depth="3" class="repo__note">{{ t('repo.testNotInitializedHint') }}</n-text>
              <n-space :size="8" align="center">
                <!-- 前置动作：密码文件缺失时 init 注定失败（restic 读不到密码）→ 先给密码入口。
                     与列表行内同源（openPassword + PasswordEntryModal），写入后 repoQuery
                     回刷新 → passfileMissing 转 false → 初始化按钮就地解锁，无需手动刷新。 -->
                <n-button
                  v-if="passfileMissing"
                  size="tiny"
                  secondary
                  type="primary"
                  @click="emit('password', false)"
                >
                  {{ t('repo.passwordSetBtn') }}
                </n-button>
                <n-tooltip v-if="passfileMissing" trigger="hover">
                  <template #trigger>
                    <n-button size="tiny" type="warning" disabled>{{ t('repo.initBtn') }}</n-button>
                  </template>
                  {{ t('repo.initBlockedByPassfile') }}
                </n-tooltip>
                <n-button
                  v-else
                  size="tiny"
                  type="warning"
                  :loading="initializing || initTargetId === currentRepoId"
                  @click="emit('init')"
                >
                  {{ t('repo.initBtn') }}
                </n-button>
              </n-space>
            </div>
            <!-- 密码错（文件在但打不开）：同样就地给出口（force 覆盖），不逼用户退回列表态 -->
            <div v-else-if="testPasswordWrong" class="repo__test-init">
              <n-text depth="3" class="repo__note">{{ t('repo.passwordWrongHint') }}</n-text>
              <n-space :size="8" align="center">
                <n-button
                  size="tiny"
                  secondary
                  type="warning"
                  @click="emit('password', true)"
                >
                  {{ t('repo.passwordResetBtn') }}
                </n-button>
                <!-- 本地 FS 追加「重建仓库」：重设只重写密码文件，仓库密钥仍是旧的
                     （打不开照旧）；文件夹被旧仓库卡住时重建是唯一恢复路径。
                     远程后端无法自动清空，不显示。 -->
                <n-button
                  v-if="isLocalRepo"
                  size="tiny"
                  secondary
                  type="error"
                  @click="emit('rebuild')"
                >
                  {{ t('repo.rebuildBtn') }}
                </n-button>
              </n-space>
            </div>
          </n-alert>
          <n-alert v-else type="info" :show-icon="false">
            <n-space align="center" justify="space-between">
              <span>{{ t('repo.testIdleHint') }}</span>
              <n-button size="tiny" type="primary" :loading="testing" @click="emit('test')">
                {{ t('repo.testNow') }}
              </n-button>
            </n-space>
          </n-alert>
        </div>

        <!-- init 进度就地展示（编辑态专属）：编辑态整页替换掉列表，行内进度卡不渲染 →
             编辑态点初始化后既看不到进度/日志，done/failed 回调也不会触发（initJobMap 不清、
             结论不作废、查询不失效）。故编辑态自带一份；放在测试结论区之外，避免
             「结论作废 → 区块消失」把终态卡一起带走。 -->
        <JobProgress
          v-if="initJobMap[currentRepoId]"
          :job-id="initJobMap[currentRepoId]"
          :title="t('repo.initJobTitle')"
          hide-progress-bar
          class="repo__init-progress"
          @done="emit('init-done', $event)"
          @failed="emit('init-done', $event)"
        />

        <!-- E1：改过配置但未复验 → 明确告知并挡住不可逆动作（init） -->
        <n-alert v-if="verifyRequired" type="warning" :show-icon="false">
          {{ t('repo.verifyRequired') }}
        </n-alert>

        <!-- 密码状态行：缺失即给出口（与未初始化分支互斥，全页只有一枚密码按钮）。
             覆盖「未初始化以外的失败」场景（凭据/网络等），避免只能退回列表态设密码。 -->
        <n-space align="center" :size="8">
          <n-text depth="3">{{ t('repo.passfileStatus') }}：
            {{ repo?.passfile_status === 'configured'
              ? t('repo.passfileConfigured')
              : t('repo.passfileMissing') }}
          </n-text>
          <n-button
            v-if="passfileMissing && !testNotInitialized"
            size="tiny"
            secondary
            type="primary"
            @click="emit('password', false)"
          >
            {{ t('repo.passwordSetBtn') }}
          </n-button>
        </n-space>
      </template>

      <!-- 底部动作（对齐 Duplicati form-footer 智能主按钮：
           新建 = 创建仓库；有变更且未验证 = 测试并保存（warn，失败转 error）；已验证/无变更 = 保存） -->
      <div class="repo__form-footer">
        <n-button quaternary @click="emit('cancel')">{{ t('common.cancel') }}</n-button>
        <!-- 新建/编辑共用一枚主按钮（语义见 saveButton）：新建态 = 创建并测试 -->
        <n-button :type="saveButton.type" :loading="saving || testing || creating" @click="saveButton.run">
          {{ saveButton.label }}
        </n-button>
      </div>
    </div>
  </n-card>
</template>

<style scoped>
/* 编辑态专属样式自 index.vue 平移（scoped 不穿透子组件，必须随模板一起搬）。
   注：.repo__note / .repo__init-progress 在列表卡与维护卡也用，父页保留一份副本。 */
.repo__note {
  display: block;
  font-size: 12px;
}

.repo__test-block {
  margin-top: 12px;
}

.repo__test-init {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
}

.repo__cred {
  width: 100%;
}

.repo__id-input {
  flex: 1 1 200px;
}

.repo__init-progress {
  width: 100%;
  margin-top: 8px;
}

.repo__id-chip {
  font-size: 12px;
}

.repo__req {
  margin-left: 2px;
  color: var(--n-error-color, #d03050);
  font-weight: 600;
  cursor: help;
}

/* 编辑卡体：flex 纵轴 12px 间距（替代 n-space，原因见上方注释） */
.repo__edit-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 编辑态（对齐 Duplicati destinations 编辑态）：顶部返回行 + 底部动作栏 */
.repo__pick-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.repo__pick-input {
  flex: 1;
  min-width: 0;
}

.repo__edit-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.repo__edit-header {
  display: flex;
}

.repo__form-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--n-divider-color, rgba(128, 128, 140, 0.16));
}
</style>
