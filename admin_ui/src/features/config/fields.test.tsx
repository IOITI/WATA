import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import type { JsonObject } from '@/lib/types'

import { getAt, setAt } from './configModel'
import { DraftProvider, NumberField } from './fields'
import { RowsField } from './listFields'

const PATH = ['trade', 'rules', 0, 'max_trades_per_day'] as const

function Form({ onInvalid, onDraft }: { onInvalid: (id: string, invalid: boolean) => void; onDraft: (draft: JsonObject) => void }) {
  const [draft, setDraft] = useState<JsonObject>({
    trade: { rules: [{ max_trades_per_day: 8 }] },
    periods: [{ label: 'Morning', scale_percent: 50 }],
  })
  onDraft(draft)
  return (
    <DraftProvider
      value={{
        draft,
        disabled: false,
        reportInvalid: onInvalid,
        update: (path, value) => setDraft((current) => setAt(current, path, value) as JsonObject),
      }}
    >
      <NumberField label="Trades per day" path={PATH} />
      <RowsField
        label="Periods"
        path={['periods']}
        columns={[
          { key: 'label', label: 'Name', kind: 'text' },
          { key: 'scale_percent', label: 'Size (%)', kind: 'number' },
        ]}
        newRow={() => ({ label: '', scale_percent: 100 })}
      />
    </DraftProvider>
  )
}

function renderForm() {
  const reported = new Map<string, boolean>()
  let draft: JsonObject = {}
  const onInvalid = vi.fn((id: string, invalid: boolean) => {
    reported.set(id, invalid)
  })
  render(<Form onInvalid={onInvalid} onDraft={(next) => (draft = next)} />)
  return {
    invalidCount: () => [...reported.values()].filter(Boolean).length,
    value: () => getAt(draft, PATH),
    period: () => getAt(draft, ['periods', 0, 'scale_percent']),
  }
}

describe('number fields of the configuration', () => {
  it('says so when the field is emptied, and that the value has not changed', async () => {
    const user = userEvent.setup()
    const form = renderForm()
    const field = screen.getByLabelText('Trades per day')

    await user.clear(field)

    expect(await screen.findByRole('alert')).toHaveTextContent('A number is needed here. The value is still 8.')
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(form.value()).toBe(8)
    expect(form.invalidCount()).toBe(1)
  })

  it('says so for something that is not a number', async () => {
    const user = userEvent.setup()
    const form = renderForm()
    const field = screen.getByLabelText('Trades per day')

    await user.clear(field)
    await user.type(field, '6x')

    expect(await screen.findByRole('alert')).toHaveTextContent('Not a number. The value is still 6.')
    expect(form.value()).toBe(6)
    expect(form.invalidCount()).toBe(1)
  })

  it('is no longer reported once a number is typed', async () => {
    const user = userEvent.setup()
    const form = renderForm()
    const field = screen.getByLabelText('Trades per day')

    await user.clear(field)
    await user.type(field, '6')

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(field).toHaveAttribute('aria-invalid', 'false')
    expect(form.value()).toBe(6)
    expect(form.invalidCount()).toBe(0)
  })

  it('reports a number cell of a row editor the same way', async () => {
    const user = userEvent.setup()
    const form = renderForm()
    const cell = screen.getByLabelText('Size (%), row 1')

    await user.clear(cell)

    expect(cell).toHaveAttribute('aria-invalid', 'true')
    expect(cell).toHaveAttribute('title', 'Not a number: the value is still 50')
    expect(form.period()).toBe(50)
    expect(form.invalidCount()).toBe(1)

    await user.type(cell, '40')
    expect(form.period()).toBe(40)
    expect(form.invalidCount()).toBe(0)
  })

  it('stops being reported when the field leaves the screen', async () => {
    const user = userEvent.setup()
    const reported = new Map<string, boolean>()
    const { unmount } = render(
      <Form onInvalid={(id, invalid) => reported.set(id, invalid)} onDraft={() => {}} />,
    )
    await user.clear(screen.getByLabelText('Trades per day'))
    expect([...reported.values()].filter(Boolean)).toHaveLength(1)

    unmount()

    expect([...reported.values()].filter(Boolean)).toHaveLength(0)
  })
})
