import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { App } from './App'
afterEach(() => { cleanup(); vi.restoreAllMocks() })
test('renders a safe empty gallery', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) }))
  render(<App />)
  expect(screen.getByText('FRAME')).toBeTruthy()
  expect(screen.getByText(/only reads source files/i)).toBeTruthy()
})

test('renders technical recommendation and explanation', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => Promise.resolve({ ok: true, json: async () => url.includes('/groups') ? { items: [] } : ({ items: [{ id: 1, name: 'photo', status: 'REVIEW', recommendation: 'REVIEW', technical_score: 62, has_thumbnail: false, assets: [{ extension: '.jpg' }], analysis: { metrics: { detailed_region_fraction: .5, mean_luminance: .4, highlight_clipping: .01 }, decision: { primary_problems: [], secondary_problems: ['Focus or motion-blur risk'], explanations: ['Fine detail is weak.'], score_note: 'Heuristic technical index; not a calibrated probability.' } } }] }) })))
  render(<App />)
  expect((await screen.findAllByText('REVIEW')).length).toBeGreaterThan(0)
  expect(screen.getByText('Why this recommendation?')).toBeTruthy()
  expect(screen.getByLabelText('Recommendation filter')).toBeTruthy()
  expect(screen.getByLabelText('Selection target')).toBeTruthy()
})
