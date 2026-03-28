# -*- coding: utf-8 -*-
# Aegis bot — V12.4 ZOMBIE FARM FIX (Kill→Resurrect, Heartbeat, Uptime Reboot, Detached Launch, Empty Farm Guard)
import os
import sys
import enum
import asyncio
import logging
import time
import tempfile
import re
import html
import subprocess
import multiprocessing
from typing import Optional, Dict, Tuple

_bot_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(_bot_dir)
sys.path.insert(0, _bot_dir)

# ── IMMORTALITY: apply before anything else ──────────────────────────────────
def apply_immortality() -> None:
    """Anti-OOM: oom_adj=-17, oom_score_adj=-1000, nice=-20, termux-wake-lock."""
    pid = os.getpid()
    # V12.0 OOM Shield: oom_adj=-17 (legacy) + oom_score_adj=-1000
    # Android kills processes with highest oom_adj first; -17 = last to die
    try:
        subprocess.run(
            ["su", "-c", f"echo -17 > /proc/{pid}/oom_adj"],
            capture_output=True, timeout=15,
        )
    except Exception:
        pass
    try:
        subprocess.run(
            ["su", "-c", f"echo -1000 > /proc/{pid}/oom_score_adj"],
            capture_output=True, timeout=15,
        )
    except Exception:
        pass
    # Highest scheduling priority
    try:
        os.nice(-20)
    except Exception:
        pass
    # Termux wake lock — prevent CPU sleep
    for wl in ["termux-wake-lock",
                "/data/data/com.termux/files/usr/bin/termux-wake-lock"]:
        try:
            subprocess.run([wl], capture_output=True, timeout=20)
            break
        except Exception:
            pass

# Apply immortality immediately at startup (before logging / arg parsing)
apply_immortality()


# ── V12.3 ROOT CHECK ─────────────────────────────────────────────────────────
def check_root() -> bool:
    """Verify root access via su -c 'id'. Returns True if root is available."""
    try:
        r = subprocess.run(
            ["su", "-c", "id"], capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0 and "uid=0" in r.stdout
    except Exception:
        return False

HAS_ROOT = check_root()
if not HAS_ROOT:
    print("❌ ROOT NOT AVAILABLE! su -c 'id' failed. Bot will NOT launch Roblox.")


# ── V12.4 FAILSAFE RAM WATCHDOG (multiprocessing.Process) ────────────────────
#    Runs as a SEPARATE PROCESS — independent of main bot.
#    Polls /proc/meminfo every 10 seconds.
#    Trigger: (MemTotal - MemAvailable) / MemTotal * 100 >= 98%
#    Action: IMMEDIATE reboot via os.system('su -c "reboot"')
#    NO clone killing, NO async, NO wrappers — straight to reboot.
RAM_REBOOT_PERCENT = 98   # reboot when RAM usage >= 98%
RAM_WATCHDOG_POLL  = 10   # poll every 10 seconds

def _ram_failsafe_loop(farm_dir: str) -> None:
    """Independent process: monitors RAM % and reboots IMMEDIATELY at 98%+.
    Uses os.system() — the simplest, most reliable reboot method.
    No clone killing, no cleanup — straight to reboot at 98%."""
    # Max priority + OOM protection
    try:
        os.nice(-20)
    except Exception:
        pass
    pid = os.getpid()
    try:
        with open(f"/proc/{pid}/oom_adj", "w") as f:
            f.write("-17")
    except Exception:
        pass
    try:
        with open(f"/proc/{pid}/oom_score_adj", "w") as f:
            f.write("-1000")
    except Exception:
        pass

    while True:
        time.sleep(RAM_WATCHDOG_POLL)
        try:
            with open("/proc/meminfo", "r") as f:
                mi = f.read()
            # Parse MemTotal and MemAvailable
            def _kb(key):
                m = re.search(rf"{key}:\s+(\d+)", mi)
                return int(m.group(1)) if m else 0
            total_kb = _kb("MemTotal")
            avail_kb = _kb("MemAvailable")
            if avail_kb == 0:
                avail_kb = _kb("MemFree") + _kb("Buffers") + _kb("Cached")

            if total_kb <= 0:
                continue  # can't calculate — skip this cycle

            usage_pct = (total_kb - avail_kb) / total_kb * 100.0
            avail_mb = avail_kb / 1024.0
            total_mb = total_kb / 1024.0

            if usage_pct >= RAM_REBOOT_PERCENT:
                # ── LOG: visible in console ──────────────────────────────
                msg = (
                    f"[WATCHDOG] Memory at {usage_pct:.1f}% "
                    f"({avail_mb:.0f}MB free / {total_mb:.0f}MB total). "
                    f"Initiating SYSTEM REBOOT..."
                )
                print(msg, flush=True)

                # ── CRASH REPORT ─────────────────────────────────────────
                try:
                    crash_path = os.path.join(farm_dir, "crash_report.txt")
                    with open(crash_path, "a", encoding="utf-8") as cf:
                        cf.write(f"\n{'='*60}\n")
                        cf.write(f"FAILSAFE REBOOT @ {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                        cf.write(f"RAM Usage: {usage_pct:.1f}% (threshold: {RAM_REBOOT_PERCENT}%)\n")
                        cf.write(f"MemAvailable: {avail_mb:.0f}MB / MemTotal: {total_mb:.0f}MB\n")
                        cf.write(f"{'='*60}\n")
                except Exception:
                    pass

                # ── REBOOT: direct os.system — no wrappers, no async ────
                print("[WATCHDOG] >>> EXECUTING: su -c reboot <<<", flush=True)
                os.system('su -c "reboot"')
                # If os.system returns (shouldn't), try sysrq
                time.sleep(5)
                print("[WATCHDOG] >>> reboot failed, trying sysrq <<<", flush=True)
                os.system('su -c "echo 1 > /proc/sys/kernel/sysrq && echo b > /proc/sysrq-trigger"')
                # Last resort
                time.sleep(5)
                os._exit(1)
        except Exception:
            pass  # Never crash — keep watching

if len(sys.argv) < 2:
    print("❌  Usage: python main.py <DEVICE_ID>")
    sys.exit(1)

DEVICE_ID = sys.argv[1]
FARM_DIR  = _bot_dir
BOOT_LOG  = os.path.join(FARM_DIR, "boot_log.txt")

from telegram import Update, Message
from telegram.ext import (
    ApplicationBuilder, Application, CommandHandler,
    ContextTypes, MessageHandler, filters, CallbackQueryHandler
)
from telegram.error import TelegramError

from config_manager      import ConfigManager
from ui_manager          import UIManager
from monitor             import MonitorEngine, clone_pgrep_alive
from injection_engine    import InjectionEngine
from bash_utils          import run_bash
from persistence_manager import PersistenceManager

VERSION = "12.4"

# ── V12.4 Configuration Constants ────────────────────────────────────────────
ENABLE_AUTO_REBOOT = True       # Enable OOM protection + uptime reboot in watchdog
# RAM_REBOOT_PERCENT = 98 — defined above (failsafe process uses it too)
# RAM_WATCHDOG_POLL  = 10 — defined above
UPTIME_REBOOT_SEC  = 9000       # V12.4: 2.5 hours → forced sysrq reboot
HEARTBEAT_SEC      = 30         # V12.4: heartbeat check interval (seconds)
EMPTY_FARM_WAIT    = 120        # V12.4: seconds to wait before empty-farm guard triggers
# SILENT_MODE is now persisted via PersistenceManager.silent_mode (toggle in System menu)

logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [{DEVICE_ID}/V{VERSION}] [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BOOT_LOG, encoding="utf-8"),
    ]
)
logger = logging.getLogger("AegisV12")
for _quiet in ("httpx", "httpcore", "telegram", "telegram.ext", "telegram.request"):
    logging.getLogger(_quiet).setLevel(logging.WARNING)

class CloneState(str, enum.Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"

class TelegramLogHandler(logging.Handler):
    def __init__(self, bot: "AegisBot"):
        super().__init__()
        self.bot = bot
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        asyncio.create_task(self.bot.add_log_line(self.format(record)))


async def watchdog_hard_reset(
    application: Application,
    bot_instance: "AegisBot",
    name: str,
    reason: str,
) -> None:
    """
    V12.1 Hard Reset Protocol (Window-Safe):
    1. am stack remove — detach floating window without collapsing others
    2. am force-stop — kill the process
    3. rm cache + code_cache
    4. Pause 3 seconds
    5. Relaunch clone
    NO drop_caches — filesystem is read-only.
    """
    suffix = name[-1].lower() if name.lower().startswith("clien") else name.lower()
    pkg = f"com.roblox.clien{suffix}"
    bot_instance.watchdog_cpu_low_since.pop(name, None)
    admin = bot_instance.config.admin_ids[0] if bot_instance.config.admin_ids else None
    if admin:
        try:
            await application.bot.send_message(
                admin,
                f"⚠️ <code>{html.escape(name.replace('_', '-'))}</code> · {html.escape(reason)}",
                parse_mode="HTML",
            )
        except TelegramError:
            pass

    # Steps 1-3: window-safe isolated stop + cache clean
    await _isolated_stop(suffix)
    # Step 4: pause
    await asyncio.sleep(3)
    bot_instance.set_state(name, CloneState.STOPPED)
    # Step 4: relaunch
    asyncio.create_task(bot_instance._enqueue_start(name, admin))


async def _isolated_stop(suffix: str) -> None:
    """
    V12.1 Window-Safe Stop:
    1. am stack remove — detach the floating window from the stack
       (prevents collapsing other floating Roblox windows)
    2. am force-stop — kill the process
    3. rm cache/* + code_cache/* — free stored data
    V12.4: All commands have 15s timeout to prevent watchdog hang.
    """
    pkg = f"com.roblox.clien{suffix}"
    logger.info(f"_isolated_stop [{pkg}]: starting (stack remove → force-stop → cache clean)")
    # Step 1: detach floating window (ignore errors if no stack found)
    await run_bash(
        f'su -c "am stack remove $(su -c \\"dumpsys activity activities'
        f" | grep {pkg} | grep 'Stack #' | cut -d '#' -f 2 | cut -d ' '"
        f' -f 1\\")" 2>/dev/null',
        timeout=15,
    )
    # Step 2: force-stop the process
    await run_bash(f"su -c 'am force-stop {pkg}'", timeout=15)
    # Step 3: cache clean
    await run_bash(f"su -c 'rm -rf /data/data/{pkg}/cache/*'", timeout=10)
    await run_bash(f"su -c 'rm -rf /data/data/{pkg}/code_cache/*'", timeout=10)
    logger.info(f"_isolated_stop [{pkg}]: done")


async def watchdog_loop(application: Application, bot_instance: "AegisBot"):
    """
    V12.4 Intelligent Watchdog — Net-Pulse powered + Zombie Farm Fix.
    Runs every 15s. Only acts on clones with enabled=True in session_state.

    Stage 1 — Liveness:   cached PID check via /proc/{pid} → Hard Reset if gone
    Stage 2 — TCP Guard:  CON ≤ 5 (ZOMBIE) sustained for 30s → Hard Reset
              CON ≥ 8  (ACTIVE) → healthy, reset zombie timer
    Stage 3 — Threads:    Deep thread check (ls /proc/PID/task).
              If TH==0 but CON>0 → ignore TH (Net-Pulse rule).
              If TH==0 and CON==0 → phantom kill.
    V12.4 — Heartbeat:    Every 30s check ALL enabled clones alive via pgrep
    V12.4 — Uptime Reboot: /proc/uptime > 2.5h → sysrq reboot
    V12.4 — Empty Farm Guard: 0 running among enabled → mass restart
    V12.4 — Kill→Resurrect: OOM Level 1 kill → wait 10s → relaunch
    """
    GRACE_SEC    = 120    # startup grace before checking
    TCP_ACTIVE   = 8      # ≥8 → ACTIVE
    TCP_ZOMBIE   = 5      # ≤5 → ZOMBIE
    ZOMBIE_SEC   = 30     # V12.0: 30s sustained ZOMBIE → Hard Reset
    POLL_SEC     = 15     # V12.0: poll every 15s

    # Per-clone zombie timer: {name: timestamp when conns first dropped to ZOMBIE}
    tcp_low_since: Dict[str, float] = {}
    # V12.4: Heartbeat timer
    last_heartbeat: float = time.time()
    # V12.4: Empty farm guard — timestamp when farm first seen empty
    empty_farm_since: Optional[float] = None

    while True:
        await asyncio.sleep(POLL_SEC)
        try:
            now = time.time()
            for name, state in list(bot_instance.clone_states.items()):
                if ":" in name:
                    continue
                # V11.0: Only watchdog clones that are enabled in session_state
                if not bot_instance.persistence.is_clone_enabled(name):
                    tcp_low_since.pop(name, None)
                    continue
                if state != CloneState.RUNNING:
                    tcp_low_since.pop(name, None)
                    continue

                suffix = name[-1].lower() if name.lower().startswith("clien") else name.lower()
                uptime = now - bot_instance.running_since.get(name, now)

                # ── Stage 1: Liveness (PID cache fast-path) ───────────────────
                cached_pid = bot_instance.pid_cache.get(name)
                if cached_pid and os.path.exists(f"/proc/{cached_pid}"):
                    alive = True
                else:
                    alive = await clone_pgrep_alive(suffix)
                    if alive:
                        # Update PID cache
                        new_pid = await MonitorEngine.get_pid(suffix)
                        if new_pid:
                            bot_instance.pid_cache[name] = new_pid
                if not alive:
                    tcp_low_since.pop(name, None)
                    logger.warning(f"Watchdog [{name}]: Stage1 — no process → Hard Reset")
                    await watchdog_hard_reset(
                        application, bot_instance, name, "нет процесса (pgrep)"
                    )
                    continue

                # ── Stage 2: TCP Connection Guard (primary — Net-Pulse) ───────
                conns = await MonitorEngine.get_clone_connections(suffix)
                bot_instance.clone_states[f"{name}:cpu"] = str(conns)

                # Deep thread count
                thr = await MonitorEngine.get_threads_deep(suffix)
                bot_instance.clone_states[f"{name}:threads"] = str(thr)

                # Net-Pulse rule: if CON > 0 and TH == 0, trust CON, ignore TH
                if conns > 0 and thr == 0:
                    logger.info(f"Watchdog [{name}]: TH=0 but CON={conns} — trusting CON (Net-Pulse)")

                if uptime >= GRACE_SEC:
                    if conns <= TCP_ZOMBIE:
                        # ZOMBIE state — Hard Reset after ZOMBIE_SEC sustained
                        t0 = tcp_low_since.get(name)
                        if t0 is None:
                            tcp_low_since[name] = now
                            logger.info(f"Watchdog [{name}]: ZOMBIE detected CON={conns}, timer started")
                        elif now - t0 >= ZOMBIE_SEC:
                            tcp_low_since.pop(name, None)
                            logger.warning(
                                f"Watchdog [{name}]: ZOMBIE CON={conns} ≤ {TCP_ZOMBIE} "
                                f"for {ZOMBIE_SEC}s → Hard Reset"
                            )
                            await watchdog_hard_reset(
                                application, bot_instance, name,
                                f"ZOMBIE TCP={conns} ≤ {TCP_ZOMBIE} for {ZOMBIE_SEC}s"
                            )
                            continue
                    else:
                        # Healthy or loading — reset zombie timer
                        tcp_low_since.pop(name, None)

                # ── Stage 3: Phantom check (TH==0 AND CON==0) ────────────────
                if alive and thr == 0 and conns == 0 and uptime >= GRACE_SEC:
                    tcp_low_since.pop(name, None)
                    logger.warning(f"Watchdog [{name}]: phantom (alive, TH=0, CON=0) → Hard Reset")
                    await watchdog_hard_reset(
                        application, bot_instance, name, "phantom (TH=0, CON=0)"
                    )
                    continue

            # ── V12.4 Heartbeat: check ALL enabled clones alive every 30s ─
            if now - last_heartbeat >= HEARTBEAT_SEC:
                last_heartbeat = now
                admin_id = bot_instance.config.admin_ids[0] if bot_instance.config.admin_ids else None
                enabled_clones = bot_instance.persistence.get_enabled_clones()
                for hb_name in enabled_clones:
                    hb_state = bot_instance.clone_states.get(hb_name)
                    # Skip clones that are currently starting
                    if hb_state == CloneState.STARTING:
                        continue
                    hb_suffix = hb_name[-1].lower() if hb_name.lower().startswith("clien") else hb_name.lower()
                    hb_alive = await clone_pgrep_alive(hb_suffix)
                    if not hb_alive and hb_state != CloneState.STARTING:
                        logger.warning(f"Heartbeat [{hb_name}]: dead (pgrep) — relaunching")
                        bot_instance.set_state(hb_name, CloneState.STOPPED)
                        bot_instance.pid_cache.pop(hb_name, None)
                        if admin_id and application and not bot_instance.persistence.silent_mode:
                            try:
                                await application.bot.send_message(
                                    admin_id,
                                    f"💓 Heartbeat: <code>{html.escape(hb_name.replace('_','-'))}</code> "
                                    f"мёртв → перезапуск",
                                    parse_mode="HTML",
                                )
                            except Exception:
                                pass
                        asyncio.create_task(bot_instance._enqueue_start(hb_name, admin_id))
                        await asyncio.sleep(5)  # small gap between heartbeat relaunches

            # ── V12.4 Empty Farm Guard ────────────────────────────────────
            enabled_clones = bot_instance.persistence.get_enabled_clones()
            if enabled_clones:
                running_count = sum(
                    1 for en in enabled_clones
                    if bot_instance.clone_states.get(en) == CloneState.RUNNING
                )
                starting_count = sum(
                    1 for en in enabled_clones
                    if bot_instance.clone_states.get(en) == CloneState.STARTING
                )
                if running_count == 0 and starting_count == 0:
                    if empty_farm_since is None:
                        empty_farm_since = now
                        logger.warning(f"Empty Farm Guard: 0 running among {len(enabled_clones)} enabled — timer started")
                    elif now - empty_farm_since >= EMPTY_FARM_WAIT:
                        empty_farm_since = None
                        logger.warning(
                            f"Empty Farm Guard: 0 running for {EMPTY_FARM_WAIT}s — "
                            f"triggering mass restart of {len(enabled_clones)} enabled clones"
                        )
                        admin_id = bot_instance.config.admin_ids[0] if bot_instance.config.admin_ids else None
                        if admin_id and application:
                            try:
                                await application.bot.send_message(
                                    admin_id,
                                    f"🚨 <b>Empty Farm Guard</b>: 0 клонов работает из "
                                    f"{len(enabled_clones)} включённых → массовый перезапуск",
                                    parse_mode="HTML",
                                )
                            except Exception:
                                pass
                        # Relaunch all enabled clones with stagger
                        for efg_name in enabled_clones:
                            if bot_instance.abort_launch:
                                break
                            asyncio.create_task(bot_instance._enqueue_start(efg_name, admin_id))
                            await asyncio.sleep(10)
                else:
                    empty_farm_since = None  # reset — farm is alive

            # ── V12.4 Uptime Reboot: /proc/uptime > 2.5h → sysrq reboot ─
            if ENABLE_AUTO_REBOOT and UPTIME_REBOOT_SEC > 0:
                try:
                    with open("/proc/uptime", "r") as _uf:
                        _uptime_str = _uf.read().split()[0]
                    device_uptime = float(_uptime_str)
                    if device_uptime >= UPTIME_REBOOT_SEC:
                        logger.warning(
                            f"Uptime Reboot: device up {device_uptime:.0f}s "
                            f"(>{UPTIME_REBOOT_SEC}s / {UPTIME_REBOOT_SEC/3600:.1f}h) → sysrq reboot"
                        )
                        admin_id = bot_instance.config.admin_ids[0] if bot_instance.config.admin_ids else None
                        if admin_id and application:
                            try:
                                await application.bot.send_message(
                                    admin_id,
                                    f"🔄 <b>Uptime Reboot</b>: устройство работает "
                                    f"{device_uptime/3600:.1f}ч (лимит {UPTIME_REBOOT_SEC/3600:.1f}ч) "
                                    f"— перезагрузка",
                                    parse_mode="HTML",
                                )
                            except Exception:
                                pass
                        # Write crash_report before reboot
                        try:
                            crash_path = os.path.join(FARM_DIR, "crash_report.txt")
                            with open(crash_path, "a", encoding="utf-8") as cf:
                                cf.write(f"\n{'='*60}\n")
                                cf.write(f"UPTIME REBOOT @ {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                                cf.write(f"Device uptime: {device_uptime:.0f}s ({device_uptime/3600:.1f}h)\n")
                                cf.write(f"Threshold: {UPTIME_REBOOT_SEC}s ({UPTIME_REBOOT_SEC/3600:.1f}h)\n")
                                cf.write(f"{'='*60}\n")
                        except Exception:
                            pass
                        logger.critical(">>> UPTIME REBOOT: SYSRQ COMMAND EXECUTING <<<")
                        await run_bash(
                            'su -c "echo 1 > /proc/sys/kernel/sysrq && echo b > /proc/sysrq-trigger"',
                            timeout=10,
                        )
                        logger.critical(">>> UPTIME REBOOT: SYSRQ FAILED, TRYING su -c reboot <<<")
                        await asyncio.sleep(3)
                        await run_bash("su -c 'reboot'", timeout=10)
                        logger.critical(">>> UPTIME REBOOT: ALL REBOOT COMMANDS FAILED <<<")
                        os._exit(1)
                except Exception as e:
                    logger.error(f"Uptime Reboot check error: {e}", exc_info=True)

            # ── OOM Protection: percentage-based RAM guard ────────────────
            #    >= 98%: IMMEDIATE reboot via os.system (no clone killing)
            #    >= 90%: kill oldest clone + resurrect after 10s
            #    V12.4: Direct /proc/meminfo read, os.system for reboot
            if ENABLE_AUTO_REBOOT:
                try:
                    with open("/proc/meminfo", "r") as _mf:
                        _mi = _mf.read()
                    def _parse_kb(key: str) -> int:
                        m = re.search(rf"{key}:\s+(\d+)", _mi)
                        return int(m.group(1)) if m else 0
                    _total_kb = _parse_kb("MemTotal")
                    _avail_kb = _parse_kb("MemAvailable")
                    if _avail_kb == 0:
                        _avail_kb = _parse_kb("MemFree") + _parse_kb("Buffers") + _parse_kb("Cached")
                    if _total_kb > 0:
                        _usage_pct = (_total_kb - _avail_kb) / _total_kb * 100.0
                        _avail_mb = _avail_kb / 1024.0
                        _total_mb = _total_kb / 1024.0
                    else:
                        _usage_pct = 0.0
                        _avail_mb = 9999.0
                        _total_mb = 0.0

                    if _usage_pct >= RAM_REBOOT_PERCENT:
                        # ── 98%+ : IMMEDIATE REBOOT — no clone killing ───
                        logger.critical(
                            f"[WATCHDOG] Memory at {_usage_pct:.1f}% "
                            f"({_avail_mb:.0f}MB free / {_total_mb:.0f}MB total). "
                            f"Initiating SYSTEM REBOOT..."
                        )
                        print(
                            f"[WATCHDOG] Memory at {_usage_pct:.1f}% "
                            f"({_avail_mb:.0f}MB free / {_total_mb:.0f}MB total). "
                            f"Initiating SYSTEM REBOOT...",
                            flush=True,
                        )
                        admin_id = bot_instance.config.admin_ids[0] if bot_instance.config.admin_ids else None
                        if admin_id and application:
                            try:
                                await application.bot.send_message(
                                    admin_id,
                                    f"🔴 <b>RAM {_usage_pct:.1f}% ≥ {RAM_REBOOT_PERCENT}%</b> "
                                    f"({_avail_mb:.0f}MB free) — REBOOT!",
                                    parse_mode="HTML",
                                )
                            except Exception:
                                pass
                        # Crash report
                        try:
                            crash_path = os.path.join(FARM_DIR, "crash_report.txt")
                            with open(crash_path, "a", encoding="utf-8") as cf:
                                cf.write(f"\n{'='*60}\n")
                                cf.write(f"WATCHDOG REBOOT @ {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                                cf.write(f"RAM: {_usage_pct:.1f}% ({_avail_mb:.0f}MB / {_total_mb:.0f}MB)\n")
                                cf.write(f"{'='*60}\n")
                        except Exception:
                            pass
                        # DIRECT REBOOT — os.system, no async wrappers
                        logger.critical(">>> EXECUTING: os.system('su -c reboot') <<<")
                        print(">>> EXECUTING: os.system('su -c reboot') <<<", flush=True)
                        os.system('su -c "reboot"')
                        time.sleep(5)
                        logger.critical(">>> reboot failed, trying sysrq <<<")
                        os.system('su -c "echo 1 > /proc/sys/kernel/sysrq && echo b > /proc/sysrq-trigger"')
                        time.sleep(5)
                        os._exit(1)

                    elif _usage_pct >= 90:
                        # ── 90-97%: kill oldest clone → resurrect ────────
                        oldest_name = None
                        oldest_ts = float("inf")
                        for cname, cstate in bot_instance.clone_states.items():
                            if ":" in cname:
                                continue
                            if cstate == CloneState.RUNNING:
                                ts = bot_instance.running_since.get(cname, float("inf"))
                                if ts < oldest_ts:
                                    oldest_ts = ts
                                    oldest_name = cname
                        if oldest_name:
                            sfx = oldest_name[-1].lower() if oldest_name.lower().startswith("clien") else oldest_name.lower()
                            logger.warning(
                                f"OOM: RAM {_usage_pct:.1f}% — killing oldest [{oldest_name}]"
                            )
                            await _isolated_stop(sfx)
                            bot_instance.set_state(oldest_name, CloneState.STOPPED)
                            admin_id = bot_instance.config.admin_ids[0] if bot_instance.config.admin_ids else None
                            if admin_id and application:
                                try:
                                    await application.bot.send_message(
                                        admin_id,
                                        f"⚠️ <b>RAM {_usage_pct:.1f}%</b> — "
                                        f"killed <code>{html.escape(oldest_name)}</code> → resurrect 10s",
                                        parse_mode="HTML",
                                    )
                                except Exception:
                                    pass
                            # Resurrect with RAM re-check
                            async def _resurrect_clone(rname, radmin):
                                await asyncio.sleep(10)
                                try:
                                    with open("/proc/meminfo", "r") as _rf:
                                        _rmi = _rf.read()
                                    _rt = re.search(r"MemTotal:\s+(\d+)", _rmi)
                                    _ra = re.search(r"MemAvailable:\s+(\d+)", _rmi)
                                    if _rt and _ra:
                                        _rpct = (int(_rt.group(1)) - int(_ra.group(1))) / int(_rt.group(1)) * 100
                                    else:
                                        _rpct = 0
                                except Exception:
                                    _rpct = 0
                                if _rpct >= 90:
                                    logger.warning(f"Resurrect [{rname}]: RAM still {_rpct:.0f}% — skip")
                                    return
                                logger.info(f"Resurrect [{rname}]: RAM {_rpct:.0f}% OK — relaunching")
                                await bot_instance._enqueue_start(rname, radmin)
                            asyncio.create_task(_resurrect_clone(oldest_name, admin_id))
                except Exception as e:
                    logger.error(f"OOM check error: {e}", exc_info=True)

            await bot_instance.refresh_dashboard()
        except Exception as e:
            logger.error(f"watchdog_loop error: {e}")


class AegisBot:
    def __init__(self):
        self.config      = ConfigManager(DEVICE_ID, FARM_DIR)
        self.persistence = PersistenceManager(FARM_DIR)
        self.application: Optional[Application] = None
        self._dash_msg: Optional[Message] = None
        self._log_handler: Optional[logging.Handler] = None
        self._console_on: bool = self.persistence.console_mode
        self._last_ui_update: float = 0.0

        self.console_queue = asyncio.Queue()
        self.last_console_flush: float = 0.0
        self.console_lock = asyncio.Lock()
        self._console_task: Optional[asyncio.Task] = None

        self.clone_states: Dict[str, CloneState] = {}
        self.running_since: Dict[str, float] = {}
        self._start_lock = asyncio.Lock()
        self.is_mass_starting = False
        self.abort_launch = False  # V12.2: set True by mass_stop to interrupt launch queue
        self.watchdog_cpu_low_since: Dict[str, Optional[float]] = {}
        self._last_sync_ts: float = 0.0  # timestamp of last hub refresh
        # V12.0: PID cache — {clone_name: pid_str}, updated on start, checked via /proc
        self.pid_cache: Dict[str, str] = {}

        for c in self.config.clones_data:
            n = c.get("name")
            if n:
                self.clone_states[n] = CloneState.STOPPED

    def reset_all_states(self):
        """Старт: все клоны в простое (IDLE), без автозапуска."""
        self.is_mass_starting = False
        self.abort_launch = False
        self.watchdog_cpu_low_since.clear()
        for c in self.config.clones_data:
            n = c.get("name")
            if n:
                self.set_state(n, CloneState.STOPPED)

    def _sync_dev_json_status(self, name: str, status: str) -> None:
        """
        Write clone status directly into DEV_{DEVICE_ID}.json so auto-recovery
        can read it on next boot. Non-fatal: any error is silently logged.
        """
        import json as _json
        dev_path = os.path.join(FARM_DIR, f"{DEVICE_ID}.json")
        if not os.path.exists(dev_path):
            return
        try:
            with open(dev_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            changed = False
            for clone in data.get("clones", []):
                if clone.get("name") == name:
                    clone["status"] = status.lower()
                    changed = True
                    break
            if changed:
                with open(dev_path, "w", encoding="utf-8") as f:
                    _json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.debug(f"_sync_dev_json_status [{name}={status}]: {e}")

    def set_state(self, name: str, state: CloneState):
        if ":" in name: return
        old = self.clone_states.get(name, CloneState.STOPPED)
        self.clone_states[name] = state
        if state == CloneState.RUNNING:
            self.running_since[name] = time.time()
        elif old == CloneState.RUNNING:
            self.running_since.pop(name, None)
        logger.info(f"State [{name}]: {str(old)} → {str(state)}")
        if state == CloneState.RUNNING:
            self.config.update_clone_status(name, "RUNNING")
            self._sync_dev_json_status(name, "RUNNING")
        elif state == CloneState.STARTING:
            self.config.update_clone_status(name, "STARTING")
        elif state == CloneState.STOPPED:
            self.config.update_clone_status(name, "IDLE")
            self._sync_dev_json_status(name, "idle")

    async def _is_admin(self, uid: int) -> bool:
        return uid in self.config.admin_ids

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update.effective_user.id): return
        await update.message.reply_text(
            UIManager.get_welcome_text(DEVICE_ID, VERSION),
            reply_markup=UIManager.get_main_keyboard(),
            parse_mode="HTML"
        )

    async def cmd_console(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Last 15 lines of boot_log.txt."""
        if not await self._is_admin(update.effective_user.id): return
        try:
            if os.path.exists(BOOT_LOG):
                with open(BOOT_LOG, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                tail = "".join(lines[-15:]).strip() or "(empty)"
            else:
                tail = "(boot_log.txt not found)"
            tail_esc = html.escape(tail)
            await update.message.reply_text(f"<pre>{tail_esc}</pre>", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Console error: {e}")

    async def cmd_exec(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update.effective_user.id): return
        cmd = update.message.text[len("/exec "):].strip()
        if not cmd:
            await update.message.reply_text("Usage: /exec <command>")
            return
        ret, out, err = await run_bash(cmd)
        res = (out + "\n" + err).strip()
        if not res: res = "(no output)"
        if len(res) > 3900:
            fd, path = tempfile.mkstemp(suffix=".txt")
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(res)
            with open(path, 'rb') as f:
                await update.message.reply_document(f, filename="exec_output.txt")
            os.remove(path)
        else:
            res_esc = html.escape(res)
            await update.message.reply_text(f"<pre>{res_esc}</pre>", parse_mode="HTML")

    async def cmd_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update.effective_user.id): return
        msg = await update.message.reply_text("🔄 Обновление…", parse_mode="HTML")
        
        try:
            await msg.edit_text("git fetch / reset…")
            await run_bash(f"git -C {_bot_dir} fetch --all")
            await run_bash(f"git -C {_bot_dir} reset --hard origin/main")
            await run_bash(f"git -C {_bot_dir} pull origin main")

            await msg.edit_text("очистка __pycache__…")
            await run_bash(f'find {_bot_dir} -type d -name "__pycache__" -exec rm -rf {{}} +')

            req_path = os.path.join(_bot_dir, "requirements.txt")
            if os.path.exists(req_path):
                await msg.edit_text("pip install…")
                await run_bash(f"pip install -r {req_path}")

            await msg.edit_text("✅ Перезапуск процесса…", parse_mode="HTML")
            await asyncio.sleep(2)
            os.execv(sys.executable, ['python'] + sys.argv)
            
        except Exception as e:
            logger.error(f"Update Module Error: {e}")
            if msg:
                try:
                    e_esc = html.escape(str(e))
                    await msg.edit_text(f"❌ Ошибка: <code>{e_esc}</code>", parse_mode="HTML")
                except Exception: pass

    async def cmd_mass_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update.effective_user.id): return
        chat = update.message.chat_id
        await update.message.reply_text("▶️ Запуск очереди клонов.", parse_mode="HTML")
        asyncio.create_task(self._mass_start(chat))

    async def cmd_mass_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update.effective_user.id): return
        chat = update.message.chat_id
        asyncio.create_task(self._mass_stop(chat))
        await update.message.reply_text("🛑 Остановка всех.", parse_mode="HTML")

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update.effective_user.id): return
        t = update.message.text
        if t == "Устройство":
            await self._open_device(update)
        elif t == "Клоны":
            await self.open_clones_hub(update)
        elif t == "Система":
            await self._open_system(update)

    async def _open_device(self, update: Update):
        ram, cpu, temp = await MonitorEngine.get_system_stats()
        await update.message.reply_text(
            UIManager.format_dashboard(DEVICE_ID, ram, cpu, temp, VERSION),
            reply_markup=UIManager.get_device_keyboard(),
            parse_mode="HTML"
        )

    async def _open_system(self, update: Update):
        await update.message.reply_text(
            "Система",
            reply_markup=UIManager.get_system_keyboard(
                self._console_on, self.persistence.auto_restore, self.persistence.silent_mode),
            parse_mode="HTML"
        )

    async def _hub_view(self) -> Tuple[str, Dict]:
        async def collect_one(clone_name: str) -> Tuple[str, str, str, str, str]:
            name_disp = clone_name.replace("_", "-")
            sfx = clone_name[-1].lower() if clone_name.lower().startswith("clien") else clone_name.lower()
            conns = 0
            try:
                alive, st, conns = await asyncio.gather(
                    clone_pgrep_alive(sfx),
                    MonitorEngine.get_clone_status(clone_name),
                    MonitorEngine.get_clone_connections(sfx),
                )
                m_thr = re.search(r"Thr:\s*(\d+)", st)
                thr = int(m_thr.group(1)) if m_thr else 0
                con_str = str(conns)
            except Exception as e:
                logger.error(f"hub collect_one [{clone_name}]: {e}")
                alive, thr, con_str = False, 0, "0"
                conns = 0
            self.clone_states[f"{clone_name}:threads"] = str(thr)
            self.clone_states[f"{clone_name}:cpu"] = con_str   # reuse ":cpu" key for connections
            cstate = self.clone_states.get(clone_name, CloneState.STOPPED)
            # V11.0 Net-Pulse: healthy = RUNNING + alive + CON >= 8 (TCP is king)
            # TH is secondary — if CON > 0 but TH == 0, still trust CON
            healthy = (
                cstate == CloneState.RUNNING
                and alive
                and conns >= 8   # V11.0: ACTIVE threshold (CON > TH)
            )
            return (
                name_disp,
                str(cstate),
                str(thr),
                con_str,
                "1" if healthy else "0",
            )

        names = [c.get("name") for c in self.config.clones_data if c.get("name")]
        rows = await asyncio.gather(*[collect_one(n) for n in names]) if names else []

        state_map: Dict[str, str] = {}
        for n, s in self.clone_states.items():
            if ":" in n:
                name_part, key_part = n.split(":", 1)
                clean_n = f"{name_part.replace('_', '-')}:{key_part}"
            else:
                clean_n = n.replace("_", "-")
            state_map[clean_n] = str(s)

        for row in rows:
            name_disp, st, thr, cpu_str, h = row
            state_map[name_disp] = st
            state_map[f"{name_disp}:threads"] = thr
            state_map[f"{name_disp}:cpu"] = cpu_str
            state_map[f"{name_disp}:healthy"] = h

        # Inject RAM info for V10 dashboard header
        try:
            state_map["__ram_free__"] = await MonitorEngine.get_ram_free_gb()
        except Exception:
            state_map["__ram_free__"] = "N/A"

        # Inject Last Sync timestamp
        self._last_sync_ts = time.time()
        import datetime as _dt
        sync_str = _dt.datetime.fromtimestamp(self._last_sync_ts).strftime("%H:%M:%S")
        state_map["__last_sync__"] = sync_str

        # V11.0: AutoResume indicator in header
        state_map["__auto_resume__"] = "ENABLED" if self.persistence.auto_restore else "DISABLED"

        text = UIManager.format_clones_hub(self.config.clones_data, state_map, VERSION)
        return text, state_map

    async def open_clones_hub(self, update: Update):
        try:
            self.config.reload()
            text, sm = await self._hub_view()
            kb = UIManager.get_clones_hub_keyboard(self.config.clones_data, sm)
            self._dash_msg = await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"open_clones_hub error: {e}")
            await update.message.reply_text(f"❌ Hub error: {e}")

    async def _get_clone_menu(self, clone_name: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Build and send per-clone control menu with correct account nickname mapping."""
        suffix = clone_name.lower()
        name_acc = clone_name
        for clone in self.config.clones_data:
            cfg_name = str(clone.get("name", "")).lower()
            if cfg_name == suffix:
                name_acc = clone.get("nickname") or clone.get("name") or clone_name
                break

        pid_sfx = suffix[-1].lower() if suffix.startswith("clien") else suffix.lower()
        alive, st, cpu = await asyncio.gather(
            clone_pgrep_alive(pid_sfx),
            MonitorEngine.get_clone_status(clone_name),
            MonitorEngine.get_clone_cpu_percent(pid_sfx),
        )
        m_thr = re.search(r"Thr:\s*(\d+)", st)
        thr = int(m_thr.group(1)) if m_thr else 0
        cpu_str = f"{cpu:.0f}" if cpu >= 0 else "—"

        raw_state = self.clone_states.get(clone_name, CloneState.STOPPED)
        healthy = (
            raw_state == CloneState.RUNNING
            and alive
            and thr >= 130
            and cpu >= 2.0
        )
        status_rus = (
            "OK (3/3)"
            if healthy
            else ("Запуск…" if raw_state == CloneState.STARTING else "Не готов")
        )

        acc_esc = html.escape(str(name_acc).replace('_', '-'))

        kb = UIManager.get_clone_submenu(clone_name, str(raw_state), thr)
        text = (
            f"👤 <code>{acc_esc}</code>\n"
            f"Статус: {status_rus}\n"
            f"{thr} th · {cpu_str}% CPU · pgrep: {'да' if alive else 'нет'}"
        )

        await context.bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")

    async def refresh_dashboard(self, force=False):
        if not self._dash_msg: return
        now = time.time()
        if not force and (now - self._last_ui_update < 60):
            return
            
        try:
            text, sm = await self._hub_view()
            kb = UIManager.get_clones_hub_keyboard(self.config.clones_data, sm)
            await self._dash_msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
            self._last_ui_update = now
        except Exception:
            pass

    async def _purge_clone_cache(self, name: str, chat_id: Optional[int]):
        if not name:
            return
        sfx = name[-1].lower() if name.lower().startswith("clien") else name.lower()
        await run_bash(f"su -c 'rm -rf /data/data/com.roblox.clien{sfx}/cache/*'")
        await run_bash(f"su -c 'rm -rf /data/data/com.roblox.clien{sfx}/code_cache/*'")
        app = self.application
        if chat_id and app:
            try:
                await app.bot.send_message(
                    chat_id,
                    f"🧹 Кэш <code>{html.escape(sfx.upper())}</code> очищен.",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        if not await self._is_admin(q.from_user.id): return
        await q.answer()
        d    = q.data
        chat = q.message.chat_id
        try:
            if d == "nav_home":
                await q.message.reply_text(UIManager.get_welcome_text(DEVICE_ID, VERSION),
                                           reply_markup=UIManager.get_main_keyboard(), parse_mode="HTML")

            elif d == "toggle_restore":
                self.persistence.auto_restore = not self.persistence.auto_restore
                self.persistence.save()
                try:
                    await q.edit_message_reply_markup(
                        UIManager.get_system_keyboard(
                            self._console_on, self.persistence.auto_restore, self.persistence.silent_mode))
                except Exception: pass

            elif d == "toggle_silent":
                self.persistence.silent_mode = not self.persistence.silent_mode
                self.persistence.save()
                try:
                    await q.edit_message_reply_markup(
                        UIManager.get_system_keyboard(
                            self._console_on, self.persistence.auto_restore, self.persistence.silent_mode))
                except Exception: pass

            elif d == "toggle_console":
                await self._toggle_console(context, chat)
                try:
                    await q.edit_message_reply_markup(
                        UIManager.get_system_keyboard(
                            self._console_on, self.persistence.auto_restore, self.persistence.silent_mode))
                except Exception: pass

            elif d == "sys_sync":  await self._git_sync(chat)
            elif d == "sys_screenshot": await self._take_screenshot(q.message)
            elif d == "sys_help": await q.message.reply_text(UIManager.get_help_text(), parse_mode="HTML")

            elif d == "deep_clean":
                await context.bot.send_message(chat, "🧹 Deep clean (sync)…", parse_mode="HTML")
                await self._do_deep_clean()
                await context.bot.send_message(chat, "✅ Sync done.", parse_mode="HTML")

            elif d == "hub_refresh":
                self.config.reload()
                text, sm = await self._hub_view()
                hub_msg = await q.message.reply_text(
                    text,
                    reply_markup=UIManager.get_clones_hub_keyboard(self.config.clones_data, sm),
                    parse_mode="HTML",
                )
                self._dash_msg = hub_msg

            elif d == "mass_start":
                chat_id = q.message.chat.id
                asyncio.create_task(self._mass_start(chat_id))
                await q.message.reply_text("▶️ Очередь запуска.", parse_mode="HTML")

            elif d == "mass_stop":
                asyncio.create_task(self._mass_stop(chat))
                await q.message.reply_text("🛑 Остановка всех.", parse_mode="HTML")

            elif d == "hub_clones":
                self.config.reload()
                text, sm = await self._hub_view()
                hub_msg = await q.message.reply_text(
                    text,
                    reply_markup=UIManager.get_clones_hub_keyboard(self.config.clones_data, sm),
                    parse_mode="HTML",
                )
                self._dash_msg = hub_msg

            elif d.startswith("start_single_"):
                name = d[13:]
                # V11.0: Save enabled state immediately on Start
                self.persistence.set_clone_enabled(name, True)
                asyncio.create_task(self._enqueue_start(name, chat))

            elif d.startswith("stop_single_"):
                name = d[12:]
                # V11.0: Save disabled state immediately on Stop
                self.persistence.set_clone_enabled(name, False)
                asyncio.create_task(self._stop_clone(name, chat))

            elif d.startswith("purge_cache_"):
                name = d[12:]
                await self._purge_clone_cache(name, chat)

            elif d.startswith("start_"):
                name = d[6:]
                # V11.0: Save enabled state immediately on Start
                self.persistence.set_clone_enabled(name, True)
                asyncio.create_task(self._enqueue_start(name, chat))

            elif d.startswith("stop_"):
                name = d[5:]
                self.persistence.set_clone_enabled(name, False)
                asyncio.create_task(self._stop_clone(name, chat))

            elif d.startswith("clone_"):
                c_name = d[6:]
                await self._get_clone_menu(c_name, chat, context)

        except Exception as e:
            logger.error(f"Callback [{d}] error: {e}")
            try:
                await context.bot.send_message(chat, f"❌ Error: {e}")
            except Exception:
                pass

    async def _enqueue_start(self, name: Optional[str], chat_id):
        if not name: return
        # V12.3: Block Roblox launch if no root access
        if not HAS_ROOT:
            logger.error(f"_enqueue_start [{name}]: ROOT NOT AVAILABLE — launch blocked!")
            app = self.application
            if chat_id and app:
                try:
                    await app.bot.send_message(
                        chat_id,
                        f"❌ <b>ROOT НЕДОСТУПЕН!</b> Запуск <code>{html.escape(name)}</code> заблокирован.\n"
                        f"Проверьте su -c 'id' на устройстве.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            return
        ci = self.config.get_clone(name)
        if not ci: return

        current = self.clone_states.get(name, CloneState.STOPPED)
        if current == CloneState.STARTING:
            return

        async with self._start_lock:
            suffix = name[-1].lower() if name.lower().startswith("clien") else name.lower()
            pkg = f"com.roblox.clien{suffix}"

            # ── GK: Ghost Killer — strict liveness via ps -A (zombie-aware) ──
            alive = await clone_pgrep_alive(suffix)
            if alive:
                # TCP check: 0 connections = phantom (zombie/ghost process)
                conns = await MonitorEngine.get_clone_connections(suffix)
                pid = await MonitorEngine.get_pid(suffix)
                thr = await MonitorEngine.get_threads(pid) if pid else 0

                # V10.7: <8 conns = zombie/stuck → silent force-stop + relaunch
                is_zombie = (thr == 0) or (conns < 8)
                if is_zombie:
                    logger.warning(
                        f"_enqueue_start [{name}]: zombie/phantom "
                        f"(threads={thr}, conns={conns}) — soft_clean + relaunch"
                    )
                    await self._soft_clean(suffix)
                    await asyncio.sleep(3)
                    # fall through to launch below
                else:
                    # Genuinely active (≥8 TCP) — skip injection
                    self.set_state(name, CloneState.RUNNING)
                    app = self.application
                    if chat_id and app:
                        try:
                            await app.bot.send_message(
                                chat_id,
                                f"👁 <code>{html.escape(name.replace('_','-'))}</code> "
                                f"активен ({conns} TCP).",
                                parse_mode="HTML")
                        except Exception: pass
                    return

            # If JSON said RUNNING but process is dead → auto-correct to IDLE
            if self.clone_states.get(name) == CloneState.RUNNING:
                self.set_state(name, CloneState.STOPPED)

            self.set_state(name, CloneState.STARTING)

            sm = None
            app = self.application
            if chat_id and app and not self.persistence.silent_mode:
                try:
                    sm = await app.bot.send_message(
                        chat_id,
                        f"▶️ <code>{html.escape(name.replace('_','-'))}</code>",
                        parse_mode="HTML")
                except Exception:
                    pass

            # ── V11.0: Safe Clean — only this clone's cache ──────────────────
            await self._soft_clean(suffix)
            await asyncio.sleep(3)  # let system reclaim RAM

            import json as _json
            p_id, l_code = None, None
            try:
                cfg_path = os.path.join(FARM_DIR, f"{DEVICE_ID}.json")
                if os.path.exists(cfg_path):
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cdata = _json.load(f)
                        p_id = cdata.get("placeID")
                        l_code = cdata.get("linkCode") or cdata.get("privateServerLink")
            except Exception as e:
                logger.error(f"server config read error: {e}")

            ok = await InjectionEngine.inject_and_launch(
                name, ci.get("cookie"), p_id, l_code, sm)

            if ok:
                self.set_state(name, CloneState.RUNNING)
                self.persistence.add_target(name, "RUNNING")
                # V12.0: Cache PID for fast liveness checks
                cached_pid = await MonitorEngine.get_pid(suffix)
                if cached_pid:
                    self.pid_cache[name] = cached_pid
            else:
                self.set_state(name, CloneState.STOPPED)

        # ── V10.6: 90s startup grace check ───────────────────────────────────
        if self.clone_states.get(name) == CloneState.RUNNING:
            await asyncio.sleep(90)
            grace_conns = await MonitorEngine.get_clone_connections(suffix)
            if grace_conns < 8:  # V11.0: ACTIVE threshold
                logger.warning(
                    f"Grace check [{name}]: conns={grace_conns} < 8 after 90s — retry"
                )
                admin = self.config.admin_ids[0] if self.config.admin_ids else None
                app_ref = self.application
                if admin and app_ref:
                    try:
                        await app_ref.bot.send_message(
                            admin,
                            f"⚠️ <code>{html.escape(name.replace('_','-'))}</code> "
                            f"grace fail ({grace_conns} TCP) → retry",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                self.set_state(name, CloneState.STOPPED)
                # Retry once
                asyncio.create_task(self._enqueue_start(name, admin))
                return

        await asyncio.sleep(10)
        await self.refresh_dashboard(force=True)

    async def _soft_clean(self, suffix: str) -> None:
        """
        V12.1 Safe Clean for a single clone before launch:
        - am stack remove (detach floating window)
        - am force-stop (kill phantom process)
        - rm cache + code_cache (free stored data, optimize RAM)
        NOTE: No drop_caches — /proc/sys/vm is read-only.
        """
        await _isolated_stop(suffix)

    async def _do_deep_clean(self) -> None:
        """Global deep clean: sync only (no drop_caches — read-only on some ROMs)."""
        await run_bash("su -c 'sync'")

    @staticmethod
    async def _get_free_ram_mb() -> float:
        """Read free RAM in MB from /proc/meminfo or free -m. Returns 9999 on error."""
        try:
            with open("/proc/meminfo", "r") as f:
                content = f.read()
            m = re.search(r"MemAvailable:\s+(\d+)\s+kB", content)
            if not m:
                m = re.search(r"MemFree:\s+(\d+)\s+kB", content)
            if m:
                return int(m.group(1)) / 1024.0
        except Exception:
            pass
        # Fallback: free -m
        try:
            _, out, _ = await run_bash("free -m | grep Mem")
            parts = out.split()
            if len(parts) >= 4:
                return float(parts[3])  # available column
        except Exception:
            pass
        return 9999.0  # assume OK if can't read

    async def _wait_for_ram(self, min_free_mb: float = 550, max_wait: int = 60) -> bool:
        """
        V12.0 RAM-Check: wait until free RAM >= min_free_mb.
        Retries every 20s up to max_wait seconds total.
        Returns True if RAM is OK, False if timed out.
        """
        waited = 0
        while waited < max_wait:
            free_mb = await self._get_free_ram_mb()
            if free_mb >= min_free_mb:
                return True
            logger.warning(f"RAM-Check: {free_mb:.0f}MB free < {min_free_mb}MB — waiting 20s…")
            await asyncio.sleep(20)
            waited += 20
        return False

    async def _mass_start(self, chat_id):
        """
        V12.2 Intelligent Queue (Anti-Freeze Start):
        - NO nuclear clean (would kill Python process)
        - Per-clone Safe Clean before each launch
        - 10s interval between launches
        - RAM-Check: if free < 600MB, delay 20s extra
        - Saves enabled state for each clone
        - SILENT_MODE: suppress per-clone messages
        - abort_launch: instant abort on mass_stop
        """
        STAGGER_SEC = 10
        RAM_MIN_MB  = 600
        self.is_mass_starting = True
        self.abort_launch = False  # V12.2: reset abort flag on new mass start
        clones = [c for c in self.config.clones_data if c.get("active", True)]

        app = self.application
        if not (chat_id and app): return

        try:
            m = await app.bot.send_message(
                chat_id,
                f"🚀 Staggered Start V12.4 — {len(clones)} клонов (пауза {STAGGER_SEC}с + RAM check)…",
                parse_mode="HTML",
            )

            # Reset all statuses to idle first
            for c in self.config.clones_data:
                n = c.get("name")
                if n:
                    self.set_state(n, CloneState.STOPPED)

            for idx, c in enumerate(clones, 1):
                if not self.is_mass_starting or self.abort_launch:
                    logger.info("Mass Start: aborted by stop signal")
                    break
                name = c.get("name")
                if not name:
                    continue

                # V12.2 RAM-Check before each clone launch
                ram_ok = await self._wait_for_ram(RAM_MIN_MB, max_wait=60)
                if not ram_ok:
                    logger.warning(f"Mass Start [{name}]: RAM still low after 60s, launching anyway")

                # Mark enabled in session_state BEFORE launch
                self.persistence.set_clone_enabled(name, True)
                if not self.persistence.silent_mode:
                    await app.bot.send_message(
                        chat_id,
                        f"[{idx}/{len(clones)}] <code>{html.escape(name.replace('_', '-'))}</code>",
                        parse_mode="HTML",
                    )
                await self._enqueue_start(name, chat_id)
                if idx < len(clones) and self.is_mass_starting and not self.abort_launch:
                    await asyncio.sleep(STAGGER_SEC)
        except Exception as e:
            logger.error(f"Mass Start Error: {e}")
        finally:
            self.is_mass_starting = False

    async def _mass_stop(self, chat_id):
        """
        V11.0 Mass stop — per-clone am force-stop + cache clean.
        Loops through each active clone with 1s pause between them.
        No pkill, no grouped commands — surgical per-clone stop.
        """
        self.is_mass_starting = False
        self.abort_launch = True  # V12.2: signal any running launch queue to stop
        app = self.application
        if not (chat_id and app): return

        try:
            m = await app.bot.send_message(chat_id, "🛑 Остановка…", parse_mode="HTML")

            for c in self.config.clones_data:
                n = c.get("name")
                if not n:
                    continue
                suffix = n[-1].lower() if n.lower().startswith("clien") else n.lower()
                # Reset bot state + session_state
                self.set_state(n, CloneState.STOPPED)
                self.persistence.remove_target(n)
                self.persistence.set_clone_enabled(n, False)

                # V12.1: Window-safe isolated stop + cache clean
                await _isolated_stop(suffix)
                await asyncio.sleep(1)  # 1s pause between clones

            await m.edit_text("🛑 Готово. Кэш всех клонов очищен.", parse_mode="HTML")
        except Exception as e:
            logger.error(f"Mass Stop Error: {e}")

        await self.refresh_dashboard(force=True)

    async def _stop_clone(self, name: Optional[str], chat_id):
        """V12.1 Stop a single clone — window-safe isolated stop. Never pkill."""
        if not name: return
        self.set_state(name, CloneState.STOPPED)
        self.persistence.remove_target(name)
        suffix = name[-1].lower() if name.lower().startswith("clien") else name.lower()
        # V12.1: Window-safe isolated stop + cache clean
        await _isolated_stop(suffix)
        await asyncio.sleep(2)  # let system free RAM
        app = self.application
        if chat_id and app:
            try:
                await app.bot.send_message(
                    chat_id,
                    f"🛑 <code>{html.escape(name.replace('_', '-'))}</code> · кэш очищен",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        await self.refresh_dashboard(force=True)

    async def add_log_line(self, line: str):
        await self.console_queue.put(line)
        lu = line.upper()
        if any(k in lu for k in ("ERROR", "CRITICAL", "SUCCESS")):
            admin_id = self.config.admin_ids[0] if self.config.admin_ids else None
            if admin_id:
                asyncio.create_task(self.flush_console_buffer(admin_id))

    async def flush_console_buffer(self, chat_id: int):
        if self.console_queue.empty(): return
        
        lines = []
        while not self.console_queue.empty():
            lines.append(await self.console_queue.get())
        
        batch_str = "\n".join(lines)
        if len(batch_str) > 4000: 
            batch_str = batch_str[:3900] + "\n[TRUNCATED...]"
        
        self.last_console_flush = time.time()

        app = self.application
        if app:
            try:
                await app.bot.send_message(chat_id, f"<code>{batch_str}</code>", parse_mode="HTML")
            except TelegramError as e:
                if "retry after" in str(e).lower():
                    wait = 5
                    match = re.search(r'after (\d+)', str(e))
                    if match: wait = int(match.group(1))
                    await asyncio.sleep(wait)
                    try:
                        await app.bot.send_message(chat_id, f"<code>{batch_str}</code>", parse_mode="HTML")
                    except Exception as e2:
                        logger.error(f"Console Retry failed: {e2}")
                else:
                    logger.error(f"Console send error: {e}")

    async def _console_auto_flush_loop(self, chat_id: int):
        while self._console_on:
            await asyncio.sleep(0.5) 
            
            qsize = self.console_queue.qsize()
            elapsed = time.time() - self.last_console_flush
            
            if qsize > 0:
                if qsize >= 10 or elapsed >= 1.5:
                    await self.flush_console_buffer(chat_id)

    async def _toggle_console(self, context, chat_id: int):
        self._console_on = not self._console_on
        if self._console_on:
            hdlr = TelegramLogHandler(self)
            self._log_handler = hdlr
            logging.getLogger().addHandler(hdlr)
            self._console_task = asyncio.create_task(self._console_auto_flush_loop(chat_id))
            logger.info("Console: ON")
        else:
            hdlr_to_rem = self._log_handler
            if hdlr_to_rem:
                logging.getLogger().removeHandler(hdlr_to_rem)
                self._log_handler = None
            task = self._console_task
            if task:
                task.cancel()
                self._console_task = None
            logger.info("Console: OFF")

    async def _take_screenshot(self, message):
        buf = "/data/local/tmp/aegis_shot.png"
        try:
            ret, _, err = await run_bash(f"su -c 'screencap -p {buf} && chmod 644 {buf}'")
            if ret != 0:
                await message.reply_text(f"❌ screencap failed: {err}")
                return
            with open(buf, "rb") as f:
                await message.reply_photo(photo=f, caption=f"📸 {DEVICE_ID}")
        except Exception as e:
            logger.error(f"Screenshot error: {e}")
            await message.reply_text(f"❌ Screenshot Exception: {e}")

    async def _git_sync(self, chat_id: int):
        await self.add_log_line("git sync…")
        try:
            await run_bash('su -c "chmod -R 777 ."')
            await run_bash("git fetch --all")
            await run_bash("git reset --hard origin/main")
            await run_bash('find . -type d -name "__pycache__" -exec rm -rf {} +')
            await run_bash("pip install -r requirements.txt")
            
            if chat_id and self.application:
                await self.application.bot.send_message(
                    chat_id, "✅ Обновлено, перезапуск…", parse_mode="HTML")
            
            await asyncio.sleep(2)
            os.execv(sys.executable, ['python'] + sys.argv)
        except Exception as e:
            logger.error(f"git sync error: {e}")
            if chat_id and self.application:
                await self.application.bot.send_message(chat_id, f"❌ Update failed: {e}")

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)
        admin_id = self.config.admin_ids[0] if self.config.admin_ids else None
        if admin_id:
            try:
                err_esc = html.escape(str(context.error))
                await context.bot.send_message(
                    admin_id, f"⚠️ <code>{err_esc}</code>", parse_mode="HTML")
            except Exception:
                pass

    async def run(self):
        logger.info(f"Aegis V{VERSION} start — {DEVICE_ID}")
        # immortality already applied at module level before arg parsing

        # V12.3: Start failsafe RAM watchdog as separate process
        if HAS_ROOT and ENABLE_AUTO_REBOOT:
            _failsafe_proc = multiprocessing.Process(
                target=_ram_failsafe_loop,
                args=(FARM_DIR,),
                daemon=True,
                name="aegis-ram-failsafe",
            )
            _failsafe_proc.start()
            logger.info(f"Failsafe RAM watchdog started (PID {_failsafe_proc.pid}, "
                         f"threshold={RAM_REBOOT_PERCENT}%, poll={RAM_WATCHDOG_POLL}s)")
        elif not HAS_ROOT:
            logger.warning("Failsafe RAM watchdog SKIPPED — no root access!")

        self.application = ApplicationBuilder().token(self.config.bot_token).build()
        app = self.application

        app.add_handler(CommandHandler("start",   self.cmd_start))
        app.add_handler(CommandHandler("console", self.cmd_console))
        app.add_handler(CommandHandler("exec",    self.cmd_exec))
        app.add_handler(CommandHandler("update",  self.cmd_update))
        app.add_handler(CommandHandler("mass_start",  self.cmd_mass_start))
        app.add_handler(CommandHandler("mass_stop",   self.cmd_mass_stop))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        app.add_handler(CallbackQueryHandler(self.handle_callback))
        app.add_error_handler(self.error_handler)

        await app.initialize()
        await app.start()

        asyncio.create_task(watchdog_loop(app, self))

        self.reset_all_states()

        # ── V11.0 AUTO-RECOVERY: restore from session_state.json ─────────────
        asyncio.create_task(self._auto_recover())

        logger.info(f"Aegis V{VERSION} online — {DEVICE_ID}")

        await app.updater.start_polling(drop_pending_updates=True)

        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await app.stop()
            await app.shutdown()


    async def _auto_recover(self) -> None:
        """
        V12.0 Persistent AutoResume — reads session_state.json (not DEV file).
        Restores all clones that had enabled=True before crash/reboot.
        Uses Intelligent Queue (15s stagger + RAM-Check) to avoid OOM.
        Runs once, 10s after startup.
        """
        await asyncio.sleep(10)

        if not self.persistence.auto_restore:
            logger.info("AutoResume: disabled in settings, skipping.")
            return

        enabled_clones = self.persistence.get_enabled_clones()
        if not enabled_clones:
            logger.info("AutoResume: no enabled clones in session_state.json.")
            return

        logger.info(f"AutoResume: {len(enabled_clones)} clone(s) to restore: {enabled_clones}")
        admin = self.config.admin_ids[0] if self.config.admin_ids else None

        if admin and self.application and not self.persistence.silent_mode:
            try:
                await self.application.bot.send_message(
                    admin,
                    f"🔄 AutoResume: restoring {len(enabled_clones)} clone(s)…",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        STAGGER_SEC = 10
        RAM_MIN_MB  = 600

        for idx, name in enumerate(enabled_clones, 1):
            # V12.2: check abort flag before each clone
            if self.abort_launch:
                logger.info("AutoResume: aborted by stop signal")
                break

            # Verify clone exists in config
            if not self.config.get_clone(name):
                logger.warning(f"AutoResume: [{name}] not in config, skipping.")
                continue

            # V12.2: RAM-Check before each resume
            ram_ok = await self._wait_for_ram(RAM_MIN_MB, max_wait=60)
            if not ram_ok:
                logger.warning(f"AutoResume [{name}]: RAM still low after 60s, launching anyway")

            suffix = name[-1].lower() if name.lower().startswith("clien") else name.lower()
            alive = await clone_pgrep_alive(suffix)
            if alive:
                # Check if truly active via TCP
                conns = await MonitorEngine.get_clone_connections(suffix)
                if conns >= 8:
                    logger.info(f"AutoResume [{name}]: alive + {conns} TCP → marking RUNNING")
                    self.set_state(name, CloneState.RUNNING)
                    continue
                else:
                    logger.warning(f"AutoResume [{name}]: alive but CON={conns} → Hard Reset")
                    await _isolated_stop(suffix)
                    await asyncio.sleep(3)

            logger.info(f"AutoResume [{idx}/{len(enabled_clones)}]: launching {name}")
            if admin and self.application and not self.persistence.silent_mode:
                try:
                    await self.application.bot.send_message(
                        admin,
                        f"🔄 [{idx}/{len(enabled_clones)}] <code>{html.escape(name.replace('_','-'))}</code>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            asyncio.create_task(self._enqueue_start(name, admin))
            # Staggered: wait 10s between launches
            if idx < len(enabled_clones):
                await asyncio.sleep(STAGGER_SEC)


if __name__ == "__main__":
    try:
        asyncio.run(AegisBot().run())
    except Exception as e:
        logger.critical(f"Fatal startup error: {e}")
