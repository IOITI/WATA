import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { RangeControl } from './RangeControl'
import type { BuiltRange } from './RangeControl'

const BUILT: BuiltRange = { tick: 0.01, decimals: 2, ticks: 60, size: 0.6 }

type Props = Parameters<typeof RangeControl>[0]

function setup(props: Partial<Props> = {}) {
  const onChange = vi.fn()
  const all: Props = { built: BUILT, unit: 'EUR', auto: false, onChange, ...props }
  const view = render(<RangeControl {...all} />)
  return {
    onChange,
    user: userEvent.setup(),
    ticks: () => screen.getByLabelText('Range in ticks (R)') as HTMLInputElement,
    size: () => screen.getByLabelText(/Range as a price move/) as HTMLInputElement,
    apply: () => screen.queryByRole('button', { name: 'Apply' }),
    /** The server answered: the chart is now built with this. */
    answer: (built: BuiltRange, more: Partial<Props> = {}) => view.rerender(<RangeControl {...all} built={built} {...more} />),
  }
}

describe('the range of a range bar', () => {
  it('is shown in ticks and as the price move that makes, in the unit of the instrument', () => {
    const { ticks, size } = setup()

    expect(ticks().value).toBe('60')
    expect(size().value).toBe('0.60')
    expect(screen.getByLabelText('Range as a price move, in EUR')).toBe(size())
    expect(screen.getByText('1R = 0.01 EUR')).toBeInTheDocument()
  })

  it('counts an index in points', () => {
    setup({ unit: 'pts', built: { tick: 0.01, decimals: 2, ticks: 1500, size: 15 } })

    expect(screen.getByLabelText('Range as a price move, in pts')).toHaveValue('15.00')
    expect(screen.getByText('1R = 0.01 pts')).toBeInTheDocument()
  })

  it('follows in price what is typed in ticks, and rebuilds on Enter only', async () => {
    const { user, ticks, size, onChange } = setup()

    await user.clear(ticks())
    await user.type(ticks(), '150')

    expect(size().value).toBe('1.50')
    expect(onChange).not.toHaveBeenCalled()

    await user.keyboard('{Enter}')

    expect(onChange).toHaveBeenCalledExactlyOnceWith(1.5)
  })

  it('follows in ticks what is typed as a price, and rebuilds when the field is left', async () => {
    const { user, ticks, size, onChange } = setup()

    await user.clear(size())
    await user.type(size(), '15')

    expect(ticks().value).toBe('1500')
    expect(onChange).not.toHaveBeenCalled()

    await user.tab()

    expect(onChange).toHaveBeenCalledExactlyOnceWith(15)
  })

  it('takes a comma for the decimals', async () => {
    const { user, size, ticks, onChange } = setup()

    await user.clear(size())
    await user.type(size(), '0,25{Enter}')

    expect(ticks().value).toBe('25')
    expect(onChange).toHaveBeenCalledExactlyOnceWith(0.25)
  })

  it('brings a price that is between two ticks to the nearest one', async () => {
    const { user, size, onChange } = setup()

    await user.clear(size())
    await user.type(size(), '0.127{Enter}')

    expect(onChange).toHaveBeenCalledExactlyOnceWith(0.13)
    expect(size().value).toBe('0.13')
  })

  it('counts in the ticks of a price quoted with three decimals', async () => {
    const { user, ticks, size, onChange } = setup({ built: { tick: 0.001, decimals: 3, ticks: 10, size: 0.01 } })

    expect(size().value).toBe('0.010')

    await user.clear(ticks())
    await user.type(ticks(), '25{Enter}')

    expect(onChange).toHaveBeenCalledExactlyOnceWith(0.025)
  })

  it('does not rebuild for a range that is already the one on the chart', async () => {
    const { user, ticks, onChange } = setup()

    await user.click(ticks())
    await user.tab()
    await user.clear(ticks())
    await user.type(ticks(), '60{Enter}')

    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('applying it', () => {
  it('offers a button as soon as what is typed is not what the chart shows, and not before', async () => {
    const { user, ticks, apply, onChange } = setup()
    expect(apply()).not.toBeInTheDocument()

    await user.clear(ticks())
    await user.type(ticks(), '80')

    expect(apply()).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('is done by that button, which is what a phone has: its number pad has no Enter', async () => {
    const { user, ticks, apply, onChange } = setup()
    await user.clear(ticks())
    await user.type(ticks(), '80')

    await user.click(apply() as HTMLElement)

    // Once: leaving the field for the button and pressing the button are one request.
    expect(onChange).toHaveBeenCalledExactlyOnceWith(0.8)
  })

  it('tells a phone that its keyboard can end the entry', () => {
    const { ticks, size } = setup()

    expect(ticks()).toHaveAttribute('enterkeyhint', 'done')
    expect(size()).toHaveAttribute('enterkeyhint', 'done')
    expect(ticks()).toHaveAttribute('inputmode', 'numeric')
    expect(size()).toHaveAttribute('inputmode', 'decimal')
  })

  it('leaves the focus in the field when the server answers, so that another range can be typed at once', async () => {
    const { user, ticks, answer, apply, onChange } = setup()
    await user.clear(ticks())
    await user.type(ticks(), '80{Enter}')

    answer({ ...BUILT, ticks: 80, size: 0.8 })

    expect(ticks()).toHaveFocus()
    expect(ticks().value).toBe('80')
    expect(apply()).not.toBeInTheDocument()

    await user.clear(ticks())
    await user.type(ticks(), '100{Enter}')

    expect(onChange).toHaveBeenLastCalledWith(1)
    expect(ticks()).toHaveFocus()
  })

  it('keeps what is being typed when the same bars are simply read again', async () => {
    const { user, ticks, answer } = setup()
    await user.clear(ticks())
    await user.type(ticks(), '8')

    answer({ ...BUILT })

    expect(ticks().value).toBe('8')
    expect(ticks()).toHaveFocus()
  })

  it('takes the server\'s values when they change and nothing of it was typed', () => {
    const { ticks, size, answer } = setup()

    answer({ ...BUILT, ticks: 2000, size: 20 })

    expect(ticks().value).toBe('2000')
    expect(size().value).toBe('20.00')
  })
})

describe('stepping it', () => {
  it('goes to the next rung of 1, 2, 5, 10… up or down, and rebuilds at once', async () => {
    const { user, ticks, size, onChange } = setup()

    await user.click(screen.getByRole('button', { name: 'Larger range' }))

    expect(onChange).toHaveBeenLastCalledWith(1)
    expect(ticks().value).toBe('100')
    expect(size().value).toBe('1.00')

    await user.click(screen.getByRole('button', { name: 'Smaller range' }))

    expect(onChange).toHaveBeenLastCalledWith(0.5)
    expect(ticks().value).toBe('50')
  })

  it('cannot go under one tick', () => {
    setup({ built: { ...BUILT, ticks: 1, size: 0.01 } })

    expect(screen.getByRole('button', { name: 'Smaller range' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Larger range' })).toBeEnabled()
  })
})

describe('a range that cannot be', () => {
  it.each(['0', '-5', '1.5', 'abc'])('typed in ticks as %j is refused on Enter, saying what the field takes', async (typed) => {
    const { user, ticks, size, onChange } = setup()

    await user.clear(ticks())
    await user.type(ticks(), `${typed}{Enter}`)

    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('A whole number of ticks, 1 at least.')
    expect(ticks()).toHaveAttribute('aria-invalid', 'true')
    // The other field is not the one at fault.
    expect(size()).toHaveAttribute('aria-invalid', 'false')
  })

  it.each(['0', '0.001', 'abc'])('typed as a price of %j is refused on Enter, in terms of a price', async (typed) => {
    const { user, ticks, size, onChange } = setup()

    await user.clear(size())
    await user.type(size(), `${typed}{Enter}`)

    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('At least 0.01 EUR, which is one tick.')
    expect(size()).toHaveAttribute('aria-invalid', 'true')
    expect(ticks()).toHaveAttribute('aria-invalid', 'false')
  })

  it('is not held against a field that is only being left', async () => {
    const { user, ticks, onChange } = setup()

    await user.clear(ticks())
    await user.tab()

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('is forgotten as soon as something else is typed', async () => {
    const { user, ticks } = setup()
    await user.clear(ticks())
    await user.type(ticks(), '0{Enter}')

    await user.type(ticks(), '3')

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('is said in the server\'s words when it is the server that refuses, with what was asked still in the fields', async () => {
    const { ticks, answer } = setup()

    answer({ ...BUILT, ticks: 1, size: 0.01 }, { refusal: 'More than 12000 range bars in this period: choose a larger range or a shorter period' })

    expect(screen.getByRole('alert')).toHaveTextContent('More than 12000 range bars in this period')
    expect(ticks().value).toBe('1')
    expect(ticks()).toBeEnabled()
  })
})

describe('leaving it to the server', () => {
  it('is one button', async () => {
    const { user, onChange } = setup()

    expect(screen.getByRole('button', { name: 'Auto' })).toHaveAttribute('aria-pressed', 'false')
    await user.click(screen.getByRole('button', { name: 'Auto' }))

    expect(onChange).toHaveBeenCalledExactlyOnceWith('auto')
  })

  it('can be asked again when it is already so', async () => {
    const { user, onChange } = setup({ auto: true })

    await user.click(screen.getByRole('button', { name: 'Auto' }))

    expect(onChange).toHaveBeenCalledExactlyOnceWith('auto')
  })

  it('waits for the first answer before offering fields, in a row that is already its full height', () => {
    const { ticks, size } = setup({ built: undefined, auto: true })

    expect(ticks()).toBeDisabled()
    expect(size()).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Larger range' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Auto' })).toHaveAttribute('aria-pressed', 'true')
  })
})
