// A conversation followed to its newest step while the agent works: the view stays at the foot of what scrolls
// as steps and text arrive, unless the operator scrolled up, when a button ("Latest") brings it back; a request
// sent goes there too. Opened, a conversation under way shows its foot, another its newest request's question.

import { useReducedMotionConfig } from 'framer-motion'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'

/** Within this of its bottom, the view is at the foot: a line or two, a step's padding. */
export const FOOT_PX = 96
// A smooth jump to the foot is on its way, not away from it, for at most this long.
const JUMP_MS = 1000

export function isAtFoot(element: Pick<HTMLElement, 'scrollTop' | 'scrollHeight' | 'clientHeight'>): boolean {
  return element.scrollHeight - element.scrollTop - element.clientHeight <= FOOT_PX
}

// At its foot at once: a smooth scroll lags behind what streams.
function toBottom(element: HTMLElement) {
  element.scrollTo?.({ top: element.scrollHeight, behavior: 'instant' })
}

/** The question of the newest request of a conversation of several (one alone is already in view). */
export function newestQuestion(content: HTMLElement | null): HTMLElement | null {
  const requests = content?.querySelectorAll<HTMLElement>('article[aria-label^="Request "]') ?? []
  return requests.length > 1 ? requests[requests.length - 1] : null
}

export function useFollowLatest(
  /** What scrolls: the shell's page, or a sheet's body; none outside them (a page's tests). */
  scroller: RefObject<HTMLElement | null> | null,
  /** The conversation, whose growth is followed. */
  content: RefObject<HTMLElement | null>,
  { id, live }: {
    /** The conversation shown, once it is drawn: another is opened anew. */
    id: string | null
    /** A request under way: what grows is followed. */
    live: boolean
  },
): { away: boolean; jump: () => void; follow: () => void } {
  const reduce = useReducedMotionConfig()
  const atFoot = useRef(true)
  const jumped = useRef(0)
  const [away, setAway] = useState(false)

  // Where the view is, as the operator (or a jump) scrolls it.
  useEffect(() => {
    const element = scroller?.current
    if (!element || !id) return
    const onScroll = () => {
      const foot = isAtFoot(element)
      // A smooth jump on its way passes by heights short of the foot: not the operator scrolling away. None yet: none.
      if (!foot && jumped.current !== 0 && performance.now() - jumped.current < JUMP_MS) return
      jumped.current = 0
      atFoot.current = foot
      setAway(!foot)
    }
    onScroll()
    element.addEventListener('scroll', onScroll, { passive: true })
    return () => element.removeEventListener('scroll', onScroll)
  }, [scroller, id])

  // What grows while it works keeps the view at the foot if it was there: at once, a smooth scroll lagging
  // behind streamed text. No ResizeObserver (jsdom): nothing is followed.
  useEffect(() => {
    const element = scroller?.current
    const watched = content.current
    if (!live || !element || !watched || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      if (atFoot.current) toBottom(element)
    })
    observer.observe(watched)
    return () => observer.disconnect()
  }, [scroller, content, live, id])

  // A request that ends draws what it ended with (its proposals, how it ended) as it stops being followed: once more.
  const wasLive = useRef(live)
  useLayoutEffect(() => {
    const element = scroller?.current
    if (wasLive.current && !live && element && atFoot.current) toBottom(element)
    wasLive.current = live
  }, [scroller, live])

  // Opened once per conversation, in the frame before it is painted: after the shell's own layout effect, which
  // opens a page at its top.
  const opened = useRef<string | null>(null)
  useLayoutEffect(() => {
    const element = scroller?.current
    if (!id || opened.current === id || !element) return
    opened.current = id
    let done = false
    const frame = requestAnimationFrame(() => {
      done = true
      if (live) toBottom(element)
      else newestQuestion(content.current)?.scrollIntoView?.({ block: 'start' })
    })
    return () => {
      if (done) return
      cancelAnimationFrame(frame)
      opened.current = null
    }
  }, [scroller, content, id, live])

  // To the foot, followed from there: smoothly from the button (what grows meanwhile takes it there at once),
  // at once for a request just sent.
  const toFoot = (smooth: boolean) => {
    const element = scroller?.current
    if (!element) return
    atFoot.current = true
    setAway(false)
    const glide = smooth && !reduce
    jumped.current = glide ? performance.now() : 0
    if (glide) element.scrollTo?.({ top: element.scrollHeight, behavior: 'smooth' })
    else toBottom(element)
  }

  return { away: away && live, jump: () => toFoot(true), follow: () => toFoot(false) }
}
