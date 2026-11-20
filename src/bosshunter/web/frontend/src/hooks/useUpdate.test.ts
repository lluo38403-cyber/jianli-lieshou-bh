import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { classifyUpdateError, formatUpdateNotes, getUpdateErrorCopy, useUpdate } from './useUpdate'

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
