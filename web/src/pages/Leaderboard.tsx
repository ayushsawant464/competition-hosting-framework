import { useState, useEffect } from 'react';
import { Trophy, TrendingUp, Medal } from 'lucide-react';

interface LeaderboardEntry {
  rank: number;
  team: string;
  score: number;
}

export default function Leaderboard() {
  const [entries, setEntries] = useState<LeaderboardEntry[]>([
    // Fallback static data if WS fails
    { rank: 1, team: 'Optiver Knights', score: 99.8 },
    { rank: 2, team: 'Quantletes', score: 98.5 },
    { rank: 3, team: 'O(1) Boys', score: 95.2 },
    { rank: 4, team: 'Latent Space', score: 88.4 },
    { rank: 5, team: 'Null Pointers', score: 82.1 },
  ]);

  const [connected, setConnected] = useState(false);

  useEffect(() => {
    // Connect to our Go WebSocket server
    const ws = new WebSocket('ws://localhost:8082/ws/leaderboard');
    
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (Array.isArray(data)) {
          setEntries(data);
        }
      } catch (e) {
        console.error("Failed to parse WS message", e);
      }
    };

    return () => ws.close();
  }, []);

  return (
    <div className="max-w-4xl mx-auto space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="text-4xl font-extrabold tracking-tight mb-2 flex items-center space-x-3">
            <Trophy className="w-10 h-10 text-yellow-500" />
            <span>Global Leaderboard</span>
          </h1>
          <p className="text-muted-foreground">Ranked by Composite Score (Correctness / Cycles)</p>
        </div>
        <div className="flex items-center space-x-2 text-sm">
          <div className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`} />
          <span className="text-muted-foreground">{connected ? 'Live Updates' : 'Disconnected'}</span>
        </div>
      </div>

      <div className="bg-card rounded-2xl border border-border/50 overflow-hidden shadow-2xl">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b border-border/50 bg-muted/30">
              <th className="py-4 px-6 font-semibold text-muted-foreground uppercase tracking-wider text-sm">Rank</th>
              <th className="py-4 px-6 font-semibold text-muted-foreground uppercase tracking-wider text-sm">Team</th>
              <th className="py-4 px-6 font-semibold text-muted-foreground uppercase tracking-wider text-sm text-right">Composite Score</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {entries.map((entry) => (
              <tr key={entry.team} className="group hover:bg-muted/10 transition-colors">
                <td className="py-4 px-6">
                  <div className="flex items-center space-x-2">
                    {entry.rank === 1 && <Medal className="w-5 h-5 text-yellow-500" />}
                    {entry.rank === 2 && <Medal className="w-5 h-5 text-gray-400" />}
                    {entry.rank === 3 && <Medal className="w-5 h-5 text-amber-700" />}
                    <span className={`font-mono ${entry.rank <= 3 ? 'font-bold' : 'text-muted-foreground'}`}>
                      #{entry.rank}
                    </span>
                  </div>
                </td>
                <td className="py-4 px-6 font-medium text-lg">
                  {entry.team}
                </td>
                <td className="py-4 px-6 text-right">
                  <div className="flex items-center justify-end space-x-2">
                    <span className="font-mono text-xl text-primary font-bold">{entry.score.toLocaleString()}</span>
                    <TrendingUp className="w-4 h-4 text-green-500 opacity-0 group-hover:opacity-100 transition-opacity" />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
