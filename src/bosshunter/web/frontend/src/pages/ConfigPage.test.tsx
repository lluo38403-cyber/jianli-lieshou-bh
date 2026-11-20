import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import ConfigPage from './ConfigPage'

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
  render(<ConfigPage />)
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
  render(<ConfigPage />)
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
  render(<ConfigPage />)

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
  render(<ConfigPage />)

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
  render(<ConfigPage />)
  await screen.findByText('每天投多少、怎么投')
  expect(screen.queryByText('AI 设置')).toBeNull()
  expect(screen.queryByText('提供商')).toBeNull()
  expect(screen.queryByText('API Key')).toBeNull()
  expect(screen.queryByText('模型名称')).toBeNull()
  expect(screen.queryByText('测试连接')).toBeNull()
  expect(screen.queryByText('AI 生成招呼语')).toBeNull()
  expect(screen.queryByText('招呼语质量提醒')).toBeNull()
})

it('shows city names without internal codes and offers hot-city quick picks', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const body = url === '/api/config'
      ? { platforms: { boss: { enabled: true, search: { keywords: ['人力'], cities: ['北京'] } } }, profile: { target_cities: ['北京'] } }
      : url.startsWith('/api/cities') ? { ok: true, cities: [{ name: '北京', code: '010000' }, { name: '上海', code: '020000' }, { name: '深圳', code: '030000' }] } : {}
    return new Response(JSON.stringify(body), { status: 200 })
  }))
  render(<ConfigPage />)
  // 页面有两个城市选择器（BOSS 平台、全局过滤），都只显示城市名。
  expect((await screen.findAllByPlaceholderText('搜索城市名')).length).toBeGreaterThan(0)
  expect(document.body.textContent).not.toContain('010000')
  expect(document.body.textContent).not.toContain('020000')
  expect(screen.queryByText('刷新城市列表')).toBeNull()
  expect(screen.getAllByRole('button', { name: '刷新城市列表' }).length).toBeGreaterThan(0)
  fireEvent.click(screen.getAllByRole('button', { name: '上海' })[0])
  expect((await screen.findAllByText('已选 2 个')).length).toBeGreaterThan(0)
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
  render(<ConfigPage />)
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
  render(<ConfigPage />)
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
  render(<ConfigPage />)
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
  render(<ConfigPage />)
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
