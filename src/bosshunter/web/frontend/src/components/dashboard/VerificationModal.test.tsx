import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { VerificationModal } from './VerificationModal'
import type { VerificationHold } from '@/hooks/useDashboard'

afterEach(() => cleanup())

function fakeHold(overrides: Partial<VerificationHold> = {}): VerificationHold {
  return {
    id: 3,
    platform: 'boss',
    url: 'https://www.zhipin.com/web/user/verify?x=1',
    evidence: 'captcha_url',
    created_at: '2026-11-19 13:00:00',
    ...overrides,
  }
}

describe('VerificationModal', () => {
  it('renders nothing without an open hold', () => {
    render(<VerificationModal hold={null} onContinue={vi.fn()} onStop={vi.fn()} canStop={false} />)
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })

  it('shows the platform name and asks the user to verify manually', () => {
    render(<VerificationModal hold={fakeHold()} onContinue={vi.fn()} onStop={vi.fn()} canStop={true} />)
    expect(screen.getByRole('alertdialog', { name: '平台安全验证' })).toBeTruthy()
    expect(screen.getByText(/BOSS 直聘弹出了安全验证/)).toBeTruthy()
    expect(screen.getByText(/请在浏览器里手动完成验证/)).toBeTruthy()
  })

  it('continue calls onContinue with the hold id', async () => {
    const onContinue = vi.fn().mockResolvedValue(undefined)
    render(<VerificationModal hold={fakeHold()} onContinue={onContinue} onStop={vi.fn()} canStop={true} />)
    fireEvent.click(screen.getByRole('button', { name: '我已完成验证，继续投递' }))
    expect(onContinue).toHaveBeenCalledWith(3)
  })

  it('stop is hidden when there is no active task', () => {
    render(<VerificationModal hold={fakeHold()} onContinue={vi.fn()} onStop={vi.fn()} canStop={false} />)
    expect(screen.queryByRole('button', { name: '停止投递' })).toBeNull()
  })

  it('stop calls onStop when there is an active task', async () => {
    const onStop = vi.fn().mockResolvedValue(undefined)
    render(<VerificationModal hold={fakeHold()} onContinue={vi.fn()} onStop={onStop} canStop={true} />)
    fireEvent.click(screen.getByRole('button', { name: '停止投递' }))
    expect(onStop).toHaveBeenCalled()
  })
})
