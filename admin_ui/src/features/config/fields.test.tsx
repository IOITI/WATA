import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import type { JsonObject } from '@/lib/types'

import { getAt, setAt } from './configModel'
import { DraftProvider, NumberField, SwitchField } from './fields'
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
    expect(cell).toHaveAccessibleDescription('Not a number: the value is still 50')
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

function OptionalForm({ onInvalid, onDraft }: { onInvalid: (id: string, invalid: boolean) => void; onDraft: (draft: JsonObject) => void }) {
  // A key the configuration may not have (reporting.signal_silence_minutes, a rule's flatten_minutes_before).
  const [draft, setDraft] = useState<JsonObject>({ reporting: { milestones_eur: [100000] } })
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
      <NumberField label="Say when no signal came for" path={['reporting', 'signal_silence_minutes']} optional />
    </DraftProvider>
  )
}

describe('an optional number of the configuration', () => {
  it('may stay empty without holding the form back, and once emptied is saved as not set', async () => {
    const user = userEvent.setup()
    const reported = new Map<string, boolean>()
    let draft: JsonObject = {}
    render(<OptionalForm onInvalid={(id, invalid) => reported.set(id, invalid)} onDraft={(next) => (draft = next)} />)
    const field = screen.getByLabelText('Say when no signal came for')

    expect([...reported.values()].filter(Boolean)).toEqual([])
    expect(getAt(draft, ['reporting', 'signal_silence_minutes'])).toBeUndefined()

    await user.type(field, '45')
    expect(getAt(draft, ['reporting', 'signal_silence_minutes'])).toBe(45)

    await user.clear(field)
    expect(getAt(draft, ['reporting', 'signal_silence_minutes'])).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
    expect([...reported.values()].filter(Boolean)).toEqual([])
  })
})

function SwitchForm({ onDraft }: { onDraft: (draft: JsonObject) => void }) {
  // position_management.wait_for_fresh_quote: on while the configuration does not have it.
  const [draft, setDraft] = useState<JsonObject>({ trade: { config: { position_management: {} } } })
  onDraft(draft)
  return (
    <DraftProvider
      value={{
        draft,
        disabled: false,
        reportInvalid: () => {},
        update: (path, value) => setDraft((current) => setAt(current, path, value) as JsonObject),
      }}
    >
      <SwitchField
        label="Wait for a quote newer than the purchase"
        path={['trade', 'config', 'position_management', 'wait_for_fresh_quote']}
        whenAbsent
      />
      <SwitchField label="Broker-side protective stop" path={['trade', 'config', 'position_management', 'protective_stop', 'enabled']} />
    </DraftProvider>
  )
}

describe('a switch of the configuration', () => {
  it('shows what the services do while its key is not there, and writes it once switched', async () => {
    const user = userEvent.setup()
    let draft: JsonObject = {}
    render(<SwitchForm onDraft={(next) => (draft = next)} />)
    const fresh = screen.getByRole('switch', { name: 'Wait for a quote newer than the purchase' })

    expect(fresh).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('switch', { name: 'Broker-side protective stop' })).toHaveAttribute('aria-checked', 'false')
    expect(getAt(draft, ['trade', 'config', 'position_management', 'wait_for_fresh_quote'])).toBeUndefined()

    await user.click(fresh)
    expect(getAt(draft, ['trade', 'config', 'position_management', 'wait_for_fresh_quote'])).toBe(false)
    expect(fresh).toHaveAttribute('aria-checked', 'false')
  })
})
