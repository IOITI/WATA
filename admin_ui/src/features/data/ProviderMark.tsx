import { History } from 'lucide-react'

import { BrandMark } from '@/components/BrandMark'
import type { Brand } from '@/components/BrandMark'

// The providers that are a service's own prices carry its mark; Dukascopy's history, an icon.
const BRANDS: Record<string, Brand> = { saxo: 'saxo', capital: 'capital' }

export function ProviderMark({ id, className }: { id: string; className?: string }) {
  const brand = BRANDS[id]
  if (brand) return <BrandMark brand={brand} className={className} />
  return <History aria-hidden className={className ?? 'size-4 shrink-0 text-muted-foreground'} />
}
