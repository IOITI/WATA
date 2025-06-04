package message_helper

import (
	"fmt"
	"sort"
	"strings"
	// "time" // Not directly needed here, but often related to stats
)

// formatFloatPtr formats a *float64 for display, handling nil as "N/A".
func formatFloatPtr(val *float64, precision int) string {
	if val == nil {
		return "N/A"
	}
	return fmt.Sprintf("%.*f", precision, *val)
}

// AppendPerformanceMessage appends formatted percentage data to an existing message string.
// percentages is a map where keys are descriptions (e.g., "Daily", "Last 7 Days")
// and values are *float64 to allow for nil (N/A) percentages.
func AppendPerformanceMessage(message string, title string, percentages map[string]*float64) string {
	var builder strings.Builder
	builder.WriteString(message) // Append to existing message

	if title != "" {
		builder.WriteString(fmt.Sprintf("\n\n--- %s ---", strings.ToUpper(title)))
	}

	// Sort keys for consistent output order
	keys := make([]string, 0, len(percentages))
	for k := range percentages {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	for _, key := range keys {
		builder.WriteString(fmt.Sprintf("\n%s: %s%%", key, formatFloatPtr(percentages[key], 2)))
	}
	return builder.String()
}

// FormatGeneralStats formats a slice of general statistics maps.
// Each map is expected to have keys like "pair", "total_trades", "win_rate", "total_pnl_percent".
func FormatGeneralStats(generalStats []map[string]interface{}) string {
	if len(generalStats) == 0 {
		return "No general statistics available."
	}

	var builder strings.Builder
	builder.WriteString("--- GENERAL STATS ---")

	for _, stats := range generalStats {
		pair, _ := stats["pair"].(string)
		totalTrades, _ := stats["total_trades"].(float64) // JSON numbers are float64
		winRate, _ := stats["win_rate"].(*float64)        // Assuming it could be nil
		totalPnlPercent, _ := stats["total_pnl_percent"].(*float64)

		builder.WriteString(fmt.Sprintf("\nPair: %s", pair))
		builder.WriteString(fmt.Sprintf("\n  Total Trades: %.0f", totalTrades))
		builder.WriteString(fmt.Sprintf("\n  Win Rate: %s%%", formatFloatPtr(winRate, 2)))
		builder.WriteString(fmt.Sprintf("\n  Total P&L: %s%%", formatFloatPtr(totalPnlPercent, 2)))
	}
	return builder.String()
}

// FormatDetailStats formats a slice of detailed statistics maps.
// Each map can have various keys representing different detailed metrics.
func FormatDetailStats(detailStats []map[string]interface{}) string {
	if len(detailStats) == 0 {
		return "No detailed statistics available."
	}

	var builder strings.Builder
	builder.WriteString("\n\n--- DETAILED STATS ---")

	for i, statsMap := range detailStats {
		builder.WriteString(fmt.Sprintf("\nDetail Set %d:", i+1))
		// Sort keys for consistent output
		keys := make([]string, 0, len(statsMap))
		for k := range statsMap {
			keys = append(keys, k)
		}
		sort.Strings(keys)

		for _, key := range keys {
			value := statsMap[key]
			// Try to format based on type for better readability
			switch v := value.(type) {
			case float64:
				builder.WriteString(fmt.Sprintf("\n  %s: %.2f", key, v))
			case *float64:
				builder.WriteString(fmt.Sprintf("\n  %s: %s", key, formatFloatPtr(v, 2)))
			case string:
				builder.WriteString(fmt.Sprintf("\n  %s: %s", key, v))
			case int, int32, int64:
				builder.WriteString(fmt.Sprintf("\n  %s: %d", key, v))
			case bool:
				builder.WriteString(fmt.Sprintf("\n  %s: %t", key, v))
			default:
				builder.WriteString(fmt.Sprintf("\n  %s: %v", key, v))
			}
		}
	}
	return builder.String()
}

// DailyStatItem represents a single item in the daily stats (e.g., for one trading pair).
type DailyStatItem struct {
	Pair         string `json:"pair"` // Assuming "pair" is a key from the map
	TotalTrades  int    `json:"total_trades"`
	WinningTrades int    `json:"winning_trades"`
	LosingTrades int    `json:"losing_trades"`
	// Add other fields as they appear in the map, ensure types match JSON unmarshal behavior (float64 for numbers)
}


// GenerateDailyStatsMessage formats daily statistics.
// statsOfTheDay is expected to be a map where keys are dates (YYYY-MM-DD)
// and values are slices of maps (each map representing stats for a pair on that day).
func GenerateDailyStatsMessage(statsOfTheDay map[string][]map[string]interface{}) string {
	if len(statsOfTheDay) == 0 {
		return "No daily statistics available."
	}

	var builder strings.Builder
	builder.WriteString("--- DAILY STATS ---")

	// Sort dates for consistent output
	dates := make([]string, 0, len(statsOfTheDay))
	for date := range statsOfTheDay {
		dates = append(dates, date)
	}
	sort.Strings(dates)

	for _, date := range dates {
		builder.WriteString(fmt.Sprintf("\n\nDate: %s", date))
		dailyPairStats := statsOfTheDay[date]
		if len(dailyPairStats) == 0 {
			builder.WriteString("\n  No trades for this day.")
			continue
		}
		for _, pairStatsMap := range dailyPairStats {
			// Safely extract and type-assert values from map[string]interface{}
			pair, _ := pairStatsMap["pair"].(string)
			totalTradesFloat, ttOk := pairStatsMap["total_trades"].(float64) // JSON numbers are float64
			winningTradesFloat, wtOk := pairStatsMap["winning_trades"].(float64)
			losingTradesFloat, ltOk := pairStatsMap["losing_trades"].(float64)

			builder.WriteString(fmt.Sprintf("\n  Pair: %s", pair))
			if ttOk {
				builder.WriteString(fmt.Sprintf("\n    Total Trades: %d", int(totalTradesFloat)))
			} else {
				builder.WriteString("\n    Total Trades: N/A")
			}
			if wtOk {
				builder.WriteString(fmt.Sprintf("\n    Winning: %d", int(winningTradesFloat)))
			} else {
				builder.WriteString("\n    Winning: N/A")
			}
			if ltOk {
				builder.WriteString(fmt.Sprintf("\n    Losing: %d", int(losingTradesFloat)))
			} else {
				builder.WriteString("\n    Losing: N/A")
			}
			// Add more fields as needed, e.g., PNL
		}
	}
	return builder.String()
}

// PerformanceData holds various percentage metrics for different periods.
// This is a suggested structure for what might be passed to GeneratePerformanceStatsMessage.
type PerformanceData struct {
	DayPercent        *float64
	Last7DaysPercent  *float64
	Last30DaysPercent *float64
	// Add more fields as needed, e.g., for theoretical percentages
	TheoreticalDayPercent              *float64
	TheoreticalBestDayPercent          *float64
	TheoreticalLast7DaysPercentOnMax   *float64
	TheoreticalBestLast7DaysPercentOnMax *float64
}

// GeneratePerformanceStatsMessage creates a message string summarizing performance over various periods.
// It takes multiple maps, each representing a category of percentages (e.g., actual, theoretical).
// This function is a more structured version of the Python original that took many individual percentage args.
func GeneratePerformanceStatsMessage(
	message string, // Base message to append to
	tradingPair string,
	actualPerformance PerformanceData, // Using the struct defined above
	// Potentially add other PerformanceData structs for different types of stats if needed
) string {
	var builder strings.Builder
	builder.WriteString(message) // Start with base message

	builder.WriteString(fmt.Sprintf("\n\n--- PERFORMANCE STATS (%s) ---", tradingPair))

	builder.WriteString(fmt.Sprintf("\nActual Performance:"))
	builder.WriteString(fmt.Sprintf("\n  Day: %s%%", formatFloatPtr(actualPerformance.DayPercent, 2)))
	builder.WriteString(fmt.Sprintf("\n  Last 7 Days: %s%%", formatFloatPtr(actualPerformance.Last7DaysPercent, 2)))
	builder.WriteString(fmt.Sprintf("\n  Last 30 Days: %s%%", formatFloatPtr(actualPerformance.Last30DaysPercent, 2)))

	// Example for theoretical, assuming it's part of the same PerformanceData struct for simplicity
	builder.WriteString(fmt.Sprintf("\n\nTheoretical Performance (Illustrative):"))
	builder.WriteString(fmt.Sprintf("\n  Day (Theo): %s%%", formatFloatPtr(actualPerformance.TheoreticalDayPercent, 2)))
	builder.WriteString(fmt.Sprintf("\n  Best Day (Theo): %s%%", formatFloatPtr(actualPerformance.TheoreticalBestDayPercent, 2)))
	builder.WriteString(fmt.Sprintf("\n  Last 7 Days (Theo on Max): %s%%", formatFloatPtr(actualPerformance.TheoreticalLast7DaysPercentOnMax, 2)))
	builder.WriteString(fmt.Sprintf("\n  Best 7 Days (Theo on Max): %s%%", formatFloatPtr(actualPerformance.TheoreticalBestLast7DaysPercentOnMax, 2)))

	return builder.String()
}
