// Vela offers bars of seconds in its menus and its timeframe dialog ("10S"), but its
// chart does not read them: `timeframeToMs` takes "10S" for an hour (its fallback),
// which is what the chart then counts on to load history, heal a gap and tell a new
// bar from the last; and its times stop at the minute. The Technical Analysis pages
// have bars of ten seconds and more (src/features/ta/engine/timeframes.ts): this plugin
// teaches the pinned package seconds where it reads a timeframe and where it writes a
// time (the time axis, the crosshair, the data window).
//
// Each change replaces one exact piece of Vela's code, found once in the chunk that
// holds them all. The build stops when one is not there, so that a new version of
// Vela is looked at before it ships. The same changes go to the dependencies Vite
// prepares for its dev server (`optimizeDeps`), where a plugin's transform never runs.

import type { Plugin } from 'vite'

export interface VelaPatch {
  /** What it changes, for the build's message. */
  what: string
  find: string
  replace: string
}

// How the chunk of Vela's core that reads timeframes is recognised.
export const VELA_CHUNK_MARK = 'function timeframeToMs(timeframe) {'

export const VELA_SECONDS_PATCHES: VelaPatch[] = [
  {
    what: 'timeframeToMs reads a number of seconds ("10S")',
    find: [
      '  const m = /^(\\d+)\\s*(m|h|d|w)?$/i.exec(tf);',
      '  if (m) {',
      '    const n = parseInt(m[1], 10);',
      '    const unit = (m[2] ?? "m").toLowerCase();',
      '    const mult = unit === "m" ? 6e4 : unit === "h" ? 36e5 : unit === "d" ? 864e5 : 6048e5;',
    ].join('\n'),
    replace: [
      '  const m = /^(\\d+)\\s*(s|m|h|d|w)?$/i.exec(tf);',
      '  if (m) {',
      '    const n = parseInt(m[1], 10);',
      '    const unit = (m[2] ?? "m").toLowerCase();',
      '    const mult = unit === "s" ? 1e3 : unit === "m" ? 6e4 : unit === "h" ? 36e5 : unit === "d" ? 864e5 : 6048e5;',
    ].join('\n'),
  },
  {
    what: 'the crosshair writes the seconds of a bar that is no whole minutes',
    find: [
      '  if (barIntervalMs >= DAY) return date;',
      '  return `${date} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;',
    ].join('\n'),
    replace: [
      '  if (barIntervalMs >= DAY) return date;',
      '  if (barIntervalMs > 0 && barIntervalMs % MIN !== 0) return `${date} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())}`;',
      '  return `${date} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;',
    ].join('\n'),
  },
  {
    what: 'the time axis writes the seconds of a tick within a minute',
    find: [
      '      if (h === 0 && m === 0) {',
      '        label = `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;',
      '        major = true;',
      '      } else {',
      '        label = `${pad2(h)}:${pad2(m)}`;',
      '      }',
    ].join('\n'),
    replace: [
      '      const s = d.getUTCSeconds();',
      '      if (h === 0 && m === 0 && s === 0) {',
      '        label = `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;',
      '        major = true;',
      '      } else {',
      '        label = s !== 0 ? `${pad2(h)}:${pad2(m)}:${pad2(s)}` : `${pad2(h)}:${pad2(m)}`;',
      '      }',
    ].join('\n'),
  },
  {
    what: 'the data window writes the seconds of a bar that is no whole minutes',
    find: [
      'function formatStampParts(ms, timeZone) {',
      '  const d = zonedDate(ms, timeZone);',
      '  const p = (n) => n < 10 ? `0${n}` : String(n);',
      '  return {',
      '    date: `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`,',
      '    time: `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`',
      '  };',
      '}',
    ].join('\n'),
    replace: [
      'function formatStampParts(ms, timeZone, barIntervalMs) {',
      '  const d = zonedDate(ms, timeZone);',
      '  const p = (n) => n < 10 ? `0${n}` : String(n);',
      '  const seconds = barIntervalMs > 0 ? barIntervalMs % 6e4 !== 0 : d.getUTCSeconds() !== 0;',
      '  return {',
      '    date: `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`,',
      '    time: `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}${seconds ? `:${p(d.getUTCSeconds())}` : ""}`',
      '  };',
      '}',
    ].join('\n'),
  },
  {
    what: "the data window is given the chart's bar length",
    find: '    const parts = bar ? formatStampParts(bar.time, this.scene.timezone) : { date: "", time: "" };',
    replace: '    const parts = bar ? formatStampParts(bar.time, this.scene.timezone, this.coords.barInterval) : { date: "", time: "" };',
  },
]

function occurrences(code: string, piece: string): number {
  let count = 0
  for (let at = code.indexOf(piece); at !== -1; at = code.indexOf(piece, at + piece.length)) count += 1
  return count
}

/** Vela's chunk with every change made; throws naming the first change whose code is not there exactly once. */
export function patchVela(code: string, patches: VelaPatch[] = VELA_SECONDS_PATCHES): string {
  let patched = code
  for (const patch of patches) {
    const found = occurrences(patched, patch.find)
    if (found !== 1) {
      throw new Error(
        `@luxalgo/vela changed: "${patch.what}" expects its code once, found ${found} times. ` +
          'The charts need it to show bars of seconds (see admin_ui/vite/velaSeconds.ts).',
      )
    }
    patched = patched.replace(patch.find, () => patch.replace)
  }
  return patched
}

function isVelaChunk(code: string, id: string): boolean {
  const path = id.split('?')[0].replaceAll('\\', '/')
  return path.includes('/node_modules/@luxalgo/vela/dist/') && code.includes(VELA_CHUNK_MARK)
}

/** The same changes for the dependencies Vite prepares for its dev server (`optimizeDeps.rolldownOptions.plugins`). */
export function velaSecondsDeps() {
  return {
    name: 'wata-vela-seconds-deps',
    transform(code: string, id: string) {
      return isVelaChunk(code, id) ? { code: patchVela(code) } : null
    },
  }
}

export function velaSeconds(): Plugin {
  let building = false
  let patched = 0
  return {
    name: 'wata-vela-seconds',
    configResolved(config) {
      building = config.command === 'build'
    },
    buildStart() {
      patched = 0
    },
    transform(code, id) {
      if (!isVelaChunk(code, id)) return null
      patched += 1
      return { code: patchVela(code), map: null }
    },
    buildEnd(error) {
      if (building && !error && patched === 0) {
        this.error(
          `@luxalgo/vela changed: no chunk holds "${VELA_CHUNK_MARK}" any more. ` +
            'The charts need it to show bars of seconds (see admin_ui/vite/velaSeconds.ts).',
        )
      }
    },
  }
}
