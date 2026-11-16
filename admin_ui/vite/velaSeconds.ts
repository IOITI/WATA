// Vela reads bars of seconds since 0.10.5 (`timeframeToMs("10S")` is ten seconds: it loads
// history, heals a gap and tells a new bar from the last by it), and its time axis marks
// ticks within a minute as seconds. It still writes every time to the minute: the axis and
// the crosshair go through its formatters, which the charts give
// (src/features/ta/chart/secondsFormatters.ts); the data window does not, and this plugin
// teaches it the seconds of a bar that is no whole minutes. Until 0.8.5 the plugin also
// taught `timeframeToMs`, the axis and the crosshair, which then took "10S" for an hour.
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

// How the chunk of Vela's core that writes the data window's times is recognised.
export const VELA_CHUNK_MARK = 'function formatStampParts('

export const VELA_SECONDS_PATCHES: VelaPatch[] = [
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
    find: '    const parts = bar ? formatStampParts(bar.time, displayZone(this.scene.timezone, this.coords.barInterval)) : { date: "", time: "" };',
    replace:
      '    const parts = bar ? formatStampParts(bar.time, displayZone(this.scene.timezone, this.coords.barInterval), ' +
      'this.coords.barInterval) : { date: "", time: "" };',
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
