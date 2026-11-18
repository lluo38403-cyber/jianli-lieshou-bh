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
        if (!data.ok) setCityMessage(data.error || '本地城市列表读取失败')
      })
      .catch(() => setCityMessage('本地城市列表读取失败'))
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
      if (!res.ok || !data.ok) throw new Error(data.error || '刷新失败，继续使用本地城市列表')
      setCityMessage(`已刷新 ${data.count} 个城市。`)
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
    return <div className="flex items-center justify-center h-full text-muted text-sm">加载中...</div>
  }

  if (error || !config) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="max-w-md rounded-2xl border border-card-border bg-surface p-6 text-center">
          <div className="text-sm font-black text-foreground">配置加载失败</div>
          <p className="mt-2 text-xs leading-6 text-muted">
            请确认后端服务已启动：在项目根目录运行 bosshunter web，或启动 127.0.0.1:8686 后刷新页面。
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
    <div className="h-full overflow-y-auto space-y-4 pr-4">
        {/* Actions bar */}
        <div className="flex items-center justify-between sticky top-0 bg-background z-10 py-2">
          <div className="flex items-center gap-2">
            {dirty && <span className="text-xs text-warning">有未保存的更改</span>}
            {message && (
              <span className={`text-xs ${message.type === 'success' ? 'text-success' : 'text-danger'}`}>
                {message.text}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={resetConfig}><RotateCcw className="w-3 h-3 mr-1" />重置</Button>
            <Button data-tour="config-save" size="sm" onClick={saveConfig} disabled={saving || !dirty}><Save className="w-3 h-3 mr-1" />{saving ? '保存中...' : '保存'}</Button>
          </div>
        </div>

        {/* Profile Section */}
        <SectionCard title="个人信息">
          <div className="space-y-4">
            <div data-tour="resume-upload">
              <ResumeUploadSection
                currentResumePath={config.profile?.resume_path || ''}
                updateConfig={updateConfig}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="最高学历">
                <Select value={config.profile?.education || ''} onChange={e => updateConfig('profile.education', e.target.value)}>
                  <option value="">未设置</option>
                  <option value="博士">博士</option>
                  <option value="硕士">硕士</option>
                  <option value="本科">本科</option>
                  <option value="大专">大专</option>
                  <option value="其他">其他</option>
                </Select>
              </Field>
              <Field label="求职招聘类型">
                <Select value={config.profile?.recruitment_type || ''} onChange={e => updateConfig('profile.recruitment_type', e.target.value)}>
                  <option value="">未设置</option>
                  <option value="campus">校招</option>
                  <option value="experienced">社招</option>
                  <option value="both">校招、社招均可</option>
                </Select>
              </Field>
            </div>
            <Field label="排除关键词">
              <TagsInput value={config.profile?.deal_breakers || []} onChange={v => updateConfig('profile.deal_breakers', v)} placeholder="如：外包、996" />
            </Field>
            <Field label="JD 排除关键词">
              <TagsInput value={config.profile?.jd_deal_breakers || []} onChange={v => updateConfig('profile.jd_deal_breakers', v)} placeholder="如：需频繁出差、纯销售" />
              <p className="mt-1 text-xs text-muted">完整 JD 含这些词时会在 AI 评分前跳过。</p>
            </Field>
            <Field label="屏蔽公司">
              <TagsInput value={config.profile?.blocked_companies || []} onChange={v => updateConfig('profile.blocked_companies', v)} placeholder="输入公司名称或关键词" />
              <p className="mt-1 text-xs text-muted">公司名包含这些词时不采集，也不会进入 AI 评分。</p>
            </Field>
            <div className="flex items-center justify-between">
              <label className="text-xs text-foreground">接受实习/管培岗位</label>
              <Switch aria-label="接受实习/管培岗位" checked={config.profile?.allow_internship ?? false} onChange={v => {
                updateConfig('profile.allow_internship', v)
                if (v) updateConfig('profile.filter_unparsed_salary', false)
              }} />
            </div>
          </div>
        </SectionCard>

        {/* Search Section */}
        <SectionCard title="搜索设置">
          <div className="space-y-4">
            <p className="rounded-xl border border-card-border bg-surface px-3 py-2 text-xs leading-5 text-muted">
              四个平台都会自动投递：BOSS 直聘发招呼语沟通，智联、前程无忧、猎聘在岗位详情页点投递；HR 回复由监测统一巡查。取消勾选即停用该平台。
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
                    <span className="text-xs text-muted">{enabled ? '已启用' : '未启用'}</span>
                  </div>
                  {enabled && <div className="mt-4 space-y-3">
                    <Field label="搜索关键词" dataTour={platform === guidePlatform ? 'search-keywords' : undefined} hint={platform === 'boss' ? '输入岗位后请按回车键确认，多岗位用","隔开，否则配置无法保存。' : undefined}>
                      <TagsInput value={Array.isArray(search.keywords) ? search.keywords : []} onChange={value => updatePlatformSearch(platform, 'keywords', value)} placeholder="如：人力、产品运营" />
                    </Field>
                    <Field label="搜索城市" dataTour={platform === guidePlatform && platform !== 'boss' ? 'search-city' : undefined}>
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
                        <p className="mt-1 text-xs text-muted">{PLATFORM_SHORT_LABELS[platform]}只使用已验证的城市编码；当前内置 {platformCityOptions.length} 个城市。</p>
                        {!!cities.length && <div className="mt-2 flex flex-wrap gap-1">{cities.map((city: string) => {
                          const matched = platformCityOptions.find(option => option.name.replace(/市$/, '') === city.replace(/市$/, ''))
                          return <span key={city} className={`rounded-full px-2 py-1 text-xs ${matched ? 'bg-success-soft text-success' : 'bg-warning-soft text-warning'}`}>{city} · {matched ? '已自动识别' : '暂未收录'}</span>
                        })}</div>}
                      </>}
                    </Field>
                    <div className="grid gap-3 md:grid-cols-2">
                      <Field label="最大页数">
                        <Input type="number" value={search.max_pages || (platform === 'boss' ? 3 : 1)} onChange={event => updatePlatformSearch(platform, 'max_pages', Number(event.target.value))} min={1} max={10} />
                        {platform === 'boss' && bossTheoreticalPages > 0 && (
                          <p className={`mt-1 rounded-lg px-3 py-2 text-xs ${bossTheoreticalExceedsLimit ? 'bg-warning-soft font-bold text-warning' : 'bg-success-soft text-success'}`}>
                            理论最多 {bossTheoreticalPages} 页（{bossEstimateKeywords.length} 个关键词 × {bossEstimateCities.length} 个城市 × {bossEstimateMaxPages} 页）。
                            {bossTheoreticalExceedsLimit
                              ? ` 已超过每日 ${bossDailySearchLimit} 页上限，到达上限后会提示并停止 BOSS 当前轮。`
                              : ` 未超过每日 ${bossDailySearchLimit} 页上限。`}
                          </p>
                        )}
                      </Field>
                      <Field label="排序">
                        <Select value={search.sort || 'default'} onChange={event => updatePlatformSearch(platform, 'sort', event.target.value)}>
                          <option value="default">默认</option>
                          {platform !== '51job' && <option value="newest">最新</option>}
                        </Select>
                      </Field>
                    </div>
                    {platform === 'boss' && <div className="space-y-3 border-t border-card-border pt-3">
                      <label className="flex items-center justify-between text-sm font-bold">城市低产切换<Switch checked={search.city_yield_policy?.enabled ?? true} onChange={enabled => updatePlatformSearch('boss', 'city_yield_policy', { warmup_minutes: 10, window_minutes: 5, min_new_jobs: 5, ...search.city_yield_policy, enabled })} /></label>
                      <div className="grid gap-3 sm:grid-cols-3">{([{ key: 'warmup_minutes', label: '首次检查（分钟）', initial: 10, max: 60 }, { key: 'window_minutes', label: '检查间隔（分钟）', initial: 5, max: 30 }, { key: 'min_new_jobs', label: '窗口最低新增', initial: 5, max: 1000 }]).map(field => <Field key={field.key} label={field.label}><Input type="number" min={1} max={field.max} value={search.city_yield_policy?.[field.key] ?? field.initial} onChange={event => updatePlatformSearch('boss', 'city_yield_policy', { enabled: true, warmup_minutes: 10, window_minutes: 5, min_new_jobs: 5, ...search.city_yield_policy, [field.key]: Number(event.target.value) })} /></Field>)}</div>
                    </div>}
                    {platform === 'boss' && <div className="space-y-3 rounded-xl border border-card-border bg-card p-3">
                      <p className="text-xs font-black text-foreground">BOSS 搜索筛选（可选）</p>
                      <div className="grid gap-3 md:grid-cols-2">
                        <Field label="职位类型">
                          <Select value={Array.isArray(bossFilters.job_type) ? bossFilters.job_type[0] || '' : ''} onChange={event => updateBossFilter(search, 'job_type', event.target.value, false)}>
                            <option value="">不限</option>
                            {BOSS_FILTER_OPTIONS.job_type.map(option => <option key={option} value={option}>{option}</option>)}
                          </Select>
                        </Field>
                        <Field label="薪资范围">
                          <Select value={Array.isArray(bossFilters.salary) ? bossFilters.salary[0] || '' : ''} onChange={event => updateBossFilter(search, 'salary', event.target.value, false)}>
                            <option value="">不限</option>
                            {BOSS_FILTER_OPTIONS.salary.map(option => <option key={option} value={option}>{option}</option>)}
                          </Select>
                        </Field>
                      </div>
                      {(['experience', 'degree', 'scale'] as const).map(key => {
                        const labels = { experience: '工作经验', degree: '学历要求', scale: '公司规模' }
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
                      <Field label="行业编码">
                        <TagsInput
                          value={Array.isArray(bossFilters.industry) ? bossFilters.industry : []}
                          onChange={value => updatePlatformSearch('boss', 'filters.industry', value)}
                          placeholder="输入 BOSS 行业数字编码后按回车"
                        />
                        <p className="mt-1 text-xs text-muted">只接受数字编码；无效内容会被安全忽略。</p>
                      </Field>
                    </div>}
                  </div>}
                </div>
              )
            })}
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="默认执行顺序">
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
                采集后自动评分
                <Switch checked={config.collection?.auto_score_default ?? false} onChange={value => updateConfig('collection.auto_score_default', value)} />
              </div>
            </div>
          </div>
        </SectionCard>

        {/* Filter Section */}
        <SectionCard title="筛选设置">
          <div className="space-y-4">
            <p className="rounded-xl border border-card-border bg-surface px-3 py-2 text-xs leading-5 text-muted">
              四项筛选在评分前生效：不合要求的岗位直接跳过，不进入评分和投递。
            </p>
            <Field label="求职类型">
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
              <p className="mt-1 text-xs text-muted">只投选中的类型；类型识别不出的岗位不拦截。全部取消等于不限。</p>
            </Field>
            <Field label="城市">
              <CityMultiSelect
                options={cityOptions}
                value={config.profile?.target_cities || []}
                onChange={value => updatePlatformCities('boss', value)}
                onRefresh={handleCityRefresh}
                refreshing={cityRefreshing}
                message={cityMessage}
              />
              <p className="mt-1 text-xs text-muted">同时作为 BOSS 的搜索城市；智联、前程无忧、猎聘的搜索城市在上方各平台里单独设置。</p>
            </Field>
            <NumberRangeField
              label="期望薪资范围（K）"
              minValue={config.profile?.salary_min ?? 0}
              maxValue={config.profile?.salary_max ?? 0}
              onMinChange={value => updateConfig('profile.salary_min', value)}
              onMaxChange={value => updateConfig('profile.salary_max', value)}
              min={0}
              max={200}
            />
            <Field label={`薪资上限放宽倍数：${config.profile?.salary_ceil_ratio ?? 1.5}`}>
              <Slider
                value={config.profile?.salary_ceil_ratio ?? 1.5}
                onChange={value => updateConfig('profile.salary_ceil_ratio', value)}
                min={1}
                max={5}
                step={0.1}
              />
              <p className="mt-1 text-xs text-muted">按岗位薪资区间下限判断；下限超过最高薪资 × 放宽倍数时会在 AI 评分前跳过。最低和最高都填 0 表示不限。</p>
            </Field>
            <div className="flex items-center justify-between rounded-xl border border-card-border bg-surface px-3 py-2">
              <div>
                <label className="text-xs text-foreground">过滤面议/无法解析薪资</label>
                <p className="mt-1 text-xs text-muted">{config.profile?.allow_internship ? '已接受实习，自动关闭此过滤，避免日薪岗位被误排除。' : '关闭后这类岗位会保留给 AI 综合判断。'}</p>
              </div>
              <Switch aria-label="过滤面议/无法解析薪资" checked={config.profile?.allow_internship ? false : (config.profile?.filter_unparsed_salary ?? true)} disabled={config.profile?.allow_internship === true} onChange={v => updateConfig('profile.filter_unparsed_salary', v)} />
            </div>
            <Field label="发布岗位时长">
              <Select value={String(config.profile?.posted_days ?? 7)} onChange={e => updateConfig('profile.posted_days', Number(e.target.value))}>
                <option value="0">不限</option>
                <option value="3">3 天内</option>
                <option value="7">7 天内</option>
                <option value="14">14 天内</option>
                <option value="30">30 天内</option>
              </Select>
              <p className="mt-1 text-xs text-muted">按首次发现岗位的时间算，超过天数的岗位不投递；平台不提供发布时间，所以用首次发现时间代替。</p>
            </Field>
          </div>
        </SectionCard>

        {/* Scoring Section */}
        <SectionCard title="评分设置">
          <div className="space-y-4">
            <Field label={`通过阈值: ${config.scoring?.threshold || 60}`}>
              <Slider value={config.scoring?.threshold || 60} onChange={v => updateConfig('scoring.threshold', v)} min={0} max={100} />
            </Field>
            <Field label="每轮最大候选数">
              <Input type="number" value={config.scoring?.max_candidates || 20} onChange={e => updateConfig('scoring.max_candidates', Number(e.target.value))} min={1} max={100} />
            </Field>
          </div>
        </SectionCard>

        <SectionCard title="投递设置">
          <div className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="BOSS 单日搜索页上限">
                <Input type="number" value={config.collection?.daily_search_page_limit ?? 60} onChange={e => updateConfig('collection.daily_search_page_limit', Number(e.target.value))} min={1} max={200} />
                {bossTheoreticalPages > 0 && (
                  <p className={`mt-1 text-xs ${bossTheoreticalExceedsLimit ? 'font-bold text-warning' : 'text-muted'}`}>
                    当前搜索组合理论最多 {bossTheoreticalPages} 页；{bossTheoreticalExceedsLimit ? `超过本上限 ${bossDailySearchLimit} 页，会在设置处和执行时提示。` : '未超过本上限。'}
                  </p>
                )}
              </Field>
              <Field label="BOSS 单日详情页尝试上限">
                <Input type="number" value={config.collection?.daily_detail_page_limit ?? 900} onChange={e => updateConfig('collection.daily_detail_page_limit', Number(e.target.value))} min={1} max={1000} />
                <p className="mt-1 text-xs text-muted">只计算打开岗位详情的尝试；列表中跳过的重复、过滤岗位不占用详情额度。详情访问也计入下方总上限。</p>
              </Field>
              <Field label="BOSS 连续页面失败停止阈值">
                <Input type="number" value={config.collection?.max_consecutive_page_failures ?? 3} onChange={e => updateConfig('collection.max_consecutive_page_failures', Number(e.target.value))} min={1} max={10} />
              </Field>
              <NumberRangeField
                label="BOSS 风险暂停范围（分钟）"
                minValue={config.collection?.risk_pause_min_minutes ?? 5}
                maxValue={config.collection?.risk_pause_max_minutes ?? 10}
                onMinChange={value => updateConfig('collection.risk_pause_min_minutes', value)}
                onMaxChange={value => updateConfig('collection.risk_pause_max_minutes', value)}
                min={1}
                max={60}
              />
              <Field label="BOSS 操作间隔倍率">
                <Input type="number" value={config.collection?.collection_delay_multiplier ?? 1.5} onChange={e => updateConfig('collection.collection_delay_multiplier', Number(e.target.value))} min={1} max={5} step={0.1} />
                <p className="mt-1 text-xs text-muted">同时作用于 BOSS 采集和监测的页面操作与每轮等待；数值越大，间隔越长。</p>
              </Field>
              <NumberRangeField
                label="BOSS 采集后投递冷却范围（分钟）"
                minValue={config.collection?.delivery_cooldown_min_minutes ?? 5}
                maxValue={config.collection?.delivery_cooldown_max_minutes ?? 15}
                onMinChange={value => updateConfig('collection.delivery_cooldown_min_minutes', value)}
                onMaxChange={value => updateConfig('collection.delivery_cooldown_max_minutes', value)}
                min={0}
                max={240}
              />
            </div>
            <p className="text-xs text-muted">完成 BOSS 采集后，每次会在设定区间内随机等待一次再投递；默认为 5–15 分钟，单独采集不受影响。</p>
            <Field label="BOSS 单日页面访问总上限">
              <Input type="number" value={config.safety?.daily_platform_page_limit ?? 1100} onChange={e => updateConfig('safety.daily_platform_page_limit', Number(e.target.value))} min={1} max={2000} />
              <p className="mt-1 text-xs text-muted">合计 BOSS 搜索页、详情页、自动投递和监测打开的页面；默认 1100 次，可容纳 60 次搜索与 900 次详情，另余 140 次供其他操作使用。其他平台不占用。</p>
            </Field>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="每日发送上限">
                <Input type="number" value={config.throttle?.daily_limit || 120} onChange={e => updateConfig('throttle.daily_limit', Number(e.target.value))} />
                <p className="mt-1 text-xs text-muted">每天最多自动投递 120 个，达到上限自动停止，第二天继续。</p>
              </Field>
              <NumberRangeField
                label="发送间隔范围（秒）"
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
                <label className="text-xs text-foreground">发送前模拟浏览</label>
                <Switch checked={config.throttle?.browse_before_greet ?? true} onChange={v => updateConfig('throttle.browse_before_greet', v)} aria-label="发送前模拟浏览" />
              </div>
              <NumberRangeField
                label="模拟浏览时长范围（秒）"
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
                <label className="text-xs text-foreground">静默模式</label>
                <p className="mt-1 text-xs text-muted">开启后 BOSS 只打开沟通会话，不发自定义招呼语；智联、前程无忧、猎聘正常投递。</p>
              </div>
              <Switch checked={config.throttle?.silent_mode ?? false} onChange={v => updateConfig('throttle.silent_mode', v)} aria-label="静默模式" />
            </div>
            <p className="text-xs text-muted">没有投递时间窗口：任何时间都能投递，全靠每日上限和匹配度门槛守住节奏。</p>
          </div>
        </SectionCard>

        {/* Monitor Section */}
        <SectionCard title="监控设置">
          <div className="space-y-4">
            <Field label="检查间隔 (分钟)">
              <Input type="number" value={config.monitor?.interval || 30} onChange={e => updateConfig('monitor.interval', Number(e.target.value))} min={1} max={120} />
              <p className="mt-1 text-xs text-muted">单独监测会立即检查一次；后续轮询还会乘以 BOSS 操作间隔倍率。</p>
            </Field>
            <Field label="全流程首次监测冷却 (分钟)">
              <Input type="number" value={config.monitor?.initial_cooldown_minutes ?? 10} onChange={e => updateConfig('monitor.initial_cooldown_minutes', Number(e.target.value))} min={0} max={120} />
              <p className="mt-1 text-xs text-muted">仅运行全流程发送结束后生效；单独监测立即检查，停止任务可取消等待。</p>
            </Field>
            <Field label="聊天页 URL">
              <Input value={config.monitor?.chat_url || ''} onChange={e => updateConfig('monitor.chat_url', e.target.value)} />
            </Field>
            <Field label="每轮最多处理对话数">
              <Input type="number" value={config.monitor?.max_conversations_per_cycle ?? 5} onChange={e => updateConfig('monitor.max_conversations_per_cycle', Number(e.target.value))} min={1} max={20} />
            </Field>
            <Field label="连续页面失败停止阈值">
              <Input type="number" value={config.monitor?.max_consecutive_page_failures ?? 3} onChange={e => updateConfig('monitor.max_consecutive_page_failures', Number(e.target.value))} min={1} max={10} />
            </Field>
            <Field label="每轮最多发简历数">
              <Input type="number" value={config.monitor?.max_resume_sends_per_cycle || 5} onChange={e => updateConfig('monitor.max_resume_sends_per_cycle', Number(e.target.value))} min={1} />
            </Field>
            <div className="flex items-center justify-between rounded-2xl border border-card-border bg-surface p-4">
              <div>
                <label className="text-sm font-black text-foreground">检测到 HR 问题时自动回复</label>
                <p className="mt-1 text-xs text-muted">默认关闭。关闭时只生成回复建议，需要你在“监测执行”中确认后发送。</p>
              </div>
              <Switch checked={config.monitor?.auto_reply_hr_questions ?? false} onChange={v => updateConfig('monitor.auto_reply_hr_questions', v)} />
            </div>
          </div>
        </SectionCard>

        {/* Follow-up Section */}
        <SectionCard title="跟进设置">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <label className="text-xs text-foreground">启用自动跟进</label>
              <Switch checked={config.follow_up?.enabled ?? false} onChange={v => updateConfig('follow_up.enabled', v)} />
            </div>
            <Field label="跟进间隔 (小时)">
              <Input type="number" value={config.follow_up?.interval_hours || 48} onChange={e => updateConfig('follow_up.interval_hours', Number(e.target.value))} min={12} max={168} />
            </Field>
            <div className="flex items-center justify-between">
              <label className="text-xs text-foreground">跳过周末节假日</label>
              <Switch checked={config.follow_up?.skip_weekends ?? true} onChange={v => updateConfig('follow_up.skip_weekends', v)} />
            </div>
          </div>
        </SectionCard>

        {/* Dedup Section */}
        <SectionCard title="去重设置">
          <div className="space-y-4">
            <Field label="历史记录文件路径">
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
