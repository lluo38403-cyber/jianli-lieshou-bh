import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { PlatformStatusCard } from './PlatformStatusCard'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const login = {
  boss: { status: 'logged_in' as const, checked_at: '2026-11-19T08:00:00', detail: '' },
  zhilian: { status: 'logged_out' as const, checked_at: '2026-11-19T08:00:00', detail: '登录过期了' },
  '51job': { status: 'unknown' as const },
  liepin: { status: 'logged_in' as const, checked_at: '2026-11-19T08:00:00', detail: '' },
}

it('shows all three states and highlights the logged-out platform with a relogin button', async () => {
  const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
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
  expect(openSpy).toHaveBeenCalledWith('https://www.zhaopin.com/', '_blank', 'noopener,noreferrer')
})

it('shows “not checked yet” for every platform when the backend has no login data', () => {
  render(<PlatformStatusCard platformLogin={null} />)
  expect(screen.getAllByText('还没查过')).toHaveLength(4)
  expect(screen.queryByText('已掉线')).toBeNull()
  expect(screen.getByRole('button', { name: '去BOSS 直聘登录' })).toBeTruthy()
})
