import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { UnsavedChangesProvider, useRegisterUnsavedChangesGuard } from '@/hooks/useUnsavedChangesGuard'

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Sidebar 未保存修改提醒', () => {
  let dirty = false
  let saveResult = true
  const saveMock = vi.fn(async () => saveResult)
  const discardMock = vi.fn()

  function GuardStub() {
    useRegisterUnsavedChangesGuard({
      isDirty: () => dirty,
      save: saveMock,
      discard: discardMock,
    })
    return null
  }

  function renderAtConfig() {
    return render(
      <MemoryRouter initialEntries={['/config']}>
        <UnsavedChangesProvider>
          <Sidebar />
          <Routes>
            <Route path="/" element={<div>首页内容</div>} />
            <Route path="/jobs" element={<div>找工作内容</div>} />
            <Route path="/config" element={<><GuardStub /><div>设置页内容</div></>} />
          </Routes>
        </UnsavedChangesProvider>
      </MemoryRouter>,
    )
  }

  beforeEach(() => {
    dirty = false
    saveResult = true
    saveMock.mockClear()
    discardMock.mockClear()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === '/api/health') return jsonResponse({ status: 'ok', version: '1.1.9' })
        if (url === '/api/history/unresolved-replies/count') return jsonResponse({ count: 0 })
        return jsonResponse({})
      }),
    )
  })

  it('改了没保存时切页弹提醒，点“留在本页”不跳转', async () => {
    dirty = true
    renderAtConfig()
    expect(await screen.findByText('设置页内容')).toBeTruthy()
    fireEvent.click(screen.getByRole('link', { name: '首页' }))
    expect(await screen.findByText('当前设置有未保存的修改，是否保存？')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '留在本页' }))
    expect(screen.queryByText('当前设置有未保存的修改，是否保存？')).toBeNull()
    expect(screen.getByText('设置页内容')).toBeTruthy()
    expect(saveMock).not.toHaveBeenCalled()
  })

  it('“保存并离开”：保存成功后才跳转', async () => {
    dirty = true
    renderAtConfig()
    expect(await screen.findByText('设置页内容')).toBeTruthy()
    fireEvent.click(screen.getByRole('link', { name: '首页' }))
    expect(await screen.findByText('当前设置有未保存的修改，是否保存？')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '保存并离开' }))
    expect(await screen.findByText('首页内容')).toBeTruthy()
    expect(saveMock).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('设置页内容')).toBeNull()
  })

  it('“保存并离开”：保存失败留在本页并提示，还能改选别的', async () => {
    dirty = true
    saveResult = false
    renderAtConfig()
    expect(await screen.findByText('设置页内容')).toBeTruthy()
    fireEvent.click(screen.getByRole('link', { name: '首页' }))
    expect(await screen.findByText('当前设置有未保存的修改，是否保存？')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '保存并离开' }))
    expect(await screen.findByText(/保存失败/)).toBeTruthy()
    expect(screen.getByText('设置页内容')).toBeTruthy()
    expect(screen.queryByText('首页内容')).toBeNull()
    // 失败后仍可改选“不保存并离开”。
    fireEvent.click(screen.getByRole('button', { name: '不保存并离开' }))
    expect(await screen.findByText('首页内容')).toBeTruthy()
  })

  it('“不保存并离开”：不保存直接跳转', async () => {
    dirty = true
    renderAtConfig()
    expect(await screen.findByText('设置页内容')).toBeTruthy()
    fireEvent.click(screen.getByRole('link', { name: '找工作' }))
    expect(await screen.findByText('当前设置有未保存的修改，是否保存？')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '不保存并离开' }))
    expect(await screen.findByText('找工作内容')).toBeTruthy()
    expect(saveMock).not.toHaveBeenCalled()
    expect(discardMock).toHaveBeenCalledTimes(1)
  })

  it('没改动时切页直接跳转，不弹提醒', async () => {
    dirty = false
    renderAtConfig()
    expect(await screen.findByText('设置页内容')).toBeTruthy()
    fireEvent.click(screen.getByRole('link', { name: '首页' }))
    expect(await screen.findByText('首页内容')).toBeTruthy()
    expect(screen.queryByText('当前设置有未保存的修改，是否保存？')).toBeNull()
  })

  it('点当前页“我的设置”不弹提醒', async () => {
    dirty = true
    renderAtConfig()
    expect(await screen.findByText('设置页内容')).toBeTruthy()
    fireEvent.click(screen.getByRole('link', { name: '我的设置' }))
    expect(screen.queryByText('当前设置有未保存的修改，是否保存？')).toBeNull()
    expect(screen.getByText('设置页内容')).toBeTruthy()
  })
})
