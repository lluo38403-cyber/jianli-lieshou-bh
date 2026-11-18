import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import AdminPage from './AdminPage'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function stubAdminFetch(overrides: {
  status?: { authorized: boolean; password_configured: boolean }
  onAdminPost?: (body: Record<string, any>) => void
  onLogin?: (body: Record<string, any>) => void
} = {}) {
  const status = overrides.status ?? { authorized: true, password_configured: true }
  const adminConfig = {
    ai: {
      service: 'zhipu',
      provider: 'openai_compatible',
      base_url: 'https://open.bigmodel.cn/api/paas/v4',
      model: 'glm-4.7-flash',
      thinking: 'auto',
      thinking_budget: 2048,
      timeout_seconds: 180,
      scoring_concurrency: 1,
      scoring_second_review: false,
      greeting_style_suggestions: false,
      resume_tailor_enabled: false,
      services: { zhipu: { api_key_masked: 'sk-z***real' } },
    },
    profile: { ai_greeting_enabled: true, greeting_preference: '语气简洁' },
  }
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    let body: unknown = {}
    let statusCode = 200
    if (url === '/api/admin/status') body = status
    else if (url === '/api/admin/config' && init?.method === 'POST') {
      const parsed = JSON.parse(String(init.body))
      overrides.onAdminPost?.(parsed)
      body = { success: true, message: '管理员配置已保存' }
    }
    else if (url === '/api/admin/config') body = adminConfig
    else if (url === '/api/admin/config/models') body = { models: ['glm-4.7-flash', 'glm-4-flash-250414'] }
    else if (url === '/api/admin/login') {
      const parsed = JSON.parse(String(init.body))
      overrides.onLogin?.(parsed)
      if (parsed.password === 'lucky-admin-1') body = { ok: true }
      else { statusCode = 401; body = { error: '管理员密码不正确' } }
    }
    else if (url === '/api/admin/logout') body = { ok: true }
    else if (url === '/api/diagnostics/ai') body = { ok: true, checks: [{ message: '智谱接口连接正常', detail: '仅检测模型列表接口，未发送生成请求。' }] }
    return new Response(JSON.stringify(body), { status: statusCode })
  })
}

it('shows the password setup notice when no admin password is configured', async () => {
  vi.stubGlobal('fetch', stubAdminFetch({ status: { authorized: false, password_configured: false } }))
  render(<AdminPage />)
  expect(await screen.findByText('未设置管理员密码')).toBeTruthy()
  expect(screen.queryByText('管理员登录')).toBeNull()
})

it('shows the login form when not authorized', async () => {
  vi.stubGlobal('fetch', stubAdminFetch({ status: { authorized: false, password_configured: true } }))
  render(<AdminPage />)
  expect(await screen.findByText('管理员登录')).toBeTruthy()
  expect(screen.queryByText('提供商')).toBeNull()
})

it('rejects a wrong password and logs in with the right one', async () => {
  vi.stubGlobal('fetch', stubAdminFetch({ status: { authorized: false, password_configured: true } }))
  render(<AdminPage />)
  const passwordInput = await screen.findByLabelText('管理员密码')
  fireEvent.change(passwordInput, { target: { value: 'wrong-password' } })
  fireEvent.click(screen.getByRole('button', { name: '登录' }))
  expect(await screen.findByText('管理员密码不正确')).toBeTruthy()
  fireEvent.change(passwordInput, { target: { value: 'lucky-admin-1' } })
  fireEvent.click(screen.getByRole('button', { name: '登录' }))
  await screen.findByText('管理员面板')
  expect((screen.getByLabelText('提供商') as HTMLSelectElement).value).toBe('zhipu')
  expect(screen.getByPlaceholderText(/已保存：sk-z\*\*\*real/)).toBeTruthy()
})

it('saves AI settings and greeting fields without leaking the saved key', async () => {
  let posted: Record<string, any> = {}
  vi.stubGlobal('fetch', stubAdminFetch({ onAdminPost: body => { posted = body } }))
  render(<AdminPage />)
  await screen.findByText('管理员面板')
  fireEvent.change(screen.getByLabelText('提供商'), { target: { value: 'deepseek' } })
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'sk-deepseek-new' } })
  fireEvent.click(screen.getByRole('switch', { name: 'AI 生成招呼语' }))
  const fixed = '您好，我做过品牌内容，也独立运营过账号。'
  fireEvent.change(screen.getByRole('textbox', { name: '固定招呼语' }), { target: { value: fixed } })
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await screen.findByText('已保存，全局生效，用户直接体验结果。')
  expect(posted.ai.service).toBe('deepseek')
  expect(posted.ai.services.deepseek.api_key).toBe('sk-deepseek-new')
  expect(posted.profile.ai_greeting_enabled).toBe(false)
  expect(posted.profile.fixed_greeting).toBe(fixed)
  expect(JSON.stringify(posted)).not.toContain('sk-z***real')
  expect(JSON.stringify(posted)).not.toContain('api_key_masked')
})

it('fetches the model list with the admin draft and picks a model', async () => {
  vi.stubGlobal('fetch', stubAdminFetch())
  render(<AdminPage />)
  await screen.findByText('管理员面板')
  fireEvent.click(screen.getByRole('button', { name: '获取模型列表' }))
  await screen.findByText(/已获取 2 个模型/)
  fireEvent.change(screen.getByLabelText('可用模型'), { target: { value: 'glm-4-flash-250414' } })
  expect((screen.getByLabelText('模型名称') as HTMLInputElement).value).toBe('glm-4-flash-250414')
})

it('tests the AI connection from the admin panel', async () => {
  vi.stubGlobal('fetch', stubAdminFetch())
  render(<AdminPage />)
  await screen.findByText('管理员面板')
  fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
  expect(await screen.findByText(/智谱接口连接正常/)).toBeTruthy()
})

it('changes the admin password only when the two entries match', async () => {
  let posted: Record<string, any> = {}
  vi.stubGlobal('fetch', stubAdminFetch({ onAdminPost: body => { posted = body } }))
  render(<AdminPage />)
  await screen.findByText('管理员面板')
  fireEvent.change(screen.getByLabelText('新管理员密码'), { target: { value: 'new-admin-9' } })
  fireEvent.change(screen.getByLabelText('确认新管理员密码'), { target: { value: 'different' } })
  fireEvent.click(screen.getByRole('button', { name: '修改密码' }))
  expect(await screen.findByText('两次输入的密码不一致')).toBeTruthy()
  expect(posted.admin).toBeUndefined()
  fireEvent.change(screen.getByLabelText('确认新管理员密码'), { target: { value: 'new-admin-9' } })
  fireEvent.click(screen.getByRole('button', { name: '修改密码' }))
  await waitFor(() => expect(posted.admin?.new_password).toBe('new-admin-9'))
  expect(await screen.findByText('管理员密码已修改')).toBeTruthy()
})

it('logs out and returns to the login form', async () => {
  vi.stubGlobal('fetch', stubAdminFetch())
  render(<AdminPage />)
  await screen.findByText('管理员面板')
  fireEvent.click(screen.getByRole('button', { name: '退出登录' }))
  expect(await screen.findByText('管理员登录')).toBeTruthy()
  expect(screen.queryByText('提供商')).toBeNull()
})
