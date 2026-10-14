import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'

import { PageHeader } from './Panel'

describe('the header of a page', () => {
  it('has no trail unless it is given one: the menu says where a page of the first level is', () => {
    render(
      <MemoryRouter>
        <PageHeader title="Positions" />
      </MemoryRouter>,
    )

    expect(screen.queryByRole('navigation', { name: 'Breadcrumb' })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Positions' })).toBeInTheDocument()
  })

  it('says where a deeper page is, from the dashboard, with router links', () => {
    render(
      <MemoryRouter>
        <PageHeader
          title="Long 24,150 · SG"
          breadcrumb={[{ label: 'Data' }, { label: 'Assets View', href: '/data/assets' }, { label: 'Long 24,150 · SG' }]}
        />
      </MemoryRouter>,
    )

    const trail = within(screen.getByRole('navigation', { name: 'Breadcrumb' }))
    expect(trail.getByRole('link', { name: 'Home' })).toHaveAttribute('href', '/')
    expect(trail.getByRole('link', { name: 'Assets View' })).toHaveAttribute('href', '/data/assets')
    expect(trail.queryByRole('link', { name: 'Data' })).not.toBeInTheDocument()
    expect(trail.getByText('Long 24,150 · SG').closest('[aria-current="page"]')).not.toBeNull()
  })
})
