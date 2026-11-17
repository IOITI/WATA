// Operations › Disk: the server's disk as watadisk samples it every hour on the host (src/disk/collect.py), what
// each kind of WATA's data takes of it, the last 14 days, and where it goes under other retentions. Nothing here
// changes a retention: the projection only computes with those asked, and each says where it is set.

import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  Alert,
  AlertDescription,
  AnimatedTabs,
  BarChart,
  Button,
  Disclosure,
  Field,
  formatBytes,
  formatDateTime,
  formatNumber,
  Hint,
  KpiTile,
  LineChart,
  Meter,
  NumberInput,
  PageHeader,
  Panel,
  QueryState,
  StatusBadge,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
} from 'ioiti-ui'
import { useMemo, useState } from 'react'
import { Link } from 'react-router'

import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { DiskOverview, DiskProjection, DiskRetentionKey, DiskRetentions } from '@/lib/types'

import { DiskCleanupsPanel } from './DiskCleanupsPanel'
import {
  changedRetentions,
  changeText,
  dailyStacks,
  daysText,
  FIELD_OF,
  familyRows,
  freePoints,
  LEVEL_LABEL,
  LEVEL_STATUS,
  levelReferences,
  paceSource,
  paceText,
  projectedRows,
  projectionPoints,
  RETENTION_FIELDS,
  retentionError,
  retentionText,
  samplesOver,
  STACKS,
} from './diskModel'

const gb = (value: number) => `${formatNumber(value, 1)} GB`
const gbTick = (value: number) => `${formatNumber(value)} GB`

/** Days until a level as set now, and with the retentions tried when they say otherwise. */
function levelLine(name: string, asSet: number | null, asked: number | null | undefined, projection: DiskProjection, tried: boolean): string {
  const now = daysText(asSet, projection.today, projection.look_days)
  if (!tried || asked === asSet) return `${name}: ${now.toLowerCase()}`
  return `${name}: ${now.toLowerCase()} as set now, ${daysText(asked ?? null, projection.today, projection.look_days).toLowerCase()} with yours`
}

function NowTiles({ overview }: { overview: DiskOverview }) {
  const { timezone } = useSession()
  const { judged, sample } = overview
  const system = sample?.filesystems.find((one) => one.wata) ?? sample?.filesystems[0]
  if (!system || judged.level === 'none') return null
  const usedRatio = (system.total - system.available) / system.total
  const tone = judged.level === 'critical' ? 'negative' : judged.level === 'ok' ? 'neutral' : 'warning'
  const over = samplesOver(sample?.first_at, sample?.at)
  const meter = (
    <Meter
      ratio={usedRatio}
      tone={tone}
      label="Used of the disk"
      scale={[`Low under ${judged.thresholds?.warning_free_percent} % free`, `critical under ${judged.thresholds?.critical_free_percent} %`]}
    />
  )
  const free = {
    label: `Free on ${system.mount}`,
    value: formatBytes(system.available),
    hint: `of ${formatBytes(system.total)}, ${formatNumber(judged.free_percent ?? 0, 1)} % free`,
  }
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 xl:grid-cols-4">
      {judged.level === 'ok' ? (
        <KpiTile {...free}>{meter}</KpiTile>
      ) : (
        <KpiTile {...free} tone={tone === 'negative' ? 'negative' : 'warning'} toneLabel={LEVEL_LABEL[judged.level]}>
          {meter}
        </KpiTile>
      )}
      <KpiTile
        label="Free space a day"
        value={paceText(sample?.trend?.available_per_day ?? null)}
        hint={sample?.trend?.available_per_day === null ? 'Said from a day of samples' : `Over ${formatNumber(sample?.trend?.over_days ?? 0, 1)} days`}
      />
      <KpiTile
        label="Critical level"
        value={judged.days_to_critical === null || judged.days_to_critical === undefined ? 'Not on this pace' : daysText(Math.round(judged.days_to_critical), sample?.at.slice(0, 10))}
        hint={`At the pace of the samples kept; a warning within ${judged.thresholds?.warning_days_left ?? 14} days`}
      />
      <KpiTile
        label="Last sample"
        value={sample ? formatDateTime(sample.at, timezone, false) : '-'}
        hint={`${sample?.samples ?? 0} kept over ${formatNumber(over ?? 0, 1)} days, measured in ${formatNumber(sample?.took_seconds ?? 0, 1)} s`}
      />
    </div>
  )
}

function KindsPanel({ overview }: { overview: DiskOverview }) {
  const rows = familyRows(overview.kinds, overview.families)
  const over = samplesOver(overview.sample?.first_at, overview.sample?.at)
  return (
    <Panel
      id="kinds"
      title="What takes the disk"
      description="Each kind of data WATA keeps, from the newest sample: its size, its pace over the samples kept, and what bounds it. System is what the disk holds besides."
    >
      <Table density="compact" className="min-w-[44rem]">
        <TableHead>
          <TableRow>
            <TableHeaderCell>Kind</TableHeaderCell>
            <TableHeaderCell numeric>Size</TableHeaderCell>
            <TableHeaderCell numeric>Share</TableHeaderCell>
            <TableHeaderCell numeric>A day</TableHeaderCell>
            <TableHeaderCell numeric>7 days</TableHeaderCell>
            <TableHeaderCell>Kept</TableHeaderCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => [
            <TableRow key={row.family} className="bg-surface-raised/40">
              <TableHeaderCell scope="rowgroup" className="font-semibold">
                {row.family}
              </TableHeaderCell>
              <TableCell numeric className="font-semibold">
                {formatBytes(row.bytes)}
              </TableCell>
              <TableCell numeric />
              <TableCell numeric />
              <TableCell numeric />
              <TableCell />
            </TableRow>,
            ...row.kinds.map((kind) => (
              <TableRow key={kind.id}>
                <TableHeaderCell scope="row" className="pl-5 font-normal">
                  <Hint content={kind.note}>
                    <span className="cursor-help underline decoration-dotted underline-offset-2">{kind.label}</span>
                  </Hint>
                </TableHeaderCell>
                <TableCell numeric>{kind.bytes === null ? 'Not measured' : formatBytes(kind.bytes)}</TableCell>
                <TableCell numeric>{kind.share === null ? '-' : `${formatNumber(kind.share, 1)} %`}</TableCell>
                <TableCell numeric>
                  <Hint content={paceSource(kind, over)}>
                    <span>{paceText(kind.per_day, '')}</span>
                  </Hint>
                </TableCell>
                <TableCell numeric>{changeText(kind.week)}</TableCell>
                <TableCell>
                  {kind.knob ? (
                    <Link className="underline-offset-2 hover:underline" to={FIELD_OF[kind.knob].href}>
                      {retentionText(kind.knob, kind.retention)}
                    </Link>
                  ) : kind.model === 'cap' ? (
                    'Rotated'
                  ) : kind.model === 'backups' ? (
                    '14 nights, 4 Sundays'
                  ) : (
                    <span className="text-muted-foreground">Never removed</span>
                  )}
                </TableCell>
              </TableRow>
            )),
          ])}
        </TableBody>
      </Table>
    </Panel>
  )
}

function DaysPanel({ overview }: { overview: DiskOverview }) {
  const { timezone } = useSession()
  const system = overview.sample?.filesystems.find((one) => one.wata)
  if (!system || overview.series.length < 2) return null
  const thresholds = {
    warning_free_percent: overview.judged.thresholds?.warning_free_percent ?? 15,
    critical_free_percent: overview.judged.thresholds?.critical_free_percent ?? 5,
  }
  return (
    <Panel id="days" title="The last 14 days" description="The free space at each hourly sample, against the two levels; and what each group took at the end of each day.">
      <div className="grid gap-6 xl:grid-cols-2">
        <LineChart
          data={freePoints(overview.series)}
          series={[{ key: 'free', label: 'Free' }]}
          area
          references={levelReferences(system.total, thresholds)}
          zero
          timeZone={timezone}
          formatValue={gb}
          formatTick={gbTick}
          ariaLabel="Free space of the disk at each sample"
        />
        <BarChart
          data={dailyStacks(overview.series)}
          series={STACKS.map((stack) => ({ key: stack.key, label: stack.label }))}
          variant="stacked"
          formatValue={gb}
          formatTick={gbTick}
          ariaLabel="What each group took at the end of each day"
        />
      </div>
    </Panel>
  )
}

const HORIZON_LABEL: Record<number, string> = { 30: '1 month', 90: '3 months', 180: '6 months', 365: '1 year' }

function ProjectionPanel({ overview }: { overview: DiskOverview }) {
  const current = overview.retentions.values
  const [draft, setDraft] = useState<Partial<Record<DiskRetentionKey, number | null>>>(current)
  const [asked, setAsked] = useState<Partial<DiskRetentions>>({})
  const [horizon, setHorizon] = useState(365)
  const kindsById = useMemo(() => new Map(overview.kinds.map((kind) => [kind.id, kind])), [overview.kinds])

  const errors = Object.fromEntries(
    RETENTION_FIELDS.map((field) => [field.key, retentionError(draft[field.key] ?? null, overview.retentions.bounds[field.key])]),
  ) as Record<DiskRetentionKey, string | null>
  const valid = Object.values(errors).every((error) => error === null)
  const asSetQuery = useQuery({
    queryKey: ['disk', 'projection', {}],
    queryFn: () => api.diskProjection({ retentions: {} }),
    staleTime: 60_000,
  })
  const askedQuery = useQuery({
    queryKey: ['disk', 'projection', asked],
    queryFn: () => api.diskProjection({ retentions: asked }),
    enabled: Object.keys(asked).length > 0,
    placeholderData: keepPreviousData,
  })
  const asSet = asSetQuery.data
  const withAsked: DiskProjection | null = Object.keys(asked).length > 0 ? (askedQuery.data ?? null) : null
  const total = asSet?.total ?? 0
  const rows = asSet ? projectedRows(asSet, withAsked, horizon) : []
  const atHorizon = (projection: DiskProjection | null | undefined) => projection?.horizons.find((one) => one.days === horizon)

  return (
    <Panel
      id="projection"
      title="Where it goes"
      description="Each kind at its pace of the samples kept, under the retentions as set now and under those you try here. A retention tried here is only computed with: change it where it is set."
    >
      <form
        className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
        onSubmit={(event) => {
          event.preventDefault()
          if (valid) setAsked(changedRetentions(draft as Partial<DiskRetentions>, current))
        }}
      >
        {RETENTION_FIELDS.map((field) => (
          <Field
            key={field.key}
            label={field.label}
            error={errors[field.key]}
            description={
              <>
                Set: {retentionText(field.key, current[field.key])}
                {field.zero && current[field.key] !== 0 ? ` (0 keeps ${field.zero})` : ''} ·{' '}
                <Link className="underline underline-offset-2" to={field.href}>
                  {field.where}
                </Link>
              </>
            }
          >
            <NumberInput
              value={draft[field.key] ?? null}
              onChange={(value) => setDraft((before) => ({ ...before, [field.key]: value }))}
              min={overview.retentions.bounds[field.key]?.[0]}
              max={overview.retentions.bounds[field.key]?.[1]}
              integer
              suffix={field.unit}
              label={field.label}
            />
          </Field>
        ))}
        <div className="flex items-end gap-2">
          <Button type="submit" disabled={!valid}>
            Project
          </Button>
          <Button
            type="button"
            variant="ghost"
            disabled={Object.keys(asked).length === 0}
            onClick={() => {
              setAsked({})
              setDraft(current)
            }}
          >
            Back to the retentions set
          </Button>
        </div>
      </form>

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <AnimatedTabs
          tabs={overview.horizons.map((days) => ({ id: String(days), label: HORIZON_LABEL[days] ?? `${days} days` }))}
          activeTab={String(horizon)}
          onChange={(id) => setHorizon(Number(id))}
          renderContent={false}
        />
        {asSet && (
          <p className="text-xs text-muted-foreground">
            {levelLine('Low', asSet.days_to_warning, withAsked?.days_to_warning, asSet, Boolean(withAsked))}
            {' · '}
            {levelLine('Critical', asSet.days_to_critical, withAsked?.days_to_critical, asSet, Boolean(withAsked))}
          </p>
        )}
      </div>

      <QueryState isPending={asSetQuery.isPending} error={asSetQuery.error ?? askedQuery.error} hasData={Boolean(asSet)}>
        {asSet && (
          <div className="mt-4 grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <LineChart
              data={projectionPoints(asSet, withAsked).filter((_, index) => index <= horizon)}
              series={[
                { key: 'asSet', label: 'As set now', ...(withAsked ? { dashed: true } : {}) },
                ...(withAsked ? [{ key: 'asked', label: 'With the retentions tried' }] : []),
              ]}
              references={levelReferences(total, asSet.thresholds)}
              zero
              formatValue={gb}
              formatTick={gbTick}
              showLegend={Boolean(withAsked)}
              ariaLabel="Free space projected day by day"
            />
            <div>
              <p className="mb-2 text-sm">
                In {HORIZON_LABEL[horizon] ?? `${horizon} days`}: {formatBytes(atHorizon(asSet)?.available)} free as set now
                {withAsked ? `, ${formatBytes(atHorizon(withAsked)?.available)} with the retentions tried` : ''}.
              </p>
              <Table density="compact">
                <TableHead>
                  <TableRow>
                    <TableHeaderCell>Kind</TableHeaderCell>
                    <TableHeaderCell numeric>Now</TableHeaderCell>
                    <TableHeaderCell numeric>As set</TableHeaderCell>
                    {withAsked && <TableHeaderCell numeric>Tried</TableHeaderCell>}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {rows.slice(0, 12).map((row) => (
                    <TableRow key={row.id}>
                      <TableHeaderCell scope="row" className="font-normal">
                        {kindsById.get(row.id)?.label ?? row.id}
                        {row.steady && (
                          <span className="block text-xs text-muted-foreground">
                            Settles at {formatBytes(row.steady[0])}
                            {row.steady[1] !== row.steady[0] ? ` to ${formatBytes(row.steady[1])}` : ''}
                          </span>
                        )}
                      </TableHeaderCell>
                      <TableCell numeric>{formatBytes(row.now)}</TableCell>
                      <TableCell numeric>{formatBytes(row.asSet)}</TableCell>
                      {withAsked && <TableCell numeric>{formatBytes(row.asked)}</TableCell>}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        )}
      </QueryState>
      <p className="mt-4 text-xs text-muted-foreground">
        A kind whose pace is not known yet stays as it is. Growth only: a kind that shrank over the samples (a prune, a run removed) is not taken to keep shrinking. The Sunday dumps hold the bars, so they grow with them.
        {overview.details.docker?.reclaimable?.build_cache
          ? ` Docker could give back ${formatBytes(overview.details.docker.reclaimable.build_cache)} of its build cache, which WATA never prunes.`
          : ''}
      </p>
    </Panel>
  )
}

function DetailsPanel({ overview }: { overview: DiskOverview }) {
  const details = overview.details
  const errors = Object.entries(overview.sample?.errors ?? {})
  return (
    <Panel id="details" title="In detail" description="What the newest sample measured.">
      <div className="space-y-3">
        {errors.length > 0 && (
          <Alert tone="warning">
            <AlertDescription>
              Not measured this time: {errors.map(([source, error]) => `${source} (${error})`).join('; ')}.
            </AlertDescription>
          </Alert>
        )}
        {details.tables && details.tables.length > 0 && (
          <Disclosure variant="boxed" summary={`Database tables (${formatBytes(details.database_bytes ?? null)} in all)`}>
            <Table density="compact">
              <TableBody>
                {details.tables.map((table) => (
                  <TableRow key={table.name}>
                    <TableHeaderCell scope="row" className="font-mono font-normal">
                      {table.name}
                      {details.partitions?.[table.name] && (
                        <span className="block font-sans text-xs text-muted-foreground">
                          {details.partitions[table.name].map((part) => `${part.month}: ${formatBytes(part.bytes)}`).join(' · ')}
                        </span>
                      )}
                    </TableHeaderCell>
                    <TableCell numeric>{formatBytes(table.bytes)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Disclosure>
        )}
        {details.logs && details.logs.length > 0 && (
          <Disclosure variant="boxed" summary={`WATA's logs, each rotated at ${formatBytes((details.log_cap ?? 0) / Math.max(details.logs.length, 1))}`}>
            <Table density="compact">
              <TableBody>
                {details.logs.map((log) => (
                  <TableRow key={log.service}>
                    <TableHeaderCell scope="row" className="font-mono font-normal">
                      {log.service}
                    </TableHeaderCell>
                    <TableCell numeric>{formatBytes(log.bytes)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Disclosure>
        )}
        {details.releases && details.releases.length > 0 && (
          <Disclosure variant="boxed" summary={`Release folders (${details.releases.length})`}>
            <p className="mb-2 text-xs text-muted-foreground">watadeploy prune keeps the newest three and those a container runs.</p>
            <Table density="compact">
              <TableBody>
                {details.releases.map((release) => (
                  <TableRow key={release.version}>
                    <TableHeaderCell scope="row" className="font-normal">
                      {release.version}
                    </TableHeaderCell>
                    <TableCell numeric>{formatBytes(release.bytes)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Disclosure>
        )}
        {details.dumps && details.dumps.length > 0 && (
          <Disclosure variant="boxed" summary={`Database dumps (${details.dumps.length})`}>
            <Table density="compact">
              <TableBody>
                {details.dumps.map((dump) => (
                  <TableRow key={dump.file}>
                    <TableHeaderCell scope="row" className="font-mono font-normal">
                      {dump.file}
                      {dump.full && <span className="ml-2 font-sans text-xs text-muted-foreground">with the bars</span>}
                    </TableHeaderCell>
                    <TableCell numeric>{formatBytes(dump.bytes)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Disclosure>
        )}
        {details.docker && (
          <Disclosure variant="boxed" summary="Docker">
            <Table density="compact">
              <TableHead>
                <TableRow>
                  <TableHeaderCell>What</TableHeaderCell>
                  <TableHeaderCell numeric>Size</TableHeaderCell>
                  <TableHeaderCell numeric>Could be given back</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {(['images', 'build_cache', 'containers', 'volumes'] as const).map((part) => (
                  <TableRow key={part}>
                    <TableHeaderCell scope="row" className="font-normal">
                      {{ images: 'Images', build_cache: 'Build cache', containers: 'Containers', volumes: 'Volumes' }[part]}
                    </TableHeaderCell>
                    <TableCell numeric>{formatBytes(details.docker?.[part] ?? null)}</TableCell>
                    <TableCell numeric>{formatBytes(details.docker?.reclaimable?.[part] ?? null)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Disclosure>
        )}
      </div>
    </Panel>
  )
}

export function DiskPage() {
  const query = useQuery({ queryKey: ['disk'], queryFn: api.disk, refetchInterval: 300_000 })
  const overview = query.data
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Disk"
        description="The server's disk, sampled every hour on the host: what WATA's data takes of it, and where it goes."
        actions={overview && <StatusBadge status={LEVEL_STATUS[overview.judged.level]} label={LEVEL_LABEL[overview.judged.level]} />}
      />
      <QueryState isPending={query.isPending} error={query.error} hasData={Boolean(overview)}>
        {overview && (
          <>
            {overview.judged.level === 'none' ? (
              <Alert tone="neutral">
                <AlertDescription>
                  {overview.judged.reason}. It runs every hour once the release is installed (<code>watadisk.timer</code>); <code>watadisk now</code> on
                  the server takes one at once.
                </AlertDescription>
              </Alert>
            ) : (
              overview.judged.level !== 'ok' && (
                <Alert tone={overview.judged.level === 'critical' ? 'danger' : 'warning'}>
                  <AlertDescription>{overview.judged.reason}.</AlertDescription>
                </Alert>
              )
            )}
            <NowTiles overview={overview} />
            {overview.kinds.length > 0 && <KindsPanel overview={overview} />}
            <DiskCleanupsPanel />
            <DaysPanel overview={overview} />
            {/* Made again when a retention is set elsewhere meanwhile: the form starts from the values set now. */}
            {overview.sample && <ProjectionPanel key={JSON.stringify(overview.retentions.values)} overview={overview} />}
            {overview.sample && <DetailsPanel overview={overview} />}
            <p className="text-xs text-muted-foreground">
              The levels are set in{' '}
              <Link className="underline underline-offset-2" to="/config">
                Configuration
              </Link>{' '}
              (<code>reporting.disk_*</code>): below them the health says so, Telegram once, and the scheduler each morning.
            </p>
          </>
        )}
      </QueryState>
    </div>
  )
}
