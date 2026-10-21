import type { JsonObject, JsonValue } from '@/lib/types'

/** A location in the config: object keys and array indices from the root. */
export type ConfigPath = readonly (string | number)[]

function isObject(value: JsonValue | undefined): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function getAt(root: JsonValue | undefined, path: ConfigPath): JsonValue | undefined {
  let node: JsonValue | undefined = root
  for (const key of path) {
    if (Array.isArray(node) && typeof key === 'number') node = node[key]
    else if (isObject(node) && typeof key === 'string') node = node[key]
    else return undefined
  }
  return node
}

/** A copy of `root` with the value at `path` replaced; everything off the path is shared, not copied. */
export function setAt(root: JsonValue | undefined, path: ConfigPath, value: JsonValue): JsonValue {
  if (path.length === 0) return value
  const [key, ...rest] = path
  if (typeof key === 'number') {
    const copy = Array.isArray(root) ? [...root] : []
    copy[key] = setAt(copy[key], rest, value)
    return copy
  }
  const copy: JsonObject = isObject(root) ? { ...root } : {}
  copy[key] = setAt(copy[key], rest, value)
  return copy
}

export function parsePath(dotted: string): ConfigPath {
  return dotted.split('.')
}

/** Where the `rule_config` of the rule of this type lives, or null when the rule is absent. */
export function ruleConfigPath(config: JsonObject, ruleType: string): ConfigPath | null {
  const rules = getAt(config, ['trade', 'rules'])
  if (!Array.isArray(rules)) return null
  const index = rules.findIndex((rule) => isObject(rule) && rule.rule_type === ruleType)
  return index === -1 ? null : ['trade', 'rules', index, 'rule_config']
}

/** The rules of ``trade.rules`` with ``rule`` added, if no rule of its type is there yet. */
export function withRule(config: JsonObject, rule: JsonObject): JsonValue[] {
  const rules = getAt(config, ['trade', 'rules'])
  const list = Array.isArray(rules) ? rules : []
  return list.some((entry) => isObject(entry) && entry.rule_type === rule.rule_type) ? list : [...list, rule]
}

/** The rules of ``trade.rules`` without those of ``ruleType``. */
export function withoutRule(config: JsonObject, ruleType: string): JsonValue[] {
  const rules = getAt(config, ['trade', 'rules'])
  return (Array.isArray(rules) ? rules : []).filter((entry) => !(isObject(entry) && entry.rule_type === ruleType))
}

/** ``chosen`` with ``item`` in or out, in the order of ``all``. */
export function toggled(all: readonly string[], chosen: JsonValue | undefined, item: string, on: boolean): string[] {
  const current = new Set(Array.isArray(chosen) ? chosen.filter((value): value is string => typeof value === 'string') : [])
  if (on) current.add(item)
  else current.delete(item)
  return all.filter((value) => current.has(value))
}

/** Only the parts of the config the server lets this interface change. */
export function pickEditable(config: JsonObject, editablePaths: readonly string[]): JsonObject {
  let picked: JsonValue = {}
  for (const dotted of editablePaths) {
    const path = parsePath(dotted)
    const value = getAt(config, path)
    if (value !== undefined) picked = setAt(picked, path, value)
  }
  return picked as JsonObject
}

/** `config` with its editable parts replaced by those of `edited`. */
export function applyEditable(config: JsonObject, edited: JsonObject, editablePaths: readonly string[]): JsonObject {
  let result: JsonValue = config
  for (const dotted of editablePaths) {
    const path = parsePath(dotted)
    const value = getAt(edited, path)
    if (value !== undefined) result = setAt(result, path, value)
  }
  return result as JsonObject
}

function flatten(value: JsonValue | undefined, prefix: string, into: Map<string, string>): void {
  if (Array.isArray(value)) {
    if (value.length === 0) into.set(prefix, '[]')
    value.forEach((item, index) => flatten(item, `${prefix}[${index}]`, into))
  } else if (isObject(value)) {
    const keys = Object.keys(value)
    if (keys.length === 0) into.set(prefix, '{}')
    for (const key of keys) flatten(value[key], prefix ? `${prefix}.${key}` : key, into)
  } else if (value !== undefined) {
    into.set(prefix, JSON.stringify(value))
  }
}

export interface ConfigChange {
  path: string
  before: string | null
  after: string | null
}

/** Leaf-by-leaf differences between two configs, for the review before saving. */
export function diffConfigs(before: JsonObject, after: JsonObject): ConfigChange[] {
  const beforeLeaves = new Map<string, string>()
  const afterLeaves = new Map<string, string>()
  flatten(before, '', beforeLeaves)
  flatten(after, '', afterLeaves)

  const changes: ConfigChange[] = []
  for (const [path, value] of beforeLeaves) {
    const next = afterLeaves.get(path)
    if (next === undefined) changes.push({ path, before: value, after: null })
    else if (next !== value) changes.push({ path, before: value, after: next })
  }
  for (const [path, value] of afterLeaves) {
    if (!beforeLeaves.has(path)) changes.push({ path, before: null, after: value })
  }
  return changes.sort((a, b) => a.path.localeCompare(b.path))
}

/**
 * What restoring `backup` would change in `current`. Like a save, a restore
 * only takes the editable sections of the backup, and leaves the protected
 * settings inside them as they are now.
 */
export function restoreChanges(
  current: JsonObject,
  backup: JsonObject,
  editablePaths: readonly string[],
  protectedPaths: readonly string[],
): ConfigChange[] {
  const isProtected = (path: string) =>
    protectedPaths.some((kept) => path === kept || path.startsWith(`${kept}.`) || path.startsWith(`${kept}[`))
  return diffConfigs(pickEditable(current, editablePaths), pickEditable(backup, editablePaths)).filter(
    (change) => !isProtected(change.path),
  )
}

export type ParsedJson = { ok: true; value: JsonObject } | { ok: false; error: string }

export function parseJsonObject(text: string): ParsedJson {
  try {
    const value: JsonValue = JSON.parse(text)
    if (!isObject(value)) return { ok: false, error: 'The document must be a JSON object' }
    return { ok: true, value }
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : 'Invalid JSON' }
  }
}
