import { describe, expect, it } from 'vitest'

import { messageAction, scriptOutline } from './outline'

describe('the outline of a script', () => {
  it('lists its alert conditions with their messages, by name or by place', () => {
    const outline = scriptOutline(`//@version=6
indicator("x")
alertcondition(enterLong, title = 'Enter Long', message = '{"action": "long", "indice": "us100", "signal_timestamp": "{{time}}"}')
alertcondition(crossDown, "Cross down", 'it fell')
alertcondition(exitAll, message = "exit", title = "Exit")
alertcondition(other, title = "Computed " + str.tostring(close))
alertcondition(enterLong, title = 'Enter Long', message = 'again')
`)

    expect(outline.alerts).toEqual([
      { title: 'Enter Long', message: '{"action": "long", "indice": "us100", "signal_timestamp": "{{time}}"}' },
      { title: 'Cross down', message: 'it fell' },
      { title: 'Exit', message: 'exit' },
    ])
    expect(outline.alertCalls).toBe(false)
  })

  it('says whether it calls alert(), and lists the titles of its plots', () => {
    const outline = scriptOutline(`//@version=6
indicator("x")
plot(ta.ema(close, 9), "Fast", color.teal)
plot(close, title = "Close")
plot(open)
plotshape(true, "Shape")
if close > open
    alert('{"action": "long"}', alert.freq_once_per_bar_close)
x = array.new_float()
`)

    expect(outline.plots).toEqual(['Fast', 'Close'])
    expect(outline.alertCalls).toBe(true)
    expect(scriptOutline('//@version=6\nindicator("x")\nf = alert.freq_all\nplot(close)\n').alertCalls).toBe(false)
  })

  it('reads the action of WATA\'s webhook JSON in a message, placeholders and all', () => {
    expect(messageAction('{"action": "long", "confidence": {{plot_0}}, "at": "{{time}}"}')).toBe('long')
    expect(messageAction('{"action": "close-position"}')).toBe('close-position')
    expect(messageAction('{"action": "close-long"}')).toBeNull()
    expect(messageAction('buy now')).toBeNull()
    expect(messageAction(null)).toBeNull()
  })
})
