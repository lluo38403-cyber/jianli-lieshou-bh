import type { WorkbenchTask } from '@/hooks/useDashboard'

interface ScoringProgressProps {
  metrics?: Record<string, number | string>
  status: string
}

function count(value: number | string | undefined): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : 0
}

/** Counts processed jobs, including filtered and failed jobs, rather than API requests. */
export function ScoringProgress({ metrics, status }: ScoringProgressProps) {
  const total = count(metrics?.ai_total)
  const completed = total > 0 ? Math.min(count(metrics?.ai_completed), total) : 0
  const percent = total > 0 ? Math.floor(completed * 100 / total) : undefined
  const state = status === 'paused' ? '先停下了'
    : status === 'stopping' ? '正在停下'
    : status === 'stopped' ? '已经停了'
    : status === 'failed' ? '这次没打成'
    : status === 'completed_with_errors' ? '打完了，有几个没打好'
    : total > 0 && completed === total ? '这轮打完了'
    : status === 'completed' ? '已经结束了'
    : '正在打分'

  const emptyText = ['running', 'stopping'].includes(status) ? '正在准备要打分的岗位…' : '还没开始打分'

  return (
    <section className="mt-3 rounded-xl border border-card-border bg-surface px-3 py-3" aria-label="这轮打分进度">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="font-semibold">这轮打分 · {state}</span>
        <span className="tabular-nums text-muted">{total > 0 ? `已打 ${completed} / ${total} 个 · ${percent}%` : emptyText}</span>
      </div>
      <div
        role="progressbar"
        aria-label="打分进度"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={total > 0 ? `${state}，已处理 ${completed} / ${total} 个岗位，${percent}%` : `${state}，${emptyText}`}
        className="mt-2 h-2 overflow-hidden rounded-full bg-secondary"
      >
        <div className={`h-full rounded-full transition-[width] duration-500 motion-reduce:transition-none ${status === 'failed' ? 'bg-danger' : 'bg-primary'} ${percent === undefined && status === 'running' ? 'animate-pulse motion-reduce:animate-none' : ''}`} style={{ width: `${percent ?? (status === 'running' ? 15 : 0)}%` }} />
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        <span>通过 {count(metrics?.ai_passed)} 个</span>
        <span>筛掉 {count(metrics?.ai_filtered)} 个</span>
        <span className={count(metrics?.ai_failed) > 0 ? 'text-danger' : undefined}>没打好 {count(metrics?.ai_failed)} 个</span>
        <span>按岗位数算，重试的不重复算；接着打的时候显示这轮还剩多少。</span>
      </div>
    </section>
  )
}

export function TaskScoringProgress({ task }: { task: WorkbenchTask | null }) {
  if (!task || (task.mode !== 'score' && task.mode !== 'rescore'
    && task.metrics?.ai_total === undefined && task.progress?.outcome !== 'scoring')) return null
  return <ScoringProgress metrics={task.metrics} status={task.status} />
}
