import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { JobsTable } from './JobsTable'
import type { Job } from '@/hooks/useDashboard'

afterEach(() => { cleanup() })

// 响应式布局回归：窄窗口下关键字段不断字、不竖排，表格横向滚动
// 而不是被压扁；长文本截断后悬停仍能看到全文，详情在展开区里。
const job: Job = {
  id: 'job-1',
  title: '高级前端开发工程师（远程优先、负责核心业务线重构与性能优化）',
  company: '深圳市某某科技有限公司广州分公司',
  salary: '9K-13K',
  city: '潮州市',
  experience: '3-5年',
  education: '本科',
  employment_type: 'full_time',
  employment_review: '这是一段很长的雇佣关系说明文字，用来验证超长内容会被截断而不是把列撑宽',
  recruitment_type: 'experienced',
  jd: '负责前端开发工作',
  score: 85,
  score_reason: '匹配度高',
  greeting: '你好',
  status: 'ready',
  hr_name: '张',
  hr_title: '招聘',
  hr_active: '不知道 HR 活跃不活跃',
  company_size: '100-499人',
  company_industry: '互联网',
  url: 'https://www.zhipin.com/job_detail/?query=1',
  created_at: new Date(Date.now() - 2 * 3600000).toISOString(),
}

function renderTable() {
  return render(
    <JobsTable
      jobs={[job]}
      page={0}
      pageSize={10}
      total={1}
      onPageChange={vi.fn()}
      selectedIds={[]}
      onToggleSelected={vi.fn()}
      onSoftDelete={vi.fn()}
      onMarkManuallySent={vi.fn()}
      onStatusChange={vi.fn()}
      onBlockCompany={vi.fn()}
      sortBy="score"
      sortOrder="desc"
      onSortChange={vi.fn()}
    />,
  )
}

describe('JobsTable 响应式布局', () => {
  it('表格有总最小宽、包在横向滚动容器里，窄窗口滚动而不是压扁', () => {
    const { container } = renderTable()
    const table = container.querySelector('table')
    expect(table?.className).toContain('min-w-[1260px]')
    expect(table?.parentElement?.className).toContain('overflow-x-auto')
  })

  it('表头列数与数据列数一致（表头与数据列对齐）', () => {
    const { container } = renderTable()
    const headerCells = container.querySelectorAll('thead th').length
    const firstRowCells = container.querySelectorAll('tbody tr')[0].querySelectorAll('td').length
    expect(headerCells).toBe(11)
    expect(firstRowCells).toBe(headerCells)
  })

  it('城市、薪资、打分、状态、时间单行不换行', () => {
    renderTable()
    for (const text of ['潮州市', '9K-13K', '85']) {
      const cell = screen.getByText(text).closest('td')
      expect(cell?.className).toContain('whitespace-nowrap')
    }
    // “等着投”在状态徽标和改状态下拉里各出现一次，取徽标那个。
    const statusBadge = screen.getAllByText('等着投').find(element => element.tagName === 'DIV')
    expect(statusBadge?.className).toContain('whitespace-nowrap')
    expect(statusBadge?.closest('td')?.className).toContain('whitespace-nowrap')
    // 时间列是“xh 前”加展开箭头，整格不换行。
    const timeCell = screen.getByText('2h 前').closest('td')
    expect(timeCell?.className).toContain('whitespace-nowrap')
  })

  it('各列有最小宽，操作列不被挤掉', () => {
    const { container } = renderTable()
    const headers = [...container.querySelectorAll('thead th')].map(th => th.className)
    expect(headers.some(name => name.includes('min-w-[84px]'))).toBe(true)
    expect(headers.some(name => name.includes('min-w-[92px]'))).toBe(true)
    expect(headers.some(name => name.includes('min-w-[52px]'))).toBe(true)
    expect(headers.some(name => name.includes('min-w-[240px]'))).toBe(true)
  })

  it('公司与岗位长文本截断，title 里保留全文', () => {
    renderTable()
    const company = screen.getByText('深圳市某某科技有限公司广州分公司')
    expect(company.className).toContain('truncate')
    expect(company.getAttribute('title')).toBe('深圳市某某科技有限公司广州分公司')
    const title = screen.getByText(/高级前端开发工程师/)
    expect(title.className).toContain('truncate')
    expect(title.getAttribute('title')).toBe(job.title)
  })

  it('学历列每行单行截断，不竖向逐字折行', () => {
    renderTable()
    const review = screen.getByText(/这是一段很长的雇佣关系说明文字/)
    expect(review.className).toContain('truncate')
    expect(review.className).toContain('whitespace-nowrap')
    expect(review.getAttribute('title')).toBe(job.employment_review)
    const employment = screen.getByText('这份工作是：全职')
    expect(employment.className).toContain('whitespace-nowrap')
  })

  it('HR 活跃列截断，title 里保留全文', () => {
    renderTable()
    const hr = screen.getByText('不知道 HR 活跃不活跃')
    expect(hr.className).toContain('truncate')
    expect(hr.getAttribute('title')).toBe('不知道 HR 活跃不活跃')
  })

  it('操作按钮不换行、不重叠', () => {
    renderTable()
    for (const label of ['打开这个岗位', '屏蔽公司']) {
      const control = screen.getByText(label).closest('a,button')
      expect(control?.className).toContain('whitespace-nowrap')
    }
  })

  it('点行展开后长文本能在详情区看到全文', () => {
    renderTable()
    fireEvent.click(screen.getByText(/高级前端开发工程师/))
    expect(screen.getByText('JD简单说')).toBeTruthy()
    expect(screen.getByText('负责前端开发工作')).toBeTruthy()
  })
})
