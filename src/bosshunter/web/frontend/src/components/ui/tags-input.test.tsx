import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { TagsInput } from './tags-input'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('splits on a Chinese comma when pressing Enter', () => {
  const onChange = vi.fn()
  render(<TagsInput value={[]} onChange={onChange} placeholder="比如：人力、产品运营" />)
  const input = screen.getByPlaceholderText('比如：人力、产品运营')
  fireEvent.change(input, { target: { value: '产品，运营' } })
  fireEvent.keyDown(input, { key: 'Enter' })
  expect(onChange).toHaveBeenCalledWith(['产品', '运营'])
})

it('adds the current input as tags when the Add button is clicked', () => {
  const onChange = vi.fn()
  render(<TagsInput value={['人力']} onChange={onChange} />)
  const input = screen.getByRole('textbox')
  fireEvent.change(input, { target: { value: '产品经理, 数据分析' } })
  fireEvent.click(screen.getByRole('button', { name: '添加' }))
  expect(onChange).toHaveBeenCalledWith(['人力', '产品经理', '数据分析'])
  expect(screen.getByText('人力')).toBeTruthy()
})

it('commits on blur as well', () => {
  const onChange = vi.fn()
  render(<TagsInput value={[]} onChange={onChange} />)
  const input = screen.getByRole('textbox')
  fireEvent.change(input, { target: { value: '运营' } })
  fireEvent.blur(input)
  expect(onChange).toHaveBeenCalledWith(['运营'])
})
