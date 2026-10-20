import { readFileSync } from 'node:fs'

import { defineConfig } from 'vite'

// The server's check of a script against TradingView's rules (src/features/ta/engine/checker.ts)
// as one file for Node: src/ta_engine/checker/check.mjs, which a Pine study starts. Apart from the
// engine's runner.mjs: the engine never loads the validator.
const validator = JSON.parse(readFileSync(new URL('./node_modules/pinescript-v6-validator/package.json', import.meta.url), 'utf8')) as {
  version: string
}

export default defineConfig({
  define: {
    __VALIDATOR_VERSION__: JSON.stringify(validator.version),
  },
  ssr: {
    noExternal: true,
    target: 'node',
  },
  build: {
    ssr: 'src/features/ta/engine/checker.ts',
    outDir: '../src/ta_engine/checker',
    emptyOutDir: true,
    target: 'node24',
    copyPublicDir: false,
    rolldownOptions: {
      output: {
        format: 'es',
        entryFileNames: 'check.mjs',
      },
    },
  },
})
