import { afterEach, describe, expect, it, vi } from 'vitest'
import { installCloseConfirm, isCloseConfirmSkipped, skipCloseConfirm } from './closeConfirm'

describe('closeConfirm', () => {
  afterEach(() => {
    delete window.__jlSkipCloseConfirm
    vi.restoreAllMocks()
  })

  it('beforeunload 时没置位就拦住关闭（弹浏览器通用确认框）', () => {
    const uninstall = installCloseConfirm()
    try {
      const event = new Event('beforeunload', { cancelable: true }) as BeforeUnloadEvent
      const preventDefault = vi.spyOn(event, 'preventDefault')
      window.dispatchEvent(event)
      expect(preventDefault).toHaveBeenCalled()
      // jsdom 把 returnValue 按布尔值存：赋 '' 后读出来是 false（真浏
      // 览器里读出来是 ''，关键是“被赋值过”，Chrome 才弹通用确认框）。
      expect(event.returnValue).toBe(false)
    } finally {
      uninstall()
    }
  })

  it('代码关窗置位后不拦 beforeunload', () => {
    skipCloseConfirm()
    expect(isCloseConfirmSkipped()).toBe(true)
    const uninstall = installCloseConfirm()
    try {
      const event = new Event('beforeunload', { cancelable: true }) as BeforeUnloadEvent
      const preventDefault = vi.spyOn(event, 'preventDefault')
      window.dispatchEvent(event)
      expect(preventDefault).not.toHaveBeenCalled()
    } finally {
      uninstall()
    }
  })

  it('取消注册后不再拦截', () => {
    const uninstall = installCloseConfirm()
    uninstall()
    const event = new Event('beforeunload', { cancelable: true }) as BeforeUnloadEvent
    const preventDefault = vi.spyOn(event, 'preventDefault')
    window.dispatchEvent(event)
    expect(preventDefault).not.toHaveBeenCalled()
  })
})
