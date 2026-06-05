class Pool:
    """
    Virtual multiprocessing pool that emulates multi-core execution
    for algorithm benchmarking without real shared-memory multithreading.
    """
    def __init__(self, processes=4):
        self.processes = processes
        
    def map(self, func, iterable):
        import __emu__
        # Track total virtual cycles (time) spent by each virtual core
        core_times = [__emu__.virtual_cycles] * self.processes
        results = []
        
        for item in iterable:
            start_cycles = __emu__.virtual_cycles
            res = func(item)
            task_cost = __emu__.virtual_cycles - start_cycles
            
            # Find the earliest available core
            idx = core_times.index(min(core_times))
            core_times[idx] += task_cost
            
            # Rewind the global cycle counter for the next task
            __emu__.virtual_cycles = start_cycles
            results.append(res)
            
        # The total time taken for the parallel batch is the max core time
        __emu__.virtual_cycles = max(core_times)
        return results
