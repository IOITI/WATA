import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { StatCard } from '@/components/ui/stat-card'
import { formatMoney } from '@/lib/format'

const money = (value: number) => formatMoney(value, 'EUR', true)

describe('a tile with a trend', () => {
  it('is scrubbed day by day with the arrows, each day said as it is reached', async () => {
    const user = userEvent.setup()
    render(
      <StatCard
        label="Day profit and loss"
        value={4.2}
        trend={[12.4, -5, 4.2]}
        trendLabels={['21 Oct 2026', '22 Oct 2026', 'Today']}
        period="1 closed trade"
        format={money}
        toneOf={(value) => (value < 0 ? 'text-negative' : 'text-positive')}
      />,
    )
    const tile = screen.getByRole('group', { name: 'Day profit and loss: +€4.20' })

    await user.tab()
    expect(tile).toHaveFocus()
    await user.keyboard('{ArrowLeft}')
    expect(screen.getByRole('status')).toHaveTextContent('Today: +€4.20')
    await user.keyboard('{ArrowLeft}')
    expect(screen.getByRole('status')).toHaveTextContent('22 Oct 2026: −€5.00')
    expect(screen.getByText('22 Oct 2026')).toBeInTheDocument()
    await user.keyboard('{ArrowLeft}')
    expect(screen.getByRole('status')).toHaveTextContent('21 Oct 2026: +€12.40')
    await user.keyboard('{Escape}')
    expect(screen.getByText('1 closed trade')).toBeInTheDocument()
  })

  it('says no change unless told what from', () => {
    render(<StatCard label="Day profit and loss" value={4.2} trend={[12.4, 4.2]} format={money} />)

    expect(screen.queryByText(/%/)).not.toBeInTheDocument()
  })

  it('gives the change from the value it is told of, in the admin’s colours', () => {
    render(
      <StatCard
        label="Account value"
        value={1025}
        trend={[1000, 1010, 1025]}
        previous={1000}
        format={(value) => formatMoney(value)}
      />,
    )

    expect(screen.getByRole('group', { name: 'Account value: €1,025.00, up 2.5%' })).toBeInTheDocument()
    expect(screen.getByText('2.5%').closest('span')).toHaveClass('text-positive')
  })
})
