import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import AdminPage from './AdminPage'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function stubAdminFetch(overrides: {
  status?: { authorized: boolean; password_configured: boolean }
  relay?: { url?: string; reachable?: boolean; provider?: string | null; model?: string | null; ai_enabled?: boolean | null }
  onAdminPost?: (body: Record<string, any>) => void
  onLogin?: (body: Record<string, any>) => void
} = {}) {
  const status = overrides.status ?? { authorized: true, password_configured: true }
  const adminConfig = {
    ai: {
      relay: overrides.relay ?? {
        url: 'http://127.0.0.1:8687',
        reachable: true,
        provider: 'zhipu',
        model: 'glm-4.7-flash',
        ai_enabled: true,
      },
      thinking: 'auto',
      timeout_seconds: 180,
      scoring_concurrency: 1,
      scoring_second_review: false,
      greeting_style_suggestions: false,
      resume_tailor_enabled: false,
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
    else if (url === '/api/admin/login') {
      const parsed = JSON.parse(String(init?.body))
      overrides.onLogin?.(parsed)
      if (parsed.password === 'lucky-admin-1') body = { ok: true }
      else { statusCode = 401; body = { error: '管理员密码不正确' } }
    }
    else if (url === '/api/admin/logout') body = { ok: true }
    else if (url === '/api/diagnostics/ai') body = { ok: true, checks: [{ message: '云端中转连接正常（zhipu/glm-4.7-flash）', detail: '仅检测中转健康接口，未发送生成请求。' }] }
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
  expect(screen.queryByText('云端中转')).toBeNull()
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
  expect(await screen.findByText('云端中转')).toBeTruthy()
  expect(screen.getByText('http://127.0.0.1:8687')).toBeTruthy()
  expect(screen.getByText('连接正常')).toBeTruthy()
  expect(screen.getByText('glm-4.7-flash')).toBeTruthy()
  expect(screen.getByText(/Key 与模型由云端统一管理/)).toBeTruthy()
  expect(screen.queryByText('提供商')).toBeNull()
  expect(screen.queryByText('API Key')).toBeNull()
})

it('shows the relay as unreachable when health fails', async () => {
  vi.stubGlobal('fetch', stubAdminFetch({
    relay: { url: 'http://127.0.0.1:8687', reachable: false, provider: null, model: null, ai_enabled: null },
  }))
  render(<AdminPage />)
  await screen.findByText('管理员面板')
  expect(screen.getByText('连接失败')).toBeTruthy()
  expect(screen.getAllByText('未知')).toHaveLength(3) // 服务商、模型、AI 开关均为未知
})

it('saves the resume switch and greeting fields without any provider or key', async () => {
  let posted: Record<string, any> = {}
  vi.stubGlobal('fetch', stubAdminFetch({ onAdminPost: body => { posted = body } }))
  render(<AdminPage />)
  await screen.findByText('管理员面板')
  fireEvent.click(screen.getByRole('switch', { name: '按岗位定制简历' }))
  fireEvent.click(screen.getByRole('switch', { name: 'AI 生成招呼语' }))
  const fixed = '您好，我做过品牌内容，也独立运营过账号。'
  fireEvent.change(screen.getByRole('textbox', { name: '固定招呼语' }), { target: { value: fixed } })
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await screen.findByText('已保存，全局生效，用户直接体验结果。')
  expect(posted.ai.resume_tailor_enabled).toBe(true)
  expect(posted.ai.service).toBeUndefined()
  expect(posted.ai.provider).toBeUndefined()
  expect(posted.ai.model).toBeUndefined()
  expect(posted.ai.api_key).toBeUndefined()
  expect(posted.ai.services).toBeUndefined()
  expect(posted.ai.relay).toBeUndefined()
  expect(posted.profile.ai_greeting_enabled).toBe(false)
  expect(posted.profile.fixed_greeting).toBe(fixed)
  expect(JSON.stringify(posted)).not.toContain('api_key')
})

it('tests the relay connection from the admin panel', async () => {
  vi.stubGlobal('fetch', stubAdminFetch())
  render(<AdminPage />)
  await screen.findByText('管理员面板')
  fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
  expect(await screen.findByText(/云端中转连接正常/)).toBeTruthy()
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
  expect(screen.queryByText('云端中转')).toBeNull()
})
