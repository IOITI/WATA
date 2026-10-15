import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { ColumnDef } from '@/components/ui/advanced-data-table'

interface Row {
  id: string
  name: string
  kind: string
}

const ROWS: Row[] = Array.from({ length: 12 }, (_, index) => ({
  id: `r${index + 1}`,
  name: `Row ${index + 1}`,
  kind: index % 2 === 0 ? 'even' : 'odd',
}))
const COLUMNS: ColumnDef<Row>[] = [
  { id: 'name', header: 'Name', accessorKey: 'name' },
  { id: 'kind', header: 'Kind', accessorKey: 'kind', filterable: true, filterOptions: [{ label: 'Even', value: 'even' }, { label: 'Odd', value: 'odd' }] },
]
const shown = () =>
  within(screen.getByRole('table'))
    .getAllByRole('row')
    .slice(1)
    .map((row) => row.textContent)
    .filter((text) => text?.startsWith('Row'))

describe('the data table, as WATA uses it', () => {
  it('shows what a search finds from its first page, wherever the reader was', async () => {
    const user = userEvent.setup()
    render(<AdvancedDataTable<Row> data={ROWS} columns={COLUMNS} getRowId={(row) => row.id} selectable={false} defaultPageSize={5} />)

    await user.click(screen.getByRole('button', { name: 'Next page' }))
    expect(shown()[0]).toMatch(/^Row 6/)

    await user.type(screen.getByPlaceholderText('Search...'), 'Row 1')

    // Row 1, 10, 11, 12: from the first of them.
    expect(shown()[0]).toMatch(/^Row 1(?!\d)/)
  })

  it('opens on the filters it is given', () => {
    render(
      <AdvancedDataTable<Row>
        data={ROWS}
        columns={COLUMNS}
        getRowId={(row) => row.id}
        selectable={false}
        initialFilters={{ kind: ['odd'] }}
        defaultPageSize={20}
      />,
    )

    expect(shown()).toHaveLength(6)
    expect(shown().every((text) => text?.endsWith('odd'))).toBe(true)
  })

  it('opens a row on a click only where it is asked to, and names each row’s button when told how', async () => {
    const user = userEvent.setup()
    const { unmount } = render(
      <AdvancedDataTable<Row>
        data={ROWS.slice(0, 2)}
        columns={COLUMNS}
        getRowId={(row) => row.id}
        selectable={false}
        renderSubComponent={(row) => <p>Inside {row.name}</p>}
      />,
    )

    // As before: the arrow opens a row, a click on the row does not, and the arrow is named in general.
    await user.click(screen.getByText('Row 1'))
    expect(screen.queryByText('Inside Row 1')).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Expand row details' })).toHaveLength(2)
    unmount()

    render(
      <AdvancedDataTable<Row>
        data={ROWS.slice(0, 2)}
        columns={COLUMNS}
        getRowId={(row) => row.id}
        selectable={false}
        expandOnRowClick
        rowLabel={(row) => row.name}
        renderSubComponent={(row) => <p>Inside {row.name}</p>}
      />,
    )

    await user.click(screen.getByText('Row 1'))
    expect(screen.getByText('Inside Row 1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Close Row 1' })).toHaveAttribute('aria-expanded', 'true')
    // Several open at once, unless told otherwise.
    await user.click(screen.getByRole('button', { name: 'Open Row 2' }))
    expect(screen.getByText('Inside Row 1')).toBeInTheDocument()
    expect(screen.getByText('Inside Row 2')).toBeInTheDocument()
  })
})
