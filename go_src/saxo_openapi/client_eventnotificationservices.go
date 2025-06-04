package saxo_openapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http" // Added import
	"net/url"
	"reflect"
	// "strings"
)

// --- Event Notification Services (ENS) ---

// CreateENSSubscription creates a subscription for event notifications.
// POST /openapi/ens/v1/activities/subscriptions (for account activities)
// POST /openapi/ens/v1/contexts/{ContextId}/subscriptions (for general context subscriptions, less common in Python client example)
// The Python client's `create_subscription` in `activities.py` points to the /activities/subscriptions endpoint.
// The `ENSCreateSubscriptionArgs` is more generic for /contexts/{ContextId}/subscriptions path.
// The `ENSActivitySubscriptionRequest` is specific for /activities/subscriptions.
// Let's provide two methods for clarity or make one smart enough.
// For now, focusing on the /activities/subscriptions as it's more concrete in Python client.
func (c *Client) CreateActivitySubscription(ctx context.Context, args ENSActivitySubscriptionRequest) (*ENSActivitySubscriptionResponse, error) {
	path := "ens/v1/activities/subscriptions"

	bodyBytes, err := json.Marshal(args)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal ENS activity subscription args: %w", err)
	}

	responseBodyType := reflect.TypeOf(ENSActivitySubscriptionResponse{})
	result, httpResp, err := c.doRequest(ctx, "POST", path, nil, bytes.NewBuffer(bodyBytes), responseBodyType)
	if err != nil {
		return nil, err
	}

	// ENS subscription creation often returns 201 Created with subscription details in body
	// It can also return 200 OK if a subscription with the same ReferenceId already exists and is updated.
	if httpResp.StatusCode != http.StatusCreated && httpResp.StatusCode != http.StatusOK {
		// If it's an OpenAPIError, it would have been returned by doRequest already.
		// This check is for unexpected success codes.
		return nil, fmt.Errorf("unexpected status code %d when creating ENS activity subscription: %s", httpResp.StatusCode, httpResp.Status)
	}

	if typedResult, ok := result.(*ENSActivitySubscriptionResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert ENSActivitySubscriptionResponse. Got: %T", result)
}


// RemoveENSSubscriptionsByTag removes ENS subscriptions for a specific contextId and tag.
// DELETE /openapi/ens/v1/activities/subscriptions/{ContextId}/{Tag}
// (The Python client does DELETE /ens/v1/activities/subscriptions/{context_id}?Tag={tag})
// This is different from portfolio. Portfolio was /port/v1/{endpoint}/subscriptions/{ContextId}/{Tag}
// ENS seems to be /ens/v1/activities/subscriptions/{ContextId}?Tag={Tag}
// Let's follow the Python client's structure for ENS if it differs.
// Python: self.client.delete(self.ENDPOINT + "/" + context_id, params=params) where ENDPOINT is "/ens/v1/activities/subscriptions"
// So, it is indeed DELETE /ens/v1/activities/subscriptions/{context_id} with Tag as query param.
func (c *Client) RemoveENSSubscriptionsByTag(ctx context.Context, contextID string, tag string) error {
	if contextID == "" {
		return fmt.Errorf("contextID is required")
	}
	// Tag is optional. If empty, all subscriptions for contextID might be removed.
	// Python client sends it as query param.

	path := fmt.Sprintf("ens/v1/activities/subscriptions/%s", contextID)
	params := url.Values{}
	if tag != "" {
		params.Set("Tag", tag)
	}

	_, httpResp, err := c.doRequest(ctx, "DELETE", path, params, nil, nil)
	if err != nil {
		return err
	}
	// Expect 202 Accepted or 204 No Content for successful deletion
	if httpResp.StatusCode != http.StatusAccepted && httpResp.StatusCode != http.StatusNoContent {
		return fmt.Errorf("unexpected status code %d when removing ENS subscription by tag", httpResp.StatusCode)
	}
	return nil
}

// RemoveENSSubscriptionByID removes a single ENS subscription by its referenceId for a given contextId.
// DELETE /openapi/ens/v1/activities/subscriptions/{ContextId}/{ReferenceId}
// Python client uses this path for its `remove_subscription(context_id, reference_id)`
func (c *Client) RemoveENSSubscriptionByID(ctx context.Context, contextID string, referenceID string) error {
	if contextID == "" || referenceID == "" {
		return fmt.Errorf("contextID and referenceID are required")
	}
	path := fmt.Sprintf("ens/v1/activities/subscriptions/%s/%s", contextID, referenceID)

	_, httpResp, err := c.doRequest(ctx, "DELETE", path, nil, nil, nil)
	if err != nil {
		return err
	}
	if httpResp.StatusCode != http.StatusAccepted && httpResp.StatusCode != http.StatusNoContent {
		// Saxo docs mention 204 No Content for DELETE of single subscription
		return fmt.Errorf("unexpected status code %d when removing ENS subscription by ID", httpResp.StatusCode)
	}
	return nil
}
