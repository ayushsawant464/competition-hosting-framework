import { Link } from 'react-router-dom';
import { ArrowRight, Cpu, Zap, Shield } from 'lucide-react';

export default function Landing() {
  return (
    <div className="flex flex-col items-center justify-center space-y-24 py-20 animate-in fade-in slide-in-from-bottom-8 duration-1000">
      
      {/* Hero Section */}
      <section className="text-center space-y-8 max-w-4xl mx-auto">
        <h1 className="text-5xl md:text-7xl font-extrabold tracking-tight">
          Algorithmic Trading{' '}
          <span className="bg-clip-text text-transparent bg-gradient-to-r from-primary to-blue-500">
            Reimagined.
          </span>
        </h1>
        <p className="text-xl text-muted-foreground max-w-2xl mx-auto leading-relaxed">
          Welcome to the IICPC Summer Hackathon 2026. Build the fastest, most efficient matching engine. 
          Compete against thousands in a fully deterministic, hardware-agnostic emulation arena.
        </p>
        <div className="flex items-center justify-center space-x-6 pt-4">
          <Link
            to="/dashboard"
            className="group flex items-center space-x-2 bg-primary text-primary-foreground px-8 py-4 rounded-full font-semibold transition-all hover:scale-105 hover:shadow-[0_0_40px_-10px_rgba(59,130,246,0.5)]"
          >
            <span>Enter the Arena</span>
            <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
          </Link>
          <Link
            to="/leaderboard"
            className="flex items-center space-x-2 px-8 py-4 rounded-full font-semibold border border-border hover:bg-white/5 transition-colors"
          >
            <span>View Leaderboard</span>
          </Link>
        </div>
      </section>

      {/* Feature Grid */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-8 w-full max-w-5xl">
        <div className="p-8 rounded-2xl bg-card border border-border/50 hover:border-primary/50 transition-colors group">
          <div className="w-12 h-12 bg-primary/10 rounded-xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
            <Cpu className="w-6 h-6 text-primary" />
          </div>
          <h3 className="text-xl font-bold mb-3">Deterministic Cycles</h3>
          <p className="text-muted-foreground leading-relaxed">
            Our custom AST-instrumentation tracks true algorithmic complexity. No hardware advantages. Fair competition.
          </p>
        </div>

        <div className="p-8 rounded-2xl bg-card border border-border/50 hover:border-blue-500/50 transition-colors group">
          <div className="w-12 h-12 bg-blue-500/10 rounded-xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
            <Zap className="w-6 h-6 text-blue-500" />
          </div>
          <h3 className="text-xl font-bold mb-3">Network Emulation</h3>
          <p className="text-muted-foreground leading-relaxed">
            Test your bot's resilience against simulated packet fragmentation, virtual jitter, and chaotic market data streams.
          </p>
        </div>

        <div className="p-8 rounded-2xl bg-card border border-border/50 hover:border-purple-500/50 transition-colors group">
          <div className="w-12 h-12 bg-purple-500/10 rounded-xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
            <Shield className="w-6 h-6 text-purple-500" />
          </div>
          <h3 className="text-xl font-bold mb-3">Sandboxed Execution</h3>
          <p className="text-muted-foreground mt-2">
            Run untrusted competitor code securely with our WebAssembly-powered Pyodide isolation and safe builtin enforcement.
          </p>
        </div>
      </section>

    </div>
  );
}
