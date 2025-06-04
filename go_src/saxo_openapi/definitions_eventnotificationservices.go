package saxo_openapi

// import "time" // If needed for timestamp fields

// --- Event Notification Services (ens) ---

// ENSContextSubscription represents a subscription to a context.
// This is similar to portfolio subscriptions but for ENS.
type ENSContextSubscription struct {
	ContextID         string                 `json:"ContextId"`
	Format            string                 `json:"Format,omitempty"`
	InactivityTimeout int                    `json:"InactivityTimeout"` // In seconds
	ReferenceID       string                 `json:"ReferenceId"`
	RefreshRate       int                    `json:"RefreshRate"`       // In milliseconds
	Snapshot          map[string]interface{} `json:"Snapshot,omitempty"`  // Snapshot of data, structure varies by context
	Tag               string                 `json:"Tag,omitempty"`
	State             string                 `json:"State,omitempty"` // e.g. "Active"
	TargetReferenceID string                 `json:"TargetReferenceId,omitempty"`
}

// ENSCreateSubscriptionArgs are arguments for creating a new ENS subscription.
type ENSCreateSubscriptionArgs struct {
	ContextID   string                 `json:"ContextId"`
	Format      string                 `json:"Format,omitempty"`
	ReferenceID string                 `json:"ReferenceId"`
	RefreshRate int                    `json:"RefreshRate"`
	Tag         string                 `json:"Tag,omitempty"`
	Arguments   map[string]interface{} `json:"Arguments"` // Specific arguments for the subscription type
	// e.g., {"AccountKey": "...", "Activities": ["AccountOrders", "Positions"]} for Account an ENS context
	// For ENS, "SchemaName" and "SchemaVersion" might be part of Arguments or top-level.
	// Python client suggests they are often top-level in the data passed to the POST.
	// The generic CreateSubscriptionArgs in portfolio had Arguments.
	// Let's assume a similar structure for now, or that these are passed in the map.
}

// ENSCreateSubscriptionResponse is the response from creating an ENS subscription.
type ENSCreateSubscriptionResponse ENSContextSubscription // Typically echoes back subscription details


// ENSActivitySubscription represents a subscription to specific activities for an account.
// This seems to be the request body for POST /ens/v1/activities/subscriptions
type ENSActivitySubscriptionRequest struct {
	ContextID   string   `json:"ContextId"`             // Unique identifier for the context
	Format      string   `json:"Format,omitempty"`      // Optional: "application/json"
	ReferenceID string   `json:"ReferenceId"`           // Unique identifier for this subscription instance
	RefreshRate int      `json:"RefreshRate,omitempty"` // Optional: Suggested client refresh rate
	Tag         string   `json:"Tag,omitempty"`         // Optional: For grouping subscriptions
	Activities  []string `json:"Activities"`            // List of activities to subscribe to, e.g., "AccountOrders", "Positions"
	AccountKey  string   `json:"AccountKey"`            // Required for account activity subscriptions
}

// ENSActivitySubscriptionResponse is the response from creating an activity subscription.
// It seems to be the same as ENSContextSubscription, representing the created subscription.
type ENSActivitySubscriptionResponse ENSContextSubscription
