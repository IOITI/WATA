package message_helper

import (
	"strings"
	"testing"
)

func floatPtr(f float64) *float64 { return &f }

func TestAppendPerformanceMessage(t *testing.T) {
	baseMsg := "Base message."
	title := "Performance Update"
	percentages := map[string]*float64{
		"Daily":         floatPtr(1.23),
		"Weekly":        floatPtr(-0.5),
		"Monthly":       nil, // N/A
		"Annual Growth": floatPtr(10.755),
	}

	result := AppendPerformanceMessage(baseMsg, title, percentages)

	if !strings.HasPrefix(result, baseMsg) {
		t.Errorf("Result should start with base message. Got: %s", result)
	}
	if !strings.Contains(result, "--- PERFORMANCE UPDATE ---") {
		t.Error("Title not found or incorrectly formatted.")
	}
	if !strings.Contains(result, "\nDaily: 1.23%") {
		t.Error("Daily percentage incorrect or missing.")
	}
	if !strings.Contains(result, "\nWeekly: -0.50%") { // Note: .2f precision
		t.Error("Weekly percentage incorrect or missing.")
	}
	if !strings.Contains(result, "\nMonthly: N/A%") {
		t.Error("Monthly (nil) percentage incorrect or missing.")
	}
	if !strings.Contains(result, "\nAnnual Growth: 10.76%") { // Check rounding
		t.Error("Annual Growth percentage incorrect, missing, or rounding issue.")
	}
	// Check order (due to sort)
	if !(strings.Index(result, "Annual Growth") < strings.Index(result, "Daily") &&
		strings.Index(result, "Daily") < strings.Index(result, "Monthly") &&
		strings.Index(result, "Monthly") < strings.Index(result, "Weekly")) {
		// Note: This order check is based on string sort of keys: "Annual Growth", "Daily", "Monthly", "Weekly"
		// If keys change, this check might need adjustment.
		// A more robust check would parse the lines.
		// For "Weekly", it comes after "Monthly" due to string sort.
		// Corrected order based on string sort: Annual Growth, Daily, Monthly, Weekly
		// Actually, "Monthly" comes before "Weekly".
		// Expected order (from sort.Strings on keys): "Annual Growth", "Daily", "Monthly", "Weekly"
		// The error was in my manual trace, let's re-verify:
		// A, D, M, W - this is correct.
		// The previous error message was "Weekly" < "Monthly", which is false.
		// Let's check the actual string output order.
		// The output format is "\nKey: Value%".
		// So we look for indexOf "\nAnnual Growth", "\nDaily", "\nMonthly", "\nWeekly"
		idxAG := strings.Index(result, "\nAnnual Growth:")
		idxD := strings.Index(result, "\nDaily:")
		idxM := strings.Index(result, "\nMonthly:")
		idxW := strings.Index(result, "\nWeekly:")

		if !(idxAG < idxD && idxD < idxM && idxM < idxW) {
			t.Errorf("Output not sorted by keys as expected. Indices: AG=%d, D=%d, M=%d, W=%d\nFull Message:\n%s", idxAG, idxD, idxM, idxW, result)
		}
	}

	// Test with empty title
	resultNoTitle := AppendPerformanceMessage(baseMsg, "", percentages)
	if strings.Contains(resultNoTitle, "---  ---") { // Should not have title separator if title is empty
		t.Error("Empty title should not produce a title line.")
	}
	if !strings.Contains(resultNoTitle, "\nDaily: 1.23%") { // Still expect values
		t.Error("Daily percentage incorrect or missing when title is empty.")
	}


	// Test with empty percentages map
	resultNoPercentages := AppendPerformanceMessage(baseMsg, title, map[string]*float64{})
	expectedNoPercentages := baseMsg + "\n\n--- PERFORMANCE UPDATE ---" // Title is still added
	if strings.TrimSpace(resultNoPercentages) != strings.TrimSpace(expectedNoPercentages) { // Trim to avoid issues with trailing newlines if any
		t.Errorf("Expected only base message and title for empty percentages. Got:\n'%s'\nExpected:\n'%s'", resultNoPercentages, expectedNoPercentages)
	}
}

func TestFormatGeneralStats(t *testing.T) {
	t.Run("WithData", func(t *testing.T) {
		stats := []map[string]interface{}{
			{"pair": "BTC/USD", "total_trades": 100.0, "win_rate": floatPtr(60.5), "total_pnl_percent": floatPtr(15.25)},
			{"pair": "ETH/USD", "total_trades": 50.0, "win_rate": nil, "total_pnl_percent": floatPtr(-2.1)},
		}
		result := FormatGeneralStats(stats)
		if !strings.Contains(result, "--- GENERAL STATS ---") {
			t.Error("Missing title")
		}
		if !strings.Contains(result, "Pair: BTC/USD") || !strings.Contains(result, "Total Trades: 100") ||
			!strings.Contains(result, "Win Rate: 60.50%") || !strings.Contains(result, "Total P&L: 15.25%") {
			t.Error("BTC/USD stats formatted incorrectly or missing.")
		}
		if !strings.Contains(result, "Pair: ETH/USD") || !strings.Contains(result, "Total Trades: 50") ||
			!strings.Contains(result, "Win Rate: N/A%") || !strings.Contains(result, "Total P&L: -2.10%") {
			t.Error("ETH/USD stats formatted incorrectly or missing.")
		}
	})
	t.Run("NoData", func(t *testing.T) {
		result := FormatGeneralStats([]map[string]interface{}{})
		if result != "No general statistics available." {
			t.Errorf("Expected 'No general statistics available.' for empty input, got: %s", result)
		}
	})
}

func TestFormatDetailStats(t *testing.T) {
	t.Run("WithData", func(t *testing.T) {
		stats := []map[string]interface{}{
			{
				"metric_a": "value_a", "metric_b": 123.0, "metric_c": floatPtr(78.912),
				"an_int": 42, "a_bool": true,
			},
			{"another_metric": "another_value", "yet_another": nil}, // nil *float64 handled by formatFloatPtr
		}
		result := FormatDetailStats(stats)

		if !strings.Contains(result, "--- DETAILED STATS ---") {t.Error("Missing title")}
		if !strings.Contains(result, "Detail Set 1:") {t.Error("Missing 'Detail Set 1' header")}
		// Check for sorted keys from first map (a_bool, an_int, metric_a, metric_b, metric_c)
		if !strings.Contains(result, "\n  a_bool: true") {t.Error("Missing/wrong a_bool")}
		if !strings.Contains(result, "\n  an_int: 42") {t.Error("Missing/wrong an_int")}
		if !strings.Contains(result, "\n  metric_a: value_a") {t.Error("Missing/wrong metric_a")}
		if !strings.Contains(result, "\n  metric_b: 123.00") {t.Error("Missing/wrong metric_b")}
		if !strings.Contains(result, "\n  metric_c: 78.91") {t.Error("Missing/wrong metric_c")} // Check rounding

		if !strings.Contains(result, "Detail Set 2:") {t.Error("Missing 'Detail Set 2' header")}
		if !strings.Contains(result, "\n  another_metric: another_value") {t.Error("Missing/wrong another_metric")}
		if !strings.Contains(result, "\n  yet_another: <nil>") {t.Error("Missing/wrong yet_another (nil value)")}
	})
	t.Run("NoData", func(t *testing.T) {
		result := FormatDetailStats([]map[string]interface{}{})
		if result != "No detailed statistics available." {
			t.Errorf("Expected 'No detailed statistics available.' for empty input, got: %s", result)
		}
	})
}

func TestGenerateDailyStatsMessage(t *testing.T) {
	t.Run("WithData", func(t *testing.T) {
		stats := map[string][]map[string]interface{}{
			"2023-10-26": {
				{"pair": "BTC/USD", "total_trades": 10.0, "winning_trades": 7.0, "losing_trades": 3.0},
				{"pair": "ETH/USD", "total_trades": 5.0, "winning_trades": 2.0, "losing_trades": 3.0},
			},
			"2023-10-25": {
				{"pair": "BTC/USD", "total_trades": 8.0, "winning_trades": 6.0, "losing_trades": 2.0},
			},
			"2023-10-24": {}, // No trades for this day for any pair
		}
		result := GenerateDailyStatsMessage(stats)

		if !strings.Contains(result, "--- DAILY STATS ---") {t.Error("Missing title")}
		// Check sorted dates (2023-10-24, 2023-10-25, 2023-10-26)
		idx24 := strings.Index(result, "Date: 2023-10-24")
		idx25 := strings.Index(result, "Date: 2023-10-25")
		idx26 := strings.Index(result, "Date: 2023-10-26")
		if !(idx24 < idx25 && idx25 < idx26) {
			t.Errorf("Dates not sorted correctly. Indices: 24=%d, 25=%d, 26=%d\nMessage:\n%s", idx24, idx25, idx26, result)
		}

		if !strings.Contains(result, "Date: 2023-10-26") ||
			!strings.Contains(result, "Pair: BTC/USD") || !strings.Contains(result, "Total Trades: 10") ||
			!strings.Contains(result, "Winning: 7") || !strings.Contains(result, "Losing: 3") ||
			!strings.Contains(result, "Pair: ETH/USD") || !strings.Contains(result, "Total Trades: 5") {
			t.Error("Stats for 2023-10-26 formatted incorrectly or missing.")
		}
		if !strings.Contains(result, "Date: 2023-10-24\n  No trades for this day.") {
			t.Error("Message for 'No trades for this day' incorrect or missing for 2023-10-24.")
		}
	})
	t.Run("NoData", func(t *testing.T) {
		result := GenerateDailyStatsMessage(map[string][]map[string]interface{}{})
		if result != "No daily statistics available." {
			t.Errorf("Expected 'No daily statistics available.' for empty input, got: %s", result)
		}
	})
}


func TestGeneratePerformanceStatsMessage(t *testing.T) {
	base := "Initial performance report."
	pair := "BTC/USD"
	perfData := PerformanceData{
		DayPercent:                      floatPtr(1.5),
		Last7DaysPercent:                floatPtr(-2.33),
		Last30DaysPercent:               nil,
		TheoreticalDayPercent:           floatPtr(2.0),
		TheoreticalBestDayPercent:       floatPtr(5.0),
		TheoreticalLast7DaysPercentOnMax:floatPtr(10.123),
		TheoreticalBestLast7DaysPercentOnMax: floatPtr(15.0),
	}

	result := GeneratePerformanceStatsMessage(base, pair, perfData)

	if !strings.HasPrefix(result, base) {t.Error("Base message missing")}
	if !strings.Contains(result, "--- PERFORMANCE STATS (BTC/USD) ---") {t.Error("Title/Pair missing")}

	if !strings.Contains(result, "\nActual Performance:") {t.Error("Actual perf section missing")}
	if !strings.Contains(result, "\n  Day: 1.50%") {t.Error("Day % missing")}
	if !strings.Contains(result, "\n  Last 7 Days: -2.33%") {t.Error("7 Day % missing")}
	if !strings.Contains(result, "\n  Last 30 Days: N/A%") {t.Error("30 Day % (nil) missing")}

	if !strings.Contains(result, "\n\nTheoretical Performance (Illustrative):") {t.Error("Theoretical perf section missing")}
	if !strings.Contains(result, "\n  Day (Theo): 2.00%") {t.Error("Theo Day % missing")}
	if !strings.Contains(result, "\n  Best Day (Theo): 5.00%") {t.Error("Theo Best Day % missing")}
	if !strings.Contains(result, "\n  Last 7 Days (Theo on Max): 10.12%") {t.Error("Theo 7 Day % missing")} // check rounding
	if !strings.Contains(result, "\n  Best 7 Days (Theo on Max): 15.00%") {t.Error("Theo Best 7 Day % missing")}
}
