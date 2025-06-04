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
	"strconv" // Now needed for paramsToQueryValues
	"strings" // Now needed for paramsToQueryValues
	"time"

	"github.com/sirupsen/logrus"
)

const (
	EnvironmentLive       = "live"
	EnvironmentSimulation = "sim"

	liveAPIBaseURL        = "https://gateway.saxobank.com"
	liveStreamBaseURL     = "https://streaming.saxobank.com"
	simulationAPIBaseURL  = "https://gateway.saxobank.com"
	liveAPIOpenAPIPath    = "/openapi"
	simAPIOpenAPIPath     = "/sim/openapi"
	defaultTimeoutSeconds = 10
)

type Client struct {
	httpClient           *http.Client
	Authenticator        *saxo_authen.SaxoAuth
	Environment          string
	apiBaseURL           string
	streamBaseURL        string
	rateLimiter          *RateLimiter
	defaultHeaders       http.Header
}

func NewClient(authenticator *saxo_authen.SaxoAuth, environment string, clientTimeout time.Duration) (*Client, error) {
	if authenticator == nil {
		return nil, fmt.Errorf("authenticator cannot be nil")
	}

	var apiBasePath, streamBasePath string
	switch strings.ToLower(environment) {
	case EnvironmentLive:
		apiBasePath = liveAPIBaseURL + liveAPIOpenAPIPath
		streamBasePath = liveStreamBaseURL
	case EnvironmentSimulation, "simdemo":
		environment = EnvironmentSimulation
		apiBasePath = simulationAPIBaseURL + simAPIOpenAPIPath
		streamBasePath = liveStreamBaseURL
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
		apiBaseURL:           apiBasePath,
		streamBaseURL:        streamBasePath,
		rateLimiter:          NewRateLimiter(DefaultLowRequestsThreshold),
		defaultHeaders:       make(http.Header),
	}

	client.defaultHeaders.Set("Accept-Encoding", "gzip, deflate")
	client.defaultHeaders.Set("Cache-Control", "no-cache")

	return client, nil
}

func (c *Client) SetAPIBaseURL(baseURL string) {
	c.apiBaseURL = baseURL
}

func (c *Client) doRequest(
	ctx context.Context,
	method string,
	path string,
	queryParams url.Values,
	requestBody io.Reader,
	responseBodyType reflect.Type,
) (interface{}, *http.Response, error) {

	fullURL, err := url.Parse(c.apiBaseURL)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to parse base API URL '%s': %w", c.apiBaseURL, err)
	}
	fullURL.Path = strings.TrimRight(fullURL.Path, "/") + "/" + strings.TrimLeft(path, "/")

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

		for key, values := range c.defaultHeaders {
			for _, value := range values {
				req.Header.Add(key, value)
			}
		}
		req.Header.Set("Authorization", "Bearer "+token)
		if requestBody != nil && method != http.MethodGet && method != http.MethodDelete {
			if req.Header.Get("Content-Type") == "" {
				req.Header.Set("Content-Type", "application/json; charset=utf-8")
			}
		}

		logrus.Debugf("Saxo API Request: %s %s", method, req.URL.String())

		httpResp, reqErr = c.httpClient.Do(req)
		if reqErr != nil {
			if ctx.Err() != nil {
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
			token, err = c.Authenticator.GetToken()
			if err != nil {
				return nil, httpResp, fmt.Errorf("failed to get authentication token for retry: %w", err)
			}
			continue
		}
		break
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
		httpResp.Body.Close()
	}
	httpResp.Body = io.NopCloser(bytes.NewBuffer(bodyBytes))

	if httpResp.StatusCode >= 400 {
		return nil, httpResp, NewOpenAPIError(httpResp.StatusCode, httpResp.Status, string(bodyBytes))
	}

	if responseBodyType != nil && httpResp.StatusCode >= 200 && httpResp.StatusCode < 300 {
		if httpResp.StatusCode == http.StatusNoContent || len(bodyBytes) == 0 {
			logrus.Debugf("Response status %d or empty body, returning zero-value for type %v for %s %s", httpResp.StatusCode, responseBodyType, method, fullURL.String())
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

	return nil, httpResp, nil
}

// paramsToQueryValues converts a struct with `url` tags to url.Values.
func paramsToQueryValues(paramsStruct interface{}) (url.Values, error) {
	values := url.Values{}
	if paramsStruct == nil {
		return values, nil
	}

	v := reflect.ValueOf(paramsStruct)
	if v.Kind() == reflect.Ptr {
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
				continue
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
			// For bools with omitempty, only include if true.
			// If not omitempty, always include.
			if isOptional && !fieldValue.Bool() {
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
			} else {
				continue
			}
		default:
			continue
		}

		// Add if not optional and value is "zero" (e.g. empty string for string type, 0 for int, false for bool)
		// OR if it has a non-zero value.
		// This handles omitempty correctly for required fields that might have a "zero" value.
		if !isOptional || !reflect.DeepEqual(fieldValue.Interface(), reflect.Zero(fieldValue.Type()).Interface()) || (fieldValue.Kind() == reflect.Bool && fieldValue.Bool()){
			// The last condition `(fieldValue.Kind() == reflect.Bool && fieldValue.Bool())` is to ensure `false` with `omitempty` is not added,
            // but `true` is. If omitempty is not present, false should be added.
            // The DeepEqual check handles most zero values correctly for omitempty.
            // For bools, if omitempty and false, it's skipped by the DeepEqual. If omitempty and true, it's added.
            // If not omitempty, it's added regardless of value.
            // This logic is getting complex; go-querystring handles this better.
            // Simplified: if isOptional and it's the zero value (and not a bool true), skip.
             if isOptional && reflect.DeepEqual(fieldValue.Interface(), reflect.Zero(fieldValue.Type()).Interface()) && fieldValue.Kind() != reflect.Bool {
                continue
            }
             if isOptional && fieldValue.Kind() == reflect.Bool && !fieldValue.Bool() { // omitempty for bool means omit if false
                continue
            }
			values.Set(paramName, valStr)
		}
	}
	return values, nil
}
