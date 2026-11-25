import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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
  // v51：后面接了 7 步（找工作/打分/投递/投递跟进/卡密激活/更新检查/
  // 智能打招呼），测试 DOM 里没有这些锚点，靠“跳过这步”走完。
  expect((await screen.findByRole('dialog', { name: '交互引导' })).textContent).toContain('找工作')
  for (let i = 0; i < 7; i += 1) {
    fireEvent.click(screen.getByRole('button', { name: '跳过这步' }))
  }
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '交互引导' })).toBeNull())
  expect(window.localStorage.getItem('jianli-lieshou:tour-done')).toBe('1')
})

describe('OnboardingTour auto-start', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  function stubUsage(funnel: Record<string, number>, keywords: string[]) {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/funnel') {
        return new Response(JSON.stringify(funnel), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response(JSON.stringify({ search: { keywords } }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
  }

  function renderTour() {
    render(
      <MemoryRouter initialEntries={['/']}>
        <div>首页内容</div>
        <OnboardingTour />
      </MemoryRouter>,
    )
  }

  const emptyFunnel = { '采集总数': 0, '初筛通过': 0, 'AI评分': 0, '自动确认': 0, '发送': 0, '回复': 0, '简历已发': 0, '待手动发简历': 0, '简历生成': 0, '跟进': 0, '拒绝': 0 }

  it('新用户第一次打开自动带一遍', async () => {
    stubUsage(emptyFunnel, ['关键词1', '关键词2'])
    renderTour()
    expect((await screen.findByRole('dialog', { name: '交互引导' })).textContent).toContain('先找到设置')
  })

  it('有岗位的老用户不自动弹', async () => {
    stubUsage({ ...emptyFunnel, '采集总数': 5 }, ['关键词1'])
    renderTour()
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)) })
    expect(screen.queryByRole('dialog', { name: '交互引导' })).toBeNull()
  })

  it('填过真实关键词的老用户不自动弹', async () => {
    stubUsage(emptyFunnel, ['仓储管理员'])
    renderTour()
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)) })
    expect(screen.queryByRole('dialog', { name: '交互引导' })).toBeNull()
  })

  it('已经走完过的用户不自动弹', async () => {
    window.localStorage.setItem('jianli-lieshou:tour-done', '1')
    stubUsage(emptyFunnel, [])
    renderTour()
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)) })
    expect(screen.queryByRole('dialog', { name: '交互引导' })).toBeNull()
  })

  it('按 Esc 退出也记下来，不再自动弹', async () => {
    stubUsage(emptyFunnel, [])
    renderTour()
    expect((await screen.findByRole('dialog', { name: '交互引导' })).textContent).toContain('先找到设置')
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: '交互引导' })).toBeNull()
    expect(window.localStorage.getItem('jianli-lieshou:tour-done')).toBe('1')
  })
})
