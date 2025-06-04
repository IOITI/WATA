package mq_telegram

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

const telegramQueueName = "telegram_channel" // As defined in Python code

// SendMessageToMQForTelegram sends a message (intended for Telegram) to a RabbitMQ queue.
// It opens a new channel on the provided connection and closes it before returning.
// The connection itself is not closed by this function.
func SendMessageToMQForTelegram(conn *amqp.Connection, messageTelegram string) error {
	if conn == nil {
		return fmt.Errorf("rabbitmq connection cannot be nil")
	}
	if conn.IsClosed() {
		return fmt.Errorf("rabbitmq connection is closed")
	}

	// Open a new channel
	ch, err := conn.Channel()
	if err != nil {
		return fmt.Errorf("failed to open a RabbitMQ channel: %w", err)
	}
	defer ch.Close() // Ensure channel is closed on function exit

	// Declare the queue (idempotent operation)
	// Arguments match Python's pika: durable=True
	_, err = ch.QueueDeclare(
		telegramQueueName, // name
		true,              // durable
		false,             // delete when unused
		false,             // exclusive
		false,             // no-wait
		nil,               // arguments
	)
	if err != nil {
		return fmt.Errorf("failed to declare RabbitMQ queue '%s': %w", telegramQueueName, err)
	}

	// Prepare the message payload for RabbitMQ
	// Python code sends: json.dumps({"message": message_telegram})
	mqPayload := map[string]string{"message": messageTelegram}
	jsonBody, err := json.Marshal(mqPayload)
	if err != nil {
		return fmt.Errorf("failed to marshal message to JSON for RabbitMQ: %w", err)
	}

	// Publish the message
	// Python code uses default exchange and routing_key=queue_name
	// Properties: delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE (2)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second) // Add a timeout for publishing
	defer cancel()

	err = ch.PublishWithContext(ctx,
		"",                // exchange (default)
		telegramQueueName, // routing key (queue name)
		false,             // mandatory
		false,             // immediate
		amqp.Publishing{
			ContentType:  "application/json",
			Body:         jsonBody,
			DeliveryMode: amqp.Persistent, // Corresponds to pika.spec.PERSISTENT_DELIVERY_MODE
		},
	)
	if err != nil {
		return fmt.Errorf("failed to publish message to RabbitMQ queue '%s': %w", telegramQueueName, err)
	}

	return nil
}
