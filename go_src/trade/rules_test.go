package trade

import (
	"errors"
	// "fmt" // Removed unused import
	"pymath/go_src/configuration"
	"pymath/go_src/database"
	"pymath/go_src/trade_exceptions"
	"strings"
	"testing"
	"time"
)

// --- Mock PositionManagerInterface ---
type mockPositionManager struct {
	PositionManagerInterface
	GetPercentOfTheDayFunc   func(userID, tradingPair string) (float64, error)
	GetOpenPositionIDsActionsFunc func() ([]database.OpenPositionAction, error)
}

func (m *mockPositionManager) GetPercentOfTheDay(userID, tradingPair string) (float64, error) {
	if m.GetPercentOfTheDayFunc != nil {
		return m.GetPercentOfTheDayFunc(userID, tradingPair)
	}
	return 0, errors.New("GetPercentOfTheDay not mocked")
}

func (m *mockPositionManager) GetOpenPositionIDsActions() ([]database.OpenPositionAction, error) {
	if m.GetOpenPositionIDsActionsFunc != nil {
		return m.GetOpenPositionIDsActionsFunc()
	}
	return nil, errors.New("GetOpenPositionIDsActions not mocked")
}

// Helper to create config for tests using direct struct initialization
func directTestConfig(timezone string, rules []configuration.TradeRule, globalSettings ...configuration.GlobalSettings) *configuration.Config {
	var gs configuration.GlobalSettings
	if len(globalSettings) > 0 {
		gs = globalSettings[0]
	} else {
		gs = configuration.GlobalSettings{AppName: "TestAppForRules"} // Default if not provided
	}

	return &configuration.Config{
		Trade: configuration.TradeConfig{
			Timezone: timezone,
			Rules:    rules,
		},
		GlobalSettings: gs,
	}
}


func TestNewTradingRules_Success(t *testing.T) {
	mockPosMgr := &mockPositionManager{}

	profitLimit := 2.5
	lossLimit := 1.5
	maxAge := 10.0
	startHour, endHour := 9, 17
	riskyHour, riskyMinute := 16, 30

	cfg := directTestConfig("Europe/Paris", []configuration.TradeRule{
		{RuleType: "allowed_indices", RuleConfig: configuration.TradeRuleConfig{
			IndiceIDs: map[string]interface{}{"CAC40": "FRA40", "DAX40": "GER40"},
		}},
		{RuleType: "market_closed_dates", RuleConfig: configuration.TradeRuleConfig{
			DatesList: []string{"2023-12-25", "2024-01-01"},
		}},
		{RuleType: "signal_validation", RuleConfig: configuration.TradeRuleConfig{
			MaxAgeMinutes: &maxAge,
		}},
		{RuleType: "day_trading", RuleConfig: configuration.TradeRuleConfig{
			ClosePositionTime:                   "21:55",
			DontEnterTradeIfDayProfitIsMoreThan: &profitLimit,
			MaxDayLossPercent:                   &lossLimit,
			TradingStartHour:                    &startHour,
			TradingEndHour:                      &endHour,
			RiskyTradingStartHour:               &riskyHour,
			RiskyTradingStartMinute:             &riskyMinute,
		}},
	})

	tr, err := NewTradingRules(cfg, mockPosMgr)
	if err != nil {
		t.Fatalf("NewTradingRules failed: %v", err)
	}
	if tr.tradingTimezone.String() != "Europe/Paris" {
		t.Errorf("Expected timezone Europe/Paris, got %s", tr.tradingTimezone.String())
	}
	if tr.closePositionTimeStr != "21:55" {
		t.Errorf("Expected closePositionTimeStr '21:55', got '%s'", tr.closePositionTimeStr)
	}
	if len(tr.allowedIndices) != 2 || tr.allowedIndices["CAC40"] != "FRA40" {
		t.Errorf("allowedIndices not parsed correctly: %v", tr.allowedIndices)
	}
	if !tr.marketClosedDates["2023-12-25"] || !tr.marketClosedDates["2024-01-01"] {
		t.Error("marketClosedDates not parsed correctly")
	}
	if tr.signalValidationMaxAge != time.Duration(maxAge)*time.Minute {
		t.Errorf("signalValidationMaxAge incorrect: expected %v got %v", time.Duration(maxAge)*time.Minute, tr.signalValidationMaxAge)
	}
	if tr.dontEnterTradeIfDayProfitIsMoreThan == nil || *tr.dontEnterTradeIfDayProfitIsMoreThan != profitLimit {
		t.Errorf("dontEnterTradeIfDayProfitIsMoreThan incorrect: %v", tr.dontEnterTradeIfDayProfitIsMoreThan)
	}
    if tr.maxDayLossPercent == nil || *tr.maxDayLossPercent != lossLimit {
        t.Errorf("maxDayLossPercent incorrect: %v", tr.maxDayLossPercent)
    }
    if tr.tradingStartHour != startHour || tr.tradingEndHour != endHour || tr.riskyTradingStartHour != riskyHour || tr.riskyTradingStartMinute != riskyMinute {
        t.Error("Day trading hours not parsed correctly")
    }
}

func TestCheckSignalTimestamp(t *testing.T) {
	mockPosMgr := &mockPositionManager{}
	maxAge := 10.0
	cfg := directTestConfig("UTC", []configuration.TradeRule{
		{RuleType: "signal_validation", RuleConfig: configuration.TradeRuleConfig{MaxAgeMinutes: &maxAge}},
	})

	tr, err := NewTradingRules(cfg, mockPosMgr)
	if err != nil {t.Fatalf("NewTradingRules failed: %v", err)}


	now := time.Now().UTC()
	validSignalTime := now.Add(-5 * time.Minute).Format(time.RFC3339Nano)
	oldSignalTime := now.Add(-15 * time.Minute).Format(time.RFC3339Nano)

	if err := tr.CheckSignalTimestamp("long", validSignalTime); err != nil {
		t.Errorf("Expected valid signal to pass, got: %v", err)
	}

	errOld := tr.CheckSignalTimestamp("short", oldSignalTime)
	if errOld == nil {
		t.Error("Expected old signal to fail, but it passed")
	} else if _, ok := errOld.(*trade_exceptions.TradingRuleViolation); !ok {
		t.Errorf("Expected TradingRuleViolation for old signal, got %T", errOld)
	}

	if err := tr.CheckSignalTimestamp("check_positions_on_saxo_api", oldSignalTime); err != nil {
		t.Errorf("Expected 'check_positions_on_saxo_api' action to bypass timestamp check, got %v", err)
	}

	tr.signalValidationMaxAge = 0
	if err := tr.CheckSignalTimestamp("long", oldSignalTime); err != nil {
		t.Errorf("Expected old signal to pass when validation is disabled, got %v", err)
	}
}

func TestCheckIfOpenPositionIsSameSignal(t *testing.T) {
	mockPosMgr := &mockPositionManager{}

	t.Run("NoOpenPositions", func(t *testing.T) {
		mockPosMgr.GetOpenPositionIDsActionsFunc = func() ([]database.OpenPositionAction, error) {
			return []database.OpenPositionAction{}, nil
		}
		err := CheckIfOpenPositionIsSameSignal("long", mockPosMgr)
		if err != nil {
			t.Errorf("Expected no error with no open positions, got %v", err)
		}
	})

	t.Run("OpenPositionDifferentDirection", func(t *testing.T) {
		mockPosMgr.GetOpenPositionIDsActionsFunc = func() ([]database.OpenPositionAction, error) {
			return []database.OpenPositionAction{{ID: "pos1", ActionType: "sell"}}, nil
		}
		err := CheckIfOpenPositionIsSameSignal("long", mockPosMgr)
		if err != nil {
			t.Errorf("Expected no error with open position in different direction, got %v", err)
		}
	})

	t.Run("OpenPositionSameDirection", func(t *testing.T) {
		mockPosMgr.GetOpenPositionIDsActionsFunc = func() ([]database.OpenPositionAction, error) {
			return []database.OpenPositionAction{{ID: "pos1", ActionType: "buy"}}, nil
		}
		err := CheckIfOpenPositionIsSameSignal("long", mockPosMgr)
		if err == nil {
			t.Error("Expected TradingRuleViolation for open position in same direction, got nil")
		} else if _, ok := err.(*trade_exceptions.TradingRuleViolation); !ok {
			t.Errorf("Expected TradingRuleViolation, got %T: %v", err, err)
		} else if !strings.Contains(err.Error(), "same signal direction ('buy') already exists") {
			t.Errorf("Unexpected error message: %v", err)
		}
	})
}
