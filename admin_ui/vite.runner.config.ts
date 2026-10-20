import { readFileSync } from 'node:fs'

import { defineConfig } from 'vite'

// The TA engine's Pine runner (src/features/ta/engine/runner.ts) as one file for
// Node, PineTS inside: src/ta_engine/runner/runner.mjs, which the engine starts.
const pinets = JSON.parse(readFileSync(new URL('./node_modules/pinets/package.json', import.meta.url), 'utf8')) as {
  version: string
}

export default defineConfig({
  define: {
    __PINETS_VERSION__: JSON.stringify(pinets.version),
  },
  ssr: {
    // Nothing is installed beside it on the server: everything goes in the file.
    noExternal: true,
    target: 'node',
  },
  build: {
    ssr: 'src/features/ta/engine/runner.ts',
    outDir: '../src/ta_engine/runner',
    emptyOutDir: true,
    target: 'node24',
    copyPublicDir: false,
    rolldownOptions: {
      output: {
        format: 'es',
        entryFileNames: 'runner.mjs',
      },
    },
  },
})
