package saxo_openapi

import (
	"context"
	"fmt"
	"net/url"
	"reflect"
	"strconv"
	"strings" // Added import
	"time"    // For Horizon and Time fields in params
)

// --- Chart ---

// GetChartDataParams defines query parameters for fetching chart data.
type GetChartDataParams struct {
	AssetType         string     `url:"AssetType"`           // Required. E.g., "Stock", "FxSpot"
	Uic               int        `url:"Uic"`                 // Required. Instrument ID.
	Horizon           int        `url:"Horizon"`             // Required. Number of bars. Max 1000.
	Mode              string     `url:"Mode"`                // Required. "UpTo", "FromTo"
	Time              *time.Time `url:"Time,omitempty"`      // Required if Mode is "UpTo". End time for "UpTo" mode. Format "YYYY-MM-DDTHH:MM:SSZ" or "YYYY-MM-DD" or ticks.
	StartTime         *time.Time `url:"StartTime,omitempty"` // Required if Mode is "FromTo". Start time.
	EndTime           *time.Time `url:"EndTime,omitempty"`   // Required if Mode is "FromTo". End time.
	Count             *int       `url:"Count,omitempty"`     // Optional. Number of bars to return (alternative to Horizon for some modes)
	FieldGroups       *string    `url:"FieldGroups,omitempty"` // E.g. "ChartInfo", "Data", "DisplayAndFormat"
	SampleRate        *string    `url:"SampleRate,omitempty"`  // E.g. "Minute", "Hour", "Day", "Week", "Month" (Not in Python client, but common)
	PriceTypeToWatch  *string    `url:"PriceTypeToWatch,omitempty"` // E.g. "Bid", "Ask", "LastTraded" (Not in Python client)
	TimeZoneId        *string    `url:"TimeZoneId,omitempty"` // IANA Timezone ID for timestamps (Not in Python client)
}

// GetChartData retrieves chart data (ohlc) for an instrument.
// GET /openapi/chart/v1/charts
func (c *Client) GetChartData(ctx context.Context, params *GetChartDataParams) (*ChartData, error) {
	if params == nil {
		return nil, fmt.Errorf("params cannot be nil for GetChartData")
	}
	// Basic validation from Python client
	if params.Uic == 0 || params.AssetType == "" || params.Horizon == 0 || params.Mode == "" {
		return nil, fmt.Errorf("Uic, AssetType, Horizon, and Mode are required parameters")
	}
	if params.Mode == "UpTo" && params.Time == nil {
		return nil, fmt.Errorf("Time parameter is required when Mode is 'UpTo'")
	}
	if params.Mode == "FromTo" && (params.StartTime == nil || params.EndTime == nil) {
		return nil, fmt.Errorf("StartTime and EndTime parameters are required when Mode is 'FromTo'")
	}


	queryParams, err := paramsToQueryValuesTime(params) // Use custom time formatting
	if err != nil {
		return nil, fmt.Errorf("failed to convert params to query values: %w", err)
	}

	responseBodyType := reflect.TypeOf(ChartData{})
	result, _, err := c.doRequest(ctx, "GET", "chart/v1/charts", queryParams, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*ChartData); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert ChartData. Got: %T", result)
}

// paramsToQueryValuesTime is a modified version of paramsToQueryValues
// that formats time.Time pointers specifically as YYYY-MM-DDTHH:MM:SSZ (RFC3339).
// This is often required by APIs for time parameters.
func paramsToQueryValuesTime(paramsStruct interface{}) (url.Values, error) {
	values := url.Values{}
	if paramsStruct == nil {
		return values, nil
	}

	v := reflect.ValueOf(paramsStruct)
	if v.Kind() == reflect.Ptr {
		v = v.Elem()
	}
	if v.Kind() != reflect.Struct {
		return nil, fmt.Errorf("paramsToQueryValuesTime: expected a struct or pointer to struct, got %T", paramsStruct)
	}

	typ := v.Type()
	for i := 0; i < v.NumField(); i++ {
		field := typ.Field(i)
		fieldValue := v.Field(i)

		tag := field.Tag.Get("url")
		if tag == "" || tag == "-" {
			continue
		}
		parts := strings.Split(tag, ",")
		paramName := parts[0]
		isOptional := false
		if len(parts) > 1 && parts[1] == "omitempty" {
			isOptional = true
		}

		if fieldValue.Kind() == reflect.Ptr {
			if fieldValue.IsNil() {
				continue
			}
			fieldValue = fieldValue.Elem()
		}

		var valStr string
		// Special handling for time.Time
		if fieldValue.Type() == reflect.TypeOf(time.Time{}) {
			t := fieldValue.Interface().(time.Time)
			if t.IsZero() && isOptional { // Don't add zero time if optional
				continue
			}
			// Format as YYYY-MM-DDTHH:MM:SSZ (RFC3339 without nano for typical API query params)
			// Or use specific format required by Saxo if different.
			// Python client uses ticks or YYYYMMDDHHMMSS. Saxo often accepts ISO 8601.
			// For "Time" param in charts, it's often YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS
			// Saxo docs: Time Format "YYYY-MM-DDTHH:MM:SSZ" (UTC).
			// For "Date" only fields, "YYYY-MM-DD".
			// Let's use RFC3339 for time.Time here.
			valStr = t.UTC().Format(time.RFC3339)
		} else { // Existing logic from paramsToQueryValues
			switch fieldValue.Kind() {
			case reflect.String:
				valStr = fieldValue.String()
			case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
				valStr = strconv.FormatInt(fieldValue.Int(), 10)
			case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
				valStr = strconv.FormatUint(fieldValue.Uint(), 10)
			case reflect.Float32, reflect.Float64:
				valStr = strconv.FormatFloat(fieldValue.Float(), 'f', -1, 64)
			case reflect.Bool:
				if isOptional && !fieldValue.Bool() { continue }
				valStr = strconv.FormatBool(fieldValue.Bool())
			case reflect.Slice:
				if fieldValue.Type().Elem().Kind() == reflect.String {
					var strSlice []string
					for j := 0; j < fieldValue.Len(); j++ {
						strSlice = append(strSlice, fieldValue.Index(j).String())
					}
					valStr = strings.Join(strSlice, ",")
				} else { continue }
			default: continue
			}
		}

		if isOptional && valStr == "" && fieldValue.Kind() != reflect.Bool { // Don't omit bool false if not pointer
             // For bools, if it's optional and false, it was skipped by the continue above.
             // For other types, if optional and empty string (after conversion), skip.
            continue
        }
		values.Set(paramName, valStr)
	}
	return values, nil
}
