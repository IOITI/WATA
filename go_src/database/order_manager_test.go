package database

import (
	"database/sql"
	"fmt" // Added back
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid" // For generating unique IDs
)

// Helper function to get a OrderManager with an in-memory DB for testing
func setupOrderManagerTest(t *testing.T) (*TradingDB, *OrderManager, func()) {
	tdb, cleanupMain := setupTestDB(t) // Uses helper from database_test.go
	om := NewOrderManager(tdb)

	err := om.CreateSchemaTurboDataOrder()
	if err != nil {
		cleanupMain()
		t.Fatalf("Failed to create turbo_data_order schema: %v", err)
	}

	cleanup := func() {
		cleanupMain()
	}
	return tdb, om, cleanup
}

func TestOrderManager_CreateSchema(t *testing.T) {
	tdb, _, cleanup := setupOrderManagerTest(t)
	defer cleanup()

	var tableName string
	err := tdb.DB().QueryRow("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_name = 'turbo_data_order';").Scan(&tableName)

	if err != nil {
		if err == sql.ErrNoRows {
			t.Fatal("Table 'turbo_data_order' was not created by CreateSchemaTurboDataOrder")
		}
		t.Fatalf("Failed to query for table 'turbo_data_order': %v", err)
	}
	if tableName != "turbo_data_order" {
		t.Fatalf("Expected table 'turbo_data_order', but found '%s'", tableName)
	}
}

func TestOrderManager_InsertTurboOrderData_Valid(t *testing.T) {
	_, om, cleanup := setupOrderManagerTest(t)
	defer cleanup()

	testID := uuid.NewString()
	now := time.Now().UTC().Truncate(time.Millisecond)
	// Example with a single quote that needs escaping for SQL, but stored as is.
	relatedIDsInput := []string{uuid.NewString(), "item_with_'single_quote"} 

	order := &TurboOrderData{
		ID:             testID,
		ActionType:     sql.NullString{String: "buy", Valid: true},
		CreatedAt:      now,
		RelatedOrderID: relatedIDsInput, // This is the original []string
		UserID:         sql.NullString{String: "user123", Valid: true},
	}

	err := om.InsertTurboOrderData(order)
	if err != nil {
		t.Fatalf("InsertTurboOrderData failed for valid order: %v", err)
	}

	var retrievedOrder TurboOrderData
	var retrievedRelatedOrderIDsInterface interface{} 

	query := `SELECT id, action_type, created_at, related_order_id, user_id FROM turbo_data_order WHERE id = ?;`
	row := om.tdb.DB().QueryRow(query, testID)
	err = row.Scan(
		&retrievedOrder.ID, &retrievedOrder.ActionType, &retrievedOrder.CreatedAt,
		&retrievedRelatedOrderIDsInterface, &retrievedOrder.UserID,
	)
	if err != nil {
		t.Fatalf("Failed to query and scan inserted order: %v", err)
	}
	
	var finalRetrievedRelatedOrderIDs []string
	if retrievedRelatedOrderIDsInterface != nil {
		if interfaceSlice, ok := retrievedRelatedOrderIDsInterface.([]interface{}); ok {
			for _, item := range interfaceSlice {
				if itemStr, okStr := item.(string); okStr {
					finalRetrievedRelatedOrderIDs = append(finalRetrievedRelatedOrderIDs, itemStr)
				} else {
					t.Fatalf("Retrieved related_order_id item is not a string: %T, value: %v", item, item)
				}
			}
		} else if strSlice, okStrSlice := retrievedRelatedOrderIDsInterface.([]string); okStrSlice {
			finalRetrievedRelatedOrderIDs = strSlice
		} else {
			t.Fatalf("Retrieved related_order_id is not []interface{} or []string, but %T", retrievedRelatedOrderIDsInterface)
		}
	}

	if retrievedOrder.ID != order.ID {
		t.Errorf("ID mismatch: expected %s, got %s", order.ID, retrievedOrder.ID)
	}
	if !retrievedOrder.CreatedAt.Equal(order.CreatedAt) {
		t.Errorf("CreatedAt mismatch: expected %v, got %v", order.CreatedAt, retrievedOrder.CreatedAt)
	}

	// The values in finalRetrievedRelatedOrderIDs are as returned by the driver.
	// We need to compare them against what we originally inserted (relatedIDsInput),
	// but adjust relatedIDsInput to match the driver's output format.
	expectedRetrievedFormat := make([]string, len(relatedIDsInput))
	for i, s := range relatedIDsInput {
		// Driver seems to return strings quoted as SQL literals: 'string' and internal ' becomes ''.
		escapedS := strings.ReplaceAll(s, "'", "''")
		expectedRetrievedFormat[i] = fmt.Sprintf("'%s'", escapedS)
	}

	sort.Strings(finalRetrievedRelatedOrderIDs)
	sort.Strings(expectedRetrievedFormat) 
	if !reflect.DeepEqual(finalRetrievedRelatedOrderIDs, expectedRetrievedFormat) {
		t.Errorf("RelatedOrderID mismatch:\nExpected (driver format): %v\nGot:                  %v\nOriginal Input:       %v", 
			expectedRetrievedFormat, finalRetrievedRelatedOrderIDs, relatedIDsInput)
	}
}

func TestOrderManager_InsertTurboOrderData_NilOrder(t *testing.T) {
	_, om, cleanup := setupOrderManagerTest(t)
	defer cleanup()
	err := om.InsertTurboOrderData(nil)
	if err == nil || !strings.Contains(err.Error(), "order data cannot be nil") {
		t.Errorf("Expected 'order data cannot be nil' error, got %v", err)
	}
}

func TestOrderManager_InsertTurboOrderData_MissingID(t *testing.T) {
	_, om, cleanup := setupOrderManagerTest(t)
	defer cleanup()
	order := &TurboOrderData{}
	err := om.InsertTurboOrderData(order)
	if err == nil || !strings.Contains(err.Error(), "order ID is required") {
		t.Errorf("Expected 'order ID is required' error, got %v", err)
	}
}

func TestOrderManager_InsertTurboOrderData_MinimalFields(t *testing.T) {
	_, om, cleanup := setupOrderManagerTest(t)
	defer cleanup()
	testID := uuid.NewString()
	order := &TurboOrderData{ID: testID, RelatedOrderID: []string{}} 
	err := om.InsertTurboOrderData(order)
	if err != nil {
		t.Fatalf("InsertTurboOrderData failed for minimal order (empty RelatedOrderID): %v", err)
	}
	
	var relatedIDsInterface interface{}
	err = om.tdb.DB().QueryRow("SELECT related_order_id FROM turbo_data_order WHERE id = ?", testID).Scan(&relatedIDsInterface)
	if err != nil {
		t.Fatalf("Failed to query minimal order (empty RelatedOrderID): %v", err)
	}
    var finalRelatedIDs []string
	if relatedIDsInterface != nil {
        if interfaceSlice, ok := relatedIDsInterface.([]interface{}); ok {
            if len(interfaceSlice) != 0 { 
                 t.Errorf("Expected empty RelatedOrderID from DB, got %v items", len(interfaceSlice))
            }
        } else if strSlice, okStrSlice := relatedIDsInterface.([]string); okStrSlice {
             if len(strSlice) != 0 {
                t.Errorf("Expected empty RelatedOrderID from DB, got %v items", len(strSlice))
             }
        } else {
            t.Fatalf("Retrieved related_order_id for empty list is not []interface{} or []string, but %T", relatedIDsInterface)
        }
    }
	if finalRelatedIDs != nil && len(finalRelatedIDs) != 0 { 
		t.Errorf("Expected empty RelatedOrderID, got %v", finalRelatedIDs)
	}


	testIDNil := uuid.NewString()
	orderNil := &TurboOrderData{ID: testIDNil, RelatedOrderID: nil} 
	err = om.InsertTurboOrderData(orderNil)
	if err != nil {
		t.Fatalf("InsertTurboOrderData failed for minimal order (nil RelatedOrderID): %v", err)
	}
	var retrievedNilRelatedOrderIDsInterface interface{}
	err = om.tdb.DB().QueryRow("SELECT related_order_id FROM turbo_data_order WHERE id = ?", testIDNil).Scan(&retrievedNilRelatedOrderIDsInterface)
	if err != nil {
		t.Fatalf("Failed to query minimal order (nil RelatedOrderID): %v", err)
	}
    if retrievedNilRelatedOrderIDsInterface != nil {
        t.Errorf("Expected nil RelatedOrderID for NULL DB value, got %v (%T)", retrievedNilRelatedOrderIDsInterface, retrievedNilRelatedOrderIDsInterface)
    }
}

func TestOrderManager_InsertTurboOrderData_DuplicateID(t *testing.T) {
	_, om, cleanup := setupOrderManagerTest(t)
	defer cleanup()
	testID := uuid.NewString()
	order1 := &TurboOrderData{ID: testID, CreatedAt: time.Now()}
	err := om.InsertTurboOrderData(order1)
	if err != nil {
		t.Fatalf("First insert failed: %v", err)
	}
	order2 := &TurboOrderData{ID: testID, CreatedAt: time.Now()}
	err = om.InsertTurboOrderData(order2)
	if err == nil {
		t.Fatal("InsertTurboOrderData should fail for duplicate ID")
	}
	if !strings.Contains(strings.ToLower(err.Error()), "primary key constraint") && !strings.Contains(strings.ToLower(err.Error()), "unique constraint") {
		t.Errorf("Expected PK/Unique constraint error, got %v", err)
	}
}

func TestOrderManager_InsertTurboOrderData_ArrayTypes(t *testing.T) {
	_, om, cleanup := setupOrderManagerTest(t)
	defer cleanup()

	baseID := uuid.NewString()
	testCases := []struct {
		name          string
		relatedOrders []string // These are the original, unescaped strings
	}{
		{"WithRelatedOrders", []string{uuid.NewString(), uuid.NewString()}},
		{"WithSpecialCharInID", []string{uuid.NewString(), "id_with_'single_quote"}},
		{"EmptyRelatedOrders", []string{}}, 
		{"NilRelatedOrders", nil},         
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			currentID := baseID + "_" + tc.name
			order := &TurboOrderData{
				ID:             currentID,
				CreatedAt:      time.Now().UTC(),
				RelatedOrderID: tc.relatedOrders, // Original strings
			}
			err := om.InsertTurboOrderData(order)
			if err != nil {
				t.Fatalf("InsertTurboOrderData failed for %s: %v", tc.name, err)
			}

			var retrievedRelatedIDsInterface interface{}
			err = om.tdb.DB().QueryRow("SELECT related_order_id FROM turbo_data_order WHERE id = ?", currentID).Scan(&retrievedRelatedIDsInterface)
			
			var finalRetrievedRelatedOrderIDs []string
			if err != nil {
				if !(tc.relatedOrders == nil && retrievedRelatedIDsInterface == nil) {
					t.Fatalf("Failed to query/scan related_order_id for %s: %v", tc.name, err)
				}
			}

			if retrievedRelatedIDsInterface != nil {
				if interfaceSlice, ok := retrievedRelatedIDsInterface.([]interface{}); ok {
					for _, item := range interfaceSlice {
						if itemStr, okStr := item.(string); okStr {
							finalRetrievedRelatedOrderIDs = append(finalRetrievedRelatedOrderIDs, itemStr)
						} else {
							t.Fatalf("For %s: Retrieved related_order_id item is not a string: %T", tc.name, item)
						}
					}
				} else if strSlice, okStrSlice := retrievedRelatedIDsInterface.([]string); okStrSlice {
					finalRetrievedRelatedOrderIDs = strSlice
				} else {
					t.Fatalf("For %s: Retrieved related_order_id is not []interface{} or []string, but %T", tc.name, retrievedRelatedIDsInterface)
				}
			}

			
			expectedForComparison := make([]string, len(tc.relatedOrders))
			if tc.relatedOrders != nil {
				for i, s := range tc.relatedOrders {
					escapedS := strings.ReplaceAll(s, "'", "''")
					expectedForComparison[i] = fmt.Sprintf("'%s'", escapedS)
				}
			}


			if tc.relatedOrders == nil { 
				if !(finalRetrievedRelatedOrderIDs == nil || len(finalRetrievedRelatedOrderIDs) == 0) {
					t.Errorf("For %s (nil input): expected nil or empty slice from DB, got %v", tc.name, finalRetrievedRelatedOrderIDs)
				}
			} else if len(tc.relatedOrders) == 0 {  // Input was []string{}
				// Expect nil OR non-nil empty slice
				if !(finalRetrievedRelatedOrderIDs == nil || (finalRetrievedRelatedOrderIDs != nil && len(finalRetrievedRelatedOrderIDs) == 0)) {
					t.Errorf("For %s (empty input): expected nil or non-nil empty slice from DB, got %v (is nil: %t, len: %d)", 
                        tc.name, finalRetrievedRelatedOrderIDs, finalRetrievedRelatedOrderIDs == nil, len(finalRetrievedRelatedOrderIDs))
				}
			} else { 
				sort.Strings(expectedForComparison)
				sort.Strings(finalRetrievedRelatedOrderIDs)
				if !reflect.DeepEqual(finalRetrievedRelatedOrderIDs, expectedForComparison) {
					t.Errorf("For %s: related_order_id mismatch.\nExpected (driver format): %v\nGot:                      %v\nOriginal Input:           %v", 
						tc.name, expectedForComparison, finalRetrievedRelatedOrderIDs, tc.relatedOrders)
				}
			}
		})
	}
}
