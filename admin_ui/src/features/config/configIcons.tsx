import { cn } from 'ioiti-ui'
import {
  Bot,
  CalendarClock,
  ChartCandlestick,
  ChartNoAxesCombined,
  Cpu,
  Database,
  Disc3,
  Eye,
  FileText,
  FlaskConical,
  KeyRound,
  MonitorCog,
  Radio,
  Scale,
  ScrollText,
  Server,
  SlidersHorizontal,
  SquareFunction,
  Target,
  Undo2,
  Wrench,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { BrandMark } from '@/components/BrandMark'
import type { Brand } from '@/components/BrandMark'
import type { MenuSection } from '@/components/navigation'

// The menu's sections (components/navigation.ts), as the Configuration page groups its parameters under them.
const SECTION_ICONS: Record<MenuSection, LucideIcon> = {
  Trading: ChartCandlestick,
  Analysis: ChartNoAxesCombined,
  Data: Database,
  'Technical Analysis': SquareFunction,
  'AI agent': Bot,
  Operations: Wrench,
}

// A group of parameters: the service's own mark where it is one service's, else what it is about.
const GROUP_ICONS: Record<string, LucideIcon | Brand> = {
  day: Target,
  hours: CalendarClock,
  signals: Radio,
  sizing: Scale,
  exits: Undo2,
  watchlist: Eye,
  report: FileText,
  backtests: FlaskConical,
  saxo: 'saxo',
  recorder: Disc3,
  capital: 'capital',
  ta: Cpu,
  agent: Bot,
  admin: MonitorCog,
  claude: 'claude',
  logs: ScrollText,
  infrastructure: Server,
  credentials: KeyRound,
}

// The blocks of a page that are one service's: its mark beside their heading.
const BLOCK_BRANDS: Record<string, Brand> = {
  'infrastructure/postgresql': 'postgresql',
  'infrastructure/rabbitmq': 'rabbitmq',
  'credentials/saxo': 'saxo',
  'credentials/telegram': 'telegram',
}

function Mark({ icon, className }: { icon: LucideIcon | Brand; className?: string }) {
  if (typeof icon === 'string') return <BrandMark brand={icon} className={className} />
  const Icon = icon
  return <Icon aria-hidden className={cn('size-4 shrink-0', className)} />
}

export function SectionIcon({ section, className }: { section: MenuSection; className?: string }) {
  return <Mark icon={SECTION_ICONS[section] ?? SlidersHorizontal} className={className} />
}

export function GroupIcon({ group, className }: { group: string; className?: string }) {
  return <Mark icon={GROUP_ICONS[group] ?? SlidersHorizontal} className={className} />
}

/** The mark of a block that is one service's, or nothing. */
export function BlockMark({ group, block, className }: { group: string; block: string; className?: string }) {
  const brand = BLOCK_BRANDS[`${group}/${block}`]
  return brand ? <BrandMark brand={brand} className={className} /> : null
}
