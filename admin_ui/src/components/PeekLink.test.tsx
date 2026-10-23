import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'

import { RunPeekLink } from '@/features/backtests/RunPeek'
import { listedRun, RUN_ID } from '@/test/backtests'

function Address() {
  return <p aria-label="address">{useLocation().pathname}</p>
}

function renderLink() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [
      {
        path: '/',
        element: (
          <>
            <RunPeekLink id={RUN_ID}>Smoke EMA cross</RunPeekLink>
            <Address />
          </>
        ),
      },
      { path: '/backtests/:runId', element: <Address /> },
    ],
    { initialEntries: ['/'] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  const listed = listedRun({ id: RUN_ID, name: 'Smoke EMA cross' })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      if (path === '/api/backtests') return new Response(JSON.stringify({ items: [listed], storage: null }), { status: 200 })
      throw new Error(`Unexpected request: ${path}`)
    }),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('a link previewed', () => {
  it('shows the run on keyboard focus, described by it', async () => {
    const user = userEvent.setup()
    renderLink()

    await user.tab()
    const link = screen.getByRole('link', { name: 'Smoke EMA cross' })
    expect(link).toHaveFocus()
    expect(await screen.findByText('Random runs beaten')).toBeInTheDocument()
    expect(link).toHaveAttribute('aria-describedby')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByText('Random runs beaten')).not.toBeInTheDocument())
  })

  it('opens on a first tap instead of leaving, and its card leads to the run', async () => {
    renderLink()
    const link = screen.getByRole('link', { name: 'Smoke EMA cross' })

    fireEvent.pointerDown(link, { pointerType: 'touch' })
    fireEvent.click(link)

    expect(await screen.findByText('Random runs beaten')).toBeInTheDocument()
    expect(screen.getByLabelText('address')).toHaveTextContent(/^\/$/)
    expect(screen.getByRole('link', { name: 'Open' })).toHaveAttribute('href', `/backtests/${RUN_ID}`)
  })

  it('is followed by a click', async () => {
    const user = userEvent.setup()
    renderLink()

    await user.click(screen.getByRole('link', { name: 'Smoke EMA cross' }))

    expect(screen.getByLabelText('address')).toHaveTextContent(`/backtests/${RUN_ID}`)
  })
})
