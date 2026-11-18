import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ScoringProgress } from './ScoringProgress'
import { Button } from '@/components/ui/button'

type ScoreScope = 'pending' | 'failed' | 'selected' | 'all_scored'

interface ScorePreview {
  eligible_jobs: number
  skipped_jobs: number
  first_attempt_requests: number
  max_attempts_per_job: number
  max_possible_requests: number
  note: string
}

interface ScoreJobsDialogProps {
  open: boolean
  selectedJobIds: string[]
  onClose: () => void
  /** 返回启动结果：成功返回 ok:true（弹窗自动关闭），失败返回错误信息（弹窗保留并展示）。 */
  onStart: (options: { scope: ScoreScope; limit: number | null; job_ids: string[]; force_rescore: boolean }) => Promise<{ ok: boolean; error?: string }>
}

interface ScoringRun {
  id: string
  status: string
  remaining_job_ids: string[]
  progress?: Record<string, number>
  pause_reason?: string
  recoverable?: boolean
}

const scopeLabels: Record<ScoreScope, string> = {
  pending: '没打过分的 / 打分失败的',
  failed: '只重新打失败的',
  selected: '找工作里选中的',
  all_scored: '全部重新打一遍',
}

export function ScoreJobsDialog({ open, selectedJobIds, onClose, onStart }: ScoreJobsDialogProps) {
  const [scope, setScope] = useState<ScoreScope>('pending')
  const [limitText, setLimitText] = useState('全部')
  const [preview, setPreview] = useState<ScorePreview | null>(null)
  const [runs, setRuns] = useState<ScoringRun[]>([])
  const [runsError, setRunsError] = useState('')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const runsRequestRef = useRef<AbortController | null>(null)
  const runsRequestRevisionRef = useRef(0)

  const normalizedLimit = limitText.trim()
  const limitValid = normalizedLimit === ''
    || normalizedLimit === '全部'
    || normalizedLimit.toLowerCase() === 'all'
    || (/^\d+$/.test(normalizedLimit) && Number(normalizedLimit) >= 1 && Number(normalizedLimit) <= 10000)
  const limit = useMemo(() => {
    if (normalizedLimit === '' || normalizedLimit === '全部' || normalizedLimit.toLowerCase() === 'all') return null
    return Number(normalizedLimit)
  }, [limitText])

  const loadPreview = async () => {
    if (!limitValid) {
      setPreview(null)
      setMessage('打分数量填 1 到 10000 的整数，或者填“全部”。')
      return
    }
    setLoading(true)
    setMessage('')
    try {
      const forceRescore = scope === 'all_scored'
      const jobIds = scope === 'selected' ? selectedJobIds : []
      const res = await fetch('/api/scoring/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ options: { scope, limit, job_ids: jobIds, force_rescore: forceRescore } }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.error || '没算出来能打多少分')
      setPreview(data)
    } catch (err) {
      setPreview(null)
      setMessage(err instanceof Error ? err.message : '无法生成评分预览')
    } finally {
      setLoading(false)
    }
  }

  const loadRuns = useCallback(async () => {
    // A slow response must not overlap the next poll and overwrite a newer progress value.
    if (runsRequestRef.current) return
    const controller = new AbortController()
    const revision = ++runsRequestRevisionRef.current
    runsRequestRef.current = controller
    const timeout = window.setTimeout(() => controller.abort(), 15000)
    try {
      const res = await fetch('/api/scoring/runs', { cache: 'no-store', signal: controller.signal })
      if (!res.ok) throw new Error('打分进度刷不出来，请检查服务还在不在。')
      const data: ScoringRun[] = await res.json()
      if (!controller.signal.aborted && runsRequestRef.current === controller && revision === runsRequestRevisionRef.current) {
        setRuns(data)
        setRunsError('')
      }
    } catch {
      if (!controller.signal.aborted && runsRequestRef.current === controller && revision === runsRequestRevisionRef.current) {
        setRunsError('无法刷新评分进度，请检查服务连接。')
      }
    } finally {
      window.clearTimeout(timeout)
      if (runsRequestRef.current === controller) runsRequestRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!open) return
    void loadRuns()
    const timer = window.setInterval(() => void loadRuns(), 3000)
    return () => {
      window.clearInterval(timer)
      runsRequestRevisionRef.current += 1
      runsRequestRef.current?.abort()
      runsRequestRef.current = null
    }
  }, [open, loadRuns])

  useEffect(() => {
    if (open) {
      void loadPreview()
    }
  }, [open, scope, limit, selectedJobIds.join(',')])

  if (!open) return null

  const start = async () => {
    if (!preview?.eligible_jobs) return
    if (scope === 'all_scored' && !window.confirm('要把所有能重新打分的岗位再打一遍，已经在投的不会动。确定继续吗？')) return
    if (!window.confirm(`这轮最多打 ${preview.max_possible_requests} 次分，现在开始吗？`)) return
    setLoading(true)
    setMessage('')
    try {
      const result = await onStart({ scope, limit, job_ids: scope === 'selected' ? selectedJobIds : [], force_rescore: scope === 'all_scored' })
      if (result.ok) {
        onClose()
        return
      }
      setMessage(result.error || '打分没启动，过会儿再试。')
    } catch (err) {
      setMessage(err instanceof Error ? err.message : '启动评分失败，请稍后重试。')
    } finally {
      setLoading(false)
    }
  }

  const runAction = async (run: ScoringRun, action: 'pause' | 'resume' | 'end') => {
    setLoading(true)
    setMessage('')
    try {
      const res = await fetch(`/api/scoring/runs/${run.id}/${action}`, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.error || '这个操作没成功')
      await loadRuns()
      await loadPreview()
      setMessage(action === 'resume' ? '已经接着剩下的岗位继续打分了。' : action === 'pause' ? '已经让它停下了，打好的分都留着。' : '打分任务结束了。')
    } catch (err) {
      setMessage(err instanceof Error ? err.message : '评分任务操作失败')
    } finally {
      setLoading(false)
    }
  }

  const activeRun = runs.find(run => run.status === 'running' || run.status === 'paused')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-5">
      <div className="w-full max-w-xl rounded-3xl border border-card-border bg-card p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs font-black tracking-[0.18em] text-primary">给岗位打分</div>
            <h3 className="mt-1 text-2xl font-black">单独打分</h3>
            <p className="mt-1 text-sm leading-6 text-muted">就用现在找工作里的岗位，不重新找；已经在投的不会重新打分。</p>
          </div>
          <Button variant="secondary" size="sm" onClick={onClose}>关闭</Button>
        </div>
        <div className="mt-5 space-y-4">
          <label className="block text-xs font-bold text-muted">
            给哪些岗位打分
            <select value={scope} onChange={event => setScope(event.target.value as ScoreScope)} className="mt-1 w-full rounded-xl border border-card-border bg-surface px-3 py-2 text-sm text-foreground outline-none focus:border-primary">
              {Object.entries(scopeLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
            </select>
          </label>
          <label className="block text-xs font-bold text-muted">
            打多少个（填正整数，或者填“全部”）
            <input value={limitText} onChange={event => setLimitText(event.target.value)} className="mt-1 w-full rounded-xl border border-card-border bg-surface px-3 py-2 text-sm text-foreground outline-none focus:border-primary" />
          </label>
          {scope === 'selected' && <div className="rounded-xl bg-secondary px-3 py-2 text-sm text-primary">找工作里选了 {selectedJobIds.length} 条</div>}
          {preview && (
            <div className="grid grid-cols-2 gap-2 text-sm">
              <Metric label="符合条件的" value={preview.eligible_jobs} />
              <Metric label="会跳过" value={preview.skipped_jobs} />
              <Metric label="第一轮最多打几次" value={preview.first_attempt_requests} />
              <Metric label="算上重试最多打几次" value={preview.max_possible_requests} />
            </div>
          )}
          <div className="rounded-2xl border border-warning-border bg-warning-soft p-3 text-xs leading-5 text-warning-strong">
            先看一眼不花钱，正式打分才开始算；个别岗位格式不对会记下来接着打，网络或服务出问题会先停下来。
          </div>
          {runs.filter(run => ['running', 'paused'].includes(run.status)).slice(0, 3).map(run => (
            <div key={run.id} className="rounded-2xl border border-card-border bg-surface p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-black">{run.status === 'running' ? '正在打分' : '先停下了'} · 还剩 {run.remaining_job_ids.length} 个岗位</span><div className="flex gap-2">{run.status === 'running' && <Button variant="secondary" size="sm" onClick={() => void runAction(run, 'pause')}>暂停</Button>}{run.recoverable && <Button size="sm" onClick={() => void runAction(run, 'resume')}>继续</Button>}<Button variant="ghost" size="sm" onClick={() => void runAction(run, 'end')}>结束</Button></div></div>
              <ScoringProgress metrics={run.progress} status={run.status} />
              {run.pause_reason && <p className="mt-1 text-xs text-muted">{run.pause_reason}</p>}
            </div>
          ))}
          {runsError && <p className="text-sm text-danger" role="status">{runsError}</p>}
          {message && <div className="rounded-xl bg-secondary px-3 py-2 text-sm text-primary">{message}</div>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={onClose}>取消</Button>
            <Button onClick={start} disabled={loading || !limitValid || !preview?.eligible_jobs || Boolean(activeRun)}>{loading ? '正在处理...' : '开始打分'}</Button>
          </div>
        </div>
      </div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: number }) {
  return <div className="rounded-xl border border-card-border bg-surface p-3"><div className="text-xs text-muted">{label}</div><div className="mt-1 text-xl font-black text-primary">{value}</div></div>
}
