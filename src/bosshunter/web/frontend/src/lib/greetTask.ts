import type { WorkbenchTask } from '@/hooks/useDashboard'

const GREET_PAUSE_REASON_LABELS: Record<string, string> = {
  auth: '服务没通过验证',
  token_quota: '额度或余额不够了',
  rate_limit: '打分太频繁，先缓一缓',
  context_limit: '内容太长，装不下了',
  output_limit: '写的内容超出了限制',
  network: '网络连不上或超时了',
  request_failed: '打分服务这次没接通',
  output_truncated: '写到一半被截断了',
  empty_response: '这次没拿到打分结果',
}

export function greetPauseReasonLabel(reason: unknown): string {
  const raw = typeof reason === 'string' ? reason.trim() : ''
  if (!raw) return ''
  const kindMatch = raw.match(/\(([a-z_]+)(?:\s*,\s*status=\d+)?\)\s*$/)
  const kind = kindMatch?.[1]
  return (kind && GREET_PAUSE_REASON_LABELS[kind]) || raw
}

export function describeGreetTaskOutcome(task: WorkbenchTask): string {
  const metrics = task.metrics ?? {}
  const conflictCount = task.progress?.conflict_ids?.length ?? 0
  const base = `打招呼写完了：新写了 ${metrics.greet_generated ?? 0} 条，沿用以前的 ${metrics.greet_preserved ?? 0} 条，失败 ${metrics.greet_failed ?? 0} 条`
    + (conflictCount ? `；${conflictCount} 个岗位状态变了，没保存` : '')
  if (metrics.greet_paused) {
    const label = greetPauseReasonLabel(metrics.greet_pause_reason) || '打分服务出了点问题'
    return `${base}；因为${label}提前停下了，写好的都留着，剩下的下次继续`
  }
  return base
}
