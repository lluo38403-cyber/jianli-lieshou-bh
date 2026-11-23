import { useEffect, useState } from 'react'
import { Play, Square } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface SessionSummary {
  id: string
  status: string
  collected_job_ids: string[]
  city_totals: Record<string, number>
  state: {
    phase: string
    round: number
    reason_code?: string
    manual_pending_reason?: string
    started_at: string
    deadline_at: string
    next_wake_at?: string | null
    phase_seconds?: Record<string, number>
    stop_requested?: boolean
  }
}

const phaseLabels: Record<string, string> = {
  collecting: '正在找', waiting_round: '歇一会儿', waiting_quota: '等明天额度',
  waiting_risk: '先冷静一下', waiting_retry: '等它恢复', waiting_user: '等你处理', finished: '结束了',
}

const reasonLabels: Record<string, string> = {
  captcha: '要过一下验证码', login_required: '登录过期了', blocked: '平台不让进了',
  persistent_risk_lock: '平台还在盯着，先等等', rate_limit: '翻得太快了，先歇歇',
  daily_search_page_limit: '今天能翻的搜索页用完了', daily_detail_page_limit: '今天能打开的岗位详情用完了',
  daily_platform_page_limit: '今天能打开的页面都用完了', consecutive_page_failures: '页面连续打不开',
  search_exhausted: '这轮要找的都找完了', no_new_jobs: '列表里没有新岗位',
  search_page_not_advanced: '列表翻到底了', repeated_search_page: '列表翻来翻去都一样',
  no_jobs_for_search: '什么都没搜到',
  session_deadline: '到时间了', user_stopped: '按你的要求停下了',
  timing_gap: '计时断了，你看一眼', persistence_error: '没保存上，你看一下',
  collection_error: '找岗位出了点问题，你看一下', salary_decode_failed: '工资没看懂',
  service_restarted: '服务重启了，等它恢复', restart_retry_exhausted: '重启恢复试太多次了',
  retry_exhausted: '自动恢复试太多次了', clock_unreliable: '系统时间变了',
  session_error: '这次找岗位没跑起来', list_load_unconfirmed: '列表还没稳稳地打开',
}

export function CollectionSessionsPanel() {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [pending, setPending] = useState('')
  useEffect(() => {
    const abort = new AbortController()
    const refresh = async () => {
      try {
        const response = await fetch('/api/collection/sessions', { signal: abort.signal, cache: 'no-store' })
        if (!response.ok) throw new Error('一直找下去的记录没读出来')
        const data = await response.json()
        if (!abort.signal.aborted) { setSessions(Array.isArray(data) ? data.slice(0, 5) : []); setError('') }
      } catch (cause) {
        if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : '读取一直找下去的记录失败')
      }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 15000)
    return () => { abort.abort(); window.clearInterval(timer) }
  }, [revision])
  const act = async (id: string, action: 'stop' | 'continue') => {
    setPending(id)
    try {
      const response = await fetch(`/api/collection/sessions/${encodeURIComponent(id)}/${action}`, { method: 'POST' })
      const result = await response.json()
      if (!response.ok) throw new Error(result.error || '这个操作没成功')
      setError('')
      setRevision(value => value + 1)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '操作失败') }
    finally { setPending('') }
  }
  if (!sessions.length && !error) return null
  return <section className="mt-5 border-t border-card-border pt-4" aria-label="持续找了记录">
    <h3 className="text-sm font-bold">持续采集记录</h3>
    {error && <p role="status" className="mt-2 text-xs text-danger">{error}</p>}
    <div className="mt-2 divide-y divide-card-border">{sessions.map(session => {
      const state = session.state
      const activeMinutes = Math.round((state.phase_seconds?.collecting || 0) / 60)
      const waitingMinutes = Math.round(Object.entries(state.phase_seconds || {}).reduce((sum, [phase, seconds]) => sum + (phase === 'collecting' ? 0 : seconds), 0) / 60)
      return <div key={session.id} className="break-words py-3 text-xs leading-6">
        <div className="flex flex-wrap justify-between gap-x-4"><span className="font-semibold">{new Date(state.started_at).toLocaleString()} · {phaseLabels[state.phase] || state.phase}</span><span>新找到 <strong className="tabular-nums">{session.collected_job_ids.length}</strong> · 第 {state.round} 轮</span></div>
        <p className="text-muted">采集 {activeMinutes} 分钟 · 等了 {waitingMinutes} 分钟 · 到 {new Date(state.deadline_at).toLocaleString()}</p>
        <div className="flex flex-wrap gap-x-4 text-muted">{Object.entries(session.city_totals || {}).map(([city, count]) => <span key={city}>{city} {count}</span>)}</div>
        {(state.manual_pending_reason || state.reason_code) && <p className={state.manual_pending_reason ? 'text-warning' : 'text-muted'}>{state.manual_pending_reason ? '等你处理：' : '怎么回事：'}{reasonLabels[state.manual_pending_reason || state.reason_code || ''] || state.manual_pending_reason || state.reason_code}</p>}
        {state.next_wake_at && <p className="text-muted">最早恢复时间 {new Date(state.next_wake_at).toLocaleString()}</p>}
        {session.status === 'running' && <div className="mt-1 flex gap-2">
          {state.phase === 'waiting_user' && <Button size="sm" variant="secondary" disabled={Boolean(pending) || state.stop_requested} onClick={() => void act(session.id, 'continue')}><Play className="h-3.5 w-3.5" />接着找</Button>}
          <Button size="sm" variant="secondary" disabled={Boolean(pending) || state.stop_requested} onClick={() => void act(session.id, 'stop')}><Square className="h-3.5 w-3.5" />{state.stop_requested ? '正在停下' : '全部停下'}</Button>
        </div>}
      </div>
    })}</div>
  </section>
}
