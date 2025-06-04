package saxo_openapi

import (
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/sirupsen/logrus"
)

const (
	XRateLimitSessionRemaining = "X-RateLimit-Session-Remaining"
	XRateLimitSessionReset = "X-RateLimit-Session-Reset"
	DefaultLowRequestsThreshold = 5
)

type RateLimiter struct {
	sessionRemaining   int
	sessionResetTime   time.Time
	mutex              sync.Mutex
	lowRequestsThreshold int
}

func NewRateLimiter(lowRequestsThreshold int) *RateLimiter {
	if lowRequestsThreshold <= 0 {
		lowRequestsThreshold = DefaultLowRequestsThreshold
	}
	return &RateLimiter{
		sessionRemaining:   lowRequestsThreshold * 2,
		sessionResetTime:   time.Now(),
		lowRequestsThreshold: lowRequestsThreshold,
	}
}

func (rl *RateLimiter) UpdateLimits(headers http.Header) {
	rl.mutex.Lock()
	defer rl.mutex.Unlock()

	remainingStr := headers.Get(XRateLimitSessionRemaining)
	resetStr := headers.Get(XRateLimitSessionReset)

	if remainingStr != "" {
		remaining, err := strconv.Atoi(remainingStr)
		if err == nil {
			rl.sessionRemaining = remaining
		} else {
			logrus.Warnf("RateLimiter: Failed to parse '%s' header '%s': %v", XRateLimitSessionRemaining, remainingStr, err)
		}
	}

	if resetStr != "" {
		resetSeconds, err := strconv.ParseFloat(resetStr, 64)
		if err == nil {
			resetDuration := time.Duration(resetSeconds*1000) * time.Millisecond
			rl.sessionResetTime = time.Now().Add(resetDuration)
		} else {
			logrus.Warnf("RateLimiter: Failed to parse '%s' header '%s': %v", XRateLimitSessionReset, resetStr, err)
		}
	}
}

func (rl *RateLimiter) WaitIfNeeded() {
	rl.mutex.Lock()
	remaining := rl.sessionRemaining
	resetAt := rl.sessionResetTime
	threshold := rl.lowRequestsThreshold
	rl.mutex.Unlock()

	if remaining <= 0 {
		waitUntil := resetAt.Add(1 * time.Second)
		sleepDuration := time.Until(waitUntil)
		if sleepDuration > 0 {
			logrus.Warnf("RateLimiter: No requests remaining. Sleeping for %v until %v.", sleepDuration, waitUntil)
			time.Sleep(sleepDuration)
		}
	} else if remaining < threshold {
		timeToReset := time.Until(resetAt)
		// Proportional wait logic from Python client was more complex.
		// This is a simplified version: if reset is very soon, just wait for it.
		if timeToReset > 0 && timeToReset < (5 * time.Second) {
			logrus.Infof("RateLimiter: Low requests (%d/%d) and reset is soon (%v). Waiting.", remaining, threshold, timeToReset)
			waitUntil := resetAt.Add(500 * time.Millisecond)
			sleepDuration := time.Until(waitUntil)
			if sleepDuration > 0 {
				time.Sleep(sleepDuration)
			}
		} else {
			logrus.Warnf("RateLimiter: Low requests (%d/%d). Consider slowing down. Reset in %v.", remaining, threshold, timeToReset)
		}
	}
}
