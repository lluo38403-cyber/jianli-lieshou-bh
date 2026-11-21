import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { classifyUpdateError, formatUpdateNotes, getUpdateErrorCopy, BACKEND_CHECK_TIMEOUT_MS, UPDATE_AUTO_RECHECK_INTERVAL_MS, UPDATE_CHECK_JSON_TIMEOUT_MS, UPDATE_CHECK_RETRIES, UPDATE_CHECK_TIMEOUT_MS, UpdateProvider, useUpdate, useUpdateContext } from './useUpdate'

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
      if (url === '/api/auth/config') return jsonResponse({})
      if (url === '/api/update/status') return jsonResponse({ status: 'idle', progress: 0 })
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
    vi.useRealTimers()
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
    // 读请求走统一重试封装，fetch 会多带一个带 signal 的 init。
    expect(fetchMock).toHaveBeenCalledWith('/api/update/check', expect.objectContaining({ signal: expect.anything() }))
    expect(fetchMock).toHaveBeenCalledWith('/api/health', expect.objectContaining({ signal: expect.anything() }))
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

  it('apply in standalone mode closes the window directly (v34: no close confirm to skip)', async () => {
    // v34：前端不再注册 beforeunload，更新换版的代码关窗直接
    // window.close()，不需要再绕过确认；换版流程本身由后端
    // updater._UPDATE_APPLYING 守卫，不会被关窗监视线程抢跑。
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.2' })
      if (url === '/api/update/status') return jsonResponse({ status: 'ready', progress: 100 })
      if (url === '/api/update/apply') return jsonResponse({ applying: true })
      return jsonResponse({})
    })
    const closeMock = vi.fn()
    Object.defineProperty(window, 'close', { value: closeMock, configurable: true })
    const matchMediaMock = vi.fn(() => ({
      matches: true,
      media: '(display-mode: standalone)',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
    Object.defineProperty(window, 'matchMedia', { value: matchMediaMock, configurable: true })
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.info?.version).toBe('1.0.2'))
    await act(async () => {
      await result.current.start()
    })
    await waitFor(() => expect(result.current.phase).toBe('ready'), { timeout: 5000 })
    await act(async () => {
      await result.current.apply()
    })
    expect(closeMock).toHaveBeenCalled()
  })
})

describe('classifyUpdateError', () => {
  it('按后端原话把失败分到下载 / 校验 / 替换三步', () => {
    expect(classifyUpdateError('新版本没下载下来，请检查网络后重试。')).toBe('download')
    expect(classifyUpdateError('下载出问题了，稍后再试。')).toBe('download')
    expect(classifyUpdateError('服务器上没有找到新版本，可能刚撤下来了。')).toBe('download')
    expect(classifyUpdateError('下载的文件对不上，怕文件坏了，这次先不换。')).toBe('verify')
    expect(classifyUpdateError('安装包里的文件路径不对，这次先不换。')).toBe('verify')
    expect(classifyUpdateError('一键更新只在正式安装的版本里能用。')).toBe('apply')
    expect(classifyUpdateError('找不到现在用的程序文件，这次先不换。')).toBe('apply')
    expect(classifyUpdateError('新版本应该换好了，但面板没重新打开，关掉再双击打开一次就行。')).toBe('apply')
    expect(classifyUpdateError('这次没换上，稍后再试。')).toBe('apply')
    expect(classifyUpdateError('没开始下载，稍后再试。')).toBe('download')
  })

  it('匹配不到时按阶段兜底，都没有才是 other', () => {
    expect(classifyUpdateError('不知道出了什么问题', 'verifying')).toBe('verify')
    expect(classifyUpdateError('不知道出了什么问题', 'applying')).toBe('apply')
    expect(classifyUpdateError('不知道出了什么问题', 'downloading')).toBe('download')
    expect(classifyUpdateError('不知道出了什么问题')).toBe('other')
    expect(classifyUpdateError('')).toBe('other')
  })

  it('三种文案都说清哪步失败、旧版本能不能用', () => {
    expect(getUpdateErrorCopy('download').title).toBe('新版本没下载下来')
    expect(getUpdateErrorCopy('download').reason).toContain('旧版本还能正常用')
    expect(getUpdateErrorCopy('verify').title).toBe('下载的文件对不上')
    expect(getUpdateErrorCopy('verify').reason).toContain('旧版本还能正常用')
    expect(getUpdateErrorCopy('apply').title).toBe('这次没换上')
    expect(getUpdateErrorCopy('apply').reason).toContain('设置和投递记录都没动')
  })
})

describe('useUpdate failure recovery', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  let originalLocation: Location | undefined

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      if (url === '/api/auth/config') return jsonResponse({})
      if (url === '/api/update/status') return jsonResponse({ status: 'ready', progress: 100 })
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
    if (originalLocation === undefined) originalLocation = window.location
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { href: originalLocation.href, reload: vi.fn() },
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    if (originalLocation !== undefined) {
      Object.defineProperty(window, 'location', { configurable: true, value: originalLocation })
    }
    vi.restoreAllMocks()
    cleanup()
  })

  it('启动时从展示配置里拿到手动下载的店铺链接', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/auth/config') return jsonResponse({ buy_url: 'https://example.com/shop' })
      if (url.startsWith('/api/update/check')) return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      return jsonResponse({})
    })
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.manualDownloadUrl).toBe('https://example.com/shop'))
  })

  it('拿不到店铺链接时手动下载地址为空，不写死链接', async () => {
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.info?.version).toBe('1.0.2'))
    expect(result.current.manualDownloadUrl).toBe('')
  })

  it('下载开始失败后分到 download，重试回到可再试的状态', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      if (url === '/api/auth/config') return jsonResponse({})
      if (url === '/api/update/start') return jsonResponse({ error: '新版本没下载下来，请检查网络后重试。' }, false)
      return jsonResponse({})
    })
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.info?.version).toBe('1.0.2'))
    await act(async () => {
      await result.current.start()
    })
    expect(result.current.phase).toBe('error')
    expect(result.current.errorKind).toBe('download')
    expect(result.current.error).toContain('没下载下来')
    // 重试：回到 idle 再查一次，不卡在 error。
    await act(async () => {
      await result.current.retry()
    })
    expect(result.current.phase).toBe('idle')
    expect(result.current.error).toBe('')
    expect(result.current.errorKind).toBeNull()
    expect(result.current.info?.version).toBe('1.0.2')
  })

  it('校验失败（文件对不上）分到 verify', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      if (url === '/api/auth/config') return jsonResponse({})
      if (url === '/api/update/start') return jsonResponse({ status: 'downloading', progress: 0 })
      if (url === '/api/update/status') return jsonResponse({ status: 'error', error: '下载的文件对不上，怕文件坏了，这次先不换。' })
      return jsonResponse({})
    })
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.info?.version).toBe('1.0.2'))
    await act(async () => {
      await result.current.start()
    })
    await waitFor(() => expect(result.current.phase).toBe('error'), { timeout: 5000 })
    expect(result.current.errorKind).toBe('verify')
    expect(result.current.error).toContain('对不上')
  })

  it('替换失败（找不到程序文件）分到 apply', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.2' })
      if (url === '/api/auth/config') return jsonResponse({})
      if (url === '/api/update/status') return jsonResponse({ status: 'ready', progress: 100 })
      if (url === '/api/update/start') return jsonResponse({ status: 'downloading', progress: 0 })
      if (url === '/api/update/apply') return jsonResponse({ error: '找不到现在用的程序文件，这次先不换。' }, false)
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
    expect(result.current.phase).toBe('error')
    expect(result.current.errorKind).toBe('apply')
    expect(result.current.error).toContain('找不到现在用的程序文件')
  })

  it('下载中本地服务断了：轮询连失败 10 次就定稿为下载失败，不一直卡在下载中', async () => {
    vi.useFakeTimers()
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      if (url === '/api/auth/config') return jsonResponse({})
      if (url === '/api/update/start') return jsonResponse({ status: 'downloading', progress: 0 })
      if (url === '/api/update/status') throw new Error('down')
      return jsonResponse({})
    })
    const { result } = renderHook(() => useUpdate())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.info?.version).toBe('1.0.2')
    await act(async () => {
      await result.current.start()
    })
    expect(result.current.phase).toBe('downloading')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000)
    })
    expect(result.current.phase).toBe('error')
    expect(result.current.errorKind).toBe('download')
    expect(result.current.error).toContain('断开')
  })
})

describe('formatUpdateNotes', () => {
  it('把 v19 那句内部说明换成默认文案', () => {
    // 回归：v19 上线时中继存的说明是“v19 1.1.5 只升版号，验证 v18
    // 的 update.bat 修复”，直接显示在首页卡片和弹窗里，用户看不懂。
    expect(formatUpdateNotes('v19 1.1.5 只升版号，验证 v18 的 update.bat 修复')).toBe(
      '修了一些小问题，用起来更稳。',
    )
  })

  it('剥掉开头的版本号，正常说明原样通过', () => {
    expect(formatUpdateNotes('1.1.5 修复了面板更新时换文件失败的问题')).toBe(
      '修复了面板更新时换文件失败的问题',
    )
    expect(formatUpdateNotes('v1.1.5 界面改成独立窗口打开')).toBe('界面改成独立窗口打开')
    expect(formatUpdateNotes('修复了面板更新时换文件失败的问题，更新过程更稳了。')).toBe(
      '修复了面板更新时换文件失败的问题，更新过程更稳了。',
    )
  })

  it('空说明和只有版本号的说明都给默认文案', () => {
    expect(formatUpdateNotes('')).toBe('修了一些小问题，用起来更稳。')
    expect(formatUpdateNotes(null)).toBe('修了一些小问题，用起来更稳。')
    expect(formatUpdateNotes(undefined)).toBe('修了一些小问题，用起来更稳。')
    expect(formatUpdateNotes('v1.1.5')).toBe('修了一些小问题，用起来更稳。')
  })

  it('含内部口径的说明都换成默认文案，不露测试术语', () => {
    expect(formatUpdateNotes('验证 update.bat 修复后发布')).toBe('修了一些小问题，用起来更稳。')
    expect(formatUpdateNotes('CI 全过，SMOKE_OK')).toBe('修了一些小问题，用起来更稳。')
    expect(formatUpdateNotes('update-flow 全流程测试通过')).toBe('修了一些小问题，用起来更稳。')
  })

  it('不误剥正常说明开头的数字', () => {
    expect(formatUpdateNotes('3 个已知问题已修复')).toBe('3 个已知问题已修复')
  })

  it('超长说明截断到 120 字以内并加省略号', () => {
    const long = `修${'了'.repeat(150)}`
    const formatted = formatUpdateNotes(long)
    expect(formatted.length).toBeLessThanOrEqual(120)
    expect(formatted.endsWith('…')).toBe(true)
  })
})

describe('UpdateProvider keeps one download state across pages', () => {
  function Probe({ label }: { label: string }) {
    const update = useUpdateContext()
    return (
      <div>
        <span data-testid={`${label}-phase`}>{update.phase}</span>
        <span data-testid={`${label}-progress`}>{update.progress}</span>
        <button data-testid={`${label}-start`} onClick={() => void update.start()}>开始下载</button>
      </div>
    )
  }

  let fetchMock: ReturnType<typeof vi.fn>
  let downloading = false

  beforeEach(() => {
    downloading = false
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      if (url === '/api/auth/config') return jsonResponse({})
      if (url === '/api/update/start' && init?.method === 'POST') {
        downloading = true
        return jsonResponse({})
      }
      if (url === '/api/update/status') {
        return jsonResponse(
          downloading
            ? { status: 'downloading', progress: 25, version: '1.0.2', size: 66_549_810, sha256: 'a'.repeat(64), notes: '更快更稳', current_version: '1.0.1' }
            : { status: 'idle', progress: 0 },
        )
      }
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    vi.restoreAllMocks()
    cleanup()
  })

  const statusCallCount = () => fetchMock.mock.calls.filter(([input]) => String(input) === '/api/update/status').length

  it('shares one download between pages and keeps polling after navigation', async () => {
    const { rerender } = render(
      <UpdateProvider>
        <Probe label="a" />
        <Probe label="b" />
      </UpdateProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('a-phase').textContent).toBe('idle'), { timeout: 5000 })
    // 首页点开始下载：另一个页面实例立刻看到同一份下载中状态。
    fireEvent.click(screen.getByTestId('a-start'))
    await waitFor(() => expect(screen.getByTestId('b-phase').textContent).toBe('downloading'), { timeout: 5000 })
    await waitFor(() => expect(screen.getByTestId('b-progress').textContent).toBe('25'), { timeout: 5000 })
    // 切到“我的设置”：页面组件卸载、Provider 还在，轮询不断。
    const callsWhileOnHome = statusCallCount()
    rerender(
      <UpdateProvider>
        <div>设置页</div>
      </UpdateProvider>,
    )
    await waitFor(() => expect(statusCallCount()).toBeGreaterThan(callsWhileOnHome), { timeout: 5000 })
    // 切回首页：还是下载中 25%，不是全新的“马上更新”。
    rerender(
      <UpdateProvider>
        <Probe label="a" />
      </UpdateProvider>,
    )
    expect(screen.getByTestId('a-phase').textContent).toBe('downloading')
    expect(screen.getByTestId('a-progress').textContent).toBe('25')
  })

  it('resumes a download already running on the server after a fresh mount', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      if (url === '/api/auth/config') return jsonResponse({})
      if (url === '/api/update/status') {
        return jsonResponse({ status: 'downloading', progress: 40, version: '1.0.2', size: 66_549_810, sha256: 'a'.repeat(64), notes: '更快更稳', current_version: '1.0.1' })
      }
      return jsonResponse({})
    })
    render(
      <UpdateProvider>
        <Probe label="a" />
      </UpdateProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('a-phase').textContent).toBe('downloading'), { timeout: 5000 })
    expect(screen.getByTestId('a-progress').textContent).toBe('40')
    const calls = statusCallCount()
    await waitFor(() => expect(statusCallCount()).toBeGreaterThan(calls), { timeout: 5000 })
  })
})

describe('useUpdate check race / timeout / failure (v29)', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  let originalLocation: Location | undefined

  /** 响应体迟迟不回来的 Response：json() 返回一个能手动放行的 promise。 */
  function pendingJsonResponse<T>(body: T) {
    let resolveJson!: (value: T) => void
    const jsonPromise = new Promise<T>((resolve) => {
      resolveJson = resolve
    })
    const res = { status: 200, json: () => jsonPromise } as unknown as Response
    return { res, resolveJson: () => resolveJson(body) }
  }

  const LATEST = { update_available: false, current_version: '1.0.1', status: 'idle' }
  const CHECK_FAILED = {
    status: 'error',
    reason_code: 'check_failed',
    error: '查新版本超时了，网络可能比较慢，稍后再点一次。',
    update_available: false,
    current_version: '1.0.1',
  }

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      if (url === '/api/auth/config') return jsonResponse({})
      if (url === '/api/update/status') return jsonResponse({ status: 'idle', progress: 0 })
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
    if (originalLocation === undefined) originalLocation = window.location
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { href: originalLocation.href, reload: vi.fn() },
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    if (originalLocation !== undefined) {
      Object.defineProperty(window, 'location', { configurable: true, value: originalLocation })
    }
    vi.restoreAllMocks()
    cleanup()
  })

  it('过期的自动检查响应清不掉手动检查置上的新版，转圈等两路都完才停', async () => {
    // 回归：录屏终态——手动先查到 1.1.14，自动检查随后回“无新版”，
    // 旧实现把 info 置 null，弹窗与横幅瞬间消失且无任何文字残留。
    let autoPending: ReturnType<typeof pendingJsonResponse> | null = null
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/update/check') {
        autoPending = pendingJsonResponse(LATEST)
        return autoPending.res
      }
      if (url === '/api/update/check?force=1') return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      return jsonResponse({})
    })
    const { result } = renderHook(() => useUpdate())
    // 挂载的自动检查卡在读响应体上，还没回来。
    await waitFor(() => expect(result.current.checking).toBe(true))
    // 手动检查后启动、先回来，置上新版。
    let found: boolean | null | undefined
    await act(async () => {
      found = await result.current.check(true)
    })
    expect(found).toBe(true)
    expect(result.current.info?.version).toBe('1.0.2')
    // 手动那路完了，自动那路还在等：转圈不能停。
    expect(result.current.checking).toBe(true)
    // 自动检查的过期响应现在才回来：说“已是最新”，不许清 info。
    expect(autoPending).not.toBeNull()
    await act(async () => {
      autoPending!.resolveJson()
    })
    await waitFor(() => expect(result.current.checking).toBe(false))
    expect(result.current.info?.version).toBe('1.0.2')
    expect(result.current.phase).toBe('idle')
  })

  it('过期的检查直接采用最新一次检查的结果，不自己下结论', async () => {
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.info?.version).toBe('1.0.2'))
    let autoPending: ReturnType<typeof pendingJsonResponse> | null = null
    let manualPending: ReturnType<typeof pendingJsonResponse> | null = null
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/update/check') {
        autoPending = pendingJsonResponse(LATEST)
        return autoPending.res
      }
      if (url === '/api/update/check?force=1') {
        manualPending = pendingJsonResponse(NEW_VERSION)
        return manualPending.res
      }
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      return jsonResponse({})
    })
    let pAuto: Promise<boolean | null> | undefined
    let pManual: Promise<boolean | null> | undefined
    let autoValue: boolean | null | undefined
    let autoDone = false
    act(() => {
      pAuto = result.current.check(false)
      pManual = result.current.check(true)
    })
    void pAuto!.then((value) => {
      autoDone = true
      autoValue = value
    })
    expect(autoPending).not.toBeNull()
    expect(manualPending).not.toBeNull()
    // 自动检查的过期响应先回来：它已经过时了，不许自己下结论，
    // 必须等手动检查的结果。
    await act(async () => {
      autoPending!.resolveJson()
      await new Promise((resolve) => setTimeout(resolve, 50))
    })
    expect(autoDone).toBe(false)
    // 手动检查回来：两路都拿到同一个“有新版”。
    await act(async () => {
      manualPending!.resolveJson()
    })
    await expect(pManual!).resolves.toBe(true)
    await expect(pAuto!).resolves.toBe(true)
    expect(autoValue).toBe(true)
    expect(result.current.info?.version).toBe('1.0.2')
  })

  it('同一路检查连点两次只发一次请求，两次拿同一个结果', async () => {
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.info?.version).toBe('1.0.2'))
    const countForceChecks = () =>
      fetchMock.mock.calls.filter(([input]) => String(input) === '/api/update/check?force=1').length
    const before = countForceChecks()
    let p1: Promise<boolean | null> | undefined
    let p2: Promise<boolean | null> | undefined
    act(() => {
      p1 = result.current.check(true)
      p2 = result.current.check(true)
    })
    // async 函数会把 return 的 promise 再包一层，对象恒等断言不成立：
    // 用“两次拿同一个结果 + 只发一次请求”来断言去重。
    await act(async () => {
      expect(await p1!).toBe(true)
      expect(await p2!).toBe(true)
    })
    expect(countForceChecks() - before).toBe(1)
  })

  it('前端单次检查超时 12 秒，大于后端 10 秒；重试 1 次，最坏约 25 秒', () => {
    // 后端超时见 updater.py CHECK_TIMEOUT = 10.0；前端 8 秒会把
    // 8–10 秒的正常慢响应每次都在后端返回前掐断。
    expect(BACKEND_CHECK_TIMEOUT_MS).toBe(10000)
    expect(UPDATE_CHECK_TIMEOUT_MS).toBe(12000)
    expect(UPDATE_CHECK_TIMEOUT_MS).toBeGreaterThan(BACKEND_CHECK_TIMEOUT_MS)
    expect(UPDATE_CHECK_RETRIES).toBe(1)
    expect((UPDATE_CHECK_RETRIES + 1) * UPDATE_CHECK_TIMEOUT_MS).toBeLessThanOrEqual(25000)
    expect(UPDATE_CHECK_JSON_TIMEOUT_MS).toBe(5000)
  })

  it('手动检查没查到进 error 态、留“再试一次”的话；自动检查失败静默', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) throw new Error('offline')
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      return jsonResponse({})
    })
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.currentVersion).toBe('1.0.1'))
    // 挂载的自动检查失败了：静默，不弹、不改 phase。
    expect(result.current.phase).toBe('idle')
    expect(result.current.info).toBeNull()
    let found: boolean | null | undefined
    await act(async () => {
      found = await result.current.check(true)
    })
    expect(found).toBeNull()
    expect(result.current.phase).toBe('error')
    expect(result.current.error).toContain('没查到')
    expect(result.current.error).toContain('再试一次')
  })

  it('后端报 check_failed 时手动检查进 error 态，自动检查不弹', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(CHECK_FAILED)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      return jsonResponse({})
    })
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.currentVersion).toBe('1.0.1'))
    // 自动检查拿到 check_failed：静默。
    expect(result.current.phase).toBe('idle')
    expect(result.current.info).toBeNull()
    let found: boolean | null | undefined
    await act(async () => {
      found = await result.current.check(true)
    })
    expect(found).toBeNull()
    expect(result.current.phase).toBe('error')
    expect(result.current.error).toContain('超时')
  })

  it('上次更新换失败的报错仍按老样子进 error 态，不被当成检查失败', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) {
        return jsonResponse({
          status: 'error',
          reason_code: 'copy',
          error: '换文件的时候出问题了，已经换回旧版本。请重新点一次更新。',
          version: '1.0.2',
          size: 66_549_810,
          sha256: 'a'.repeat(64),
          notes: '更快更稳',
          current_version: '1.0.1',
        })
      }
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      return jsonResponse({})
    })
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.phase).toBe('error'))
    expect(result.current.info?.version).toBe('1.0.2')
    expect(result.current.error).toContain('换回旧版本')
  })

  it('响应体一直不回来：读超时后手动检查进 error 态，已知的新版不被清掉', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/update/check') return jsonResponse(NEW_VERSION)
      if (url === '/api/update/check?force=1') {
        return { status: 200, json: () => new Promise(() => {}) } as unknown as Response
      }
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      return jsonResponse({})
    })
    const { result } = renderHook(() => useUpdate())
    await waitFor(() => expect(result.current.info?.version).toBe('1.0.2'))
    let found: boolean | null | undefined
    await act(async () => {
      found = await result.current.check(true)
    })
    expect(found).toBeNull()
    expect(result.current.phase).toBe('error')
    expect(result.current.error).toContain('没查到')
    // 读超时只是这次没查到，之前查到的新版还在。
    expect(result.current.info?.version).toBe('1.0.2')
  }, 20000)
})

describe('useUpdate auto re-check (v31)', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  let originalLocation: Location | undefined

  const LATEST = { update_available: false, current_version: '1.0.1', status: 'idle' }
  const CHECK_FAILED = {
    status: 'error',
    reason_code: 'check_failed',
    error: '查新版本超时了，网络可能比较慢，稍后再点一次。',
    update_available: false,
    current_version: '1.0.1',
  }

  /** 只数自动检查（不带 force=1 的 /api/update/check）：手动检查另算。 */
  function autoCheckCalls() {
    return fetchMock.mock.calls.filter(([input]) => String(input) === '/api/update/check').length
  }

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(NEW_VERSION)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      if (url === '/api/auth/config') return jsonResponse({})
      if (url === '/api/update/status') return jsonResponse({ status: 'idle', progress: 0 })
      return jsonResponse({})
    })
    vi.stubGlobal('fetch', fetchMock)
    if (originalLocation === undefined) originalLocation = window.location
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { href: originalLocation.href, reload: vi.fn() },
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    if (originalLocation !== undefined) {
      Object.defineProperty(window, 'location', { configurable: true, value: originalLocation })
    }
    vi.restoreAllMocks()
    cleanup()
  })

  it('面板一直开着：挂载时已是最新，30 分钟后自动复查发现新版，不用手动点', async () => {
    // 回归：lucky 云电脑 2026-11-21 事故——面板 03:47 开到 12:45，
    // 中转 05:31 发了 1.1.16，启动检查当时说“已是最新”是对的，但之后
    // 7 小时再没查过，横幅/弹窗一直不出现，手动点才出来。
    let checkCalls = 0
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/update/check') {
        checkCalls += 1
        // 第一次（启动检查）还是旧版，之后中转发了新版。
        return jsonResponse(checkCalls === 1 ? LATEST : NEW_VERSION)
      }
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      return jsonResponse({})
    })
    vi.useFakeTimers()
    const { result } = renderHook(() => useUpdate())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.currentVersion).toBe('1.0.1')
    expect(result.current.info).toBeNull()
    expect(autoCheckCalls()).toBe(1)

    // 没到 30 分钟不复查。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(UPDATE_AUTO_RECHECK_INTERVAL_MS - 60 * 1000)
    })
    expect(autoCheckCalls()).toBe(1)
    expect(result.current.info).toBeNull()

    // 到 30 分钟自动复查：没点过任何按钮，新版状态就出现了——
    // 横幅按 info 渲染、弹窗按 info 自动弹（那两处已有单测）。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60 * 1000)
    })
    expect(autoCheckCalls()).toBe(2)
    expect(result.current.info?.version).toBe('1.0.2')
    expect(result.current.phase).toBe('idle')
    expect(fetchMock).not.toHaveBeenCalledWith(
      '/api/update/check?force=1',
      expect.objectContaining({ signal: expect.anything() }),
    )
  })

  it('自动复查失败时静默：已有新版不被清掉，也不进 error 态', async () => {
    let checkCalls = 0
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/update/check') {
        checkCalls += 1
        if (checkCalls === 1) return jsonResponse(NEW_VERSION)
        throw new Error('offline')
      }
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      return jsonResponse({})
    })
    vi.useFakeTimers()
    const { result } = renderHook(() => useUpdate())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.info?.version).toBe('1.0.2')
    expect(result.current.phase).toBe('idle')

    // 复查两次都断网：自动分支静默，不弹、不清 info、不改 phase。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(UPDATE_AUTO_RECHECK_INTERVAL_MS)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(UPDATE_AUTO_RECHECK_INTERVAL_MS)
    })
    expect(result.current.info?.version).toBe('1.0.2')
    expect(result.current.phase).toBe('idle')
    expect(result.current.error).toBe('')
  })

  it('报错态不自动复查：等用户点“再试一次”，不自己把失败盖掉', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/update/check')) return jsonResponse(CHECK_FAILED)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      return jsonResponse({})
    })
    vi.useFakeTimers()
    const { result } = renderHook(() => useUpdate())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    // 启动自动检查拿 check_failed：静默，phase 还是 idle。
    expect(result.current.phase).toBe('idle')
    expect(autoCheckCalls()).toBe(1)
    // 手动检查同样没查到：进 error 态，等用户处理。
    await act(async () => {
      await result.current.check(true)
    })
    expect(result.current.phase).toBe('error')
    expect(fetchMock.mock.calls.filter(([input]) => String(input) === '/api/update/check?force=1').length).toBe(1)

    // 30 分钟后也不自动复查：报错态只认手动重试。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(UPDATE_AUTO_RECHECK_INTERVAL_MS)
    })
    expect(autoCheckCalls()).toBe(1)
  })

  it('卸载后不再复查', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/update/check') return jsonResponse(LATEST)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.0.1' })
      return jsonResponse({})
    })
    vi.useFakeTimers()
    const { result, unmount } = renderHook(() => useUpdate())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.info).toBeNull()
    expect(autoCheckCalls()).toBe(1)
    unmount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2 * UPDATE_AUTO_RECHECK_INTERVAL_MS)
    })
    expect(autoCheckCalls()).toBe(1)
  })
})
