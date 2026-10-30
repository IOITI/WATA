import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FOOT_PX, isAtFoot, newestQuestion, useFollowLatest } from './useFollowLatest'

// jsdom lays nothing out: what scrolls says its sizes as a browser would, and what grows is told by hand.
function scroller(size: { height: number; view: number }, top: number) {
  const element = document.createElement('div')
  let scrolled = top
  Object.defineProperties(element, {
    scrollHeight: { get: () => size.height },
    clientHeight: { get: () => size.view },
    scrollTop: { get: () => scrolled, set: (value: number) => { scrolled = Math.max(0, Math.min(value, size.height - size.view)) } },
  })
  element.scrollTo = vi.fn((options?: ScrollToOptions | number) => {
    if (typeof options === 'object' && options.top !== undefined) element.scrollTop = options.top
  }) as unknown as typeof element.scrollTo
  return element
}

function conversation(requests: number) {
  const element = document.createElement('div')
  for (let index = 0; index < requests; index += 1) {
    const article = document.createElement('article')
    article.setAttribute('aria-label', `Request ${index + 1}`)
    article.scrollIntoView = vi.fn()
    element.append(article)
  }
  return element
}

let grown: (() => void)[] = []

beforeEach(() => {
  grown = []
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: () => void) {
      grown.push(callback)
    }
    observe() {}
    disconnect() {}
  })
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    callback(0)
    return 1
  })
  vi.stubGlobal('cancelAnimationFrame', () => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('following the newest step', () => {
  it('is at the foot within a line or two of the bottom', () => {
    expect(isAtFoot({ scrollHeight: 2000, clientHeight: 500, scrollTop: 1500 })).toBe(true)
    expect(isAtFoot({ scrollHeight: 2000, clientHeight: 500, scrollTop: 1500 - FOOT_PX })).toBe(true)
    expect(isAtFoot({ scrollHeight: 2000, clientHeight: 500, scrollTop: 1400 - FOOT_PX })).toBe(false)
  })

  it("names the newest request's question of a conversation of several, none of one alone", () => {
    expect(newestQuestion(conversation(1))).toBeNull()
    const several = conversation(3)
    expect(newestQuestion(several)).toBe(several.lastElementChild)
    expect(newestQuestion(null)).toBeNull()
  })

  it('opens a conversation under way at its foot, and keeps it there as it grows', () => {
    const size = { height: 2000, view: 500 }
    const page = scroller(size, 0)
    renderHook(() => useFollowLatest({ current: page }, { current: conversation(1) }, { id: 'c1', live: true }))
    expect(page.scrollTop).toBe(1500)

    size.height = 2600
    act(() => grown.forEach((callback) => callback()))
    expect(page.scrollTop).toBe(2100)
  })

  it('follows once more what a request ends with, then nothing', () => {
    const size = { height: 2000, view: 500 }
    const page = scroller(size, 0)
    const { rerender } = renderHook(({ live }) => useFollowLatest({ current: page }, { current: conversation(1) }, { id: 'c1', live }),
      { initialProps: { live: true } })
    expect(page.scrollTop).toBe(1500)

    size.height = 2300
    rerender({ live: false })
    expect(page.scrollTop).toBe(1800)
    size.height = 2600
    rerender({ live: false })
    expect(page.scrollTop).toBe(1800)
  })

  it('leaves the view where the operator scrolled it, and brings it back to the newest', () => {
    const size = { height: 2000, view: 500 }
    const page = scroller(size, 1500)
    const { result } = renderHook(() => useFollowLatest({ current: page }, { current: conversation(1) }, { id: 'c1', live: true }))

    act(() => {
      page.scrollTop = 200
      page.dispatchEvent(new Event('scroll'))
    })
    expect(result.current.away).toBe(true)
    size.height = 2600
    act(() => grown.forEach((callback) => callback()))
    expect(page.scrollTop).toBe(200)

    act(() => result.current.jump())
    expect(page.scrollTo).toHaveBeenCalledWith({ top: 2600, behavior: 'smooth' })
    expect(result.current.away).toBe(false)
    act(() => {
      page.dispatchEvent(new Event('scroll'))
    })
    expect(result.current.away).toBe(false)
  })

  it('takes a smooth jump on its way for the foot, what grows meanwhile taking it there at once', () => {
    const size = { height: 2000, view: 500 }
    const page = scroller(size, 0)
    // A smooth scroll that has not gone far yet: only what is at once moves the view.
    page.scrollTo = vi.fn((options?: ScrollToOptions | number) => {
      if (typeof options === 'object' && options.behavior === 'instant' && options.top !== undefined) page.scrollTop = options.top
    }) as unknown as typeof page.scrollTo
    const { result } = renderHook(() => useFollowLatest({ current: page }, { current: conversation(1) }, { id: 'c1', live: true }))
    act(() => {
      page.scrollTop = 100
      page.dispatchEvent(new Event('scroll'))
    })
    expect(result.current.away).toBe(true)

    act(() => result.current.jump())
    // Halfway down, the jump goes on: the view is not away.
    act(() => {
      page.scrollTop = 800
      page.dispatchEvent(new Event('scroll'))
    })
    expect(result.current.away).toBe(false)
    size.height = 2400
    act(() => grown.forEach((callback) => callback()))
    expect(page.scrollTop).toBe(1900)
  })

  it('goes to the foot at once for a request just sent, and follows from there', () => {
    const size = { height: 2000, view: 500 }
    const page = scroller(size, 1500)
    const { result } = renderHook(() => useFollowLatest({ current: page }, { current: conversation(1) }, { id: 'c1', live: false }))
    act(() => {
      page.scrollTop = 0
      page.dispatchEvent(new Event('scroll'))
    })

    act(() => result.current.follow())
    expect(page.scrollTo).toHaveBeenCalledWith({ top: 2000, behavior: 'instant' })
    expect(page.scrollTop).toBe(1500)
  })

  it("follows nothing once it is done, and opens a finished conversation on its newest request's question", () => {
    const size = { height: 2000, view: 500 }
    const page = scroller(size, 0)
    const content = conversation(2)
    const { result } = renderHook(() => useFollowLatest({ current: page }, { current: content }, { id: 'c2', live: false }))

    expect(page.scrollTop).toBe(0)
    expect((content.lastElementChild as HTMLElement).scrollIntoView).toHaveBeenCalledWith({ block: 'start' })
    expect(grown).toHaveLength(0)
    expect(result.current.away).toBe(false)
  })

  it('does nothing outside what scrolls, nor before a conversation is drawn', () => {
    const { result } = renderHook(() => useFollowLatest(null, { current: null }, { id: null, live: true }))

    expect(result.current.away).toBe(false)
    expect(() => result.current.jump()).not.toThrow()
  })
})
