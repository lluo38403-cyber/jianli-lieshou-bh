import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useConfig } from './useConfig'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const INITIAL = {
  profile: { salary_min: 4, salary_max: 7, resume_path: '' },
  scoring: { threshold: 60 },
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function stubFetch(saved: { current: Record<string, any> }, postOk = true) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/config' && init?.method === 'POST') {
        if (postOk) {
          saved.current = JSON.parse(String(init.body))
          return jsonResponse({ success: true })
        }
        return jsonResponse({ success: false, error: '保存失败' })
      }
      if (url === '/api/config') return jsonResponse(saved.current)
      if (url === '/api/config/schema') return jsonResponse({})
      return jsonResponse({})
    }),
  )
}

async function renderLoaded(saved: { current: Record<string, any> }, postOk = true) {
  stubFetch(saved, postOk)
  const hook = renderHook(() => useConfig())
  await waitFor(() => expect(hook.result.current.config).toEqual(INITIAL))
  expect(hook.result.current.dirty).toBe(false)
  return hook
}

describe('useConfig 脏判定（快照深比较）', () => {
  it('改了值是脏，改回原值就不脏', async () => {
    const saved = { current: JSON.parse(JSON.stringify(INITIAL)) }
    const { result } = await renderLoaded(saved)
    act(() => {
      result.current.updateConfig('profile.salary_min', 5)
    })
    expect(result.current.dirty).toBe(true)
    act(() => {
      result.current.updateConfig('profile.salary_min', 4)
    })
    expect(result.current.dirty).toBe(false)
  })

  it('改了嵌套值又改回也不脏', async () => {
    const saved = { current: JSON.parse(JSON.stringify(INITIAL)) }
    const { result } = await renderLoaded(saved)
    act(() => {
      result.current.updateConfig('scoring.threshold', 80)
    })
    expect(result.current.dirty).toBe(true)
    act(() => {
      result.current.updateConfig('scoring.threshold', 60)
    })
    expect(result.current.dirty).toBe(false)
  })

  it('服务端已存的字段同步（简历上传）不算脏，也不影响改回判定', async () => {
    const saved = { current: JSON.parse(JSON.stringify(INITIAL)) }
    const { result } = await renderLoaded(saved)
    act(() => {
      result.current.updateConfig('profile.resume_path', '/data/resume.pdf', { markDirty: false })
    })
    expect(result.current.dirty).toBe(false)
    act(() => {
      result.current.updateConfig('profile.salary_min', 5)
    })
    expect(result.current.dirty).toBe(true)
    // 改回原值后不脏：说明快照里已经带上了同步过来的简历路径。
    act(() => {
      result.current.updateConfig('profile.salary_min', 4)
    })
    expect(result.current.dirty).toBe(false)
  })

  it('恢复原样后不脏，值回到服务端那份', async () => {
    const saved = { current: JSON.parse(JSON.stringify(INITIAL)) }
    const { result } = await renderLoaded(saved)
    act(() => {
      result.current.updateConfig('profile.salary_min', 5)
    })
    expect(result.current.dirty).toBe(true)
    act(() => {
      result.current.resetConfig()
    })
    await waitFor(() => expect(result.current.dirty).toBe(false))
    expect(result.current.config!.profile.salary_min).toBe(4)
  })
})

describe('useConfig saveConfig 返回值', () => {
  it('保存成功返回 true，脏 cleared，改动写到了服务端', async () => {
    const saved = { current: JSON.parse(JSON.stringify(INITIAL)) }
    const { result } = await renderLoaded(saved)
    act(() => {
      result.current.updateConfig('profile.salary_min', 5)
    })
    let ok: boolean | undefined
    await act(async () => {
      ok = await result.current.saveConfig()
    })
    expect(ok).toBe(true)
    await waitFor(() => expect(result.current.dirty).toBe(false))
    expect(saved.current.profile.salary_min).toBe(5)
  })

  it('保存失败返回 false，脏还在，值也还在', async () => {
    const saved = { current: JSON.parse(JSON.stringify(INITIAL)) }
    const { result } = await renderLoaded(saved, false)
    act(() => {
      result.current.updateConfig('profile.salary_min', 5)
    })
    let ok: boolean | undefined
    await act(async () => {
      ok = await result.current.saveConfig()
    })
    expect(ok).toBe(false)
    expect(result.current.dirty).toBe(true)
    expect(result.current.config!.profile.salary_min).toBe(5)
    expect(result.current.message?.type).toBe('error')
  })
})
