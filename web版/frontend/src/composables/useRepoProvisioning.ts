/**
 * 建仓编排（方案 v1.1 §4.3 F14b）。
 *
 * 存在理由：仓库页 `pages/repo/index.vue` 的 runCreate / onCreateAndTest 是这套编排的
 * 原型，任务向导的建仓步需要**同一套**语义。若在向导里重写一份，两步之间任何一处
 * 调整（顺序、幂等收敛、错误文案）都会静默分叉（§6.1 R6）。此处上提为单一实现点。
 *
 * 顺序是硬约束，不得调换（§3.1 / §3.2）：
 *   createRepo          落行，但**不建密码文件**
 *   setRepoPassword     建密码文件——凭据加密密钥源；缺它写 cred.* 必 422
 *   putConfig(cred.*)   写凭据（要求密码文件已存在，否则 CredentialKeyUnavailableError）
 *   ui.setCurrentRepoId 切仓——init 作业按 X-Repo-Id 定目标仓库（routers/jobs.py:62）
 *   createJob('init')   初始化仓库（远程后端同样可用；**绝不可换 rebuild**，远程 422）
 *   testRepo            后端读已落库配置，必须最后
 *
 * 为什么不用 rebuild：services/repo_lifecycle.py 对远程后端直接 422，
 * 且 rebuild 会清空既有快照——对「刚建的新仓库」既不可用也不该用。
 */

import { ref } from 'vue'
import { putConfig } from '@/api/config'
import { createJob } from '@/api/jobs'
import { createRepo, setRepoPassword, testRepo } from '@/api/repo'
import type { RepoCreateRequest, RepoTestResponse } from '@/api/types'
import { CRED_FIELDS } from '@/utils/repoCredentials'
import type { RepoTypeKey } from '@/utils/repoUrl'

export interface ProvisionInput {
  /** 仓库标识；留空 = 服务端生成 r<YYMMDD>-xxxxxx。 */
  id: string
  name: string
  /** 已组译好的 restic URL（null = 暂不设地址）。 */
  url: string | null
  hostTag: string | null
  /** 表单所选类型（决定凭据字段集）。 */
  type: RepoTypeKey
  /** 凭据输入，键 = 后缀（如 aws_access_key_id）。 */
  credInput: Record<string, string>
  /** 自填仓库密码；留空 = 服务端生成强随机并一次性回显。 */
  password?: string
}

export interface ProvisionResult {
  repoId: string
  /** 仅服务端生成时回显；自填时为 null。明文不得落 localStorage / 审计（红线 S3）。 */
  generatedPassword: string | null
  /** init 作业 id（异步；终态经 useJobStream 或轮询取得）。 */
  initJobId: string
  /** 建仓后立刻测试的结论（失败也返回，由调用方决定展示）。 */
  test: RepoTestResponse
}

export interface ProvisionDeps {
  /** 切当前仓库（stores/ui.ts）。init 作业按它定目标，必须在提交作业之前调用。 */
  setCurrentRepoId: (id: string) => void
  /** 当前仓库标识（测试用；缺省不读）。 */
  currentRepoId?: () => string
  /** 仓库列表 / 配置查询失效钩子（调用方注入 vue-query 的 invalidateQueries）。 */
  onRepoCreated?: (id: string) => Promise<void> | void
}

export function useRepoProvisioning(deps: ProvisionDeps) {
  const running = ref(false)
  /** 已创建的仓库 id：失败重试时复用它，避免重复建仓（幂等收敛）。 */
  const createdRepoId = ref<string | null>(null)

  /** 仅落库（不含密码/凭据/init/测试）——供需要分步控制的调用方单独使用。 */
  async function create(input: ProvisionInput): Promise<string> {
    const body: RepoCreateRequest = {
      id: input.id.trim() || undefined,
      name: input.name.trim(),
      url: input.url?.trim() || null,
      host_tag: input.hostTag?.trim() || null,
    }
    const created = await createRepo(body)
    createdRepoId.value = created.id
    await deps.onRepoCreated?.(created.id)
    return created.id
  }

  /** 建密码文件（凭据加密密钥源）。已存在时后端 422——调用方按「已配置」收敛，不当失败。
   *  `force` = 覆盖已有文件：仅「仓库已打不开（密码错误）」场景后端才接受（见
   *  routers/repos_status.py 的覆盖前置门禁——仓库仍能打开时覆盖＝自毁仓库，后端会拒）。 */
  async function writePassword(repoId: string, password?: string, force = false): Promise<string | null> {
    const body: { password?: string; force?: boolean } = {}
    if (password) body.password = password
    if (force) body.force = true
    const result = await setRepoPassword(repoId, body)
    return result.generated ? result.password : null
  }

  /** 写凭据：键 = cred.{repoId}.{suffix}。空值跳过（不覆盖已有值）。 */
  async function writeCredentials(repoId: string, type: RepoTypeKey, credInput: Record<string, string>): Promise<void> {
    const patch: Record<string, string | null> = {}
    for (const field of CRED_FIELDS[type]) {
      const value = credInput[field.suffix]
      if (value) {
        patch[`cred.${repoId}.${field.suffix}`] = value
      }
    }
    if (Object.keys(patch).length > 0) {
      await putConfig(patch)
    }
  }

  /** 提交 init 作业。调用前必须已把 currentRepoId 切到 repoId（后端按请求头定目标）。 */
  async function submitInit(): Promise<string> {
    const created = await createJob('init')
    return created.id
  }

  /**
   * 全链路：建仓 → 密码 → 凭据 → 切仓 → init → 测试。
   * 任一步抛错即中断，已创建的仓库 id 留在 createdRepoId（供重试复用 / 提示用户）。
   */
  async function provision(input: ProvisionInput): Promise<ProvisionResult> {
    running.value = true
    try {
      const repoId = createdRepoId.value ?? (await create(input))
      const generatedPassword = await writePassword(repoId, input.password)
      await writeCredentials(repoId, input.type, input.credInput)
      // 切仓必须先于 init 提交：作业目标仓库取自 X-Repo-Id（routers/jobs.py:62）
      deps.setCurrentRepoId(repoId)
      const initJobId = await submitInit()
      const test = await testRepo(repoId)
      return { repoId, generatedPassword, initJobId, test }
    } finally {
      running.value = false
    }
  }

  /** 丢弃「已创建」记忆（撤销 / 换表单时调用，让下次从零开始）。 */
  function reset(): void {
    createdRepoId.value = null
  }

  return { running, createdRepoId, create, writePassword, writeCredentials, submitInit, provision, reset }
}
