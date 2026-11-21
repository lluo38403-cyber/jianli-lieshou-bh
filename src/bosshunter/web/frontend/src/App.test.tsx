import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import App from './App'

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('App close behavior (v34)', () => {
  it('不注册 beforeunload：点 X 直接关窗，不弹任何确认框', async () => {
    // v34：彻底去掉 X 的原生离开确认框。Chrome --app 窗口的 X 只能
    // 走 beforeunload，而 beforeunload 只有“弹浏览器通用框”和
    // “不弹直接关”两个选项、放不进自定义弹窗；所以前端不再注册
    // beforeunload，关闭清理全部走后端关窗监视线程
    // （watch_app_window_close → _perform_exit_cleanup）。
    // 钉住这条：以后谁再把 beforeunload 装回来，这个测试会失败。
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.1.19' })
        return jsonResponse({})
      }),
    )
    // jsdom 没有 matchMedia：App 挂载时的 useTheme 会调它，先 stub
    // 一个（与 useUpdate.test.tsx 里的 stub 同款）。
    Object.defineProperty(window, 'matchMedia', {
      value: vi.fn(() => ({
        matches: false,
        media: '(prefers-color-scheme: dark)',
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
      configurable: true,
    })
    render(<App />)
    // 等挂载时的 effect 都跑完，再发 beforeunload。
    await vi.waitFor(() => expect(document.body.textContent ?? '').not.toBe(''))
    const event = new Event('beforeunload', { cancelable: true }) as BeforeUnloadEvent
    const preventDefault = vi.spyOn(event, 'preventDefault')
    window.dispatchEvent(event)
    expect(preventDefault).not.toHaveBeenCalled()
    expect(event.defaultPrevented).toBe(false)
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })
})
