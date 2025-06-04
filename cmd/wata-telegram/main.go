package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log" // Using standard log before custom logger is set up
	"os"
	"os/signal"
	"syscall"
	"time"

	"pymath/go_src/configuration"
	"pymath/go_src/logging_helper"
	"pymath/go_src/mq_telegram"

	amqp "github.com/rabbitmq/amqp091-go"
	"github.com/sirupsen/logrus" // For using logrus features after setup
)

const (
	appName           = "wata-telegram"
	telegramQueueName = "telegram_channel" // Must match publisher and Python version
	configPathEnvVar  = "WATA_CONFIG_PATH"
	defaultConfigPath = "./config/config.json" // Default if env var is not set
)

// MessagePayload is the expected structure of messages from the RabbitMQ queue.
type MessagePayload struct {
	Message string `json:"message"`
}

func main() {
	// Initial bootstrap logging (before full logger setup)
	log.Printf("Starting %s application...", appName)

	// --- Configuration ---
	configPath := os.Getenv(configPathEnvVar)
	if configPath == "" {
		log.Printf("Environment variable %s not set, using default config path: %s", configPathEnvVar, defaultConfigPath)
		configPath = defaultConfigPath
	} else {
		log.Printf("Loading configuration from: %s (via %s)", configPath, configPathEnvVar)
	}

	cfg, err := configuration.LoadConfig(configPath)
	if err != nil {
		log.Fatalf("Failed to load configuration from %s: %v", configPath, err)
	}
	log.Println("Configuration loaded successfully.")

	// --- Logging Setup ---
	if err := logging_helper.SetupLogging(cfg, appName); err != nil {
		log.Fatalf("Failed to setup logging: %v", err)
		// Note: If SetupLogging fails, further logrus calls might not work as expected.
	}
	// From now on, use logrus for logging
	logrus.Info("Logging has been initialized.")

	// --- Retrieve Necessary Config Values ---
	// RabbitMQ
	mqHost := cfg.RabbitMQ.Host
	mqPort := cfg.RabbitMQ.Port
	mqUser := cfg.RabbitMQ.Username
	mqPass := cfg.RabbitMQ.Password // Be careful with logging passwords
	mqVHost := cfg.RabbitMQ.VirtualHost
	if mqVHost == "" {
		mqVHost = "/" // Default virtual host
	}
	// Telegram (assuming it's in a custom section or global_settings, adjust as needed)
	// For this example, let's assume they are in GlobalSettings for simplicity.
	// This needs to match the actual structure of your config.json and configuration.Config Go struct.
	telegramToken := cfg.GlobalSettings.Version // Placeholder: Replace with actual config field
	telegramChatID := cfg.GlobalSettings.AppName // Placeholder: Replace with actual config field

	// A more realistic config structure might be:
	// cfg.APIServices.Telegram.Token, cfg.APIServices.Telegram.ChatID
	// Or a dedicated "telegram" section.
	// The Python code uses: config.get_specific_config("telegram_bot") -> "token" and "chat_id"
	// This implies a structure like: "telegram_bot": {"token": "...", "chat_id": "..."}
	// Let's try to fetch it that way using GetConfigValue, assuming it's stored under "telegram_bot.token" etc.

	tokenVal, err := cfg.GetConfigValue("telegram_bot.token")
	if err != nil {
		logrus.Fatalf("Failed to get telegram_bot.token from config: %v", err)
	}
	telegramToken, ok := tokenVal.(string)
	if !ok || telegramToken == "" {
		logrus.Fatalf("telegram_bot.token is missing or not a string in config")
	}

	chatIDVal, err := cfg.GetConfigValue("telegram_bot.chat_id")
	if err != nil {
		logrus.Fatalf("Failed to get telegram_bot.chat_id from config: %v", err)
	}
	telegramChatID, ok = chatIDVal.(string)
	if !ok || telegramChatID == "" {
		logrus.Fatalf("telegram_bot.chat_id is missing or not a string in config")
	}
	logrus.Info("Telegram configuration retrieved.")


	// --- RabbitMQ Connection ---
	connURL := fmt.Sprintf("amqp://%s:%s@%s:%d%s", mqUser, mqPass, mqHost, mqPort, mqVHost)
	// logrus.Debugf("Attempting to connect to RabbitMQ at %s:%d%s", mqHost, mqPort, mqVHost) // Avoid logging full URL with creds

	var conn *amqp.Connection
	var connErr error
	// Retry mechanism for RabbitMQ connection
	for i := 0; i < 5; i++ {
		conn, connErr = amqp.Dial(connURL)
		if connErr == nil {
			logrus.Info("Successfully connected to RabbitMQ.")
			break
		}
		logrus.Warnf("Failed to connect to RabbitMQ (attempt %d/5): %v. Retrying in 5 seconds...", i+1, connErr)
		time.Sleep(5 * time.Second)
	}
	if connErr != nil {
		logrus.Fatalf("Failed to connect to RabbitMQ after multiple retries: %v", connErr)
	}
	defer conn.Close()

	ch, err := conn.Channel()
	if err != nil {
		logrus.Fatalf("Failed to open a RabbitMQ channel: %v", err)
	}
	defer ch.Close()
	logrus.Info("RabbitMQ channel opened.")

	// Declare the queue (should be idempotent)
	_, err = ch.QueueDeclare(
		telegramQueueName, // name
		true,              // durable
		false,             // delete when unused
		false,             // exclusive
		false,             // no-wait
		nil,               // arguments
	)
	if err != nil {
		logrus.Fatalf("Failed to declare RabbitMQ queue '%s': %v", telegramQueueName, err)
	}
	logrus.Infof("RabbitMQ queue '%s' declared/ensured.", telegramQueueName)

	// --- Consumer Logic ---
	// Prefetch count (optional, but good for controlling message flow)
	// err = ch.Qos(1, 0, false) // Process one message at a time
	// if err != nil {
	// 	logrus.Fatalf("Failed to set QoS: %v", err)
	// }

	msgs, err := ch.Consume(
		telegramQueueName, // queue
		appName,           // consumer tag
		false,             // auto-ack (set to false for manual acknowledgment)
		false,             // exclusive
		false,             // no-local (not relevant for default exchange)
		false,             // no-wait
		nil,               // args
	)
	if err != nil {
		logrus.Fatalf("Failed to register a consumer: %v", err)
	}
	logrus.Info("Consumer registered. Waiting for messages...")

	// --- Graceful Shutdown Setup ---
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// --- Message Processing Loop ---
forever:
	for {
		select {
		case <-ctx.Done(): // Shutdown signal received
			logrus.Info("Shutdown signal received. Exiting consumer loop.")
			break forever
		case d, ok := <-msgs:
			if !ok {
				logrus.Error("Message channel closed by RabbitMQ. Attempting to reconnect or shutdown...")
				// Handle reconnection logic or exit. For now, we'll exit.
				// This can happen if the connection drops.
				// A robust service would have a reconnection loop for `conn` and `ch` as well.
				break forever
			}

			logrus.Infof("Received a message: CorrelationID=%s, MessageID=%s", d.CorrelationId, d.MessageId)
			// logrus.Debugf("Raw message body: %s", string(d.Body))

			var payload MessagePayload
			err := json.Unmarshal(d.Body, &payload)
			if err != nil {
				logrus.Errorf("Failed to unmarshal message body: %v. Body: %s", err, string(d.Body))
				// Decide how to handle unparseable messages: Nack without requeue, or move to dead-letter queue.
				if nackErr := d.Nack(false, false); nackErr != nil { // false for multiple, false for requeue
					logrus.Errorf("Failed to Nack unparseable message: %v", nackErr)
				}
				continue // Move to next message
			}

			if payload.Message == "" {
				logrus.Warn("Received message with empty 'message' field. Acknowledging and skipping.")
				if ackErr := d.Ack(false); ackErr != nil {
					logrus.Errorf("Failed to Ack message with empty content: %v", ackErr)
				}
				continue
			}

			logrus.Infof("Sending to Telegram: ChatID=%s, Message snippet: %.50s...", telegramChatID, payload.Message)
			err = mq_telegram.SendTelegramMessage(telegramToken, telegramChatID, payload.Message)
			if err != nil {
				logrus.Errorf("Failed to send message to Telegram: %v", err)
				// Consider Nack with requeue for transient errors, or Nack without requeue for persistent errors.
				// For simplicity here, we'll Nack and requeue to demonstrate.
				// Be cautious with auto-requeue as it can lead to message loops if an error is persistent.
				if nackErr := d.Nack(false, true); nackErr != nil { // false for multiple, true for requeue
					logrus.Errorf("Failed to Nack message after Telegram send error: %v", nackErr)
				}
			} else {
				logrus.Infof("Message successfully sent to Telegram. CorrelationID=%s", d.CorrelationId)
				if err := d.Ack(false); err != nil { // false for multiple
					logrus.Errorf("Failed to Ack message: %v", err)
					// This is problematic: message processed but couldn't Ack.
					// Might lead to reprocessing if connection drops before broker confirms Ack.
				}
			}
		}
	}

	logrus.Info("Wata-Telegram application shut down gracefully.")
}
