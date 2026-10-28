import { Code } from 'ioiti-ui'
import type { Change } from 'ioiti-ui'

import type { ConfigChange } from './configModel'

/** Changes of config.json as ioiti-ui's ChangeList says them: each key, from its value before to its value after. */
export function keyChanges(changes: ConfigChange[], under?: string): Change[] {
  return changes.map((change) => ({
    label: <Code className="break-all">{under ? `${under}.${change.path}` : change.path}</Code>,
    before: change.before ?? undefined,
    after: change.after ?? undefined,
  }))
}
