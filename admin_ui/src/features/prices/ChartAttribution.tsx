/**
 * The notice the charting library's licence asks for, on every page that
 * shows one of its charts. It goes wherever `PriceChart` goes.
 */
export function ChartAttribution() {
  return (
    <p className="text-[11px] text-muted-foreground">
      TradingView Lightweight Charts™ Copyright (c) 2025 TradingView, Inc.{' '}
      <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer noopener" className="underline underline-offset-2">
        https://www.tradingview.com/
      </a>
    </p>
  )
}
