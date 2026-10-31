import { describe, expect, it } from 'vitest'

import { MENU_ICONS } from './menuIcons'
import { MENU } from './navigation'

describe("the sidebar's icons", () => {
  it('give every entry of the menu its icon, and none to an entry it does not have', () => {
    expect(MENU.filter((entry) => !MENU_ICONS[entry.id]).map((entry) => entry.id)).toEqual([])
    expect(Object.keys(MENU_ICONS).filter((id) => !MENU.some((entry) => entry.id === id))).toEqual([])
  })
})
