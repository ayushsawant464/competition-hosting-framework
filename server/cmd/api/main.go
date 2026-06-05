package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"sort"
	"sync"

	"github.com/gorilla/websocket"
)

const SecretKey = "iicpc_super_secret_hackathon_key_2026"

type SubmitRequest struct {
	Payload   json.RawMessage `json:"payload"`
	Signature string          `json:"signature"`
}

type SubmissionResponse struct {
	Success bool   `json:"success"`
	Error   string `json:"error,omitempty"`
	Message string `json:"message,omitempty"`
}

type Payload struct {
	Team     string `json:"team"`
	OTP      string `json:"otp"`
	Scenario string `json:"scenario"`
	Score    struct {
		CompositeScore float64 `json:"composite_score"`
	} `json:"score"`
}

var teamOTPs = map[string]string{
	"AlphaTeam":    "IICPC-9482",
	"BetaTeam":     "IICPC-2139",
	"GammaTeam":    "IICPC-7501",
	"QuantKnights": "IICPC-3847",
}

type LeaderboardEntry struct {
	Rank  int     `json:"rank"`
	Team  string  `json:"team"`
	Score float64 `json:"score"`
}

type LeaderboardManager struct {
	mu       sync.RWMutex
	entries  map[string]float64 // maps team name -> best score
	clients  map[*websocket.Conn]bool
	upgrader websocket.Upgrader
}

var manager = &LeaderboardManager{
	entries: map[string]float64{
		"Optiver Knights": 9200.0,
		"Quantletes":      8800.0,
		"O(1) Boys":       7500.0,
		"Latent Space":    6200.0,
		"Null Pointers":   4500.0,
	},
	clients: make(map[*websocket.Conn]bool),
	upgrader: websocket.Upgrader{
		CheckOrigin: func(r *http.Request) bool {
			return true // Allow all for hackathon
		},
	},
}

func (lm *LeaderboardManager) GetSortedEntries() []LeaderboardEntry {
	lm.mu.RLock()
	defer lm.mu.RUnlock()

	var list []LeaderboardEntry
	for team, score := range lm.entries {
		list = append(list, LeaderboardEntry{
			Team:  team,
			Score: score,
		})
	}

	// Sort descending by score
	sort.Slice(list, func(i, j int) bool {
		return list[i].Score > list[j].Score
	})

	// Assign ranks
	for i := range list {
		list[i].Rank = i + 1
	}

	return list
}

func (lm *LeaderboardManager) AddOrUpdate(team string, score float64) bool {
	lm.mu.Lock()
	defer lm.mu.Unlock()

	oldScore, exists := lm.entries[team]
	if !exists || score > oldScore {
		lm.entries[team] = score
		return true // Leaderboard updated
	}
	return false
}

func (lm *LeaderboardManager) RegisterClient(conn *websocket.Conn) {
	lm.mu.Lock()
	lm.clients[conn] = true
	lm.mu.Unlock()

	// Send current leaderboard immediately
	current := lm.GetSortedEntries()
	_ = conn.WriteJSON(current)
}

func (lm *LeaderboardManager) UnregisterClient(conn *websocket.Conn) {
	lm.mu.Lock()
	delete(lm.clients, conn)
	lm.mu.Unlock()
}

func (lm *LeaderboardManager) Broadcast() {
	current := lm.GetSortedEntries()

	lm.mu.Lock()
	defer lm.mu.Unlock()

	for client := range lm.clients {
		err := client.WriteJSON(current)
		if err != nil {
			log.Printf("WS write error: %v", err)
			client.Close()
			delete(lm.clients, client)
		}
	}
}

func verifyHMAC(payloadBytes []byte, signature string) bool {
	h := hmac.New(sha256.New, []byte(SecretKey))
	h.Write(payloadBytes)
	expectedMAC := hex.EncodeToString(h.Sum(nil))

	return hmac.Equal([]byte(signature), []byte(expectedMAC))
}

func enableCors(w *http.ResponseWriter) {
	(*w).Header().Set("Access-Control-Allow-Origin", "*")
	(*w).Header().Set("Access-Control-Allow-Methods", "POST, GET, OPTIONS, PUT, DELETE")
	(*w).Header().Set("Access-Control-Allow-Headers", "Accept, Content-Type, Content-Length, Accept-Encoding, X-CSRF-Token, Authorization")
}

func submitHandler(w http.ResponseWriter, r *http.Request) {
	enableCors(&w)
	if r.Method == http.MethodOptions {
		return
	}

	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req SubmitRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	w.Header().Set("Content-Type", "application/json")

	// Verify Cryptographic Signature
	if !verifyHMAC(req.Payload, req.Signature) {
		log.Printf("SECURITY ALERT: Invalid payload signature detected. Possible tampering.")
		w.WriteHeader(http.StatusUnauthorized)
		json.NewEncoder(w).Encode(SubmissionResponse{
			Success: false,
			Error:   "Cryptographic signature verification failed. Payload tampering detected.",
		})
		return
	}

	// Parse payload contents
	var payloadData Payload
	if err := json.Unmarshal(req.Payload, &payloadData); err != nil {
		log.Printf("Error unmarshaling payload: %v", err)
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(SubmissionResponse{
			Success: false,
			Error:   fmt.Sprintf("Failed to parse inner payload: %v", err),
		})
		return
	}

	// Verify Team OTP Authentication
	expectedOTP, ok := teamOTPs[payloadData.Team]
	if !ok || expectedOTP != payloadData.OTP {
		log.Printf("AUTHENTICATION FAILURE: Team '%s' provided invalid OTP '%s'", payloadData.Team, payloadData.OTP)
		w.WriteHeader(http.StatusForbidden)
		json.NewEncoder(w).Encode(SubmissionResponse{
			Success: false,
			Error:   "Invalid Team Name or Authentication OTP passcode. Please check your credentials.",
		})
		return
	}

	log.Printf("Verified payload successfully: %s", string(req.Payload))
	log.Printf("Adding/Updating score for '%s': %f", payloadData.Team, payloadData.Score.CompositeScore)

	// Update in-memory leaderboard
	changed := manager.AddOrUpdate(payloadData.Team, payloadData.Score.CompositeScore)
	if changed {
		log.Printf("Leaderboard updated. Broadcasting new scores...")
		manager.Broadcast()
	}

	json.NewEncoder(w).Encode(SubmissionResponse{
		Success: true,
		Message: "Score verified and Leaderboard updated successfully.",
	})
}

func leaderboardWsHandler(w http.ResponseWriter, r *http.Request) {
	conn, err := manager.upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Println("Upgrade error:", err)
		return
	}

	manager.RegisterClient(conn)
	defer func() {
		manager.UnregisterClient(conn)
		conn.Close()
	}()

	// Keep connection alive, listen for client disconnects/messages
	for {
		_, _, err := conn.ReadMessage()
		if err != nil {
			break
		}
	}
}

func main() {
	// API Server Router
	apiMux := http.NewServeMux()
	apiMux.HandleFunc("/api/submit", submitHandler)

	// WebSocket Server Router
	wsMux := http.NewServeMux()
	wsMux.HandleFunc("/ws/leaderboard", leaderboardWsHandler)

	// Start WebSocket Server on :8082
	go func() {
		fmt.Println("WebSocket Server starting on :8082...")
		if err := http.ListenAndServe(":8082", wsMux); err != nil {
			log.Fatalf("WebSocket server error: %v", err)
		}
	}()

	// Start API Server on :8081 (Blocks main goroutine)
	fmt.Println("API Server starting on :8081...")
	log.Fatal(http.ListenAndServe(":8081", apiMux))
}
