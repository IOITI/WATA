// Vela writes its times to the minute (0.10.8): its time axis marks a tick within a minute as a
// 'second' and still writes it HH:MM, and its crosshair drops a bar's seconds. Every Vela chart of
// the pages is given these formatters (`formatters` of its options), which write the seconds of a
// time that is no whole minute and leave every other time to Vela (`ctx.fallback()`), so a chart of
// 1-minute bars reads as before. The data window does not go through Vela's formatters: it is taught
// the seconds when Vela is built (vite/velaSeconds.ts).

import type { VelaFormatters } from '@luxalgo/vela'

const pad2 = (value: number) => (value < 10 ? `0${value}` : String(value))

export const SECONDS_FORMATTERS: Pick<VelaFormatters, 'timeTick' | 'timeStamp'> = {
  /**
   * A tick of the time axis: its seconds when it is not on the minute. Vela marks such a tick as a 'second', or
   * as the day for every tick of a day's first minute (its day boundary reads the hour and the minute alone).
   */
  timeTick(ms, ctx) {
    const wall = new Date(ms + ctx.offsetMs)
    if (wall.getUTCSeconds() === 0 || (ctx.unit !== 'second' && ctx.unit !== 'day')) return ctx.fallback()
    return `${pad2(wall.getUTCHours())}:${pad2(wall.getUTCMinutes())}:${pad2(wall.getUTCSeconds())}`
  },
  /** The crosshair's time: Vela's, and the seconds after it when there are some (time zones differ by whole minutes). */
  timeStamp(ms, ctx) {
    const seconds = new Date(ms).getUTCSeconds()
    return ctx.withTime && seconds !== 0 ? `${ctx.fallback()}:${pad2(seconds)}` : ctx.fallback()
  },
}
