import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
  expect(await screen.findByText('投递设置')).toBeTruthy()
  expect(screen.queryByRole('button', { name: '添加时间段' })).toBeNull()
  expect(screen.queryByLabelText('时间段 1 开始时间')).toBeNull()
  expect(screen.getByText(/没有投递时间窗口/)).toBeTruthy()
  const silent = screen.getByRole('switch', { name: '静默模式' })
  expect(silent.getAttribute('aria-checked')).toBe('false')
  fireEvent.click(silent)
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await screen.findByText('配置已保存')
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  expect(saved.throttle.silent_mode).toBe(true)
})

it('saves fixed greeting mode and restores it when the settings page is reopened', async () => {
  let saved: Record<string, any> = { profile: { greeting_preference: '不要问问题' } }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    let body: unknown = {}
    if (url === '/api/config') {
      if (init?.method === 'POST') {
        saved = JSON.parse(String(init.body))
        body = { success: true }
      } else body = saved
    }
    if (url.startsWith('/api/cities')) body = { cities: [] }
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fetchMock)
  const page = render(<ConfigPage />)
  const toggle = await screen.findByRole('switch', { name: 'AI 生成招呼语' })
  expect(toggle.getAttribute('aria-checked')).toBe('true')
  fireEvent.click(toggle)
  const fixed = '您好，我做过品牌内容，也独立运营过账号。想了解这个岗位。'
  fireEvent.change(screen.getByRole('textbox', { name: '固定招呼语' }), { target: { value: fixed } })
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(saved.profile.ai_greeting_enabled).toBe(false))
  await screen.findByText('配置已保存')
  expect(saved.profile.fixed_greeting).toBe(fixed)
  expect(saved.profile.greeting_preference).toBe('不要问问题')
  page.unmount()
  render(<ConfigPage />)
  expect((await screen.findByRole('textbox', { name: '固定招呼语' }) as HTMLTextAreaElement).value).toBe(fixed)
  fireEvent.click(screen.getByRole('switch', { name: 'AI 生成招呼语' }))
  expect(screen.queryByRole('textbox', { name: '固定招呼语' })).toBeNull()
  fireEvent.click(screen.getByRole('switch', { name: 'AI 生成招呼语' }))
  expect((screen.getByRole('textbox', { name: '固定招呼语' }) as HTMLTextAreaElement).value).toBe(fixed)
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
  // AI 设置区现在平铺展示，不再需要点开折叠面板。
  expect(screen.getByRole('switch', { name: '招呼语质量提醒' }).getAttribute('aria-checked')).toBe('false')
  expect(screen.queryByText('自动采用优化版')).toBeNull()
  fireEvent.click(internship)
  expect(filter.getAttribute('aria-checked')).toBe('false')
  expect((filter as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await screen.findByText('配置已保存')
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

  const keyword = await screen.findByPlaceholderText('如：人力、产品运营')
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
