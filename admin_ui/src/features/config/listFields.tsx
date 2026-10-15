import { Plus, Trash2 } from 'lucide-react'
import { useId, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { DatePicker } from '@/components/ui/date-picker'
import { FormDescription, FormLabel, Input } from '@/components/ui/form'
import type { JsonObject, JsonValue } from '@/lib/types'

import { getAt } from './configModel'
import type { ConfigPath } from './configModel'
import { fromConfigDate, sortConfigDates, toConfigDate } from './dates'
import { isNumberText, useDraft, useReportInvalid } from './draft-context'

export interface RowColumn {
  key: string
  label: string
  kind: 'text' | 'number'
  /** Relative width of the column in the grid. */
  width?: string
}

interface CellProps {
  ariaLabel: string
  kind: RowColumn['kind']
  value: JsonValue | undefined
  disabled: boolean
  onChange: (value: JsonValue) => void
}

/** One input of a row. A number being typed ("-", "1.") is kept as text until it parses. */
function Cell({ ariaLabel, kind, value, disabled, onChange }: CellProps) {
  const [text, setText] = useState(value === undefined || value === null ? '' : String(value))
  const invalid = kind === 'number' && !isNumberText(text)
  useReportInvalid(invalid)

  return (
    <Input
      aria-label={ariaLabel}
      inputMode={kind === 'number' ? 'decimal' : undefined}
      value={text}
      disabled={disabled}
      error={invalid}
      aria-invalid={invalid}
      // No room for a message under a cell: the reason is on the field, the count in the bar under the form.
      title={invalid ? `Not a number: the value is still ${String(value ?? 'unset')}` : undefined}
      onChange={(event) => {
        const next = event.target.value
        setText(next)
        if (kind === 'text') onChange(next)
        else if (isNumberText(next)) onChange(Number(next))
      }}
    />
  )
}

interface RowsEditorProps {
  label: string
  hint?: string
  columns: RowColumn[]
  rows: JsonObject[]
  disabled: boolean
  newRow: () => JsonObject
  onChange: (rows: JsonObject[]) => void
}

function RowsEditor({ label, hint, columns, rows, disabled, newRow, onChange }: RowsEditorProps) {
  // Bumped when a row is added or removed, so every input shows the value of its new row.
  const [layout, setLayout] = useState(0)
  const template = `${columns.map((column) => column.width ?? '1fr').join(' ')} auto`

  const replaceRows = (next: JsonObject[]) => {
    setLayout((version) => version + 1)
    onChange(next)
  }

  return (
    <div className="space-y-2">
      <FormLabel>{label}</FormLabel>
      <div className="overflow-x-auto">
        <div className="min-w-[36rem] space-y-2">
          <div className="grid items-center gap-2 text-xs text-muted-foreground" style={{ gridTemplateColumns: template }}>
            {columns.map((column) => (
              <span key={column.key}>{column.label}</span>
            ))}
            <span className="w-8" />
          </div>
          {rows.map((row, index) => (
            <div
              key={`${layout}-${index}`}
              className="grid items-center gap-2"
              style={{ gridTemplateColumns: template }}
            >
              {columns.map((column) => (
                <Cell
                  key={column.key}
                  ariaLabel={`${column.label}, row ${index + 1}`}
                  kind={column.kind}
                  value={row[column.key]}
                  disabled={disabled}
                  onChange={(value) =>
                    onChange(rows.map((other, at) => (at === index ? { ...other, [column.key]: value } : other)))
                  }
                />
              ))}
              <button
                type="button"
                aria-label={`Remove row ${index + 1}`}
                disabled={disabled}
                onClick={() => replaceRows(rows.filter((_, at) => at !== index))}
                className="grid size-8 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-surface-hover hover:text-foreground focus-ring disabled:opacity-30"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      </div>
      {rows.length === 0 && <p className="text-xs text-muted-foreground">No row.</p>}
      <Button
        variant="outline"
        size="sm"
        leftIcon={<Plus className="size-3.5" />}
        disabled={disabled}
        onClick={() => replaceRows([...rows, newRow()])}
      >
        Add a row
      </Button>
      {hint && <FormDescription>{hint}</FormDescription>}
    </div>
  )
}

interface RowsFieldProps {
  label: string
  path: ConfigPath
  columns: RowColumn[]
  newRow: () => JsonObject
  hint?: string
}

/** A list of objects of the config (sizing periods, scaling rules...), one row each. */
export function RowsField({ label, path, columns, newRow, hint }: RowsFieldProps) {
  const { draft, update, disabled } = useDraft()
  const current = getAt(draft, path)
  const rows = Array.isArray(current)
    ? current.filter((row): row is JsonObject => typeof row === 'object' && row !== null && !Array.isArray(row))
    : []

  return (
    <RowsEditor
      label={label}
      hint={hint}
      columns={columns}
      rows={rows}
      disabled={disabled}
      newRow={newRow}
      onChange={(next) => update(path, next)}
    />
  )
}

interface MapFieldProps {
  label: string
  path: ConfigPath
  keyLabel: string
  valueLabel: string
  hint?: string
}

/** An object of the config edited as name/value rows (the allowed indices and their Saxo identifiers). */
export function MapField({ label, path, keyLabel, valueLabel, hint }: MapFieldProps) {
  const { draft, update, disabled } = useDraft()
  const current = getAt(draft, path)
  // Rows are kept here while they are being typed: a row without a name yet has no place in the object.
  const [rows, setRows] = useState<JsonObject[]>(() =>
    typeof current === 'object' && current !== null && !Array.isArray(current)
      ? Object.entries(current).map(([name, value]) => ({ name, value }))
      : [],
  )

  const commit = (next: JsonObject[]) => {
    setRows(next)
    const original = typeof current === 'object' && current !== null && !Array.isArray(current) ? current : {}
    const entries = next
      .map((row) => [String(row.name ?? '').trim(), String(row.value ?? '').trim()] as const)
      .filter(([name]) => name !== '')
      // A value left as it was keeps its type (a number stays a number).
      .map(([name, value]) => [name, String(original[name]) === value ? original[name] : value] as const)
    update(path, Object.fromEntries(entries))
  }

  return (
    <RowsEditor
      label={label}
      hint={hint}
      columns={[
        { key: 'name', label: keyLabel, kind: 'text' },
        { key: 'value', label: valueLabel, kind: 'text' },
      ]}
      rows={rows}
      disabled={disabled}
      newRow={() => ({ name: '', value: '' })}
      onChange={commit}
    />
  )
}

function startOfToday(): Date {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return today
}

interface DateListFieldProps {
  label: string
  path: ConfigPath
  hint?: string
}

/** The market-closed dates (DD/MM/YYYY in the config), picked on a calendar and listed in order. */
export function DateListField({ label, path, hint }: DateListFieldProps) {
  const { draft, update, disabled } = useDraft()
  const id = useId()
  const current = getAt(draft, path)
  const dates = sortConfigDates(Array.isArray(current) ? current.filter((date) => typeof date === 'string') : [])
  // Read once when the field appears: "past" does not need to follow the clock.
  const [today] = useState(startOfToday)
  const past = dates.filter((date) => {
    const parsed = fromConfigDate(date)
    return parsed !== null && parsed < today
  })

  return (
    <div className="space-y-3">
      <FormLabel id={id}>{label}</FormLabel>
      <ul aria-labelledby={id} className="flex flex-wrap gap-2">
        {dates.map((date) => (
          <li key={date}>
            <Badge
              variant="outline"
              className={fromConfigDate(date) === null ? 'border-rose-500/50' : undefined}
              onRemove={disabled ? undefined : () => update(path, dates.filter((other) => other !== date))}
            >
              {date}
            </Badge>
          </li>
        ))}
        {dates.length === 0 && <li className="text-xs text-muted-foreground">No date.</li>}
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        <DatePicker
          // Emptied after each pick, ready for the next date.
          value={null}
          placeholder="Add a date"
          locale="en-GB"
          weekStartsOn={1}
          disabled={disabled}
          onChange={(date) => {
            if (!date) return
            const added = toConfigDate(date)
            if (!dates.includes(added)) update(path, sortConfigDates([...dates, added]))
          }}
        />
        {past.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            disabled={disabled}
            onClick={() => update(path, dates.filter((date) => !past.includes(date)))}
          >
            Remove the {past.length} past date{past.length === 1 ? '' : 's'}
          </Button>
        )}
      </div>
      {hint && <FormDescription>{hint}</FormDescription>}
    </div>
  )
}
