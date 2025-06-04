package scheduler

import (
	"context"
	"encoding/json"
	"fmt"
	"pymath/go_src/configuration" // Assuming this path is correct
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
	"github.com/sirupsen/logrus"
)

const (
	tradingActionQueue = "trading-action" // As per Python code
	// Message types, matching Python
	msgTypeCheckPositions    = "check_positions_on_saxo_api"
	msgTypeDailyStats        = "daily_stats"
	msgTypeClosePosition     = "close-position" // Python uses "close-position"
	msgTypeRepeatLastAction  = "try_repeat_last_action_at_the_open" // If implemented
)

var (
	// Allow overriding for tests
	connectToRabbitMQFunc = connectToRabbitMQ
	publishMessageFunc    = publishMessage
)


// connectToRabbitMQ establishes a new connection and channel.
// It's the responsibility of the caller to close them.
func connectToRabbitMQ(cfg *configuration.Config) (*amqp.Connection, *amqp.Channel, error) {
	if cfg == nil {
		return nil, nil, fmt.Errorf("configuration is nil")
	}

	mqHost := cfg.RabbitMQ.Host
	mqPort := cfg.RabbitMQ.Port
	mqUser := cfg.RabbitMQ.Username
	mqPass := cfg.RabbitMQ.Password
	mqVHost := cfg.RabbitMQ.VirtualHost
	if mqVHost == "" {
		mqVHost = "/"
	}
	connURL := fmt.Sprintf("amqp://%s:%s@%s:%d%s", mqUser, mqPass, mqHost, mqPort, mqVHost)

	conn, err := amqp.Dial(connURL)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to connect to RabbitMQ: %w", err)
	}

	ch, err := conn.Channel()
	if err != nil {
		conn.Close() // Close connection if channel opening fails
		return nil, nil, fmt.Errorf("failed to open a RabbitMQ channel: %w", err)
	}
	return conn, ch, nil
}

// publishMessage declares a queue and publishes a message.
func publishMessage(ch *amqp.Channel, queueName string, body []byte) error {
	_, err := ch.QueueDeclare(
		queueName,
		true,  // durable
		false, // delete when unused
		false, // exclusive
		false, // no-wait
		nil,   // arguments
	)
	if err != nil {
		return fmt.Errorf("failed to declare queue '%s': %w", queueName, err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	err = ch.PublishWithContext(ctx,
		"",        // exchange (default)
		queueName, // routing key (queue name)
		false,     // mandatory
		false,     // immediate
		amqp.Publishing{
			ContentType:  "application/json",
			DeliveryMode: amqp.Persistent,
			Body:         body,
		},
	)
	if err != nil {
		return fmt.Errorf("failed to publish message to queue '%s': %w", queueName, err)
	}
	return nil
}


// sendMessageToTradingQueueLogic is the actual implementation.
func sendMessageToTradingQueueLogic(cfg *configuration.Config, message map[string]interface{}) error {
	conn, ch, err := connectToRabbitMQFunc(cfg) // Use the mockable function
	if err != nil {
		return err // Error already contains context
	}
	defer conn.Close() // Ensure connection is closed
	defer ch.Close()   // Ensure channel is closed

	jsonBody, err := json.Marshal(message)
	if err != nil {
		return fmt.Errorf("failed to marshal message to JSON: %w", err)
	}

	logrus.Debugf("Publishing message to queue '%s': %s", tradingActionQueue, string(jsonBody))
	return publishMessageFunc(ch, tradingActionQueue, jsonBody) // Use the mockable function
}

// SendMessageToTradingQueue is a package-level variable for easy mocking in tests.
var SendMessageToTradingQueue = sendMessageToTradingQueueLogic


// --- Job Functions ---

// JobCheckPositions sends a message to check positions.
func JobCheckPositions(cfg *configuration.Config) {
	logrus.Info("Scheduler: Running JobCheckPositions")
	message := map[string]interface{}{
		"message_type": msgTypeCheckPositions,
		"timestamp":    time.Now().UTC().Format(time.RFC3339),
	}
	if err := SendMessageToTradingQueue(cfg, message); err != nil {
		logrus.Errorf("JobCheckPositions: Failed to send message to MQ: %v", err)
	} else {
		logrus.Infof("JobCheckPositions: Message '%s' sent successfully.", msgTypeCheckPositions)
	}
}

// JobDailyStats sends a message to trigger daily statistics generation.
func JobDailyStats(cfg *configuration.Config) {
	logrus.Info("Scheduler: Running JobDailyStats")
	message := map[string]interface{}{
		"message_type": msgTypeDailyStats,
		"timestamp":    time.Now().UTC().Format(time.RFC3339),
	}
	if err := SendMessageToTradingQueue(cfg, message); err != nil {
		logrus.Errorf("JobDailyStats: Failed to send message to MQ: %v", err)
	} else {
		logrus.Infof("JobDailyStats: Message '%s' sent successfully.", msgTypeDailyStats)
	}
}

// JobClosePositions sends a message to trigger closing of positions.
// closeTimeStr is not directly used in the message but triggers the job at that time.
func JobClosePositions(cfg *configuration.Config, closeTimeStr string /* unused in message itself */) {
	logrus.Infof("Scheduler: Running JobClosePositions (triggered for close time: %s)", closeTimeStr)
	message := map[string]interface{}{
		"message_type": msgTypeClosePosition,
		"timestamp":    time.Now().UTC().Format(time.RFC3339),
		// "close_time": closeTimeStr, // Could be added if needed by consumer
	}
	if err := SendMessageToTradingQueue(cfg, message); err != nil {
		logrus.Errorf("JobClosePositions: Failed to send message to MQ: %v", err)
	} else {
		logrus.Infof("JobClosePositions: Message '%s' sent successfully.", msgTypeClosePosition)
	}
}

// JobTryRepeatLastAction (if implemented)
// func JobTryRepeatLastAction(cfg *configuration.Config) {
// 	logrus.Info("Scheduler: Running JobTryRepeatLastAction")
// 	message := map[string]interface{}{
// 		"message_type": msgTypeRepeatLastAction,
// 		"timestamp":    time.Now().UTC().Format(time.RFC3339),
// 	}
// 	if err := SendMessageToTradingQueue(cfg, message); err != nil {
// 		logrus.Errorf("JobTryRepeatLastAction: Failed to send message to MQ: %v", err)
// 	}
// }
