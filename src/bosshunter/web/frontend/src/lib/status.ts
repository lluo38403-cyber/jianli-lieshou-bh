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

export function getActionLabel(action: string | null | undefined): string {
  if (!action) return '未知'
  return ACTION_LABELS[action] ?? action
}
