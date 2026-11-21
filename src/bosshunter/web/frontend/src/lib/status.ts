export const STATUS_LABELS: Record<string, string> = {
  pending: '还没打分',
  scored: '已打分',
  filtered: '已筛掉',
  ready: '等着投',
  approved: '已通过',
  skipped: '已跳过',
  sent: '已投出',
  replied: 'HR 回了',
  resume_sent: '简历已发',
  needs_resume: '要手动发简历',
  follow_up_sent: '已问过一次',
  rejected: '不要了',
  error: '没投出去',
}

export const ACTION_LABELS: Record<string, string> = {
  status_changed: '你改了状态',
  scrape: '找到的',
  scored: '打分',
  filtered: '筛掉',
  ready: '等着投',
  approved: '通过',
  skipped: '跳过',
  sent: '投出去了',
  manual_sent: '你标记已投',
  replied: '回了消息',
  hr_reply_detected: 'HR 回消息了',
  reply_pending: '等你确认回复',
  reply_dismissed: '不想回了',
  auto_replied: '自动回了',
  resume_failed: '简历没做出来',
  resume_failed_dismissed: '不管简历失败了',
  resume_sent: '简历已发',
  needs_resume: '要手动发简历',
  follow_up_sent: '问过一次了',
  rejected: '不要了',
  error: '出问题了',
}

export function getStatusLabel(status: string | null | undefined): string {
  if (!status) return '未知'
  return STATUS_LABELS[status] ?? status
}

/** 已投出岗位的状态：与后端 db.py 的 APPLIED_STATUSES 是同一份
 * 口径（sent / replied / resume_sent / needs_resume / follow_up_sent）。
 * 前端只用它来画筛选选项、判断一行是不是已投出；真正决定哪页能看到
 * 哪类岗位的是后端 /api/jobs/search 的分池，前端筛选项改不改都不影响。 */
export const APPLIED_JOB_STATUSES = ['sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent'] as const

export function isAppliedJobStatus(status: string | null | undefined): boolean {
  if (!status) return false
  return (APPLIED_JOB_STATUSES as readonly string[]).includes(status)
}

export function getActionLabel(action: string | null | undefined): string {
  if (!action) return '未知'
  return ACTION_LABELS[action] ?? action
}

/** 平台登录状态（GET /api/workbench 的 platform_login 字段），大白话。 */
export type PlatformLoginStatus = 'logged_in' | 'logged_out' | 'unknown'

export const PLATFORM_LOGIN_STATUS_LABELS: Record<PlatformLoginStatus, string> = {
  logged_in: '已登录',
  logged_out: '已掉线',
  unknown: '还没查过',
}

export function getPlatformLoginStatusLabel(status: string | null | undefined): string {
  if (status === 'logged_in') return PLATFORM_LOGIN_STATUS_LABELS.logged_in
  if (status === 'logged_out') return PLATFORM_LOGIN_STATUS_LABELS.logged_out
  return PLATFORM_LOGIN_STATUS_LABELS.unknown
}
