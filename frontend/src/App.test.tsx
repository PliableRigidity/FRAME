import { render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { App } from './App'
afterEach(() => vi.restoreAllMocks())
test('renders a safe empty gallery', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) }))
  render(<App />)
  expect(screen.getByText('FRAME')).toBeTruthy()
  expect(screen.getByText(/only reads source files/i)).toBeTruthy()
})
