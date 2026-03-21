# -*- coding: utf-8 -*-
import os
import logging
from typing import Optional
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
    async def get_pid(suffix: str) -> Optional[str]:
        """
        V6.0 Final: Strict PID discovery via ps -A | grep | grep -v.
        """
        cmd = f"su -c 'ps -A | grep com.roblox.clien{suffix} | grep -v grep'"
        _, stdout, _ = await run_bash(cmd)
        
        output = stdout.strip()
        if output:
            try:
                # Берем вторую колонку (PID) из первой строки вывода
                pid = output.split()[1]
                return pid
            except IndexError:
                pass
        return None

    @staticmethod
    async def get_threads(pid: str) -> int:
        """
        V6.0 Final: Accurate thread count via /proc/{pid}/task.
        Returns 0 on any failure.
        """
        if not pid or not str(pid).isdigit():
            return 0
            
        cmd = f"su -c \"ls /proc/{pid}/task | wc -l\""
        _, stdout, _ = await run_bash(cmd)
        
        raw = stdout.strip()
        if not raw or any(err in raw.lower() for err in ["rooting", "no such", "denied"]):
            return 0
            
        try:
            return int(raw)
        except ValueError:
            return 0

    @staticmethod
    async def get_clone_status(clone_name: str) -> str:
        """
        V6.0 Stable: Advanced root-level monitoring via /proc FS.
        """
        suffix = clone_name[-1] if clone_name.startswith("clien") else clone_name
        pid = await MonitorEngine.get_pid(suffix)
        
        if not pid:
            return "Offline"

        try:
            threads = await MonitorEngine.get_threads(pid)
            
            # Memory check (VmRSS)
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
