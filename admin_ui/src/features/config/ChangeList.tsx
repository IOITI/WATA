import type { ConfigChange } from './configModel'

/** Value-by-value differences between two versions of the config. */
export function ChangeList({ changes }: { changes: ConfigChange[] }) {
  return (
    <ul className="max-h-64 space-y-2 overflow-y-auto rounded-lg border border-border bg-surface-raised/40 p-3 font-mono text-xs">
      {changes.map((change) => (
        <li key={change.path}>
          <p className="break-all text-foreground">{change.path}</p>
          <p className="text-muted-foreground">
            {change.before ?? 'not set'} → <span className="text-foreground">{change.after ?? 'removed'}</span>
          </p>
        </li>
      ))}
    </ul>
  )
}
