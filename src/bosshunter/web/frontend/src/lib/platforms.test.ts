import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openPlatformLogin } from './platforms'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('openPlatformLogin', () => {
  // 直接写 ReturnType<typeof vi.spyOn> 会丢掉 window.open 的重载签名，
  // tsc 报错；用辅助函数拿到 spy 的精确类型。
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
    fetchMock.mockResolvedValue(jsonResponse({ ok: true, target_id: 'target-1' }))
    await expect(openPlatformLogin('boss')).resolves.toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/browser/open')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ url: 'https://www.zhipin.com/web/user/login' })
    expect(openSpy).not.toHaveBeenCalled()
  })

  it('falls back to window.open when the backend cannot be reached', async () => {
    fetchMock.mockRejectedValue(new Error('network down'))
    await expect(openPlatformLogin('zhilian')).resolves.toBe(false)
    expect(openSpy).toHaveBeenCalledWith('https://www.zhaopin.com/', '_blank', 'noopener,noreferrer')
  })

  it('falls back to window.open when the backend says the browser is not connected', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: false, error: '浏览器还没连上' }))
    await expect(openPlatformLogin('liepin')).resolves.toBe(false)
    expect(openSpy).toHaveBeenCalledWith('https://www.liepin.com/', '_blank', 'noopener,noreferrer')
  })

  it('falls back to window.open when the backend returns a non-200 status', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: false }, 503))
    await expect(openPlatformLogin('51job')).resolves.toBe(false)
    expect(openSpy).toHaveBeenCalledWith('https://www.51job.com/', '_blank', 'noopener,noreferrer')
  })
})
