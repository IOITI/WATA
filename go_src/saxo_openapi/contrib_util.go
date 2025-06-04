package saxo_openapi // Part of saxo_openapi package for Client access

import (
	"context"
	"fmt"
	"strconv"
	// "strings"
	// "github.com/sirupsen/logrus" // If logging is needed
)

// InstrumentToUic attempts to resolve an instrument identifier (UIC or symbol) to a UIC.
// instrument: Can be an int (UIC), string (Symbol or UIC as string).
// client: An instance of the Saxo OpenAPI client to make calls if needed.
// assetTypes: Optional comma-separated string of AssetTypes to narrow down search if instrument is a symbol.
// Returns UIC (int), AssetType (string, if resolved), and error.
func InstrumentToUic(ctx context.Context, client *Client, instrument interface{}, assetTypes *string) (int, string, error) {
	if instrument == nil {
		return 0, "", fmt.Errorf("instrument identifier cannot be nil")
	}

	// Check if instrument is already an int (UIC)
	if uic, ok := instrument.(int); ok {
		// To get the AssetType, we might need to call GetInstrumentDetails
		// This makes the utility function more complex if AssetType is always required.
		// For now, if UIC is provided, we assume AssetType might not be discoverable by this func alone.
		// The Python version also seems to primarily focus on getting the UIC.
		// It does pass AssetType to the /instruments call.
		// Let's assume if UIC is given, AssetType might be known or not needed by caller from this func.
		return uic, "", nil // Returning empty AssetType if UIC is directly provided
	}

	// Check if instrument is a string (could be UIC as string or a symbol)
	if instrStr, ok := instrument.(string); ok {
		// Try to parse as int first
		if uic, err := strconv.Atoi(instrStr); err == nil {
			// Similar to above, AssetType might need a separate call.
			return uic, "", nil
		}

		// If not an int string, assume it's a symbol and search
		if client == nil {
			return 0, "", fmt.Errorf("client is required to search for instrument by symbol '%s'", instrStr)
		}

		params := GetInstrumentsParams{
			Keywords:   &instrStr,
			AssetTypes: assetTypes, // Use provided assetTypes for filtering
			Top:        intPtr(5),   // Limit results to make a choice if multiple match
		}

		resp, err := client.GetInstruments(ctx, &params)
		if err != nil {
			return 0, "", fmt.Errorf("API call to search for instrument '%s' failed: %w", instrStr, err)
		}

		if resp == nil || len(resp.Data) == 0 {
			return 0, "", fmt.Errorf("no instrument found matching symbol '%s' (AssetTypes: %v)", instrStr, getStringVal(assetTypes))
		}

		if len(resp.Data) > 1 {
			// Multiple instruments found. Log warning, return the first one.
			// Or, could return an error asking for more specific criteria.
			// logrus.Warnf("Multiple instruments found for symbol '%s'. Returning the first one: %+v", instrStr, resp.Data[0])
			// For now, let's be strict if an exact match is expected.
			// The Python version seems to pick the first one if its Description or Symbol matches.
			// For simplicity, if more than one, ask for more specific keywords or use UIC.
			// However, the python code picks the first if only one, or if multiple, it iterates and tries to find
			// an exact match on Symbol or Description.
			// Let's try to replicate that part if essential.
			// The most common use case is a single, clear match.
			// For now, if not single, return error.
			return 0, "", fmt.Errorf("multiple instruments (%d) found for symbol '%s'. Please use UIC or more specific keywords. First found: %s (UIC: %d)", len(resp.Data), instrStr, resp.Data[0].Description, resp.Data[0].Uic)
		}

		// Single instrument found
		instrumentDetail := resp.Data[0]
		return instrumentDetail.Uic, instrumentDetail.AssetType, nil
	}

	return 0, "", fmt.Errorf("unsupported instrument identifier type: %T", instrument)
}

// Helper to get string value from pointer or default.
func getStringVal(s *string) string {
	if s == nil {
		return "<not specified>"
	}
	return *s
}

// intPtr and stringPtr helpers would be useful here if GetInstrumentsParams used them.
// They are defined in other _test.go files. Ideally, they'd be in a shared util or this package if also used by main code.
// For GetInstrumentsParams, they are already pointers.

/*
// Example usage (conceptual, would be in a main or test):
func ResolveAndPrintUic(client *Client, instrumentIdentifier interface{}, assetTypes *string) {
	uic, assetType, err := InstrumentToUic(context.Background(), client, instrumentIdentifier, assetTypes)
	if err != nil {
		fmt.Printf("Error resolving instrument '%v': %v\n", instrumentIdentifier, err)
		return
	}
	fmt.Printf("Instrument '%v' resolved to UIC: %d, AssetType: '%s'\n", instrumentIdentifier, uic, assetType)
}
*/
