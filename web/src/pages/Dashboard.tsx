import { useState } from 'react';
import Editor from '@monaco-editor/react';
import { Play, Loader2, Activity, Cpu, Gauge, HardDrive, Network, Zap } from 'lucide-react';

const DEFAULT_CODE = `import emu.network as network
import json

s = network.Socket(network.AF_INET, network.SOCK_STREAM)
s.setsockopt(network.SOL_SOCKET, network.SO_REUSEADDR, 1)
s.bind(('0.0.0.0', 8080))
s.listen(1)
conn, addr = s.accept()

buffer = ""
while True:
    data = conn.recv(1024)
    if not data:
        break
    buffer += data.decode('utf-8')
    while "\\n" in buffer:
        line, buffer = buffer.split("\\n", 1)
        if line.strip():
            order = json.loads(line)
            trade = {
                "after_sequence": order.get("sequence"),
                "taker_id": order.get("order_id"),
                "maker_id": "MOCK",
                "price": order.get("price", "100"),
                "quantity": order.get("quantity", "10"),
                "taker_side": order.get("side", "BUY")
            }
            conn.sendall((json.dumps(trade) + "\\n").encode('utf-8'))
conn.close()
`;

function formatNs(ns: number): string {
  if (ns >= 1_000_000) return (ns / 1_000_000).toFixed(2) + ' ms';
  if (ns >= 1_000) return (ns / 1_000).toFixed(2) + ' μs';
  return Math.round(ns) + ' ns';
}

export default function Dashboard() {
  const [code, setCode] = useState(DEFAULT_CODE);
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [statusMessage, setStatusMessage] = useState<string>('');
  const [teamName, setTeamName] = useState('AlphaTeam');
  const [teamOtp, setTeamOtp] = useState('IICPC-9482');

  const handleRun = async () => {
    setIsRunning(true);
    setResult(null);
    setStatusMessage('Starting Pyodide Worker...');

    try {
      const worker = new Worker(new URL('../workers/pyodide.worker.ts', import.meta.url), {
        type: 'module'
      });

      worker.onmessage = async (e) => {
        const data = e.data;
        if (data.type === 'status') {
          setStatusMessage(data.message);
        } else if (data.type === 'result') {
          setResult(data.result);
          setStatusMessage('Complete. Submitting to Leaderboard...');

          try {
            await fetch('http://localhost:8081/api/submit', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                payload: data.payload,
                signature: data.signature
              }),
            });
            setStatusMessage('Leaderboard Updated!');
          } catch (err) {
            console.error("Failed to submit to leaderboard", err);
            setStatusMessage('Execution successful, but Leaderboard sync failed.');
          }

          setIsRunning(false);
          worker.terminate();
        } else if (data.type === 'error') {
          setResult({ success: false, error: data.error });
          setIsRunning(false);
          worker.terminate();
        }
      };

      worker.postMessage({
        code: code,
        scenario: 'warmup',
        team: teamName,
        otp: teamOtp
      });

    } catch (err: any) {
      setResult({ success: false, error: err.message });
      setIsRunning(false);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)]">
      <div className="flex justify-between items-center mb-4">
        <div>
          <h1 className="text-2xl font-bold">Benchmark IDE</h1>
          <p className="text-muted-foreground text-sm">Write your Python matching engine. Test against the warmup scenario.</p>
        </div>
      </div>

      {/* Team Authentication Card */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6 p-4 rounded-xl border border-border/50 bg-card/30 backdrop-blur-sm">
        <div className="flex flex-col sm:flex-row items-center gap-4 flex-1">
          <div className="flex flex-col w-full sm:w-auto">
            <label className="text-[10px] uppercase font-bold text-muted-foreground mb-1 tracking-wider">Team Name</label>
            <input
              type="text"
              value={teamName}
              onChange={(e) => setTeamName(e.target.value)}
              className="bg-background border border-border/50 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-primary/50 text-foreground w-full sm:w-48 font-semibold"
              placeholder="e.g. AlphaTeam"
            />
          </div>
          <div className="flex flex-col w-full sm:w-auto">
            <label className="text-[10px] uppercase font-bold text-muted-foreground mb-1 tracking-wider">Auth OTP</label>
            <input
              type="text"
              value={teamOtp}
              onChange={(e) => setTeamOtp(e.target.value)}
              className="bg-background border border-border/50 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-primary/50 text-foreground w-full sm:w-48 font-mono"
              placeholder="IICPC-XXXX"
            />
          </div>
        </div>
        <div className="flex items-end h-full">
          <button
            onClick={handleRun}
            disabled={isRunning}
            className="flex items-center space-x-2 bg-primary text-primary-foreground px-6 py-2 rounded-lg font-medium hover:bg-primary/90 transition-colors disabled:opacity-50 w-full md:w-auto justify-center"
          >
            {isRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4 fill-current" />}
            <span>{isRunning ? 'Benchmarking...' : 'Run Benchmark'}</span>
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-1 min-h-0">
        {/* Editor Pane */}
        <div className="lg:col-span-2 rounded-xl border border-border/50 overflow-hidden shadow-2xl flex flex-col bg-[#1e1e1e]">
          <div className="bg-muted/30 px-4 py-2 border-b border-border/50 text-xs font-mono text-muted-foreground flex items-center space-x-2">
            <div className="w-2 h-2 rounded-full bg-red-500" />
            <div className="w-2 h-2 rounded-full bg-yellow-500" />
            <div className="w-2 h-2 rounded-full bg-green-500" />
            <span className="ml-2">engine.py</span>
          </div>
          <Editor
            height="100%"
            defaultLanguage="python"
            theme="vs-dark"
            value={code}
            onChange={(val) => setCode(val || '')}
            options={{
              minimap: { enabled: false },
              fontSize: 14,
              fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
              padding: { top: 16 },
            }}
          />
        </div>

        {/* Results Pane */}
        <div className="rounded-xl border border-border/50 bg-card p-6 overflow-y-auto flex flex-col shadow-xl">
          <h3 className="font-semibold mb-4 flex items-center space-x-2">
            <Activity className="w-5 h-5 text-primary" />
            <span>Execution Results</span>
          </h3>

          {!result && !isRunning && (
            <div className="flex-1 flex flex-col text-muted-foreground space-y-4">
              <div className="space-y-3">
                <div className="bg-muted/20 p-3 rounded-lg border border-border/50 text-sm">
                  <h4 className="font-bold text-foreground mb-1 text-xs uppercase tracking-wider">Sandbox API</h4>
                  <ul className="list-disc pl-4 space-y-1 text-xs">
                    <li><code className="text-primary">emu.network.Socket</code> — Fragmented network I/O</li>
                    <li><code className="text-primary">emu.multiprocessing.Pool</code> — 4 virtual cores</li>
                    <li>Allowed: <code>json</code>, <code>math</code>, <code>struct</code>, <code>typing</code></li>
                  </ul>
                </div>
                <div className="bg-muted/20 p-3 rounded-lg border border-border/50 text-sm">
                  <h4 className="font-bold text-foreground mb-1 text-xs uppercase tracking-wider">Hardware Spec</h4>
                  <ul className="list-disc pl-4 space-y-1 text-xs">
                    <li>3.0 GHz Virtual CPU · 4 Cores</li>
                    <li>512 MB Memory Limit</li>
                    <li>Graded: Correctness · Latency · Memory · Throughput · I/O</li>
                  </ul>
                </div>
              </div>
              <div className="flex-1 flex flex-col items-center justify-center opacity-50 pt-4">
                <Cpu className="w-10 h-10 mb-3" />
                <p className="text-xs text-center">Run your code to see all 5 macro evaluation metrics.</p>
              </div>
            </div>
          )}

          {isRunning && (
            <div className="flex-1 flex flex-col items-center justify-center text-primary space-y-4">
              <Loader2 className="w-8 h-8 animate-spin" />
              <p className="text-sm animate-pulse">{statusMessage}</p>
            </div>
          )}

          {result && (
            <div className="space-y-4 animate-in slide-in-from-right-4 fade-in duration-500">
              {result.success ? (
                <>
                  <div className="p-3 rounded-lg bg-green-500/10 border border-green-500/20">
                    <p className="text-green-500 font-medium text-sm">Execution Successful</p>
                  </div>

                  {/* Composite Score */}
                  <div className="p-4 rounded-lg bg-gradient-to-r from-primary/20 to-blue-500/20 border border-primary/30 text-center">
                    <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Composite Score</p>
                    <p className="text-3xl font-bold text-primary">{Math.round(result.score.composite_score).toLocaleString()}</p>
                  </div>

                  {/* 1. Correctness */}
                  <div className="p-3 rounded-lg bg-muted/20 border border-border/50">
                    <div className="flex items-center space-x-2 mb-2">
                      <Gauge className="w-4 h-4 text-green-400" />
                      <p className="text-xs font-bold uppercase tracking-wider">Correctness</p>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-center">
                      <div>
                        <p className="text-lg font-bold">{(result.score.correctness_score * 100).toFixed(1)}%</p>
                        <p className="text-[10px] text-muted-foreground">Accuracy</p>
                      </div>
                      <div>
                        <p className="text-lg font-mono">{result.score.total_trades_actual}</p>
                        <p className="text-[10px] text-muted-foreground">Actual</p>
                      </div>
                      <div>
                        <p className="text-lg font-mono">{result.score.total_trades_expected}</p>
                        <p className="text-[10px] text-muted-foreground">Expected</p>
                      </div>
                    </div>
                  </div>

                  {/* 2. Latency */}
                  <div className="p-3 rounded-lg bg-muted/20 border border-border/50">
                    <div className="flex items-center space-x-2 mb-2">
                      <Zap className="w-4 h-4 text-yellow-400" />
                      <p className="text-xs font-bold uppercase tracking-wider">Latency</p>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-center">
                      <div>
                        <p className="text-sm font-mono">{formatNs(result.score.avg_latency_ns)}</p>
                        <p className="text-[10px] text-muted-foreground">Average</p>
                      </div>
                      <div>
                        <p className="text-sm font-mono">{formatNs(result.score.p95_latency_ns)}</p>
                        <p className="text-[10px] text-muted-foreground">p95</p>
                      </div>
                      <div>
                        <p className="text-sm font-mono">{formatNs(result.score.p99_latency_ns)}</p>
                        <p className="text-[10px] text-muted-foreground">p99</p>
                      </div>
                    </div>
                  </div>

                  {/* 3. Memory Profile */}
                  <div className="p-3 rounded-lg bg-muted/20 border border-border/50">
                    <div className="flex items-center space-x-2 mb-2">
                      <HardDrive className="w-4 h-4 text-purple-400" />
                      <p className="text-xs font-bold uppercase tracking-wider">Memory Profile</p>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-center">
                      <div>
                        <p className="text-sm font-mono">{result.score.memory_p50_mb.toFixed(2)}</p>
                        <p className="text-[10px] text-muted-foreground">p50 MB</p>
                      </div>
                      <div>
                        <p className="text-sm font-mono">{result.score.memory_p95_mb.toFixed(2)}</p>
                        <p className="text-[10px] text-muted-foreground">p95 MB</p>
                      </div>
                      <div>
                        <p className="text-sm font-mono">{result.score.memory_p99_mb.toFixed(2)}</p>
                        <p className="text-[10px] text-muted-foreground">p99 MB</p>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-center mt-2">
                      <div>
                        <p className="text-sm font-mono">{result.score.memory_volatility.toFixed(3)}</p>
                        <p className="text-[10px] text-muted-foreground">Volatility (σ)</p>
                      </div>
                      <div>
                        <p className="text-sm font-mono">{result.score.peak_memory_mb.toFixed(2)}</p>
                        <p className="text-[10px] text-muted-foreground">Peak MB</p>
                      </div>
                    </div>
                  </div>

                  {/* 4. Throughput */}
                  <div className="p-3 rounded-lg bg-muted/20 border border-border/50">
                    <div className="flex items-center space-x-2 mb-2">
                      <Activity className="w-4 h-4 text-blue-400" />
                      <p className="text-xs font-bold uppercase tracking-wider">Throughput</p>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-center">
                      <div>
                        <p className="text-sm font-mono">{Math.round(result.score.throughput_ops).toLocaleString()}</p>
                        <p className="text-[10px] text-muted-foreground">Ops/sec</p>
                      </div>
                      <div>
                        <p className="text-sm font-mono">{result.score.throughput_degradation.toFixed(2)}×</p>
                        <p className="text-[10px] text-muted-foreground">Degradation</p>
                      </div>
                    </div>
                  </div>

                  {/* 5. I/O Efficiency */}
                  <div className="p-3 rounded-lg bg-muted/20 border border-border/50">
                    <div className="flex items-center space-x-2 mb-2">
                      <Network className="w-4 h-4 text-cyan-400" />
                      <p className="text-xs font-bold uppercase tracking-wider">I/O Efficiency</p>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-center">
                      <div>
                        <p className="text-sm font-mono">{result.score.io_recv_calls.toLocaleString()}</p>
                        <p className="text-[10px] text-muted-foreground">recv() calls</p>
                      </div>
                      <div>
                        <p className="text-sm font-mono">{result.score.io_bytes_per_recv.toFixed(1)} B</p>
                        <p className="text-[10px] text-muted-foreground">Bytes/recv</p>
                      </div>
                    </div>
                  </div>

                  {/* Virtual Cycles */}
                  <div className="p-2 rounded-lg bg-muted/10 text-center">
                    <p className="text-xs text-muted-foreground">
                      {result.score.virtual_cycles.toLocaleString()} virtual cycles · {formatNs(result.score.virtual_cycles / 3.0)}
                    </p>
                  </div>
                </>
              ) : (
                <div className="p-4 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive">
                  <p className="font-medium mb-2">Execution Failed</p>
                  <pre className="text-xs font-mono whitespace-pre-wrap">{result.error}</pre>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
