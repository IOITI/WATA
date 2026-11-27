// Every day the trader takes no entry, or fewer (GET /api/market-calendar): the US market's holidays and early
// closes, config.json's closed dates and the windows around US releases. The year at a glance, then each day with
// what WATA does then. Nothing here changes anything: each kind says where it is changed.

import { useQuery } from '@tanstack/react-query'
import {
  Alert,
  AlertDescription,
  Badge,
  cn,
  Hint,
  KpiTile,
  PageHeader,
  Panel,
  QueryState,
  Switch,
  Tabs,
} from 'ioiti-ui'
import { RouterLink } from 'ioiti-ui/react-router'
import { useEffect, useMemo } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router'

import { configKeyHref } from '@/features/config/catalog'
import type { BlockedDay, DayEffect, MarketCalendar } from '@/lib/types'

import {
  blockLabel,
  blockName,
  blockSentence,
  blockTone,
  chosenYear,
  countByEffect,
  dayAnchor,
  dayLabel,
  dayOffWhat,
  daysOfYear,
  EFFECT_LABELS,
  monthsOf,
  nextDayOff,
  nextWindow,
  relativeLabel,
  todayTile,
  yearsOf,
  zonesLine,
} from './marketCalendarModel'
import type { Month } from './marketCalendarModel'
import { MARKET_CALENDAR_QUERY } from './marketCalendarQuery'

const EFFECT_CELL: Record<DayEffect, string> = {
  closed: 'bg-negative/15 text-negative font-semibold',
  early_close: 'bg-warning/15 text-warning font-semibold',
  window: 'bg-info/15 text-info',
}

const WEEK_HEADS = ['M', 'T', 'W', 'T', 'F', 'S', 'S']

/** Scrolls to the day the address names (#d-…) once the days are drawn, and marks it a moment. */
function useScrollToDay(ready: boolean): void {
  const { hash } = useLocation()
  useEffect(() => {
    if (!ready || !hash.startsWith('#d-')) return
    const element = document.getElementById(decodeURIComponent(hash.slice(1)))
    if (!element) return
    element.scrollIntoView?.({ block: 'center' })
    element.classList.add('ring-2', 'ring-warning/60')
    const timer = window.setTimeout(() => element.classList.remove('ring-2', 'ring-warning/60'), 2500)
    return () => window.clearTimeout(timer)
  }, [hash, ready])
}

function Notices({ calendar }: { calendar: MarketCalendar }) {
  const newsHref = calendar.news.link ? configKeyHref(calendar.news.link) : '/config'
  const closedHref = calendar.closed_dates.link ? configKeyHref(calendar.closed_dates.link) : '/config'
  return (
    <>
      {!calendar.us.readable && (
        <Alert tone="danger">
          <AlertDescription>
            The US market's calendar could not be read ({calendar.us.error ?? 'unknown error'}): its holidays and early closes are not listed.
          </AlertDescription>
        </Alert>
      )}
      {calendar.us.upkeep && (
        <Alert tone="warning">
          <AlertDescription>{calendar.us.upkeep}.</AlertDescription>
        </Alert>
      )}
      {calendar.news.on && !calendar.news.readable && (
        <Alert tone="danger">
          <AlertDescription>
            The window around US releases could not be worked out ({calendar.news.error ?? 'unknown error'}): its days are not listed.
          </AlertDescription>
        </Alert>
      )}
      {calendar.news.upkeep && (
        <Alert tone="warning">
          <AlertDescription>{calendar.news.upkeep}.</AlertDescription>
        </Alert>
      )}
      {calendar.closed_dates.unreadable.length > 0 && (
        <Alert tone="warning">
          <AlertDescription>
            Not a date: {calendar.closed_dates.unreadable.join(', ')} in config.json's{' '}
            <Link className="underline underline-offset-2" to={closedHref}>
              closed dates
            </Link>
            .
          </AlertDescription>
        </Alert>
      )}
      {!calendar.news.on && (
        <Alert tone="neutral">
          <AlertDescription>
            No window is kept around US releases: the{' '}
            <Link className="underline underline-offset-2" to={newsHref}>
              macro_news rule
            </Link>{' '}
            is not in config.json, so FOMC, CPI, NFP and PCE days block nothing.
          </AlertDescription>
        </Alert>
      )}
    </>
  )
}

function Tiles({ calendar, year, days }: { calendar: MarketCalendar; year: number; days: BlockedDay[] }) {
  const next = nextDayOff(calendar)
  const release = nextWindow(calendar)
  const counts = countByEffect(days)
  const today = todayTile(calendar)
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <KpiTile label="Today" value={today.value} hint={today.hint} />
      <KpiTile
        label="Next day off"
        value={next ? dayLabel(next.day, next.day.slice(0, 4) !== calendar.today.slice(0, 4)) : 'None known'}
        hint={next ? dayOffWhat(next) : `Nothing known after ${calendar.us.until ?? 'the calendar'}`}
      />
      <KpiTile
        label="Next release window"
        value={release ? dayLabel(release.day, release.day.slice(0, 4) !== calendar.today.slice(0, 4)) : calendar.news.on ? 'None known' : 'Off'}
        hint={
          release
            ? blockSentence(release.blocks.find((block) => block.kind === 'release') ?? release.blocks[0], calendar.timezone)
            : calendar.news.on
              ? `Nothing known after ${calendar.news.known_until ?? 'the calendar'}`
              : 'The macro_news rule is not in config.json'
        }
      />
      <KpiTile
        label={`In ${year}`}
        value={`${counts.closed} closed`}
        hint={`${counts.early_close} early close${counts.early_close === 1 ? '' : 's'}, ${counts.window} release window${counts.window === 1 ? '' : 's'}`}
      />
    </div>
  )
}

function MonthGrid({ month, timeZone, today }: { month: Month; timeZone: string; today: string }) {
  return (
    <table className="w-full table-fixed border-separate border-spacing-0.5 text-center text-xs tabular-nums">
      <caption className="pb-1 text-left text-sm font-medium text-foreground">{month.label}</caption>
      <thead>
        <tr>
          {WEEK_HEADS.map((head, index) => (
            <th key={index} scope="col" className="font-normal text-muted-foreground">
              {head}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {month.weeks.map((week, index) => (
          <tr key={index}>
            {week.map((cell, position) => {
              if (!cell) return <td key={position} />
              const frame = cn(
                'block rounded-sm py-0.5',
                cell.weekend && !cell.blocked && 'text-muted-foreground/50',
                // A day gone by is greyed, a blocked one kept readable: yesterday's holiday is what is looked for.
                cell.past && !cell.today && !cell.blocked && 'opacity-60',
                cell.today && 'ring-1 ring-foreground',
              )
              if (!cell.blocked) {
                return (
                  <td key={position}>
                    <span className={frame}>{cell.date}</span>
                  </td>
                )
              }
              const blocked = cell.blocked
              const said = blocked.blocks.map((block) => `${blockLabel(block)}: ${blockSentence(block, timeZone)}`)
              const relative = relativeLabel(blocked.day, today)
              return (
                <td key={position}>
                  <Hint
                    content={
                      <span className="block max-w-xs space-y-1 text-left">
                        <span className="block font-medium">
                          {dayLabel(blocked.day)}
                          {relative && ` (${relative})`}
                        </span>
                        {said.map((line) => (
                          <span key={line} className="block">
                            {line}
                          </span>
                        ))}
                      </span>
                    }
                  >
                    <a href={`#${dayAnchor(blocked.day)}`} className={cn(frame, EFFECT_CELL[blocked.effect], 'hover:underline')}>
                      <span aria-hidden>{cell.date}</span>
                      <span className="sr-only">{`${cell.date} ${month.label}: ${EFFECT_LABELS[blocked.effect]}`}</span>
                    </a>
                  </Hint>
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function YearPanel({ calendar, year, days }: { calendar: MarketCalendar; year: number; days: BlockedDay[] }) {
  const months = useMemo(() => monthsOf(year, days, calendar.today), [year, days, calendar.today])
  return (
    <Panel id="year" title={`${year} at a glance`} description="Each blocked day in the colour of what it does; a day leads to its line below.">
      <div className="grid grid-cols-2 gap-x-4 gap-y-4 sm:gap-x-6 lg:grid-cols-3 xl:grid-cols-4">
        {months.map((month) => (
          <MonthGrid key={month.month} month={month} timeZone={calendar.timezone} today={calendar.today} />
        ))}
      </div>
      <ul className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label="Legend">
        {(['closed', 'early_close', 'window'] as DayEffect[]).map((effect) => (
          <li key={effect} className="flex items-center gap-1.5">
            <span className={cn('inline-block size-3 rounded-sm ring-1 ring-current', EFFECT_CELL[effect])} aria-hidden />
            {EFFECT_LABELS[effect]}
          </li>
        ))}
        <li className="flex items-center gap-1.5">
          <span className="inline-block size-3 rounded-sm ring-1 ring-foreground" aria-hidden />
          Today
        </li>
      </ul>
    </Panel>
  )
}

function DaysPanel({ calendar, days }: { calendar: MarketCalendar; days: BlockedDay[] }) {
  const closedHref = calendar.closed_dates.link ? configKeyHref(calendar.closed_dates.link) : '/config'
  return (
    <Panel id="days" title="Day by day" description={zonesLine(calendar)}>
      {days.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">No blocked day known this year.</p>
      ) : (
        // One list for every width: the day beside its reasons on a wide screen, above them on a phone.
        <ol className="divide-y divide-border/40">
          {days.map((day) => {
            const relative = relativeLabel(day.day, calendar.today)
            return (
              <li
                key={day.day}
                id={dayAnchor(day.day)}
                aria-label={dayLabel(day.day)}
                className={cn(
                  'grid scroll-mt-24 gap-2 rounded-md px-1 py-3 transition-shadow sm:grid-cols-[11rem_1fr] sm:gap-4',
                  day.day < calendar.today && 'opacity-60',
                  day.day === calendar.today && 'bg-surface-raised/60',
                )}
              >
                <p className="flex flex-wrap items-center gap-2 self-start text-sm font-medium text-foreground">
                  {dayLabel(day.day, false)}
                  {relative && (
                    <Badge tone="neutral" variant="outline">
                      {relative}
                    </Badge>
                  )}
                </p>
                <ul className="space-y-2">
                  {day.blocks.map((block, index) => (
                    <li key={index} className="space-y-0.5">
                      <p className="flex flex-wrap items-center gap-2 text-sm">
                        <Badge tone={blockTone(block)}>{blockLabel(block)}</Badge>
                        <span>{blockName(block)}</span>
                      </p>
                      <p className="text-sm text-muted-foreground">
                        {blockSentence(block, calendar.timezone)}
                        {block.kind === 'config_closed' && block.also_us && (
                          <>
                            {' '}
                            <Link className="underline underline-offset-2" to={closedHref}>
                              Closed dates
                            </Link>
                          </>
                        )}
                      </p>
                    </li>
                  ))}
                </ul>
              </li>
            )
          })}
        </ol>
      )}
    </Panel>
  )
}

export function MarketCalendarPage() {
  const query = useQuery(MARKET_CALENDAR_QUERY)
  const calendar = query.data
  const [searchParams, setSearchParams] = useSearchParams()
  const releases = searchParams.get('releases') !== 'hide'
  const year = calendar ? chosenYear(calendar, searchParams.get('year')) : null
  const days = useMemo(() => (calendar && year !== null ? daysOfYear(calendar, year, releases) : []), [calendar, year, releases])
  useScrollToDay(Boolean(calendar))

  const yearHref = (chosen: number) => {
    const params = new URLSearchParams(searchParams)
    params.set('year', String(chosen))
    return `?${params.toString()}`
  }
  const closedHref = calendar?.closed_dates.link ? configKeyHref(calendar.closed_dates.link) : '/config'
  const newsHref = calendar?.news.link ? configKeyHref(calendar.news.link) : '/config'

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Market calendar"
        description="Every day the trader takes no entry, or fewer: the US market's holidays and early closes, the closed dates of config.json, and the windows around US releases."
      />
      <QueryState isPending={query.isPending} error={query.error} hasData={Boolean(calendar)}>
        {calendar && year !== null && (
          <>
            <Notices calendar={calendar} />
            <Tiles calendar={calendar} year={year} days={daysOfYear(calendar, year)} />
            <div className="flex flex-wrap items-center justify-between gap-3">
              <Tabs
                tabs={yearsOf(calendar).map((each) => ({ id: String(each), label: String(each), href: yearHref(each) }))}
                value={String(year)}
                onChange={(chosen) => setSearchParams((params) => {
                  params.set('year', chosen)
                  return params
                }, { replace: true })}
                linkComponent={RouterLink}
                renderContent={false}
              />
              {calendar.news.on && (
                <Switch
                  label="Release windows"
                  checked={releases}
                  onChange={(checked) => setSearchParams((params) => {
                    if (checked) params.delete('releases')
                    else params.set('releases', 'hide')
                    return params
                  }, { replace: true })}
                />
              )}
            </div>
            <YearPanel calendar={calendar} year={year} days={days} />
            <DaysPanel calendar={calendar} days={days} />
            <p className="text-xs text-muted-foreground">
              The US holidays and early closes come with WATA (<code>src/trade/data/us_market_calendar.csv</code>, known until{' '}
              {calendar.us.until ?? 'nothing'}), as the release days do (<code>src/trade/data/macro_calendar.csv</code>
              {calendar.news.known_until ? `, known until ${calendar.news.known_until}` : ''}): a release brings their next year. The{' '}
              <Link className="underline underline-offset-2" to={closedHref}>
                closed dates
              </Link>{' '}
              and the{' '}
              <Link className="underline underline-offset-2" to={newsHref}>
                window around releases
              </Link>{' '}
              are set in Configuration.
            </p>
          </>
        )}
      </QueryState>
    </div>
  )
}
