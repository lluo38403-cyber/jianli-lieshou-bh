import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { CollectJobsDialog } from './CollectJobsDialog'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const data = url === '/api/config' ? { search: { keywords: ['AI'], cities: ['北京', '上海'] } }
      : url.startsWith('/api/collection/runs') ? [] : { ok: true, cities: [] }
    return new Response(JSON.stringify(data), { status: 200 })
  }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('submits the city policy and only enables an eight hour session when explicitly selected', async () => {
  const start = vi.fn(async (_options: Record<string, unknown>) => ({ ok: true }))
  const close = vi.fn()
  render(<CollectJobsDialog open activeTask={null} onClose={close} onStart={start} />)
  await screen.findByDisplayValue('AI')
  expect(screen.getByRole('switch', { name: '持续采集' }).getAttribute('aria-checked')).toBe('false')
  fireEvent.click(screen.getByRole('switch', { name: '持续采集' }))
  expect((screen.getByLabelText('一次最多找几小时') as HTMLInputElement).value).toBe('8')
  fireEvent.click(screen.getByRole('button', { name: '重新采集' }))
  await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
  expect(start.mock.calls[0][0]).toMatchObject({
    platform_order: ['boss'], continuous_collection: { enabled: true, max_duration_hours: 8 },
    platforms: { boss: { cities: ['北京', '上海'], city_yield_policy: { enabled: true, warmup_minutes: 10, window_minutes: 5, min_new_jobs: 5 } } },
  })
  expect(close).toHaveBeenCalled()
})

it('full workflow hides continuous collection and submits the existing scoring behavior', async () => {
  const start = vi.fn(async (_options: Record<string, unknown>) => ({ ok: true }))
  render(<CollectJobsDialog open mode="full" activeTask={null} onClose={vi.fn()} onStart={start} />)
  await screen.findByDisplayValue('AI')
  expect(screen.queryByRole('switch', { name: '持续采集' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '开始自动找工作' }))
  await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
  expect(start.mock.calls[0][0].auto_score).toBe(true)
  expect(start.mock.calls[0][0].continuous_collection).toBeUndefined()
})

it('shows only the active platform tab and keeps the start buttons in a fixed bottom bar', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const data = url === '/api/config'
      ? { platforms: {
        boss: { enabled: true, search: { keywords: ['AI'], cities: ['北京'] } },
        zhilian: { enabled: true, search: { keywords: ['运营'], cities: ['深圳'] } },
      } }
      : url.startsWith('/api/collection/runs') ? [] : { ok: true, cities: [{ name: '深圳', code: '070000' }, { name: '北京', code: '010000' }] }
    return new Response(JSON.stringify(data), { status: 200 })
  }))
  const start = vi.fn(async (_options: Record<string, unknown>) => ({ ok: true }))
  const { container } = render(<CollectJobsDialog open activeTask={null} onClose={vi.fn()} onStart={start} />)
  await screen.findByDisplayValue('AI')
  // 窄弹窗：max-w-2xl，且一次只配当前 Tab 的平台。
  expect(container.querySelector('.max-w-2xl')).toBeTruthy()
  expect(container.querySelector('.max-w-4xl')).toBeNull()
  expect(screen.getAllByPlaceholderText('产品经理, 产品运营')).toHaveLength(1)
  expect(screen.queryByDisplayValue('运营')).toBeNull()
  fireEvent.click(screen.getByRole('tab', { name: '智联' }))
  await screen.findByDisplayValue('运营')
  expect(screen.queryByDisplayValue('AI')).toBeNull()
  expect(screen.getAllByPlaceholderText('产品经理, 产品运营')).toHaveLength(1)
  // 底部按钮固定可见。
  const footer = screen.getByRole('button', { name: '重新采集' }).closest('div')
  expect(footer?.className).toContain('sticky')
  // 切 Tab 不丢草稿：切回 BOSS，关键词还在；入参结构不变。
  fireEvent.click(screen.getByRole('tab', { name: 'BOSS' }))
  await screen.findByDisplayValue('AI')
  fireEvent.click(screen.getByRole('button', { name: '重新采集' }))
  await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
  expect(start.mock.calls[0][0]).toMatchObject({
    platform_order: ['boss', 'zhilian'],
    platforms: { boss: { keywords: ['AI'], cities: ['北京'] }, zhilian: { keywords: ['运营'], cities: ['深圳'] } },
  })
})

it('shows risk protection in full mode and syncs it to /api/config immediately', async () => {
  let savedConfig: Record<string, any> | null = null
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url === '/api/config') {
      if (init?.method === 'POST') {
        savedConfig = JSON.parse(String(init.body))
        return new Response(JSON.stringify({ success: true }), { status: 200 })
      }
      return new Response(JSON.stringify({ search: { keywords: ['AI'], cities: ['北京'] }, throttle: { risk_tier: 'steady', random_interval: true } }), { status: 200 })
    }
    if (url.startsWith('/api/collection/runs')) return new Response(JSON.stringify([]), { status: 200 })
    return new Response(JSON.stringify({ ok: true, cities: [] }), { status: 200 })
  }))
  const start = vi.fn(async (_options: Record<string, unknown>) => ({ ok: true }))
  render(<CollectJobsDialog open mode="full" activeTask={null} onClose={vi.fn()} onStart={start} />)
  await screen.findByDisplayValue('AI')
  expect(screen.getByText('风控保护')).toBeTruthy()
  expect((screen.getByDisplayValue('稳妥模式') as HTMLSelectElement).value).toBe('steady')
  // 改档位马上保存：同一组配置键、同一条保存链路，和“我的设置”是一份。
  fireEvent.change(screen.getByDisplayValue('稳妥模式'), { target: { value: 'fast' } })
  await waitFor(() => expect(savedConfig?.throttle?.risk_tier).toBe('fast'))
  expect(screen.getByText(/节奏较快时更容易触发平台频率限制/)).toBeTruthy()
  fireEvent.click(screen.getByRole('switch', { name: '随机操作间隔' }))
  await waitFor(() => expect(savedConfig?.throttle?.random_interval).toBe(false))
  await waitFor(() => expect(screen.getByText(/已保存，和“我的设置”里是一样的。/)).toBeTruthy())
})
