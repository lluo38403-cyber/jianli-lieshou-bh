import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { CityMultiSelect } from './CityMultiSelect'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const options = [
  { name: '北京', code: '010000', hot: true },
  { name: '上海', code: '020000', hot: true },
  { name: '深圳', code: '030000' },
  { name: '广州', code: '040000' },
]

it('never shows the internal city code, only the Chinese name', () => {
  render(<CityMultiSelect options={options} value={['北京']} onChange={vi.fn()} onRefresh={vi.fn()} refreshing={false} message="" />)
  expect(screen.getByText('北京')).toBeTruthy()
  expect(document.body.textContent).not.toContain('010000')
  expect(document.body.textContent).not.toContain('020000')
  expect(screen.getByPlaceholderText('搜索城市名')).toBeTruthy()
})

it('lets hot cities be picked with one tap', () => {
  const onChange = vi.fn()
  render(<CityMultiSelect options={options} value={['北京']} onChange={onChange} onRefresh={vi.fn()} refreshing={false} message="" />)
  fireEvent.click(screen.getByRole('button', { name: '上海' }))
  expect(onChange).toHaveBeenCalledWith(['北京', '上海'])
})

it('offers a search dropdown and picks from it', () => {
  const onChange = vi.fn()
  render(<CityMultiSelect options={options} value={['北京']} onChange={onChange} onRefresh={vi.fn()} refreshing={false} message="" />)
  const input = screen.getByLabelText('搜索城市')
  fireEvent.change(input, { target: { value: '深' } })
  const suggestion = screen.getByRole('button', { name: '深圳' })
  fireEvent.mouseDown(suggestion)
  expect(onChange).toHaveBeenCalledWith(['北京', '深圳'])
})

it('shows the refresh control as a small icon button', () => {
  const onRefresh = vi.fn()
  render(<CityMultiSelect options={options} value={[]} onChange={vi.fn()} onRefresh={onRefresh} refreshing={false} message="" />)
  expect(screen.queryByRole('button', { name: '刷新城市列表' })).toBeTruthy()
  expect(screen.queryByText('刷新城市列表')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '刷新城市列表' }))
  expect(onRefresh).toHaveBeenCalled()
})
