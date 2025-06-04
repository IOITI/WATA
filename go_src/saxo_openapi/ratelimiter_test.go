package saxo_openapi

import (
	"net/http"
	// "strconv" // No longer used
	"testing"
	"time"
)

func TestNewRateLimiter(t *testing.T) {
	rlDefault := NewRateLimiter(0) // Test with 0, should use default
	if rlDefault.lowRequestsThreshold != DefaultLowRequestsThreshold {
		t.Errorf("Expected default threshold %d, got %d", DefaultLowRequestsThreshold, rlDefault.lowRequestsThreshold)
	}
	if rlDefault.sessionRemaining <= rlDefault.lowRequestsThreshold {
		t.Errorf("Expected initial sessionRemaining (%d) to be > lowRequestsThreshold (%d)", rlDefault.sessionRemaining, rlDefault.lowRequestsThreshold)
	}

	customThreshold := 10
	rlCustom := NewRateLimiter(customThreshold)
	if rlCustom.lowRequestsThreshold != customThreshold {
		t.Errorf("Expected custom threshold %d, got %d", customThreshold, rlCustom.lowRequestsThreshold)
	}
}

func TestUpdateLimits(t *testing.T) {
	rl := NewRateLimiter(5)
	headers := http.Header{}

	// Test with valid headers
	headers.Set(XRateLimitSessionRemaining, "100")
	headers.Set(XRateLimitSessionReset, "30.5") // 30.5 seconds

	rl.UpdateLimits(headers)

	rl.mutex.Lock()
	if rl.sessionRemaining != 100 {
		t.Errorf("Expected sessionRemaining 100, got %d", rl.sessionRemaining)
	}
	expectedResetTimeRough := time.Now().Add(30*time.Second + 500*time.Millisecond)
	if rl.sessionResetTime.Before(time.Now().Add(30*time.Second)) || rl.sessionResetTime.After(expectedResetTimeRough.Add(time.Second)) {
		t.Errorf("sessionResetTime not updated correctly. Expected around %v, got %v", expectedResetTimeRough, rl.sessionResetTime)
	}
	rl.mutex.Unlock()

	initialRemainingAfterValidUpdate := 100 // Store it before it's potentially changed by invalid updates
	initialResetTimeAfterValidUpdate := rl.sessionResetTime // Store it

	headers.Set(XRateLimitSessionRemaining, "not-an-int")
	headers.Set(XRateLimitSessionReset, "not-a-float")
	rl.UpdateLimits(headers)

	rl.mutex.Lock()
	if rl.sessionRemaining != initialRemainingAfterValidUpdate {
		t.Errorf("sessionRemaining changed with invalid header, expected %d, got %d", initialRemainingAfterValidUpdate, rl.sessionRemaining)
	}
	if !rl.sessionResetTime.Equal(initialResetTimeAfterValidUpdate) {
		if rl.sessionResetTime.Sub(initialResetTimeAfterValidUpdate).Abs() > time.Millisecond*100 {
			t.Errorf("sessionResetTime changed with invalid header, expected %v, got %v", initialResetTimeAfterValidUpdate, rl.sessionResetTime)
		}
	}
	rl.mutex.Unlock()

	headers = http.Header{}
	rl.UpdateLimits(headers)
	rl.mutex.Lock()
	if rl.sessionRemaining != initialRemainingAfterValidUpdate {
		t.Errorf("sessionRemaining changed with missing header, expected %d, got %d", initialRemainingAfterValidUpdate, rl.sessionRemaining)
	}
	if !rl.sessionResetTime.Equal(initialResetTimeAfterValidUpdate) {
		if rl.sessionResetTime.Sub(initialResetTimeAfterValidUpdate).Abs() > time.Millisecond*100 {
			t.Errorf("sessionResetTime changed with missing header, expected %v, got %v", initialResetTimeAfterValidUpdate, rl.sessionResetTime)
		}
	}
	rl.mutex.Unlock()
}

func TestWaitIfNeeded_NoWait(t *testing.T) {
	rl := NewRateLimiter(5)
	rl.sessionRemaining = 10
	rl.sessionResetTime = time.Now().Add(60 * time.Second)

	startTime := time.Now()
	rl.WaitIfNeeded()
	duration := time.Since(startTime)

	if duration > 50*time.Millisecond {
		t.Errorf("WaitIfNeeded waited for %v, expected no significant wait", duration)
	}
}

func TestWaitIfNeeded_WaitWhenZeroRemaining(t *testing.T) {
	rl := NewRateLimiter(5)
	rl.mutex.Lock()
	rl.sessionRemaining = 0
	resetDuration := 100 * time.Millisecond
	rl.sessionResetTime = time.Now().Add(resetDuration)
	rl.mutex.Unlock()

	startTime := time.Now()
	rl.WaitIfNeeded()
	duration := time.Since(startTime)

	minExpectedWait := resetDuration
	maxExpectedWait := resetDuration + 1*time.Second + 200*time.Millisecond

	if duration < minExpectedWait {
		t.Errorf("WaitIfNeeded waited for %v, expected at least %v", duration, minExpectedWait)
	}
	if duration > maxExpectedWait {
		t.Errorf("WaitIfNeeded waited for %v, which is more than expected max %v", duration, maxExpectedWait)
	}
}

func TestWaitIfNeeded_LowRequestsProportionalWait(t *testing.T) {
	rl := NewRateLimiter(10)
	rl.mutex.Lock()
	rl.sessionRemaining = 3
	resetDuration := 2 * time.Second
	rl.sessionResetTime = time.Now().Add(resetDuration)
	rl.mutex.Unlock()

	startTime := time.Now()
	rl.WaitIfNeeded()
	duration := time.Since(startTime)

	minExpectedWait := resetDuration + 400*time.Millisecond
	maxExpectedWait := resetDuration + 700*time.Millisecond

	if duration < minExpectedWait {
		t.Errorf("WaitIfNeeded (low, reset soon) waited for %v, expected at least %v", duration, minExpectedWait)
	}
	if duration > maxExpectedWait {
		t.Errorf("WaitIfNeeded (low, reset soon) waited for %v, which is more than expected max %v", duration, maxExpectedWait)
	}

	rl.mutex.Lock()
	rl.sessionRemaining = 3
	rl.sessionResetTime = time.Now().Add(60 * time.Second)
	rl.mutex.Unlock()

	startTimeFarReset := time.Now()
	rl.WaitIfNeeded()
	durationFarReset := time.Since(startTimeFarReset)

	if durationFarReset > 100*time.Millisecond {
		t.Errorf("WaitIfNeeded (low, reset far) waited for %v, expected minimal wait", durationFarReset)
	}
}
