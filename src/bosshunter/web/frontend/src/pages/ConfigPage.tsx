import { useConfig } from '@/hooks/useConfig'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Slider } from '@/components/ui/slider'
import { TagsInput } from '@/components/ui/tags-input'
import { CityMultiSelect, type CityOption } from '@/components/config/CityMultiSelect'
import { ResumeUploadSection } from '@/components/config/ResumeUploadSection'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Save, RotateCcw } from 'lucide-react'
import { useState, useEffect } from 'react'
import { PLATFORM_LABELS, PLATFORM_SHORT_LABELS } from '@/lib/platforms'

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
  const [cityOptions, setCityOptions] = useState<CityOption[]>([])
  const [zhilianCityOptions, setZhilianCityOptions] = useState<CityOption[]>([])
  const [job51CityOptions, setJob51CityOptions] = useState<CityOption[]>([])
  const [liepinCityOptions, setLiepinCityOptions] = useState<CityOption[]>([])
  const [cityRefreshing, setCityRefreshing] = useState(false)
  const [cityMessage, setCityMessage] = useState('')

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
      max_pages: specific.max_pages || legacy.max_pages || (platform === 'boss' ? 3 : 1),
      sort: specific.sort || legacy.sort || 'default',
    }
  }

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

  const updatePlatformCities = (platform: PlatformId, cities: string[]) => {
    const platformCityOptions = platform === 'zhilian' ? zhilianCityOptions : platform === 'liepin' ? liepinCityOptions : job51CityOptions
    const cityCodes = platform !== 'boss'
      ? Object.fromEntries(cities.map(city => {
        const found = platformCityOptions.find(option => option.name.replace(/市$/, '') === city.replace(/市$/, ''))
        return [city, found?.code || '']
      }).filter(([, code]) => code))
      : Object.fromEntries(cityOptions.filter(city => cities.includes(city.name)).map(city => [city.name, city.code]))
    updatePlatformSearch(platform, 'cities', cities)
    updatePlatformSearch(platform, 'city_codes', cityCodes)
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

  return (
    <div className="page-sections h-full overflow-y-auto space-y-4 pr-4">
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
            <Button variant="ghost" size="sm" onClick={resetConfig}><RotateCcw className="w-3 h-3 mr-1" />恢复原样</Button>
            <Button data-tour="config-save" size="sm" onClick={saveConfig} disabled={saving || !dirty}><Save className="w-3 h-3 mr-1" />{saving ? '保存中…' : '保存'}</Button>
          </div>
        </div>

        {/* Profile Section */}
        <SectionCard title="我的简历和我">
          <div className="space-y-4">
            <div data-tour="resume-upload">
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
            <div className="flex items-center justify-between">
              <label className="text-xs text-foreground">实习、管培生的岗位也要</label>
              <Switch aria-label="接受实习/管培岗位" checked={config.profile?.allow_internship ?? false} onChange={v => {
                updateConfig('profile.allow_internship', v)
                if (v) updateConfig('profile.filter_unparsed_salary', false)
              }} />
            </div>
          </div>
        </SectionCard>

        {/* Search Section */}
        <SectionCard title="我想找什么工作">
          <div className="space-y-4">
            <p className="rounded-xl border border-card-border bg-surface px-3 py-2 text-xs leading-5 text-muted">
              四个平台都会自动投：BOSS 直聘是发打招呼聊一聊，智联、前程无忧、猎聘是直接点投递；HR 回了消息会统一来看。不想用的平台把勾去掉。
            </p>
            {(['boss', 'zhilian', '51job', 'liepin'] as PlatformId[]).map(platform => {
              const search = platformSearch(platform)
              const label = PLATFORM_LABELS[platform]
              const platformCityOptions = platform === 'zhilian' ? zhilianCityOptions : platform === 'liepin' ? liepinCityOptions : job51CityOptions
              const enabled = config.platforms?.[platform]?.enabled ?? platform === 'boss'
              const cities = Array.isArray(search.cities) && search.cities.length
                ? search.cities
                : platform === 'boss' ? (config.profile?.target_cities || []) : []
              const cityInput = cities.join(', ')
              const bossFilters = search.filters && typeof search.filters === 'object' ? search.filters : {}
              return (
                <div key={platform} data-tour={!guidePlatform && platform === 'boss' ? 'search-city' : undefined} className={`rounded-2xl border p-4 ${enabled ? 'border-primary/30 bg-surface' : 'border-card-border bg-card opacity-70'}`}>
                  <div data-tour={!guidePlatform && platform === 'boss' ? 'search-keywords' : undefined} className="flex items-center justify-between gap-3">
                    <label className="flex items-center gap-2 text-sm font-black text-foreground">
                      <input type="checkbox" checked={enabled} onChange={event => setPlatformEnabled(platform, event.target.checked)} className="h-4 w-4 accent-primary" />
                      {label}
                    </label>
                    <span className="text-xs text-muted">{enabled ? '正在用' : '没在用'}</span>
                  </div>
                  {enabled && <div className="mt-4 space-y-3">
                    <Field label="想找的岗位" dataTour={platform === guidePlatform ? 'search-keywords' : undefined} hint={platform === 'boss' ? '输完岗位按回车确认，多个岗位用","隔开，不然保存不了。' : undefined}>
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
                          return <span key={city} className={`rounded-full px-2 py-1 text-xs ${matched ? 'bg-success-soft text-success' : 'bg-warning-soft text-warning'}`}>{city} · {matched ? '已经认出来了' : '里面没有'}</span>
                        })}</div>}
                      </>}
                    </Field>
                    <div className="grid gap-3 md:grid-cols-2">
                      <Field label="最多翻几页">
                        <Input type="number" value={search.max_pages || (platform === 'boss' ? 3 : 1)} onChange={event => updatePlatformSearch(platform, 'max_pages', Number(event.target.value))} min={1} max={10} />
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
                    {platform === 'boss' && <div className="space-y-3 border-t border-card-border pt-3">
                      <label className="flex items-center justify-between text-sm font-bold">这个城市新岗位太少就换地方<Switch checked={search.city_yield_policy?.enabled ?? true} onChange={enabled => updatePlatformSearch('boss', 'city_yield_policy', { warmup_minutes: 10, window_minutes: 5, min_new_jobs: 5, ...search.city_yield_policy, enabled })} /></label>
                      <div className="grid gap-3 sm:grid-cols-3">{([{ key: 'warmup_minutes', label: '过几分钟先看一次', initial: 10, max: 60 }, { key: 'window_minutes', label: '隔几分钟看一次', initial: 5, max: 30 }, { key: 'min_new_jobs', label: '一次至少新找到几个', initial: 5, max: 1000 }]).map(field => <Field key={field.key} label={field.label}><Input type="number" min={1} max={field.max} value={search.city_yield_policy?.[field.key] ?? field.initial} onChange={event => updatePlatformSearch('boss', 'city_yield_policy', { enabled: true, warmup_minutes: 10, window_minutes: 5, min_new_jobs: 5, ...search.city_yield_policy, [field.key]: Number(event.target.value) })} /></Field>)}</div>
                    </div>}
                    {platform === 'boss' && <div className="space-y-3 rounded-xl border border-card-border bg-card p-3">
                      <p className="text-xs font-black text-foreground">BOSS 再细筛一下（可以不填）</p>
                      <div className="grid gap-3 md:grid-cols-2">
                        <Field label="全职、兼职还是实习">
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

        {/* Filter Section */}
        <SectionCard title="只要符合这些的">
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
              <p className="mt-1 text-xs text-muted">只投你选中的类型；看不出是哪种类型的不会拦。全部取消就是都要。</p>
            </Field>
            <Field label="在哪个城市找">
              <CityMultiSelect
                options={cityOptions}
                value={config.profile?.target_cities || []}
                onChange={value => updatePlatformCities('boss', value)}
                onRefresh={handleCityRefresh}
                refreshing={cityRefreshing}
                message={cityMessage}
              />
              <p className="mt-1 text-xs text-muted">也是 BOSS 的搜索城市；智联、前程无忧、猎聘的城市在上面各平台里单独设。</p>
            </Field>
            <NumberRangeField
              label="我想要的工资（千元）"
              minValue={config.profile?.salary_min ?? 0}
              maxValue={config.profile?.salary_max ?? 0}
              onMinChange={value => updateConfig('profile.salary_min', value)}
              onMaxChange={value => updateConfig('profile.salary_max', value)}
              min={0}
              max={200}
            />
            <Field label={`工资高一点也能接受：${config.profile?.salary_ceil_ratio ?? 1.5}`}>
              <Slider
                value={config.profile?.salary_ceil_ratio ?? 1.5}
                onChange={value => updateConfig('profile.salary_ceil_ratio', value)}
                min={1}
                max={5}
                step={0.1}
              />
              <p className="mt-1 text-xs text-muted">看岗位工资区间的下限；下限比你填的最高工资 × 这个倍数还高，打分前就跳过。最低和最高都填 0 就是不限。</p>
            </Field>
            <div className="flex items-center justify-between rounded-xl border border-card-border bg-surface px-3 py-2">
              <div>
                <label className="text-xs text-foreground">不写工资的岗位不要</label>
                <p className="mt-1 text-xs text-muted">{config.profile?.allow_internship ? '你已经要实习岗位了，这个过滤自动关了，免得把按天算钱的岗位误伤了。' : '关掉后，这类岗位会留下来打分的时候再看。'}</p>
              </div>
              <Switch aria-label="过滤面议/无法解析薪资" checked={config.profile?.allow_internship ? false : (config.profile?.filter_unparsed_salary ?? true)} disabled={config.profile?.allow_internship === true} onChange={v => updateConfig('profile.filter_unparsed_salary', v)} />
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
            <Field label={`多少分以上才投： ${config.scoring?.threshold || 60}`}>
              <Slider value={config.scoring?.threshold || 60} onChange={v => updateConfig('scoring.threshold', v)} min={0} max={100} />
            </Field>
            <Field label="一次最多看多少个">
              <Input type="number" value={config.scoring?.max_candidates || 20} onChange={e => updateConfig('scoring.max_candidates', Number(e.target.value))} min={1} max={100} />
            </Field>
          </div>
        </SectionCard>

        <SectionCard title="每天投多少、怎么投">
          <div className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="BOSS 每天最多翻多少页">
                <Input type="number" value={config.collection?.daily_search_page_limit ?? 60} onChange={e => updateConfig('collection.daily_search_page_limit', Number(e.target.value))} min={1} max={200} />
                {bossTheoreticalPages > 0 && (
                  <p className={`mt-1 text-xs ${bossTheoreticalExceedsLimit ? 'font-bold text-warning' : 'text-muted'}`}>
                    现在这个组合最多要翻 {bossTheoreticalPages} 页；{bossTheoreticalExceedsLimit ? `超过了这个上限 ${bossDailySearchLimit} 页，设置这里和跑起来的时候都会提醒你。` : '还没超过这个上限。'}
                  </p>
                )}
              </Field>
              <Field label="BOSS 每天最多打开多少个岗位">
                <Input type="number" value={config.collection?.daily_detail_page_limit ?? 900} onChange={e => updateConfig('collection.daily_detail_page_limit', Number(e.target.value))} min={1} max={1000} />
                <p className="mt-1 text-xs text-muted">只算真正打开岗位详情的次数；列表里跳过的重复、筛掉的岗位不算。打开详情也算进下面的总数里。</p>
              </Field>
              <Field label="BOSS 页面连续打不开几次就停">
                <Input type="number" value={config.collection?.max_consecutive_page_failures ?? 3} onChange={e => updateConfig('collection.max_consecutive_page_failures', Number(e.target.value))} min={1} max={10} />
              </Field>
              <NumberRangeField
                label="出问题先歇几分钟"
                minValue={config.collection?.risk_pause_min_minutes ?? 5}
                maxValue={config.collection?.risk_pause_max_minutes ?? 10}
                onMinChange={value => updateConfig('collection.risk_pause_min_minutes', value)}
                onMaxChange={value => updateConfig('collection.risk_pause_max_minutes', value)}
                min={1}
                max={60}
              />
              <Field label="动作快慢（越大越慢）">
                <Input type="number" value={config.collection?.collection_delay_multiplier ?? 1.5} onChange={e => updateConfig('collection.collection_delay_multiplier', Number(e.target.value))} min={1} max={5} step={0.1} />
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
            <p className="text-xs text-muted">找完 BOSS 的岗位，每次投之前会随机歇一会儿再投；默认歇 5–15 分钟，只找新岗位的时候不受影响。</p>
            <Field label="BOSS 每天最多打开多少个页面">
              <Input type="number" value={config.safety?.daily_platform_page_limit ?? 1100} onChange={e => updateConfig('safety.daily_platform_page_limit', Number(e.target.value))} min={1} max={2000} />
              <p className="mt-1 text-xs text-muted">BOSS 的搜索页、岗位详情、自动投递、看回复打开的页面都算在一起；默认 1100 次，够 60 次搜索加 900 个详情，还剩 140 次给别的操作。其他平台不占这个数。</p>
            </Field>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="每天最多投多少个">
                <Input type="number" value={config.throttle?.daily_limit || 120} onChange={e => updateConfig('throttle.daily_limit', Number(e.target.value))} />
                <p className="mt-1 text-xs text-muted">每天最多自动投 120 个，投满了就停，明天继续。</p>
              </Field>
              <NumberRangeField
                label="每投一个隔几秒"
                minValue={config.throttle?.interval_min ?? 60}
                maxValue={config.throttle?.interval_max ?? 180}
                onMinChange={value => updateConfig('throttle.interval_min', value)}
                onMaxChange={value => updateConfig('throttle.interval_max', value)}
                min={10}
                max={600}
              />
            </div>
            <div className="grid items-end gap-4 md:grid-cols-2">
              <div className="flex h-9 items-center justify-between rounded-md border border-card-border bg-surface px-3">
                <label className="text-xs text-foreground">投之前先假装看一看</label>
                <Switch checked={config.throttle?.browse_before_greet ?? true} onChange={v => updateConfig('throttle.browse_before_greet', v)} aria-label="发送前模拟浏览" />
              </div>
              <NumberRangeField
                label="假装看几秒"
                minValue={config.throttle?.browse_duration_min ?? 15}
                maxValue={config.throttle?.browse_duration_max ?? 30}
                onMinChange={value => updateConfig('throttle.browse_duration_min', value)}
                onMaxChange={value => updateConfig('throttle.browse_duration_max', value)}
                min={5}
                max={120}
                disabled={!(config.throttle?.browse_before_greet ?? true)}
              />
            </div>
            <div className="flex items-center justify-between rounded-xl border border-card-border bg-surface px-3 py-2">
              <div>
                <label className="text-xs text-foreground">安静投递（不发打招呼）</label>
                <p className="mt-1 text-xs text-muted">打开后，在 BOSS 上只打开聊天窗口，不发打招呼的话；其他三个平台照常投。</p>
              </div>
              <Switch checked={config.throttle?.silent_mode ?? false} onChange={v => updateConfig('throttle.silent_mode', v)} aria-label="静默模式" />
            </div>
            <p className="text-xs text-muted">什么时候都能投，不用挑时间；每天的数量和分数门槛会帮你控制节奏。</p>
          </div>
        </SectionCard>

        {/* Monitor Section */}
        <SectionCard title="多久看一次 HR 回复">
          <div className="space-y-4">
            <Field label="隔几分钟看一次">
              <Input type="number" value={config.monitor?.interval || 30} onChange={e => updateConfig('monitor.interval', Number(e.target.value))} min={1} max={120} />
              <p className="mt-1 text-xs text-muted">只看回复的时候会马上检查一次；之后每轮还会按上面“动作快慢”再等一等。</p>
            </Field>
            <Field label="投完先歇几分钟再看回复">
              <Input type="number" value={config.monitor?.initial_cooldown_minutes ?? 10} onChange={e => updateConfig('monitor.initial_cooldown_minutes', Number(e.target.value))} min={0} max={120} />
              <p className="mt-1 text-xs text-muted">只有自动找工作投完才等这一会儿；只看回复是马上就查，停下任务就不等了。</p>
            </Field>
            <Field label="聊天页面地址">
              <Input value={config.monitor?.chat_url || ''} onChange={e => updateConfig('monitor.chat_url', e.target.value)} />
            </Field>
            <Field label="一次最多处理几段对话">
              <Input type="number" value={config.monitor?.max_conversations_per_cycle ?? 5} onChange={e => updateConfig('monitor.max_conversations_per_cycle', Number(e.target.value))} min={1} max={20} />
            </Field>
            <Field label="页面连续打不开几次就停">
              <Input type="number" value={config.monitor?.max_consecutive_page_failures ?? 3} onChange={e => updateConfig('monitor.max_consecutive_page_failures', Number(e.target.value))} min={1} max={10} />
            </Field>
            <Field label="一次最多发几份简历">
              <Input type="number" value={config.monitor?.max_resume_sends_per_cycle || 5} onChange={e => updateConfig('monitor.max_resume_sends_per_cycle', Number(e.target.value))} min={1} />
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
              <Switch checked={config.follow_up?.enabled ?? false} onChange={v => updateConfig('follow_up.enabled', v)} />
            </div>
            <Field label="过几小时没回就问一次">
              <Input type="number" value={config.follow_up?.interval_hours || 48} onChange={e => updateConfig('follow_up.interval_hours', Number(e.target.value))} min={12} max={168} />
            </Field>
            <div className="flex items-center justify-between">
              <label className="text-xs text-foreground">周末节假日不打扰</label>
              <Switch checked={config.follow_up?.skip_weekends ?? true} onChange={v => updateConfig('follow_up.skip_weekends', v)} />
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
    </div>
  )
}

// Helper components
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
}) {
  const inputClassName = 'h-9 min-w-0 flex-1 bg-transparent px-2 text-center text-sm text-foreground outline-none disabled:cursor-not-allowed disabled:text-muted'

  return (
    <Field label={label}>
      <div className="flex h-9 items-center overflow-hidden rounded-md border border-card-border bg-card focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/30">
        <span className="shrink-0 pl-3 text-[11px] text-muted">最少</span>
        <input
          aria-label={`${label}最少`}
          className={inputClassName}
          type="number"
          value={minValue}
          onChange={event => onMinChange(Number(event.target.value))}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
        />
        <span className="flex h-full shrink-0 items-center border-x border-card-border bg-surface px-3 text-xs font-bold text-muted">至</span>
        <span className="shrink-0 pl-3 text-[11px] text-muted">最多</span>
        <input
          aria-label={`${label}最多`}
          className={inputClassName}
          type="number"
          value={maxValue}
          onChange={event => onMaxChange(Number(event.target.value))}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
        />
      </div>
    </Field>
  )
}
