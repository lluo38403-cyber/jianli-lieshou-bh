import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { JobFilterBar } from './JobFilterBar'
import { EMPTY_JOB_FILTERS } from '@/lib/jobFilters'

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
