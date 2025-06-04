package saxo_openapi

import (
	"context"
	"encoding/json"
	"fmt"
	"io" // Added io
	"net/http"
	"strings" // Added strings
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"github.com/sirupsen/logrus"
	// "pymath/go_src/saxo_authen" // Client already has this
)

const (
	// DefaultStreamingPath is the common path for Saxo's streaming connect endpoint.
	// e.g., /openapi/streamingws/connect or /openapi/streamingws/v1/connect
	// The Python client uses "/openapi" + service_path (e.g. /streamingws/connect)
	// Let's assume client.streamBaseURL already contains up to e.g. "wss://streaming.saxobank.com"
	// and the connect path needs to be appended.
	// Saxo docs: wss://streaming.saxobank.com/openapi/streamingws/connect?contextId=<contextId>
	// So, streamBaseURL should be "wss://streaming.saxobank.com" (from client.streamBaseURL)
	// and path "/openapi/streamingws/connect"
	defaultStreamingConnectPath = "/openapi/streamingws/connect"

	defaultHeartbeatInterval = 5 * time.Second
	writeWait                = 10 * time.Second // Time allowed to write a message to the peer.
	pongWait                 = 60 * time.Second // Time allowed to read the next pong message from the peer.
	// PingPeriod is (pongWait * 9) / 10 as per gorilla example.
	// MaxMessageSize is the max size for a message.
)

// ControlMessageType defines the type of control message for the WebSocket stream.
type ControlMessageType int

const (
	ControlTypeSubscribe ControlMessageType = iota
	ControlTypeUnsubscribe
	ControlTypeModify // Not fully implemented in this pass, placeholder
	ControlTypeUpdateToken // If Saxo supports token update over WS
)

// ControlMessage is used to send commands (subscribe, unsubscribe) to the manageConnection goroutine.
type ControlMessage struct {
	Type        ControlMessageType
	Data        interface{} // Can be SubscriptionData for subscribe, or referenceID (string) for unsubscribe
	ResponseChan chan error // Optional: for acknowledging control message processing
}

// SubscriptionData holds information about a single active subscription.
type SubscriptionData struct {
	ReferenceID  string                 `json:"ReferenceId"`
	ResourcePath string                 // The actual API path for the resource, e.g., "trade/v1/orders/subscriptions"
	Format       string                 `json:"Format,omitempty"` // e.g., "application/json"
	Arguments    map[string]interface{} `json:"Arguments"`        // Arguments for the subscription, e.g. {"AccountKey": "...", "Uics": "123,456"}
	RefreshRate  int                    `json:"RefreshRate,omitempty"`
	Tag          string                 `json:"Tag,omitempty"`

	// Internal state
	isActive          bool      // Whether this subscription is currently active on the stream
	lastUpdate        time.Time // When last data was received for this
	desiredSnapshot   interface{} // Placeholder if we want to request snapshot and store it
	currentSnapshot   interface{} // Placeholder
	activityTimeout   int       // From server, in seconds
	heartbeatInterval time.Duration // Derived from server inactivityTimeout / N
}


// WebSocketStream manages a Saxo OpenAPI WebSocket connection.
type WebSocketStream struct {
	ctx          context.Context    // For overall lifecycle management
	cancelFunc   context.CancelFunc // To stop all goroutines
	client       *Client            // Pointer to the main OpenAPI client for auth & config
	conn         *websocket.Conn
	wg           sync.WaitGroup     // To wait for goroutines to finish

	streamingURL string             // Full WSS URL
	authToken    string             // Current auth token for the stream
	contextID    string             // Streaming context ID from handshake/subscription

	subscriptions    map[string]*SubscriptionData // Keyed by ReferenceID
	activeSubLock    sync.RWMutex                 // For subscriptions map

	messageChannel   chan []byte        // Channel for broadcasting received data messages
	errorChannel     chan error         // Channel for broadcasting errors
	internalCtrlChan chan ControlMessage// For internal commands like "reset" (reconnect)
	userCtrlChan     chan ControlMessage// For user commands (subscribe, unsubscribe)

	lastHeartbeatSent time.Time
	lastMessageReceived time.Time
	heartbeatInterval time.Duration

	isActive   bool
	connectMux sync.Mutex // Ensures Connect is not called concurrently
	connMux    sync.Mutex // Protects conn field
}

// NewWebSocketStream creates a new WebSocketStream manager.
// ctx: Parent context for managing the lifecycle of the stream.
// client: The Saxo OpenAPI client (used for base URLs and authentication).
func NewWebSocketStream(ctx context.Context, client *Client) (*WebSocketStream, error) {
	if client == nil {
		return nil, fmt.Errorf("client cannot be nil")
	}

	derivedCtx, cancel := context.WithCancel(ctx)

	ws := &WebSocketStream{
		ctx:              derivedCtx,
		cancelFunc:       cancel,
		client:           client,
		subscriptions:    make(map[string]*SubscriptionData),
		messageChannel:   make(chan []byte, 100), // Buffered channel
		errorChannel:     make(chan error, 10),   // Buffered channel
		internalCtrlChan: make(chan ControlMessage, 10),
		userCtrlChan:     make(chan ControlMessage, 10),
		heartbeatInterval: defaultHeartbeatInterval, // Default, can be adjusted by server
	}
	return ws, nil
}

// Connect establishes the WebSocket connection and starts processing goroutines.
// contextID is required to initiate the streaming session.
func (ws *WebSocketStream) Connect(initialContextID string) error {
	ws.connectMux.Lock() // Ensure only one connect operation at a time
	defer ws.connectMux.Unlock()

	if ws.isActive {
		return fmt.Errorf("WebSocketStream is already connected or connecting")
	}
	if initialContextID == "" {
		return fmt.Errorf("initialContextID is required to connect to the stream")
	}
	ws.contextID = initialContextID

	var err error
	ws.authToken, err = ws.client.Authenticator.GetToken()
	if err != nil {
		return fmt.Errorf("failed to get authentication token for WebSocket: %w", err)
	}

	// Construct WebSocket URL
	// Base URL from client (e.g., "wss://streaming.saxobank.com")
	// Path e.g., "/openapi/streamingws/connect"
	// Query param: "?contextId=<contextId>"
	baseURL := ws.client.streamBaseURL // Should be like "wss://streaming.saxobank.com"
	if !strings.HasPrefix(baseURL, "ws://") && !strings.HasPrefix(baseURL, "wss://") {
		return fmt.Errorf("invalid streamBaseURL: must start with ws:// or wss://, got %s", baseURL)
	}

	// Ensure no double slashes if streamBaseURL has trailing and path has leading
	fullPath := strings.TrimRight(baseURL, "/") + defaultStreamingConnectPath
	ws.streamingURL = fmt.Sprintf("%s?contextId=%s", fullPath, ws.contextID)

	logrus.Infof("WebSocketStream: Connecting to %s", ws.streamingURL)

	// Setup headers, including Authorization
	headers := http.Header{}
	headers.Set("Authorization", "Bearer "+ws.authToken)
	headers.Set("User-Agent", fmt.Sprintf("GoSaxoOpenAPIClient/%s", ws.client.Environment)) // Example User-Agent

	// Dial the WebSocket connection
	// Use ws.ctx for the dialer so it respects overall lifecycle cancellation
	conn, resp, err := websocket.DefaultDialer.DialContext(ws.ctx, ws.streamingURL, headers)
	if err != nil {
		// errMsg := fmt.Sprintf("WebSocket dial error to %s: %v", ws.streamingURL, err) // Removed unused variable
		if resp != nil {
			bodyBytes, _ := io.ReadAll(resp.Body)
			bodyString := string(bodyBytes)
			resp.Body.Close()
			// Wrap the original dial error with more context
			return fmt.Errorf("WebSocket dial error to %s (HTTP Status: %s, Response: %s): %w", ws.streamingURL, resp.Status, bodyString, err)
		}
		// If no response, just return the original dial error with URL context
		return fmt.Errorf("WebSocket dial error to %s: %w", ws.streamingURL, err)
	}

	ws.connMux.Lock()
	ws.conn = conn
	ws.connMux.Unlock()

	ws.isActive = true
	ws.lastMessageReceived = time.Now() // Initialize for heartbeats

	// Start goroutines
	ws.wg.Add(2) // For readMessages and manageConnection
	go ws.readMessages()
	go ws.manageConnection()

	logrus.Info("WebSocketStream: Successfully connected and goroutines started.")
	return nil
}


// Further implementation will include:
// - readMessages() goroutine
// - manageConnection() goroutine (heartbeats, token refresh, control messages)
// - Public methods: Subscribe(), Unsubscribe(), Messages(), Errors(), Close()
// - JSON structures for control messages sent to Saxo

// readMessages continuously reads messages from the WebSocket connection.
// It sends data messages to messageChannel and errors to errorChannel.
// It's run as a goroutine.
func (ws *WebSocketStream) readMessages() {
	defer ws.wg.Done()
	defer logrus.Debug("WebSocketStream: readMessages goroutine stopped.")

	if ws.conn == nil {
		logrus.Error("WebSocketStream: readMessages called with nil connection.")
		return
	}

	// Set a read deadline that is periodically refreshed by pong messages or any message.
	// ws.conn.SetReadDeadline(time.Now().Add(pongWait)) // Initial deadline
	// ws.conn.SetPongHandler(func(string) error {
	// 	logrus.Debug("WebSocketStream: Pong received.")
	// 	ws.conn.SetReadDeadline(time.Now().Add(pongWait))
	// 	ws.activeSubLock.Lock() // Using activeSubLock for lastMessageReceived as it's related to connection health
	// 	ws.lastMessageReceived = time.Now()
	// 	ws.activeSubLock.Unlock()
	// 	return nil
	// })
	// Gorilla's default PongHandler updates read deadline if set via SetReadDeadline.
	// Alternatively, manage it manually based on lastMessageReceived in manageConnection.

	for {
		select {
		case <-ws.ctx.Done(): // Overall context cancelled
			logrus.Info("WebSocketStream: readMessages received done from context.")
			return
		default:
			ws.connMux.Lock()
			conn := ws.conn
			ws.connMux.Unlock()
			if conn == nil {
				logrus.Info("WebSocketStream: readMessages detected nil connection, exiting.")
				// Send a specific error or signal that connection is lost if not already handled
				// ws.errorChannel <- StreamTerminated // Or a more specific error
				return
			}

			// Set a read deadline for each ReadMessage call to ensure it doesn't block indefinitely
			// if the underlying connection is broken without sending a close frame.
			// This is an alternative to SetReadDeadline + PongHandler.
			conn.SetReadDeadline(time.Now().Add(pongWait)) // pongWait is a reasonable timeout for a message

			messageType, message, err := conn.ReadMessage()
			if err != nil {
				// Check if the error is due to context cancellation
				if ws.ctx.Err() != nil {
					logrus.Infof("WebSocketStream: readMessages context cancelled during ReadMessage: %v", ws.ctx.Err())
					return // Exit if context is done
				}

				// Check for "normal" close
				if websocket.IsCloseError(err, websocket.CloseNormalClosure, websocket.CloseGoingAway) ||
				   strings.Contains(err.Error(), "use of closed network connection") { // This can happen on Close()
					logrus.Infof("WebSocketStream: Connection closed normally: %v", err)
				} else {
					logrus.Errorf("WebSocketStream: Error reading message: %v", err)
					// Non-blocking send to errorChannel
					select {
					case ws.errorChannel <- fmt.Errorf("read error: %w", err):
					default:
						logrus.Warn("WebSocketStream: errorChannel full, discarding read error.")
					}
				}
				// Regardless of error type, if ReadMessage fails, we usually need to stop this goroutine
				// and signal that the connection is effectively lost.
				// The manageConnection goroutine can then attempt to reconnect.
				ws.internalCtrlChan <- ControlMessage{Type: ControlTypeUpdateToken, Data: "reconnect_due_to_read_error"} // Signal for potential reconnect
				return
			}

			ws.activeSubLock.Lock()
			ws.lastMessageReceived = time.Now()
			ws.activeSubLock.Unlock()

			switch messageType {
			case websocket.TextMessage:
				logrus.Debugf("WebSocketStream: Received Text Message: %s", string(message))
				// Non-blocking send to messageChannel
				select {
				case ws.messageChannel <- message:
				default:
					logrus.Warn("WebSocketStream: messageChannel full, discarding incoming text message.")
				}
			case websocket.BinaryMessage:
				logrus.Debugf("WebSocketStream: Received Binary Message (length %d), discarding.", len(message))
				// Handle binary messages if expected, otherwise discard or error
			case websocket.PingMessage:
				logrus.Debug("WebSocketStream: Received Ping, sending Pong.")
				ws.connMux.Lock()
				err = ws.conn.WriteMessage(websocket.PongMessage, []byte{})
				ws.connMux.Unlock()
				if err != nil {
					logrus.Errorf("WebSocketStream: Error sending Pong: %v", err)
					// This might also signal a need to reconnect
					ws.internalCtrlChan <- ControlMessage{Type: ControlTypeUpdateToken, Data: "reconnect_due_to_pong_error"}
					return
				}
			case websocket.PongMessage:
				logrus.Debug("WebSocketStream: Received Pong (handled by updating lastMessageReceived).")
				// Already handled by lastMessageReceived update after ReadMessage
			case websocket.CloseMessage:
				logrus.Info("WebSocketStream: Received Close frame. Shutting down readMessages.")
				// Already handled by IsCloseError check typically
				return // Exit goroutine
			default:
				logrus.Warnf("WebSocketStream: Received unknown message type: %d", messageType)
			}
		}
	}
}

// manageConnection handles heartbeats, token refreshes, and control messages.
// It's run as a goroutine.
func (ws *WebSocketStream) manageConnection() {
	defer ws.wg.Done()
	defer logrus.Debug("WebSocketStream: manageConnection goroutine stopped.")

	// Heartbeat ticker
	// PingPeriod should be less than pongWait. Gorilla example uses (pongWait * 9) / 10.
	// Let's use a simpler interval for sending pings if no messages are received.
	heartbeatTicker := time.NewTicker(ws.heartbeatInterval)
	defer heartbeatTicker.Stop()

	// Token refresh timer (placeholder, needs actual expiry from token)
	// This needs to be more sophisticated, based on actual token expiry time.
	// For now, a simple periodic check or a long timer.
	// A better way: calculate duration until (expiry - buffer) and set timer.
	tokenRefreshInterval := 20 * time.Minute // Example: refresh token every 20 mins
	tokenRefreshTimer := time.NewTimer(tokenRefreshInterval)
	defer tokenRefreshTimer.Stop()


	for {
		select {
		case <-ws.ctx.Done(): // Overall context cancelled
			logrus.Info("WebSocketStream: manageConnection received done from context.")
			ws.cleanupConnection() // Ensure connection is closed
			return

		case ctrlMsg := <-ws.userCtrlChan:
			logrus.Debugf("WebSocketStream: Received user control message: %v", ctrlMsg.Type)
			ws.handleUserControlMessage(ctrlMsg)

		case internalMsg := <-ws.internalCtrlChan:
			logrus.Debugf("WebSocketStream: Received internal control message: %v", internalMsg.Type)
			if internalMsg.Type == ControlTypeUpdateToken && internalMsg.Data == "reconnect_due_to_read_error" {
				logrus.Warn("WebSocketStream: manageConnection attempting to handle read error by reconnecting.")
				// ws.reconnect() // Implement reconnect logic
			}
			// Handle other internal messages if any

		case <-heartbeatTicker.C:
			ws.connMux.Lock() // Need to lock conn for WriteMessage and also for checking ws.conn != nil
			if ws.conn == nil {
				ws.connMux.Unlock()
				logrus.Debug("WebSocketStream: Heartbeat: connection is nil, skipping ping.")
				continue
			}

			// Check if connection is stale
			ws.activeSubLock.RLock() // Lock for reading lastMessageReceived
			timeSinceLastMessage := time.Since(ws.lastMessageReceived)
			ws.activeSubLock.RUnlock()

			if timeSinceLastMessage > pongWait { // pongWait is the max time to wait for a pong or any message
				logrus.Warnf("WebSocketStream: Stale connection detected (no message for %v). Closing and attempting reconnect.", timeSinceLastMessage)
				ws.conn.Close() // This will cause readMessages to exit
				ws.connMux.Unlock() // Unlock before sending to internalCtrlChan to avoid deadlock if channel is full
				ws.internalCtrlChan <- ControlMessage{Type: ControlTypeUpdateToken, Data: "reconnect_due_to_stale"}
				continue // Loop will eventually exit or reconnect logic will take over
			}

			logrus.Debug("WebSocketStream: Sending Ping for heartbeat.")
			// Set a write deadline for the ping message
			ws.conn.SetWriteDeadline(time.Now().Add(writeWait))
			err := ws.conn.WriteMessage(websocket.PingMessage, []byte{})
			ws.connMux.Unlock() // Unlock after WriteMessage
			if err != nil {
				logrus.Errorf("WebSocketStream: Error sending Ping: %v", err)
				// This might signal a need to reconnect
				ws.internalCtrlChan <- ControlMessage{Type: ControlTypeUpdateToken, Data: "reconnect_due_to_ping_error"}
				// No return here, let the loop continue and potentially hit stale connection check
			} else {
				ws.activeSubLock.Lock()
				ws.lastHeartbeatSent = time.Now()
				ws.activeSubLock.Unlock()
			}

		case <-tokenRefreshTimer.C:
			logrus.Info("WebSocketStream: Attempting scheduled token refresh.")
			newToken, err := ws.client.Authenticator.GetToken() // GetToken should handle refresh if needed
			if err != nil {
				logrus.Errorf("WebSocketStream: Failed to refresh token: %v", err)
				// Optionally, signal error or attempt reconnect if token is critical
				// For now, just schedule next attempt
			} else {
				ws.authToken = newToken
				logrus.Info("WebSocketStream: Auth token refreshed successfully.")
				// If Saxo requires sending the new token over WebSocket:
				// ws.sendTokenUpdateControlMessage(newToken)
			}
			tokenRefreshTimer.Reset(tokenRefreshInterval) // Schedule next refresh
		}
	}
}

// handleUserControlMessage processes messages from the userCtrlChan.
func (ws *WebSocketStream) handleUserControlMessage(msg ControlMessage) {
	switch msg.Type {
	case ControlTypeSubscribe:
		if subData, ok := msg.Data.(SubscriptionData); ok {
			logrus.Infof("WebSocketStream: Processing subscribe request for RefID: %s, Path: %s", subData.ReferenceID, subData.ResourcePath)
			ws.sendSubscriptionControlMessage(subData, "Subscribe") // Or specific JSON for subscribe
		} else {
			logrus.Errorf("WebSocketStream: Invalid data type for Subscribe control message: %T", msg.Data)
		}
	case ControlTypeUnsubscribe:
		if refID, ok := msg.Data.(string); ok {
			logrus.Infof("WebSocketStream: Processing unsubscribe request for RefID: %s", refID)
			// Need to find the original SubscriptionData to get the path for Saxo's unsubscribe.
			// Saxo's unsubscribe is often by referenceId and contextId, but control message format varies.
			// Python client sends: {"ContextId": self.context_id, "ReferenceId": reference_id}
			// The actual path for unsubscribe control messages might not be needed if it's a generic control message format.
			// Assuming Saxo streaming takes a generic control message to remove specific reference IDs.
			// This requires knowing Saxo's specific control message format for unsubscribe.
			// Placeholder:
			subData, found := ws.getSubscription(refID)
			if found {
				ws.sendSubscriptionControlMessage(*subData, "Unsubscribe") // Or specific JSON
			} else {
				logrus.Warnf("WebSocketStream: Cannot unsubscribe, ReferenceID %s not found.", refID)
			}
		} else {
			logrus.Errorf("WebSocketStream: Invalid data type for Unsubscribe control message: %T", msg.Data)
		}
	// Add ControlTypeModify later
	default:
		logrus.Warnf("WebSocketStream: Received unknown user control message type: %d", msg.Type)
	}
	if msg.ResponseChan != nil {
		close(msg.ResponseChan) // Signal completion (error handling would be more complex)
	}
}

// sendSubscriptionControlMessage sends a generic subscribe/unsubscribe control message.
// This needs to be adapted to Saxo's specific JSON format for control messages.
// The Python client's _send_control_message constructs:
// `{"ContextId": self.context_id, "ReferenceId": reference_id, "Format": format, "Arguments": args, ...}`
// and sends it to a control endpoint (usually not needed for WebSocket directly, but part of WS message).
// For WebSocket, it's usually a JSON message sent over the WS connection.
func (ws *WebSocketStream) sendSubscriptionControlMessage(sub SubscriptionData, operation string) {
	// This is a placeholder structure. Actual Saxo control message format is needed.
	// Typically, for data subscriptions (like price updates), the control message payload
	// would include the resource path (e.g., "/trade/v1/infoprices/subscriptions"),
	// the reference ID, format, arguments, and refresh rate.
	// The "operation" (Subscribe/Unsubscribe) might be implicit in the endpoint or part of payload.
	// Example structure (highly speculative, CHECK SAXO DOCS):
	type SaxoControlPayload struct {
		ContextID   string                 `json:"ContextId"`
		ReferenceID string                 `json:"ReferenceId"`
		Format      string                 `json:"Format,omitempty"`
		Arguments   map[string]interface{} `json:"Arguments"`
		RefreshRate int                    `json:"RefreshRate,omitempty"`
		Tag         string                 `json:"Tag,omitempty"`
		// Some APIs use a "Op" field like "subscribe" or "unsubscribe"
		// Some use different payload structures for sub vs unsub.
		// For Saxo, often it's about adding/removing ReferenceIDs from a list tied to ContextID.
		// The HTTP based subscription (e.g. portfolio, ens) is different from streaming data subscriptions.
		// Streaming data subscriptions are often "request this data for this referenceId".
	}

	// This function is a placeholder for the actual logic to send a control message
	// formatted according to Saxo's streaming API specifications.
	// For example, to subscribe to infoprices for UIC 123:
	// RefID: "myprice_123"
	// Payload: {"ReferenceId": "myprice_123", "Arguments": {"Uic": 123, "AssetType": "Stock"}}
	// This payload is then sent as a JSON string over the WebSocket.
	// The resource path like "/trade/v1/infoprices/subscriptions/{ContextId}/{ReferenceId}"
	// is what the *data messages* will be associated with, not necessarily the control message itself.

	payload := SaxoControlPayload{
		ContextID:   ws.contextID, // The overall streaming context
		ReferenceID: sub.ReferenceID,
		Format:      sub.Format,
		Arguments:   sub.Arguments,
		RefreshRate: sub.RefreshRate,
		Tag:         sub.Tag,
	}
	jsonMsg, err := json.Marshal(payload)
	if err != nil {
		logrus.Errorf("WebSocketStream: Failed to marshal %s control message for RefID %s: %v", operation, sub.ReferenceID, err)
		return
	}

	logrus.Debugf("WebSocketStream: Sending %s control message: %s", operation, string(jsonMsg))
	ws.connMux.Lock()
	defer ws.connMux.Unlock()
	if ws.conn == nil {
		logrus.Error("WebSocketStream: Cannot send control message, connection is nil.")
		return
	}
	ws.conn.SetWriteDeadline(time.Now().Add(writeWait))
	err = ws.conn.WriteMessage(websocket.TextMessage, jsonMsg)
	if err != nil {
		logrus.Errorf("WebSocketStream: Failed to write %s control message for RefID %s: %v", operation, sub.ReferenceID, err)
		// Potentially signal reconnect
	} else {
		if operation == "Subscribe" { // Simplified, actual subscribe might be more complex
			ws.activeSubLock.Lock()
			sub.isActive = true
			sub.lastUpdate = time.Now()
			ws.subscriptions[sub.ReferenceID] = &sub
			ws.activeSubLock.Unlock()
		} else if operation == "Unsubscribe" {
			ws.activeSubLock.Lock()
			delete(ws.subscriptions, sub.ReferenceID)
			ws.activeSubLock.Unlock()
		}
	}
}

// getSubscription retrieves a copy of a subscription by its reference ID.
func (ws *WebSocketStream) getSubscription(referenceID string) (*SubscriptionData, bool) {
	ws.activeSubLock.RLock()
	defer ws.activeSubLock.RUnlock()
	sub, found := ws.subscriptions[referenceID]
	if !found {
		return nil, false
	}
	// Return a copy to avoid race conditions if caller modifies it (though not typical for this use)
	subCopy := *sub
	return &subCopy, true
}

// cleanupConnection closes the WebSocket connection.
func (ws *WebSocketStream) cleanupConnection() {
	ws.connMux.Lock()
	if ws.conn != nil {
		logrus.Info("WebSocketStream: Cleaning up WebSocket connection.")
		ws.conn.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""))
		ws.conn.Close()
		ws.conn = nil
	}
	ws.connMux.Unlock()
	ws.isActive = false
}

// --- Public Methods ---

// Subscribe initiates a new subscription on the WebSocket stream.
// It sends a control message to the manageConnection goroutine.
// Returns an error if the request cannot be sent (e.g., channel full, not connected).
func (ws *WebSocketStream) Subscribe(sub SubscriptionData) error {
	if !ws.isActive || ws.conn == nil {
		return fmt.Errorf("WebSocketStream is not connected")
	}
	if sub.ReferenceID == "" {
		return fmt.Errorf("SubscriptionData.ReferenceID cannot be empty")
	}
	// ResourcePath is not directly used in Saxo control messages, Arguments usually contain Uic, AssetType etc.
	// However, it's good for internal tracking if needed.

	// A channel to confirm processing might be useful for robust applications.
	// ackChan := make(chan error, 1)
	msg := ControlMessage{
		Type: ControlTypeSubscribe,
		Data: sub,
		// ResponseChan: ackChan,
	}

	select {
	case ws.userCtrlChan <- msg:
		logrus.Debugf("WebSocketStream: Subscribe message for RefID %s sent to userCtrlChan.", sub.ReferenceID)
		// Optionally wait for ack from ackChan with a timeout
		// select {
		// case err := <-ackChan:
		//  return err
		// case <-time.After(5 * time.Second): // Timeout for ack
		//  return fmt.Errorf("timeout waiting for subscribe RefID %s to be processed", sub.ReferenceID)
		// }
		return nil
	case <-ws.ctx.Done():
		return fmt.Errorf("WebSocketStream context done, cannot subscribe RefID %s: %w", sub.ReferenceID, ws.ctx.Err())
	default:
		// This case might be hit if userCtrlChan is full.
		return fmt.Errorf("WebSocketStream control channel is full, cannot process subscribe for RefID %s", sub.ReferenceID)
	}
}

// Unsubscribe removes an existing subscription.
// referenceID: The ReferenceID of the subscription to remove.
func (ws *WebSocketStream) Unsubscribe(referenceID string) error {
	if !ws.isActive || ws.conn == nil {
		return fmt.Errorf("WebSocketStream is not connected")
	}
	if referenceID == "" {
		return fmt.Errorf("referenceID cannot be empty for unsubscribe")
	}

	msg := ControlMessage{
		Type: ControlTypeUnsubscribe,
		Data: referenceID,
	}
	select {
	case ws.userCtrlChan <- msg:
		logrus.Debugf("WebSocketStream: Unsubscribe message for RefID %s sent to userCtrlChan.", referenceID)
		return nil
	case <-ws.ctx.Done():
		return fmt.Errorf("WebSocketStream context done, cannot unsubscribe RefID %s: %w", referenceID, ws.ctx.Err())
	default:
		return fmt.Errorf("WebSocketStream control channel is full, cannot process unsubscribe for RefID %s", referenceID)
	}
}

// Messages returns a read-only channel for received data messages (as raw []byte).
func (ws *WebSocketStream) Messages() <-chan []byte {
	return ws.messageChannel
}

// Errors returns a read-only channel for errors encountered during WebSocket operations.
func (ws *WebSocketStream) Errors() <-chan error {
	return ws.errorChannel
}

// Close gracefully closes the WebSocket connection and stops associated goroutines.
func (ws *WebSocketStream) Close() error {
	logrus.Info("WebSocketStream: Close called.")
	ws.connectMux.Lock() // Ensure no new connection attempts while closing
	defer ws.connectMux.Unlock()

	if !ws.isActive {
		logrus.Warn("WebSocketStream: Close called, but stream is not active.")
		// Call cancelFunc anyway to ensure any pending goroutines from a failed connect are stopped.
		if ws.cancelFunc != nil {
			ws.cancelFunc()
		}
		return nil
	}

	ws.isActive = false // Mark as inactive immediately to prevent new operations

	// Signal goroutines to stop via context cancellation
	if ws.cancelFunc != nil {
		ws.cancelFunc()
	}

	// Close the WebSocket connection (this will also help unblock readMessages)
	ws.connMux.Lock()
	if ws.conn != nil {
		logrus.Debug("WebSocketStream: Sending CloseMessage and closing connection.")
		// Attempt to send a close message to the server.
		err := ws.conn.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""))
		if err != nil && err != websocket.ErrCloseSent { // ErrCloseSent is fine
			logrus.Warnf("WebSocketStream: Error writing close message: %v", err)
		}
		ws.conn.Close()
		ws.conn = nil
	}
	ws.connMux.Unlock()

	// Wait for goroutines to finish
	// Add a timeout to wg.Wait() to prevent indefinite blocking if a goroutine is stuck.
	waitTimeout := 5 * time.Second
	done := make(chan struct{})
	go func() {
		ws.wg.Wait()
		close(done)
	}()

	select {
	case <-done:
		logrus.Info("WebSocketStream: All goroutines finished.")
	case <-time.After(waitTimeout):
		logrus.Error("WebSocketStream: Timeout waiting for goroutines to finish on Close.")
		return fmt.Errorf("timeout waiting for WebSocket goroutines to stop")
	}

	// Close channels (after goroutines have stopped to prevent panic on send to closed channel)
	// Note: messageChannel and errorChannel are read by the user.
	// Closing them signals to the user that no more messages/errors will come.
	// internalCtrlChan and userCtrlChan are written to by user/internal logic and read by manageConnection.
	// Since manageConnection is stopped, these don't need explicit closing from here if they are buffered
	// and writers are also stopping. If they were unbuffered, this could deadlock.
	// It's generally safer to close channels that are used to signal 'done' or when the producer stops.
	// For now, let's assume the context cancellation handles goroutine shutdown sufficiently.
	// If a user is still reading Messages() or Errors(), they will block until these are closed.
	// This should be documented for the user of the library.
	// Let's close them to be explicit.
	close(ws.messageChannel)
	close(ws.errorChannel)
	// close(ws.internalCtrlChan) // Generally, don't close channels written to by multiple producers or if producer isn't self-aware of closing.
	// close(ws.userCtrlChan)     // Let garbage collection handle these if manageConnection is the sole consumer and has exited.

	logrus.Info("WebSocketStream: Close completed.")
	return nil
}
