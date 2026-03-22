# -*- coding: utf-8 -*-
# Aegis MonitorEngine — V10.1 Ghost Killer
import os
import re
import logging
from typing import Optional, Tuple
from bash_utils import run_bash

logger = logging.getLogger("MonitorEngine")


async def clone_pgrep_alive(suffix: str) -> bool:
    """
    STRICT liveness check via ps -A.
    Process is considered alive only when:
      - It appears in ps -A for com.roblox.clien{suffix}
      - Its status column is NOT 'Z' (zombie)
    Falls back to plain pgrep if su fails.
    Groups commands into a single su -c call.
    """
    pkg = f"com.roblox.clien{suffix}"
    # Single grouped su call: ps -A + filter
    _, out, _ = await run_bash(
        f"su -c \"ps -A | grep {pkg} | grep -v grep\""
    )
    if out.strip():
        for line in out.strip().splitlines():
            cols = line.split()
            # ps -A columns: USER PID PPID VSZ RSS WCHAN ADDR S NAME
            # Status (S) column is index 7; if 'Z' → zombie
            if len(cols) >= 8:
                status_col = cols[7].upper()
                if "Z" not in status_col:
                    return True
            else:
                # Unexpected format — trust it's alive
                return True
    # Fallback: plain pgrep without su
    _, out2, _ = await run_bash(f"pgrep -f {pkg}")
    return bool(out2.strip())


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
    async def get_ram_free_gb() -> str:
        """
        Returns available RAM as string like '2.1 GB free'.
        Reads /proc/meminfo — works on Android without psutil.
        """
        try:
            with open("/proc/meminfo", "r") as f:
                content = f.read()
            # MemAvailable is most accurate; fall back to MemFree
            m = re.search(r"MemAvailable:\s+(\d+)\s+kB", content)
            if not m:
                m = re.search(r"MemFree:\s+(\d+)\s+kB", content)
            if m:
                kb = int(m.group(1))
                return f"{kb / 1024 / 1024:.1f} GB free"
        except Exception:
            pass
        try:
            import psutil
            mem = psutil.virtual_memory()
            return f"{mem.available / 1024 / 1024 / 1024:.1f} GB free"
        except Exception:
            pass
        return "N/A"

    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def get_pid(suffix: str) -> Optional[str]:
        """
        Strict PID: grouped single su call — pgrep + ps fallback in one shot.
        Returns non-zombie PID string or None.
        """
        pkg = f"com.roblox.clien{suffix}"
        # One su call: pgrep fast path
        _, pg_out, _ = await run_bash(
            f"su -c 'pgrep -f {pkg} | head -n 1'"
        )
        pid = pg_out.strip().splitlines()[0].strip() if pg_out.strip() else ""
        if pid and pid.isdigit():
            return pid
        # One su call: ps -A fallback, skip zombies
        _, ps_out, _ = await run_bash(
            f"su -c \"ps -A | grep {pkg} | grep -v grep\""
        )
        for line in ps_out.strip().splitlines():
            cols = line.split()
            if len(cols) >= 8 and "Z" not in cols[7].upper():
                try:
                    return cols[1]  # PID column
                except IndexError:
                    pass
        return None

    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def get_threads(pid: str) -> int:
        """
        Thread count — single su call reads status + task dir in one shot.
        Returns 0 on any failure.
        """
        if not pid or not str(pid).isdigit():
            return 0
        # Grouped: try Threads from /proc/status; pipe to grep
        _, stdout, _ = await run_bash(
            f"su -c \"grep Threads /proc/{pid}/status 2>/dev/null || ls /proc/{pid}/task 2>/dev/null | wc -l\""
        )
        raw = stdout.strip()
        if not raw:
            return 0
        # If result is "Threads:\t142" style
        m = re.search(r"Threads:\s*(\d+)", raw)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
        # Otherwise it's the wc -l count
        lines = raw.splitlines()
        last = lines[-1].strip()
        try:
            return int(last)
        except ValueError:
            return 0

    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def get_clone_cpu_percent(suffix: str, _prev_cache: dict = {}) -> float:
        """
        CPU % for com.roblox.clien{suffix}.

        Strategy (V10.2):
          1. PRIMARY: top -b -n 2 -p {PID} | tail -1  (2-pass average, most accurate)
             Android top may not support -b; if output is empty, falls through.
          2. SECONDARY: /proc/{pid}/stat utime+stime delta over 0.8s.
          3. STICKY: if both yield 0 — return last cached value (no UI flicker).

        Returns -1.0 only when process is dead/not found.
        """
        import time as _time
        import asyncio as _asyncio
        pkg = f"com.roblox.clien{suffix}"

        # ── Step 1: get PID ───────────────────────────────────────────────────
        _, pg_out, _ = await run_bash(f"su -c 'pgrep -f {pkg} | head -n 1'")
        pid = pg_out.strip().splitlines()[0].strip() if pg_out.strip() else ""
        if not pid or not pid.isdigit():
            _prev_cache.pop(suffix, None)
            return -1.0

        # ── Step 2 (PRIMARY): top -b -n 2 -p {PID} | tail -1 ─────────────────
        _, top2_out, _ = await run_bash(
            f"su -c \"top -b -n 2 -p {pid} 2>/dev/null | tail -1\""
        )
        top2_line = top2_out.strip()
        if top2_line:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", top2_line)
            if not m:
                # Some Android top omits %; grab first number in line
                m = re.search(r"\s(\d+(?:\.\d+)?)\s", top2_line)
            if m:
                try:
                    top_cpu = float(m.group(1))
                    if top_cpu > 0:
                        _prev_cache[suffix] = top_cpu
                        return top_cpu
                except ValueError:
                    pass

        # ── Step 3 (SECONDARY): /proc/stat delta ──────────────────────────────
        def _read_ticks(p: str) -> Optional[int]:
            try:
                with open(f"/proc/{p}/stat", "r") as f:
                    fields = f.read().split()
                return int(fields[13]) + int(fields[14])
            except Exception:
                return None

        t0 = _time.monotonic()
        ticks0 = _read_ticks(pid)
        if ticks0 is not None:
            await _asyncio.sleep(0.8)
            t1 = _time.monotonic()
            ticks1 = _read_ticks(pid)
            if ticks1 is not None:
                hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
                elapsed = t1 - t0
                stat_cpu = round(max(((ticks1 - ticks0) / hz) / elapsed * 100.0, 0.0), 1)
                if stat_cpu > 0:
                    _prev_cache[suffix] = stat_cpu
                    return stat_cpu

        # ── Step 4 (STICKY): keep last known — don't flash 0% in UI ──────────
        last = _prev_cache.get(suffix)
        if last is not None:
            return last

        return 0.0

    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def get_clone_stats(suffix: str) -> Tuple[bool, int, float]:
        """
        Single-call triple-check: returns (is_alive, threads, cpu_percent).
        Uses the manifest command:
          su -c "cat /proc/$(pgrep -f com.roblox.clien{suffix} | head -n 1)/status | grep Threads"
        cpu_percent is -1.0 when not measurable.
        """
        pkg = f"com.roblox.clien{suffix}"

        # ── liveness ──────────────────────────────────────────────────────────
        is_alive = await clone_pgrep_alive(suffix)
        if not is_alive:
            return False, 0, -1.0

        # ── threads via /proc/status Threads field ────────────────────────────
        threads = 0
        _, thr_out, _ = await run_bash(
            f"su -c \"cat /proc/$(pgrep -f {pkg} | head -n 1)/status | grep Threads\""
        )
        thr_raw = thr_out.strip()
        if thr_raw:
            m = re.search(r"Threads:\s*(\d+)", thr_raw)
            if m:
                try:
                    threads = int(m.group(1))
                except ValueError:
                    pass

        if not threads:
            # fallback: get PID then task dir
            pid = await MonitorEngine.get_pid(suffix)
            if pid:
                threads = await MonitorEngine.get_threads(pid)

        # ── CPU ───────────────────────────────────────────────────────────────
        cpu = await MonitorEngine.get_clone_cpu_percent(suffix)

        return True, threads, cpu

    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def get_clone_connections(suffix: str) -> int:
        """
        Count active TCP connections for com.roblox.clien{suffix}.
        Uses: su -c "netstat -ntp | grep com.roblox.clien{suffix} | wc -l"
        Falls back to ss if netstat is unavailable.
        Returns 0 on any error.
        """
        pkg = f"com.roblox.clien{suffix}"
        # Primary: netstat
        _, out, _ = await run_bash(
            f"su -c \"netstat -ntp 2>/dev/null | grep {pkg} | wc -l\""
        )
        raw = out.strip()
        try:
            n = int(raw)
            if n >= 0:
                return n
        except ValueError:
            pass
        # Fallback: ss (socket statistics)
        _, out2, _ = await run_bash(
            f"su -c \"ss -ntp 2>/dev/null | grep {pkg} | wc -l\""
        )
        raw2 = out2.strip()
        try:
            return int(raw2)
        except ValueError:
            return 0

    # V10.7 calibrated thresholds (observed: healthy=8-14 CON, zombie=3-5 CON)
    TCP_ACTIVE  = 8    # ≥8 → ACTIVE (normal game session)
    TCP_ZOMBIE  = 5    # ≤5 → ZOMBIE (frozen/crashed — triggers restart after 30s)

    @staticmethod
    def classify_connections(conns: int) -> str:
        """
        V10.7 TCP status thresholds (real-world calibrated):
        0        → IDLE    (not running)
        1-5      → ZOMBIE  (frozen/crashed, triggers restart)
        6-7      → LOADING (connecting to server)
        ≥8       → ACTIVE  (healthy in-game session, typical 8-14)
        """
        if conns >= MonitorEngine.TCP_ACTIVE:
            return "ACTIVE"
        if conns > MonitorEngine.TCP_ZOMBIE:
            return "LOADING"
        if conns > 0:
            return "ZOMBIE"
        return "IDLE"

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
