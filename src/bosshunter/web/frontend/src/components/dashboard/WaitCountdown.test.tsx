import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { WaitCountdown, formatWaitRemaining } from './WaitCountdown'

afterEach(cleanup)

describe('formatWaitRemaining', () => {
  it('formats minutes and seconds as MM:SS with zero padding', () => {
    expect(formatWaitRemaining(300)).toBe('05:00')
    expect(formatWaitRemaining(61)).toBe('01:01')
    expect(formatWaitRemaining(0)).toBe('00:00')
    expect(formatWaitRemaining(754)).toBe('12:34')
  })

  it('clamps negative values to zero', () => {
    expect(formatWaitRemaining(-5)).toBe('00:00')
  })
})

describe('WaitCountdown', () => {
  it('shows MM:SS countdown and the kind text with a skip button', () => {
    const onSkip = vi.fn()
    render(
      <WaitCountdown
        wait={{ kind: 'round_rest', label: '已连续投递 20 个', ends_at: Date.now() / 1000 + 300, total_seconds: 300 }}
        onSkip={onSkip}
      />,
    )
    expect(screen.getByText('投完一轮后的风控等待')).toBeTruthy()
    expect(screen.getByText('已连续投递 20 个')).toBeTruthy()
    expect(screen.getByText('05:00')).toBeTruthy()
    const button = screen.getByRole('button', { name: '跳过等待' })
    fireEvent.click(button)
    expect(onSkip).toHaveBeenCalledTimes(1)
  })

  it('labels the per-job interval as the time until the next delivery', () => {
    render(
      <WaitCountdown
        wait={{ kind: 'interval', ends_at: Date.now() / 1000 + 90, total_seconds: 120 }}
        onSkip={() => {}}
      />,
    )
    expect(screen.getByText('距离投递下一个岗位还有')).toBeTruthy()
    expect(screen.getByText('01:30')).toBeTruthy()
  })

  it('disappears when the countdown reaches zero', () => {
    const { container } = render(
      <WaitCountdown
        wait={{ kind: 'batch_rest', ends_at: Date.now() / 1000 - 1, total_seconds: 60 }}
        onSkip={() => {}}
      />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('ticks down once a second', () => {
    vi.useFakeTimers()
    try {
      render(
        <WaitCountdown
          wait={{ kind: 'cooldown', label: '采集到投递的安全冷却', ends_at: Date.now() / 1000 + 120, total_seconds: 600 }}
          onSkip={() => {}}
        />,
      )
      expect(screen.getByText('02:00')).toBeTruthy()
      act(() => {
        vi.advanceTimersByTime(1000)
      })
      expect(screen.getByText('01:59')).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })

  it('disables the skip button while skipping', () => {
    render(
      <WaitCountdown
        wait={{ kind: 'interval', ends_at: Date.now() / 1000 + 60, total_seconds: 60 }}
        onSkip={() => {}}
        skipping
      />,
    )
    expect(screen.getByRole('button', { name: '正在跳过…' }).hasAttribute('disabled')).toBe(true)
  })
})
