import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import ConfigPage from './ConfigPage'
import { UpdateProvider } from '@/hooks/useUpdate'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('has no send-window controls and saves without windows', async () => {
  let saved: Record<string, any> = { throttle: { send_windows: ['09:00-12:00'] } }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    let body: unknown = {}
    if (String(input) === '/api/config') {
      if (init?.method === 'POST') { saved = JSON.parse(String(init.body)); body = { success: true } }
      else body = saved
    }
    if (String(input).startsWith('/api/cities')) body = { cities: [] }
    return new Response(JSON.stringify(body), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  expect(await screen.findByText('每天投多少、怎么投')).toBeTruthy()
  expect(screen.queryByRole('button', { name: '添加时间段' })).toBeNull()
  expect(screen.queryByLabelText('时间段 1 开始时间')).toBeNull()
  expect(screen.getByText(/什么时候都能投，不用挑时间/)).toBeTruthy()
  const silent = screen.getByRole('switch', { name: '静默模式' })
  expect(silent.getAttribute('aria-checked')).toBe('false')
  fireEvent.click(silent)
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await screen.findByText('设置已经保存了')
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  expect(saved.throttle.silent_mode).toBe(true)
})

it('defaults optimization off and disabling unknown salary filtering follows internship selection', async () => {
  let saved: Record<string, any> = { profile: { allow_internship: false, filter_unparsed_salary: true } }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    let body: unknown = {}
    if (url === '/api/config') {
      if (init?.method === 'POST') { saved = JSON.parse(String(init.body)); body = { success: true } }
      else body = saved
    }
    if (url.startsWith('/api/cities')) body = { cities: [] }
    return new Response(JSON.stringify(body), { status: 200 })
  }))
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  const internship = await screen.findByRole('switch', { name: '接受实习/管培岗位' })
  const filter = screen.getByRole('switch', { name: '过滤面议/无法解析薪资' })
  expect(filter.getAttribute('aria-checked')).toBe('true')
  expect(screen.queryByText('自动采用优化版')).toBeNull()
  fireEvent.click(internship)
  expect(filter.getAttribute('aria-checked')).toBe('false')
  expect((filter as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await screen.findByText('设置已经保存了')
  expect(saved.profile.allow_internship).toBe(true)
  expect(saved.profile.filter_unparsed_salary).toBe(false)
  fireEvent.click(screen.getByRole('switch', { name: '接受实习/管培岗位' }))
  expect((screen.getByRole('switch', { name: '过滤面议/无法解析薪资' }) as HTMLButtonElement).disabled).toBe(false)
  expect(screen.getByRole('switch', { name: '过滤面议/无法解析薪资' }).getAttribute('aria-checked')).toBe('false')
})

it('points the guide at the enabled search platform when BOSS is off', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const body = url === '/api/config'
      ? { platforms: { boss: { enabled: false }, zhilian: { enabled: true, search: {} } } }
      : url.startsWith('/api/cities') ? { cities: [] } : {}
    return new Response(JSON.stringify(body), { status: 200 })
  }))
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)

  const keyword = await screen.findByPlaceholderText('比如：人力、产品运营')
  const city = screen.getByPlaceholderText('如：深圳')
  expect(document.querySelector('[data-tour="search-keywords"]')?.contains(keyword)).toBe(true)
  expect(document.querySelector('[data-tour="search-city"]')?.contains(city)).toBe(true)
})

it('points the guide at the platform switch when no search platform is enabled', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const body = url === '/api/config'
      ? { platforms: Object.fromEntries(['boss', 'zhilian', '51job', 'liepin'].map(platform => [platform, { enabled: false }])) }
      : url.startsWith('/api/cities') ? { cities: [] } : {}
    return new Response(JSON.stringify(body), { status: 200 })
  }))
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)

  const bossSwitch = await screen.findByRole('checkbox', { name: 'BOSS 直聘' })
  expect(document.querySelector('[data-tour="search-keywords"]')?.contains(bossSwitch)).toBe(true)
  expect(document.querySelector('[data-tour="search-city"]')?.contains(bossSwitch)).toBe(true)
})

it('has no AI settings section on the user page', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const body = url === '/api/config'
      ? { profile: {}, ai: { service: 'zhipu', model: 'glm-4.7-flash' } }
      : url.startsWith('/api/cities') ? { cities: [] } : {}
    return new Response(JSON.stringify(body), { status: 200 })
  }))
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  await screen.findByText('每天投多少、怎么投')
  expect(screen.queryByText('AI 设置')).toBeNull()
  expect(screen.queryByText('提供商')).toBeNull()
  expect(screen.queryByText('API Key')).toBeNull()
  expect(screen.queryByText('模型名称')).toBeNull()
  expect(screen.queryByText('测试连接')).toBeNull()
  expect(screen.queryByText('AI 生成招呼语')).toBeNull()
  expect(screen.queryByText('招呼语质量提醒')).toBeNull()
})

it('shows the smart greeting switch off by default and saves it with the config', async () => {
  let saved: Record<string, any> = { profile: {}, throttle: {} }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    let body: unknown = {}
    if (String(input) === '/api/config') {
      if (init?.method === 'POST') { saved = JSON.parse(String(init.body)); body = { success: true } }
      else body = saved
    }
    if (String(input).startsWith('/api/cities')) body = { cities: [] }
    return new Response(JSON.stringify(body), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  const greetingSwitch = await screen.findByRole('switch', { name: '智能定制打招呼（结合岗位与简历优化）' })
  expect(greetingSwitch.getAttribute('aria-checked')).toBe('false')
  fireEvent.click(greetingSwitch)
  expect(greetingSwitch.getAttribute('aria-checked')).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await screen.findByText('设置已经保存了')
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  expect(saved.profile.ai_greeting_enabled).toBe(true)
  // v38：固定文案改由用户端可配；这轮没动输入框所以没带上它，偏好仍只由管理员面板管。
  expect(saved.profile.fixed_greeting).toBeUndefined()
  expect(saved.profile.greeting_preference).toBeUndefined()
})

it('lets the user edit the fixed greeting and saves it with the config', async () => {
  const DEFAULT_FIXED = '您好，我对贵公司的这个岗位很感兴趣，简历已附上，期待与您进一步沟通，谢谢！'
  let saved: Record<string, any> = { profile: { ai_greeting_enabled: false, fixed_greeting: DEFAULT_FIXED }, throttle: {} }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    let body: unknown = {}
    if (String(input) === '/api/config') {
      if (init?.method === 'POST') { saved = JSON.parse(String(init.body)); body = { success: true } }
      else body = saved
    }
    if (String(input).startsWith('/api/cities')) body = { cities: [] }
    return new Response(JSON.stringify(body), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  const greetingSwitch = await screen.findByRole('switch', { name: '智能定制打招呼（结合岗位与简历优化）' })
  expect(greetingSwitch.getAttribute('aria-checked')).toBe('false')
  const fixedInput = screen.getByLabelText('自定义固定打招呼文案') as HTMLTextAreaElement
  expect(fixedInput.value).toBe(DEFAULT_FIXED)
  fireEvent.change(fixedInput, { target: { value: '您好，看到这个岗位很合适，想和您聊聊，谢谢！' } })
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await screen.findByText('设置已经保存了')
  expect(saved.profile.ai_greeting_enabled).toBe(false)
  expect(saved.profile.fixed_greeting).toBe('您好，看到这个岗位很合适，想和您聊聊，谢谢！')
  expect(saved.profile.greeting_preference).toBeUndefined()
})

it('has only one city picker and BOSS cities still sync to target_cities', async () => {
  let saved: Record<string, any> = {}
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    let body: unknown = {}
    if (url === '/api/config') {
      if (init?.method === 'POST') { saved = JSON.parse(String(init.body)); body = { success: true } }
      else body = { platforms: { boss: { enabled: true, search: { keywords: ['人力'], cities: ['北京'] } } }, profile: { target_cities: ['北京'] } }
    } else if (url.startsWith('/api/cities')) body = { ok: true, cities: [{ name: '北京', code: '010000' }, { name: '上海', code: '020000' }, { name: '深圳', code: '030000' }] }
    return new Response(JSON.stringify(body), { status: 200 })
  }))
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  // 全局过滤里重复的城市选择已去掉，只剩 BOSS 平台标签页里的一处。
  expect((await screen.findAllByPlaceholderText('搜索城市名'))).toHaveLength(1)
  expect(screen.queryByText('在哪个城市找')).toBeNull()
  expect(document.body.textContent).not.toContain('010000')
  expect(document.body.textContent).not.toContain('020000')
  expect(screen.queryByText('刷新城市列表')).toBeNull()
  expect(screen.getAllByRole('button', { name: '刷新城市列表' }).length).toBeGreaterThan(0)
  fireEvent.click(screen.getByRole('button', { name: '上海' }))
  expect(screen.getByText('已选 2 个')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '保存设置' }))
  await screen.findByText('设置已经保存了')
  // BOSS 城市改值仍同步到 profile.target_cities，后端打分/过滤还读它。
  expect(saved.profile.target_cities).toEqual(['北京', '上海'])
  expect(saved.platforms.boss.search.cities).toEqual(['北京', '上海'])
})

it('warns explicitly instead of silently dropping a city without a code', async () => {
  let saved: Record<string, any> = {}
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    let body: unknown = {}
    if (url === '/api/config') {
      if (init?.method === 'POST') { saved = JSON.parse(String(init.body)); body = { success: true } }
      else body = {
        platforms: {
          boss: { enabled: true, search: { keywords: ['人力'], cities: ['北京'] } },
          zhilian: { enabled: true, search: { keywords: [], cities: [] } },
        },
        profile: { target_cities: ['北京'] },
      }
    }
    if (url.startsWith('/api/cities')) body = { ok: true, cities: [{ name: '北京', code: '010000' }, { name: '上海', code: '020000' }] }
    return new Response(JSON.stringify(body), { status: 200 })
  }))
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  await screen.findByText('人力')
  fireEvent.click(screen.getByRole('tab', { name: '智联' }))
  const cityInput = await screen.findByPlaceholderText('如：深圳')
  fireEvent.change(cityInput, { target: { value: '喀什' } })
  expect(await screen.findByText(/这几个城市还没认出来：喀什/)).toBeTruthy()
  // 没认出来的城市还留着，没被偷偷丢掉。
  expect(screen.getByText('喀什', { exact: true })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '保存设置' }))
  await screen.findByText('设置已经保存了')
  expect(saved.platforms.zhilian.search.cities).toEqual(['喀什'])
  expect(saved.platforms.zhilian.search.city_codes).toEqual({ '喀什': '' })
})

it('adds a keyword with the Add button and no longer shows the stale warning', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const body = url === '/api/config'
      ? { platforms: { boss: { enabled: true, search: { keywords: [], cities: ['北京'] } } }, profile: { target_cities: ['北京'] } }
      : url.startsWith('/api/cities') ? { ok: true, cities: [{ name: '北京', code: '010000' }] } : {}
    return new Response(JSON.stringify(body), { status: 200 })
  }))
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  const keywordInput = await screen.findByPlaceholderText('比如：人力、产品运营')
  expect(screen.queryByText(/不然保存不了/)).toBeNull()
  const tagsBox = keywordInput.closest('div')!.parentElement as HTMLElement
  fireEvent.change(keywordInput, { target: { value: '产品经理' } })
  fireEvent.click(within(tagsBox).getByRole('button', { name: '添加' }))
  expect(within(tagsBox).getByText('产品经理')).toBeTruthy()
})

it('switches platform tabs and only shows the selected platform config', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const body = url === '/api/config'
      ? { platforms: {
        boss: { enabled: true, search: { keywords: ['人力'], cities: ['北京'] } },
        zhilian: { enabled: true, search: { keywords: ['运营'], cities: ['深圳'] } },
      }, profile: { target_cities: ['北京'] } }
      : url.startsWith('/api/cities') ? { ok: true, cities: [{ name: '北京', code: '010000' }, { name: '深圳', code: '030000' }] } : {}
    return new Response(JSON.stringify(body), { status: 200 })
  }))
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  await screen.findByText('人力')
  expect(screen.getByText('人力')).toBeTruthy()
  expect(screen.queryByText('运营')).toBeNull()
  fireEvent.click(screen.getByRole('tab', { name: '智联' }))
  await waitFor(() => expect(screen.getByText('运营')).toBeTruthy())
  expect(screen.queryByText('人力')).toBeNull()
  // 底部固定保存栏与顶部共用同一保存逻辑。
  expect(screen.getByRole('button', { name: '保存设置' })).toBeTruthy()
  expect(screen.getByRole('button', { name: '保存' })).toBeTruthy()
})

it('lets the user pick a browser and fill the manual path from the detected list', async () => {
  let saved: Record<string, any> = { browser: { browser_choice: 'auto', executable_path: '' } }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    let body: unknown = {}
    if (url === '/api/config') {
      if (init?.method === 'POST') { saved = JSON.parse(String(init.body)); body = { success: true } }
      else body = saved
    } else if (url === '/api/browser/diagnostics') {
      body = { ok: false, discovered: { chrome: 'C:/Chrome/chrome.exe', edge: 'C:/Edge/msedge.exe' } }
    } else if (url.startsWith('/api/cities')) body = { cities: [] }
    return new Response(JSON.stringify(body), { status: 200 })
  }))
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  expect(await screen.findByText('用哪个浏览器')).toBeTruthy()
  expect(screen.getByRole('radio', { name: /自动选择/ })).toBeTruthy()
  fireEvent.click(screen.getByRole('radio', { name: /只用 Edge/ }))
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await screen.findByText('设置已经保存了')
  expect(saved.browser.browser_choice).toBe('edge')
  // 手动模式：点已检测到的 Edge 就把路径填进去，不用手打。
  fireEvent.click(screen.getByRole('radio', { name: /手动指定浏览器/ }))
  const fill = await screen.findByRole('button', { name: /Edge：C:\/Edge\/msedge\.exe/ })
  fireEvent.click(fill)
  expect((screen.getByPlaceholderText(/例如 C:/) as HTMLInputElement).value).toBe('C:/Edge/msedge.exe')
})

it('shows the risk protection section, defaults to steady, and saves tier and switches', async () => {
  let saved: Record<string, any> = { throttle: {} }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    let body: unknown = {}
    if (String(input) === '/api/config') {
      if (init?.method === 'POST') { saved = JSON.parse(String(init.body)); body = { success: true } }
      else body = saved
    }
    if (String(input).startsWith('/api/cities')) body = { cities: [] }
    return new Response(JSON.stringify(body), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  expect(await screen.findByText('风控保护')).toBeTruthy()
  // 旧的硬编码 120 提示已经删了（守卫逻辑还在后端，不在这里展示）。
  expect(screen.queryByText(/每天最多自动投 120 个/)).toBeNull()
  expect((screen.getByDisplayValue('稳妥模式') as HTMLSelectElement).value).toBe('steady')
  expect(screen.getByRole('switch', { name: '随机操作间隔' }).getAttribute('aria-checked')).toBe('true')
  expect(screen.getByRole('switch', { name: '阶段性自动休息' }).getAttribute('aria-checked')).toBe('true')
  expect(screen.getByRole('switch', { name: '遇到验证或限制提示时自动暂停' }).getAttribute('aria-checked')).toBe('true')
  expect((screen.getByLabelText('每日投递上限') as HTMLInputElement).value).toBe('120')
  // 换成极速档位：温和提示出现。
  fireEvent.change(screen.getByDisplayValue('稳妥模式'), { target: { value: 'fast' } })
  expect(screen.getByText(/节奏较快时更容易触发平台频率限制/)).toBeTruthy()
  // 关掉随机间隔、每日上限改成 50，保存后同一组键写进配置。
  fireEvent.click(screen.getByRole('switch', { name: '随机操作间隔' }))
  fireEvent.change(screen.getByLabelText('每日投递上限'), { target: { value: '50' } })
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await screen.findByText('设置已经保存了')
  expect(saved.throttle.risk_tier).toBe('fast')
  expect(saved.throttle.random_interval).toBe(false)
  expect(saved.throttle.daily_cap).toBe(50)
})

it('notes the BOSS daily 150 application limit next to the daily cap', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const body = url === '/api/config'
      ? { throttle: {} }
      : url.startsWith('/api/cities') ? { cities: [] } : {}
    return new Response(JSON.stringify(body), { status: 200 })
  }))
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  expect(await screen.findByText('BOSS 直聘普通账号每天只能投 150 个岗位，合理设置即可。')).toBeTruthy()
})

it('explains the job-type toggle state in plain words and keeps the three buttons consistent', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const body = url === '/api/config'
      ? { platforms: { boss: { enabled: true, search: { keywords: [], cities: [] } } }, profile: { job_types: [] } }
      : url.startsWith('/api/cities') ? { cities: [] } : {}
    return new Response(JSON.stringify(body), { status: 200 })
  }))
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  await screen.findByText('全局过滤规则')
  expect(screen.getByText(/亮着的就是你要投的类型；全都灭掉/)).toBeTruthy()
  const fulltime = screen.getByRole('button', { name: '全职' })
  const parttime = screen.getByRole('button', { name: '兼职' })
  const intern = screen.getByRole('button', { name: '实习' })
  // 三颗按钮都没选时都没有高亮，点一颗后只有它亮，选中态一致。
  for (const button of [fulltime, parttime, intern]) expect(button.className).not.toContain('bg-secondary')
  fireEvent.click(fulltime)
  expect(fulltime.className).toContain('bg-secondary')
  expect(parttime.className).not.toContain('bg-secondary')
  expect(intern.className).not.toContain('bg-secondary')
  // BOSS 再细筛的下拉框已改名，不再跟全局的三颗按钮重名（v36：文案
  // 里的 BOSS 字样也去掉了，它本来就只在 BOSS 页签里出现）。
  expect(screen.getByText('搜索只找这种类型')).toBeTruthy()
})

it('shows salary in k with 4k/7k placeholders and explains the ceiling ratio in plain words', async () => {
  // GET 返回最后一次 POST 存下的值，和真实后端一致。
  let saved: Record<string, any> = { profile: { salary_min: 0, salary_max: 0, salary_ceil_ratio: 1.5 } }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    let body: unknown = {}
    if (url === '/api/config') {
      if (init?.method === 'POST') { saved = JSON.parse(String(init.body)); body = { success: true } }
      else body = saved
    }
    if (url.startsWith('/api/cities')) body = { cities: [] }
    return new Response(JSON.stringify(body), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  await screen.findByText('全局过滤规则')
  const minInput = screen.getByLabelText('我想要的工资（千元）最少') as HTMLInputElement
  const maxInput = screen.getByLabelText('我想要的工资（千元）最多') as HTMLInputElement
  // 0 显示为空，占位作示例；输入框旁边明确标 k。
  expect(minInput.value).toBe('')
  expect(maxInput.value).toBe('')
  expect(minInput.placeholder).toBe('4')
  expect(maxInput.placeholder).toBe('7')
  expect(screen.getAllByText('k')).toHaveLength(2)
  expect(screen.getByText(/超过你期望的工资也行，但有个上限/)).toBeTruthy()
  expect(screen.getByText(/最低和最高都填 0 就是不限/)).toBeTruthy()
  // 改了又改回原值不算脏：5 再清空回 0（原值），保存按钮是灰的，不发保存。
  fireEvent.change(minInput, { target: { value: '5' } })
  fireEvent.change(minInput, { target: { value: '' } })
  expect((screen.getByRole('button', { name: '保存' }) as HTMLButtonElement).disabled).toBe(true)
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  // 清空后写回去的仍是数字 0：先改成 5 保存，再清空成 0 保存，两次都存对了。
  fireEvent.change(minInput, { target: { value: '5' } })
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await screen.findByText('设置已经保存了')
  expect(saved.profile.salary_min).toBe(5)
  fireEvent.change(minInput, { target: { value: '' } })
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  // 别等“设置已经保存了”：第一次保存的提示还没消失，会误命中旧的；
  // 直接等第二次保存的 POST 写进去。
  await waitFor(() => expect(saved.profile.salary_min).toBe(0))
  expect(saved.profile.salary_max).toBe(0)
})

it('已存的坏数字不被默认值掩盖：失焦就地报错并恢复，保存也被拦住（H-6）', async () => {
  let saved: Record<string, any> = { scoring: { max_candidates: 0 } }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    let body: unknown = {}
    if (url === '/api/config') {
      if (init?.method === 'POST') { saved = JSON.parse(String(init.body)); body = { success: true } }
      else body = saved
    }
    if (url.startsWith('/api/cities')) body = { cities: [] }
    return new Response(JSON.stringify(body), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  await screen.findByText('多少分才投')
  // 已存的 0 露出来，不显示成默认值 20。
  const input = screen.getByDisplayValue('0') as HTMLInputElement
  expect(input.value).toBe('0')
  // 清空再失焦：就地报“不能为空”，并恢复成原来的 0。
  fireEvent.change(input, { target: { value: '' } })
  fireEvent.blur(input)
  expect(screen.getByText('一次最多看多少个不能为空，请填写数字')).toBeTruthy()
  expect((screen.getByDisplayValue('0') as HTMLInputElement).value).toBe('0')
  // 改成范围外再失焦：就地报范围，同样恢复。
  fireEvent.change(input, { target: { value: '999' } })
  fireEvent.blur(input)
  expect(screen.getByText('一次最多看多少个应在 1–100 之间')).toBeTruthy()
  // 脏了之后点保存：坏数字拦在保存前，不发 POST。
  fireEvent.click(screen.getByRole('switch', { name: '随机操作间隔' }))
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  // 保存失败的提示与框下就地报错是同一句话，共两处。
  await waitFor(() => expect(screen.getAllByText('一次最多看多少个应在 1–100 之间')).toHaveLength(2))
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  expect(saved.scoring.max_candidates).toBe(0)
})

it('shows both recruitment types selected when the config says so', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const body = url === '/api/config'
      ? { profile: { recruitment_type: 'both' } }
      : url.startsWith('/api/cities') ? { cities: [] } : {}
    return new Response(JSON.stringify(body), { status: 200 })
  }))
  render(<UpdateProvider><ConfigPage /></UpdateProvider>)
  await screen.findByText('我的简历和我')
  const select = screen.getByDisplayValue('校招、社招均可') as HTMLSelectElement
  expect(select.value).toBe('both')
})
