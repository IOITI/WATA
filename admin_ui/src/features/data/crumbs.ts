import type { BreadcrumbItem } from '@/components/ui/breadcrumb'

import { ASSETS_PATH, PROVIDERS_PATH } from './assets'

/**
 * Where each page of the Data section is, after "Home" (PageHeader adds it).
 * The section has no page of its own: its crumb is not a link.
 */
const DATA: BreadcrumbItem = { label: 'Data' }

export function assetsCrumbs(): BreadcrumbItem[] {
  return [DATA, { label: 'Assets View' }]
}

export function assetCrumbs(name: string): BreadcrumbItem[] {
  return [DATA, { label: 'Assets View', href: ASSETS_PATH }, { label: name }]
}

export function providersCrumbs(): BreadcrumbItem[] {
  return [DATA, { label: 'Providers' }]
}

export function providerCrumbs(name: string): BreadcrumbItem[] {
  return [DATA, { label: 'Providers', href: PROVIDERS_PATH }, { label: name }]
}
