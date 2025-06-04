package saxo_openapi

// This file contains common helper utilities for tests in the saxo_openapi package.
// The _test.go suffix ensures it's only compiled with tests.

func stringPtr(s string) *string { return &s }
func intPtr(i int) *int       { return &i }
func boolPtr(b bool) *bool    { return &b }
func floatPtr(f float64) *float64 { return &f } // Renamed from float64Ptr
