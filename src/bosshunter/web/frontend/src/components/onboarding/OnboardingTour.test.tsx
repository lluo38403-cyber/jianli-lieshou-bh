import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { useState } from 'react'
import { OnboardingTour } from './OnboardingTour'

function SaveControl() {
  const [dirty, setDirty] = useState(true)
  return <button data-tour="config-save" disabled={!dirty} onClick={() => setDirty(false)}>保存</button>
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    disconnect() {}
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('walks from the highlighted config entry to the real form controls', async () => {
  render(
    <MemoryRouter initialEntries={['/']}>
      <Link to="/" data-tour="nav-workbench">首页</Link>
      <Link to="/config" data-tour="nav-config">我的设置</Link>
      <Routes>
        <Route path="/" element={<>
          <a data-tour="chrome-guide" href="/chrome-help">连接 Chrome</a>
          <button data-tour="check-preflight">检查准备情况</button>
        </>} />
        <Route path="/config" element={<>
          <div data-tour="resume-upload">简历上传区域</div>
          <div data-tour="search-keywords">搜索关键词</div>
          <div data-tour="search-city">选择城市</div>
          <SaveControl />
        </>} />
      </Routes>
      <OnboardingTour />
    </MemoryRouter>,
  )

  act(() => window.dispatchEvent(new Event('jianli-lieshou:start-onboarding')))
  expect((await screen.findByRole('dialog', { name: '交互引导' })).textContent).toContain('点左边的“我的设置”')
  fireEvent.click(screen.getByRole('link', { name: '我的设置' }))
  expect((await screen.findByRole('dialog', { name: '交互引导' })).textContent).toContain('上传简历')
  fireEvent.click(screen.getByRole('button', { name: '下一步' }))
  expect(screen.getByRole('dialog', { name: '交互引导' }).textContent).toContain('填想找的岗位')
  fireEvent.click(screen.getByRole('button', { name: '下一步' }))
  expect(screen.getByRole('dialog', { name: '交互引导' }).textContent).toContain('选想去的城市')
  fireEvent.click(screen.getByRole('button', { name: '下一步' }))
  expect(screen.getByRole('dialog', { name: '交互引导' }).textContent).toContain('保存')
  expect((screen.getByRole('button', { name: '下一步' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect((screen.getByRole('button', { name: '下一步' }) as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: '下一步' }))
  expect(screen.getByRole('dialog', { name: '交互引导' }).textContent).toContain('首页')
  fireEvent.click(screen.getByRole('link', { name: '首页' }))
  expect((await screen.findByRole('dialog', { name: '交互引导' })).textContent).toContain('连上浏览器')
  fireEvent.click(screen.getByRole('button', { name: '下一步' }))
  expect(screen.getByRole('dialog', { name: '交互引导' }).textContent).toContain('检查能不能跑')
  fireEvent.click(screen.getByRole('button', { name: '检查准备情况' }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '交互引导' })).toBeNull())
})
