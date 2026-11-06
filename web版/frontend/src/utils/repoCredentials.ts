/**
 * 仓库凭据字段表与地址建议值的单一来源（方案 v1.1 §4.3 F14c）。
 *
 * 存在理由：这些表原先定义在 `pages/repo/index.vue`（CRED_FIELDS / ENDPOINT_OPTIONS）
 * 与 `pages/repo/RepoEditForm.vue`（REGION_OPTIONS）。任务向导的建仓步要复用同一套
 * 表单与凭据语义，若在向导里再抄一份，两处字段不一致时错误只会在运行期暴露
 * （§6.1 R6 漂移面）。抽到纯常量模块后，页面、表单、向导三处同源。
 *
 * 纯常量 + 纯类型，无副作用、无 IO——可在任何层 import。
 */

import type { RepoTypeKey } from './repoUrl'

export interface CredField {
  /** 配置键后缀；完整键 = cred.{repo_id}.{suffix}（R-023 命名空间化）。 */
  suffix: string
  /** i18n 标签键。 */
  labelKey: string
  /** secret = 密码框；region = 常用值下拉建议（可手填）；text = 明文（ID/名称/路径等非敏感项）。
   *  曾有的 endpoint 类已退役：restic 0.19.1 的 s3 后端 ApplyEnvironment 只读
   *  AWS_DEFAULT_REGION，AWS_ENDPOINT_URL 全库零引用（minio-go v7.1.0 实证）——
   *  自定义 S3 服务器的正确配置位是地址区的「服务器地址」（进 URL 首段）。 */
  kind: 'text' | 'secret' | 'region'
}

/** 凭据键表（§4.3）：基础后缀；完整键 = cred.{repo_id}.{suffix}（R-023 命名空间化）。
 *  A1 新增后端（restic 0.19.1 官方 env 逐一登记；sftp 无凭据字段——免密登录；
 *  rclone 无凭据字段——conf 宿主机托管，Q17 只读档位）。 */
export const CRED_FIELDS: Record<RepoTypeKey, CredField[]> = {
  local: [],
  s3: [
    { suffix: 'aws_access_key_id', labelKey: 'repo.credAwsAccessKeyId', kind: 'text' },
    { suffix: 'aws_secret_access_key', labelKey: 'repo.credAwsSecretAccessKey', kind: 'secret' },
    { suffix: 'aws_default_region', labelKey: 'repo.credAwsDefaultRegion', kind: 'region' },
  ],
  b2: [
    { suffix: 'b2_account_id', labelKey: 'repo.credB2AccountId', kind: 'text' },
    { suffix: 'b2_account_key', labelKey: 'repo.credB2AccountKey', kind: 'secret' },
  ],
  sftp: [],
  azure: [
    { suffix: 'azure_account_name', labelKey: 'repo.credAzureAccountName', kind: 'text' },
    { suffix: 'azure_account_key', labelKey: 'repo.credAzureAccountKey', kind: 'secret' },
  ],
  gcs: [
    { suffix: 'google_project_id', labelKey: 'repo.credGoogleProjectId', kind: 'text' },
    { suffix: 'google_application_credentials', labelKey: 'repo.credGoogleApplicationCredentials', kind: 'text' },
  ],
  rest: [
    { suffix: 'restic_rest_username', labelKey: 'repo.credRestUsername', kind: 'text' },
    { suffix: 'restic_rest_password', labelKey: 'repo.credRestPassword', kind: 'secret' },
  ],
  rclone: [],
}

/** 仓库类型下拉的单一来源（方案 v1.1 §4.3 F14c / §6.1 R6）。
 *  原先 `pages/repo/index.vue` 与 `components/task/NewRepoStep.vue` 各写一份 8 项列表，
 *  改一处忘另一处两处下拉就会静默漂移。这里只存类型键与 i18n 标签键（文案仍由消费方
 *  用 t() 解析，保持本模块纯常量、无 i18n 依赖）；顺序即下拉展示顺序。 */
export const REPO_TYPE_PRESETS: ReadonlyArray<{ key: RepoTypeKey; labelKey: string }> = [
  { key: 'local', labelKey: 'repo.typeLocal' },
  { key: 'rclone', labelKey: 'repo.typeRclone' },
  { key: 's3', labelKey: 'repo.typeS3' },
  { key: 'b2', labelKey: 'repo.typeB2' },
  { key: 'sftp', labelKey: 'repo.typeSftp' },
  { key: 'azure', labelKey: 'repo.typeAzure' },
  { key: 'gcs', labelKey: 'repo.typeGcs' },
  { key: 'rest', labelKey: 'repo.typeRest' },
]

/** 常用 S3 Endpoint 建议值（MinIO 本机 / 阿里云 OSS / 腾讯云 COS / AWS 官方）。
 *  用于地址区「服务器地址」输入（endpointSuggestions 在其后拼接既有仓库提取值）。 */
export const ENDPOINT_OPTIONS = [
  'http://127.0.0.1:9000',
  'https://oss-cn-hangzhou.aliyuncs.com',
  'https://oss-cn-beijing.aliyuncs.com',
  'https://oss-cn-shanghai.aliyuncs.com',
  'https://cos.ap-beijing.myqcloud.com',
  'https://cos.ap-shanghai.myqcloud.com',
  'https://s3.amazonaws.com',
]

/** 常用区域建议值（S3 URL 区域段与 AWS_DEFAULT_REGION 共用）。 */
export const REGION_OPTIONS = [
  'cn-north-1',
  'cn-northwest-1',
  'ap-east-1',
  'ap-southeast-1',
  'us-east-1',
  'us-west-1',
  'us-west-2',
  'eu-west-1',
]

/** 字段类型 → 建议值列表（null = 无建议，纯输入）。 */
export function credOptions(kind: CredField['kind']): string[] | null {
  return kind === 'region' ? REGION_OPTIONS : null
}
