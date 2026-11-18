import { useEffect, useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, RefreshCw, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import type { WorkbenchTask } from '@/hooks/useDashboard'
import { PLATFORM_LABELS, PLATFORM_SHORT_LABELS } from '@/lib/platforms'

type PlatformId = 'boss' | 'zhilian' | '51job' | 'liepin'

interface PlatformDraft {
  enabled: boolean
  keywords: string
  cities: string
  cityCodes: string
  maxPages: string
  sort: string
}

interface PlatformCityOption {
  name: string
  code: string
}

interface ResumableRun {
  id: string
  created_at: string
  can_resume: boolean
  options: {
    auto_score: boolean
    platforms: { boss: { keywords: string[]; cities: string[]; max_pages: number } }
  }
}

interface CollectJobsDialogProps {
  open: boolean
  mode?: 'collect' | 'full'
  activeTask: WorkbenchTask | null
  onClose: () => void
  /** 返回启动结果：成功返回 ok:true（弹窗自动关闭），失败返回错误信息（弹窗保留并展示）。 */
  onStart: (options: Record<string, unknown>) => Promise<{ ok: boolean; error?: string }>
}

const initialDrafts: Record<PlatformId, PlatformDraft> = {
  boss: { enabled: true, keywords: '', cities: '', cityCodes: '', maxPages: '3', sort: 'default' },
  zhilian: { enabled: false, keywords: '', cities: '', cityCodes: '', maxPages: '1', sort: 'default' },
  '51job': { enabled: false, keywords: '', cities: '上海', cityCodes: '上海=020000', maxPages: '1', sort: 'default' },
  liepin: { enabled: false, keywords: '', cities: '', cityCodes: '', maxPages: '1', sort: 'default' },
}

function splitValues(value: string) {
  return value.split(/[,，\n]/).map(item => item.trim()).filter(Boolean)
}

function parseCityCodes(value: string) {
  const result: Record<string, string> = {}
  value.split(/[,，\n]/).forEach(item => {
    const [city, code] = item.split(/[=:：]/).map(part => part.trim())
    if (city && code) result[city] = code
  })
  return result
}

function normalizeCityName(value: string) {
  const city = value.trim()
  return city.replace(/市$/, '')
}

function findPlatformCity(city: string, options: PlatformCityOption[]) {
  const target = normalizeCityName(city)
  return options.find(option => normalizeCityName(option.name) === target)
}

function draftFromConfig(config: Record<string, any> | null, platform: PlatformId): PlatformDraft {
  const legacy = platform === 'boss' && config?.search && typeof config.search === 'object' ? config.search : {}
  const specific = config?.platforms?.[platform]?.search && typeof config.platforms[platform].search === 'object'
    ? config.platforms[platform].search
    : {}
  const configured = {
    ...legacy,
    ...specific,
    keywords: specific.keywords?.length ? specific.keywords : legacy.keywords,
    cities: specific.cities?.length ? specific.cities : legacy.cities,
    city_codes: Object.keys(specific.city_codes || {}).length ? specific.city_codes : legacy.city_codes,
    max_pages: specific.max_pages || legacy.max_pages,
    sort: specific.sort || legacy.sort,
  }
  const cities = Array.isArray(configured.cities) && configured.cities.length
    ? configured.cities
    : platform === 'boss' ? (config?.profile?.target_cities || []) : []
  return {
    enabled: config?.platforms?.[platform]?.enabled ?? platform === 'boss',
    keywords: Array.isArray(configured.keywords) ? configured.keywords.join(', ') : '',
    cities: cities.join(', '),
    cityCodes: configured.city_codes && typeof configured.city_codes === 'object'
      ? Object.entries(configured.city_codes).map(([city, code]) => `${city}=${code}`).join(', ')
      : '',
    maxPages: String(configured.max_pages || (platform === 'boss' ? 3 : 1)),
    sort: configured.sort || (platform === 'boss' ? 'default' : 'default'),
  }
}

export function CollectJobsDialog({ open, mode = 'collect', activeTask, onClose, onStart }: CollectJobsDialogProps) {
  const [drafts, setDrafts] = useState(initialDrafts)
  const [order, setOrder] = useState<PlatformId[]>(['boss'])
  const [autoScore, setAutoScore] = useState(false)
  const [cityPolicy, setCityPolicy] = useState({ enabled: true, warmup_minutes: 10, window_minutes: 5, min_new_jobs: 5 })
  const [continuous, setContinuous] = useState(false)
  const [durationHours, setDurationHours] = useState(8)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  const [resumableRuns, setResumableRuns] = useState<ResumableRun[]>([])
  const [resumeRunId, setResumeRunId] = useState('')
  const [zhilianCities, setZhilianCities] = useState<PlatformCityOption[]>([])
  const [job51Cities, setJob51Cities] = useState<PlatformCityOption[]>([])
  const [liepinCities, setLiepinCities] = useState<PlatformCityOption[]>([])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setResumableRuns([])
    setResumeRunId('')
    setContinuous(false)
    if (mode === 'collect') {
      fetch('/api/collection/runs?limit=100', { cache: 'no-store' })
        .then(response => {
          if (!response.ok) throw new Error('上次没做完的任务没读出来')
          return response.json()
        })
        .then((runs: ResumableRun[]) => {
          if (cancelled) return
          const available = runs.filter(run => run.can_resume)
          setResumableRuns(available)
          setResumeRunId(available[0]?.id || '')
        })
        .catch(() => {
          if (!cancelled) setError('上次没做完的任务没读出来，关掉窗口再试试。')
        })
    }
    fetch('/api/config', { cache: 'no-store' })
      .then(response => response.json())
      .then(config => {
        if (cancelled) return
        const nextBoss = draftFromConfig(config, 'boss')
        const nextZhilian = draftFromConfig(config, 'zhilian')
        const nextJob51 = draftFromConfig(config, '51job')
        const nextLiepin = draftFromConfig(config, 'liepin')
        if (mode === 'full') {
          nextZhilian.enabled = false
          nextJob51.enabled = false
          nextLiepin.enabled = false
        }
        const configuredOrder = Array.isArray(config?.collection?.default_order) ? config.collection.default_order : ['boss']
        const enabledFromConfig = (['boss', 'zhilian', '51job', 'liepin'] as PlatformId[]).filter(platform => config?.platforms?.[platform]?.enabled === true)
        const nextOrder = [...configuredOrder, ...enabledFromConfig].filter((item: unknown, index, values): item is PlatformId =>
          (item === 'boss' || item === 'zhilian' || item === '51job' || item === 'liepin') && values.indexOf(item) === index,
        )
        setDrafts({ boss: nextBoss, zhilian: nextZhilian, '51job': nextJob51, liepin: nextLiepin })
        setOrder(mode === 'full' ? ['boss'] : (nextOrder.length ? nextOrder : ['boss']))
        setAutoScore(mode === 'full' || config?.collection?.auto_score_default === true)
        setCityPolicy({ enabled: true, warmup_minutes: 10, window_minutes: 5, min_new_jobs: 5, ...(config?.platforms?.boss?.search?.city_yield_policy || config?.search?.city_yield_policy || {}) })
      })
      .catch(() => {
        if (!cancelled) setError('默认设置没读出来，直接填好就能开始。')
      })
    fetch('/api/cities?platform=zhilian', { cache: 'no-store' })
      .then(response => response.json())
      .then(data => {
        if (cancelled) return
        if (Array.isArray(data.cities)) setZhilianCities(data.cities)
        if (!data.ok && !cancelled) setError(data.error || '智联的城市列表没读出来。')
      })
      .catch(() => {
        if (!cancelled) setError('智联的城市列表没读出来，过会儿再试。')
      })
    fetch('/api/cities?platform=51job', { cache: 'no-store' })
      .then(response => response.json())
      .then(data => {
        if (cancelled) return
        if (Array.isArray(data.cities)) setJob51Cities(data.cities)
        if (!data.ok) setError(data.error || '前程无忧的城市列表没读出来。')
      })
      .catch(() => {
        if (!cancelled) setError('前程无忧的城市列表没读出来，过会儿再试。')
      })
    fetch('/api/cities?platform=liepin', { cache: 'no-store' })
      .then(response => response.json())
      .then(data => {
        if (cancelled) return
        if (Array.isArray(data.cities)) setLiepinCities(data.cities)
        if (!data.ok) setError(data.error || '猎聘的城市列表没读出来。')
      })
      .catch(() => {
        if (!cancelled) setError('猎聘的城市列表没读出来，过会儿再试。')
      })
    return () => { cancelled = true }
  }, [open, mode])

  const enabledOrder = useMemo(
    () => order.filter(platform => drafts[platform].enabled),
    [drafts, order],
  )

  if (!open) return null

  const updateDraft = (platform: PlatformId, key: keyof PlatformDraft, value: string | boolean) => {
    setDrafts(previous => ({ ...previous, [platform]: { ...previous[platform], [key]: value } }))
    setError('')
  }

  const togglePlatform = (platform: PlatformId, enabled: boolean) => {
    updateDraft(platform, 'enabled', enabled)
    setOrder(previous => enabled
      ? [...previous.filter(item => item !== platform), platform]
      : previous.filter(item => item !== platform))
  }

  const move = (platform: PlatformId, direction: -1 | 1) => {
    setOrder(previous => {
      const index = previous.indexOf(platform)
      const target = index + direction
      if (index < 0 || target < 0 || target >= previous.length) return previous
      const next = [...previous]
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
  }

  const start = () => {
    if (starting) return
    if (!enabledOrder.length) {
      setError('至少选一个平台。')
      return
    }
    const platforms: Record<string, unknown> = {}
    for (const platform of enabledOrder) {
      const draft = drafts[platform]
      const keywords = splitValues(draft.keywords)
      const cities = splitValues(draft.cities)
      if (!keywords.length || !cities.length) {
        setError(`${PLATFORM_LABELS[platform]} 至少填一个想找的岗位和一个城市。`)
        return
      }
      const configuredCodes = parseCityCodes(draft.cityCodes)
      const platformCities = platform === 'zhilian' ? zhilianCities : platform === '51job' ? job51Cities : platform === 'liepin' ? liepinCities : []
      const cityCodes = platform !== 'boss'
        ? Object.fromEntries(cities.map(city => [city, findPlatformCity(city, platformCities)?.code || configuredCodes[city] || '']).filter(([, code]) => code))
        : configuredCodes
      if (platform !== 'boss' && cities.some(city => !cityCodes[city])) {
        const missing = cities.filter(city => !cityCodes[city]).join('、')
        setError(`${PLATFORM_SHORT_LABELS[platform]} 这些城市里面没有：${missing}。请选里面有的城市。`)
        return
      }
      platforms[platform] = {
        keywords,
        cities,
        city_codes: cityCodes,
        max_pages: Number(draft.maxPages),
        sort: draft.sort,
        ...(platform === 'boss' ? { city_yield_policy: cityPolicy } : {}),
      }
    }
    if (continuous && (mode !== 'collect' || enabledOrder.join(',') !== 'boss')) {
      setError('一直找下去只支持单独找 BOSS 直聘。')
      return
    }
    void submit({ platform_order: enabledOrder, auto_score: mode === 'full' ? true : autoScore, platforms,
      ...(continuous ? { continuous_collection: { enabled: true, max_duration_hours: durationHours } } : {}) })
  }

  const submit = async (options: Record<string, unknown>) => {
    if (starting) return
    setStarting(true)
    setError('')
    try {
      const result = await onStart(options)
      if (result.ok) {
        onClose()
        return
      }
      setError(result.error || '没启动，过会儿再试。')
    } catch (err) {
      setError(err instanceof Error ? err.message : '启动失败，请稍后重试。')
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-4" role="dialog" aria-modal="true" aria-label="找新岗位">
      <div className="max-h-[92vh] w-full max-w-4xl overflow-y-auto rounded-3xl border border-card-border bg-card p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs font-black tracking-[0.18em] text-primary">COLLECT JOBS</div>
            <h2 className="mt-1 text-2xl font-black">{mode === 'full' ? '找新岗位的设置' : '岗位采集'}</h2>
            <p className="mt-1 text-sm leading-6 text-muted">平台一个一个来，不会同时找；每个平台只要设翻几页、按什么排。</p>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} disabled={starting} aria-label="关闭"><X className="h-5 w-5" /></Button>
        </div>

        {mode === 'collect' && <div className="mt-4 rounded-2xl border border-card-border bg-surface p-4">
          <div className="text-sm font-black">接着上次没找完的 BOSS 继续找</div>
          <p className="mt-1 text-xs leading-5 text-muted">还是用上次的条件，从断掉的那页接着找；找过的就不再找了。想看最新的在前的岗位，就用下面的“重新找”。</p>
          {resumableRuns.length > 0 ? <>
            <div className="mt-3 flex flex-wrap gap-2">
              <Select aria-label="上次没找完的 BOSS 任务" value={resumeRunId} onChange={event => setResumeRunId(event.target.value)}>
                {resumableRuns.map(run => <option key={run.id} value={run.id}>
                  {new Date(run.created_at.replace(' ', 'T') + 'Z').toLocaleString()} · {run.options.platforms.boss.cities.join('、')} · {run.options.platforms.boss.keywords.join('、')} · 每组 {run.options.platforms.boss.max_pages} 页
                </option>)}
              </Select>
              <Button variant="secondary" disabled={Boolean(activeTask) || !resumeRunId || starting} onClick={() => void submit({ resume_run_id: resumeRunId })}>接着找</Button>
            </div>
            <p className="mt-2 text-xs text-muted">{resumableRuns.find(run => run.id === resumeRunId)?.options.auto_score ? '上次找完是自动打分的，接着找也会打分。' : '上次找完不打分，接着找完就结束。'}</p>
          </> : <p className="mt-2 text-xs text-muted">现在没有能接着找的任务。新版本开始的 BOSS 单独找会保存进度，老版本的记录得重新找。</p>}
        </div>}

        {activeTask?.progress?.platforms && (
          <div className="mt-4 rounded-2xl border border-primary/20 bg-secondary p-4">
            <div className="text-sm font-black text-primary">正在找</div>
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              {Object.entries(activeTask.progress.platforms).map(([platform, state]) => (
                <div key={platform} className="rounded-xl border border-card-border bg-card p-3 text-sm">
                  <div className="flex items-center justify-between font-black"><span>{PLATFORM_LABELS[platform] || platform}</span><span>新找到 {state.new}</span></div>
                  <div className="mt-1 text-xs text-muted">{state.status} · {state.city || '等一下'} · {state.keyword || ''} · 第 {state.page || 0}/{state.max_pages || 0} 页</div>
                  <div className="mt-1 text-xs text-muted">看了 {state.seen || 0} · 重复的 {state.duplicate || 0} · 筛掉的 {state.filtered || 0} · 没看懂的 {state.parse_failed || 0} · 没存上的 {state.save_failed || 0}</div>
                  {state.message && <div className="mt-1 text-xs text-primary">{state.message}</div>}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mt-5 grid gap-4 lg:grid-cols-2">
          {(['boss', 'zhilian', '51job', 'liepin'] as PlatformId[]).map(platform => {
            const draft = drafts[platform]
            const label = PLATFORM_LABELS[platform]
            const platformCities = platform === 'zhilian' ? zhilianCities : platform === '51job' ? job51Cities : liepinCities
            return (
              <section key={platform} className={`rounded-2xl border p-4 ${draft.enabled ? 'border-primary/30 bg-surface' : 'border-card-border bg-card opacity-70'}`}>
                <div className="flex items-center justify-between gap-3">
                  <label className="flex items-center gap-2 text-lg font-black"><input type="checkbox" checked={draft.enabled} disabled={mode === 'full' && platform !== 'boss'} onChange={event => togglePlatform(platform, event.target.checked)} className="h-4 w-4 accent-primary" />{label}</label>
                  {draft.enabled && <div className="text-xs font-bold text-primary">排队 {enabledOrder.indexOf(platform) + 1}</div>}
                </div>
                {draft.enabled && <div className="mt-4 space-y-3">
                  <label className="block text-xs font-bold text-muted">想找的岗位（逗号或换行隔开）<Input value={draft.keywords} onChange={event => updateDraft(platform, 'keywords', event.target.value)} placeholder="产品经理, 产品运营" /></label>
                  <label className="block text-xs font-bold text-muted">城市（逗号或换行隔开）<Input list={platform !== 'boss' ? `${platform}-city-options` : undefined} value={draft.cities} onChange={event => updateDraft(platform, 'cities', event.target.value)} placeholder={platform === '51job' ? '上海' : '北京'} /></label>
                  {platform !== 'boss' ? <>
                    <datalist id={`${platform}-city-options`}>{platformCities.map(city => <option key={city.code} value={city.name} />)}</datalist>
                    <div className="rounded-xl border border-card-border bg-card px-3 py-2 text-xs text-muted">
                      <div className="font-bold text-foreground">城市编号</div>
                      <p className="mt-1">只用里面有的{PLATFORM_SHORT_LABELS[platform]}城市编号，不会瞎猜。</p>
                      {!!splitValues(draft.cities).length && <div className="mt-2 flex flex-wrap gap-1">
                        {splitValues(draft.cities).map(city => <span key={city} className={`rounded-full px-2 py-1 ${findPlatformCity(city, platformCities) ? 'bg-success-soft text-success' : 'bg-warning-soft text-warning'}`}>
                          {city} · {findPlatformCity(city, platformCities) ? '已经认出来了' : '里面没有'}
                        </span>)}
                      </div>}
                    </div>
                  </> : <p className="rounded-xl border border-card-border bg-card px-3 py-2 text-xs text-muted">BOSS 城市编号系统自己会配，不用你填。</p>}
                  <div className="grid grid-cols-2 gap-2">
                    <label className="text-xs font-bold text-muted">最多翻几页<Input type="number" min={1} max={10} value={draft.maxPages} onChange={event => updateDraft(platform, 'maxPages', event.target.value)} /></label>
                    <label className="text-xs font-bold text-muted">按什么排<Select value={draft.sort} onChange={event => updateDraft(platform, 'sort', event.target.value)}><option value="default">默认</option>{platform !== '51job' && <option value="newest">最新</option>}</Select></label>
                  </div>
                </div>}
                {!draft.enabled && mode === 'full' && platform !== 'boss' && <p className="mt-3 text-xs text-muted">现在只支持“岗位采集”，不会顺便投出去。</p>}
              </section>
            )
          })}
        </div>

        <div className="mt-4 rounded-2xl border border-card-border bg-surface p-4">
          <div className="flex items-center justify-between gap-3"><div><div className="text-sm font-black">先找哪个</div><p className="mt-1 text-xs text-muted">平台一个一个找；智联、前程无忧、猎聘这次不投、也不盯着。</p></div><div className="flex gap-2">{enabledOrder.map((platform, index) => <div key={platform} className="flex items-center gap-1 rounded-full bg-card px-3 py-1 text-xs font-black text-primary"><span>{index + 1}. {PLATFORM_SHORT_LABELS[platform]}</span><button type="button" onClick={() => move(platform, -1)} disabled={index === 0} aria-label="往上"><ArrowUp className="h-3 w-3" /></button><button type="button" onClick={() => move(platform, 1)} disabled={index === enabledOrder.length - 1} aria-label="往下"><ArrowDown className="h-3 w-3" /></button></div>)}</div></div>
        </div>

        {drafts.boss.enabled && <section className="mt-4 border-t border-card-border pt-4">
          <label className="flex items-center justify-between gap-3 text-sm font-bold">这个城市新岗位太少就换地方<Switch aria-label="城市低产切换" checked={cityPolicy.enabled} onChange={enabled => setCityPolicy(p => ({ ...p, enabled }))} /></label>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label className="text-xs text-muted">过几分钟先看一次<Input type="number" min={1} max={60} value={cityPolicy.warmup_minutes} onChange={e => setCityPolicy(p => ({ ...p, warmup_minutes: Number(e.target.value) }))} /></label>
            <label className="text-xs text-muted">隔几分钟看一次<Input type="number" min={1} max={30} value={cityPolicy.window_minutes} onChange={e => setCityPolicy(p => ({ ...p, window_minutes: Number(e.target.value) }))} /></label>
            <label className="text-xs text-muted">一次至少新找到几个<Input type="number" min={1} max={1000} value={cityPolicy.min_new_jobs} onChange={e => setCityPolicy(p => ({ ...p, min_new_jobs: Number(e.target.value) }))} /></label>
          </div>
        </section>}
        {mode === 'collect' && <section className="mt-4 border-t border-card-border pt-4">
          <label className="flex items-center justify-between gap-3 text-sm font-bold">一直找下去<Switch aria-label="持续采集" checked={continuous} onChange={setContinuous} disabled={enabledOrder.join(',') !== 'boss'} /></label>
          {continuous && <label className="mt-3 block text-xs text-muted">一次最多找几小时<Input type="number" min={1} max={24} value={durationHours} onChange={e => setDurationHours(Number(e.target.value))} /></label>}
        </section>}
        <label className="mt-4 flex items-center justify-between border-t border-card-border py-4"><span className="text-sm font-black">{mode === 'full' ? '找完自动打分' : continuous ? '找完再打分' : '找到就打分'}</span><Switch checked={mode === 'full' || autoScore} onChange={mode === 'full' ? () => undefined : setAutoScore} disabled={mode === 'full'} /></label>
        {error && <div className="mt-3 rounded-xl bg-danger-soft px-3 py-2 text-sm text-danger">{error}</div>}
        <div className="mt-5 flex justify-end gap-2"><Button variant="secondary" onClick={onClose} disabled={starting}>取消</Button><Button onClick={() => void start()} disabled={Boolean(activeTask) || starting}>{starting ? <span className="inline-flex items-center gap-2"><RefreshCw className="h-4 w-4 animate-spin" />正在开始…</span> : mode === 'full' ? '开始自动找工作' : '重新采集'}</Button></div>
      </div>
    </div>
  )
}
