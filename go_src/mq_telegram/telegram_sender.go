package mq_telegram

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

var telegramAPIBaseURL = "https://api.telegram.org/bot/" // Added trailing slash

// TelegramRequest represents the payload for sending a message to Telegram.
type TelegramRequest struct {
	ChatID    string `json:"chat_id"`
	Text      string `json:"text"`
	ParseMode string `json:"parse_mode,omitempty"`
}

// TelegramResponse represents the structure of a response from the Telegram API.
type TelegramResponse struct {
	Ok          bool   `json:"ok"`
	Description string `json:"description,omitempty"`
}

// SendTelegramMessage sends a message to a specified Telegram chat using the bot API.
func SendTelegramMessage(token, chatID, message string) error {
	if token == "" {
		return fmt.Errorf("telegram bot token cannot be empty")
	}
	if chatID == "" {
		return fmt.Errorf("telegram chat ID cannot be empty")
	}

	// Now constructs as ".../bot/TOKEN/sendMessage"
	apiURL := fmt.Sprintf("%s%s/sendMessage", telegramAPIBaseURL, token)

	payload := TelegramRequest{
		ChatID: chatID,
		Text:   message,
	}

	jsonPayload, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("failed to marshal telegram message payload: %w", err)
	}

	req, err := http.NewRequest("POST", apiURL, bytes.NewBuffer(jsonPayload))
	if err != nil {
		return fmt.Errorf("failed to create new HTTP request for telegram: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("failed to send HTTP request to telegram API: %w", err)
	}
	defer resp.Body.Close()

	var telegramResp TelegramResponse
	if err := json.NewDecoder(resp.Body).Decode(&telegramResp); err != nil {
		return fmt.Errorf("failed to decode telegram API response (status %s): %w", resp.Status, err)
	}

	if !telegramResp.Ok {
		return fmt.Errorf("telegram API error (HTTP Status %s): %s",
			resp.Status, telegramResp.Description)
	}

	if resp.StatusCode >= 400 { // This check might be redundant if !Ok already covers it
		return fmt.Errorf("telegram API request failed with HTTP status %s. API Response (if any): '%s'",
			resp.Status, telegramResp.Description)
	}

	return nil
}
