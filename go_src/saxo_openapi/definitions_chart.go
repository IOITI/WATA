package saxo_openapi

// import "time" // Removed unused import

// --- Chart Data ---

// ChartData represents chart data for an instrument.
type ChartData struct {
	ChartInfo *ChartInfo `json:"ChartInfo,omitempty"` // Information about the chart data returned
	Data      [][]interface{} `json:"Data"`          // Array of arrays/tuples: [timestamp, open, high, low, close, volume]
	                                               // Timestamp is typically int64 (milliseconds since epoch)
	                                               // Other values are float64
	DataVersion *int64 `json:"DataVersion,omitempty"` // Version of the data
	NextPage    *string `json:"__next,omitempty"`    // For pagination if data is chunked (not typical for chart data itself)
	Next        *string `json:"NextPage,omitempty"`  // Alternative pagination key
}

// ChartInfo provides metadata about the returned chart data.
type ChartInfo struct {
	DelayInMinutes    *int    `json:"DelayInMinutes,omitempty"`
	ExchangeID        *string `json:"ExchangeId,omitempty"`
	FirstTime         *int64  `json:"FirstTime,omitempty"` // Timestamp in milliseconds
	LastTime          *int64  `json:"LastTime,omitempty"`  // Timestamp in milliseconds
	MarketState       *string `json:"MarketState,omitempty"`
	TimeZone          *string `json:"TimeZone,omitempty"` // IANA Timezone ID
	TimeZoneOffset    *string `json:"TimeZoneOffset,omitempty"` // Offset from UTC, e.g., "-PT8H"
	TradingPeriod     *TradingPeriod `json:"TradingPeriod,omitempty"`
}

// TradingPeriod defines the trading hours for an instrument on a specific day.
type TradingPeriod struct {
	Begin *int64 `json:"Begin,omitempty"` // Timestamp in milliseconds
	End   *int64 `json:"End,omitempty"`   // Timestamp in milliseconds
}

// ChartErrorData is a specific error structure that might be returned by chart endpoints
// if a general OpenAPIError is not sufficient or if there's more detailed info.
// For now, assume chart errors are handled by the standard OpenAPIError.
/*
type ChartErrorData struct {
	ErrorCode    string `json:"ErrorCode"`
	ErrorMessage string `json:"ErrorMessage"`
	// ... other fields
}
*/
