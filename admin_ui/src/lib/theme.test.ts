import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { stubSystemTheme } from '@/test/systemTheme'

import {
  applyTheme,
  nextPreference,
  readPreference,
  resolveTheme,
  systemIsDark,
  THEME_STORAGE_KEY,
  themeLabel,
  watchSystem,
  writePreference,
} from './theme'

beforeEach(() => window.localStorage.clear())
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('the theme chosen', () => {
  it('is to follow the system until one is picked', () => {
    expect(readPreference()).toBe('system')
  })

  it('is read back from this browser', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'light')
    expect(readPreference()).toBe('light')
    window.localStorage.setItem(THEME_STORAGE_KEY, 'dark')
    expect(readPreference()).toBe('dark')
  })

  it('is the system again for anything else found there', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'sepia')
    expect(readPreference()).toBe('system')
  })

  it('is kept when it is a theme, and forgotten when it is to follow the system', () => {
    writePreference('dark')
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')

    writePreference('system')
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull()
  })

  it('does not break where the browser keeps nothing', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage is blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage is blocked')
    })

    expect(readPreference()).toBe('system')
    expect(() => writePreference('light')).not.toThrow()
  })
})

describe('what is shown', () => {
  it('is the system\'s theme while following it, and the chosen one otherwise', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
  })

  it('is marked on the page by the class every dark style hangs on', () => {
    const root = document.createElement('html')

    applyTheme('dark', root)
    expect(root.classList.contains('dark')).toBe(true)

    applyTheme('light', root)
    expect(root.classList.contains('dark')).toBe(false)
  })
})

describe('the button', () => {
  it('goes from the system to light, to dark, and back to the system', () => {
    expect(nextPreference('system')).toBe('light')
    expect(nextPreference('light')).toBe('dark')
    expect(nextPreference('dark')).toBe('system')
  })

  it('says where things stand and what a press does', () => {
    expect(themeLabel('system', 'dark')).toBe('Theme: follows the system (dark now). Press for always light.')
    expect(themeLabel('system', 'light')).toBe('Theme: follows the system (light now). Press for always light.')
    expect(themeLabel('light', 'light')).toBe('Theme: always light. Press for always dark.')
    expect(themeLabel('dark', 'dark')).toBe('Theme: always dark. Press to follow the system.')
  })
})

describe('the system setting', () => {
  it('is read from the browser', () => {
    stubSystemTheme(true)
    expect(systemIsDark()).toBe(true)

    stubSystemTheme(false)
    expect(systemIsDark()).toBe(false)
  })

  it('is watched, and no longer once that is stopped', () => {
    const system = stubSystemTheme(false)
    const onChange = vi.fn()

    const stop = watchSystem(onChange)
    system.set(true)
    expect(onChange).toHaveBeenCalledTimes(1)

    stop()
    system.set(false)
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(system.listening()).toBe(0)
  })

  it('is taken for light where the browser cannot say', () => {
    // jsdom, and browsers older than the setting itself.
    expect(systemIsDark()).toBe(false)
    expect(() => watchSystem(() => {})()).not.toThrow()
  })
})
