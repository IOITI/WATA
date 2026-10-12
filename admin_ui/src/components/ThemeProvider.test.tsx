import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { THEME_STORAGE_KEY } from '@/lib/theme'
import { stubSystemTheme } from '@/test/systemTheme'

import { useTheme } from './theme-context'
import { ThemeProvider } from './ThemeProvider'
import { ThemeToggle } from './ThemeToggle'

const isDark = () => document.documentElement.classList.contains('dark')
const button = () => screen.getByRole('button', { name: /^Theme:/ })

function Shown() {
  const { theme } = useTheme()
  return <output aria-label="theme">{theme}</output>
}

function renderPage() {
  render(
    <ThemeProvider>
      <ThemeToggle />
      <Shown />
    </ThemeProvider>,
  )
}

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.classList.remove('dark')
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('a first visit', () => {
  it('is dark on a system set to dark', () => {
    stubSystemTheme(true)
    renderPage()

    expect(isDark()).toBe(true)
    expect(screen.getByLabelText('theme')).toHaveTextContent('dark')
    expect(button()).toHaveAccessibleName('Theme: follows the system (dark now). Press for always light.')
  })

  it('is light on a system set to light', () => {
    stubSystemTheme(false)
    renderPage()

    expect(isDark()).toBe(false)
    expect(screen.getByLabelText('theme')).toHaveTextContent('light')
  })

  it('follows the system when it changes, at once', () => {
    const system = stubSystemTheme(false)
    renderPage()

    act(() => system.set(true))

    expect(isDark()).toBe(true)
    expect(button()).toHaveAccessibleName('Theme: follows the system (dark now). Press for always light.')
  })
})

describe('a theme that was chosen', () => {
  it('wins over the system, at the next visit too', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'light')
    stubSystemTheme(true)
    renderPage()

    expect(isDark()).toBe(false)
    expect(button()).toHaveAccessibleName('Theme: always light. Press for always dark.')
  })

  it('stays when the system changes', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'light')
    const system = stubSystemTheme(false)
    renderPage()

    act(() => system.set(true))

    expect(isDark()).toBe(false)
  })
})

describe('the button', () => {
  it('goes to light, to dark, then back to following the system, and the choice is kept', async () => {
    stubSystemTheme(true)
    const user = userEvent.setup()
    renderPage()

    await user.click(button())
    expect(isDark()).toBe(false)
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
    expect(button()).toHaveAccessibleName('Theme: always light. Press for always dark.')

    await user.click(button())
    expect(isDark()).toBe(true)
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')
    expect(button()).toHaveAccessibleName('Theme: always dark. Press to follow the system.')

    await user.click(button())
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull()
    expect(button()).toHaveAccessibleName('Theme: follows the system (dark now). Press for always light.')
  })

  it('says the same thing to a pointer that rests on it', () => {
    stubSystemTheme(false)
    renderPage()

    expect(button()).toHaveAttribute('title', button().getAttribute('aria-label'))
  })

  it('still switches where the browser keeps nothing', async () => {
    stubSystemTheme(true)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage is blocked')
    })
    const user = userEvent.setup()
    renderPage()

    await user.click(button())

    expect(isDark()).toBe(false)
  })
})

describe('outside a provider', () => {
  it('a component is shown dark, as the interface always was', () => {
    render(<Shown />)

    expect(screen.getByLabelText('theme')).toHaveTextContent('dark')
  })
})
