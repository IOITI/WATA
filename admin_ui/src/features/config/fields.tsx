import { useId, useState } from 'react'
import type { ReactNode } from 'react'

import { FormControl, FormDescription, FormItem, FormLabel, FormMessage, Input } from '@/components/ui/form'
import { StretchSwitch } from '@/components/ui/stretch-switch'

import { getAt } from './configModel'
import type { ConfigPath } from './configModel'
import { DraftContext, isNumberText, useDraft, useReportInvalid } from './draft-context'
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

/**
 * A number of the configuration. ``optional``: left empty, it is not set (saved as null, which the
 * services read as the setting's default), and an empty field does not hold the form back.
 */
export function NumberField({ label, path, hint, unit, optional = false }: FieldProps & { unit?: string; optional?: boolean }) {
  const { draft, update, disabled } = useDraft()
  const id = useId()
  const current = path ? getAt(draft, path) : undefined
  // What is being typed, which is not always a number yet ("-", "1.").
  const [text, setText] = useState(typeof current === 'number' ? String(current) : '')
  const unset = optional && text.trim() === ''
  const invalid = !!path && !unset && !isNumberText(text)
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
          disabled={disabled || !path}
          error={invalid}
          aria-invalid={invalid}
          aria-describedby={invalid ? `${id}-error` : undefined}
          onChange={(event) => {
            const next = event.target.value
            setText(next)
            if (path && optional && next.trim() === '') update(path, null)
            else if (path && isNumberText(next)) update(path, Number(next))
          }}
        />
      </FormControl>
      {invalid && (
        // The draft still holds the last number typed here: say so, or the form shows one thing and saves another.
        <FormMessage id={`${id}-error`}>
          {text.trim() === '' ? 'A number is needed here.' : 'Not a number.'} The value is still{' '}
          {typeof current === 'number' ? current : 'unset'}.
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
