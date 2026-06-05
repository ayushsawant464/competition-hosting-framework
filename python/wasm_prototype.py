import wasmtime

def test_wasm_fuel_metering():
    print("="*60)
    print("IICPC V2: WASM Fuel Metering Proof of Concept")
    print("="*60)
    
    # Configure Wasmtime engine to consume fuel
    config = wasmtime.Config()
    config.consume_fuel = True
    engine = wasmtime.Engine(config)
    
    # 1. Algorithm A: O(N) Simple Loop (Simulating Cache-Friendly / Fast)
    # This WAT simply loops N times, adding 1 to a counter.
    wat_fast = """
    (module
      (func (export "run_fast") (param $n i32) (result i32)
        (local $i i32)
        (local $sum i32)
        (local.set $i (i32.const 0))
        (local.set $sum (i32.const 0))
        (block $end
          (loop $loop
            (br_if $end (i32.ge_s (local.get $i) (local.get $n)))
            (local.set $sum (i32.add (local.get $sum) (i32.const 1)))
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $loop)
          )
        )
        (local.get $sum)
      )
    )
    """
    
    # 2. Algorithm B: Heavy compute / Sub-optimal (Simulating Cache-Misses / Slow)
    # This WAT simulates a heavier operation by doing 10 internal additions per loop
    wat_slow = """
    (module
      (func (export "run_slow") (param $n i32) (result i32)
        (local $i i32)
        (local $sum i32)
        (local $j i32)
        (local.set $i (i32.const 0))
        (local.set $sum (i32.const 0))
        (block $end
          (loop $loop
            (br_if $end (i32.ge_s (local.get $i) (local.get $n)))
            
            ;; Sub-optimal inner loop (10 iterations)
            (local.set $j (i32.const 0))
            (block $inner_end
              (loop $inner_loop
                (br_if $inner_end (i32.ge_s (local.get $j) (i32.const 10)))
                (local.set $sum (i32.add (local.get $sum) (i32.const 1)))
                (local.set $j (i32.add (local.get $j) (i32.const 1)))
                (br $inner_loop)
              )
            )
            
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $loop)
          )
        )
        (local.get $sum)
      )
    )
    """
    
    # Compile the modules
    mod_fast = wasmtime.Module(engine, wat_fast)
    mod_slow = wasmtime.Module(engine, wat_slow)
    
    # Create stores with 10,000,000 Fuel (Cycles)
    store_fast = wasmtime.Store(engine)
    store_fast.set_fuel(10_000_000)
    
    store_slow = wasmtime.Store(engine)
    store_slow.set_fuel(10_000_000)
    
    # Instantiate modules
    instance_fast = wasmtime.Instance(store_fast, mod_fast, [])
    instance_slow = wasmtime.Instance(store_slow, mod_slow, [])
    
    run_fast = instance_fast.exports(store_fast)["run_fast"]
    run_slow = instance_slow.exports(store_slow)["run_slow"]
    
    # Test Question 1: Does Fuel Metering accurately track O(N) operations?
    print("\\n[Test 1] Testing O(N) execution for N=10,000...")
    n = 10_000
    
    # Run Fast
    fuel_before = store_fast.get_fuel()
    result_fast = run_fast(store_fast, n)
    fuel_after = store_fast.get_fuel()
    fuel_fast = fuel_before - fuel_after
    print(f"Algorithm A (Optimized)   | Result: {result_fast} | Fuel Consumed: {fuel_fast:,} cycles")
    
    # Run Slow
    fuel_before = store_slow.get_fuel()
    result_slow = run_slow(store_slow, n)
    fuel_after = store_slow.get_fuel()
    fuel_slow = fuel_before - fuel_after
    print(f"Algorithm B (Sub-optimal) | Result: {result_slow} | Fuel Consumed: {fuel_slow:,} cycles")
    
    # Assert correctness
    if fuel_slow > fuel_fast * 5:
        print("✓ SUCCESS: Fuel metering perfectly penalized the sub-optimal algorithm!")
    else:
        print("✗ FAILURE: Fuel metering failed to differentiate efficiency.")
        
    print("\\n[Test 2] What happens if code exceeds the Fuel Limit (Infinite Loop / Time Limit Exceeded)?")
    # Let's run the slow one for N = 1,000,000 which should exhaust the 10M fuel limit!
    n_huge = 1_000_000
    try:
        run_slow(store_slow, n_huge)
        print("✗ FAILURE: Code bypassed the fuel limit!")
    except wasmtime.Trap as e:
        print(f"✓ SUCCESS: Caught deterministic Time Limit Exceeded! Wasmtime Trap: {e}")

if __name__ == "__main__":
    test_wasm_fuel_metering()
