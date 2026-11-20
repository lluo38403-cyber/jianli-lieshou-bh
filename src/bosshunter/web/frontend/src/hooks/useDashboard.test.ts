import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useDashboard, type Job } from './useDashboard'

const jsonResponse = (body: unknown) => new Response(JSON.stringify(body))

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('greeting save and dashboard refresh', () => {
  it('keeps the saved final version when an older refresh finishes later', async () => {
    const original = { id: 'greeting-1', greeting: '原文', greeting_selection: 'original' } as Job
    const saved = { ...original, greeting: '刚刚确认的编辑版本', greeting_selection: 'edited' }
    const workbench = {
      pending_confirmation: [], pending_greetings: [original], send_errors: [], needs_resume: [],
    }
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(workbench)))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useDashboard('workbench'))
    await waitFor(() => expect(result.current.loading).toBe(false))

    let finishOldRefresh!: (value: Response) => void
    fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finishOldRefresh = resolve }))
    let oldRefresh!: Promise<void>
    act(() => { oldRefresh = result.current.refresh() })
    act(() => { result.current.updateGreetingJob(saved) })
    expect(result.current.workbench.pending_greetings[0].greeting).toBe(saved.greeting)
    await act(async () => {
      finishOldRefresh(jsonResponse(workbench))
      await oldRefresh
    })
    expect(result.current.workbench.pending_greetings[0].greeting).toBe(saved.greeting)
    expect(result.current.workbench.pending_greetings[0].greeting_selection).toBe('edited')

    const later = { ...saved, greeting: '稍后在另一个页面确认的版本' }
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...workbench, pending_greetings: [later] }))
    await act(async () => { await result.current.refresh() })
    expect(result.current.workbench.pending_greetings[0].greeting).toBe(later.greeting)
  })
})


describe('dashboard waits for the local service and recovers', () => {
  it('retries a refused connection and loads once the service is up', async () => {
    const workbench = {
      pending_confirmation: [], pending_greetings: [], send_errors: [], needs_resume: [],
    }
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockImplementation(() => Promise.resolve(jsonResponse(workbench)))
    vi.stubGlobal('fetch', fetchMock)
    const { result, unmount } = renderHook(() => useDashboard('workbench'))
    // 前两次联不上：先撤掉骨架屏、给等服务的提示，而不是一直干等。
    await waitFor(() => expect(result.current.loading).toBe(false), { timeout: 10000 })
    // 服务起来后数据自动补上，报错清掉。
    await waitFor(() => expect(result.current.workbench.pending_greetings).toEqual([]), { timeout: 10000 })
    await waitFor(() => expect(result.current.error).toBe(''), { timeout: 10000 })
    expect(result.current.waitingForService).toBe(false)
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(3)
    unmount()
  })

  it('service stays down: shows a waiting error, then recovers when it comes back', async () => {
    vi.useFakeTimers()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const workbench = {
      pending_confirmation: [], pending_greetings: [], send_errors: [], needs_resume: [],
    }
    const fetchMock = vi.fn(async (): Promise<Response> => {
      throw new TypeError('Failed to fetch')
    })
    vi.stubGlobal('fetch', fetchMock)
    const { result, unmount } = renderHook(() => useDashboard('workbench'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000)
    })
    expect(result.current.loading).toBe(false)
    expect(result.current.waitingForService).toBe(true)
    expect(result.current.error).toContain('本地服务还没起来')
    // 服务起来后，下一轮自动刷新就把数据补上，不用手动点。
    // 注意：每次调用都要返回新的 Response，复用同一个会被 .json() 消费掉。
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(workbench)))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000)
    })
    expect(result.current.error).toBe('')
    expect(result.current.waitingForService).toBe(false)
    unmount()
  })

  it('a 500 from the service is treated as not ready, not a data error', async () => {
    vi.useFakeTimers()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const fetchMock = vi.fn(async (): Promise<Response> => new Response('{}', { status: 500 }))
    vi.stubGlobal('fetch', fetchMock)
    const { result, unmount } = renderHook(() => useDashboard('workbench'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000)
    })
    expect(result.current.waitingForService).toBe(true)
    expect(result.current.error).toContain('本地服务还没起来')
    unmount()
  })
})

describe('verification state', () => {
  it('surfaces an open hold and suspended platforms from the workbench payload', async () => {
    const workbench = {
      pending_confirmation: [], pending_greetings: [], send_errors: [], needs_resume: [],
      send_quota: { daily_limit: 120, sent: 5, remaining: 115, exhausted: false, suspended_platforms: ['boss'] },
      open_hold: { id: 7, platform: 'boss', url: 'https://www.zhipin.com/web/user/verify', evidence: 'captcha_url', created_at: '2026-11-19 13:00:00' },
    }
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(workbench)))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useDashboard('workbench'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.verification.open_hold?.id).toBe(7)
    expect(result.current.verification.suspended_platforms).toEqual(['boss'])
  })

  it('defaults to no hold when the payload carries none', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({
      pending_confirmation: [], pending_greetings: [], send_errors: [], needs_resume: [],
    })))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useDashboard('workbench'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.verification.open_hold).toBeNull()
    expect(result.current.verification.suspended_platforms).toEqual([])
  })
})


describe('dashboard progress polling', () => {
  it('updates progress even when the embedded document reports hidden', async () => {
    vi.useFakeTimers()
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    let completed = 1
    const fetchMock = vi.fn(async () => jsonResponse({ task: { metrics: { ai_completed: completed } } }))
    vi.stubGlobal('fetch', fetchMock)
    const { result, unmount } = renderHook(() => useDashboard('workbench'))
    await act(async () => {})
    expect(result.current.workbench.task?.metrics?.ai_completed).toBe(1)
    completed = 2
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(result.current.workbench.task?.metrics?.ai_completed).toBe(2)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    unmount()
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('releases a timed out poll so later progress can be read', async () => {
    vi.useFakeTimers()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const fetchMock = vi.fn().mockImplementationOnce((_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    })).mockImplementation(() => Promise.resolve(jsonResponse({ task: { metrics: { ai_completed: 3 } } })))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useDashboard('workbench'))
    await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true)
    expect(result.current.workbench.task?.metrics?.ai_completed).toBe(3)
    expect(result.current.error).toBe('')
    expect(result.current.refreshing).toBe(false)
  })

  it('aborts and ignores a poll left over from a previous scope', async () => {
    vi.useFakeTimers()
    let finishOld!: (value: Response) => void
    const fetchMock = vi.fn().mockImplementationOnce(() => new Promise<Response>(resolve => { finishOld = resolve }))
      .mockResolvedValueOnce(jsonResponse([]))
    vi.stubGlobal('fetch', fetchMock)
    const { result, rerender } = renderHook(({ scope }: { scope: 'workbench' | 'monitor' }) => useDashboard(scope), { initialProps: { scope: 'workbench' as 'workbench' | 'monitor' } })
    rerender({ scope: 'monitor' })
    await act(async () => {})
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true)
    await act(async () => { finishOld(jsonResponse({ task: { metrics: { ai_completed: 99 } } })) })
    expect(result.current.workbench.task).toBeNull()
    expect(result.current.history).toEqual([])
  })
})

describe('platform login states', () => {
  const oldLogin = {
    boss: { status: 'logged_out', checked_at: '2026-11-19T08:00:00', detail: '登录过期了' },
    zhilian: { status: 'logged_out', checked_at: '2026-11-19T08:00:00', detail: '登录过期了' },
    '51job': { status: 'unknown' },
    liepin: { status: 'unknown' },
  }
  const diagnosticsAllReady = {
    ok: true,
    chrome: true,
    boss_tab: { targetId: '1', url: 'https://www.zhipin.com/web/geek/job' },
    zhilian_tab: { targetId: '2', url: 'https://www.zhaopin.com/' },
    zhilian_page: { status: 'ready', message: '已发现智联搜索框，未发现登录墙' },
    job51_tab: { targetId: '3', url: 'https://we.51job.com/pc/search' },
    job51_page: { status: 'ready', message: '已发现前程无忧搜索框，未发现登录墙' },
    liepin_tab: { targetId: '4', url: 'https://www.liepin.com/zhaopin/' },
    liepin_page: { status: 'ready', message: '已发现猎聘搜索框，未发现登录墙' },
  }

  it('refreshPlatformLogin only refreshes login states, not the whole page', async () => {
    let workbenchPayload: Record<string, unknown> = {
      pending_confirmation: [], pending_greetings: [], send_errors: [], needs_resume: [],
      platform_login: oldLogin,
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/browser/diagnostics' && init?.method === 'POST') return jsonResponse(diagnosticsAllReady)
      if (url === '/api/workbench') return jsonResponse(workbenchPayload)
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
    const { result, unmount } = renderHook(() => useDashboard('workbench'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.workbench.platform_login?.boss?.status).toBe('logged_out')

    let data: unknown
    await act(async () => { data = await result.current.refreshPlatformLogin() })
    expect(result.current.workbench.platform_login?.boss?.status).toBe('logged_in')
    expect(result.current.workbench.platform_login?.zhilian?.status).toBe('logged_in')
    expect(result.current.platformLoginRefreshedAt).toBeInstanceOf(Date)
    expect(result.current.platformLoginRefreshing).toBe(false)
    expect(result.current.platformLoginRefreshError).toBe('')
    expect(data).toEqual(diagnosticsAllReady)
    // 只调了一次诊断接口，没碰整页 refresh 的历史接口，也没重读 workbench。
    expect(fetchMock.mock.calls.filter(([url, init]) =>
      String(url) === '/api/browser/diagnostics' && (init as RequestInit)?.method === 'POST')).toHaveLength(1)
    expect(fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/api/history'))).toHaveLength(0)
    // 紧接着的轮询回来的旧库值盖不回新值。
    await act(async () => { await result.current.refresh() })
    expect(result.current.workbench.platform_login?.boss?.status).toBe('logged_in')
    // 库里出现更新的掉线行时，新的库值才接管。
    workbenchPayload = {
      ...workbenchPayload,
      platform_login: { ...oldLogin, boss: { status: 'logged_out', checked_at: new Date().toISOString(), detail: '又掉了' } },
    }
    await act(async () => { await result.current.refresh() })
    expect(result.current.workbench.platform_login?.boss?.status).toBe('logged_out')
    expect(result.current.workbench.platform_login?.boss?.detail).toBe('又掉了')
    unmount()
  })

  it('refreshPlatformLogin keeps old values and explains when the browser is down', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/browser/diagnostics' && init?.method === 'POST') {
        return jsonResponse({ ok: false, chrome: false, error_kind: 'connection_failed', message: '浏览器已经装好了，但是现在连不上。' })
      }
      if (url === '/api/workbench') return jsonResponse({ platform_login: oldLogin })
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
    const { result, unmount } = renderHook(() => useDashboard('workbench'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    await act(async () => { await result.current.refreshPlatformLogin() })
    expect(result.current.workbench.platform_login?.boss?.status).toBe('logged_out')
    expect(result.current.platformLoginRefreshedAt).toBeNull()
    expect(result.current.platformLoginRefreshError).toContain('浏览器没连上')
    unmount()
  })

  it('refreshPlatformLogin reports a plain failure when the request itself fails', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/browser/diagnostics') throw new TypeError('Failed to fetch')
      if (url === '/api/workbench') return jsonResponse({ platform_login: oldLogin })
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
    const { result, unmount } = renderHook(() => useDashboard('workbench'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    await act(async () => { await result.current.refreshPlatformLogin() })
    expect(result.current.workbench.platform_login?.boss?.status).toBe('logged_out')
    expect(result.current.platformLoginRefreshedAt).toBeNull()
    expect(result.current.platformLoginRefreshError).toContain('没刷出来')
    expect(result.current.platformLoginRefreshing).toBe(false)
    unmount()
  })

  it('applyPlatformLoginStates only touches the login slice', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({
      pending_confirmation: [], pending_greetings: [], send_errors: [], needs_resume: [],
      platform_login: oldLogin,
    })))
    vi.stubGlobal('fetch', fetchMock)
    const { result, unmount } = renderHook(() => useDashboard('workbench'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    act(() => {
      result.current.applyPlatformLoginStates({
        boss: { status: 'logged_in', checked_at: new Date().toISOString(), detail: '新' },
      })
    })
    expect(result.current.workbench.platform_login?.boss?.status).toBe('logged_in')
    expect(result.current.workbench.platform_login?.zhilian?.status).toBe('logged_out')
    expect(result.current.workbench.pending_greetings).toEqual([])
    unmount()
  })
})

describe('dashboard does not stall on a stalled response body', () => {
  it('clears the skeleton when res.json() never resolves', async () => {
    vi.useFakeTimers()
    const workbench = { pending_confirmation: [], pending_greetings: [], send_errors: [], needs_resume: [] }
    const hangingResponse = () => {
      const res = new Response(JSON.stringify(workbench))
      vi.spyOn(res, 'json').mockImplementation(() => new Promise(() => {}))
      return res
    }
    const fetchMock = vi.fn(async () => hangingResponse())
    vi.stubGlobal('fetch', fetchMock)
    const { result, unmount } = renderHook(() => useDashboard('workbench'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(12000)
    })
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBeTruthy()
    unmount()
  })
})
