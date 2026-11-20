import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useConfig } from './useConfig'

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

describe('useConfig loading watchdog', () => {
  it('does not leave the skeleton up forever when requests never settle', async () => {
    vi.useFakeTimers()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const { result, unmount } = renderHook(() => useConfig())
    expect(result.current.loading).toBe(true)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(21000)
    })
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toMatch(/重试/)
    unmount()
  })
})
