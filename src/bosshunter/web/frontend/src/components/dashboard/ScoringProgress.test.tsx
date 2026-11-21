import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ScoringProgress } from './ScoringProgress'

afterEach(cleanup)

describe('ScoringProgress', () => {
  it('counts filtered and failed jobs as processed, and clamps invalid counters', () => {
    const { rerender } = render(<ScoringProgress status="running" metrics={{ ai_total: 3, ai_completed: 2, ai_passed: 1, ai_failed: 1 }} />)
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('66')
    expect(screen.getByText('已打 2 / 3 个 · 66%')).toBeTruthy()
    expect(screen.getByText('没打好 1 个')).toBeTruthy()
    rerender(<ScoringProgress status="completed" metrics={{ ai_total: 3, ai_completed: 9, ai_filtered: -1 }} />)
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('100')
    expect(screen.getByText('筛掉 0 个')).toBeTruthy()
  })

  it('keeps preparation indeterminate and never shows a fabricated percentage', () => {
    render(<ScoringProgress status="running" metrics={{ ai_total: NaN, ai_completed: Infinity }} />)
    expect(screen.getByRole('progressbar').hasAttribute('aria-valuenow')).toBe(false)
    expect(screen.getByText('正在准备要打分的岗位…')).toBeTruthy()
  })

  it('does not show a waiting animation for an ended run without counters', () => {
    render(<ScoringProgress status="stopped" />)
    expect(screen.getByText('还没开始打分')).toBeTruthy()
    expect(screen.queryByText('正在准备要打分的岗位…')).toBeNull()
  })

  it('preserves partial progress while paused and starts a resumed batch at zero', () => {
    const { rerender } = render(<ScoringProgress status="paused" metrics={{ ai_total: 10, ai_completed: 4 }} />)
    expect(screen.getByRole('progressbar').getAttribute('aria-valuetext')).toContain('先停下了，已处理 4 / 10')
    rerender(<ScoringProgress status="running" metrics={{ ai_total: 6, ai_completed: 0 }} />)
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('0')
    expect(screen.getByText('已打 0 / 6 个 · 0%')).toBeTruthy()
  })

  it.each(['failed', 'stopped', 'completed_with_errors'])('retains actual counters when %s', status => {
    render(<ScoringProgress status={status} metrics={{ ai_total: 10, ai_completed: 4 }} />)
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('40')
    expect(screen.getByRole('progressbar').getAttribute('aria-valuetext')).not.toContain('评分中')
  })

  it('shows the job currently being scored while running', () => {
    render(<ScoringProgress
      status="running"
      metrics={{ ai_total: 10, ai_completed: 1 }}
      progress={{ scoring_current: [
        { index: 2, company: '测试公司', title: 'AI 产品经理', attempt: 1, max_attempts: 2 },
        { index: 3, company: '另一家', title: '后端工程师', attempt: 1, max_attempts: 2 },
      ] }}
    />)
    expect(screen.getByText(/正在为第 2\/10 份岗位打分：测试公司｜AI 产品经理/)).toBeTruthy()
    expect(screen.getByText(/同时还在打另外 1 个/)).toBeTruthy()
  })

  it('shows retry and review states on the current job', () => {
    const { rerender } = render(<ScoringProgress
      status="running"
      metrics={{ ai_total: 5, ai_completed: 0 }}
      progress={{ scoring_current: [{ index: 1, company: '测试公司', title: 'AI 产品经理', attempt: 2, max_attempts: 2 }] }}
    />)
    expect(screen.getByText(/第 2\/2 次尝试/)).toBeTruthy()
    rerender(<ScoringProgress
      status="running"
      metrics={{ ai_total: 5, ai_completed: 0 }}
      progress={{ scoring_current: [{ index: 1, company: '测试公司', title: 'AI 产品经理', note: '二次复核' }] }}
    />)
    expect(screen.getByText(/二次复核/)).toBeTruthy()
  })

  it('hides the current job line once the run is no longer active', () => {
    render(<ScoringProgress
      status="completed"
      metrics={{ ai_total: 5, ai_completed: 5 }}
      progress={{ scoring_current: [{ index: 5, company: '测试公司', title: 'AI 产品经理' }] }}
    />)
    expect(screen.queryByText(/正在为第/)).toBeNull()
    expect(screen.getByText(/这轮打完了/)).toBeTruthy()
  })
})
