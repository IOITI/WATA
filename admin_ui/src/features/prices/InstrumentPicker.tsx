import { AlertTriangle, ChevronDown, Search } from 'lucide-react'
import { useMemo, useState } from 'react'

import { Panel } from '@/components/Panel'
import { Input } from '@/components/ui/form'
import type { PriceAvailability, PriceInstrument } from '@/lib/types'
import { cn } from '@/lib/utils'

import { availabilityOf, flagText, instrumentFacts, isFlagged } from './availability'
import { groupInstruments, instrumentLabel } from './chartData'

interface InstrumentPickerProps {
  instruments: readonly PriceInstrument[]
  selectedId: number | null
  onSelect: (instrument: PriceInstrument) => void
  /** What is missing from each instrument; undefined while it is being read. */
  availability?: PriceAvailability
  timeZone: string
}

function matches(instrument: PriceInstrument, words: string[]): boolean {
  const text = [
    instrumentLabel(instrument),
    instrument.symbol,
    instrument.description,
    instrument.indice,
    instrument.uic,
  ]
    .join(' ')
    .toLowerCase()
  return words.every((word) => text.includes(word))
}

/**
 * The instruments recorded in the period: the index, then the turbos by
 * direction and knock-out level. A list beside the chart on a wide screen,
 * so that going from one turbo to the next is one click; folded under the
 * name of the instrument shown on a narrow one.
 *
 * A row is two buttons: its name shows the instrument, the arrow beside it
 * opens what is known of it without leaving the chart being looked at.
 */
export function InstrumentPicker({ instruments, selectedId, onSelect, availability, timeZone }: InstrumentPickerProps) {
  const [search, setSearch] = useState('')
  const [open, setOpen] = useState(false)
  const [detailed, setDetailed] = useState<ReadonlySet<number>>(new Set())

  const groups = useMemo(() => {
    const words = search.toLowerCase().split(/\s+/).filter(Boolean)
    const { index, long, short } = groupInstruments(instruments.filter((instrument) => matches(instrument, words)))
    return [
      { title: 'Index', items: index },
      { title: 'Long turbos', items: long },
      { title: 'Short turbos', items: short },
    ].filter((group) => group.items.length > 0)
  }, [instruments, search])

  // Several indices recorded: a knock-out level alone no longer says which one.
  const severalIndices = new Set(instruments.map((instrument) => instrument.indice)).size > 1
  const selected = instruments.find((instrument) => instrument.id === selectedId)
  const severalDays = availability !== undefined && availability.from !== availability.to
  const flagged = instruments.filter((instrument) => isFlagged(availabilityOf(availability, instrument))).length

  const toggleDetails = (id: number) =>
    setDetailed((current) => {
      const next = new Set(current)
      if (!next.delete(id)) next.add(id)
      return next
    })

  return (
    <Panel
      title="Instruments"
      description={
        flagged > 0
          ? `${instruments.length} recorded in this period, ${flagged} with missing data`
          : `${instruments.length} recorded in this period`
      }
      bodyClassName="pt-3"
      className="xl:self-start"
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls="price-instruments"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between gap-3 rounded-lg border border-border/70 px-3 py-2 text-left text-sm xl:hidden"
      >
        <span className="min-w-0 truncate">{selected ? instrumentLabel(selected) : 'Choose an instrument'}</span>
        <ChevronDown aria-hidden className={cn('size-4 shrink-0 transition-transform', open && 'rotate-180')} />
      </button>

      <div id="price-instruments" className={cn('mt-3 xl:mt-0 xl:block', open ? 'block' : 'hidden')}>
        <Input
          type="search"
          aria-label="Search the instruments"
          placeholder="Level, issuer, symbol…"
          leftIcon={<Search className="size-3.5" />}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />

        <div className="mt-3 max-h-80 overflow-y-auto xl:max-h-[31rem]">
          {groups.length === 0 && (
            <p className="py-4 text-center text-sm text-muted-foreground">No instrument matches “{search}”.</p>
          )}
          {groups.map((group) => (
            <section key={group.title} aria-label={group.title} className="mb-3 last:mb-0">
              <h3 className="px-2 pb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {group.title} <span className="tabular-nums">· {group.items.length}</span>
              </h3>
              <ul>
                {group.items.map((instrument) => {
                  const isSelected = instrument.id === selectedId
                  const isDetailed = detailed.has(instrument.id)
                  const label = instrumentLabel(instrument)
                  const item = availabilityOf(availability, instrument)
                  const flag = item && isFlagged(item) ? flagText(item, timeZone, severalDays) : ''
                  const detailsId = `price-instrument-${instrument.id}`
                  return (
                    <li key={instrument.id}>
                      <div
                        className={cn(
                          'flex items-stretch rounded-lg text-sm',
                          isSelected ? 'bg-foreground/10 font-medium' : 'hover:bg-foreground/5',
                        )}
                      >
                        <button
                          type="button"
                          aria-pressed={isSelected}
                          onClick={() => {
                            onSelect(instrument)
                            setOpen(false)
                          }}
                          className="flex min-w-0 flex-1 items-baseline justify-between gap-3 rounded-l-lg py-1.5 pl-2 text-left"
                        >
                          <span className="flex min-w-0 items-center gap-1.5">
                            <span className="min-w-0 truncate">{label}</span>
                            {flag && (
                              // Told by its shape and by its text, not by its colour alone.
                              <span className="shrink-0 text-warning" title={flag}>
                                <AlertTriangle aria-hidden className="size-3.5" />
                                <span className="sr-only">{flag}</span>
                              </span>
                            )}
                          </span>
                          {/* On the grey of the selected row the secondary text needs one step more, in light. */}
                          <span
                            className={cn(
                              'shrink-0 text-xs font-normal text-muted-foreground',
                              isSelected && 'text-foreground/75 dark:text-muted-foreground',
                            )}
                          >
                            {severalIndices && instrument.indice ? `${instrument.indice} · ` : ''}
                            {instrument.symbol ?? instrument.uic}
                          </span>
                        </button>
                        <button
                          type="button"
                          aria-expanded={isDetailed}
                          aria-controls={detailsId}
                          aria-label={`Details of ${label}`}
                          onClick={() => toggleDetails(instrument.id)}
                          className="flex shrink-0 items-center rounded-r-lg px-2 text-muted-foreground hover:text-foreground"
                        >
                          <ChevronDown aria-hidden className={cn('size-3.5 transition-transform', isDetailed && 'rotate-180')} />
                        </button>
                      </div>
                      {isDetailed && (
                        <dl id={detailsId} className="mb-1.5 space-y-1 px-2 pb-1.5 pt-1 text-xs">
                          {instrumentFacts(instrument, availability, timeZone).map((fact) => (
                            <div key={fact.label} className="flex gap-2">
                              <dt className="w-[5.25rem] shrink-0 text-muted-foreground">{fact.label}</dt>
                              <dd className={cn('min-w-0 tabular-nums', fact.warning && 'text-warning')}>{fact.value}</dd>
                            </div>
                          ))}
                          {availability === undefined && (
                            <div className="text-muted-foreground">Reading what was recorded of it…</div>
                          )}
                        </dl>
                      )}
                    </li>
                  )
                })}
              </ul>
            </section>
          ))}
        </div>
      </div>
    </Panel>
  )
}
