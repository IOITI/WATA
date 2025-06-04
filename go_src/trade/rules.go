package trade

import (
	"encoding/json" // Added import
	"fmt"
	"pymath/go_src/configuration"
	"pymath/go_src/database"
	"pymath/go_src/trade_exceptions"
	"strings"
	"time"

	"github.com/sirupsen/logrus"
)

// Define an interface for the methods TradingRules uses from PositionManager.
type PositionManagerInterface interface {
	GetPercentOfTheDay(userID, tradingPair string) (float64, error)
	GetOpenPositionIDsActions() ([]database.OpenPositionAction, error) // Uses database.OpenPositionAction
}


// TradingRules holds all parsed trading rule configurations.
type TradingRules struct {
	config        *configuration.Config
	posMgr        PositionManagerInterface
	tradingTimezone *time.Location

	allowedIndices          map[string]interface{}
	marketClosedDates       map[string]bool
	signalValidationMaxAge  time.Duration

	dayTradingRuleEnabled           bool
	dontEnterTradeIfDayProfitIsMoreThan *float64
	maxDayLossPercent                 *float64
	tradingStartHour                int
	tradingEndHour                  int
	riskyTradingStartHour           int
	riskyTradingStartMinute         int
	closePositionTimeStr            string
}

// NewTradingRules parses rule configurations and returns a TradingRules instance.
func NewTradingRules(cfg *configuration.Config, posMgr PositionManagerInterface) (*TradingRules, error) {
	if cfg == nil {
		return nil, fmt.Errorf("configuration.Config cannot be nil")
	}
	if posMgr == nil {
		return nil, fmt.Errorf("positionManager cannot be nil")
	}

	tr := &TradingRules{
		config:       cfg,
		posMgr:       posMgr,
		allowedIndices: make(map[string]interface{}),
		marketClosedDates: make(map[string]bool),
		tradingStartHour: 9, tradingEndHour: 21,
		riskyTradingStartHour: 21, riskyTradingStartMinute: 30,
	}

	var tzStr string
	if cfg.Trade.Timezone == "" {
		logrus.Warnf("'trading.timezone' not found or empty in config, using UTC as default.")
		tzStr = "UTC"
	} else {
		tzStr = cfg.Trade.Timezone
	}
	loc, err := time.LoadLocation(tzStr)
	if err != nil {
		return nil, fmt.Errorf("failed to load trading timezone '%s': %w", tzStr, err)
	}
	tr.tradingTimezone = loc

	if cfg.Trade.Rules == nil {
		logrus.Info("No 'trade.rules' found or rules list is empty in typed config.")
	}

	for _, rule := range cfg.Trade.Rules {
		ruleType := rule.RuleType
		ruleConfig := rule.RuleConfig

		switch ruleType {
		case "allowed_indices":
			if ruleConfig.IndiceIDs != nil {
				tr.allowedIndices = ruleConfig.IndiceIDs
			} else {
				logrus.Warnf("allowed_indices rule_config 'indice_ids' is nil or missing.")
			}
		case "market_closed_dates":
			if ruleConfig.DatesList != nil {
				for _, dateStr := range ruleConfig.DatesList {
					tr.marketClosedDates[dateStr] = true
				}
			} else {
				logrus.Warnf("market_closed_dates rule_config 'dates_list' is nil or missing.")
			}
		case "signal_validation":
			if ruleConfig.MaxAgeMinutes != nil {
				if *ruleConfig.MaxAgeMinutes > 0 {
					tr.signalValidationMaxAge = time.Duration(*ruleConfig.MaxAgeMinutes) * time.Minute
				} else {
					logrus.Warnf("signal_validation rule_config 'max_age_minutes' is not positive: %f", *ruleConfig.MaxAgeMinutes)
				}
			} else {
				logrus.Warnf("signal_validation rule_config 'max_age_minutes' is nil or missing.")
			}
		case "day_trading":
			tr.dayTradingRuleEnabled = true
			if ruleConfig.DontEnterTradeIfDayProfitIsMoreThan != nil {
				tr.dontEnterTradeIfDayProfitIsMoreThan = ruleConfig.DontEnterTradeIfDayProfitIsMoreThan
			}
			if ruleConfig.MaxDayLossPercent != nil {
				tr.maxDayLossPercent = ruleConfig.MaxDayLossPercent
			}
			if ruleConfig.TradingStartHour != nil { tr.tradingStartHour = *ruleConfig.TradingStartHour }
			if ruleConfig.TradingEndHour != nil { tr.tradingEndHour = *ruleConfig.TradingEndHour }
			if ruleConfig.RiskyTradingStartHour != nil { tr.riskyTradingStartHour = *ruleConfig.RiskyTradingStartHour }
			if ruleConfig.RiskyTradingStartMinute != nil { tr.riskyTradingStartMinute = *ruleConfig.RiskyTradingStartMinute }

			if ruleConfig.ClosePositionTime != "" {
				tr.closePositionTimeStr = ruleConfig.ClosePositionTime
			} else {
				logrus.Warnf("day_trading rule is missing 'close_position_time'.")
			}
		default:
			logrus.Warnf("Unknown rule_type '%s' encountered in typed config.", ruleType)
		}
	}
	if !tr.dayTradingRuleEnabled && tr.closePositionTimeStr == "" {
		logrus.Info("Day trading rule not found or not fully configured.")
	}
	return tr, nil
}

func (tr *TradingRules) GetRuleConfig(ruleType string) (map[string]interface{}, error) {
	// This method might need adjustment if cfg.Trade.Rules is the source of truth
	// and we want to return the raw map[string]interface{} for a specific rule.
	// For now, it's less critical as specific configs are parsed into TradingRules fields.
	for _, rule := range tr.config.Trade.Rules {
		if rule.RuleType == ruleType {
			// Need to convert rule.RuleConfig (struct) back to map[string]interface{} if that's the contract
			// This is complex. Simpler to assume this func is less used now.
			b, _ := json.Marshal(rule.RuleConfig) // Example of converting back
			var m map[string]interface{}
			json.Unmarshal(b, &m)
			return m, nil
		}
	}
	return nil, fmt.Errorf("rule_type '%s' not found", ruleType)
}


func (tr *TradingRules) CheckSignalTimestamp(signalAction string, signalTimestampStr string) error {
	if signalAction == "check_positions_on_saxo_api" {
		return nil
	}
	if tr.signalValidationMaxAge <= 0 {
		logrus.Warn("Signal timestamp validation skipped: max_age_minutes not configured or is zero.")
		return nil
	}

	signalTime, err := time.Parse(time.RFC3339Nano, signalTimestampStr)
	if err != nil {
		signalTime, err = time.Parse(time.RFC3339, signalTimestampStr)
		if err != nil {
			return fmt.Errorf("invalid signal_timestamp format '%s': %w", signalTimestampStr, err)
		}
	}

	nowInTradingTimezone := time.Now().In(tr.tradingTimezone)
	if nowInTradingTimezone.Sub(signalTime.In(tr.tradingTimezone)) > tr.signalValidationMaxAge {
		return trade_exceptions.NewTradingRuleViolation(
			fmt.Sprintf("Signal is too old: received at %s, current time %s, max age %v",
				signalTime.Format(time.RFC3339), nowInTradingTimezone.Format(time.RFC3339), tr.signalValidationMaxAge),
			"SIGNAL_AGE_VALIDATION",
		)
	}
	return nil
}

func (tr *TradingRules) GetAllowedIndiceID(indiceName string) (interface{}, error) {
	if len(tr.allowedIndices) == 0 {
		logrus.Warn("No allowed_indices configured. Allowing all by default.")
		return indiceName, nil
	}
	indiceID, ok := tr.allowedIndices[indiceName]
	if !ok {
		return nil, trade_exceptions.NewTradingRuleViolation(
			fmt.Sprintf("Indice '%s' is not allowed by 'allowed_indices' rule.", indiceName),
			"ALLOWED_INDICES_VALIDATION",
		)
	}
	return indiceID, nil
}

func (tr *TradingRules) CheckMarketHours(signalTimestampStr string) error {
	signalTime, err := time.Parse(time.RFC3339Nano, signalTimestampStr)
	if err != nil {
		signalTime, err = time.Parse(time.RFC3339, signalTimestampStr)
		if err != nil {
			return fmt.Errorf("invalid signal_timestamp format '%s' for market hours check: %w", signalTimestampStr, err)
		}
	}
	signalTimeInZone := signalTime.In(tr.tradingTimezone)

	dateStr := signalTimeInZone.Format("2006-01-02")
	if tr.marketClosedDates[dateStr] {
		return trade_exceptions.NewNoMarketAvailableException(
			fmt.Sprintf("Market is closed due to holiday/configured closed date: %s", dateStr),
			"",
		)
	}

	weekday := signalTimeInZone.Weekday()
	if weekday == time.Saturday || weekday == time.Sunday {
		return trade_exceptions.NewNoMarketAvailableException(
			fmt.Sprintf("Market is closed on weekends (Signal time: %s is a %s)", dateStr, weekday.String()),
			"",
		)
	}

	currentTimeInMinutes := signalTimeInZone.Hour()*60 + signalTimeInZone.Minute()
	tradingStartInMinutes := tr.tradingStartHour * 60
	tradingEndInMinutes := tr.tradingEndHour * 60

	if currentTimeInMinutes < tradingStartInMinutes || currentTimeInMinutes >= tradingEndInMinutes {
		return trade_exceptions.NewNoMarketAvailableException(
			fmt.Sprintf("Market is closed. Signal time %s is outside trading hours (%02d:00 - %02d:00 %s).",
				signalTimeInZone.Format("15:04"), tr.tradingStartHour, tr.tradingEndHour, tr.tradingTimezone.String()),
			"",
		)
	}

	riskyPeriodStartInMinutes := tr.riskyTradingStartHour*60 + tr.riskyTradingStartMinute
	if tr.dayTradingRuleEnabled && currentTimeInMinutes >= riskyPeriodStartInMinutes && currentTimeInMinutes < tradingEndInMinutes {
		logrus.Warnf("Signal time %s is within the risky trading period (from %02d:%02d %s).",
			signalTimeInZone.Format("15:04"), tr.riskyTradingStartHour, tr.riskyTradingStartMinute, tr.tradingTimezone.String())
	}

	return nil
}

func (tr *TradingRules) CheckProfitPerDay() error {
	if !tr.dayTradingRuleEnabled {
		return nil
	}
	if tr.dontEnterTradeIfDayProfitIsMoreThan == nil && tr.maxDayLossPercent == nil {
		return nil
	}

	userID := tr.config.GlobalSettings.AppName
	tradingPair := "PRIMARY_PAIR_CONTEXT"

	todayPnlPercent, err := tr.posMgr.GetPercentOfTheDay(userID, tradingPair)
	if err != nil {
		logrus.Errorf("CheckProfitPerDay: Failed to get PNL: %v. Rule check will be skipped.", err)
		return nil
	}

	if tr.dontEnterTradeIfDayProfitIsMoreThan != nil && todayPnlPercent >= *tr.dontEnterTradeIfDayProfitIsMoreThan {
		return trade_exceptions.NewTradingRuleViolation(
			fmt.Sprintf("Daily profit limit reached (%.2f%% >= %.2f%%). No new trades allowed.",
				todayPnlPercent, *tr.dontEnterTradeIfDayProfitIsMoreThan),
			"DAILY_PROFIT_LIMIT",
		)
	}
	if tr.maxDayLossPercent != nil && todayPnlPercent <= -(*tr.maxDayLossPercent) {
		return trade_exceptions.NewTradingRuleViolation(
			fmt.Sprintf("Daily loss limit reached (%.2f%% <= -%.2f%%). No new trades allowed.",
				todayPnlPercent, *tr.maxDayLossPercent),
			"DAILY_LOSS_LIMIT",
		)
	}
	return nil
}

func CheckIfOpenPositionIsSameSignal(action string, posMgr PositionManagerInterface) error {
	if posMgr == nil {
		return fmt.Errorf("PositionManager is nil, cannot check open positions")
	}
	openPositions, err := posMgr.GetOpenPositionIDsActions()
	if err != nil {
		return fmt.Errorf("failed to get open positions: %w", err)
	}

	if len(openPositions) == 0 {
		return nil
	}

	var signalDirection string
	if action == "long" {
		signalDirection = "buy"
	} else if action == "short" {
		signalDirection = "sell"
	} else {
		return fmt.Errorf("unknown signal action '%s' for open position check", action)
	}

	for _, pos := range openPositions {
		if strings.ToLower(pos.ActionType) == signalDirection {
			return trade_exceptions.NewTradingRuleViolation(
				fmt.Sprintf("An open position with the same signal direction ('%s') already exists (ID: %s).",
					signalDirection, pos.ID),
				"EXISTING_POSITION_SAME_DIRECTION",
			)
		}
	}
	return nil
}
