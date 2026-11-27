// One answer of GET /api/market-calendar for the Market calendar page and the dashboard's next day off: the files
// change with a release, the closed dates with a save of config.json.

import { api } from '@/lib/api'

export const MARKET_CALENDAR_QUERY = {
  queryKey: ['market-calendar'],
  queryFn: api.marketCalendar,
  staleTime: 600_000,
  refetchInterval: 1_800_000,
} as const
