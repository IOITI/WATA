package saxo_openapi

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"pymath/go_src/database"
	"pymath/go_src/saxo_authen"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	// "github.com/sirupsen/logrus" // Removed unused import
)

// --- Mock TokenManagerInterface for SaxoAuth in WebSocket tests ---
type mockTokenManagerForWSTests struct {
	saxo_authen.TokenManagerInterface
	GetTokenFunc func(tokenKey string) (*database.AuthTokenData, error)
}
func (m *mockTokenManagerForWSTests) GetToken(tokenKey string) (*database.AuthTokenData, error) {
	if m.GetTokenFunc != nil { return m.GetTokenFunc(tokenKey) }
	return nil, fmt.Errorf("mockTokenManagerForWSTests: GetToken called, default not found for key '%s'", tokenKey)
}
func (m *mockTokenManagerForWSTests) StoreToken(tokenHash, userID string, encryptedPayload []byte, expiresAt time.Time, ipAddress, userAgent string, metadata map[string]interface{}) error { return nil }


// --- Mock WebSocket Server ---
var upgrader = websocket.Upgrader{
	ReadBufferSize:  1024,
	WriteBufferSize: 1024,
	CheckOrigin: func(r *http.Request) bool { return true },
}

type wsMessage struct {
	Type    int
	Payload []byte
}

type mockWebSocketServer struct {
	server           *httptest.Server
	conn             *websocket.Conn
	t                *testing.T
	messageHandler   func(conn *websocket.Conn, msgType int, msg []byte)
	connectAuthToken string
	connectContextID string
	onConnect        func(w http.ResponseWriter, r *http.Request)
	mu               sync.Mutex
	receivedMessages []wsMessage
}

func newMockWebSocketServer(t *testing.T, connectAuthToken, connectContextID string, onConnect func(w http.ResponseWriter, r *http.Request), handler func(conn *websocket.Conn, msgType int, msg []byte)) *mockWebSocketServer {
	mws := &mockWebSocketServer{
		t:                t,
		messageHandler:   handler,
		connectAuthToken: connectAuthToken,
		connectContextID: connectContextID,
		onConnect:        onConnect,
	}
	mws.server = httptest.NewServer(http.HandlerFunc(mws.handleWebSocket))
	return mws
}

func (mws *mockWebSocketServer) handleWebSocket(w http.ResponseWriter, r *http.Request) {
	authHeader := r.Header.Get("Authorization")
	expectedAuth := "Bearer " + mws.connectAuthToken
	if authHeader != expectedAuth {
		mws.t.Errorf("WebSocket Server: Incorrect Authorization header. Got '%s', Expected '%s'", authHeader, expectedAuth)
		http.Error(w, "Unauthorized", http.StatusUnauthorized)
		return
	}
	if mws.connectContextID != "" {
		queryContextID := r.URL.Query().Get("contextId")
		if queryContextID != mws.connectContextID {
			mws.t.Errorf("WebSocket Server: Incorrect contextId query param. Got '%s', Expected '%s'", queryContextID, mws.connectContextID)
			http.Error(w, "Bad Request - Invalid contextId", http.StatusBadRequest)
			return
		}
	}

	if mws.onConnect != nil {
		mws.onConnect(w, r)
		// Check if onConnect has already written a response or hijacked the connection.
		// A simple check could be to see if headers were written, though not foolproof.
		// For robust hijacking check, one might need to inspect ResponseWriter's internal state
		// or use a more complex ResponseWriter wrapper.
		// For this mock, if onConnect writes an error, it should handle the full response.
		// The `upgrader.Upgrade` will fail if headers already sent.
	}

	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		mws.t.Logf("WebSocket Server: Upgrade error: %v", err)
		return
	}
	mws.mu.Lock()
	mws.conn = conn
	mws.mu.Unlock()
	defer conn.Close()

	if mws.messageHandler == nil {
		mws.messageHandler = func(c *websocket.Conn, mt int, p []byte) {
			mws.t.Logf("WebSocket Server: Default handler echoing message type %d: %s", mt, string(p))
			if err := c.WriteMessage(mt, p); err != nil {
				mws.t.Logf("WebSocket Server: Echo error: %v", err)
			}
		}
	}

	for {
		msgType, msg, errRead := conn.ReadMessage()
		if errRead != nil {
			if websocket.IsUnexpectedCloseError(errRead, websocket.CloseGoingAway, websocket.CloseNormalClosure) {
				mws.t.Logf("WebSocket Server: Read error: %v", errRead)
			}
			break
		}
		mws.mu.Lock()
		mws.receivedMessages = append(mws.receivedMessages, wsMessage{Type: msgType, Payload: msg})
		mws.mu.Unlock()
		mws.messageHandler(conn, msgType, msg)
	}
}

func (mws *mockWebSocketServer) URL() string {
	return strings.Replace(mws.server.URL, "http", "ws", 1)
}

func (mws *mockWebSocketServer) Close() {
	mws.mu.Lock()
	if mws.conn != nil {
		mws.conn.Close()
	}
	mws.mu.Unlock()
	mws.server.Close()
}

func (mws *mockWebSocketServer) GetReceivedMessages() []wsMessage {
	mws.mu.Lock()
	defer mws.mu.Unlock()
	msgs := make([]wsMessage, len(mws.receivedMessages))
	copy(msgs, mws.receivedMessages)
	return msgs
}


// --- Test Setup for WebSocketStream ---
func setupWebSocketStreamTest(t *testing.T, serverConnectToken, serverConnectCtxID string,
	onConnect func(w http.ResponseWriter, r *http.Request),
	serverMsgHandler func(conn *websocket.Conn, msgType int, msg []byte)) (*WebSocketStream, *mockWebSocketServer, func()) {

	mockServer := newMockWebSocketServer(t, serverConnectToken, serverConnectCtxID, onConnect, serverMsgHandler)

	tempDir := t.TempDir()
	tokenDirPath := filepath.Join(tempDir, "test_saxo_auth_data_for_ws_client")
	appCfg := saxo_authen.SaxoAppConfig{
		AppName:   "TestWSClientApp", AppKey: "testkey_ws_client",
		AppSecret: "testsecret_ws_client_must_be_32_bytes_long",
		AuthURL: "http://dummy/auth", TokenURL: "http://dummy/token", RedirectURL: "http://dummy/redirect",
	}
	mockTokenDB := &mockTokenManagerForWSTests{}
	testAuth, errAuth := saxo_authen.NewSaxoAuth(appCfg, tokenDirPath, mockTokenDB, nil)
	if errAuth != nil {t.Fatalf("Failed to create SaxoAuth for test setup: %v", errAuth)}

	testAuth.GetTokenOverride = func() (string, error) { return serverConnectToken, nil }

	client, errClient := NewClient(testAuth, EnvironmentSimulation, 10*time.Second)
	if errClient != nil {t.Fatalf("Failed to create client: %v", errClient)}

	client.streamBaseURL = mockServer.URL()

	ctx, cancelCtx := context.WithCancel(context.Background())
	wsStream, errWs := NewWebSocketStream(ctx, client)
	if errWs != nil {
		cancelCtx()
		mockServer.Close()
		t.Fatalf("NewWebSocketStream failed: %v", errWs)
	}

	cleanup := func() {
		wsStream.Close()
		mockServer.Close()
	}
	return wsStream, mockServer, cleanup
}


// --- Actual Tests ---

func TestWebSocketStream_Connect_Success(t *testing.T) {
	connectToken := "connect-token"
	connectContextID := "connect-ctx-id"

	wsStream, _, cleanup := setupWebSocketStreamTest(t, connectToken, connectContextID, nil, nil)
	defer cleanup()

	err := wsStream.Connect(connectContextID)
	if err != nil {
		t.Fatalf("Connect failed: %v", err)
	}
	if !wsStream.isActive {
		t.Error("WebSocketStream should be active after successful Connect")
	}
}

func TestWebSocketStream_Connect_AuthFailure(t *testing.T) {
	connectToken := "connect-token"
	connectContextID := "connect-ctx-id"

	wsStream, _, cleanup := setupWebSocketStreamTest(t, connectToken, connectContextID, nil, nil)
	defer cleanup()

	wsStream.client.Authenticator.GetTokenOverride = func() (string, error) {
		return "", fmt.Errorf("mock GetToken failed")
	}

	err := wsStream.Connect(connectContextID)
	if err == nil {
		t.Fatal("Connect should have failed due to auth error")
	}
	if !strings.Contains(err.Error(), "mock GetToken failed") {
		t.Errorf("Expected auth failure message, got: %v", err)
	}
	if wsStream.isActive {
		t.Error("WebSocketStream should not be active after failed Connect")
	}
}


func TestWebSocketStream_ReceiveMessages(t *testing.T) {
	connectToken := "token1"
	connectContextID := "ctx1"
	serverSentMessage := []byte(`{"data": "hello world"}`)

	// This var was unused, removing it.
	// onConnectHandler := func(w http.ResponseWriter, r *http.Request) {}

	// Redefine mock server for this test to send a message after connect
	currentTestServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil { return }
		defer conn.Close()
		conn.WriteMessage(websocket.TextMessage, serverSentMessage)
	}))
	defer currentTestServer.Close()

	// Setup client and wsStream pointing to this specific server
	// Copied and adapted from setupWebSocketStreamTest
	tempDir := t.TempDir()
	tokenDirPath := filepath.Join(tempDir, "test_saxo_auth_data_for_rcv_test")
	appCfg := saxo_authen.SaxoAppConfig{
		AppName:   "TestWSClientRcvApp", AppKey: "testkey_ws_rcv",
		AppSecret: "testsecret_ws_rcv_must_be_32_bytes_long",
		AuthURL: "http://dummy/auth", TokenURL: "http://dummy/token", RedirectURL: "http://dummy/redirect",
	}
	mockTokenDB := &mockTokenManagerForWSTests{}
	testAuth, _ := saxo_authen.NewSaxoAuth(appCfg, tokenDirPath, mockTokenDB, nil)
	testAuth.GetTokenOverride = func() (string, error) { return connectToken, nil }
	client, _ := NewClient(testAuth, EnvironmentSimulation, 10*time.Second)
	client.streamBaseURL = strings.Replace(currentTestServer.URL, "http", "ws", 1)

	ctx, cancelCtx := context.WithCancel(context.Background())
	defer cancelCtx() // Ensure context is cancelled for this test
	wsStream, _ := NewWebSocketStream(ctx, client)
	// End of adapted setup


	err := wsStream.Connect(connectContextID)
	if err != nil {
		t.Fatalf("Connect failed: %v", err)
	}
	defer wsStream.Close() // Ensure stream is closed for this test

	select {
	case receivedMsg := <-wsStream.Messages():
		if string(receivedMsg) != string(serverSentMessage) {
			t.Errorf("Received message mismatch. Got '%s', Exp '%s'", string(receivedMsg), string(serverSentMessage))
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Timeout waiting for message from WebSocket stream")
	case err := <-wsStream.Errors():
		t.Fatalf("Received unexpected error from stream: %v", err)
	}
}
