# -*- coding: utf-8 -*-
import os
import subprocess
import time

class MemoryManager:
    SWAP_PATH = "/data/local/tmp/swapfile"
    SWAP_SIZE_GB = 4

    @staticmethod
    def _run_su_detached(cmd):
        """Runs su command in detached mode to save RAM."""
        try:
            subprocess.Popen(
                f"su -c '{cmd}'",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setpgrp
            )
        except Exception:
            pass

    @staticmethod
    def setup_swap():
        """Check and setup 4GB swap file on /data/local/tmp."""
        try:
            if os.path.exists(MemoryManager.SWAP_PATH):
                print(f"Swap existing at {MemoryManager.SWAP_PATH}")
                return

            print(f"Creating {MemoryManager.SWAP_SIZE_GB}GB swap file...")
            steps = [
                f"dd if=/dev/zero of={MemoryManager.SWAP_PATH} bs=1M count={MemoryManager.SWAP_SIZE_GB * 1024}",
                f"chmod 600 {MemoryManager.SWAP_PATH}",
                f"mkswap {MemoryManager.SWAP_PATH}",
                f"swapon {MemoryManager.SWAP_PATH}"
            ]
            for step in steps:
                subprocess.run(f"su -c '{step}'", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1)
            print("Swap setup complete.")
        except Exception as e:
            print(f"Swap error: {e}")

    @staticmethod
    def v4_pre_launch_optimize():
        """I/O and Thermal Protection (v4)."""
        # RAM flush and Sync to relieve I/O pressure
        cmd = "sync; echo 3 > /proc/sys/vm/drop_caches"
        subprocess.run(f"su -c '{cmd}'", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @staticmethod
    def set_priority(pid):
        """Sets highest CPU priority for the game PID (v4)."""
        try:
            cmd = f"renice -n -20 -p {pid}"
            subprocess.run(f"su -c '{cmd}'", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    @staticmethod
    def periodic_trim():
        """Aggressive cache trim (runs every 30m in v4)."""
        cmd = "pm trim-caches 999G"
        MemoryManager._run_su_detached(cmd)

    @staticmethod
    def set_oom_priority():
        """Protect bot process from LMK."""
        try:
            pid = os.getpid()
            subprocess.run(f"su -c 'echo -1000 > /proc/{pid}/oom_score_adj'", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    @staticmethod
    def get_free_ram_percentage() -> float:
        """Возвращает процент свободной ОЗУ."""
        try:
            import psutil
            mem = psutil.virtual_memory()
            return 100.0 - mem.percent
        except:
            return 50.0

    @staticmethod
    def smart_ram_cleanup():
        """Очистка кешей клонов и сброс системных кешей (v5.2)."""
        cmds = [
            "rm -rf /data/data/com.roblox.client*/cache/*",
            "rm -rf /data/data/com.roblox.client*/code_cache/*",
            "sync; echo 3 > /proc/sys/vm/drop_caches"
        ]
        for cmd in cmds:
            subprocess.run(f"su -c '{cmd}'", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if __name__ == "__main__":
    MemoryManager.setup_swap()
    MemoryManager.v4_pre_launch_optimize()
