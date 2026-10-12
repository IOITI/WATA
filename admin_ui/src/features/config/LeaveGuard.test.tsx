import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { createMemoryRouter, Link, RouterProvider } from 'react-router'

import { LeaveGuard } from './LeaveGuard'

function renderPage(dirty: boolean) {
  const router = createMemoryRouter(
    [
      {
        path: '/config',
        element: (
          <>
            <h1>Configuration</h1>
            <Link to="/positions">Positions</Link>
            <Link to="/login">Session ended</Link>
            <LeaveGuard dirty={dirty} summary="3 unsaved changes to the configuration will be lost." />
          </>
        ),
      },
      { path: '/positions', element: <h1>Positions page</h1> },
      { path: '/login', element: <h1>Sign in</h1> },
    ],
    { initialEntries: ['/config'] },
  )
  render(<RouterProvider router={router} />)
  return router
}

describe('leaving a page with unsaved changes', () => {
  it('asks first, and stays when asked to', async () => {
    const user = userEvent.setup()
    const router = renderPage(true)

    await user.click(screen.getByRole('link', { name: 'Positions' }))

    const dialog = await screen.findByRole('dialog', { name: 'Leave without saving?' })
    expect(dialog).toHaveTextContent('3 unsaved changes to the configuration will be lost.')
    expect(router.state.location.pathname).toBe('/config')

    await user.click(screen.getByRole('button', { name: 'Stay on this page' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByRole('heading', { name: 'Configuration' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/config')
  })

  it('leaves when that is confirmed', async () => {
    const user = userEvent.setup()
    renderPage(true)

    await user.click(screen.getByRole('link', { name: 'Positions' }))
    await user.click(await screen.findByRole('button', { name: 'Leave and discard' }))

    expect(await screen.findByRole('heading', { name: 'Positions page' })).toBeInTheDocument()
  })

  it('does not ask when there is nothing to lose', async () => {
    const user = userEvent.setup()
    renderPage(false)

    await user.click(screen.getByRole('link', { name: 'Positions' }))

    expect(await screen.findByRole('heading', { name: 'Positions page' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('does not hold back a session that ended', async () => {
    const user = userEvent.setup()
    renderPage(true)

    await user.click(screen.getByRole('link', { name: 'Session ended' }))

    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('asks the browser to confirm a reload or a closed tab, only while there are changes', () => {
    const { unmount } = render(
      <RouterProvider
        router={createMemoryRouter([{ path: '/', element: <LeaveGuard dirty summary="Lost." /> }])}
      />,
    )
    const leaving = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(leaving)
    expect(leaving.defaultPrevented).toBe(true)

    unmount()
    const later = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(later)
    expect(later.defaultPrevented).toBe(false)
  })
})
