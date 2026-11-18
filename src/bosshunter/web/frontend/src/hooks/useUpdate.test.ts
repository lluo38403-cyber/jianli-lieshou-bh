import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useUpdate } from './useUpdate'

function jsonResponse(body: unknown, ok = true) {
  return new Response(JSON.stringify(body), {
    status: ok ? 200 : 500,
    headers: { 'Content-Type': 'application/json' },
  })
}

const NEW_VERSION = {
  update_available: true,
  version: '1.0.2',
  size: 66_549_810,
  sha256: 'a'.repeat(64),
  notes: '更快更稳',
  current_version: '1.0.1',
  status: 'idle',
}

describe('useUpdate', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  let reloadMock: ReturnType<typeof vi.fn>
  let originalLocation: Location | undefined

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      if (url === '/api/update/status') return jsonResponse({ status: 'ready', progress: 100 })
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
    reloadMock = vi.fn()
    // jsdom 的 location.reload 是只读方法：把整个 location 换成带 mock reload 的对象，测完还原。
    if (originalLocation === undefined) originalLocation = window.location
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { href: originalLocation.href, reload: reloadMock },
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    if (originalLocation !== undefined) {
      Object.defineProperty(window, 'location', { configurable: true, value: originalLocation })
    }
    vi.restoreAllMocks()
    cleanup()
  })

  it('checks on mount and surfaces the new version', async () => {
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.info?.version).toBe('1.0.2'))
    expect(result.current.currentVersion).toBe('1.0.1')
    expect(result.current.phase).toBe('idle')
    expect(fetchMock).toHaveBeenCalledWith('/api/update/check')
    expect(fetchMock).toHaveBeenCalledWith('/api/health')
  })

  it('treats a failed mount check as no update and never throws', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      throw new Error('offline')
    })
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.currentVersion).toBe('1.0.1'))
    expect(result.current.info).toBeNull()
    expect(result.current.phase).toBe('idle')
  })

  it('manual check returns true when a new version exists, false when latest, null when offline', async () => {
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.info?.version).toBe('1.0.2'))
    let found: boolean | null | undefined
    await act(async () => {
      found = await result.current.check(true)
    })
    expect(found).toBe(true)

    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse({ update_available: false, current_version: '1.0.1', status: 'idle' })
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      return jsonResponse({})
    })
    await act(async () => {
      found = await result.current.check(true)
    })
    expect(found).toBe(false)

    fetchMock.mockImplementation(async () => {
      throw new Error('offline')
    })
    await act(async () => {
      found = await result.current.check(true)
    })
    expect(found).toBeNull()
  })

  it('start polls status until ready', async () => {
    let statusCalls = 0
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      if (url === '/api/update/start') return jsonResponse({ status: 'downloading', progress: 0 })
      if (url === '/api/update/status') {
        statusCalls += 1
        return statusCalls < 2
          ? jsonResponse({ status: 'downloading', progress: 42 })
          : jsonResponse({ status: 'ready', progress: 100 })
      }
      return jsonResponse({})
    })
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.info?.version).toBe('1.0.2'))
    await act(async () => {
      await result.current.start()
    })
    expect(result.current.phase).toBe('downloading')
    await waitFor(() => expect(result.current.phase).toBe('ready'), { timeout: 5000 })
    expect(result.current.progress).toBe(100)
    expect(fetchMock).toHaveBeenCalledWith('/api/update/start', { method: 'POST' })
  })

  it('start shows a plain-language error when the download cannot begin', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      if (url === '/api/update/start') return jsonResponse({ error: '没开始下载，稍后再试。' }, false)
      return jsonResponse({})
    })
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.info?.version).toBe('1.0.2'))
    await act(async () => {
      await result.current.start()
    })
    expect(result.current.phase).toBe('error')
    expect(result.current.error).toBe('没开始下载，稍后再试。')
  })

  it('apply waits for the panel to come back, then reloads', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.2' })
      if (url === '/api/update/status') return jsonResponse({ status: 'ready', progress: 100 })
      if (url === '/api/update/apply') return jsonResponse({ applying: true })
      return jsonResponse({})
    })
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.info?.version).toBe('1.0.2'))
    await act(async () => {
      await result.current.start()
    })
    await waitFor(() => expect(result.current.phase).toBe('ready'), { timeout: 5000 })
    await act(async () => {
      await result.current.apply()
    })
    expect(result.current.phase).toBe('applying')
    expect(fetchMock).toHaveBeenCalledWith('/api/update/apply', { method: 'POST' })
    await waitFor(() => expect(reloadMock).toHaveBeenCalled(), { timeout: 8000 })
  })
})
