package saxo_openapi

import (
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/sirupsen/logrus"
)

const (
	// XRateLimitSessionRemaining is the header for remaining requests in the current window.
	XRateLimitSessionRemaining = "X-RateLimit-Session-Remaining"
	// XRateLimitSessionReset is the header for seconds until the request quota window resets.
	XRateLimitSessionReset = "X-RateLimit-Session-Reset"
	// DefaultLowRequestsThreshold is a threshold below which we might start to slow down or pause.
	DefaultLowRequestsThreshold = 5 // Example value, can be configured
)

// RateLimiter manages API call rates based on Saxo's rate limit headers.
type RateLimiter struct {
	sessionRemaining   int
	sessionResetTime   time.Time
	mutex              sync.Mutex
	lowRequestsThreshold int
	// lastRateLimitWarningTime time.Time // To avoid spamming warnings
}

// NewRateLimiter creates a new RateLimiter.
// lowRequestsThreshold can be set to define when WaitIfNeeded starts actively pausing.
func NewRateLimiter(lowRequestsThreshold int) *RateLimiter {
	if lowRequestsThreshold <= 0 {
		lowRequestsThreshold = DefaultLowRequestsThreshold
	}
	return &RateLimiter{
		sessionRemaining:   lowRequestsThreshold * 2, // Start with a reasonable default above threshold
		sessionResetTime:   time.Now(),             // Default to now, will be updated by first response
		lowRequestsThreshold: lowRequestsThreshold,
	}
}

// UpdateLimits parses rate limit headers from an HTTP response and updates the limiter's state.
func (rl *RateLimiter) UpdateLimits(headers http.Header) {
	rl.mutex.Lock()
	defer rl.mutex.Unlock()

	remainingStr := headers.Get(XRateLimitSessionRemaining)
	resetStr := headers.Get(XRateLimitSessionReset)

	if remainingStr != "" {
		remaining, err := strconv.Atoi(remainingStr)
		if err == nil {
			rl.sessionRemaining = remaining
			// logrus.Debugf("RateLimiter: Updated sessionRemaining to %d", remaining)
		} else {
			logrus.Warnf("RateLimiter: Failed to parse '%s' header '%s': %v", XRateLimitSessionRemaining, remainingStr, err)
		}
	} else {
		// If header is missing, it could mean no limit enforced or an issue.
		// We might gradually decrease remaining or keep it as is.
		// For now, if header is missing, we don't change sessionRemaining.
		// logrus.Debugf("RateLimiter: '%s' header not found.", XRateLimitSessionRemaining)
	}

	if resetStr != "" {
		resetSeconds, err := strconv.ParseFloat(resetStr, 64) // Saxo uses float seconds e.g. "14.678"
		if err == nil {
			// Calculate duration from float seconds
			// Nanoseconds not needed for reset, Millisecond precision is fine.
			resetDuration := time.Duration(resetSeconds*1000) * time.Millisecond
			rl.sessionResetTime = time.Now().Add(resetDuration)
			// logrus.Debugf("RateLimiter: Updated sessionResetTime to %v (in %v)", rl.sessionResetTime, resetDuration)
		} else {
			logrus.Warnf("RateLimiter: Failed to parse '%s' header '%s': %v", XRateLimitSessionReset, resetStr, err)
		}
	} else {
		// logrus.Debugf("RateLimiter: '%s' header not found.", XRateLimitSessionReset)
	}
}

// WaitIfNeeded checks if the number of remaining requests is low or zero.
// If so, it waits until the session reset time.
func (rl *RateLimiter) WaitIfNeeded() {
	rl.mutex.Lock()
	// Make a copy of current values for decision making after unlocking if needed,
	// but for this logic, direct use is fine as long as lock is held for reads/writes.
	remaining := rl.sessionRemaining
	resetAt := rl.sessionResetTime
	threshold := rl.lowRequestsThreshold
	rl.mutex.Unlock() // Unlock early if further logic doesn't need to protect shared state

	if remaining <= 0 {
		// No requests left, must wait
		waitUntil := resetAt.Add(1 * time.Second) // Add a small buffer
		sleepDuration := time.Until(waitUntil)
		if sleepDuration > 0 {
			logrus.Warnf("RateLimiter: No requests remaining. Sleeping for %v until %v.", sleepDuration, waitUntil)
			time.Sleep(sleepDuration)
		}
	} else if remaining < threshold {
		// Approaching limit, consider a proportional wait or just log
		// The Python version has a more complex proportional wait.
		// For a simpler Go version, we can just log a warning or implement a smaller wait.
		// If sessionResetTime is very close, even with few remaining, waiting might be good.
		timeToReset := time.Until(resetAt)
		if timeToReset > 0 && timeToReset < (time.Duration(threshold-remaining)*time.Second*2) { // Example: if 2s per remaining req needed
			logrus.Infof("RateLimiter: Low requests (%d/%d). Reset in %v. Considering a short pause.", remaining, threshold, timeToReset)
			// Simple wait: if reset is soon, just wait for it.
			if timeToReset < (5 * time.Second) { // If reset is within 5s and we are low
				waitUntil := resetAt.Add(500 * time.Millisecond) // Small buffer
				sleepDuration := time.Until(waitUntil)
				if sleepDuration > 0 {
					logrus.Warnf("RateLimiter: Low requests and reset is soon. Sleeping for %v.", sleepDuration)
					time.Sleep(sleepDuration)
				}
			}
		} else {
			logrus.Warnf("RateLimiter: Low requests (%d/%d). Consider slowing down. Reset in %v.", remaining, threshold, timeToReset)
		}
	}
	// No waiting needed if above threshold
}
