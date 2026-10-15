import { useEffect, useMemo, useRef, useState } from 'react'

import { rowCandle, rowVolume, startTail, quoteAge, tailRows, withBar, withQuote } from '@/features/prices/liveTail'
import type { LiveChart, LiveTail, LiveUpdate } from '@/features/prices/liveTail'
import type { Side } from '@/features/prices/chartData'
import { assetLiveUrl } from '@/lib/api'
import type { LiveBarMessage, LiveQuoteMessage, LiveSnapshot, LiveStateMessage, PriceBarRow, PriceBars } from '@/lib/types'

/** How often what the page says of the stream (its delay, the market) is drawn again: a quote moves the chart, not the page. */
const STATUS_EVERY_MS = 250
/** Closed ten seconds kept to fold again into bars read later: an hour of them. */
const RECENT_BARS = 360

export interface LiveStatus {
  /** The stream to this page is open. */
  connected: boolean
  /** The admin receives the feed's prices. */
  live: boolean
  /** Why it is not live. */
  detail: string | null
  /** How long ago Capital.com quoted the last quote received, as the server's clock counts. */
  delayMs: number | null
  market: 'open' | 'closed' | null
  /** The newest bar, with the ten seconds in progress: what the readout shows when nothing is pointed at. */
  lastRow: PriceBarRow | null
}

const IDLE: LiveStatus = { connected: false, live: false, detail: null, delayMs: null, market: null, lastRow: null }

interface UseLiveTailOptions {
  /** The asset whose prices are followed (its ref); null follows nothing. */
  assetRef: string | null
  /** The bars read: what the stream adds to. */
  prices: PriceBars | undefined
  side: Side
  timeZone: string
  /** Ten seconds closed: range bars are asked for again then. */
  onBar?: () => void
  /** Bars may have been missed (the stream came back, or fell behind): read them again. */
  onResync?: () => void
}

function parse<T>(message: MessageEvent<string>): T | null {
  try {
    return JSON.parse(message.data) as T
  } catch {
    return null
  }
}

/**
 * Follow an asset's live prices: the chart gets a channel that moves its last
 * candles at each quote, the page a status a few times a second. While
 * `assetRef` is null (a period that does not reach today, another provider),
 * nothing is opened.
 */
export function useLiveTail({ assetRef, prices, side, timeZone, onBar, onResync }: UseLiveTailOptions) {
  const tail = useRef<LiveTail | null>(null)
  const recent = useRef<PriceBarRow[]>([])
  const listeners = useRef(new Set<(update: LiveUpdate) => void>())
  const shown = useRef<LiveUpdate | null>(null)
  const callbacks = useRef({ onBar, onResync })
  const drawing = useRef({ side, timeZone })
  const [status, setStatus] = useState<LiveStatus>(IDLE)
  const pending = useRef<Partial<LiveStatus>>({})
  const statusTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    callbacks.current = { onBar, onResync }
    drawing.current = { side, timeZone }
  }, [onBar, onResync, side, timeZone])

  const chart = useMemo<LiveChart>(
    () => ({
      current: () => shown.current,
      subscribe: (listener) => {
        listeners.current.add(listener)
        return () => listeners.current.delete(listener)
      },
    }),
    [],
  )

  /** Draw the tail as it is now, and keep it for a chart that subscribes later. */
  function emit() {
    const current = tail.current
    if (!current) return
    const rows = tailRows(current)
    const { side: drawnSide, timeZone: zone } = drawing.current
    const update: LiveUpdate = {
      candles: rows.map((row) => rowCandle(row, drawnSide, zone)).filter((candle) => candle !== null),
      volume: rows.map((row) => rowVolume(row, zone)),
    }
    shown.current = update
    for (const listener of listeners.current) listener(update)
    report({ lastRow: rows[rows.length - 1] ?? null })
  }

  function report(changes: Partial<LiveStatus>) {
    pending.current = { ...pending.current, ...changes }
    if (statusTimer.current !== null) return
    statusTimer.current = setTimeout(() => {
      statusTimer.current = null
      const next = pending.current
      pending.current = {}
      setStatus((before) => ({ ...before, ...next }))
    }, STATUS_EVERY_MS)
  }

  // The bars read (another period, length or side, or the same read again): what the stream adds to.
  useEffect(() => {
    if (!prices) {
      tail.current = null
      shown.current = null
      return
    }
    let next = startTail(prices.bars, prices.through, prices.interval)
    // What closed since the bars were read and is not in them yet.
    for (const bar of recent.current) next = withBar(next, bar)
    tail.current = next
    emit()
    // `emit` reads refs only: the bars read are what this follows.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prices, side, timeZone])

  useEffect(() => {
    if (!assetRef || typeof EventSource === 'undefined') {
      setStatus(IDLE)
      return
    }
    const source = new EventSource(assetLiveUrl(assetRef))
    let offsetMs = 0
    let opened = false

    source.addEventListener('snapshot', (message) => {
      const snapshot = parse<LiveSnapshot>(message as MessageEvent<string>)
      if (!snapshot) return
      offsetMs = snapshot.server_ms - Date.now()
      // Coming back after a break: bars closed meanwhile are read again.
      if (opened) callbacks.current.onResync?.()
      opened = true
      report({ connected: true, live: snapshot.live, detail: snapshot.detail, market: snapshot.state?.market ?? null })
      if (snapshot.quote && tail.current) {
        tail.current = withQuote(tail.current, snapshot.quote.bar)
        report({ delayMs: quoteAge(snapshot.quote, offsetMs, Date.now()) })
        emit()
      }
    })
    source.addEventListener('quote', (message) => {
      const quote = parse<LiveQuoteMessage>(message as MessageEvent<string>)
      if (!quote || !tail.current) return
      tail.current = withQuote(tail.current, quote.bar)
      report({ delayMs: quoteAge(quote, offsetMs, Date.now()), market: 'open' })
      emit()
    })
    source.addEventListener('bar', (message) => {
      const closed = parse<LiveBarMessage>(message as MessageEvent<string>)
      if (!closed) return
      recent.current = [...recent.current, closed.bar].slice(-RECENT_BARS)
      if (tail.current) {
        tail.current = withBar(tail.current, closed.bar)
        emit()
      }
      callbacks.current.onBar?.()
    })
    source.addEventListener('state', (message) => {
      const state = parse<LiveStateMessage>(message as MessageEvent<string>)
      if (state?.market) report({ market: state.market })
    })
    source.addEventListener('live', (message) => {
      const live = parse<{ live: boolean; detail: string | null }>(message as MessageEvent<string>)
      if (live) report({ live: live.live, detail: live.detail })
    })
    source.addEventListener('resync', () => callbacks.current.onResync?.())
    // The browser comes back by itself; meanwhile the page says so.
    source.addEventListener('error', () => report({ connected: false }))

    return () => {
      source.close()
      recent.current = []
      if (statusTimer.current !== null) clearTimeout(statusTimer.current)
      statusTimer.current = null
      pending.current = {}
      setStatus(IDLE)
    }
    // `report` and `emit` read refs only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assetRef])

  return { chart, status }
}
