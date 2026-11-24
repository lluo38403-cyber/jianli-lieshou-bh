import { useConfig } from '@/hooks/useConfig'
import { useRegisterUnsavedChangesGuard } from '@/hooks/useUnsavedChangesGuard'
import { useUpdateContext, formatUpdateNotes, formatUpdateSize } from '@/hooks/useUpdate'
import { UpdateModal } from '@/components/dashboard/UpdateModal'
import { CHECK_UPDATE_EVENT } from '@/lib/updateEvents'
import { Button } from '@/components/ui/button'
import { ModalPortal } from '@/components/ui/modal-portal'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Slider } from '@/components/ui/slider'
import { TagsInput } from '@/components/ui/tags-input'
import { CityMultiSelect, type CityOption } from '@/components/config/CityMultiSelect'
import { ResumeUploadSection } from '@/components/config/ResumeUploadSection'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Save, RotateCcw } from 'lucide-react'
import { useState, useEffect, useRef } from 'react'
import { validateNumberInput } from '@/lib/configValidation'
import { PLATFORM_LABELS, PLATFORM_SHORT_LABELS, PLATFORM_IDS, MONITOR_CHAT_URL_DEFAULTS, type PlatformId as SharedPlatformId } from '@/lib/platforms'

type PlatformId = 'boss' | 'zhilian' | '51job' | 'liepin'

const BOSS_FILTER_OPTIONS = {
  job_type: ['全职', '兼职', '实习'],
  experience: ['经验不限', '应届生', '1年以内', '1-3年', '3-5年', '5-10年', '10年以上', '在校生'],
  degree: ['学历不限', '大专', '本科', '硕士', '博士', '高中', '中专/中技', '初中及以下'],
  scale: ['0-20人', '20-99人', '100-499人', '500-999人', '1000-9999人', '10000人以上'],
  salary: ['3K以下', '3-5K', '5-10K', '10-20K', '20-50K', '50K以上'],
} as const

export default function ConfigPage() {
  const { config, loading, saving, dirty, error, message, updateConfig, saveConfig, resetConfig } = useConfig()
  // 离开设置页时的未保存提醒：向侧边栏注册守卫；“我想找什么工作 /
  // 全局过滤规则”和“我的设置”是同一页、同一份保存，走这一个守卫就够。
  // 岗位采集弹窗（CollectJobsDialog）的草稿是另一条立即保存链路，不归这里管。
  useRegisterUnsavedChangesGuard({
    isDirty: () => dirty,
    save: () => saveConfig(),
    discard: () => resetConfig(),
  })
  const update = useUpdateContext()
  const [updateNote, setUpdateNote] = useState('')
  const [manualUpdateSignal, setManualUpdateSignal] = useState(0)
  const handleManualUpdateCheck = async () => {
    setUpdateNote('')
    const found = await update.check(true)
    if (found === null) {
      setUpdateNote('没查到，网络可能断了，稍后再点一次。')
    } else if (found === false) {
      setUpdateNote('已经是最新版，不用换。')
    } else {
      // 有新版：让弹窗弹出来（手动检查忽略跳过记录）。
      setManualUpdateSignal(Date.now())
    }
  }
  // 侧边栏点版本号：同样走这里的手动检查。
  useEffect(() => {
    const onCheckUpdate = () => {
      void handleManualUpdateCheck()
    }
    window.addEventListener(CHECK_UPDATE_EVENT, onCheckUpdate)
    return () => window.removeEventListener(CHECK_UPDATE_EVENT, onCheckUpdate)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  // v28：首页预检失败点“去传简历”会带 #resume-upload 跳过来；设置内容
  // 是异步加载的，挂载时可能还没渲染出来，轮询几次再滚过去。
  useEffect(() => {
    const hash = window.location.hash
    if (!hash) return
    let attempts = 0
    const timer = window.setInterval(() => {
      attempts += 1
      const target = document.querySelector(hash)
      if (target && typeof (target as HTMLElement).scrollIntoView === 'function') {
        ;(target as HTMLElement).scrollIntoView({ behavior: 'smooth', block: 'start' })
        window.clearInterval(timer)
      } else if (attempts >= 10) {
        window.clearInterval(timer)
      }
    }, 300)
    return () => window.clearInterval(timer)
  }, [])
  const [cityOptions, setCityOptions] = useState<CityOption[]>([])
  const [zhilianCityOptions, setZhilianCityOptions] = useState<CityOption[]>([])
  const [job51CityOptions, setJob51CityOptions] = useState<CityOption[]>([])
  const [liepinCityOptions, setLiepinCityOptions] = useState<CityOption[]>([])
  const [cityRefreshing, setCityRefreshing] = useState(false)
  const [cityMessage, setCityMessage] = useState('')
  const [activeTab, setActiveTab] = useState<PlatformId | null>(null)
  const [unrecognizedCities, setUnrecognizedCities] = useState<Partial<Record<PlatformId, string[]>>>({})

  useEffect(() => {
    fetch('/api/cities', { cache: 'no-store' })
      .then(r => r.json())
      .then(data => {
        if (Array.isArray(data.cities)) setCityOptions(data.cities)
        if (!data.ok) setCityMessage(data.error || '城市列表没读出来')
      })
      .catch(() => setCityMessage('城市列表没读出来'))
    fetch('/api/cities?platform=zhilian', { cache: 'no-store' })
      .then(r => r.json())
      .then(data => {
        if (Array.isArray(data.cities)) setZhilianCityOptions(data.cities)
      })
      .catch(() => {})
    fetch('/api/cities?platform=51job', { cache: 'no-store' })
      .then(r => r.json())
      .then(data => {
        if (Array.isArray(data.cities)) setJob51CityOptions(data.cities)
      })
      .catch(() => {})
    fetch('/api/cities?platform=liepin', { cache: 'no-store' })
      .then(r => r.json())
      .then(data => {
        if (Array.isArray(data.cities)) setLiepinCityOptions(data.cities)
      })
      .catch(() => {})
  }, [])

  const handleCityRefresh = async () => {
    setCityRefreshing(true)
    setCityMessage('')
    try {
      const res = await fetch('/api/cities/refresh', { method: 'POST' })
      const data = await res.json()
      if (Array.isArray(data.cities)) setCityOptions(data.cities)
      if (!res.ok || !data.ok) throw new Error(data.error || '刷新失败，先用原来的城市列表')
      setCityMessage(`已经刷新了， ${data.count} 个城市。`)
    } catch (error) {
      setCityMessage(error instanceof Error ? error.message : '刷新失败，继续使用本地城市列表')
    } finally {
      setCityRefreshing(false)
    }
  }

  const platformSearch = (platform: PlatformId) => {
    const legacy = platform === 'boss' && config?.search && typeof config.search === 'object' ? config.search : {}
    const specific = config?.platforms?.[platform]?.search && typeof config.platforms[platform].search === 'object'
      ? config.platforms[platform].search
      : {}
    return {
      ...legacy,
      ...specific,
      filters: { ...(legacy.filters || {}), ...(specific.filters || {}) },
      keywords: specific.keywords?.length ? specific.keywords : legacy.keywords,
      cities: specific.cities?.length ? specific.cities : legacy.cities,
      city_codes: Object.keys(specific.city_codes || {}).length ? specific.city_codes : legacy.city_codes,
      // H-6：用 ?? 不用 ||——已存的 0 是坏值要露出来，不能悄悄显示成默认值。
      max_pages: specific.max_pages ?? legacy.max_pages ?? (platform === 'boss' ? 3 : 1),
      sort: specific.sort || legacy.sort || 'default',
    }
  }

  // 写路径：只写平台自有 platforms.<platform>.search。BOSS 额外同步一份到
  // 顶层 search.*，是因为后端还在读它（agent_api.py / orchestrator.py 用
  // 顶层 search 做回落，prefilter/scorer 还读 profile.target_cities）；
  // 后端改掉回落读取后，这里同步就可以删掉。非 BOSS 平台本来就不镜像。
  const updatePlatformSearch = (platform: PlatformId, key: string, value: any) => {
    updateConfig(`platforms.${platform}.search.${key}`, value)
    if (platform === 'boss') updateConfig(`search.${key}`, value)
  }

  const updateBossFilter = (search: any, key: keyof typeof BOSS_FILTER_OPTIONS | 'industry', value: string, multiple = true) => {
    const current = Array.isArray(search.filters?.[key]) ? search.filters[key] : []
    const next = multiple
      ? (current.includes(value) ? current.filter((item: string) => item !== value) : [...current, value])
      : (value ? [value] : [])
    updatePlatformSearch('boss', `filters.${key}`, next)
  }

  // 找不到编码的城市不再静默丢弃：编码为空也保留在 city_codes 里，
  // 并在界面上明确告诉用户哪个城市没认出来（见下面的提示行）。
  const updatePlatformCities = (platform: PlatformId, cities: string[]) => {
    const platformCityOptions = platform === 'zhilian' ? zhilianCityOptions : platform === 'liepin' ? liepinCityOptions : job51CityOptions
    const findCode = (city: string, options: CityOption[]) =>
      options.find(option => option.name.replace(/市$/, '') === city.replace(/市$/, ''))?.code || ''
    const cityCodes = platform !== 'boss'
      ? Object.fromEntries(cities.map(city => [city, findCode(city, platformCityOptions)]))
      : Object.fromEntries(cities.map(city => [city, findCode(city, cityOptions)]))
    const unrecognized = cities.filter(city => !cityCodes[city])
    setUnrecognizedCities(previous => ({ ...previous, [platform]: unrecognized }))
    updatePlatformSearch(platform, 'cities', cities)
    updatePlatformSearch(platform, 'city_codes', cityCodes)
    // BOSS 城市同步到 profile.target_cities：后端打分/过滤还在读它
    //（scorer.py / prefilter.py），后端改掉后这里同步就可以删掉。
    if (platform === 'boss') updateConfig('profile.target_cities', cities)
  }

  const setPlatformEnabled = (platform: PlatformId, enabled: boolean) => {
    updateConfig(`platforms.${platform}.enabled`, enabled)
    const currentOrder: PlatformId[] = Array.isArray(config?.collection?.default_order)
      ? config.collection.default_order.filter((item: unknown): item is PlatformId => item === 'boss' || item === 'zhilian' || item === '51job' || item === 'liepin')
      : ['boss'] as PlatformId[]
    const nextOrder = enabled
      ? [...currentOrder, ...(!currentOrder.includes(platform) ? [platform] : [])]
      : currentOrder.filter(item => item !== platform)
    updateConfig('collection.default_order', nextOrder.length ? nextOrder : ['boss'])
  }

  const setCollectionOrder = (value: string) => {
    const enabled = (['boss', 'zhilian', '51job', 'liepin'] as PlatformId[]).filter(platform => config?.platforms?.[platform]?.enabled !== false)
    const requested = value.split(',').filter((item): item is PlatformId => item === 'boss' || item === 'zhilian' || item === '51job' || item === 'liepin')
    const next = [...requested, ...enabled.filter(platform => !requested.includes(platform))]
    updateConfig('collection.default_order', next.length ? next : ['boss'])
  }

  if (loading) {
    return (
      <div className="space-y-4 pr-4" aria-label="正在打开">
        <div className="skeleton h-10 w-full" />
        <div className="skeleton h-56 w-full" />
        <div className="skeleton h-56 w-full" />
      </div>
    )
  }

  if (error || !config) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="max-w-md rounded-2xl border border-card-border bg-surface p-6 text-center">
          <div className="text-sm font-black text-foreground">设置没打开</div>
          <p className="mt-2 text-xs leading-6 text-muted">
            请确认简历猎手还在运行，然后刷新页面再试。
          </p>
          {error && <p className="mt-3 rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger">{error}</p>}
          <Button className="mt-4" size="sm" onClick={resetConfig}>重试</Button>
        </div>
      </div>
    )
  }

  const bossSearchEstimate = platformSearch('boss')
  const bossEstimateKeywords = Array.isArray(bossSearchEstimate.keywords) ? bossSearchEstimate.keywords : []
  const bossEstimateCities = Array.isArray(bossSearchEstimate.cities) && bossSearchEstimate.cities.length
    ? bossSearchEstimate.cities
    : (config.profile?.target_cities || [])
  const bossEstimateMaxPages = Math.max(Number(bossSearchEstimate.max_pages) || 1, 1)
  const bossTheoreticalPages = bossEstimateKeywords.length * bossEstimateCities.length * bossEstimateMaxPages
  const bossDailySearchLimit = Math.max(Number(config.collection?.daily_search_page_limit) || 60, 1)
  const bossTheoreticalExceedsLimit = bossTheoreticalPages > bossDailySearchLimit
  const guidePlatform = (['boss', 'zhilian', '51job', 'liepin'] as PlatformId[])
    .find(platform => config.platforms?.[platform]?.enabled ?? platform === 'boss')
  // 跟进总开关（v48）：关了整个“没回消息怎么办”块变灰、子项改不了。
  const followUpEnabled = config.follow_up?.enabled ?? false

  return (
    <div className="page-sections h-full overflow-y-auto space-y-4 pb-20 pr-4">
        {/* Actions bar */}
        <div className="flex items-center justify-between sticky top-0 bg-background z-10 py-2">
          <div className="flex items-center gap-2">
            {dirty && <span className="text-xs text-warning">改了还没保存</span>}
            {message && (
              <span className={`text-xs ${message.type === 'success' ? 'text-success' : 'text-danger'}`}>
                {message.text}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={resetConfig}><RotateCcw className="w-3 h-3 mr-1" />恢复默认设置</Button>
            <Button data-tour="config-save" size="sm" onClick={saveConfig} disabled={saving || !dirty}><Save className="w-3 h-3 mr-1" />{saving ? '保存中…' : '保存'}</Button>
          </div>
        </div>

        {/* Profile Section */}
        <SectionCard title="我的简历和我">
          <div className="space-y-4">
            <div data-tour="resume-upload" id="resume-upload" className="scroll-mt-6">
              <ResumeUploadSection
                currentResumePath={config.profile?.resume_path || ''}
                updateConfig={updateConfig}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="我的学历">
                <Select value={config.profile?.education || ''} onChange={e => updateConfig('profile.education', e.target.value)}>
                  <option value="">未设置</option>
                  <option value="博士">博士</option>
                  <option value="硕士">硕士</option>
                  <option value="本科">本科</option>
                  <option value="大专">大专</option>
                  <option value="其他">其他</option>
                </Select>
              </Field>
              <Field label="我想找校招还是社招">
                <Select value={config.profile?.recruitment_type || ''} onChange={e => updateConfig('profile.recruitment_type', e.target.value)}>
                  <option value="">未设置</option>
                  <option value="campus">校招</option>
                  <option value="experienced">社招</option>
                  <option value="both">校招、社招均可</option>
                </Select>
              </Field>
            </div>
            <Field label="这些词的岗位不要">
              <TagsInput value={config.profile?.deal_breakers || []} onChange={v => updateConfig('profile.deal_breakers', v)} placeholder="如：外包、996" />
            </Field>
            <Field label="岗位介绍里有这些词也不要">
              <TagsInput value={config.profile?.jd_deal_breakers || []} onChange={v => updateConfig('profile.jd_deal_breakers', v)} placeholder="如：需频繁出差、纯销售" />
              <p className="mt-1 text-xs text-muted">岗位介绍里有这些词，打分之前就直接跳过。</p>
            </Field>
            <Field label="不想去的公司">
              <TagsInput value={config.profile?.blocked_companies || []} onChange={v => updateConfig('profile.blocked_companies', v)} placeholder="输入公司名称或关键词" />
              <p className="mt-1 text-xs text-muted">公司名字里有这些词，就不去找，也不会打分。</p>
            </Field>
          </div>
        </SectionCard>

        {/* Search Section */}
        <SectionCard title="我想找什么工作">
          <div className="space-y-4">
            <p className="rounded-xl border border-card-border bg-surface px-3 py-2 text-xs leading-5 text-muted">
              四个平台都会自动投：BOSS 直聘是发打招呼聊一聊，智联、前程无忧、猎聘是直接点投递；HR 回了消息会统一来看。不想用的平台把勾去掉。
            </p>
            <div data-tour={!guidePlatform ? 'search-city' : undefined}>
            <div data-tour={!guidePlatform ? 'search-keywords' : undefined}>
            <div className="flex gap-1 rounded-full border border-card-border bg-surface p-1" role="tablist" aria-label="选一个平台来设置">
              {(['boss', 'zhilian', '51job', 'liepin'] as PlatformId[]).map(platform => {
                const enabled = config.platforms?.[platform]?.enabled ?? platform === 'boss'
                return (
                  <button
                    key={platform}
                    type="button"
                    role="tab"
                    aria-selected={(activeTab ?? guidePlatform ?? 'boss') === platform}
                    onClick={() => setActiveTab(platform)}
                    className={`min-w-0 flex-1 rounded-full px-2 py-1.5 text-xs font-black transition ${(activeTab ?? guidePlatform ?? 'boss') === platform ? 'bg-primary text-primary-foreground shadow-sm' : 'text-muted hover:text-primary'}`}
                  >
                    {PLATFORM_SHORT_LABELS[platform]}{enabled ? '' : '（未开启）'}
                  </button>
                )
              })}
            </div>
            {(['boss', 'zhilian', '51job', 'liepin'] as PlatformId[]).filter(platform => platform === (activeTab ?? guidePlatform ?? 'boss')).map(platform => {
              const search = platformSearch(platform)
              const label = PLATFORM_LABELS[platform]
              const platformCityOptions = platform === 'zhilian' ? zhilianCityOptions : platform === 'liepin' ? liepinCityOptions : job51CityOptions
              const enabled = config.platforms?.[platform]?.enabled ?? platform === 'boss'
              const cities = Array.isArray(search.cities) && search.cities.length
                ? search.cities
                : platform === 'boss' ? (config.profile?.target_cities || []) : []
              const cityInput = cities.join(', ')
              const bossFilters = search.filters && typeof search.filters === 'object' ? search.filters : {}
              const missingCities = unrecognizedCities[platform] || []
              return (
                <div key={platform} data-tour={!guidePlatform && platform === 'boss' ? 'search-city' : undefined} className={`mt-3 rounded-2xl border p-4 ${enabled ? 'border-primary/30 bg-surface' : 'border-card-border bg-card opacity-70'}`}>
                  <div data-tour={!guidePlatform && platform === 'boss' ? 'search-keywords' : undefined} className="flex items-center justify-between gap-3">
                    <label className="flex items-center gap-2 text-sm font-black text-foreground">
                      <input type="checkbox" checked={enabled} onChange={event => setPlatformEnabled(platform, event.target.checked)} className="h-4 w-4 accent-primary" />
                      {label}
                    </label>
                    <span className="text-xs text-muted">{enabled ? '已开启' : '未开启'}</span>
                  </div>
                  {enabled && <div className="mt-4 space-y-3">
                    <Field label="想找的岗位" dataTour={platform === guidePlatform ? 'search-keywords' : undefined} hint={platform === 'boss' ? '输完按回车确认，多个岗位用逗号隔开。' : undefined}>
                      <TagsInput value={Array.isArray(search.keywords) ? search.keywords : []} onChange={value => updatePlatformSearch(platform, 'keywords', value)} placeholder="比如：人力、产品运营" />
                    </Field>
                    <Field label="想在哪个城市找" dataTour={platform === guidePlatform && platform !== 'boss' ? 'search-city' : undefined}>
                      {platform === 'boss' ? <CityMultiSelect
                        tourTarget={platform === guidePlatform ? 'search-city' : undefined}
                        options={cityOptions}
                        value={cities}
                        onChange={value => updatePlatformCities(platform, value)}
                        onRefresh={handleCityRefresh}
                        refreshing={cityRefreshing}
                        message={cityMessage}
                      /> : <>
                        <Input list={`config-${platform}-city-options`} value={cityInput} onChange={event => updatePlatformCities(platform, event.target.value.split(/[,，]/).map(value => value.trim()).filter(Boolean))} placeholder={platform === '51job' ? '如：上海' : '如：深圳'} />
                        <datalist id={`config-${platform}-city-options`}>{platformCityOptions.map(city => <option key={city.code} value={city.name} />)}</datalist>
                        <p className="mt-1 text-xs text-muted">{PLATFORM_SHORT_LABELS[platform]}只用里面有的城市；现在里面有 {platformCityOptions.length} 个城市。</p>
                        {!!cities.length && <div className="mt-2 flex flex-wrap gap-1">{cities.map((city: string) => {
                          const matched = platformCityOptions.find(option => option.name.replace(/市$/, '') === city.replace(/市$/, ''))
                          return <span key={city} title={matched ? '已经认出来了' : '这个城市还没认出来'} className={`rounded-full px-2 py-1 text-xs ${matched ? 'bg-success-soft text-success' : 'bg-warning-soft text-warning'}`}>{city}</span>
                        })}</div>}
                      </>}
                      {missingCities.length > 0 && (
                        <p className="mt-1 rounded-lg bg-warning-soft px-3 py-2 text-xs text-warning">
                          这几个城市还没认出来：{missingCities.join('、')}，换个写法或稍后再试；没认出来的城市先留着，不会偷偷丢掉。
                        </p>
                      )}
                    </Field>
                    <div className="grid gap-3 md:grid-cols-2">
                      <Field label="最多翻几页">
                        <ValidatedNumberInput label="最多翻几页" value={search.max_pages ?? (platform === 'boss' ? 3 : 1)} onChange={value => updatePlatformSearch(platform, 'max_pages', value)} min={1} max={10} />
                        {platform === 'boss' && bossTheoreticalPages > 0 && (
                          <p className={`mt-1 rounded-lg px-3 py-2 text-xs ${bossTheoreticalExceedsLimit ? 'bg-warning-soft font-bold text-warning' : 'bg-success-soft text-success'}`}>
                            最多要翻 {bossTheoreticalPages} 页（{bossEstimateKeywords.length} 个岗位 × {bossEstimateCities.length} 个城市 × {bossEstimateMaxPages} 页）。
                            {bossTheoreticalExceedsLimit
                              ? ` 已经超过每天 ${bossDailySearchLimit} 页的上限，到上限会停下来告诉你。`
                              : ` 还没超过每天 ${bossDailySearchLimit} 页的上限。`}
                          </p>
                        )}
                      </Field>
                      <Field label="按什么顺序找">
                        <Select value={search.sort || 'default'} onChange={event => updatePlatformSearch(platform, 'sort', event.target.value)}>
                          <option value="default">默认</option>
                          {platform !== '51job' && <option value="newest">最新</option>}
                        </Select>
                      </Field>
                    </div>
                    {platform === 'boss' && <div className="space-y-3 rounded-xl border border-card-border bg-card p-3">
                      <p className="text-xs font-black text-foreground">再细筛一下（可以不填）</p>
                      <div className="grid gap-3 md:grid-cols-2">
                        <Field label="搜索只找这种类型" hint="只对这个平台的搜索生效">
                          <Select value={Array.isArray(bossFilters.job_type) ? bossFilters.job_type[0] || '' : ''} onChange={event => updateBossFilter(search, 'job_type', event.target.value, false)}>
                            <option value="">不限</option>
                            {BOSS_FILTER_OPTIONS.job_type.map(option => <option key={option} value={option}>{option}</option>)}
                          </Select>
                        </Field>
                        <Field label="工资范围">
                          <Select value={Array.isArray(bossFilters.salary) ? bossFilters.salary[0] || '' : ''} onChange={event => updateBossFilter(search, 'salary', event.target.value, false)}>
                            <option value="">不限</option>
                            {BOSS_FILTER_OPTIONS.salary.map(option => <option key={option} value={option}>{option}</option>)}
                          </Select>
                        </Field>
                      </div>
                      {(['experience', 'degree', 'scale'] as const).map(key => {
                        const labels = { experience: '要几年经验', degree: '要什么学历', scale: '公司多大' }
                        const selected: string[] = Array.isArray(bossFilters[key]) ? bossFilters[key] : []
                        return <Field key={key} label={labels[key]}>
                          <div className="flex flex-wrap gap-1.5">
                            {BOSS_FILTER_OPTIONS[key].map(option => <button
                              key={option}
                              type="button"
                              onClick={() => updateBossFilter(search, key, option)}
                              className={`rounded-full border px-2.5 py-1 text-xs font-bold ${selected.includes(option) ? 'border-primary bg-secondary text-primary' : 'border-card-border bg-card text-muted hover:border-primary/40'}`}
                            >{option}</button>)}
                          </div>
                        </Field>
                      })}
                      <Field label="行业编号（懂的再填）">
                        <TagsInput
                          value={Array.isArray(bossFilters.industry) ? bossFilters.industry : []}
                          onChange={value => updatePlatformSearch('boss', 'filters.industry', value)}
                          placeholder="输入 BOSS 的行业数字编号后按回车"
                        />
                        <p className="mt-1 text-xs text-muted">只填数字编号，填错了也没事，会自动忽略。</p>
                      </Field>
                    </div>}
                  </div>}
                </div>
              )
            })}
            </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="先去哪个平台找">
                <Select value={Array.isArray(config.collection?.default_order) ? config.collection.default_order.join(',') : 'boss'} onChange={event => setCollectionOrder(event.target.value)}>
                  <option value="boss">BOSS 直聘</option>
                  <option value="zhilian">智联招聘</option>
                  <option value="boss,zhilian">BOSS 直聘 → 智联招聘</option>
                  <option value="zhilian,boss">智联招聘 → BOSS 直聘</option>
                  <option value="51job">前程无忧</option>
                  <option value="liepin">猎聘</option>
                  <option value="boss,zhilian,51job">BOSS → 智联 → 前程无忧</option>
                  <option value="boss,zhilian,51job,liepin">BOSS → 智联 → 前程无忧 → 猎聘</option>
                </Select>
              </Field>
              <div className="flex items-center justify-between rounded-xl border border-card-border bg-surface px-3 py-2 text-xs font-bold text-muted">
                找到就马上打分
                <Switch checked={config.collection?.auto_score_default ?? false} onChange={value => updateConfig('collection.auto_score_default', value)} />
              </div>
            </div>
          </div>
        </SectionCard>

        {/* Filter Section：全局过滤规则，与上面的平台投递配置分开 */}
        <SectionCard title="全局过滤规则">
          <div className="space-y-4">
            <p className="rounded-xl border border-card-border bg-surface px-3 py-2 text-xs leading-5 text-muted">
              下面这几项是硬条件：不符合的直接跳过，不打分也不投。
            </p>
            <Field label="全职、兼职还是实习">
              <div className="flex flex-wrap gap-1.5">
                {(['全职', '兼职', '实习'] as const).map(option => {
                  const selected: string[] = Array.isArray(config.profile?.job_types) ? config.profile.job_types : []
                  return (
                    <button
                      key={option}
                      type="button"
                      onClick={() => {
                        const next = selected.includes(option)
                          ? selected.filter(item => item !== option)
                          : [...selected, option]
                        updateConfig('profile.job_types', next)
                      }}
                      className={`rounded-full border px-2.5 py-1 text-xs font-bold ${selected.includes(option) ? 'border-primary bg-secondary text-primary' : 'border-card-border bg-card text-muted hover:border-primary/40'}`}
                    >{option}</button>
                  )
                })}
              </div>
              <p className="mt-1 rounded-lg bg-secondary px-3 py-2 text-xs font-bold text-foreground">亮着的就是你要投的类型；三个都亮 = 什么类型都要，不限；全都灭掉 = 一个都不投。看不出是哪种类型的岗位不会拦。</p>
            </Field>
            <NumberRangeField
              label="我想要的工资（千元）"
              minValue={config.profile?.salary_min ?? 0}
              maxValue={config.profile?.salary_max ?? 0}
              onMinChange={value => updateConfig('profile.salary_min', value)}
              onMaxChange={value => updateConfig('profile.salary_max', value)}
              min={0}
              max={200}
              unit="k"
              minPlaceholder="4"
              maxPlaceholder="7"
              emptyZero
              allowMaxZero
            />
            <Field label={`工资高一点也能接受：${config.profile?.salary_ceil_ratio ?? 1.5}`}>
              <Slider
                value={config.profile?.salary_ceil_ratio ?? 1.5}
                onChange={value => updateConfig('profile.salary_ceil_ratio', value)}
                min={1}
                max={5}
                step={0.1}
              />
              <p className="mt-1 text-xs text-muted">超过你期望的工资也行，但有个上限：岗位工资下限超过『你填的最高工资 × 这个倍数』就跳过；倍数越大，接受的高薪范围越宽。最低和最高都填 0 就是不限。</p>
            </Field>
            <div className="flex items-center justify-between rounded-xl border border-card-border bg-surface px-3 py-2">
              <div>
                <label className="text-xs text-foreground">不写工资的岗位不要</label>
                <p className="mt-1 text-xs text-muted">关掉后，这类岗位会留下来打分的时候再看。</p>
              </div>
              <Switch aria-label="过滤面议/无法解析薪资" checked={config.profile?.filter_unparsed_salary ?? true} onChange={v => updateConfig('profile.filter_unparsed_salary', v)} />
            </div>
            <Field label="只要最近发布的">
              <Select value={String(config.profile?.posted_days ?? 7)} onChange={e => updateConfig('profile.posted_days', Number(e.target.value))}>
                <option value="0">不限</option>
                <option value="3">3 天内</option>
                <option value="7">7 天内</option>
                <option value="14">14 天内</option>
                <option value="30">30 天内</option>
              </Select>
              <p className="mt-1 text-xs text-muted">按第一次发现这个岗位的时间算，超过天数的就不投了；平台不给发布时间，只能用第一次发现的时间来算。</p>
            </Field>
          </div>
        </SectionCard>

        {/* Scoring Section */}
        <SectionCard title="多少分才投">
          <div className="space-y-4">
            <Field label={`多少分以上才投： ${config.scoring?.threshold ?? 60}`}>
              <Slider value={config.scoring?.threshold ?? 60} onChange={v => updateConfig('scoring.threshold', v)} min={0} max={100} />
            </Field>
            <div className="flex items-center justify-between rounded-xl border border-card-border bg-surface px-3 py-2">
              <div>
                <label className="text-xs text-foreground">符合评分标准的直接投递</label>
                <p className="mt-1 text-xs text-muted">打开后，打完分直接投递分数达到阈值的岗位；关了只打分、不投。</p>
              </div>
              <Switch checked={config.scoring?.auto_deliver_ready ?? false} onChange={v => updateConfig('scoring.auto_deliver_ready', v)} aria-label="符合评分标准的直接投递" />
            </div>
          </div>
        </SectionCard>

        <SectionCard title="每天投多少、怎么投">
          <div className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="每天最多翻多少页">
                <ValidatedNumberInput label="每天最多翻多少页" value={config.collection?.daily_search_page_limit ?? 60} onChange={value => updateConfig('collection.daily_search_page_limit', value)} min={1} max={200} />
                {bossTheoreticalPages > 0 && (
                  <p className={`mt-1 text-xs ${bossTheoreticalExceedsLimit ? 'font-bold text-warning' : 'text-muted'}`}>
                    现在这个组合最多要翻 {bossTheoreticalPages} 页；{bossTheoreticalExceedsLimit ? `超过了这个上限 ${bossDailySearchLimit} 页，设置这里和跑起来的时候都会提醒你。` : '还没超过这个上限。'}
                  </p>
                )}
              </Field>
              <Field label="每天最多打开多少个岗位">
                <ValidatedNumberInput label="每天最多打开多少个岗位" value={config.collection?.daily_detail_page_limit ?? 900} onChange={value => updateConfig('collection.daily_detail_page_limit', value)} min={1} max={1000} />
                <p className="mt-1 text-xs text-muted">只算真正打开岗位详情的次数；列表里跳过的重复、筛掉的岗位不算。打开详情也算进下面的总数里。</p>
              </Field>
              <Field label="一轮投递几个岗位">
                <ValidatedNumberInput label="一轮投递几个岗位" value={config.throttle?.delivery_round_size ?? 20} onChange={value => updateConfig('throttle.delivery_round_size', value)} min={1} max={100} />
                <p className="mt-1 text-xs text-muted">每投完一轮就歇一次，再继续投下一轮。</p>
              </Field>
              <NumberRangeField
                label="投完一轮之后休息几分钟再继续投递"
                minValue={config.throttle?.delivery_round_rest_min_minutes ?? 5}
                maxValue={config.throttle?.delivery_round_rest_max_minutes ?? 10}
                onMinChange={value => updateConfig('throttle.delivery_round_rest_min_minutes', value)}
                onMaxChange={value => updateConfig('throttle.delivery_round_rest_max_minutes', value)}
                min={1}
                max={60}
              />
              <Field label="动作快慢（越大越慢）">
                <ValidatedNumberInput label="动作快慢" value={config.collection?.collection_delay_multiplier ?? 1.5} onChange={value => updateConfig('collection.collection_delay_multiplier', value)} min={1} max={5} step={0.1} integer={false} />
                <p className="mt-1 text-xs text-muted">找岗位和看回复的时候都按这个来；数字越大，等得越久。</p>
              </Field>
              <NumberRangeField
                label="找完先歇几分钟再投"
                minValue={config.collection?.delivery_cooldown_min_minutes ?? 5}
                maxValue={config.collection?.delivery_cooldown_max_minutes ?? 15}
                onMinChange={value => updateConfig('collection.delivery_cooldown_min_minutes', value)}
                onMaxChange={value => updateConfig('collection.delivery_cooldown_max_minutes', value)}
                min={0}
                max={240}
              />
            </div>
            <p className="text-xs text-muted">找完岗位，每次投之前会随机歇一会儿再投；默认歇 5–15 分钟，只找新岗位的时候不受影响。</p>
            <Field label="每天最多打开多少个页面">
              <ValidatedNumberInput label="每天最多打开多少个页面" value={config.safety?.daily_platform_page_limit ?? 1100} onChange={value => updateConfig('safety.daily_platform_page_limit', value)} min={1} max={2000} />
              <p className="mt-1 text-xs text-muted">采集时打开的搜索页、岗位详情页都算在该平台当天的总数里；默认 1100 次，够 60 次搜索加 900 个详情，还剩 140 次给别的操作。各平台分开算。</p>
            </Field>
            <div className="flex items-center justify-between rounded-xl border border-card-border bg-surface px-3 py-2">
              <div>
                <label className="text-xs text-foreground">投之前先假装看一看</label>
                <p className="mt-1 text-xs text-muted">打开后先像真人一样滚动看一会儿再投；关了就直接投。</p>
              </div>
              <Switch checked={config.throttle?.browse_before_greet ?? true} onChange={v => updateConfig('throttle.browse_before_greet', v)} aria-label="发送前模拟浏览" />
            </div>
            <div className="flex items-center justify-between rounded-xl border border-card-border bg-surface px-3 py-2">
              <div>
                <label className="text-xs text-foreground">智能定制打招呼（结合岗位与简历优化）</label>
                <p className="mt-1 text-xs text-muted">默认关闭；开启后结合岗位与简历定制打招呼，生成失败时用固定文案继续投。</p>
              </div>
              <Switch checked={config.profile?.ai_greeting_enabled ?? false} onChange={v => updateConfig('profile.ai_greeting_enabled', v)} aria-label="智能定制打招呼（结合岗位与简历优化）" />
            </div>
            <div>
              <label className="text-xs text-foreground" htmlFor="fixed-greeting">自定义固定打招呼文案</label>
              <textarea
                id="fixed-greeting"
                aria-label="自定义固定打招呼文案"
                value={config.profile?.fixed_greeting ?? ''}
                onChange={event => updateConfig('profile.fixed_greeting', event.target.value)}
                placeholder="您好，我对贵公司的这个岗位很感兴趣，简历已附上，期待与您进一步沟通，谢谢！"
                rows={3}
                maxLength={300}
                className="mt-1 w-full resize-y rounded-xl border border-card-border bg-surface px-3 py-2 text-sm text-foreground outline-none focus:border-primary"
              />
              <p className="mt-1 text-xs text-muted">开关关时直接发这句；开关开时 AI 没生成出来、没简历、生成失败也回退到这句。最多 300 字，改完点保存就生效，岗位列表里的“打招呼的话”会跟着变。</p>
            </div>
            <p className="text-xs text-muted">什么时候都能投，不用挑时间；每天的数量和分数门槛会帮你控制节奏。</p>
          </div>
        </SectionCard>

        <SectionCard title="风控保护">
          <div className="space-y-4">
            <Field label="风控档位">
              <Select value={config.throttle?.risk_tier || 'standard'} onChange={e => updateConfig('throttle.risk_tier', e.target.value)}>
                <option value="steady">稳妥模式</option>
                <option value="standard">标准模式</option>
                <option value="fast">极速模式</option>
              </Select>
              <p className="mt-1 text-xs text-muted">每批投 3–5 个，批与批之间歇一会：稳妥 3–5 分钟、标准 1–3 分钟、极速半分钟–1 分钟。稳妥模式节奏最克制，适合长期稳妥使用；标准模式兼顾效率与节奏控制；极速模式节奏更快、间隔更短。</p>
              {(config.throttle?.risk_tier || 'standard') === 'fast' && (
                <p className="mt-1 text-xs text-warning">投得太快可能被平台判刷得快，遇到验证提示请及时处理。</p>
              )}
            </Field>
            <div className="flex items-center justify-between rounded-xl border border-card-border bg-surface px-3 py-2">
              <div>
                <label className="text-xs text-foreground">随机操作间隔</label>
                <p className="mt-1 text-xs text-muted">每次点击之间随机歇几秒，更像真人在操作；关了就不随机停顿。</p>
              </div>
              <Switch checked={config.throttle?.random_interval ?? true} onChange={v => updateConfig('throttle.random_interval', v)} aria-label="随机操作间隔" />
            </div>
            <div className="flex items-center justify-between rounded-xl border border-card-border bg-surface px-3 py-2">
              <div>
                <label className="text-xs text-foreground">阶段性自动休息</label>
                <p className="mt-1 text-xs text-muted">每批投 3–5 个就歇一次再继续；关了就不休息。</p>
              </div>
              <Switch checked={config.throttle?.stage_rest ?? true} onChange={v => updateConfig('throttle.stage_rest', v)} aria-label="阶段性自动休息" />
            </div>
            <div className="flex items-center justify-between rounded-xl border border-card-border bg-surface px-3 py-2">
              <div>
                <label className="text-xs text-foreground">遇到验证或限制提示时自动暂停</label>
                <p className="mt-1 text-xs text-muted">平台弹出验证或说操作太频繁时，先停下来等你在浏览器里手动验证完；关了就只提示一声，不再自动停。</p>
              </div>
              <Switch checked={config.throttle?.auto_pause_on_verify ?? true} onChange={v => updateConfig('throttle.auto_pause_on_verify', v)} aria-label="遇到验证或限制提示时自动暂停" />
            </div>
            <div className="flex items-center justify-between gap-3 rounded-xl border border-card-border bg-surface px-3 py-2">
              <div className="min-w-0">
                <label className="text-xs text-foreground">每日投递上限</label>
                <p className="mt-1 text-xs text-muted">达到上限就自动停下今天的投递，明天继续；关了就是不限量。</p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <ValidatedNumberInput
                  label="每日投递上限"
                  className="w-20"
                  value={config.throttle?.daily_cap ?? 120}
                  onChange={value => updateConfig('throttle.daily_cap', value)}
                  min={1}
                  max={500}
                  disabled={!(config.throttle?.daily_cap_enabled ?? true)}
                  ariaLabel="每日投递上限"
                />
                <Switch checked={config.throttle?.daily_cap_enabled ?? true} onChange={v => updateConfig('throttle.daily_cap_enabled', v)} aria-label="每日投递上限开关" />
              </div>
            </div>
            <p className="text-xs text-muted">BOSS 直聘普通账号每天只能投 150 个岗位，合理设置即可。</p>
            <p className="text-xs text-muted">换档位、开关马上生效，不用重启；下次投递就按新的来。“找新岗位的设置”里也能改档位和随机间隔，两边是同一组设置。</p>
          </div>
        </SectionCard>

        {/* Monitor Section */}
        <SectionCard title="多久看一次 HR 回复">
          <div className="space-y-4">
            <Field label="隔几分钟看一次">
              <ValidatedNumberInput label="隔几分钟看一次" value={config.monitor?.interval ?? 30} onChange={value => updateConfig('monitor.interval', value)} min={1} max={120} />
              <p className="mt-1 text-xs text-muted">只看回复的时候会马上检查一次；之后每轮还会按上面“动作快慢”再等一等。</p>
            </Field>
            <Field label="投完先歇几分钟再看回复">
              <ValidatedNumberInput label="投完先歇几分钟再看回复" value={config.monitor?.initial_cooldown_minutes ?? 10} onChange={value => updateConfig('monitor.initial_cooldown_minutes', value)} min={0} max={120} />
              <p className="mt-1 text-xs text-muted">只有自动找工作投完才等这一会儿；只看回复是马上就查，停下任务就不等了。</p>
            </Field>
            {/* v27：聊天页按平台分开配。显示的是实际生效的地址：
                chat_urls 里填了就用填的；没填时 BOSS 沿用老的 chat_url，
                其余平台用内置默认（消息中心入口）。改了要记得点保存。 */}
            <div className="space-y-3">
              <p className="text-xs font-bold text-foreground">各平台聊天页面地址</p>
              {PLATFORM_IDS.map(platform => {
                const configured = config.monitor?.chat_urls?.[platform as SharedPlatformId]
                const legacyBoss = platform === 'boss' ? config.monitor?.chat_url : ''
                const effective = configured || legacyBoss || MONITOR_CHAT_URL_DEFAULTS[platform as SharedPlatformId]
                return (
                  <Field key={platform} label={`${PLATFORM_LABELS[platform]}聊天页面地址`}>
                    <Input
                      value={effective}
                      onChange={e => updateConfig(`monitor.chat_urls.${platform}`, e.target.value)}
                    />
                  </Field>
                )
              })}
              <p className="text-xs text-muted">BOSS 是已验证的沟通页；其余三家默认是各平台消息中心入口，核实到专用聊天页后可改成直链。查 HR 回复时各平台只读自己这一行，不会混用。</p>
            </div>
            <Field label="一次最多处理几段对话">
              <ValidatedNumberInput label="一次最多处理几段对话" value={config.monitor?.max_conversations_per_cycle ?? 5} onChange={value => updateConfig('monitor.max_conversations_per_cycle', value)} min={1} max={20} />
            </Field>
            <Field label="页面连续打不开几次就停">
              <ValidatedNumberInput label="页面连续打不开几次就停" value={config.monitor?.max_consecutive_page_failures ?? 3} onChange={value => updateConfig('monitor.max_consecutive_page_failures', value)} min={1} max={10} />
            </Field>
            <Field label="一次最多发几份简历">
              <ValidatedNumberInput label="一次最多发几份简历" value={config.monitor?.max_resume_sends_per_cycle ?? 5} onChange={value => updateConfig('monitor.max_resume_sends_per_cycle', value)} min={1} max={100} />
            </Field>
            <div className="flex items-center justify-between rounded-2xl border border-card-border bg-surface p-4">
              <div>
                <label className="text-sm font-black text-foreground">HR 问问题就自动回</label>
                <p className="mt-1 text-xs text-muted">默认是关着的。关着的时候只给你写好建议，你在“投递跟进”里看一眼再发。</p>
              </div>
              <Switch checked={config.monitor?.auto_reply_hr_questions ?? false} onChange={v => updateConfig('monitor.auto_reply_hr_questions', v)} />
            </div>
          </div>
        </SectionCard>

        {/* Follow-up Section */}
        <SectionCard title="没回消息怎么办">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <label className="text-xs text-foreground">HR 不回就自动问一次</label>
              <Switch checked={followUpEnabled} onChange={v => updateConfig('follow_up.enabled', v)} aria-label="HR 不回就自动问一次" />
            </div>
            {/* 总开关关了：整个块变灰，子项显示为关、改不了；只有总开关开了才能改。 */}
            <div className={followUpEnabled ? 'space-y-4' : 'pointer-events-none space-y-4 opacity-50'} aria-disabled={!followUpEnabled}>
              <Field label="过几小时没回就问一次">
                <ValidatedNumberInput label="过几小时没回就问一次" value={config.follow_up?.interval_hours ?? 48} onChange={value => updateConfig('follow_up.interval_hours', value)} min={12} max={168} disabled={!followUpEnabled} />
              </Field>
              <div className="flex items-center justify-between">
                <label className="text-xs text-foreground">周末节假日不打扰</label>
                <Switch checked={followUpEnabled ? (config.follow_up?.skip_weekends ?? true) : false} onChange={v => updateConfig('follow_up.skip_weekends', v)} disabled={!followUpEnabled} aria-label="周末节假日不打扰" />
              </div>
            </div>
          </div>
        </SectionCard>

        {/* Dedup Section */}
        <SectionCard title="别重复记岗位">
          <div className="space-y-4">
            <Field label="记录存在哪个文件">
              <Input value={config.dedup?.history_file || ''} onChange={e => updateConfig('dedup.history_file', e.target.value)} />
            </Field>
          </div>
        </SectionCard>

        {/* Browser Section：登录页 / 投递页 / 主界面窗口用哪个浏览器 */}
        <SectionCard title="用哪个浏览器">
          <BrowserSettingsSection config={config} updateConfig={updateConfig} />
        </SectionCard>

        {/* Version Section */}
        <SectionCard title="版本">
          <div className="space-y-3">
            <p className="text-sm text-foreground">
              现在是 {update.currentVersion ? `${update.currentVersion} 版` : '…'}
            </p>
            {update.phase === 'error' ? (
              <p className="text-sm text-muted">
                {update.error || '这次没换上，稍后再试。'}
                {update.info?.downloadCached ? '安装包已经下好了，再试一次不用重新下载。' : ''}
              </p>
            ) : update.info ? (
              <p className="text-sm text-muted">
                有新版本 {update.info.version} 了
                {formatUpdateSize(update.info.size) ? `，大概 ${formatUpdateSize(update.info.size)}` : ''}
                {` — ${formatUpdateNotes(update.info.notes)}`}，点一下就能换上，不用重新下载。
              </p>
            ) : updateNote ? (
              <p className="text-sm text-muted">{updateNote}</p>
            ) : (
              <p className="text-sm text-muted">打开面板时会自动看有没有新版，有了会在首页顶上告诉你。</p>
            )}
            <div className="flex flex-wrap items-center gap-2">
              {update.info && (update.phase === 'idle' || update.phase === 'checking') ? (
                <Button data-testid="config-update-start" onClick={() => void update.start()}>
                  立即更新
                </Button>
              ) : null}
              {update.info && update.phase === 'ready' ? (
                <Button data-testid="config-update-apply" onClick={() => void update.apply()}>
                  马上换上
                </Button>
              ) : null}
              {update.info && (update.phase === 'downloading' || update.phase === 'verifying') ? (
                <span className="inline-flex items-center gap-2 rounded-full bg-surface px-3 py-1.5 text-sm text-muted">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
                  {update.phase === 'downloading' ? `下载中…${update.progress > 0 ? ` ${update.progress}%` : ''}` : '检查中…'}
                </span>
              ) : null}
              {update.phase === 'applying' ? (
                <span className="inline-flex items-center gap-2 rounded-full bg-surface px-3 py-1.5 text-sm text-muted">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
                  换新版本中，面板马上重新打开…
                </span>
              ) : null}
              {update.phase === 'error' ? (
                <Button variant="secondary" onClick={() => void update.retry()}>
                  再试一次
                </Button>
              ) : null}
              <Button
                variant="secondary"
                onClick={() => void handleManualUpdateCheck()}
                disabled={update.checking || update.phase === 'downloading' || update.phase === 'verifying' || update.phase === 'applying'}
              >
                {update.checking ? '正在看有没有新版…' : '检查更新'}
              </Button>
            </div>
            <p className="text-xs text-muted">更新时你的设置和投递记录都不会动，换完自动重新打开。</p>
          </div>
        </SectionCard>
        <UpdateModal update={update} manualCheckSignal={manualUpdateSignal} />

        {/* 底部固定保存栏：与顶部保存栏共用同一 saveConfig 与 dirty 状态。
            经 ModalPortal 挂到 body：原来 fixed bottom-0 在 .animate-page-in
            （带 transform）里面，参照变成整页容器，保存栏沉到页底、滚到底才
            看得见；挂到 body 后才真正贴在视口底部。 */}
        <ModalPortal>
        <div data-testid="config-save-bar" className="fixed bottom-0 left-0 right-0 z-20 border-t border-card-border bg-card/95 px-4 py-3 backdrop-blur">
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-3">
            <span className="text-xs text-muted">
              {dirty ? '改了还没保存' : '设置都保存好了'}
            </span>
            <Button size="sm" onClick={saveConfig} disabled={saving || !dirty}><Save className="w-3 h-3 mr-1" />{saving ? '保存中…' : '保存设置'}</Button>
          </div>
        </div>
        </ModalPortal>
    </div>
  )
}

// Helper components
type BrowserChoice = 'auto' | 'chrome' | 'edge' | 'manual'

function BrowserSettingsSection({ config, updateConfig }: {
  config: Record<string, any>
  updateConfig: (path: string, value: any) => void
}) {
  const [discovered, setDiscovered] = useState<{ chrome?: string | null; edge?: string | null }>({})
  const [detecting, setDetecting] = useState(false)
  const choice = (config.browser?.browser_choice as BrowserChoice) || 'auto'
  const manualPath = (config.browser?.executable_path as string) || ''
  // 本机检测到的浏览器列表：只做检测，不拉起浏览器。10 秒没回来就当
  // 没检测到，只显示选项，不挡设置。
  useEffect(() => {
    let cancelled = false
    setDetecting(true)
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), 10000)
    fetch('/api/browser/diagnostics', { cache: 'no-store', signal: controller.signal })
      .then(res => res.json())
      .then(data => { if (!cancelled && data?.discovered) setDiscovered(data.discovered) })
      .catch(() => { /* 检测不到就只显示选项，不挡设置 */ })
      .finally(() => { window.clearTimeout(timer); if (!cancelled) setDetecting(false) })
    return () => { cancelled = true; controller.abort() }
  }, [])
  const detectedList = [
    { kind: 'chrome' as const, label: 'Chrome', path: discovered.chrome || null },
    { kind: 'edge' as const, label: 'Edge', path: discovered.edge || null },
  ]
  const pickChoice = (next: BrowserChoice) => updateConfig('browser.browser_choice', next)
  const options: { value: BrowserChoice; label: string; hint: string }[] = [
    { value: 'auto', label: '自动选择', hint: discovered.chrome ? `优先用 Chrome（已检测到）` : discovered.edge ? '优先用 Chrome，没找到就用 Edge（已检测到）' : '优先用 Chrome，没找到就用 Edge' },
    { value: 'chrome', label: '只用 Chrome', hint: discovered.chrome || '这台电脑上没检测到 Chrome' },
    { value: 'edge', label: '只用 Edge', hint: discovered.edge || '这台电脑上没检测到 Edge' },
    { value: 'manual', label: '手动指定浏览器', hint: '填浏览器启动文件（.exe）的完整路径，只用它' },
  ]
  return (
    <div className="space-y-3">
      <div className="space-y-2">
        {options.map(option => (
          <label key={option.value} className="flex cursor-pointer items-start gap-2 rounded-xl border border-card-border bg-surface px-3 py-2">
            <input
              type="radio"
              name="browser-choice"
              className="mt-0.5"
              checked={choice === option.value}
              onChange={() => pickChoice(option.value)}
            />
            <span>
              <span className="block text-sm font-bold text-foreground">{option.label}</span>
              <span className="mt-0.5 block break-all text-xs text-muted">{option.hint}</span>
            </span>
          </label>
        ))}
      </div>
      {choice === 'manual' && (
        <div className="space-y-2">
          <Field label="浏览器启动文件完整路径">
            <Input
              value={manualPath}
              onChange={event => updateConfig('browser.executable_path', event.target.value)}
              placeholder="例如 C:\Program Files\Google\Chrome\Application\chrome.exe"
            />
          </Field>
          {detectedList.some(item => item.path) && (
            <div className="space-y-1">
              <p className="text-xs text-muted">已检测到的浏览器，点一下就填进去：</p>
              {detectedList.filter(item => item.path).map(item => (
                <button
                  key={item.kind}
                  type="button"
                  onClick={() => updateConfig('browser.executable_path', item.path)}
                  className="block w-full break-all rounded-lg border border-card-border bg-card px-3 py-1.5 text-left text-xs text-primary hover:border-primary/40"
                >
                  {item.label}：{item.path}
                </button>
              ))}
            </div>
          )}
          <p className="text-xs text-muted">手动路径不存在时会自动回落到检测到的 Chrome 或 Edge，不会直接打不开。</p>
        </div>
      )}
      <p className="text-xs text-muted">
        {detecting ? '正在看电脑上装了哪些浏览器…' : '换了马上生效：下次点“去登录”或开始找工作时就用新的，不用重启。'}
      </p>
    </div>
  )
}

function SectionCard({ title, children }: {
  title: string; children: React.ReactNode
}) {
  // 全部平铺：不折叠、不隐藏，选项一次看全。
  return (
    <Card>
      <div className="p-4 pb-0">
        <span className="text-sm font-black text-foreground">{title}</span>
      </div>
      <div className="px-4 pb-4 pt-3">{children}</div>
    </Card>
  )
}

function Field({ label, children, hint, dataTour }: { label: string; children: React.ReactNode; hint?: string; dataTour?: string }) {
  return (
    <div data-tour={dataTour}>
      <div className="mb-1.5 flex items-baseline justify-between gap-2">
        <label className="block text-xs text-foreground">{label}</label>
        {hint && <span className="text-[11px] leading-4 text-muted">{hint}</span>}
      </div>
      {children}
    </div>
  )
}

/** H-6 数字框：失焦即校验，空值/非法值/范围外就地红字报错并恢复上一次合法值。 */
function ValidatedNumberInput({
  label,
  value,
  min,
  max,
  step,
  integer = true,
  onChange,
  className,
  disabled,
  ariaLabel,
}: {
  label: string
  value: number | undefined | null
  min: number
  max: number
  step?: number
  integer?: boolean
  onChange: (value: number) => void
  className?: string
  disabled?: boolean
  ariaLabel?: string
}) {
  // raw 为 null 时跟随配置值；正在输入时不被配置回写打断。
  const [raw, setRaw] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const lastValidRef = useRef<number | null>(
    typeof value === 'number' && Number.isFinite(value) ? value : null,
  )
  const committedRef = useRef<number | undefined | null>(value)
  const display = raw ?? (value === undefined || value === null ? '' : String(value))
  // 外部改了配置值（如恢复默认设置）时跟上，不留着旧的输入残影。
  useEffect(() => {
    if (value !== committedRef.current) {
      committedRef.current = value
      setRaw(null)
      setError(null)
    }
  }, [value])
  const handleChange = (text: string) => {
    setRaw(text)
    setError(null)
    if (text.trim() === '') return // 空着等失焦再报，不把 0/NaN 写进配置
    const next = Number(text)
    if (!Number.isFinite(next)) return
    committedRef.current = next
    if (validateNumberInput(text, label, min, max, { integer }) === null) {
      lastValidRef.current = next
    }
    onChange(next)
  }
  const handleBlur = () => {
    const text = raw ?? (value === undefined || value === null ? '' : String(value))
    const message = validateNumberInput(text, label, min, max, { integer })
    if (message) {
      setError(message)
      const fallback = lastValidRef.current
      if (fallback !== null) {
        committedRef.current = fallback
        onChange(fallback)
      }
      setRaw(null)
    } else {
      setError(null)
      setRaw(null)
    }
  }
  return (
    <div>
      <Input
        type="number"
        value={display}
        onChange={event => handleChange(event.target.value)}
        onBlur={handleBlur}
        min={min}
        max={max}
        step={step}
        className={className}
        disabled={disabled}
        aria-label={ariaLabel}
      />
      {error ? <p className="mt-1 text-xs text-danger">{error}</p> : null}
    </div>
  )
}

function NumberRangeField({
  label,
  minValue,
  maxValue,
  onMinChange,
  onMaxChange,
  min,
  max,
  step,
  disabled = false,
  unit,
  minPlaceholder,
  maxPlaceholder,
  emptyZero = false,
  allowMaxZero = false,
}: {
  label: string
  minValue: number
  maxValue: number
  onMinChange: (value: number) => void
  onMaxChange: (value: number) => void
  min?: number
  max?: number
  step?: number
  disabled?: boolean
  // 只在薪资这种带单位的区间用：输入框旁标单位（如 k），0 显示为空并
  // 用占位作示例（4k / 7k）。空值 onChange 回来仍是 0，不改过滤行为。
  unit?: string
  minPlaceholder?: string
  maxPlaceholder?: string
  emptyZero?: boolean
  /** 薪资专用：最多填 0 表示不限，最少比最多大也不算错。 */
  allowMaxZero?: boolean
}) {
  const inputClassName = 'h-9 min-w-0 flex-1 bg-transparent px-2 text-center text-sm text-foreground outline-none disabled:cursor-not-allowed disabled:text-muted'
  // H-6：区间框失焦也校验范围与最少<=最多，就地报错；保存前集中校验是后盾。
  const [rangeError, setRangeError] = useState<string | null>(null)
  const checkRange = () => {
    if (!Number.isFinite(minValue) || !Number.isFinite(maxValue)) {
      setRangeError(`${label}请填写数字`)
      return
    }
    if (min !== undefined && max !== undefined && (minValue < min || maxValue < min || minValue > max || maxValue > max)) {
      setRangeError(`${label}应在 ${min}–${max} 之间`)
      return
    }
    if (minValue > maxValue && !(allowMaxZero && maxValue === 0)) {
      setRangeError(`${label}：最少不能比最多大`)
      return
    }
    setRangeError(null)
  }

  return (
    <Field label={label}>
      <div className="flex h-9 items-center overflow-hidden rounded-md border border-card-border bg-card focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/30">
        <span className="shrink-0 pl-3 text-[11px] text-muted">最少</span>
        <input
          aria-label={`${label}最少`}
          className={inputClassName}
          type="number"
          value={emptyZero && minValue === 0 ? '' : minValue}
          placeholder={minPlaceholder}
          onChange={event => onMinChange(Number(event.target.value))}
          onBlur={checkRange}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
        />
        {unit && <span className="shrink-0 pr-1 text-xs font-bold text-muted">{unit}</span>}
        <span className="flex h-full shrink-0 items-center border-x border-card-border bg-surface px-3 text-xs font-bold text-muted">至</span>
        <span className="shrink-0 pl-3 text-[11px] text-muted">最多</span>
        <input
          aria-label={`${label}最多`}
          className={inputClassName}
          type="number"
          value={emptyZero && maxValue === 0 ? '' : maxValue}
          placeholder={maxPlaceholder}
          onChange={event => onMaxChange(Number(event.target.value))}
          onBlur={checkRange}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
        />
        {unit && <span className="shrink-0 pr-3 text-xs font-bold text-muted">{unit}</span>}
      </div>
      {rangeError ? <p className="mt-1 text-xs text-danger">{rangeError}</p> : null}
    </Field>
  )
}
