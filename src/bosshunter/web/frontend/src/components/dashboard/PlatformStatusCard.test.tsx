import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { PlatformStatusCard } from './PlatformStatusCard'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

// 卡片按 checked_at 判断行是否过期：超过 10 分钟没更新的旧值不再当成
// 事实展示，所以用例里要断言“已掉线/已登录”的行必须用新鲜时间戳。
const freshAt = () => new Date().toISOString()
const staleAt = '2026-11-19T08:00:00'

const login = {
  boss: { status: 'logged_in' as const, checked_at: freshAt(), detail: '' },
  zhilian: { status: 'logged_out' as const, checked_at: freshAt(), detail: '登录过期了' },
  '51job': { status: 'unknown' as const },
  liepin: { status: 'logged_in' as const, checked_at: freshAt(), detail: '' },
}

it('shows all three states and highlights the logged-out platform with a relogin button', async () => {
  const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
    ok: true, target_id: 't-1', browser_started: false, reused_tab: false, message: '已在工作浏览器打开。',
  }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
  render(<PlatformStatusCard platformLogin={login} />)
  expect(screen.getByRole('region', { name: '各平台登录状态' })).toBeTruthy()
  expect(screen.getAllByText('已登录')).toHaveLength(2)
  expect(screen.getByText('已掉线')).toBeTruthy()
  expect(screen.getByText('还没查过')).toBeTruthy()
  expect(screen.getByText('登录过期了')).toBeTruthy()
  expect(screen.getByText('1 个平台掉线了，先重新登录再跑')).toBeTruthy()
  const relogin = screen.getByRole('button', { name: '重新登录智联招聘' })
  expect(relogin.closest('div')?.className).toContain('bg-danger-soft')
  fireEvent.click(relogin)
  // v27：走后端 /api/browser/open 在工作浏览器里打开，成功后卡片上给反馈，
  // 不再回退到 window.open。
  await waitFor(() => expect(screen.getByText('已在工作浏览器打开智联招聘登录页。')).toBeTruthy())
  expect(openSpy).not.toHaveBeenCalled()
})

it('shows one shared detail once instead of repeating it on all four platforms', () => {
  const shared = '浏览器还没连上，先点任意一个平台的“去登录”。'
  render(<PlatformStatusCard platformLogin={{
    boss: { status: 'unknown' as const, checked_at: freshAt(), detail: shared },
    zhilian: { status: 'unknown' as const, checked_at: freshAt(), detail: shared },
    '51job': { status: 'unknown' as const, checked_at: freshAt(), detail: shared },
    liepin: { status: 'unknown' as const, checked_at: freshAt(), detail: shared },
  }} />)
  expect(screen.getAllByText(shared)).toHaveLength(1)
  expect(screen.getAllByText('还没查过')).toHaveLength(4)
})

it('shows “not checked yet” for every platform when the backend has no login data', () => {
  render(<PlatformStatusCard platformLogin={null} />)
  expect(screen.getAllByText('还没查过')).toHaveLength(4)
  expect(screen.queryByText('已掉线')).toBeNull()
  expect(screen.getByRole('button', { name: '去BOSS 直聘登录' })).toBeTruthy()
})

it('refresh button only refreshes login states and shows progress and time', async () => {
  const onRefresh = vi.fn()
  const { rerender } = render(<PlatformStatusCard platformLogin={login} onRefresh={onRefresh} />)
  const button = screen.getByRole('button', { name: '刷新登录状态' })
  expect(button).toBeTruthy()
  fireEvent.click(button)
  expect(onRefresh).toHaveBeenCalledTimes(1)
  rerender(<PlatformStatusCard platformLogin={login} onRefresh={onRefresh} refreshing />)
  expect(screen.getByRole('button', { name: '刷新登录状态' }).hasAttribute('disabled')).toBe(true)
  expect(screen.getByText('正在刷新登录状态…')).toBeTruthy()
  const refreshedAt = new Date('2026-11-20T12:34:56')
  rerender(<PlatformStatusCard platformLogin={login} onRefresh={onRefresh} refreshedAt={refreshedAt} />)
  expect(screen.getByText(`已刷新 ${refreshedAt.toLocaleTimeString('zh-CN', { hour12: false })}`)).toBeTruthy()
  rerender(<PlatformStatusCard platformLogin={login} onRefresh={onRefresh} refreshError="没刷出来，稍后再点刷新试试。" />)
  expect(screen.getByText('没刷出来，稍后再点刷新试试。')).toBeTruthy()
})

it('treats a stale logged-out row as not checked instead of dropped', () => {
  render(<PlatformStatusCard platformLogin={{
    boss: { status: 'logged_out' as const, checked_at: staleAt, detail: '登录过期了' },
    zhilian: { status: 'logged_in' as const, checked_at: freshAt(), detail: '' },
    '51job': { status: 'unknown' as const },
    liepin: { status: 'logged_in' as const, checked_at: freshAt(), detail: '' },
  }} />)
  // 上次启动留下的旧“掉线”不再当成事实：不显示掉线徽标、不显示旧文案。
  expect(screen.queryByText('已掉线')).toBeNull()
  expect(screen.queryByText('个平台掉线了，先重新登录再跑')).toBeNull()
  expect(screen.queryByText('登录过期了')).toBeNull()
  expect(screen.getAllByText('还没查过')).toHaveLength(2)
})

it('shows “checking” for stale rows while a check is running, fresh verdicts stay', () => {
  render(<PlatformStatusCard platformLogin={{
    boss: { status: 'logged_out' as const, checked_at: staleAt, detail: '登录过期了' },
    zhilian: { status: 'logged_out' as const, checked_at: freshAt(), detail: '登录过期了' },
    '51job': { status: 'unknown' as const },
    liepin: { status: 'logged_in' as const, checked_at: freshAt(), detail: '' },
  }} checking />)
  expect(screen.getAllByText('正在确认')).toHaveLength(2)
  // 新鲜的掉线结论在检查进行中也不闪回“正在确认”。
  expect(screen.getByText('已掉线')).toBeTruthy()
  expect(screen.getByText('1 个平台掉线了，先重新登录再跑')).toBeTruthy()
})

it('shows the failure reason in the card when the login open fails', async () => {
  // v27：后端没接通不再回退到 window.open——失败原因直接显示在卡片上。
  const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')))
  render(<PlatformStatusCard platformLogin={login} />)
  fireEvent.click(screen.getByRole('button', { name: '重新登录智联招聘' }))
  await waitFor(() => expect(screen.getByText('没连上程序的浏览器，请确认简历猎手还在运行，再点一次。')).toBeTruthy())
  expect(openSpy).not.toHaveBeenCalled()
})
