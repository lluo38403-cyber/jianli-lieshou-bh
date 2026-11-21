import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchWithRetry, isRetryableStatus, isServiceNotReadyError, readJsonWithTimeout, withTimeout } from './api'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('isRetryableStatus / isServiceNotReadyError', () => {
  it('5xx 可重试，4xx 不可重试', () => {
    expect(isRetryableStatus(500)).toBe(true)
    expect(isRetryableStatus(502)).toBe(true)
    expect(isRetryableStatus(503)).toBe(true)
    expect(isRetryableStatus(404)).toBe(false)
    expect(isRetryableStatus(200)).toBe(false)
  })

  it('联不上、超时、5xx 都算本地服务没起来', () => {
    expect(isServiceNotReadyError(new TypeError('Failed to fetch'))).toBe(true)
    expect(isServiceNotReadyError(new DOMException('Aborted', 'AbortError'))).toBe(true)
    expect(isServiceNotReadyError(new Error('本地服务返回了 502'))).toBe(true)
    expect(isServiceNotReadyError(new Error('没读到数据'))).toBe(false)
  })
})

describe('fetchWithRetry', () => {
  it('第一次就成功只发一次请求', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)
    const res = await fetchWithRetry('/api/health')
    expect(res.ok).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('联不上时退避重试，好了就返回', async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(jsonResponse({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)
    const onRetry = vi.fn()
    const res = await fetchWithRetry('/api/health', {}, { baseDelayMs: 10, onRetry })
    expect(res.ok).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(onRetry).toHaveBeenCalledTimes(2)
  })

  it('502/503 重试，4xx 直接交回不重试', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({}, 503))
      .mockResolvedValueOnce(jsonResponse({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)
    const res = await fetchWithRetry('/api/health', {}, { baseDelayMs: 10 })
    expect(res.ok).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(2)

    fetchMock.mockReset()
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: '没有这个' }, 404))
    const notFound = await fetchWithRetry('/api/health', {}, { baseDelayMs: 10 })
    expect(notFound.status).toBe(404)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('一直联不上：到次数后把最后一次错误抛出来', async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    vi.stubGlobal('fetch', fetchMock)
    await expect(fetchWithRetry('/api/health', {}, { retries: 2, baseDelayMs: 10 })).rejects.toThrow('Failed to fetch')
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('一直 5xx：到次数后把最后一次响应交回，不抛错', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({}, 500))
    vi.stubGlobal('fetch', fetchMock)
    const res = await fetchWithRetry('/api/health', {}, { retries: 2, baseDelayMs: 10 })
    expect(res.status).toBe(500)
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('写请求（POST）只发一次，不重试', async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    vi.stubGlobal('fetch', fetchMock)
    await expect(
      fetchWithRetry('/api/workbench/task', { method: 'POST', body: '{}' }, { retries: 3, baseDelayMs: 10 }),
    ).rejects.toThrow('Failed to fetch')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('调用方取消后立即停，不再重试', async () => {
    vi.useFakeTimers()
    const controller = new AbortController()
    const fetchMock = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    vi.stubGlobal('fetch', fetchMock)
    const promise = fetchWithRetry('/api/health', {}, { retries: 5, baseDelayMs: 1000, signal: controller.signal })
    const assertion = expect(promise).rejects.toThrow('Aborted')
    // 第一次失败后还在退避等的时候取消。
    await vi.advanceTimersByTimeAsync(100)
    controller.abort()
    await vi.advanceTimersByTimeAsync(5000)
    await assertion
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('单次超时会打断这次尝试再重试，而不是无限等下去', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn((_url: unknown, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))
    vi.stubGlobal('fetch', fetchMock)
    const promise = fetchWithRetry('/api/health', {}, { retries: 1, baseDelayMs: 10, timeoutMs: 1000 })
    const assertion = expect(promise).rejects.toThrow('Aborted')
    await vi.advanceTimersByTimeAsync(5000)
    await assertion
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})

describe('readJsonWithTimeout (v29)', () => {
  it('res.json() 卡住时按超时失败，不一直等', async () => {
    // 回归：fetch 只管到响应头，body 卡住时 res.json() 会一直等，
    // 请求层的超时管不到它——检查更新就一直转圈、没有任何反馈。
    const res = { json: () => new Promise(() => {}) } as unknown as Response
    await expect(readJsonWithTimeout(res, 50)).rejects.toThrow('读取数据超时')
  })

  it('正常响应按时返回解析结果', async () => {
    await expect(readJsonWithTimeout(jsonResponse({ ok: true }), 1000)).resolves.toEqual({ ok: true })
  })
})

describe('withTimeout', () => {
  it('没超时就按原样返回', async () => {
    await expect(withTimeout(Promise.resolve('ok'), 1000, '超时了')).resolves.toBe('ok')
  })

  it('超时就按失败处理，调用方才能恢复按钮、显示错误', async () => {
    vi.useFakeTimers()
    const promise = withTimeout(new Promise(() => {}), 1000, '读取超时了')
    const assertion = expect(promise).rejects.toThrow('读取超时了')
    await vi.advanceTimersByTimeAsync(1000)
    await assertion
  })
})
