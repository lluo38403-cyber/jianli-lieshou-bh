import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { AppliedJobsPanel } from './AppliedJobsPanel'

const appliedJob = (overrides: Record<string, unknown> = {}) => ({
  id: 'job-sent-1',
  title: '产品经理',
  company: 'Example',
  salary: '20-30K',
  city: '上海',
  experience: '1-3 years',
  jd: '负责 AI 产品功能设计',
  score: 82,
  score_reason: '数据库技能匹配',
  greeting: '您好，我对这个岗位很感兴趣。',
  status: 'sent',
  hr_name: 'HR',
  hr_title: 'Recruiter',
  hr_active: '',
  company_size: '',
  company_industry: '',
  url: 'https://www.zhipin.com/job_detail/123.html',
  created_at: '2026-11-21 09:00:00',
  updated_at: '2026-11-21 10:00:00',
  source_platform: 'boss',
  ...overrides,
})

function searchResponse(items: unknown[], allTotal: number) {
  return new Response(JSON.stringify({
    items, total: items.length, all_total: allTotal, city_options: ['上海'],
  }), { headers: { 'Content-Type': 'application/json' } })
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function stubSearch(items: unknown[], allTotal: number) {
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(searchResponse(items, allTotal)))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('AppliedJobsPanel', () => {
  it('lists applied jobs from the applied-only search with their follow-up status', async () => {
    const fetchMock = stubSearch([
      appliedJob(),
      appliedJob({ id: 'job-replied-1', company: 'Acme', title: '前端工程师', status: 'replied', updated_at: '2026-11-21 11:00:00' }),
    ], 2)
    render(<AppliedJobsPanel />)
    await act(async () => {})
    expect(screen.getByText('Example｜产品经理')).toBeTruthy()
    expect(screen.getByText('Acme｜前端工程师')).toBeTruthy()
    // “已投出”出现两次：岗位行的状态徽标 + 状态筛选项；“HR 回了”同理。
    expect(screen.getAllByText('已投出')).toHaveLength(2)
    expect(screen.getAllByText('HR 回了')).toHaveLength(2)
    expect(screen.getByText('一共 2 个已投出')).toBeTruthy()
    const url = new URL(fetchMock.mock.calls[0][0], 'http://localhost')
    expect(url.searchParams.get('applied_only')).toBe('1')
    expect(url.searchParams.get('sort_by')).toBe('updated_at')
    // 状态筛选项里只有已投出状态，没有“等着投”这类投递前状态。
    expect(screen.queryByText('等着投')).toBeNull()
  })

  it('finds an applied job by company keyword', async () => {
    const fetchMock = stubSearch([appliedJob()], 1)
    render(<AppliedJobsPanel />)
    await act(async () => {})
    fireEvent.change(screen.getByLabelText('关键词'), { target: { value: 'Example' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(300) })
    const lastUrl = new URL(fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0], 'http://localhost')
    expect(lastUrl.searchParams.get('q')).toBe('Example')
    expect(lastUrl.searchParams.get('applied_only')).toBe('1')
  })

  it('expands a row to show the job detail and greeting that was sent', async () => {
    stubSearch([appliedJob()], 1)
    render(<AppliedJobsPanel />)
    await act(async () => {})
    fireEvent.click(screen.getByText('Example｜产品经理'))
    expect(screen.getByText('负责 AI 产品功能设计')).toBeTruthy()
    expect(screen.getByText('您好，我对这个岗位很感兴趣。')).toBeTruthy()
  })

  it('shows the empty state when nothing has been applied yet', async () => {
    stubSearch([], 0)
    render(<AppliedJobsPanel />)
    await act(async () => {})
    expect(screen.getByText(/还没投出过岗位/)).toBeTruthy()
  })

  it('runs the reply check on demand and shows what happened', async () => {
    stubSearch([appliedJob()], 1)
    const onCheckReplies = vi.fn().mockResolvedValue('已经开始查 HR 回复了，查到会出现在上面「等你处理」里。')
    render(<AppliedJobsPanel onCheckReplies={onCheckReplies} />)
    await act(async () => {})
    fireEvent.click(screen.getByText('再查一次 HR 回复'))
    await act(async () => {})
    expect(onCheckReplies).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/已经开始查 HR 回复了/)).toBeTruthy()
  })
})
