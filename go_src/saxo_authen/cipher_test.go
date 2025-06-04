package saxo_authen

import (
	"bytes"
	"os"
	"path/filepath"
	"strings" // Added import
	"testing"
)

func TestInitializeSalt(t *testing.T) {
	tempDir := t.TempDir()
	saltFilePath := filepath.Join(tempDir, "test.salt")

	// 1. Test salt generation if file doesn't exist
	salt1, err := initializeSalt(saltFilePath)
	if err != nil {
		t.Fatalf("initializeSalt (generate new) failed: %v", err)
	}
	if len(salt1) != saltSizeBytes {
		t.Errorf("Expected salt size %d, got %d", saltSizeBytes, len(salt1))
	}
	if _, errStat := os.Stat(saltFilePath); os.IsNotExist(errStat) {
		t.Error("Salt file was not created")
	}
	fileInfo, _ := os.Stat(saltFilePath)
	if fileInfo.Mode().Perm() != 0600 {
		t.Errorf("Salt file permissions incorrect: expected 0600, got %s", fileInfo.Mode().Perm().String())
	}


	// 2. Test reading existing salt
	salt2, err := initializeSalt(saltFilePath)
	if err != nil {
		t.Fatalf("initializeSalt (read existing) failed: %v", err)
	}
	if !bytes.Equal(salt1, salt2) {
		t.Error("Read salt does not match generated salt")
	}

	// 3. Test incorrect salt file size (tampered file)
	if err := os.WriteFile(saltFilePath, []byte{1, 2, 3}, 0600); err != nil {
		t.Fatalf("Failed to write tampered salt file: %v", err)
	}
	_, err = initializeSalt(saltFilePath)
	if err == nil {
		t.Error("initializeSalt should have failed for incorrect salt file size")
	} else if !strings.Contains(err.Error(), "incorrect size") {
		t.Errorf("Expected error about incorrect size, got: %v", err)
	}

	// 4. Test unreadable salt file (permissions) - harder to test reliably across platforms
	// os.Chmod(saltFilePath, 0000)
	// _, err = initializeSalt(saltFilePath)
	// if err == nil { t.Error("Expected error for unreadable salt file") }
	// os.Chmod(saltFilePath, 0600) // cleanup permission

	// 5. Test ensureDirExists part
	nestedSaltPath := filepath.Join(tempDir, "subdir1", "subdir2", "nested.salt")
	_, err = initializeSalt(nestedSaltPath)
	if err != nil {
		t.Fatalf("initializeSalt with nested path failed: %v", err)
	}
	if _, errStat := os.Stat(nestedSaltPath); os.IsNotExist(errStat) {
		t.Error("Nested salt file was not created")
	}
}

func TestNewCipher(t *testing.T) {
	tempDir := t.TempDir()
	saltFilePath := filepath.Join(tempDir, "cipher_test.salt")
	appSecret := "testAppSecret123"

	// 1. Test successful cipher creation
	aead, err := NewCipher(appSecret, saltFilePath)
	if err != nil {
		t.Fatalf("NewCipher failed: %v", err)
	}
	if aead == nil {
		t.Fatal("NewCipher returned nil AEAD")
	}
	if aead.NonceSize() != 12 { // Standard GCM nonce size
		t.Errorf("Expected GCM nonce size 12, got %d", aead.NonceSize())
	}

	// 2. Test with empty appSecret
	_, err = NewCipher("", saltFilePath)
	if err == nil {
		t.Error("NewCipher should fail with empty appSecret")
	} else if !strings.Contains(err.Error(), "appSecret cannot be empty") {
		t.Errorf("Unexpected error for empty appSecret: %v", err)
	}

	// 3. Test with empty saltFilePath
	_, err = NewCipher(appSecret, "")
	if err == nil {
		t.Error("NewCipher should fail with empty saltFilePath")
	} else if !strings.Contains(err.Error(), "saltFilePath cannot be empty") {
		t.Errorf("Unexpected error for empty saltFilePath: %v", err)
	}
}

func TestEncryptDecrypt(t *testing.T) {
	tempDir := t.TempDir()
	saltFilePath := filepath.Join(tempDir, "enc_dec_test.salt")
	appSecret := "anotherTestSecret"

	aead, err := NewCipher(appSecret, saltFilePath)
	if err != nil {
		t.Fatalf("NewCipher failed for EncryptDecrypt test: %v", err)
	}

	testData := []byte("This is some secret data to encrypt!")

	// 1. Encrypt
	encryptedData, err := Encrypt(aead, testData)
	if err != nil {
		t.Fatalf("Encrypt failed: %v", err)
	}
	if len(encryptedData) == 0 {
		t.Fatal("Encrypted data is empty")
	}
	if bytes.Equal(encryptedData, testData) {
		t.Fatal("Encrypted data is the same as original data")
	}

	// 2. Decrypt
	decryptedData, err := Decrypt(aead, encryptedData)
	if err != nil {
		t.Fatalf("Decrypt failed: %v", err)
	}
	if !bytes.Equal(decryptedData, testData) {
		t.Errorf("Decrypted data does not match original. Got: %s, Expected: %s", string(decryptedData), string(testData))
	}

	// 3. Test Decrypt with corrupted data (tamper with ciphertext)
	if len(encryptedData) > aead.NonceSize()+1 { // Ensure there's ciphertext to tamper
		corruptedData := make([]byte, len(encryptedData))
		copy(corruptedData, encryptedData)
		corruptedData[aead.NonceSize()]++ // Flip one byte of the actual ciphertext part

		_, err = Decrypt(aead, corruptedData)
		if err == nil {
			t.Error("Decrypt should fail with corrupted data")
		} else if !strings.Contains(err.Error(), "failed to decrypt data") && !strings.Contains(err.Error(), "message authentication failed") {
			// "cipher: message authentication failed" is the specific error from GCM.Open
			t.Errorf("Expected 'message authentication failed' or similar, got: %v", err)
		}
	} else {
		t.Log("Skipping corrupted data test as encrypted data is too short.")
	}


	// 4. Test Decrypt with too short data (shorter than nonce)
	shortData := make([]byte, aead.NonceSize()-1)
	_, err = Decrypt(aead, shortData)
	if err == nil {
		t.Error("Decrypt should fail with data shorter than nonce size")
	} else if !strings.Contains(err.Error(), "too short to contain a nonce") {
		t.Errorf("Expected error about data being too short, got: %v", err)
	}

	// 5. Test Encrypt/Decrypt with nil AEAD
	_, err = Encrypt(nil, testData)
	if err == nil || !strings.Contains(err.Error(), "AEAD cipher is nil") {
		t.Errorf("Encrypt with nil AEAD: expected specific error, got %v", err)
	}
	_, err = Decrypt(nil, encryptedData)
	if err == nil || !strings.Contains(err.Error(), "AEAD cipher is nil") {
		t.Errorf("Decrypt with nil AEAD: expected specific error, got %v", err)
	}
}

// Test ensureDirExists separately if needed, though it's covered by initializeSalt
func TestEnsureDirExists(t *testing.T) {
    tempDir := t.TempDir()
    testPath := filepath.Join(tempDir, "new_dir", "file.txt")

    err := ensureDirExists(testPath)
    if err != nil {
        t.Fatalf("ensureDirExists failed: %v", err)
    }
    if _, statErr := os.Stat(filepath.Dir(testPath)); os.IsNotExist(statErr) {
        t.Errorf("Directory %s was not created", filepath.Dir(testPath))
    }

    // Test again to ensure it doesn't fail if dir exists
    err = ensureDirExists(testPath)
    if err != nil {
        t.Fatalf("ensureDirExists failed on existing directory: %v", err)
    }
}
