# -*- coding: utf-8 -*-
import os
import logging
from bash_utils import run_bash

logger = logging.getLogger("MonitorEngine")

class MonitorEngine:
    @staticmethod
    async def get_system_stats() -> tuple[str, str, str]:
        """Возвращает (RAM_str, CPU_str, TEMP_str)"""
        ram, cpu, temp = "N/A", "N/A", "N/A"
        
        try:
            import psutil
            mem = psutil.virtual_memory()
            ram = f"{mem.percent}%"
            cpu = f"{psutil.cpu_percent()}%"
        except ImportError:
            # Fallback if psutil is missing
            pass
            
        try:
            paths = ["/sys/class/thermal/thermal_zone0/temp", "/sys/class/thermal/thermal_zone1/temp"]
            for path in paths:
                if os.path.exists(path):
                    with open(path, "r") as f:
                        temp = f"{int(int(f.read()) / 1000)}°C"
                    break
        except Exception:
            pass
            
        return ram, cpu, temp

    @staticmethod
    async def get_clone_status(clone_name: str) -> str:
        """
        V5.7 Kernel Sight: Checks for clone using root-level ps chain.
        """
        # Optimized PID discovery (V5.7 awk chain)
        # Suffix is extracted from com.roblox.clien[suffix]
        suffix = clone_name[-1] if clone_name.startswith("clien") else clone_name
        cmd_pid = f"su -c \"ps -ef | grep com.roblox.clien{suffix} | grep -v grep | awk '{{print $2}}'\""
        ret, stdout_pid, _ = await run_bash(cmd_pid)
        pid = stdout_pid.strip()
        
        if pid:
            try:
                # Kernel-level threads and memory (V5.7)
                cmd_st = f"su -c \"cat /proc/{pid}/status | grep -E '(VmRSS|Threads)'\""
                _, stdout_st, _ = await run_bash(cmd_st)
                
                cmd_stat = f"su -c \"cat /proc/{pid}/stat\""
                _, stdout_stat, _ = await run_bash(cmd_stat)
                
                cpu_ticks = "0"
                if stdout_stat:
                    parts = stdout_stat.split()
                    if len(parts) >= 15:
                        cpu_ticks = str(int(parts[13]) + int(parts[14]))
                
                threads, mem = "?", "?"
                for line in stdout_st.split('\n'):
                    line = line.strip()
                    if line.startswith('VmRSS:'):
                        p = line.split()
                        if len(p) >= 2: mem = f"{int(p[1])//1024}MB"
                    elif line.startswith('Threads:'):
                        p = line.split()
                        if len(p) >= 2: threads = p[1]
                                
                return f"Mem: {mem} | Thr: {threads} | CpuTicks: {cpu_ticks}"
            except Exception as e:
                logger.error(f"V5.7 Monitor Error: {e}")
                return "Stats Error"
        return "Offline"
