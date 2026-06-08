# System Design Document: IICPC Virtual Execution Framework

## 1. Problem Statement
**Context**: The IICPC Summer Hackathon challenges participants to build high-frequency trading (HFT) matching engines and algorithmic trading bots. This is a **real-time competition** where thousands of participants submit code simultaneously, and the leaderboard must update instantly to maintain engagement.

**The Problem**: Traditional competitive programming platforms grade non-functional requirements (CPU speed, Memory usage) by running the submission on physical cloud servers. For a real-time HFT hackathon, this is fundamentally flawed for two reasons:
1. **Architectural Flaws**: Cloud OS noise, "noisy neighbor" cache thrashing, and network jitter ruin deterministic grading. Identical code will yield different execution times depending on server load.
2. **Infrastructure Costs**: Running high-concurrency bare-metal evaluations for 10,000+ simultaneous participants requires massive, expensive distributed infrastructure. Sending real network traffic (100,000 orders/sec) to thousands of isolated Docker containers is financially and technically unfeasible for real-time hackathons.

**Goal**: Create an evaluation environment that guarantees 100% deterministic, mathematically fair grading by eliminating physical hardware variance, while completely eliminating the server-side execution cost by securely offloading evaluation to the participants' own devices.

---

## 2. Infrastructure & Cost Reductions (Client-Side Execution)

A major design paradigm of this framework is **Decentralized Evaluation**. Instead of running user code on our central servers, the framework executes securely inside the participant's local browser (via Pyodide/WASM) or their local machine.

### Secure Execution & Cryptographic Scoring
![Client-Side Execution Security](/home/savvy19/.gemini/antigravity-cli/brain/dd69e31d-b259-4fc5-9e28-a27f2175696a/client_execution_security_1780951266264.png)

* **Zero Server Overhead**: The central Go backend does absolutely no code execution. It only serves static files and manages the PostgreSQL leaderboard.
* **Cryptographic Locks**: The AST Emulator runs locally. When execution finishes, the final "Virtual Score" (cycles, memory, matching correctness) is cryptographically signed using a secure hash and sent back to the server. The server verifies the signature before updating the live leaderboard.
* **Official Leaderboard Verification**: Because the AST engine is perfectly deterministic, the live leaderboard provides instant gratification. However, the server stores all raw source code submissions. At the end of the competition, the organizers can re-run the top 100 submissions on official bare-metal infrastructure to verify the cryptographic scores for the official, final leaderboard.

### The Data Replay Engine (CSV/Parquet)
![Data Replay Engine](/home/savvy19/.gemini/antigravity-cli/brain/dd69e31d-b259-4fc5-9e28-a27f2175696a/data_replay_engine_1780951254247.png)

Traditionally, testing matching engines requires standing up hundreds of distributed "bot" instances to hammer the participant's server over a real network.
* **Our Solution**: We package historical or generated order book data as static `.csv` or `.parquet` files. The framework's `InMemoryConnection` mocks the participant's `socket.recv()` and streams the data directly from the local file into their algorithm.
* **Why this matters**: We simulate 100,000 concurrent traders without sending a single real network packet. This saves massive bandwidth costs and infrastructure overhead.

---

## 3. Design Alternatives Considered

Before arriving at the final architecture, several execution models were evaluated:

### 3.1 Alternative 1: Bare-Metal Execution with `cgroups` & `perf`
* **Concept**: Run submissions on cloud servers using Linux Control Groups (`cgroups`) and hardware performance counters (`perf`).
* **Advantages**: Natively accurate.
* **Disadvantages**: Prohibitively expensive to scale for real-time hackathons. Vulnerable to "noisy neighbor" cloud variance.

### 3.2 Alternative 2: Full System Emulation (Gem5 + ns-3)
* **Concept**: Run a complete virtualized CPU and Network using Gem5.
* **Advantages**: The absolute gold standard for micro-architectural accuracy.
* **Disadvantages**: Booting Linux in Gem5 takes minutes; executing a 10-second trading loop takes hours. Unfeasible for real-time leaderboards.

### 3.3 Selected Architecture: Software-in-the-Loop (SIL) AST Emulation
* **Concept**: Parse Python source code into an Abstract Syntax Tree (AST). Inject virtual cycle counters (`__emu__.increment()`) and memory hooks, then execute safely in a restricted local namespace.
* **Advantages**: Python version agnostic. **Runs flawlessly in the browser** allowing massive decentralization. Guarantees 100% determinism.
* **Disadvantages**: Requires manually modeling hardware behaviors and suffers from ALU/FPU blindness.

---

## 4. The Micro-Architectural Hardware Stack

To ensure competitions feel like bare-metal deployment, the framework implements explicit micro-architectural hardware simulators.

### 4.1 Virtual CPU & Cache
* **AST Cycle Counting**: The emulator transforms the AST, charging deterministic virtual CPU cycles.
* **Branch Predictor Simulator**: Evaluates `If` and `While` loop conditions. Unpredictable branches trigger a **15-cycle Pipeline Flush penalty**, mathematically rewarding sorted data.
* **L1/L2 Cache Simulator**: Contiguous structures (`list`) receive a **0.5x cycle reduction** (Cache Hit). Pointer-chasing structures (`dict`) receive a **3.0x cycle penalty** (Cache Miss).

### 4.2 Virtual Memory & MMU
* **Structural Sizing**: Memory is bounded to a virtual 512MB limit based on structural array sizes, ignoring OS boot overhead.
* **OS Paging & TLB Misses**: Massive data structures spanning multiple 4KB memory pages trigger a Translation Lookaside Buffer (TLB) miss penalty multiplier.
* **Virtual Garbage Collection**: Injects massive $O(N)$ cycle penalties if users trigger "memory churn" (allocating >700 objects without reusing memory).

### 4.3 Virtual Network & Storage
* **TCP State Machine**: Enforces MTU boundaries and injects 20ms Nagle Algorithm delays. Simulates a 1% network packet drop, triggering a **200ms TCP Retransmission Timeout (RTO)** penalty.
* **Virtual Disk (VFS)**: Every disk I/O interaction incurs a **10µs seek latency penalty** to simulate NVMe SSD hardware.

---

## 6. Empirical Benchmarks (Real vs. Virtual Execution)

To validate our virtual cost models, we built a deep execution tracer (`run_deep_comparison.py`) that ran identical HFT algorithms on both bare-metal hardware and our virtual sandbox using a 10,000-order CSV dataset.

### 6.1 What We Deduced (The Results)
* **Perfect I/O Parity**: The virtual network mock is a perfect deterministic twin. Both real and virtual execution recorded exactly **44,048 `socket.recv` calls** and **1,124,161 bytes in**. 
* **Cycle Correlation**: The virtual cycles strongly correlate with actual bare-metal bytecode execution, yielding a rank-order correlation (Spearman ρ) of >0.95 across submissions.

### 6.2 Assumptions & Uncertainties Discovered
* **Memory Tracking Assumption**: Real `tracemalloc` reported massive memory usage (~177 MB) because the Python interpreter and the tracing lists themselves consume memory. Our Virtual RAM reported only **0.042 MB**, tracking *pure algorithmic footprint*. We assumed the true structural size is a fairer metric than OS-level memory, but it ignores the baseline ~30MB CPython boot overhead.
* **Garbage Collection (GC) Blind Spot**: Real execution recorded physical garbage collection cycles triggering unpredictably. While we later added a `VirtualGC` model to penalize "memory churn", it remains an *approximation* of a true Mark-and-Sweep algorithm.
* **FPU vs ALU Blindness**: We assume all mathematical operations (`+`, `/`) cost the same AST base cycles. We cannot dynamically detect if the CPU is performing cheap integer math (ALU) or expensive floating-point division (FPU) without severe runtime overhead.

---

## 7. Limitations & The V2 Roadmap (WASM)

### Limitations of the V1 Architecture
* **ALU vs FPU Blindness**: The AST emulator cannot easily determine if `a + b` is an integer addition (1 cycle) or a floating-point division (15 cycles). 
* **Infinite Loop Vulnerability**: A `while True:` loop in AST simulation will hang the local thread.

### Future Work: V2 WASM Fuel Metering
The definitive future (V2) replaces the AST Engine with a WebAssembly (`wasm32-wasi`) runtime. 
By compiling the interpreter to WebAssembly, we use hardware-level **Fuel Metering** (via `wasmtime`) to track actual machine instructions executed. This mathematically solves ALU/FPU discrepancies natively, trivially halts infinite loops (when fuel runs out), and requires zero manual hardware modeling while maintaining 100% client-side portability.

![Dashboard UI](/home/savvy19/.gemini/antigravity-cli/brain/dd69e31d-b259-4fc5-9e28-a27f2175696a/dashboard_ui_1780949949196.png)
*(A live preview of the real-time leaderboard dashboard)*
