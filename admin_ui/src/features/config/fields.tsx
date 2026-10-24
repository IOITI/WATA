import { useId, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { Combobox } from '@/components/ui/combobox'
import { FormControl, FormDescription, FormItem, FormLabel, FormMessage, Input } from '@/components/ui/form'
import { SpringSelect } from '@/components/ui/spring-select'
import { StretchSwitch } from '@/components/ui/stretch-switch'

import { getAt, toggled } from './configModel'
import { knownZones, zoneLabel } from './zones'
import type { ConfigPath } from './configModel'
import { DraftContext, isNumberText, numberProblem, useDraft, useReportInvalid } from './draft-context'
import type { DraftContextValue } from './draft-context'

export function DraftProvider({ value, children }: { value: DraftContextValue; children: ReactNode }) {
  return <DraftContext.Provider value={value}>{children}</DraftContext.Provider>
}

interface FieldProps {
  label: string
  /** null when the rule this field belongs to is not in the configuration. */
  path: ConfigPath | null
  hint?: string
}

interface NumberFieldProps extends FieldProps {
  unit?: string
  optional?: boolean
  /** Bounds the value must keep to, and whether it is a whole number. */
  minimum?: number | null
  maximum?: number | null
  integer?: boolean
  /** Said inside the field while it is empty: what the services use then. */
  placeholder?: string
}

/**
 * A number of the configuration. ``optional``: left empty, it is not set (saved as null, which the
 * services read as the setting's default), and an empty field does not hold the form back.
 */
export function NumberField({
  label, path, hint, unit, optional = false, minimum = null, maximum = null, integer = false, placeholder,
}: NumberFieldProps) {
  const { draft, update, disabled } = useDraft()
  const id = useId()
  const current = path ? getAt(draft, path) : undefined
  // What is being typed, which is not always a number yet ("-", "1.").
  const [text, setText] = useState(typeof current === 'number' ? String(current) : '')
  const problem = path ? numberProblem(text, { optional, minimum, maximum, integer }) : null
  const invalid = problem !== null
  useReportInvalid(invalid)

  return (
    <FormItem>
      <FormLabel htmlFor={id}>
        {label}
        {unit && <span className="ml-1 font-normal text-muted-foreground">({unit})</span>}
      </FormLabel>
      <FormControl>
        <Input
          id={id}
          inputMode="decimal"
          value={text}
          placeholder={placeholder}
          disabled={disabled || !path}
          error={invalid}
          aria-invalid={invalid}
          aria-describedby={invalid ? `${id}-error` : undefined}
          onChange={(event) => {
            const next = event.target.value
            setText(next)
            if (path && optional && next.trim() === '') update(path, null)
            else if (path && numberProblem(next, { optional, minimum, maximum, integer }) === null) update(path, Number(next))
          }}
        />
      </FormControl>
      {invalid && (
        // The draft still holds the last number typed here: say so, or the form shows one thing and saves another.
        <FormMessage id={`${id}-error`}>
          {problem} The value is still {typeof current === 'number' ? current : 'unset'}.
        </FormMessage>
      )}
      {hint && <FormDescription>{hint}</FormDescription>}
    </FormItem>
  )
}

export function TextField({ label, path, hint, placeholder }: FieldProps & { placeholder?: string }) {
  const { draft, update, disabled } = useDraft()
  const id = useId()
  const current = path ? getAt(draft, path) : undefined

  return (
    <FormItem>
      <FormLabel htmlFor={id}>{label}</FormLabel>
      <FormControl>
        <Input
          id={id}
          value={typeof current === 'string' ? current : ''}
          placeholder={placeholder}
          disabled={disabled || !path}
          onChange={(event) => path && update(path, event.target.value)}
        />
      </FormControl>
      {hint && <FormDescription>{hint}</FormDescription>}
    </FormItem>
  )
}

/** A switch of the configuration. ``whenAbsent``: what the services do while the key is not there (off unless said). */
export function SwitchField({ label, path, hint, whenAbsent = false }: FieldProps & { whenAbsent?: boolean }) {
  const { draft, update, disabled } = useDraft()
  const current = path ? getAt(draft, path) : undefined

  return (
    <StretchSwitch
      label={label}
      description={hint}
      checked={current === undefined || current === null ? whenAbsent : current === true}
      disabled={disabled || !path}
      onChange={(checked) => path && update(path, checked)}
    />
  )
}

const CLOCK = /^([01][0-9]|2[0-3]):[0-5][0-9]$/

/** A time of day, HH:MM with two digits for the hour (the scheduler reads it so). */
export function TimeField({ label, path, hint, optional = false, placeholder }: FieldProps & { optional?: boolean; placeholder?: string }) {
  const { draft, update, disabled } = useDraft()
  const id = useId()
  const current = path ? getAt(draft, path) : undefined
  const [text, setText] = useState(typeof current === 'string' ? current : '')
  const empty = text.trim() === ''
  const invalid = !!path && !(empty && optional) && !CLOCK.test(text.trim())
  useReportInvalid(invalid)

  return (
    <FormItem>
      <FormLabel htmlFor={id}>{label}</FormLabel>
      <FormControl>
        <Input
          id={id}
          value={text}
          placeholder={placeholder ?? 'HH:MM'}
          disabled={disabled || !path}
          error={invalid}
          aria-invalid={invalid}
          aria-describedby={invalid ? `${id}-error` : undefined}
          onChange={(event) => {
            const next = event.target.value
            setText(next)
            if (!path) return
            if (optional && next.trim() === '') update(path, null)
            else if (CLOCK.test(next.trim())) update(path, next.trim())
          }}
        />
      </FormControl>
      {invalid && (
        <FormMessage id={`${id}-error`}>
          A time of day is needed, written HH:MM (09:30). The value is still {typeof current === 'string' ? current : 'unset'}.
        </FormMessage>
      )}
      {hint && <FormDescription>{hint}</FormDescription>}
    </FormItem>
  )
}

/** One of a few values, each said. */
export function SelectField({ label, path, hint, choices }: FieldProps & { choices: string[] }) {
  const { draft, update, disabled } = useDraft()
  const current = path ? getAt(draft, path) : undefined

  return (
    <FormItem>
      <SpringSelect
        label={label}
        options={choices.map((choice) => ({ value: choice, label: choice }))}
        value={typeof current === 'string' ? current : undefined}
        placeholder="Not set"
        disabled={disabled || !path}
        onChange={(choice) => path && update(path, choice)}
      />
      {hint && <FormDescription>{hint}</FormDescription>}
    </FormItem>
  )
}

/** A timezone, picked among those the browser knows; ``unsetLabel``: the option that takes the key out. */
export function TimezoneField({ label, path, hint, unsetLabel }: FieldProps & { unsetLabel?: string }) {
  const { draft, update, disabled } = useDraft()
  const id = useId()
  const current = path ? getAt(draft, path) : undefined
  const unset = '__unset__'
  const options = useMemo(() => {
    const zones = knownZones().map((zone) => ({ value: zone, label: zone, keywords: [zoneLabel(zone)] }))
    return unsetLabel ? [{ value: unset, label: unsetLabel, keywords: [] }, ...zones] : zones
  }, [unsetLabel])

  return (
    <FormItem>
      {/* A label beside the field, not around it: a label passes an option's click on to its field. */}
      <label htmlFor={id} className="text-xs text-text-secondary">
        {label}
      </label>
      <Combobox
        id={id}
        className="w-full"
        value={typeof current === 'string' && current !== '' ? current : unsetLabel ? unset : null}
        options={options}
        disabled={disabled || !path}
        placeholder="Choose a timezone"
        searchPlaceholder="A city or a zone…"
        emptyText="No timezone of that name."
        onValueChange={(value) => {
          if (!path || !value) return
          update(path, value === unset ? null : value)
        }}
      />
      {hint && <FormDescription>{hint}</FormDescription>}
    </FormItem>
  )
}

/** Some of a fixed list of values, one switch each, kept in the list's order. */
export function ChoicesField({ label, path, hint, choices }: FieldProps & { choices: string[] }) {
  const { draft, update, disabled } = useDraft()
  const current = path ? getAt(draft, path) : undefined
  const chosen = Array.isArray(current) ? current : choices

  return (
    <div className="space-y-2">
      <p className="text-xs text-text-secondary">{label}</p>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        {choices.map((choice) => (
          <StretchSwitch
            key={choice}
            label={choice}
            checked={chosen.includes(choice)}
            disabled={disabled || !path || (chosen.length === 1 && chosen.includes(choice))}
            onChange={(on) => path && update(path, toggled(choices, chosen, choice, on))}
          />
        ))}
      </div>
      {hint && <FormDescription>{hint}</FormDescription>}
    </div>
  )
}

/** A list of numbers, written with commas between them. */
export function NumbersField({ label, path, hint, unit }: FieldProps & { unit?: string }) {
  const { draft, update, disabled } = useDraft()
  const id = useId()
  const current = path ? getAt(draft, path) : undefined
  const [text, setText] = useState(Array.isArray(current) ? current.join(', ') : '')
  const parts = text.split(',').map((part) => part.trim()).filter(Boolean)
  const invalid = !!path && parts.some((part) => !isNumberText(part))
  useReportInvalid(invalid)

  return (
    <FormItem>
      <FormLabel htmlFor={id}>
        {label}
        {unit && <span className="ml-1 font-normal text-muted-foreground">({unit})</span>}
      </FormLabel>
      <FormControl>
        <Input
          id={id}
          value={text}
          disabled={disabled || !path}
          error={invalid}
          aria-invalid={invalid}
          onChange={(event) => {
            const next = event.target.value
            setText(next)
            const values = next.split(',').map((part) => part.trim()).filter(Boolean)
            if (path && values.every(isNumberText)) update(path, values.map(Number))
          }}
        />
      </FormControl>
      {invalid && <FormMessage>Numbers only, with commas between them.</FormMessage>}
      {hint && <FormDescription>{hint}</FormDescription>}
    </FormItem>
  )
}

export function FieldGrid({ children }: { children: ReactNode }) {
  return <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{children}</div>
}

export function MissingRule({ ruleType }: { ruleType: string }) {
  return (
    <p className="text-xs text-muted-foreground">
      The rule <code>{ruleType}</code> is not in the configuration. Add it in the JSON tab to edit it here.
    </p>
  )
}
