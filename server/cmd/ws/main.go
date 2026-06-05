package main

import (
	"fmt"
	"log"
	"net/http"
	"time"

	"github.com/gorilla/websocket"
)

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool {
		return true // Allow all for hackathon
	},
}

func leaderboardWsHandler(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Println("Upgrade error:", err)
		return
	}
	defer conn.Close()

	// Mocking leaderboard updates for now.
	// In a real implementation, this would subscribe to a Redis Pub/Sub channel.
	for {
		mockLeaderboard := []map[string]interface{}{
			{"rank": 1, "team": "Alpha", "score": 98.5},
			{"rank": 2, "team": "Beta", "score": 95.2},
		}
		
		if err := conn.WriteJSON(mockLeaderboard); err != nil {
			log.Println("Write error:", err)
			break
		}
		
		time.Sleep(5 * time.Second)
	}
}

func main() {
	http.HandleFunc("/ws/leaderboard", leaderboardWsHandler)

	fmt.Println("WebSocket Server starting on :8082...")
	log.Fatal(http.ListenAndServe(":8082", nil))
}
