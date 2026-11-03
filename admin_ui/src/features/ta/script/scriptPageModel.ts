// A script's page (/ta/indicators/<id>): its tabs, each with its address, and what they say of the
// script: the rules that run it, its runs and their verdicts, each version's uses. Pure.

import type { Judged } from '@/features/backtests/verdict'
import type { BacktestRunListed, TaRule, TaRuleMode, TaScriptKind, TaScriptSummary } from '@/lib/types'

export const SCRIPT_TABS = ['editor', 'rule', 'backtests', 'rules', 'history'] as const
export type ScriptTab = (typeof SCRIPT_TABS)[number]

export const TAB_LABEL: Record<ScriptTab, string> = {
  editor: 'Editor', rule: 'As a rule', backtests: 'Backtests', rules: 'Rules', history: 'History',
}

/**
 * The tabs a script has: a library runs inside the scripts that import it, is never backtested alone, and is no
 * rule's script (its rule check is theirs).
 */
export function tabsOf(kind: TaScriptKind, backtests: boolean): ScriptTab[] {
  return SCRIPT_TABS.filter((tab) => (tab !== 'backtests' || (backtests && kind !== 'library')) && (tab !== 'rule' || kind !== 'library'))
}

/** The tab an address names; one the script has not is its editor. */
export function scriptTabOf(value: string | null, tabs: readonly ScriptTab[]): ScriptTab {
  return tabs.find((tab) => tab === value) ?? 'editor'
}

/** A tab's address: the editor is the script's own address; the backtests and the rule check of one version keep it. */
export function scriptTabHref(tab: ScriptTab, version: number | null = null): string {
  if (tab === 'editor') return '?'
  return (tab === 'backtests' || tab === 'rule') && version !== null ? `?tab=${tab}&version=${version}` : `?tab=${tab}`
}

/** The editor at a line: where a finding of the rule check leads. */
export function editorLineHref(line: number): string {
  return `?line=${line}`
}

/** The line an address names in the editor, null when none. */
export function lineOf(value: string | null): number | null {
  const line = Number(value)
  return Number.isInteger(line) && line >= 1 ? line : null
}

/** The version an address names among the script's; null for every version. */
export function versionOf(value: string | null, script: Pick<TaScriptSummary, 'versions'>): number | null {
  const number = Number(value)
  return script.versions.some((entry) => entry.number === number) ? number : null
}

/** The version of a script a rule runs: its own script's, or the library's it pins; null when it runs neither. */
export function pinnedVersion(rule: TaRule, scriptId: string): number | null {
  if (rule.script.id === scriptId) return rule.script.version
  return rule.libraries.find((library) => library.id === scriptId)?.version ?? null
}

/** The rules that run a script, at any version: those of an indicator or strategy, or those whose pins hold a library. */
export function rulesRunning(scriptId: string, rules: readonly TaRule[]): TaRule[] {
  return rules.filter((rule) => pinnedVersion(rule, scriptId) !== null)
}

export function modeCounts(rules: readonly TaRule[]): Record<TaRuleMode, number> {
  const counts: Record<TaRuleMode, number> = { live: 0, shadow: 0, off: 0 }
  for (const rule of rules) counts[rule.mode] += 1
  return counts
}

const MODE_SAID: Record<TaRuleMode, string> = { live: 'live', shadow: 'in shadow', off: 'off' }

/** "1 live · 2 in shadow": the modes the rules are in, live first; "None" without a rule. */
export function modesSaid(counts: Record<TaRuleMode, number>): string {
  const said = (['live', 'shadow', 'off'] as const).filter((mode) => counts[mode] > 0).map((mode) => `${counts[mode]} ${MODE_SAID[mode]}`)
  return said.length > 0 ? said.join(' · ') : 'None'
}

/** "Runs v3; v5 is the newest", or "Runs v5, the newest": the version a rule pins against the script's newest. */
export function pinnedSaid(rule: TaRule, script: Pick<TaScriptSummary, 'id' | 'version'>): string {
  const version = pinnedVersion(rule, script.id)
  if (version === null) return 'Runs it no more'
  return version === script.version ? `Runs v${version}, the newest` : `Runs v${version}; v${script.version} is the newest`
}

/** The runs that ran a script, at any version, from a list of runs. */
export function runsOfScript(runs: readonly BacktestRunListed[], scriptId: string): BacktestRunListed[] {
  return runs.filter((run) => run.source.script?.id === scriptId)
}

/** What a script's runs say together: those of its own (not a stress or a model check of one), and how many pass every check. */
export function runsSaid(runs: readonly BacktestRunListed[], judged: ReadonlyMap<string, Judged>): { runs: number; passing: number } {
  const own = runs.filter((run) => !run.stress_of && !run.model_check_of)
  return { runs: own.length, passing: own.filter((run) => judged.get(run.id)?.verdict.tone === 'pass').length }
}

/** What each version of a script was used by: its runs and the rules that pin it. */
export interface VersionUse {
  runs: number
  rules: TaRule[]
}

export function versionUses(
  script: Pick<TaScriptSummary, 'id' | 'versions'>,
  runs: readonly BacktestRunListed[],
  rules: readonly TaRule[],
): Map<number, VersionUse> {
  const uses = new Map<number, VersionUse>(script.versions.map((entry) => [entry.number, { runs: 0, rules: [] }]))
  for (const run of runsOfScript(runs, script.id)) {
    const use = uses.get(run.source.script?.version ?? -1)
    if (use) use.runs += 1
  }
  for (const rule of rules) {
    const use = uses.get(pinnedVersion(rule, script.id) ?? -1)
    if (use) use.rules.push(rule)
  }
  return uses
}
