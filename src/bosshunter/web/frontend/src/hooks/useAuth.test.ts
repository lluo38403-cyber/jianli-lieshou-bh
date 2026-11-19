import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { copyText, formatRemaining, useAuth } from './useAuth'

function jsonResponse(body: unknown, ok = true) {
  return new Response(JSON.stringify(body), {
    status: ok ? 200 : 400,
    headers: { 'Content-Type': 'application/json' },
  })
}

const TRIAL_STATUS = {
  machine_id_short: 'a1b2c3d4e5f6',
  status: 'trial',
  remaining_seconds: 6300,
  offline: false,
  error: '',
}

const DISPLAY_CONFIG = {
  buy_url: 'https://example.com/shop',
  wechat_id: 'wx123',
  wechat_qr_path: '/wechat-qr.png',
}

describe('formatRemaining', () => {
  it('试用中按时分秒显示', () => {
    expect(formatRemaining(6300, 'trial')).toBe('试用中（剩余 01:45:00）')
    expect(formatRemaining(59, 'trial')).toBe('试用中（剩余 00:00:59）')
  })

  it('有效期超过一天按天加小时显示', () => {
    expect(formatRemaining(6 * 86400 + 14 * 3600, 'active')).toBe('VIP有效（剩余 6天 14小时）')
    expect(formatRemaining(86400, 'active')).toBe('VIP有效（剩余 1天 0小时）')
  })

  it('剩余 365 天及以上显示长期有效，364 天仍按天数显示', () => {
    expect(formatRemaining(365 * 86400, 'active')).toBe('长期有效')
    expect(formatRemaining(366 * 86400, 'active')).toBe('长期有效')
    expect(formatRemaining(315360000, 'active')).toBe('长期有效')
    expect(formatRemaining(364 * 86400 + 5 * 3600, 'active')).toBe('VIP有效（剩余 364天 5小时）')
  })

  it('过期与未知状态的文案', () => {
    expect(formatRemaining(0, 'expired')).toBe('已过期，请激活')
    expect(formatRemaining(100, 'expired')).toBe('已过期，请激活')
    expect(formatRemaining(0, 'unknown')).toBe('正在看授权状态…')
  })
})

describe('copyText', () => {
  it('剪贴板接口可用时直接用它', async () => {
    const writeText = vi.fn(async () => {})
    Object.defineProperty(window.navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    await expect(copyText('abc')).resolves.toBe(true)
    expect(writeText).toHaveBeenCalledWith('abc')
  })
})

describe('useAuth', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/auth/status') return jsonResponse(TRIAL_STATUS)
      if (url === '/api/auth/config') return jsonResponse(DISPLAY_CONFIG)
      if (url === '/api/auth/refresh') return jsonResponse(TRIAL_STATUS)
      if (url === '/api/auth/activate') {
        const body = JSON.parse(String(init?.body || '{}')) as { card_code?: string }
        if (body.card_code === 'JL-D-AAAA-BBBB-CCCC') return jsonResponse({ ok: true, card_type: 'day' })
        if (body.card_code === 'JL-MASTER-TEST') return jsonResponse({ ok: true, card_type: 'master' })
        return jsonResponse({ error: '这个卡密不对，请检查有没有输错。' }, false)
      }
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    cleanup()
  })

  it('启动时拉取状态与展示配置', async () => {
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.snapshot?.status).toBe('trial'))
    expect(result.current.snapshot?.machineIdShort).toBe('a1b2c3d4e5f6')
    expect(result.current.remainingSeconds).toBe(6300)
    expect(result.current.display.wechatId).toBe('wx123')
    expect(result.current.display.buyUrl).toBe('https://example.com/shop')
  })

  it('激活成功后刷新状态，卡的种类带回来', async () => {
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.snapshot).not.toBeNull())
    let outcome: { ok: boolean; cardType?: string; error?: string } | undefined
    await act(async () => {
      outcome = await result.current.activate('JL-D-AAAA-BBBB-CCCC')
    })
    expect(outcome?.ok).toBe(true)
    expect(outcome?.cardType).toBe('day')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/activate',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('长期权限的卡激活后卡的种类是 master', async () => {
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.snapshot).not.toBeNull())
    let outcome: { ok: boolean; cardType?: string; error?: string } | undefined
    await act(async () => {
      outcome = await result.current.activate('JL-MASTER-TEST')
    })
    expect(outcome?.ok).toBe(true)
    expect(outcome?.cardType).toBe('master')
  })

  it('激活失败返回大白话错误', async () => {
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.snapshot).not.toBeNull())
    let outcome: { ok: boolean; error?: string } | undefined
    await act(async () => {
      outcome = await result.current.activate('JL-D-NOPE-NOPE-NOPE')
    })
    expect(outcome?.ok).toBe(false)
    expect(outcome?.error).toContain('不对')
  })

  it('空卡密不发请求', async () => {
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.snapshot).not.toBeNull())
    const callsBefore = fetchMock.mock.calls.length
    let outcome: { ok: boolean; error?: string } | undefined
    await act(async () => {
      outcome = await result.current.activate('   ')
    })
    expect(outcome?.ok).toBe(false)
    expect(fetchMock.mock.calls.length).toBe(callsBefore)
  })

  it('状态拿不到时不崩，保持未知', async () => {
    fetchMock = vi.fn(async () => {
      throw new Error('down')
    })
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useAuth())
    // 请求层会先退避重试几次（约 3.5 秒），等它耗尽再断言。
    await waitFor(() => expect(result.current.loading).toBe(false), { timeout: 10000 })
    expect(result.current.snapshot).toBeNull()
  })

  it('本地服务刚起来时联不上，重试几次后拿到状态', async () => {
    let statusCalls = 0
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/auth/config') return jsonResponse(DISPLAY_CONFIG)
      if (url === '/api/auth/status') {
        statusCalls += 1
        if (statusCalls <= 2) throw new TypeError('Failed to fetch')
        return jsonResponse(TRIAL_STATUS)
      }
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.snapshot?.status).toBe('trial'), { timeout: 10000 })
    expect(statusCalls).toBeGreaterThanOrEqual(3)
  })
})
