# -*- coding: utf-8 -*-
# Aegis MonitorEngine — V9.0
import os
import re
import logging
from typing import Optional
from bash_utils import run_bash

logger = logging.getLogger("MonitorEngine")


async def clone_pgrep_alive(suffix: str) -> bool:
    """
    Liveness check: pgrep -f com.roblox.clien{suffix} found a PID.
    Tries su first, then plain pgrep (Termux fallback).
    """
    pkg = f"com.roblox.clien{suffix}"
    ret, out, _ = await run_bash(f"su -c 'pgrep -f {pkg}'")
    if ret == 0 and out.strip():
        return True
    ret2, out2, _ = await run_bash(f"pgrep -f {pkg}")
    return ret2 == 0 and bool(out2.strip())


class MonitorEngine:
    """Triple-check monitoring engine for Aegis Roblox farm."""

    # ── static alias so both `clone_pgrep_alive(sfx)` and
    #    `MonitorEngine.clone_pgrep_alive(sfx)` work everywhere ──────────────
    clone_pgrep_alive = staticmethod(clone_pgrep_alive)

    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def get_system_stats() -> tuple:
        """Returns (RAM_str, CPU_str, TEMP_str)."""
        ram, cpu, temp = "N/A", "N/A", "N/A"

        try:
            import psutil
            mem = psutil.virtual_memory()
            ram = f"{mem.percent:.0f}%"
            cpu = f"{psutil.cpu_percent(interval=None):.0f}%"
        except ImportError:
            pass

        try:
            paths = [
                "/sys/class/thermal/thermal_zone0/temp",
                "/sys/class/thermal/thermal_zone1/temp",
            ]
            for path in paths:
                if os.path.exists(path):
                    with open(path, "r") as f:
                        temp = f"{int(int(f.read()) / 1000)}°C"
                    break
        except Exception:
            pass

        return ram, cpu, temp

    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def get_pid(suffix: str) -> Optional[str]:
        """
        Strict PID discovery: ps -A | grep | grep -v grep.
        Returns string PID or None.
        """
        cmd = f"su -c 'ps -A | grep com.roblox.clien{suffix} | grep -v grep'"
        _, stdout, _ = await run_bash(cmd)
        output = stdout.strip()
        if output:
            try:
                return output.split()[1]
            except IndexError:
                pass
        return None

    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def get_threads(pid: str) -> int:
        """
        Accurate thread count via /proc/{pid}/task.
        Returns 0 on any failure.
        """
        if not pid or not str(pid).isdigit():
            return 0
        _, stdout, _ = await run_bash(f"su -c \"ls /proc/{pid}/task | wc -l\"")
        raw = stdout.strip()
        if not raw or any(e in raw.lower() for e in ("rooting", "no such", "denied")):
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0

    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def get_clone_cpu_percent(suffix: str) -> float:
        """
        CPU % for com.roblox.clien{suffix}.

        Strategy:
          1. top -n 1  (Android/Termux — no -b flag)
          2. grep the package line and extract the CPU column.
          3. /proc/{pid}/stat fallback (utime+stime delta) if top gives nothing.

        Returns -1.0 if process not found or any error.
        """
        pkg = f"com.roblox.clien{suffix}"

        # ── attempt 1: top (Android does NOT support -b) ─────────────────────
        _, top_out, _ = await run_bash(f"su -c \"top -n 1 | grep {pkg}\"")
        line = top_out.strip().splitlines()[0] if top_out.strip() else ""
        if line:
            # Android top output: PID USER PR NI ... %CPU %MEM ...
            # The CPU column position can vary; grab the first bare percentage.
            m = re.search(r"(\d+(?:\.\d+)?)%", line)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    pass

        # ── attempt 2: /proc/{pid}/stat delta ────────────────────────────────
        try:
            import time
            _, pg_out, _ = await run_bash(f"su -c 'pgrep -f {pkg}'")
            pid = pg_out.strip().splitlines()[0].strip() if pg_out.strip() else ""
            if not pid or not pid.isdigit():
                return -1.0

            def _read_stat(p: str) -> Optional[int]:
                try:
                    with open(f"/proc/{p}/stat", "r") as f:
                        fields = f.read().split()
                    # utime = fields[13], stime = fields[14]
                    return int(fields[13]) + int(fields[14])
                except Exception:
                    return None

            t0 = time.monotonic()
            ticks0 = _read_stat(pid)
            if ticks0 is None:
                return -1.0

            import asyncio
            await asyncio.sleep(0.5)

            t1 = time.monotonic()
            ticks1 = _read_stat(pid)
            if ticks1 is None:
                return -1.0

            hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
            elapsed = t1 - t0
            cpu = ((ticks1 - ticks0) / hz) / elapsed * 100.0
            return round(max(cpu, 0.0), 1)

        except Exception as e:
            logger.debug(f"get_clone_cpu_percent fallback error [{suffix}]: {e}")
            return -1.0

    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def get_clone_status(clone_name: str) -> str:
        """
        Full status string: 'Mem: XMB | Thr: YYY'
        Returns 'Offline' when process is not found.
        """
        suffix = clone_name[-1] if clone_name.lower().startswith("clien") else clone_name
        pid = await MonitorEngine.get_pid(suffix)

        if not pid:
            return "Offline"

        try:
            threads = await MonitorEngine.get_threads(pid)

            _, stdout_st, _ = await run_bash(
                f"su -c \"cat /proc/{pid}/status | grep VmRSS\""
            )
            mem = "?"
            if "VmRSS:" in stdout_st:
                parts = stdout_st.split()
                if len(parts) >= 2:
                    try:
                        mem = f"{int(parts[1]) // 1024}MB"
                    except ValueError:
                        pass

            return f"Mem: {mem} | Thr: {threads}"
        except Exception as e:
            logger.error(f"get_clone_status error [{clone_name}]: {e}")
            return "Stats Error"
