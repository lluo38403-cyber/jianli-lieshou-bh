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

  it('继续投递后端失败时红字显示原因并给下一步（H-7）', async () => {
    const onContinue = vi.fn().mockRejectedValue(new Error('验证已过期，请重新验证'))
    render(<VerificationModal hold={fakeHold()} onContinue={onContinue} onStop={vi.fn()} canStop={true} />)
    fireEvent.click(screen.getByRole('button', { name: '我已完成验证，继续投递' }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText(/没成功：验证已过期，请重新验证/)).toBeTruthy()
    expect(screen.getByText(/可以再点一次；如果一直不行，先去浏览器里确认验证是不是真的通过了/)).toBeTruthy()
    // 失败后按钮恢复可点，能再试一次。
    expect((screen.getByRole('button', { name: '我已完成验证，继续投递' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('停止投递后端失败时同样红字显示原因（H-7）', async () => {
    const onStop = vi.fn().mockRejectedValue(new Error('没停下来'))
    render(<VerificationModal hold={fakeHold()} onContinue={vi.fn()} onStop={onStop} canStop={true} />)
    fireEvent.click(screen.getByRole('button', { name: '停止投递' }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText(/没成功：没停下来/)).toBeTruthy()
  })

  it('重试成功后错误提示消失（H-7）', async () => {
    const onContinue = vi.fn()
      .mockRejectedValueOnce(new Error('网络断了'))
      .mockResolvedValueOnce(undefined)
    render(<VerificationModal hold={fakeHold()} onContinue={onContinue} onStop={vi.fn()} canStop={true} />)
    fireEvent.click(screen.getByRole('button', { name: '我已完成验证，继续投递' }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '我已完成验证，继续投递' }))
    await vi.waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  })

  it('stop calls onStop when there is an active task', async () => {
    const onStop = vi.fn().mockResolvedValue(undefined)
    render(<VerificationModal hold={fakeHold()} onContinue={vi.fn()} onStop={onStop} canStop={true} />)
    fireEvent.click(screen.getByRole('button', { name: '停止投递' }))
    expect(onStop).toHaveBeenCalled()
  })
})
