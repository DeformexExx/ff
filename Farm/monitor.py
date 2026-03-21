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
        V6.0 Stable: Advanced root-level monitoring via /proc FS.
        """
        # Suffix extracion: clienb -> b, clienc -> c
        suffix = clone_name[-1] if clone_name.startswith("clien") else clone_name
        
        # 1. PID Discovery (V6.0 confirmed awk chain)
        cmd_pid = f"su -c \"ps -ef | grep com.roblox.clien{suffix} | grep -v grep | awk '{{print $2}}'\""
        ret, stdout_pid, _ = await run_bash(cmd_pid)
        
        # Robust validation: check for error strings or multiple lines
        raw_pid = stdout_pid.strip()
        if any(err in raw_pid for err in ["rooting", "No such", "denied", "found"]):
            return "Offline"
            
        pid = raw_pid.split('\n')[0] if raw_pid else ""
        if not pid or not pid.isdigit():
            return "Offline"

        try:
            # 2. Thread Counting (V6.0 Kernel confirmed)
            cmd_thr = f"su -c \"ls /proc/{pid}/task | wc -l\""
            _, stdout_thr, _ = await run_bash(cmd_thr)
            
            raw_thr = stdout_thr.strip()
            if any(err in raw_thr for err in ["rooting", "No such", "denied"]):
                threads = 0
            else:
                try:
                    threads = int(raw_thr)
                except ValueError:
                    threads = 0

            # 3. Memory & CPU (Still useful for UI, but subordinated to new discovery)
            # We use the discovered PID to get VmRSS if it still exists
            cmd_st = f"su -c \"cat /proc/{pid}/status | grep VmRSS\""
            _, stdout_st, _ = await run_bash(cmd_st)
            
            mem = "?"
            if "VmRSS:" in stdout_st:
                p = stdout_st.split()
                if len(p) >= 2: mem = f"{int(p[1])//1024}MB"
                                
            return f"Mem: {mem} | Thr: {threads}"
        except Exception as e:
            logger.error(f"V6.0 Monitor Error: {e}")
            return "Stats Error"
