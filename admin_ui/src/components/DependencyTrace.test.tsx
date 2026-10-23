import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { DependencyTrace } from '@/components/ui/dependency-trace'

const NODES = [
  { id: 'tv', label: 'TradingView', x: 50, y: 50 },
  { id: 'web', label: 'Web server', x: 150, y: 50, status: 'ok' as const, statusLabel: 'Up' },
  { id: 'trader', label: 'Trader', x: 250, y: 50, status: 'down' as const, statusLabel: 'Down', detail: 'Stopped' },
]
const LINKS = [
  { from: 'tv', to: 'web', label: 'webhook' },
  { from: 'trader', to: 'web', label: '/latest-signals' },
]

describe('a map of what calls what', () => {
  it('names each node with its health, and traces one from the keyboard', async () => {
    const user = userEvent.setup()
    render(<DependencyTrace nodes={NODES} connections={LINKS} width={300} height={100} label="Services" />)
    const map = screen.getByRole('region', { name: 'Services' })

    expect(within(map).getByRole('button', { name: 'Trader: Down' })).toBeInTheDocument()
    await user.tab()
    await user.tab()
    expect(within(map).getByRole('button', { name: 'Web server: Up' })).toHaveFocus()
    expect(map).toHaveTextContent('Web server: Up · called by TradingView, Trader')
    expect(within(map).getByText('webhook')).toBeInTheDocument()

    await user.keyboard('{Enter}')
    expect(within(map).getByRole('button', { name: 'Web server: Up' })).toHaveAttribute('aria-pressed', 'true')
  })
})
