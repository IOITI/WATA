import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { Hint } from './Hint'

describe('a hint', () => {
  it('describes its control for screen readers, and shows on keyboard focus', async () => {
    const user = userEvent.setup()
    render(
      <Hint content="Switch it off before deleting it">
        <button type="button">Delete</button>
      </Hint>,
    )

    const button = screen.getByRole('button', { name: 'Delete' })
    expect(button).toHaveAccessibleDescription('Switch it off before deleting it')
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    await user.tab()
    // Drawn over the page, out of any parent that would cut it.
    expect(await screen.findByRole('tooltip', { hidden: true })).toHaveTextContent('Switch it off before deleting it')
    expect(screen.getByRole('tooltip', { hidden: true }).parentElement).toBe(document.body)
  })

  it('shows at once on a tap, and goes on Escape', async () => {
    render(
      <Hint content="The page refreshes on a timer" wrap>
        <span>Polling</span>
      </Hint>,
    )

    const wrapper = screen.getByText('Polling').parentElement as HTMLElement
    act(() => {
      fireEvent.pointerDown(wrapper, { pointerType: 'touch' })
    })
    expect(screen.getByRole('tooltip', { hidden: true })).toHaveTextContent('The page refreshes on a timer')

    act(() => {
      fireEvent.keyDown(document, { key: 'Escape' })
    })
    await waitFor(() => expect(screen.queryByRole('tooltip', { hidden: true })).not.toBeInTheDocument(), { timeout: 3000 })
  })

  it('lets the keyboard reach a disabled control through a wrapper that carries its hint', async () => {
    const user = userEvent.setup()
    render(
      <Hint content="Two to 4 finished runs can be compared" wrap>
        <button type="button" disabled>
          Compare
        </button>
      </Hint>,
    )

    await user.tab()
    const wrapper = screen.getByRole('button', { name: 'Compare' }).parentElement as HTMLElement
    expect(wrapper).toHaveFocus()
    expect(wrapper).toHaveAccessibleDescription('Two to 4 finished runs can be compared')
  })

  it('says and shows nothing, and takes no focus, when it has nothing to say', async () => {
    const user = userEvent.setup()
    render(
      <Hint content={null} wrap>
        <button type="button" disabled>
          Saved
        </button>
      </Hint>,
    )

    const button = screen.getByRole('button', { name: 'Saved' })
    expect(button).not.toHaveAccessibleDescription()
    await user.tab()
    expect(document.body).toHaveFocus()
  })
})
