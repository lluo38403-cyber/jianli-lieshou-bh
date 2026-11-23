import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { JobsTable } from './JobsTable'
import type { Job } from '@/hooks/useDashboard'

afterEach(() => { cleanup() })

// v38 瘦身布局回归：8 列定宽百分比，总和恒等于 100%，表格宽度永远
// 等于内容区宽，不再定最小宽、不再横向滚动；城市+工资并进岗位列次级行，
// HR+时间合并一列，学历列只留短标签，占位句子全部删掉。
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

const secondJob: Job = {
  ...job,
  id: 'job-2',
  title: '后端开发工程师',
  company: '另一家公司',
  education: '大专',
  employment_type: undefined,
  recruitment_type: undefined,
  employment_review: '',
}

function renderTable(jobs: Job[] = [job], loading = false, onSortChange = vi.fn()) {
  return {
    ...render(
      <JobsTable
        jobs={jobs}
        page={0}
        pageSize={10}
        total={jobs.length}
        onPageChange={vi.fn()}
        selectedIds={[]}
        onToggleSelected={vi.fn()}
        onSoftDelete={vi.fn()}
        onMarkManuallySent={vi.fn()}
        onStatusChange={vi.fn()}
        onBlockCompany={vi.fn()}
        sortBy="score"
        sortOrder="desc"
        onSortChange={onSortChange}
        loading={loading}
      />,
    ),
    onSortChange,
  }
}

describe('JobsTable 瘦身布局（v38）', () => {
  it('表格没有最小宽、没有横向滚动容器，宽度自适应内容区', () => {
    const { container } = renderTable()
    const table = container.querySelector('table')
    expect(table?.className).toContain('table-fixed')
    expect(table?.className).toContain('w-full')
    expect(table?.className).not.toContain('min-w-')
    expect(table?.parentElement?.className).not.toContain('overflow-x-auto')
  })

  it('表头 8 列，数据列与表头对齐', () => {
    const { container } = renderTable()
    const headerCells = container.querySelectorAll('thead th').length
    const firstRowCells = container.querySelectorAll('tbody tr')[0].querySelectorAll('td').length
    expect(headerCells).toBe(8)
    expect(firstRowCells).toBe(headerCells)
  })

  it('colgroup 百分比之和为 100，1920 与 1366 两档全屏各列都放得下', () => {
    const { container } = renderTable()
    const widths = [...container.querySelectorAll('colgroup col')].map(col =>
      Number.parseFloat((col as HTMLTableColElement).style.width.replace('%', '')))
    expect(widths).toEqual([4, 15, 23, 10, 5, 8, 11, 24])
    expect(widths.reduce((sum, value) => sum + value, 0)).toBe(100)
    // 宽度账：内容区 = 窗口宽 − 侧边栏 240 − 主区 padding 48 − 卡片 padding 32。
    // 1920 全屏内容区 1600px，1366 全屏内容区 1046px。
    const floors = [32, 140, 220, 90, 48, 72, 100, 200]
    for (const contentWidth of [1600, 1046]) {
      widths.forEach((percent, index) => {
        expect(contentWidth * percent / 100).toBeGreaterThanOrEqual(floors[index])
      })
    }
    // 1366 档操作列 1046 × 24% = 251px ≥ 200，四个操作按钮两排能放下。
    expect(1046 * 24 / 100).toBeGreaterThanOrEqual(200)
  })

  it('城市与工资并进岗位列次级行，缺失项不留空位', () => {
    renderTable()
    const subLine = screen.getByText('潮州市 · 9K-13K')
    expect(subLine.className).toContain('truncate')
    expect(screen.queryByText('城市')).toBeNull()
    expect(screen.queryByText('工资')).toBeNull()
  })

  it('学历列只留一行短标签，占位句子不在主行', () => {
    renderTable()
    expect(screen.getByText('本科 · 全职 · 社招')).toBeTruthy()
    expect(screen.queryByText(/还没弄清/)).toBeNull()
    expect(screen.queryByText(/这份工作是/)).toBeNull()
    expect(screen.queryByText(/这是一段很长的雇佣关系说明文字/)).toBeNull()
  })

  it('两段都未知时学历格只留学历或短横线', () => {
    renderTable([secondJob])
    expect(screen.getByText('大专')).toBeTruthy()
    expect(screen.queryByText(/还没弄清/)).toBeNull()
  })

  it('HR 活跃与时间在同一格，主次两行', () => {
    renderTable()
    const hr = screen.getByText('不知道 HR 活跃不活跃')
    const timeCell = screen.getByText('2h 前').closest('td')
    expect(hr.closest('td')).toBe(timeCell)
    expect(hr.className).toContain('whitespace-nowrap')
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

  it('打分与状态单行不换行', () => {
    renderTable()
    expect(screen.getByText('85').closest('td')?.className).toContain('whitespace-nowrap')
    // “等着投”在状态徽标和改状态下拉里各出现一次，取徽标那个。
    const statusBadge = screen.getAllByText('等着投').find(element => element.tagName === 'DIV')
    expect(statusBadge?.className).toContain('whitespace-nowrap')
  })

  it('操作列四个控件都在：打开岗位、删除图标、屏蔽公司、改状态', () => {
    renderTable()
    for (const label of ['打开这个岗位', '屏蔽公司']) {
      const control = screen.getByText(label).closest('a,button')
      expect(control?.className).toContain('whitespace-nowrap')
    }
    expect(screen.getByRole('button', { name: /扔进回收站/ })).toBeTruthy()
    expect(screen.getByRole('combobox', { name: /改一下/ })).toBeTruthy()
  })

  it('点薪资表头按工资排序', () => {
    const { onSortChange } = renderTable()
    fireEvent.click(screen.getByRole('button', { name: /薪资/ }))
    expect(onSortChange).toHaveBeenCalledWith('salary')
  })

  it('当前排序的表头要发光：只有它带激活态', () => {
    renderTable()
    const active = screen.getByRole('button', { name: /打分/ })
    expect(active.getAttribute('aria-pressed')).toBe('true')
    expect(active.className).toContain('border-primary')
    expect(active.className).toContain('bg-secondary')
    const inactive = screen.getByRole('button', { name: /薪资/ })
    expect(inactive.getAttribute('aria-pressed')).toBe('false')
    expect(inactive.className).toContain('border-transparent')
    expect(inactive.className).not.toContain('border-primary')
  })

  it('点非当前排序的表头会亮起来', () => {
    const { rerender } = renderTable()
    fireEvent.click(screen.getByRole('button', { name: /薪资/ }))
    const base = {
      jobs: [job], page: 0, pageSize: 10, total: 1, onPageChange: vi.fn(),
      selectedIds: [], onToggleSelected: vi.fn(), onSoftDelete: vi.fn(),
      onMarkManuallySent: vi.fn(), onStatusChange: vi.fn(), onBlockCompany: vi.fn(),
      sortBy: 'salary' as const, sortOrder: 'asc' as const, onSortChange: vi.fn(), loading: false,
    }
    rerender(<JobsTable {...base} />)
    expect(screen.getByRole('button', { name: /薪资/ }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: /打分/ }).getAttribute('aria-pressed')).toBe('false')
  })

  it('数据行带 stagger 淡入类，延迟按行号递增', () => {
    const { container } = renderTable([job, secondJob])
    const rows = container.querySelectorAll('tbody tr.job-row-enter')
    expect(rows.length).toBe(2)
    expect((rows[0] as HTMLElement).style.animationDelay).toBe('0s')
    expect((rows[1] as HTMLElement).style.animationDelay).toBe('0.045s')
  })

  it('加载且无数据时出 5 行流光骨架行，不留空白', () => {
    const { container } = renderTable([], true)
    expect(container.querySelectorAll('.skeleton').length).toBeGreaterThan(0)
    expect(container.querySelectorAll('tbody tr').length).toBe(5)
    expect(screen.queryByText('按现在的条件没找到合适的')).toBeNull()
  })

  it('不加载且无数据时仍显示“按现在的条件没找到合适的”', () => {
    renderTable([], false)
    expect(screen.getByText('按现在的条件没找到合适的')).toBeTruthy()
  })

  it('点行展开后雇佣说明只在详情区出现，全文可见', () => {
    renderTable()
    fireEvent.click(screen.getByText(/高级前端开发工程师/))
    expect(screen.getByText('JD简单说')).toBeTruthy()
    expect(screen.getByText('负责前端开发工作')).toBeTruthy()
    expect(screen.getByText(/这是一段很长的雇佣关系说明文字/)).toBeTruthy()
  })

  it('展开详情的“打招呼的话”显示当前生效文案，优先于库里旧文案', () => {
    renderTable([{ ...job, greeting: '旧定制文案', effective_greeting: '新固定文案' }])
    fireEvent.click(screen.getByText(/高级前端开发工程师/))
    expect(screen.getByText('新固定文案')).toBeTruthy()
    expect(screen.queryByText('旧定制文案')).toBeNull()
  })

  it('没有当前生效文案时回退到库里招呼语', () => {
    renderTable()
    fireEvent.click(screen.getByText(/高级前端开发工程师/))
    expect(screen.getByText('你好')).toBeTruthy()
  })
})
