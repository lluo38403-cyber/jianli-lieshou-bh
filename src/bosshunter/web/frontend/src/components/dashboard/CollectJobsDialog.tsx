import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, RefreshCw, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ModalPortal } from '@/components/ui/modal-portal'
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
  const [activeTab, setActiveTab] = useState<PlatformId>('boss')
  const [order, setOrder] = useState<PlatformId[]>(['boss'])
  const [autoScore, setAutoScore] = useState(false)
  const [continuous, setContinuous] = useState(false)
  const [durationHours, setDurationHours] = useState(8)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  /** 风控保护（和“我的设置”同一组配置键、同一条保存链路）：打开弹窗时
   * 从 /api/config 读，改了就整个配置 POST 回 /api/config 立即保存。 */
  const [fullConfig, setFullConfig] = useState<Record<string, any> | null>(null)
  const [riskTier, setRiskTier] = useState('standard')
  const [randomInterval, setRandomInterval] = useState(true)
  const [riskSaving, setRiskSaving] = useState(false)
  const [riskNote, setRiskNote] = useState('')
  const [resumableRuns, setResumableRuns] = useState<ResumableRun[]>([])
  const [resumeRunId, setResumeRunId] = useState('')
  const [zhilianCities, setZhilianCities] = useState<PlatformCityOption[]>([])
  const [job51Cities, setJob51Cities] = useState<PlatformCityOption[]>([])
  const [liepinCities, setLiepinCities] = useState<PlatformCityOption[]>([])
  const panelRef = useRef<HTMLDivElement>(null)
  const previouslyFocusedRef = useRef<Element | null>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  const startingRef = useRef(starting)
  startingRef.current = starting

  /** 弹窗开着时：焦点锁在弹窗里（Tab 跑不到底层按钮上）、Esc 关闭、
   * 背景页面不滚动；关掉后焦点回到打开前的位置。 */
  useEffect(() => {
    if (!open) return
    previouslyFocusedRef.current = document.activeElement
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    panelRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (!startingRef.current) onCloseRef.current()
        return
      }
      if (event.key !== 'Tab' || !panelRef.current) return
      const focusable = Array.from(panelRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ))
      if (!focusable.length) {
        event.preventDefault()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      // 焦点在面板容器本身（刚打开时）或已跑到外面时，也拉回弹窗里。
      if (!panelRef.current.contains(document.activeElement)) {
        event.preventDefault()
        ;(event.shiftKey ? last : first).focus()
        return
      }
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
      const previous = previouslyFocusedRef.current
      if (previous instanceof HTMLElement) previous.focus()
    }
  }, [open])

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
        setFullConfig(config)
        setRiskTier(config?.throttle?.risk_tier || 'standard')
        setRandomInterval(config?.throttle?.random_interval ?? true)
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

  /** 风控设置改了立即保存：同一组配置键、同一条保存链路（POST /api/config），
   * “我的设置”里看到的是同一份，不用再点保存。 */
  const saveRiskSetting = (path: string, value: unknown) => {
    if (!fullConfig) return
    const keys = path.split('.')
    const next = JSON.parse(JSON.stringify(fullConfig))
    let obj = next
    for (let i = 0; i < keys.length - 1; i++) {
      if (!obj[keys[i]]) obj[keys[i]] = {}
      obj = obj[keys[i]]
    }
    obj[keys[keys.length - 1]] = value
    setFullConfig(next)
    setRiskSaving(true)
    setRiskNote('')
    fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(next),
    })
      .then(response => response.json())
      .then(data => setRiskNote(data?.success ? '已保存，和“我的设置”里是一样的。' : (data?.error || '没保存上。')))
      .catch(() => setRiskNote('没保存上，检查一下网络。'))
      .finally(() => setRiskSaving(false))
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
    // 定位：经 ModalPortal 挂到 body，fixed 才相对视口居中，不会被
    // .animate-page-in 的 transform 拽到整页中部。
    // 布局本身已是对的（面板 max-h-[85vh] 纵向分栏、正文区滚动、底部按钮
    // sticky），这次只改挂载位置，不动布局与任何业务逻辑。
    // 全屏模态：z-[60] 高于右下角更新卡片（z-50，无遮罩）和验证弹窗（z-50），
    // 不管挂载顺序如何都盖在最上面；更新卡片只是被暂时盖住，关掉弹窗后还在，能继续点。
    <ModalPortal>
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/35 p-4" role="dialog" aria-modal="true" aria-label="找新岗位">
      <div ref={panelRef} tabIndex={-1} className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-3xl border border-card-border bg-card shadow-2xl focus:outline-none">
        <div className="flex items-start justify-between gap-4 px-6 pt-6">
          <div className="min-w-0">
            <div className="text-xs font-black tracking-[0.18em] text-primary">COLLECT JOBS</div>
            <h2 className="mt-1 text-2xl font-black">{mode === 'full' ? '找新岗位的设置' : '岗位采集'}</h2>
            <p className="mt-1 text-sm leading-6 text-muted">平台一个一个来，不会同时找；每个平台只要设翻几页、按什么排。</p>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} disabled={starting} aria-label="关闭" title={starting ? '正在开始，稍等一下就能关' : undefined}><X className="h-5 w-5" /></Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">

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

        <div className="mt-5" role="tablist" aria-label="选一个平台来设置">
          <div className="flex gap-1 rounded-full border border-card-border bg-surface p-1">
            {(['boss', 'zhilian', '51job', 'liepin'] as PlatformId[]).map(platform => (
              <button
                key={platform}
                type="button"
                role="tab"
                aria-selected={activeTab === platform}
                onClick={() => setActiveTab(platform)}
                className={`min-w-0 flex-1 rounded-full px-2 py-1.5 text-xs font-black transition ${activeTab === platform ? 'bg-primary text-primary-foreground shadow-sm' : 'text-muted hover:text-primary'}`}
              >
                {PLATFORM_SHORT_LABELS[platform]}{drafts[platform].enabled ? '' : '（未开启）'}
              </button>
            ))}
          </div>
        </div>

        {(['boss', 'zhilian', '51job', 'liepin'] as PlatformId[]).filter(platform => platform === activeTab).map(platform => {
          const draft = drafts[platform]
          const label = PLATFORM_LABELS[platform]
          const platformCities = platform === 'zhilian' ? zhilianCities : platform === '51job' ? job51Cities : liepinCities
          return (
            <section key={platform} className={`mt-3 rounded-2xl border p-4 ${draft.enabled ? 'border-primary/30 bg-surface' : 'border-card-border bg-card opacity-70'}`}>
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
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <label className="text-xs font-bold text-muted">最多翻几页<Input type="number" min={1} max={10} value={draft.maxPages} onChange={event => updateDraft(platform, 'maxPages', event.target.value)} /></label>
                  <label className="text-xs font-bold text-muted">按什么排<Select value={draft.sort} onChange={event => updateDraft(platform, 'sort', event.target.value)}><option value="default">默认</option>{platform !== '51job' && <option value="newest">最新</option>}</Select></label>
                </div>
              </div>}
              {!draft.enabled && mode === 'full' && platform !== 'boss' && <p className="mt-3 text-xs text-muted">现在只支持“岗位采集”，不会顺便投出去。</p>}
              {!draft.enabled && !(mode === 'full' && platform !== 'boss') && <p className="mt-3 text-xs text-muted">这个平台现在没勾上，勾上才会找。</p>}
            </section>
          )
        })}

        <div className="mt-4 rounded-2xl border border-card-border bg-surface p-4">
          <div className="flex flex-wrap items-center justify-between gap-3"><div className="min-w-0"><div className="text-sm font-black">先找哪个</div><p className="mt-1 text-xs text-muted">平台一个一个找；智联、前程无忧、猎聘这次不投、也不盯着。</p></div><div className="flex flex-wrap gap-2">{enabledOrder.map((platform, index) => <div key={platform} className="flex items-center gap-1 rounded-full bg-card px-3 py-1 text-xs font-black text-primary"><span>{index + 1}. {PLATFORM_SHORT_LABELS[platform]}</span><button type="button" onClick={() => move(platform, -1)} disabled={index === 0} aria-label="往上"><ArrowUp className="h-3 w-3" /></button><button type="button" onClick={() => move(platform, 1)} disabled={index === enabledOrder.length - 1} aria-label="往下"><ArrowDown className="h-3 w-3" /></button></div>)}</div></div>
        </div>

        {mode === 'collect' && <section className="mt-4 border-t border-card-border pt-4">
          <label className="flex items-center justify-between gap-3 text-sm font-bold">一直找下去<Switch aria-label="持续采集" checked={continuous} onChange={setContinuous} disabled={enabledOrder.join(',') !== 'boss'} /></label>
          {continuous && <label className="mt-3 block text-xs text-muted">一次最多找几小时<Input type="number" min={1} max={24} value={durationHours} onChange={e => setDurationHours(Number(e.target.value))} /></label>}
        </section>}
        <label className="mt-4 flex items-center justify-between border-t border-card-border py-4"><span className="text-sm font-black">{mode === 'full' ? '找完自动打分' : continuous ? '找完再打分' : '找到就打分'}</span><Switch checked={mode === 'full' || autoScore} onChange={mode === 'full' ? () => undefined : setAutoScore} disabled={mode === 'full'} /></label>
        {mode === 'full' && <section className="mt-4 rounded-2xl border border-card-border bg-surface p-4">
          <div className="text-sm font-black">风控保护</div>
          <p className="mt-1 text-xs leading-5 text-muted">和“我的设置”里的风控保护是同一组开关，这边改了那边跟着变，改完马上保存。</p>
          <div className="mt-3 space-y-3">
            <label className="block text-xs font-bold text-muted">风控档位
              <Select value={riskTier} onChange={e => { const value = e.target.value; setRiskTier(value); saveRiskSetting('throttle.risk_tier', value) }}>
                <option value="steady">稳妥模式</option>
                <option value="standard">标准模式</option>
                <option value="fast">极速模式</option>
              </Select>
              <span className="mt-1 block font-normal">每批投 3–5 个，批与批之间歇一会：稳妥 3–5 分钟、标准 1–3 分钟、极速半分钟–1 分钟。</span>
            </label>
            {riskTier === 'fast' && <p className="text-xs text-warning">投得太快可能被平台判刷得快，遇到验证提示请及时处理。</p>}
            <label className="flex items-center justify-between gap-3 text-sm font-bold">随机操作间隔<Switch aria-label="随机操作间隔" checked={randomInterval} onChange={value => { setRandomInterval(value); saveRiskSetting('throttle.random_interval', value) }} /></label>
            {riskSaving ? <p className="text-xs text-muted">正在保存…</p> : riskNote ? <p className="text-xs text-muted">{riskNote}</p> : null}
          </div>
        </section>}
        {error && <div className="mt-3 rounded-xl bg-danger-soft px-3 py-2 text-sm text-danger">{error}</div>}
        </div>
        <div className="sticky bottom-0 flex shrink-0 flex-wrap justify-end gap-2 border-t border-card-border bg-card px-6 py-3"><Button variant="secondary" onClick={onClose} disabled={starting} title={starting ? '正在开始，稍等一下就能关' : undefined}>取消</Button><Button onClick={() => void start()} disabled={Boolean(activeTask) || starting} title={activeTask ? '现在有任务在跑，先停下再开始' : undefined}>{starting ? <span className="inline-flex items-center gap-2"><RefreshCw className="h-4 w-4 animate-spin" />正在开始…</span> : mode === 'full' ? '开始自动找工作' : '重新采集'}</Button></div>
      </div>
    </div>
    </ModalPortal>
  )
}
