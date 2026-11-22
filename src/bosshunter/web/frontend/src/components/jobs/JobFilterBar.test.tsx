import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { JobFilterBar } from './JobFilterBar'
import { EMPTY_JOB_FILTERS } from '@/lib/jobFilters'

afterEach(() => { cleanup() })

function renderCollapsible(filters = {}) {
  const onChange = vi.fn()
  const utils = render(
    <JobFilterBar
      filters={{ ...EMPTY_JOB_FILTERS, ...filters }}
      onChange={onChange}
      onReset={vi.fn()}
      resultCount={3}
      totalCount={10}
      showStatus
      showSource
      collapsible
      cityOptions={['上海', '北京']}
      statusOptions={[{ value: 'ready', label: '等着投' }]}
    />,
  )
  return { ...utils, onChange }
}

describe('JobFilterBar status options', () => {
  it('limits the status picker to the options it is given', () => {
    render(
      <JobFilterBar
        filters={{ ...EMPTY_JOB_FILTERS }}
        onChange={vi.fn()}
        onReset={vi.fn()}
        resultCount={0}
        totalCount={0}
        showStatus
        statusOptions={[{ value: 'sent', label: '已投出' }, { value: 'replied', label: 'HR 回了' }]}
      />,
    )
    expect(screen.getByText('已投出')).toBeTruthy()
    expect(screen.getByText('HR 回了')).toBeTruthy()
    // 没传进来的“等着投”不出现在筛选项里。
    expect(screen.queryByText('等着投')).toBeNull()
  })
})

describe('JobFilterBar 折叠（v38）', () => {
  it('默认收起高级筛选，核心行只有 1 行', () => {
    const { container } = renderCollapsible()
    const toggle = screen.getByRole('button', { name: /展开更多筛选/ })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    const collapse = container.querySelector('.filter-collapse')
    expect(collapse?.className).toContain('filter-collapse')
    expect(collapse?.className).not.toContain('open')
    // 核心控件都在：关键词、平台、状态、最低分、计数。
    expect(screen.getByLabelText('关键词')).toBeTruthy()
    expect(screen.getByLabelText('最低分')).toBeTruthy()
    expect(screen.getByText('找到 3 个 / 一共 10 个')).toBeTruthy()
  })

  it('点折叠按钮展开/收起，标签与 open 类跟着变', () => {
    const { container } = renderCollapsible()
    const toggle = screen.getByRole('button', { name: /展开更多筛选/ })
    fireEvent.click(toggle)
    expect(screen.getByRole('button', { name: /收起筛选/ }).getAttribute('aria-expanded')).toBe('true')
    expect(container.querySelector('.filter-collapse')?.className).toContain('open')
    fireEvent.click(screen.getByRole('button', { name: /收起筛选/ }))
    expect(screen.getByRole('button', { name: /展开更多筛选/ }).getAttribute('aria-expanded')).toBe('false')
    expect(container.querySelector('.filter-collapse')?.className).not.toContain('open')
  })

  it('收起时已选的高级条件以 chips 露出，点 × 只清这一项', () => {
    const { onChange } = renderCollapsible({
      createdWithin: '3d',
      salaryMin: '5',
      education: ['大专'],
      recruitmentType: ['experienced'],
    })
    expect(screen.getByRole('button', { name: '去掉筛选 近 3 天发布' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '去掉筛选 最低 5千' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '去掉筛选 大专' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '去掉筛选 社招' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '去掉筛选 大专' }))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ education: [] }))
    // 只清了学历这一项，其他高级条件原样保留。
    const cleared = onChange.mock.calls[0][0]
    expect(cleared.createdWithin).toBe('3d')
    expect(cleared.salaryMin).toBe('5')
    expect(cleared.recruitmentType).toEqual(['experienced'])
  })

  it('没传 collapsible 时没有折叠按钮，高级条件全部展开', () => {
    render(
      <JobFilterBar
        filters={{ ...EMPTY_JOB_FILTERS }}
        onChange={vi.fn()}
        onReset={vi.fn()}
        resultCount={0}
        totalCount={0}
        showStatus
        statusOptions={[{ value: 'ready', label: '等着投' }]}
      />,
    )
    expect(screen.queryByRole('button', { name: /展开更多筛选|收起筛选/ })).toBeNull()
    expect(screen.getByLabelText('最低工资（千）')).toBeTruthy()
  })
})
