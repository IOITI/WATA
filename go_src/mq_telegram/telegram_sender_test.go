package mq_telegram

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSendTelegramMessage_Success(t *testing.T) {
	mockedToken := "test_token"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			t.Errorf("Expected POST request, got %s", r.Method)
		}
		expectedPath := "/" + mockedToken + "/sendMessage"
		if r.URL.Path != expectedPath {
			t.Errorf("Expected URL path '%s', got '%s'", expectedPath, r.URL.Path)
		}
		if r.Header.Get("Content-Type") != "application/json" {
			t.Errorf("Expected Content-Type application/json, got %s", r.Header.Get("Content-Type"))
		}

		bodyBytes, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("Failed to read request body: %v", err)
		}
		defer r.Body.Close()

		var reqPayload TelegramRequest
		if err := json.Unmarshal(bodyBytes, &reqPayload); err != nil {
			t.Fatalf("Failed to unmarshal request payload: %v", err)
		}

		if reqPayload.ChatID != "test_chat_id" {
			t.Errorf("Expected chat_id 'test_chat_id', got '%s'", reqPayload.ChatID)
		}
		if reqPayload.Text != "Hello, Telegram!" {
			t.Errorf("Expected text 'Hello, Telegram!', got '%s'", reqPayload.Text)
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		fmt.Fprintln(w, `{"ok": true, "result": {"message_id": 123}}`)
	}))
	defer server.Close()

	originalBaseURL := telegramAPIBaseURL
	telegramAPIBaseURL = server.URL + "/" // Assign to package-level var
	defer func() { telegramAPIBaseURL = originalBaseURL }()

	err := SendTelegramMessage(mockedToken, "test_chat_id", "Hello, Telegram!")
	if err != nil {
		t.Errorf("SendTelegramMessage failed: %v", err)
	}
}

func TestSendTelegramMessage_APIFailure_OkFalse(t *testing.T) {
	mockedToken := "test_token_fail"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		expectedPath := "/" + mockedToken + "/sendMessage"
		if r.URL.Path != expectedPath {
			t.Errorf("Expected URL path '%s', got '%s'", expectedPath, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		fmt.Fprintln(w, `{"ok": false, "description": "Chat not found"}`)
	}))
	defer server.Close()

	originalBaseURL := telegramAPIBaseURL
	telegramAPIBaseURL = server.URL + "/" // Assign to package-level var
	defer func() { telegramAPIBaseURL = originalBaseURL }()

	err := SendTelegramMessage(mockedToken, "test_chat_id", "message")
	if err == nil {
		t.Fatal("SendTelegramMessage should have failed due to API error (ok: false)")
	}
	if !strings.Contains(err.Error(), "Chat not found") {
		t.Errorf("Expected error message to contain 'Chat not found', got: %v", err)
	}
}

func TestSendTelegramMessage_HTTPError(t *testing.T) {
	mockedToken := "invalid_token"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		expectedPath := "/" + mockedToken + "/sendMessage"
		if r.URL.Path != expectedPath {
			t.Errorf("Expected URL path '%s', got '%s'", expectedPath, r.URL.Path)
		}
		w.WriteHeader(http.StatusForbidden)
		fmt.Fprintln(w, `{"ok": false, "description": "Forbidden: bot token invalid", "error_code": 403}`)
	}))
	defer server.Close()

	originalBaseURL := telegramAPIBaseURL
	telegramAPIBaseURL = server.URL + "/" // Assign to package-level var
	defer func() { telegramAPIBaseURL = originalBaseURL }()

	err := SendTelegramMessage(mockedToken, "test_chat_id", "message")
	if err == nil {
		t.Fatal("SendTelegramMessage should have failed due to HTTP error")
	}
	if !strings.Contains(err.Error(), "telegram API error (HTTP Status 403 Forbidden): Forbidden: bot token invalid") {
		t.Errorf("Expected error message for HTTP 403 with specific description, got: %v", err)
	}
}

func TestSendTelegramMessage_EmptyToken(t *testing.T) {
	err := SendTelegramMessage("", "chat_id", "message")
	if err == nil {
		t.Fatal("Expected error for empty token")
	}
	if !strings.Contains(err.Error(), "telegram bot token cannot be empty") {
		t.Errorf("Unexpected error message for empty token: %v", err)
	}
}

func TestSendTelegramMessage_EmptyChatID(t *testing.T) {
	err := SendTelegramMessage("token", "", "message")
	if err == nil {
		t.Fatal("Expected error for empty chat_id")
	}
	if !strings.Contains(err.Error(), "telegram chat ID cannot be empty") {
		t.Errorf("Unexpected error message for empty chat_id: %v", err)
	}
}

func TestSendTelegramMessage_MalformedResponse(t *testing.T) {
	mockedToken := "test_token_malformed"
	serverWithMalformedJSON := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		fmt.Fprintln(w, `{"ok": tru`) // Malformed JSON
	}))
	defer serverWithMalformedJSON.Close()

	originalBaseURL := telegramAPIBaseURL
	telegramAPIBaseURL = serverWithMalformedJSON.URL + "/" // Assign to package-level var
	defer func() { telegramAPIBaseURL = originalBaseURL }()

	err := SendTelegramMessage(mockedToken, "test_chat_id", "message")
	if err == nil {
		t.Fatal("Expected error due to malformed JSON response")
	}
	if !strings.Contains(err.Error(), "failed to decode telegram API response") {
		t.Errorf("Expected error about decoding failure, got: %v", err)
	}
}

func TestSendTelegramMessage_ClientDoError(t *testing.T) {
	originalBaseURL := telegramAPIBaseURL
	telegramAPIBaseURL = "http://nonexistentdomain123abc:1234/" // Assign to package-level var
	defer func() { telegramAPIBaseURL = originalBaseURL }()

	err := SendTelegramMessage("test_token", "test_chat_id", "message")
	if err == nil {
		t.Fatal("Expected error due to client.Do failure")
	}
	if !strings.Contains(err.Error(), "failed to send HTTP request") {
		t.Errorf("Expected error about HTTP request sending failure, got: %v", err)
	}
}
