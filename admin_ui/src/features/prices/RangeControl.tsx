import { Minus, Plus } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/form'

import { sizeOf, stepRange, ticksOf } from './chartData'

export interface BuiltRange {
  /** One step of the prices: what 1R is. */
  tick: number
  decimals: number
  /** The range in ticks (R) and as a price move. */
  ticks: number
  size: number
}

interface RangeControlProps {
  /** What the bars on the chart were built with, or what was asked when it was refused; undefined until known. */
  built?: BuiltRange
  /** What a price move is counted in: the instrument's currency, or points for an index. */
  unit: string
  /** The range is left to the server. */
  auto: boolean
  /** The server refused the range asked for, in its own words. */
  refusal?: string
  onChange: (range: number | 'auto') => void
}

type FieldName = 'ticks' | 'size'

/**
 * The range of a range bar, two ways that are the same thing: in ticks, which
 * is TradingView's "R" (60R is sixty ticks), and as the price move that makes.
 * Typing in one fills the other.
 *
 * Nothing is rebuilt at each key: a week of range bars takes a second or two,
 * and "1" on the way to "150" would ask for a hundred times too many of them.
 * Enter, the Apply button, or leaving the field does it. The button is what a
 * phone has: its number pad has no Enter.
 */
export function RangeControl({ built, unit, auto, refusal, onChange }: RangeControlProps) {
  // What is being typed. null: the fields show what the chart was built with.
  const [draft, setDraft] = useState<{ ticks: string; size: string; edited: FieldName } | null>(null)
  const [invalid, setInvalid] = useState<FieldName | null>(null)
  // What was last sent, so that Enter then leaving the field does not send it twice.
  const [sent, setSent] = useState<number | null>(null)

  // A new answer from the server replaces what was typed: it is now what the chart shows.
  // The fields themselves stay (and the focus with them); only what they hold starts again.
  const answer = built ? `${built.tick}|${built.ticks}` : ''
  const [seen, setSeen] = useState(answer)
  if (answer !== seen) {
    setSeen(answer)
    setDraft(null)
    setInvalid(null)
    setSent(null)
  }

  const tick = built?.tick ?? 0.01
  const decimals = built?.decimals ?? 2
  const ticksText = draft?.ticks ?? (built ? String(built.ticks) : '')
  const sizeText = draft?.size ?? (built ? built.size.toFixed(decimals) : '')
  // The range typed, in ticks, read from the field that was typed in: an unusable price
  // must not pass for the ticks that were there before it.
  const typedSize = number(sizeText)
  const typed =
    draft?.edited === 'size'
      ? Number.isFinite(typedSize) && typedSize > 0
        ? ticksOf(typedSize, tick)
        : Number.NaN
      : number(ticksText)
  const usable = Number.isInteger(typed) && typed >= 1
  const differs = built !== undefined && draft !== null && (!usable || typed !== built.ticks)

  const send = (ticks: number) => {
    if (!built || ticks === built.ticks || ticks === sent) return
    setSent(ticks)
    onChange(sizeOf(ticks, tick, decimals))
  }
  const typeTicks = (text: string) => {
    const ticks = number(text)
    const size = Number.isInteger(ticks) && ticks >= 1 ? sizeOf(ticks, tick, decimals).toFixed(decimals) : sizeText
    setDraft({ ticks: text, size, edited: 'ticks' })
    setInvalid(null)
  }
  const typeSize = (text: string) => {
    const size = number(text)
    const ticks = Number.isFinite(size) && size > 0 ? String(ticksOf(size, tick)) : ticksText
    setDraft({ ticks, size: text, edited: 'size' })
    setInvalid(null)
  }
  const apply = (event?: FormEvent) => {
    event?.preventDefault()
    if (!draft) return
    if (!usable) {
      setInvalid(draft.edited)
      return
    }
    // What was typed as a price is brought to a whole number of ticks.
    setDraft({ ...draft, ticks: String(typed), size: sizeOf(typed, tick, decimals).toFixed(decimals) })
    send(typed)
  }
  // Leaving a field with something usable in it applies it; with something that is not, says nothing yet.
  const leave = () => {
    if (draft && usable) apply()
  }
  const step = (direction: 1 | -1) => {
    const next = stepRange(usable ? typed : (built?.ticks ?? 1), direction)
    setDraft({ ticks: String(next), size: sizeOf(next, tick, decimals).toFixed(decimals), edited: 'ticks' })
    setInvalid(null)
    send(next)
  }

  const waiting = built === undefined
  const shownTicks = usable ? typed : (built?.ticks ?? 1)

  return (
    <form onSubmit={apply} noValidate className="flex flex-wrap items-center gap-x-2 gap-y-2 text-xs">
      <span className="text-muted-foreground">Range</span>
      <span className="w-20">
        <Input
          className="h-8 px-2.5 text-right tabular-nums"
          aria-label="Range in ticks (R)"
          inputMode="numeric"
          enterKeyHint="done"
          autoComplete="off"
          disabled={waiting}
          aria-busy={waiting}
          value={ticksText}
          error={invalid === 'ticks'}
          aria-invalid={invalid === 'ticks'}
          onChange={(event) => typeTicks(event.target.value)}
          onBlur={leave}
        />
      </span>
      <span className="text-muted-foreground">R =</span>
      <span className="w-24">
        <Input
          className="h-8 px-2.5 text-right tabular-nums"
          aria-label={`Range as a price move, in ${unit}`}
          inputMode="decimal"
          enterKeyHint="done"
          autoComplete="off"
          disabled={waiting}
          aria-busy={waiting}
          value={sizeText}
          error={invalid === 'size'}
          aria-invalid={invalid === 'size'}
          onChange={(event) => typeSize(event.target.value)}
          onBlur={leave}
        />
      </span>
      <span className="text-muted-foreground">{unit}</span>

      <span role="group" aria-label="Step the range" className="flex gap-1">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="px-2"
          aria-label="Smaller range"
          disabled={waiting || shownTicks <= 1}
          onClick={() => step(-1)}
        >
          <Minus aria-hidden className="size-3.5" />
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="px-2"
          aria-label="Larger range"
          disabled={waiting}
          onClick={() => step(1)}
        >
          <Plus aria-hidden className="size-3.5" />
        </Button>
      </span>
      <Button
        type="button"
        variant={auto ? 'primary' : 'outline'}
        size="sm"
        aria-pressed={auto}
        title="A range chosen for the period"
        onClick={() => onChange('auto')}
      >
        Auto
      </Button>
      {differs && (
        <Button type="submit" variant="primary" size="sm">
          Apply
        </Button>
      )}
      {built && (
        <span className="text-muted-foreground">
          1R = {tick.toFixed(decimals)} {unit}
        </span>
      )}

      {invalid === 'ticks' && (
        <p role="alert" className="basis-full text-negative">
          A whole number of ticks, 1 at least.
        </p>
      )}
      {invalid === 'size' && (
        <p role="alert" className="basis-full text-negative">
          At least {tick.toFixed(decimals)} {unit}, which is one tick.
        </p>
      )}
      {refusal && !invalid && (
        <p role="alert" className="basis-full text-negative">
          {refusal}
        </p>
      )}
    </form>
  )
}

function number(text: string): number {
  // A comma for the decimals is what half of Europe types.
  return text.trim() === '' ? Number.NaN : Number(text.trim().replace(',', '.'))
}
