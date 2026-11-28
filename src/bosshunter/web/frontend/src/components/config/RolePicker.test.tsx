import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { RolePicker } from './RolePicker'

afterEach(cleanup)

describe('RolePicker', () => {
  it('suggests duty-distinct roles when typing a broad keyword', () => {
    render(<RolePicker value={[]} onChange={() => {}} />)
    const input = screen.getByLabelText('选岗位方向')
    fireEvent.change(input, { target: { value: '电商' } })
    expect(screen.getByText('国内电商运营')).toBeTruthy()
    expect(screen.getByText('跨境电商运营')).toBeTruthy()
    expect(screen.getByText('电商客服')).toBeTruthy()
    expect(screen.getByText('电商美工')).toBeTruthy()
  })

  it('adds a picked role as a chip and removes it with the × button', () => {
    const onChange = vi.fn()
    render(<RolePicker value={[]} onChange={onChange} />)
    const input = screen.getByLabelText('选岗位方向')
    fireEvent.change(input, { target: { value: '电商' } })
    fireEvent.mouseDown(screen.getByText('国内电商运营'))
    expect(onChange).toHaveBeenCalledTimes(1)
    const added = onChange.mock.calls[0][0]
    expect(added).toHaveLength(1)
    expect(added[0].label).toBe('国内电商运营')
    expect(added[0].keywords).toContain('淘宝运营')

    const onRemove = vi.fn()
    render(<RolePicker value={added} onChange={onRemove} />)
    fireEvent.click(screen.getByLabelText('去掉国内电商运营'))
    expect(onRemove).toHaveBeenCalledWith([])
  })

  it('does not suggest a role that is already picked', () => {
    render(
      <RolePicker
        value={[{ label: '国内电商运营', keywords: ['淘宝运营'] }]}
        onChange={() => {}}
      />,
    )
    const input = screen.getByLabelText('选岗位方向')
    fireEvent.change(input, { target: { value: '电商' } })
    const listbox = screen.getByRole('listbox')
    expect(within(listbox).queryByText('国内电商运营')).toBeNull()
    expect(within(listbox).getByText('跨境电商运营')).toBeTruthy()
  })

  it('shows a hint when nothing matches', () => {
    render(<RolePicker value={[]} onChange={() => {}} />)
    const input = screen.getByLabelText('选岗位方向')
    fireEvent.change(input, { target: { value: '不存在的岗位xyz' } })
    expect(screen.getByText(/没找到/)).toBeTruthy()
  })
})
