import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ScoreJobsDialog } from './ScoreJobsDialog'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('ScoreJobsDialog selected scope rescore', () => {
  it('sends force_rescore=true when previewing the selected scope', async () => {
    // v45 回归（何铜浩反馈“不给重新打分”）：选“找工作里选中的”时预览
    // 必须带 force_rescore=true，否则已打过分的岗位会被后端滤掉、预览
    // 显示“符合条件的 0，会跳过 1”。
    const previewCalls: unknown[] = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/scoring/preview') {
        previewCalls.push(JSON.parse(String(init?.body)))
        return new Response(JSON.stringify({ eligible_jobs: 1, skipped_jobs: 0, first_attempt_requests: 1, max_attempts_per_job: 2, max_possible_requests: 2, note: '' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    vi.stubGlobal('fetch', fetchMock)
    const props = { selectedJobIds: ['job-a'], onClose: vi.fn(), onStart: vi.fn() }
    render(<ScoreJobsDialog open {...props} />)
    fireEvent.change(screen.getByLabelText(/给哪些岗位打分/), { target: { value: 'selected' } })
    expect(await screen.findByText('找工作里选了 1 条')).toBeTruthy()
    const selectedPreviews = previewCalls.filter(call => (call as { options: { scope: string } }).options.scope === 'selected')
    expect(selectedPreviews.length).toBeGreaterThan(0)
    expect((selectedPreviews[0] as { options: Record<string, unknown> }).options).toEqual({
      scope: 'selected', limit: null, job_ids: ['job-a'], force_rescore: true,
    })
  })
})

describe('ScoreJobsDialog progress polling', () => {
  it('refreshes an active run and stops polling when the dialog closes', async () => {
    vi.useFakeTimers()
    let completed = 1
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(
      String(input) === '/api/scoring/runs'
        ? [{ id: 'run-1', status: 'running', remaining_job_ids: ['a', 'b'], progress: { ai_total: 4, ai_completed: completed } }]
        : { eligible_jobs: 0 }
    ), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    const props = { selectedJobIds: [], onClose: vi.fn(), onStart: vi.fn() }
    const { rerender } = render(<ScoreJobsDialog open {...props} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('25')
    completed = 2
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('50')
    rerender(<ScoreJobsDialog open={false} {...props} />)
    const calls = fetchMock.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(fetchMock.mock.calls.length).toBe(calls)
  })

  it('does not start an overlapping poll while the previous run response is slow', async () => {
    vi.useFakeTimers()
    let resolveFirst!: (value: Response) => void
    let resolveSecond!: (value: Response) => void
    let runCalls = 0
    const runResponse = (completed: number) => new Response(JSON.stringify([
      { id: 'run-1', status: 'running', remaining_job_ids: ['a', 'b'], progress: { ai_total: 4, ai_completed: completed } },
    ]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input) === '/api/scoring/runs') {
        runCalls += 1
        return new Promise<Response>(resolve => {
          if (runCalls === 1) resolveFirst = resolve
          else resolveSecond = resolve
        })
      }
      return Promise.resolve(new Response(JSON.stringify({ eligible_jobs: 0 }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const props = { selectedJobIds: [], onClose: vi.fn(), onStart: vi.fn() }
    render(<ScoreJobsDialog open {...props} />)

    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(runCalls).toBe(1)
    await act(async () => { resolveFirst(runResponse(1)) })
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('25')

    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(runCalls).toBe(2)
    await act(async () => { resolveSecond(runResponse(2)) })
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('50')
  })

})

describe('ScoreJobsDialog Escape', () => {
  const stubFetch = () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(
      String(input) === '/api/scoring/runs' ? [] : { eligible_jobs: 0 },
    ), { status: 200, headers: { 'Content-Type': 'application/json' } })))
  }

  it('closes on Escape like the collect dialog and the tour', async () => {
    // v51 回归：之前按 Esc 没反应，只能点「关闭」/「取消」。
    stubFetch()
    const onClose = vi.fn()
    render(<ScoreJobsDialog open selectedJobIds={[]} onClose={onClose} onStart={vi.fn()} />)
    await screen.findByText('单独打分')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('does not close on Escape when the dialog is not open', () => {
    stubFetch()
    const onClose = vi.fn()
    render(<ScoreJobsDialog open={false} selectedJobIds={[]} onClose={onClose} onStart={vi.fn()} />)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
  })
})
