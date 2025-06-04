package database

// This file can hold common types for the database package,
// especially those needed by other packages but not tied to a specific manager's full implementation.

// OpenPositionAction represents an open position with its action type.
// This type is used by the PositionManagerInterface in the 'trade' package.
type OpenPositionAction struct {
	ID         string
	ActionType string // e.g., "buy", "sell"
}

// Add other shared database-related types here if they emerge.
// For example, if OrderData or PositionData structs from manager files
// become needed by multiple packages, they could be centralized or have base versions here.
// For now, only OpenPositionAction is added to resolve the immediate dependency.
