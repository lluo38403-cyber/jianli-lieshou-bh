import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openPlatformLogin } from './platforms'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('openPlatformLogin', () => {
  // v27 起不再回退到 window.open：回退会把登录页开在面板自己的浏览器里，
  // 登录态落在另一个配置目录，程序永远检测不到。失败就把原因交回给界面。
  const createOpenSpy = () => vi.spyOn(window, 'open').mockImplementation(() => null)
  let openSpy: ReturnType<typeof createOpenSpy>
  const fetchMock = vi.fn()

  beforeEach(() => {
    openSpy = createOpenSpy()
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('opens the login page through the runtime browser when the backend answers', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true, target_id: 'target-1', browser_started: true, reused_tab: false, message: '已在工作浏览器打开。' }))
    await expect(openPlatformLogin('boss')).resolves.toEqual({
      ok: true,
      browserStarted: true,
      reusedTab: false,
      message: '已在工作浏览器打开。',
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/browser/open')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ url: 'https://www.zhipin.com/web/user/login' })
    expect(openSpy).not.toHaveBeenCalled()
  })

  it('reports reusing an already-open tab instead of opening a new one', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true, target_id: 'target-1', browser_started: false, reused_tab: true, message: '已经打开了，直接切过去了。' }))
    await expect(openPlatformLogin('boss')).resolves.toEqual({
      ok: true,
      browserStarted: false,
      reusedTab: true,
      message: '已经打开了，直接切过去了。',
    })
    expect(openSpy).not.toHaveBeenCalled()
  })

  it('returns the failure reason and never falls back to window.open when the backend cannot be reached', async () => {
    fetchMock.mockRejectedValue(new Error('network down'))
    const result = await openPlatformLogin('zhilian')
    expect(result.ok).toBe(false)
    expect(result.message).toBeTruthy()
    expect(openSpy).not.toHaveBeenCalled()
  })

  it('returns the backend error and never falls back to window.open when the browser is not connected', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: false, error: '浏览器还没连上' }))
    await expect(openPlatformLogin('liepin')).resolves.toEqual({ ok: false, message: '浏览器还没连上' })
    expect(openSpy).not.toHaveBeenCalled()
  })

  it('returns a failure and never falls back to window.open on a non-200 status', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: false }, 503))
    const result = await openPlatformLogin('51job')
    expect(result.ok).toBe(false)
    expect(openSpy).not.toHaveBeenCalled()
  })
})
