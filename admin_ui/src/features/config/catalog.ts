/**
 * What the Configuration page makes of the catalog of config.json
 * (src/admin/config_catalog/): where a parameter is in the file, whether it
 * changed, what resetting it writes, and how the page finds and links one.
 * The server stays the authority on values (its validators) and on what a
 * change reaches (its preview); this only places and describes.
 */

import type { MenuSection } from '@/components/navigation'
import type { CatalogBlock, CatalogGroup, CatalogParam, ConfigCatalog, JsonObject, JsonValue } from '@/lib/types'

import { getAt, setAt } from './configModel'
import type { ConfigPath } from './configModel'

const RULE_PATH = /^trade\.rules\[([^\]]+)\](?:\.(.*))?$/
const RULE_NAME = 'trade.rules[*].rule_name'

function isObject(value: JsonValue | undefined): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** The type of the rule a catalog path belongs to, or null for a path outside the rules. */
export function ruleOf(path: string): string | null {
  return RULE_PATH.exec(path)?.[1] ?? null
}

function ruleIndex(config: JsonObject, ruleType: string): number {
  const rules = getAt(config, ['trade', 'rules'])
  return Array.isArray(rules) ? rules.findIndex((rule) => isObject(rule) && rule.rule_type === ruleType) : -1
}

/** Whether ``config`` holds a rule of ``ruleType``. */
export function hasRule(config: JsonObject, ruleType: string): boolean {
  return ruleIndex(config, ruleType) !== -1
}

/**
 * Where the catalog's ``path`` is in ``config``: a rule's keys under its index
 * in ``trade.rules``, since the services find a rule by its type. Null while
 * the rule is absent.
 */
export function configPathOf(config: JsonObject, path: string): ConfigPath | null {
  const match = RULE_PATH.exec(path)
  if (!match) return path.split('.')
  const index = ruleIndex(config, match[1])
  if (index === -1) return null
  return ['trade', 'rules', index, ...(match[2] ? match[2].split('.') : [])]
}

export function valueOf(config: JsonObject, path: string): JsonValue | undefined {
  const at = configPathOf(config, path)
  return at ? getAt(config, at) : undefined
}

/** A copy of ``root`` without the key at ``path``; everything off the path is shared. */
export function removeAt(root: JsonValue | undefined, path: ConfigPath): JsonValue | undefined {
  if (path.length === 0 || root === undefined) return root
  const [key, ...rest] = path
  if (Array.isArray(root) && typeof key === 'number') {
    if (rest.length === 0) return root.filter((_, index) => index !== key)
    const copy = [...root]
    copy[key] = removeAt(copy[key], rest) as JsonValue
    return copy
  }
  if (!isObject(root) || typeof key !== 'string' || !(key in root)) return root
  if (rest.length === 0) {
    const { [key]: _removed, ...kept } = root
    return kept
  }
  return { ...root, [key]: removeAt(root[key], rest) as JsonValue }
}

/** Two values of the file alike, whatever the order of an object's keys. */
export function sameValue(a: JsonValue | undefined, b: JsonValue | undefined): boolean {
  if (a === b) return true
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((item, index) => sameValue(item, b[index]))
  if (isObject(a) && isObject(b)) {
    const keys = Object.keys(a)
    return keys.length === Object.keys(b).length && keys.every((key) => key in b && sameValue(a[key], b[key]))
  }
  return false
}

/** The parameter at ``path``: exact, or the name of any rule. */
export function lookup(catalog: ConfigCatalog, path: string): CatalogParam | undefined {
  const found = catalog.params.find((param) => param.path === path)
  if (found || !RULE_PATH.test(path) || !path.endsWith('.rule_name')) return found
  return catalog.params.find((param) => param.path === RULE_NAME)
}

/** What the services use for ``param``: the value in the file, or its default while absent. */
export function effectiveValue(param: CatalogParam, value: JsonValue | undefined): JsonValue | undefined {
  if (value !== undefined && value !== null) return value
  return param.optional ? (param.default ?? undefined) : value
}

/** Whether ``value`` is not what the services would use with the key absent. */
export function differsFromDefault(param: CatalogParam, value: JsonValue | undefined): boolean {
  if (!param.optional || param.kind === 'rule' || value === undefined) return false
  if (value === null) return param.default !== null
  return !sameValue(value, param.default)
}

/** ``config`` with ``param`` back to what the services use without it: its default, or the key taken out. */
export function resetTo(config: JsonObject, param: CatalogParam): JsonObject {
  const at = configPathOf(config, param.path)
  if (!at) return config
  if (param.default === null || param.default === undefined) return (removeAt(config, at) as JsonObject) ?? config
  return setAt(config, at, param.default) as JsonObject
}

/** ``config`` with the optional rule of ``param`` added, with the settings it is added with. */
export function withRuleOf(config: JsonObject, param: CatalogParam): JsonObject {
  const ruleType = ruleOf(param.path)
  if (!ruleType || hasRule(config, ruleType)) return config
  const rules = getAt(config, ['trade', 'rules'])
  const added = { rule_type: ruleType, rule_name: param.label, rule_config: param.rule_default ?? {} }
  return setAt(config, ['trade', 'rules'], [...(Array.isArray(rules) ? rules : []), added]) as JsonObject
}

/** ``config`` without the rule of ``ruleType``. */
export function withoutRule(config: JsonObject, ruleType: string): JsonObject {
  const rules = getAt(config, ['trade', 'rules'])
  if (!Array.isArray(rules)) return config
  return setAt(config, ['trade', 'rules'], rules.filter((rule) => !(isObject(rule) && rule.rule_type === ruleType))) as JsonObject
}

/** The parameters whose value is not the same in ``saved`` and ``draft``: the unsaved changes, by parameter. */
export function changedParams(params: CatalogParam[], saved: JsonObject, draft: JsonObject): Set<string> {
  const changed = new Set<string>()
  for (const param of params) {
    if (param.path === RULE_NAME) continue
    if (param.kind === 'rule') {
      const ruleType = ruleOf(param.path)
      if (ruleType && hasRule(saved, ruleType) !== hasRule(draft, ruleType)) changed.add(param.path)
      continue
    }
    if (!sameValue(valueOf(saved, param.path), valueOf(draft, param.path))) changed.add(param.path)
  }
  return changed
}

/** The parameters every word of ``query`` is found in: label, path, help, effects, the group's title. */
export function searchParams(catalog: ConfigCatalog, query: string): CatalogParam[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  if (words.length === 0) return []
  const titles = new Map(catalog.groups.map((group) => [group.id, group.title]))
  return catalog.params.filter((param) => {
    const text = [param.label, param.path, param.help, ...param.effects, titles.get(param.group) ?? '']
      .join(' ')
      .toLowerCase()
    return words.every((word) => text.includes(word))
  })
}

/** The id a parameter's row has on its group's page, for a link to lead to it. */
export function anchorOf(path: string): string {
  return `p-${path.replace(/[^a-zA-Z0-9]+/g, '-').replace(/-+$/, '')}`
}

/** Where a parameter is shown: its group's page, scrolled to its row. */
export function hrefOf(param: CatalogParam): string {
  return `/config/${param.group}#${anchorOf(param.path)}`
}

export interface Segment {
  /** The rule the parameters belong to, or null. */
  rule: string | null
  params: CatalogParam[]
}

/** A group's parameters in the catalog's order, those of one rule kept together under it. */
export function segmentsOf(params: CatalogParam[]): Segment[] {
  const segments: Segment[] = []
  for (const param of params) {
    if (param.path === RULE_NAME) continue
    const rule = ruleOf(param.path)
    const last = segments.at(-1)
    if (last && last.rule === rule) last.params.push(param)
    else segments.push({ rule, params: [param] })
  }
  return segments
}

export interface PlacedBlock {
  block: CatalogBlock
  params: CatalogParam[]
}

/**
 * A group's blocks in the catalog's order, each with its parameters in the
 * catalog's order; a block that holds none is left out, and a parameter whose
 * block the catalog does not list comes last, under "Other".
 */
export function blocksOf(catalog: ConfigCatalog, groupId: string): PlacedBlock[] {
  const params = catalog.params.filter((param) => param.group === groupId)
  const placed: PlacedBlock[] = catalog.blocks
    .filter((block) => block.group === groupId)
    .map((block) => ({ block, params: params.filter((param) => param.block === block.id) }))
  const known = new Set(placed.map(({ block }) => block.id))
  const others = params.filter((param) => !known.has(param.block))
  if (others.length > 0) {
    placed.push({ block: { group: groupId, id: 'other', title: 'Other', summary: '', paths: [] }, params: others })
  }
  return placed.filter(({ params: held }) => held.length > 0)
}

export interface SectionGroups {
  section: MenuSection
  groups: CatalogGroup[]
}

/** The menu's sections that have groups, in the menu's order, each with its groups in the catalog's order. */
export function sectionsOf(catalog: ConfigCatalog): SectionGroups[] {
  const sections: MenuSection[] = [...catalog.sections]
  for (const group of catalog.groups) if (!sections.includes(group.section)) sections.push(group.section)
  return sections
    .map((section) => ({ section, groups: catalog.groups.filter((group) => group.section === section) }))
    .filter(({ groups }) => groups.length > 0)
}

/** The anchor of a block on its group's page. */
export function blockAnchorOf(blockId: string): string {
  return `b-${blockId}`
}

export interface GroupCounts {
  total: number
  /** Not what the services would use without the key. */
  changedFromDefault: number
  unsaved: number
  serverOnly: number
  unused: number
}

/** What the overview says of a group. */
export function groupCounts(params: CatalogParam[], draft: JsonObject, unsaved: Set<string>): GroupCounts {
  return params.reduce<GroupCounts>(
    (counts, param) => ({
      total: counts.total + 1,
      changedFromDefault: counts.changedFromDefault + (differsFromDefault(param, valueOf(draft, param.path)) ? 1 : 0),
      unsaved: counts.unsaved + (unsaved.has(param.path) ? 1 : 0),
      serverOnly: counts.serverOnly + (param.access === 'server' || param.access === 'credential' ? 1 : 0),
      unused: counts.unused + (param.access === 'unused' ? 1 : 0),
    }),
    { total: 0, changedFromDefault: 0, unsaved: 0, serverOnly: 0, unused: 0 },
  )
}

/** A value of the file as the page shows it outside a field: a list joined, an object as JSON. */
export function shownValue(value: JsonValue | undefined): string {
  if (value === undefined) return 'not set'
  if (value === null) return 'not set'
  if (typeof value === 'string') return value === '' ? '(empty)' : value
  if (Array.isArray(value) && value.every((item) => typeof item !== 'object' || item === null)) return value.join(', ')
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

/** What a parameter's bounds are, said: "from 0 to 23", "at least 1". */
export function boundsSaid(param: { minimum: number | null; maximum: number | null; unit?: string | null }): string | null {
  const unit = param.unit && param.unit !== '%' ? ` ${param.unit}` : (param.unit ?? '')
  if (param.minimum !== null && param.maximum !== null) return `From ${param.minimum} to ${param.maximum}${unit}`
  if (param.minimum !== null) return `At least ${param.minimum}${unit}`
  if (param.maximum !== null) return `At most ${param.maximum}${unit}`
  return null
}

/** The command that starts these services again on the server, their containers named. */
export function restartCommand(catalog: ConfigCatalog, services: string[]): string {
  const containers = catalog.services.filter((service) => services.includes(service.id)).map((service) => service.container)
  return containers.length ? `docker restart ${containers.join(' ')}` : ''
}
