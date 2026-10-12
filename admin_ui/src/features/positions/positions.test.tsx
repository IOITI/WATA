import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { Position } from '@/lib/types'

import { positionColumns } from './columns'
import { unrealisedProfitLoss } from './unrealised'

const CLOSED = {
  position_id: '5002',
  action: 'long',
  instrument_name: 'TURBO LONG NASDAQ-100 KO 24150 SG',
  instrument_symbol: 'SG-NDX-L1',
  instrument_currency: 'EUR',
  position_amount: 260,
  position_open_price: 8.42,
  position_close_price: 8.52,
  position_profit_loss: 26,
  position_total_performance_percent: 1.2,
  position_max_performance_percent: 3.1,
  position_status: 'Closed',
  position_close_reason: 'Trailing stop',
  execution_time_open: '2026-10-12T04:51:21Z',
  execution_time_close: '2026-10-12T06:10:00Z',
} as Position

const OPTIONS = { now: Date.parse('2026-10-12T07:00:00Z'), canClose: true }

function headers() {
  return screen.getAllByRole('columnheader').map((header) => header.textContent?.trim()).filter(Boolean)
}

function renderTable(view: 'open' | 'closed' | 'all', rows: Position[] = [CLOSED]) {
  render(
    <AdvancedDataTable<Position>
      title="Positions"
      data={rows}
      columns={positionColumns('Europe/Paris', view, OPTIONS)}
      getRowId={(row) => row.position_id}
      defaultViewMode="table"
      selectable={false}
      emptyTitle="No closed position in this range"
    />,
  )
}

describe('closed positions table', () => {
  it('starts with when it closed, what it made and why, and keeps the prices one click away', () => {
    renderTable('closed')

    expect(headers()).toEqual([
      'Closed',
      'Instrument',
      'Direction',
      'Profit / loss',
      'Performance',
      'Best level',
      'Close reason',
    ])
    expect(screen.getByText('Trailing stop')).toBeInTheDocument()
    expect(screen.queryByText('€8.42')).not.toBeInTheDocument()
  })

  it('brings a hidden column back from the column list', async () => {
    const user = userEvent.setup()
    renderTable('closed')

    await user.click(screen.getByRole('button', { name: 'Toggle visible columns' }))
    await user.click(screen.getByRole('checkbox', { name: /Open price/ }))

    expect(headers()).toContain('Open price')
    expect(screen.getByText('€8.42')).toBeInTheDocument()
  })

  it('shows the status only where rows can differ by it', () => {
    renderTable('all')

    expect(headers()).toEqual([
      'Instrument',
      'Direction',
      'Status',
      'Profit / loss',
      'Performance',
      'Best level',
      'Opened',
      'Closed',
      'Close reason',
    ])
  })
})

describe('a table with nothing in it', () => {
  it('says so in the words of the page, without offering to clear filters that are not set', () => {
    renderTable('closed', [])

    expect(screen.getByText('No closed position in this range')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Clear filters' })).not.toBeInTheDocument()
    expect(screen.queryByText('No matching records found')).not.toBeInTheDocument()
  })

  it('says that nothing matches when a search leaves no row, and offers to clear it', async () => {
    const user = userEvent.setup()
    renderTable('closed')

    await user.type(screen.getByPlaceholderText(/Search/), 'no such instrument')

    expect(screen.getByText('No matching records found')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(within(screen.getByRole('table')).getByText('Trailing stop')).toBeInTheDocument()
  })
})

describe('unrealised profit and loss', () => {
  it('is the bid less the open price, times the quantity', () => {
    const open = { ...CLOSED, live_bid: 8.57 } as Position
    expect(unrealisedProfitLoss(open)).toBeCloseTo(39, 6)
    expect(unrealisedProfitLoss({ ...open, live_bid: 8.12 } as Position)).toBeCloseTo(-78, 6)
  })

  it('is unknown until the monitor has seen a price', () => {
    expect(unrealisedProfitLoss({ ...CLOSED, live_bid: null } as Position)).toBeNull()
    expect(unrealisedProfitLoss({ ...CLOSED, live_bid: 8.5, position_amount: null } as Position)).toBeNull()
  })
})
