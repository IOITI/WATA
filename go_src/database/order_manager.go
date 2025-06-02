package database

import (
	"database/sql"
	"fmt"
	"strings"
	"time"
	// "github.com/lib/pq" // For handling array types with SQL drivers, might be useful for DuckDB depending on driver specifics
)

// OrderManager handles operations for the turbo_data_order table.
type OrderManager struct {
	tdb *TradingDB
}

// NewOrderManager creates a new OrderManager.
func NewOrderManager(tdb *TradingDB) *OrderManager {
	return &OrderManager{tdb: tdb}
}

// CreateSchemaTurboDataOrder creates the turbo_data_order table.
func (om *OrderManager) CreateSchemaTurboDataOrder() error {
	schema := `
	CREATE TABLE IF NOT EXISTS turbo_data_order (
		action_type VARCHAR,
		created_at TIMESTAMP,
		effective_time TIMESTAMP,
		expiration_time TIMESTAMP,
		id VARCHAR PRIMARY KEY,
		market VARCHAR,
		order_type VARCHAR,
		percent_change DOUBLE,
		price DOUBLE,
		quantity DOUBLE,
		related_order_id VARCHAR[], -- DuckDB supports ARRAY type, VARCHAR[] is a common way to represent string arrays
		status VARCHAR,
		stop_price DOUBLE,
		take_profit_price DOUBLE,
		time_in_force VARCHAR,
		trading_pair VARCHAR,
		trigger_price DOUBLE,
		updated_at TIMESTAMP,
		user_id VARCHAR,
		version INTEGER,
		position_id VARCHAR,
		old_position_id VARCHAR,
		new_position_id VARCHAR,
		trade_performance_id VARCHAR
	);`
	_, err := om.tdb.DB().Exec(schema)
	if err != nil {
		return fmt.Errorf("failed to create turbo_data_order schema: %w", err)
	}
	return nil
}

// TurboOrderData represents the data for an order.
type TurboOrderData struct {
	ActionType         sql.NullString  `json:"action_type"`
	CreatedAt          time.Time       `json:"created_at"`
	EffectiveTime      sql.NullTime    `json:"effective_time"`
	ExpirationTime     sql.NullTime    `json:"expiration_time"`
	ID                 string          `json:"id"`
	Market             sql.NullString  `json:"market"`
	OrderType          sql.NullString  `json:"order_type"`
	PercentChange      sql.NullFloat64 `json:"percent_change"`
	Price              sql.NullFloat64 `json:"price"`
	Quantity           sql.NullFloat64 `json:"quantity"`
	RelatedOrderID     []string        `json:"related_order_id"` // Go slice for array type
	Status             sql.NullString  `json:"status"`
	StopPrice          sql.NullFloat64 `json:"stop_price"`
	TakeProfitPrice    sql.NullFloat64 `json:"take_profit_price"`
	TimeInForce        sql.NullString  `json:"time_in_force"`
	TradingPair        sql.NullString  `json:"trading_pair"`
	TriggerPrice       sql.NullFloat64 `json:"trigger_price"`
	UpdatedAt          sql.NullTime    `json:"updated_at"`
	UserID             sql.NullString  `json:"user_id"`
	Version            sql.NullInt64   `json:"version"`
	PositionID         sql.NullString  `json:"position_id"`
	OldPositionID      sql.NullString  `json:"old_position_id"`
	NewPositionID      sql.NullString  `json:"new_position_id"`
	TradePerformanceID sql.NullString  `json:"trade_performance_id"`
}

// InsertTurboOrderData inserts a new order into the turbo_data_order table.
func (om *OrderManager) InsertTurboOrderData(order *TurboOrderData) error {
	if order == nil {
		return fmt.Errorf("order data cannot be nil")
	}
	if order.ID == "" {
		return fmt.Errorf("order ID is required")
	}
	if order.CreatedAt.IsZero() { // created_at is often non-nullable
		order.CreatedAt = time.Now().UTC()
	}

	// For DuckDB LIST type (VARCHAR[]), format as a string literal "['item1', 'item2']".
	// The driver currently doesn't seem to support direct []string substitution for LIST parameters well.
	var relatedOrderIDArg interface{} // Use interface{} to handle NULL or string
	if order.RelatedOrderID == nil {
		relatedOrderIDArg = nil // Pass SQL NULL if the slice is nil
	} else {
		if len(order.RelatedOrderID) == 0 {
			relatedOrderIDArg = "[]" // DuckDB empty list literal
		} else {
			var parts []string
			for _, id := range order.RelatedOrderID {
				// Escape single quotes within the ID string by doubling them (SQL standard)
				escapedID := strings.ReplaceAll(id, "'", "''")
				parts = append(parts, fmt.Sprintf("'%s'", escapedID))
			}
			relatedOrderIDArg = fmt.Sprintf("[%s]", strings.Join(parts, ", "))
		}
	}

	query := `
	INSERT INTO turbo_data_order (
		action_type, created_at, effective_time, expiration_time, id, market, order_type,
		percent_change, price, quantity, related_order_id, status, stop_price, take_profit_price,
		time_in_force, trading_pair, trigger_price, updated_at, user_id, version, position_id,
		old_position_id, new_position_id, trade_performance_id
	) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);`

	_, err := om.tdb.DB().Exec(query,
		order.ActionType,
		order.CreatedAt,
		order.EffectiveTime,
		order.ExpirationTime,
		order.ID,
		order.Market,
		order.OrderType,
		order.PercentChange,
		order.Price,
		order.Quantity,
		relatedOrderIDArg, // Use the formatted string or NULL
		order.Status,
		order.StopPrice,
		order.TakeProfitPrice,
		order.TimeInForce,
		order.TradingPair,
		order.TriggerPrice,
		order.UpdatedAt,
		order.UserID,
		order.Version,
		order.PositionID,
		order.OldPositionID,
		order.NewPositionID,
		order.TradePerformanceID,
	)

	if err != nil {
		return fmt.Errorf("failed to insert turbo order data for ID %s: %w", order.ID, err)
	}
	return nil
}
