import { act, render, screen } from '@testing-library/react'
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useToast } from './toast-context'
import { ToastProvider } from './ToastProvider'

let show: ReturnType<typeof useToast>

/** Hands the provider's function to the test. */
function Capture() {
  const toast = useToast()
  useEffect(() => {
    show = toast
  }, [toast])
  return null
}

function renderProvider() {
  render(
    <ToastProvider>
      <Capture />
    </ToastProvider>,
  )
}

/** Let the toast's own clock (animation frames) run for `ms`. */
async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('ToastProvider', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame', 'performance'] })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows a second toast after the first one instead of replacing it', async () => {
    renderProvider()

    act(() => {
      show({ title: 'Close requested', variant: 'success' })
      show({ title: 'Position closed', variant: 'success' })
    })

    expect(screen.getByText('Close requested')).toBeInTheDocument()
    expect(screen.queryByText('Position closed')).not.toBeInTheDocument()

    // With another one waiting, the first stays 2.5 seconds.
    await advance(2000)
    expect(screen.getByText('Close requested')).toBeInTheDocument()
    await advance(1000)
    expect(screen.getByText('Position closed')).toBeInTheDocument()
  })

  it('keeps an error on screen longer', async () => {
    renderProvider()

    act(() => show({ title: 'Position monitor is down', variant: 'error' }))

    await advance(6000)
    expect(screen.getByText('Position monitor is down')).toBeInTheDocument()
  })

  it('drops the oldest waiting toasts of a burst', () => {
    renderProvider()

    act(() => {
      for (let index = 1; index <= 10; index += 1) show({ title: `Event ${index}` })
    })

    // Ten arrived, six are kept: the fifth is the oldest left.
    expect(screen.getByText('Event 5')).toBeInTheDocument()
  })
})
