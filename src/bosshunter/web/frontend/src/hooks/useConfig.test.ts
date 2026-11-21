import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useConfig } from './useConfig'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const CONFIG = { profile: { target_cities: ['北京'] } }
const SCHEMA = { type: 'object', properties: {} }

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('useConfig', () => {
  it('启动时拉取配置与结构', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/config') return jsonResponse(CONFIG)
      if (url === '/api/config/schema') return jsonResponse(SCHEMA)
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useConfig())
    await waitFor(() => expect(result.current.config).toEqual(CONFIG))
    expect(result.current.schema).toEqual(SCHEMA)
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBeNull()
    expect(result.current.waitingForService).toBe(false)
  })

  it('本地服务没起来：先等服务，服务起来后自动再拉一次，不用手动点', async () => {
    vi.useFakeTimers()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    let mode = 'throw'
    // /api/config 与 /api/config/schema 各走一次：都按当前 mode 返回。
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (mode === 'throw') throw new TypeError('Failed to fetch')
      return String(input) === '/api/config' ? jsonResponse(CONFIG) : jsonResponse(SCHEMA)
    })
    vi.stubGlobal('fetch', fetchMock)
    const { result, unmount } = renderHook(() => useConfig())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000)
    })
    expect(result.current.loading).toBe(false)
    expect(result.current.config).toBeNull()
    expect(result.current.waitingForService).toBe(true)
    expect(result.current.error).toContain('本地服务还没起来')
    // 服务起来后，10 秒一轮的自动重试会把配置拉回来。
    mode = 'ok'
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000)
    })
    expect(result.current.config).toEqual(CONFIG)
    expect(result.current.error).toBeNull()
    expect(result.current.waitingForService).toBe(false)
    unmount()
  })

  it('服务返回 500 不是“没起来”的样子：不等服务，直接报失败文案', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const fetchMock = vi.fn(async () => jsonResponse({}, 500))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useConfig())
    await waitFor(() => expect(result.current.loading).toBe(false), { timeout: 10000 })
    expect(result.current.config).toBeNull()
    expect(result.current.error).toBe('配置接口请求失败')
    expect(result.current.waitingForService).toBe(false)
  })

  it('保存前拦住坏数字：不发请求并报出原因（H-6）', async () => {
    const postCalls: unknown[] = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/config' && init?.method === 'POST') {
        postCalls.push(init.body)
        return jsonResponse({ success: true })
      }
      if (url === '/api/config') return jsonResponse(CONFIG)
      if (url === '/api/config/schema') return jsonResponse(SCHEMA)
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useConfig())
    await waitFor(() => expect(result.current.config).toEqual(CONFIG))
    // 清空数字框曾把 0 写进配置：保存时必须拦住，而不是发给后端。
    act(() => {
      result.current.updateConfig('scoring.max_candidates', 0)
    })
    let saved = true
    await act(async () => {
      saved = await result.current.saveConfig()
    })
    expect(saved).toBe(false)
    expect(postCalls).toHaveLength(0)
    expect(result.current.message?.type).toBe('error')
    expect(result.current.message?.text).toContain('一次最多看多少个')
  })

  it('合法数字照常保存（H-6 回归）', async () => {
    const postCalls: unknown[] = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/config' && init?.method === 'POST') {
        postCalls.push(init.body)
        return jsonResponse({ success: true })
      }
      if (url === '/api/config') return jsonResponse(CONFIG)
      if (url === '/api/config/schema') return jsonResponse(SCHEMA)
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useConfig())
    await waitFor(() => expect(result.current.config).toEqual(CONFIG))
    act(() => {
      result.current.updateConfig('scoring.max_candidates', 25)
    })
    let saved = false
    await act(async () => {
      saved = await result.current.saveConfig()
    })
    expect(saved).toBe(true)
    expect(postCalls).toHaveLength(1)
  })

  it('手动重试（恢复原样）能重新拉取', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/config') return jsonResponse(CONFIG)
      if (url === '/api/config/schema') return jsonResponse(SCHEMA)
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useConfig())
    await waitFor(() => expect(result.current.config).toEqual(CONFIG))
    act(() => {
      result.current.resetConfig()
    })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.config).toEqual(CONFIG)
    expect(fetchMock.mock.calls.filter(([url]) => String(url) === '/api/config').length).toBeGreaterThanOrEqual(2)
  })
})
