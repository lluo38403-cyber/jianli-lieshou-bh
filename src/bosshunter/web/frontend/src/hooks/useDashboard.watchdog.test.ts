import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useDashboard } from './useDashboard'

/** fetchWithRetry 本身永远不返回：骨架屏只能靠 20 秒兜底撤掉。 */
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, fetchWithRetry: () => new Promise<Response>(() => {}) }
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('useDashboard loading watchdog', () => {
  it('does not leave the skeleton up forever when requests never settle', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})))
    const { result, unmount } = renderHook(() => useDashboard('workbench'))
    expect(result.current.loading).toBe(true)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(21000)
    })
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toMatch(/刷新一下/)
    unmount()
  })
})
