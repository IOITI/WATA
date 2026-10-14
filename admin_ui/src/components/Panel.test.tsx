import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'

import { PageHeader } from './Panel'

describe('the header of a page', () => {
  it('names the page in its heading and in the tab, and leaves the trail to the top bar', () => {
    render(
      <MemoryRouter>
        <PageHeader title="Long 24,150 · SG" parents={[{ label: 'Somewhere', href: '/somewhere' }]} />
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { level: 1, name: 'Long 24,150 · SG' })).toBeInTheDocument()
    expect(document.title).toBe('Long 24,150 · SG · WATA Admin')
    // Outside the shell, its name goes nowhere, and nothing fails.
    expect(screen.queryByRole('navigation', { name: 'Breadcrumb' })).not.toBeInTheDocument()
  })
})
