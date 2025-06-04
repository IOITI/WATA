package scheduler

import (
	// "encoding/json" // Removed as no longer used in tests after refactor
	"errors"
	// "context"
	"pymath/go_src/configuration"
	"sync"
	"testing"
	// "time"

	amqp "github.com/rabbitmq/amqp091-go" // Added back import
	// "github.com/stretchr/testify/assert"
)

// --- Mocking for SendMessageToTradingQueue (the package-level var) ---
var (
	mockMu             sync.Mutex
	mockSentMessages   []map[string]interface{}
	mockSendMessageErr error
)

func mockSendMessage(cfg *configuration.Config, message map[string]interface{}) error {
	mockMu.Lock()
	defer mockMu.Unlock()
	if mockSendMessageErr != nil {
		return mockSendMessageErr
	}
	mockSentMessages = append(mockSentMessages, message)
	return nil
}

func setupJobTest() func() {
	originalSendFunc := SendMessageToTradingQueue
	SendMessageToTradingQueue = mockSendMessage

	mockMu.Lock()
	mockSentMessages = []map[string]interface{}{}
	mockSendMessageErr = nil
	mockMu.Unlock()

	return func() {
		SendMessageToTradingQueue = originalSendFunc
	}
}

func getTestConfig() *configuration.Config {
	return &configuration.Config{}
}

func TestJobCheckPositions(t *testing.T) {
	cleanup := setupJobTest()
	defer cleanup()

	cfg := getTestConfig()
	JobCheckPositions(cfg)

	mockMu.Lock()
	defer mockMu.Unlock()

	if len(mockSentMessages) != 1 {
		t.Fatalf("Expected 1 message to be sent, got %d", len(mockSentMessages))
	}
	msg := mockSentMessages[0]
	if msg["message_type"] != msgTypeCheckPositions {
		t.Errorf("Expected message_type '%s', got '%s'", msgTypeCheckPositions, msg["message_type"])
	}
	if _, ok := msg["timestamp"].(string); !ok {
		t.Error("Timestamp not found or not a string")
	}
}

func TestJobDailyStats(t *testing.T) {
	cleanup := setupJobTest()
	defer cleanup()

	cfg := getTestConfig()
	JobDailyStats(cfg)

	mockMu.Lock()
	defer mockMu.Unlock()

	if len(mockSentMessages) != 1 {
		t.Fatalf("Expected 1 message to be sent, got %d", len(mockSentMessages))
	}
	msg := mockSentMessages[0]
	if msg["message_type"] != msgTypeDailyStats {
		t.Errorf("Expected message_type '%s', got '%s'", msgTypeDailyStats, msg["message_type"])
	}
	if _, ok := msg["timestamp"].(string); !ok {
		t.Error("Timestamp not found or not a string")
	}
}

func TestJobClosePositions(t *testing.T) {
	cleanup := setupJobTest()
	defer cleanup()

	cfg := getTestConfig()
	closeTime := "17:00"
	JobClosePositions(cfg, closeTime)

	mockMu.Lock()
	defer mockMu.Unlock()

	if len(mockSentMessages) != 1 {
		t.Fatalf("Expected 1 message to be sent, got %d", len(mockSentMessages))
	}
	msg := mockSentMessages[0]
	if msg["message_type"] != msgTypeClosePosition {
		t.Errorf("Expected message_type '%s', got '%s'", msgTypeClosePosition, msg["message_type"])
	}
	if _, ok := msg["timestamp"].(string); !ok {
		t.Error("Timestamp not found or not a string")
	}
}

func TestSendMessageToTradingQueue_Error(t *testing.T) {
	cleanup := setupJobTest()
	defer cleanup()

	cfg := getTestConfig()
	expectedErr := errors.New("MQ publish error")

	mockMu.Lock()
	mockSendMessageErr = expectedErr
	mockMu.Unlock()

	err := SendMessageToTradingQueue(cfg, map[string]interface{}{"test": "data"})
	if err == nil {
		t.Fatal("Expected an error from SendMessageToTradingQueue, got nil")
	}
	if !errors.Is(err, expectedErr) {
		t.Errorf("Expected error '%v', got '%v'", expectedErr, err)
	}
}

func TestSendMessageToTradingQueue_WithMockedAmqp(t *testing.T) {
	// This test verifies the internal logic of `sendMessageToTradingQueueLogic`
	// by mocking its direct dependencies: `connectToRabbitMQFunc` and `publishMessageFunc`.

	originalConnect := connectToRabbitMQFunc
	originalPublish := publishMessageFunc
	defer func() {
		connectToRabbitMQFunc = originalConnect
		publishMessageFunc = originalPublish
	}()

	var connectCalled bool
	mockConnectErr := errors.New("mock connect error")

	connectToRabbitMQFunc = func(cfg *configuration.Config) (*amqp.Connection, *amqp.Channel, error) {
		connectCalled = true
		return nil, nil, mockConnectErr // Return an error to prevent Close() calls on nil/dummy objects
	}
	// publishMessageFunc will not be called in this path.

	cfg := getTestConfig()
	testMsgContent := map[string]interface{}{"data": "my test data"}
	err := sendMessageToTradingQueueLogic(cfg, testMsgContent)

	if !errors.Is(err, mockConnectErr) {
		t.Fatalf("sendMessageToTradingQueueLogic did not return the expected mockConnectErr. Got: %v", err)
	}

	if !connectCalled {
		t.Error("connectToRabbitMQFunc was not called")
	}
	// Cannot assert publishMessageFunc was called as we are erroring out before it.
}
