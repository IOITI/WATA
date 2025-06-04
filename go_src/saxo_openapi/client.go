package saxo_openapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"pymath/go_src/saxo_authen"
	"reflect"
	"strconv"
	"strings"
	"time"

	"github.com/sirupsen/logrus"
)

const (
	EnvironmentLive       = "live"
	EnvironmentSimulation = "sim"

	liveAPIBaseURL        = "https://gateway.saxobank.com"
	liveStreamBaseURL     = "https://streaming.saxobank.com" // For WebSocket
	simulationAPIBaseURL  = "https://gateway.saxobank.com"
	liveAPIOpenAPIPath    = "/openapi" // Base path for REST API
	simAPIOpenAPIPath     = "/sim/openapi"
	defaultTimeoutSeconds = 10
)

type Client struct {
	httpClient           *http.Client
	Authenticator        *saxo_authen.SaxoAuth
	Environment          string
	apiBaseURL           string // Full base URL for REST API, e.g., "https://gateway.saxobank.com/openapi"
	streamBaseURL        string // Base URL for streaming, e.g., "wss://streaming.saxobank.com"
	rateLimiter          *RateLimiter
	defaultHeaders       http.Header
}

func NewClient(authenticator *saxo_authen.SaxoAuth, environment string, clientTimeout time.Duration) (*Client, error) {
	if authenticator == nil {
		return nil, fmt.Errorf("authenticator cannot be nil")
	}

	var apiBase, streamBase string // These are the hostnames
	var apiPath string

	switch strings.ToLower(environment) {
	case EnvironmentLive:
		apiBase = liveAPIBaseURL
		streamBase = liveStreamBaseURL
		apiPath = liveAPIOpenAPIPath
	case EnvironmentSimulation, "simdemo":
		environment = EnvironmentSimulation
		apiBase = simulationAPIBaseURL
		streamBase = liveStreamBaseURL // Saxo uses the same streaming host for SIM
		apiPath = simAPIOpenAPIPath
	default:
		return nil, fmt.Errorf("invalid environment: '%s'. Must be '%s' or '%s'",
			environment, EnvironmentLive, EnvironmentSimulation)
	}

	if clientTimeout <= 0 {
		clientTimeout = time.Duration(defaultTimeoutSeconds) * time.Second
	}

	client := &Client{
		httpClient:           &http.Client{Timeout: clientTimeout},
		Authenticator:        authenticator,
		Environment:          environment,
		apiBaseURL:           apiBase + apiPath, // Store the full base path for API calls
		streamBaseURL:        streamBase,        // Store just the host for streaming
		rateLimiter:          NewRateLimiter(DefaultLowRequestsThreshold),
		defaultHeaders:       make(http.Header),
	}

	client.defaultHeaders.Set("Accept-Encoding", "gzip, deflate")
	client.defaultHeaders.Set("Cache-Control", "no-cache")
	// Other client-wide default headers can be set here if needed.

	return client, nil
}

// SetAPIBaseURL allows overriding the API base URL, primarily for testing purposes.
func (c *Client) SetAPIBaseURL(baseURL string) {
	c.apiBaseURL = baseURL
}

// SetStreamBaseURL allows overriding the Stream base URL, primarily for testing purposes.
func (c *Client) SetStreamBaseURL(baseURL string) {
	c.streamBaseURL = baseURL
}


// doRequest is a private helper method to make HTTP requests to the Saxo API.
func (c *Client) doRequest(
	ctx context.Context,
	method string,
	path string, // Relative path to the specific endpoint, e.g., "port/v1/accounts"
	queryParams url.Values,
	requestBody io.Reader,
	responseBodyType reflect.Type,
) (interface{}, *http.Response, error) {

	// Construct full URL: c.apiBaseURL already includes /openapi or /sim/openapi
	fullURLString := strings.TrimRight(c.apiBaseURL, "/") + "/" + strings.TrimLeft(path, "/")
	fullURL, err := url.Parse(fullURLString)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to construct URL from base '%s' and path '%s': %w", c.apiBaseURL, path, err)
	}

	if queryParams != nil {
		fullURL.RawQuery = queryParams.Encode()
	}

	c.rateLimiter.WaitIfNeeded()

	token, err := c.Authenticator.GetToken()
	if err != nil {
		return nil, nil, fmt.Errorf("failed to get authentication token: %w", err)
	}

	var attempt int
	var httpResp *http.Response
	var reqErr error

	for attempt = 0; attempt < 2; attempt++ {
		req, err := http.NewRequestWithContext(ctx, method, fullURL.String(), requestBody)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to create HTTP request for %s %s: %w", method, fullURL.String(), err)
		}

		for key, values := range c.defaultHeaders { // Apply client-wide default headers
			for _, value := range values {
				req.Header.Add(key, value)
			}
		}
		req.Header.Set("Authorization", "Bearer "+token)
		// Set Content-Type for relevant methods if a body is present and Content-Type not already set
		if requestBody != nil && (method == http.MethodPost || method == http.MethodPut || method == http.MethodPatch) {
			if req.Header.Get("Content-Type") == "" { // Don't override if already set (e.g. by defaultHeaders)
				req.Header.Set("Content-Type", "application/json; charset=utf-8")
			}
		}

		logrus.Debugf("Saxo API Request: %s %s", method, req.URL.String())

		httpResp, reqErr = c.httpClient.Do(req)
		if reqErr != nil {
			if ctx.Err() != nil { // Check if the context was cancelled
				return nil, nil, fmt.Errorf("HTTP request context cancelled for %s %s: %w", method, fullURL.String(), ctx.Err())
			}
			return nil, nil, fmt.Errorf("HTTP request execution failed for %s %s: %w", method, fullURL.String(), reqErr)
		}

		c.rateLimiter.UpdateLimits(httpResp.Header)

		if httpResp.StatusCode == http.StatusTooManyRequests && attempt == 0 {
			logrus.Warnf("Rate limit hit (429). Waiting as per rate limiter and retrying once for %s %s.", method, fullURL.String())
			if httpResp.Body != nil {
				io.Copy(io.Discard, httpResp.Body)
				httpResp.Body.Close()
			}
			c.rateLimiter.WaitIfNeeded()
			// Re-fetch token in case the previous one caused an issue or for long waits.
			// GetToken() should handle internal refresh if necessary.
			token, err = c.Authenticator.GetToken()
			if err != nil {
				// Return the 429 response if token re-fetch fails, as we can't retry.
				return nil, httpResp, fmt.Errorf("failed to get authentication token for retry after 429: %w", err)
			}
			continue
		}
		break // Exit loop if not 429 or if it's the second attempt
	}

	if reqErr != nil {
	    return nil, httpResp, fmt.Errorf("HTTP request execution failed finally for %s %s: %w", method, fullURL.String(), reqErr)
	}

	var bodyBytes []byte
	if httpResp.Body != nil {
		bodyBytes, err = io.ReadAll(httpResp.Body)
		if err != nil {
			return nil, httpResp, fmt.Errorf("failed to read response body for %s %s: %w", method, fullURL.String(), err)
		}
		defer httpResp.Body.Close() // Close the original body
		// Replace Body with a new reader for the read bytes, so it can be "re-read" by callers if they need raw response
		httpResp.Body = io.NopCloser(bytes.NewBuffer(bodyBytes))
	}


	if httpResp.StatusCode >= 400 {
		return nil, httpResp, NewOpenAPIError(httpResp.StatusCode, httpResp.Status, string(bodyBytes))
	}

	if responseBodyType != nil && httpResp.StatusCode >= 200 && httpResp.StatusCode < 300 {
		if httpResp.StatusCode == http.StatusNoContent || len(bodyBytes) == 0 {
			logrus.Debugf("Response status %d or empty body, returning zero-value for type %v for %s %s", httpResp.StatusCode, responseBodyType, method, fullURL.String())
			// Create a new zero-value instance of the target type (which is a pointer to struct)
			return reflect.New(responseBodyType).Interface(), httpResp, nil
		}

		targetInstance := reflect.New(responseBodyType).Interface()
		err := json.Unmarshal(bodyBytes, targetInstance)
		if err != nil {
			return nil, httpResp, fmt.Errorf("failed to unmarshal successful response JSON (status %d) into type %v for %s %s: %w. Body: %s",
				httpResp.StatusCode, responseBodyType, method, fullURL.String(), err, string(bodyBytes))
		}
		return targetInstance, httpResp, nil
	}

	return nil, httpResp, nil // Success status but no target type or not 2xx that implies unmarshalling
}

// paramsToQueryValues converts a struct with `url` tags to url.Values.
func paramsToQueryValues(paramsStruct interface{}) (url.Values, error) {
	values := url.Values{}
	if paramsStruct == nil {
		return values, nil
	}

	v := reflect.ValueOf(paramsStruct)
	if v.Kind() == reflect.Ptr {
		if v.IsNil() { // If pointer is nil, treat as empty params
			return values, nil
		}
		v = v.Elem()
	}
	if v.Kind() != reflect.Struct {
		return nil, fmt.Errorf("paramsToQueryValues: expected a struct or pointer to struct, got %T", paramsStruct)
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
				continue // Omit if nil pointer
			}
			fieldValue = fieldValue.Elem()
		}

		var valStr string
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
			if isOptional && !fieldValue.Bool() { // omitempty for bool means omit if false
				continue
			}
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

		// Add if not optional and value is "zero" (e.g. empty string for string type)
		// OR if it has a non-zero value.
		// This logic for omitempty needs to be robust:
		// - For pointers, handled by IsNil.
		// - For value types, if isOptional and it's the zero value, omit.
		// - Bool with omitempty: omit if false (handled above).
		if isOptional && valStr == "" && fieldValue.Kind() != reflect.Bool { // For non-bools, if optional and empty string, omit.
            continue
        }
		values.Set(paramName, valStr)
	}
	return values, nil
}
