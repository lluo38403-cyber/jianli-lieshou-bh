import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ErrorBoundary } from './ErrorBoundary'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function Boom(): never {
  throw new Error('boom')
}

it('shows a retry fallback instead of a blank page when a child throws', () => {
  vi.spyOn(console, 'error').mockImplementation(() => {})
  render(
    <ErrorBoundary>
      <Boom />
    </ErrorBoundary>,
  )
  expect(screen.getByText('这个页面打不开了')).toBeTruthy()
  expect(screen.getByRole('button', { name: '重新打开' })).toBeTruthy()
})

it('renders children when nothing throws', () => {
  render(
    <ErrorBoundary>
      <p>页面内容</p>
    </ErrorBoundary>,
  )
  expect(screen.getByText('页面内容')).toBeTruthy()
})
