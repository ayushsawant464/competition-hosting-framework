import { loadPyodide } from 'pyodide';

// Obfuscated Secret Key for HMAC (In a real app, hide this better via env vars or WASM)
const SECRET_KEY = "iicpc_super_secret_hackathon_key_2026";

async function signPayload(payload: any): Promise<string> {
  const encoder = new TextEncoder();
  const keyData = encoder.encode(SECRET_KEY);
  
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    keyData,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  
  const payloadString = JSON.stringify(payload);
  const signatureBuffer = await crypto.subtle.sign(
    "HMAC",
    cryptoKey,
    encoder.encode(payloadString)
  );
  
  const hashArray = Array.from(new Uint8Array(signatureBuffer));
  const hashHex = hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
  return hashHex;
}

let pyodideReadyPromise: Promise<any> | null = null;

async function initPyodide() {
  self.postMessage({ type: 'status', message: 'Downloading Pyodide runtime...' });
  const pyodide = await loadPyodide({
    indexURL: "https://cdn.jsdelivr.net/pyodide/v0.29.4/full/",
  });
  
  self.postMessage({ type: 'status', message: 'Fetching VFS configuration...' });
  const response = await fetch('/vfs.json');
  const vfs = await response.json();
  
  self.postMessage({ type: 'status', message: 'Mounting virtual file system...' });
  // Create directories and write files
  for (const [filepath, content] of Object.entries(vfs)) {
    const parts = filepath.split('/');
    let currentPath = '';
    for (let i = 0; i < parts.length - 1; i++) {
      currentPath += (i === 0 ? '' : '/') + parts[i];
      try {
        pyodide.FS.mkdir(currentPath);
      } catch (e) {
        // Directory might already exist, ignore
      }
    }
    pyodide.FS.writeFile(filepath, content);
  }
  
  // Create an empty submission.py
  pyodide.FS.writeFile('submission.py', '');
  
  self.postMessage({ type: 'status', message: 'Ready.' });
  return pyodide;
}

self.onmessage = async (event) => {
  const { code, scenario, team } = event.data;
  
  try {
    if (!pyodideReadyPromise) {
      pyodideReadyPromise = initPyodide();
    }
    const pyodide = await pyodideReadyPromise;
    
    self.postMessage({ type: 'status', message: 'Executing Sandbox...' });
    
    // Write user code to virtual file
    pyodide.FS.writeFile('submission.py', code);
    
    // The harnessCode is stored as a separate variable to avoid Vite parsing
    // the Python import/from statements as JS imports.
    const scenarioName = scenario;
    const harnessCode = [
      "import sys",
      "import json",
      "import traceback",
      "",
      "try:",
      "    from harness import BenchmarkHarness",
      "    harness = BenchmarkHarness(user_code_path='submission.py', scenario_dir='data')",
      "    score = harness.score_scenario('" + scenarioName + "')",
      "    result = {",
      '        "success": True,',
      '        "score": {',
      '            "correctness_score": score.correctness_score,',
      '            "total_trades_expected": score.total_trades_expected,',
      '            "total_trades_actual": score.total_trades_actual,',
      '            "memory_p50_mb": score.memory_p50_mb,',
      '            "memory_p95_mb": score.memory_p95_mb,',
      '            "memory_p99_mb": score.memory_p99_mb,',
      '            "memory_volatility": score.memory_volatility,',
      '            "memory_curve_spike": score.memory_curve_spike,',
      '            "avg_latency_ns": score.avg_latency_ns,',
      '            "p95_latency_ns": score.p95_latency_ns,',
      '            "p99_latency_ns": score.p99_latency_ns,',
      '            "throughput_ops": score.throughput_ops,',
      '            "throughput_degradation": score.throughput_degradation,',
      '            "io_recv_calls": score.io_recv_calls,',
      '            "io_bytes_per_recv": score.io_bytes_per_recv,',
      '            "virtual_cycles": score.virtual_cycles,',
      '            "peak_memory_mb": score.peak_memory_mb,',
      '            "composite_score": score.composite_score',
      "        }",
      "    }",
      "    result_json = json.dumps(result)",
      "except Exception as e:",
      "    result = {",
      '        "success": False,',
      '        "error": traceback.format_exc()',
      "    }",
      "    result_json = json.dumps(result)",
      "",
      "result_json",
    ].join("\n");
    
    const resultJson = await pyodide.runPythonAsync(harnessCode);
    const result = JSON.parse(resultJson);
    
    if (result.success) {
      self.postMessage({ type: 'status', message: 'Signing payload...' });
      
      const payload = {
        team: team || "Anonymous",
        otp: event.data.otp || "",
        scenario: scenario,
        score: result.score
      };
      
      const signature = await signPayload(payload);
      
      self.postMessage({
        type: 'result',
        payload: payload,
        signature: signature,
        result: result
      });
    } else {
      self.postMessage({
        type: 'error',
        error: result.error
      });
    }
    
  } catch (error: any) {
    self.postMessage({ type: 'error', error: error.message || error.toString() });
  }
};
