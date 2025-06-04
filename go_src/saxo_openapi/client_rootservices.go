package saxo_openapi

import (
	"bytes"
	"context" // Added context
	"encoding/json"
	"fmt"
	"net/url"
	"reflect" // Added reflect
)

// GetUser retrieves user information.
// Corresponds to GET /openapi/root/v1/user
func (c *Client) GetUser(ctx context.Context) (*UserResponse, error) {
	responseBodyType := reflect.TypeOf(UserResponse{})
	result, _, err := c.doRequest(ctx, "GET", "root/v1/user", nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*UserResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert UserResponse from API response. Got: %T", result)
}

// GetClient retrieves information about the client associated with the current session.
// Corresponds to GET /openapi/root/v1/clients/me
func (c *Client) GetClient(ctx context.Context) (*ClientResponse, error) {
	responseBodyType := reflect.TypeOf(ClientResponse{})
	result, _, err := c.doRequest(ctx, "GET", "root/v1/clients/me", nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*ClientResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert ClientResponse from API response. Got: %T", result)
}

// GetApplication retrieves information about the current application.
// Corresponds to GET /openapi/root/v1/applications/me
func (c *Client) GetApplication(ctx context.Context) (*ApplicationResponse, error) {
	responseBodyType := reflect.TypeOf(ApplicationResponse{})
	result, _, err := c.doRequest(ctx, "GET", "root/v1/applications/me", nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*ApplicationResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert ApplicationResponse from API response. Got: %T", result)
}


// GetSessionCapabilities retrieves the capabilities of the current session.
// Corresponds to GET /openapi/root/v1/sessions/capabilities
func (c *Client) GetSessionCapabilities(ctx context.Context) (*SessionCapabilities, error) {
	responseBodyType := reflect.TypeOf(SessionCapabilities{})
	result, _, err := c.doRequest(ctx, "GET", "root/v1/sessions/capabilities", nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*SessionCapabilities); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert SessionCapabilities from API response. Got: %T", result)
}

// UpdateSessionCapabilities updates capabilities for the current session.
// Corresponds to PATCH /openapi/root/v1/sessions/capabilities
func (c *Client) UpdateSessionCapabilities(ctx context.Context, capabilities map[string]bool) (*SessionCapabilities, error) {
	bodyBytes, err := json.Marshal(capabilities)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal capabilities for update: %w", err)
	}

	responseBodyType := reflect.TypeOf(SessionCapabilities{})
	result, _, err := c.doRequest(ctx, "PATCH", "root/v1/sessions/capabilities", nil, bytes.NewBuffer(bodyBytes), responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*SessionCapabilities); ok {
		return typedResult, nil
	}
	// Handle cases like 204 No Content where result might be a zero-value instance if doRequest returns that
    if result != nil && reflect.ValueOf(result).Elem().IsValid() && reflect.ValueOf(result).Elem().IsZero() {
        // This means doRequest returned a zero-value struct (e.g. for 204 No Content)
        // Depending on API spec, this might be valid. If an empty struct is not desired, return nil.
        // Or, ensure the specific type is returned if that's the contract.
        // For now, if it's a zero struct of the correct pointer type, assume it's intended (e.g. 204 response)
        if _, ok := result.(*SessionCapabilities); ok {
             // It's a pointer to a zero SessionCapabilities struct.
             // If the API guarantees a body on 200 for PATCH, then this path might indicate unexpected 204.
             // If 204 is possible and means "success, no change to return" or "success, state is as requested",
             // then returning the zero struct (or nil) might be acceptable.
             // For now, let's assume if it's non-nil, it's what we want.
            return result.(*SessionCapabilities), nil
        }
    }
	return nil, fmt.Errorf("failed to type assert SessionCapabilities from API response or response was unexpected. Got: %T, Value: %v", result, result)
}


// GetDiagnostics retrieves diagnostic information about the service.
// Corresponds to GET /openapi/root/v1/diagnostics
func (c *Client) GetDiagnostics(ctx context.Context) (*DiagnosticsResponse, error) {
	responseBodyType := reflect.TypeOf(DiagnosticsResponse{})
	result, _, err := c.doRequest(ctx, "GET", "root/v1/diagnostics", nil, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*DiagnosticsResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert DiagnosticsResponse from API response. Got: %T", result)
}

// GetFeatures retrieves a list of features and their status.
// Corresponds to GET /openapi/root/v1/features
func (c *Client) GetFeatures(ctx context.Context, group *string, namesCSV *string) (*FeaturesResponse, error) {
	params := url.Values{}
	if group != nil {
		params.Set("Group", *group)
	}
	if namesCSV != nil {
		params.Set("NamesCSV", *namesCSV)
	}

	responseBodyType := reflect.TypeOf(FeaturesResponse{})
	result, _, err := c.doRequest(ctx, "GET", "root/v1/features", params, nil, responseBodyType)
	if err != nil {
		return nil, err
	}
	if typedResult, ok := result.(*FeaturesResponse); ok {
		return typedResult, nil
	}
	return nil, fmt.Errorf("failed to type assert FeaturesResponse from API response. Got: %T", result)
}
