import { render, screen } from '@testing-library/react'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import { PnlValue } from './status'

// Motion reads the preference once per page: in a file of its own, before anything renders.
beforeAll(() => {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: query.includes('prefers-reduced-motion'),
      media: query,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
    })),
  )
})

describe('a rolling profit or loss where motion is reduced', () => {
  it('is the value as plain text', () => {
    const { container } = render(<PnlValue value={1.25} kind="percent" animate />)

    expect(screen.getByText('+1.25%')).not.toHaveClass('sr-only')
    expect(container.querySelector('[aria-hidden="true"]')).toBeNull()
  })
})
