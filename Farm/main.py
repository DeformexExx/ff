# -*- coding: utf-8 -*-
# main.py — Project Aegis V6.0 Stable Edition
import os
import sys
import enum
import asyncio
import logging
import time
import tempfile
import re
import html
from typing import Optional, Dict

# ── ABSOLUTE PATH LOCK ─────────────────────────────────────────────────────
_bot_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(_bot_dir)
sys.path.insert(0, _bot_dir)

from telegram import Update, Message
from telegram.ext import (
    ApplicationBuilder, Application, CommandHandler,
    ContextTypes, MessageHandler, filters, CallbackQueryHandler
)
from telegram.error import TelegramError

from config_manager      import ConfigManager
from ui_manager          import UIManager
from monitor             import MonitorEngine
from injection_engine    import InjectionEngine
from bash_utils          import run_bash
from persistence_manager import PersistenceManager

# ═══════════════════════════════════════════════════════════════════════════
# VERSION
# ═══════════════════════════════════════════════════════════════════════════
VERSION = "6.0 (Stable)"

# ── DEVICE ID ──────────────────────────────────────────────────────────────
if len(sys.argv) < 2:
    print("❌  Usage: python main.py <DEVICE_ID>")
    sys.exit(1)

DEVICE_ID = sys.argv[1]
FARM_DIR  = _bot_dir
BOOT_LOG  = os.path.join(FARM_DIR, "boot_log.txt")

# ── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [{DEVICE_ID}/V{VERSION}] [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BOOT_LOG, encoding="utf-8"),
    ]
)
logger = logging.getLogger("AegisV40")

# ═══════════════════════════════════════════════════════════════════════════
# STATE MACHINE ENUM
# ═══════════════════════════════════════════════════════════════════════════
class CloneState(str, enum.Enum):
    STOPPED  = "STOPPED"   # Not running
    STARTING = "STARTING"  # 1/4 - 4/4 + 300s grace window
    RUNNING  = "RUNNING"   # Fully online, monitored by Watchdog

class TelegramLogHandler(logging.Handler):
    def __init__(self, bot: "AegisBot"):
        super().__init__()
        self.bot = bot
    def emit(self, record):
        asyncio.create_task(self.bot.add_log_line(f"[{record.levelname[:3]}] {self.format(record)}"))


async def watchdog_loop(application: Application, bot_instance: "AegisBot"):
    """
    CRITICAL RULE: Watchdog is LEGALLY BLIND to any clone not in RUNNING state.
    Only RUNNING clones are checked for Frozen/Leaking conditions.
    """

    offline_strikes: Dict[str, int]   = {}
    low_thread_strikes: Dict[str, int] = {}
    last_action:     Dict[str, float] = {}

    while True:
        await asyncio.sleep(30)
        
        try:
            now = time.time()

            for name, state in list(bot_instance.clone_states.items()):
                if ":" in name: continue 

                if state != CloneState.RUNNING:
                    continue

                if now - last_action.get(name, 0) < 60:
                    continue

                st = await MonitorEngine.get_clone_status(name)
                needs_action = False
                reason       = ""

                if "Offline" in st:
                    offline_strikes[name] = offline_strikes.get(name, 0) + 1
                    bot_instance.clone_states[f"{name}:status"] = "Offline"
                    if offline_strikes[name] >= 3:
                        reason       = f"Offline Strike 3/3"
                        needs_action = True
                    else:
                        logger.info(f"Watchdog [{name}]: Offline strike {offline_strikes[name]}/3")
                else:
                    offline_strikes[name] = 0
                    
                    m_thr = re.search(r"Thr:\s*(\d+)", st)
                    thr = int(m_thr.group(1)) if m_thr else 0
                    bot_instance.clone_states[f"{name}:threads"] = str(thr)
                    
                    uptime = now - bot_instance.running_since.get(name, now)
                    
                    if uptime < 120:
                        low_thread_strikes[name] = 0
                        bot_instance.clone_states[f"{name}:status"] = "Starting Up"
                        continue
                        
                    # V6.0 Logic: Anti-False Positive
                    if thr >= 130:
                        low_thread_strikes[name] = 0
                        bot_instance.clone_states[f"{name}:status"] = "Stable"
                        continue
                        
                    # V6.0 Final Logic
                    if thr == 0:
                        reason       = "Dead (Threads: 0)"
                        needs_action = True
                    elif 0 < thr < 130:
                        low_thread_strikes[name] = low_thread_strikes.get(name, 0) + 1
                        logger.info(f"[{name.upper()}] Low threads: {thr}. Strike {low_thread_strikes[name]}/6.")
                        if low_thread_strikes[name] >= 6:
                            reason       = f"Freeze (Thr:{thr}<130 for 3m)"
                            needs_action = True
                    else:
                        low_thread_strikes[name] = 0

                    if needs_action:
                        bot_instance.clone_states[f"{name}:status"] = "Lagging"
                    else:
                        bot_instance.clone_states[f"{name}:status"] = "Stable"

                if needs_action:
                    last_action[name]        = now
                    offline_strikes[name]    = 0
                    low_thread_strikes[name] = 0
                    bot_instance.set_state(name, CloneState.STOPPED)
                    
                    logger.warning(f"Watchdog: [{name}] {reason}. Relaunching…")
                    admin = bot_instance.config.admin_ids[0] if bot_instance.config.admin_ids else None
                    if admin:
                        try:
                            n_esc = html.escape(name.replace('_', '-'))
                            r_esc = html.escape(reason.replace('_', '-'))
                            await application.bot.send_message(
                                admin,
                                f"🐕 <b>Watchdog</b>: <code>{n_esc}</code> → {r_esc}\n🧹 PURGE &amp; RELAUNCH…",
                                parse_mode="HTML"
                            )
                        except TelegramError:
                            pass
                    asyncio.create_task(bot_instance._purge_restart(name, admin))

            await bot_instance.refresh_dashboard()

        except Exception as e:
            logger.error(f"watchdog_loop error: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# BOT CLASS
# ═══════════════════════════════════════════════════════════════════════════
class AegisBot:
    def __init__(self):
        self.config      = ConfigManager(DEVICE_ID, FARM_DIR)
        self.persistence = PersistenceManager(FARM_DIR)
        self.application: Optional[Application] = None
        self._dash_msg: Optional[Message] = None
        self._log_handler: Optional[logging.Handler] = None
        self._console_on: bool = self.persistence.console_mode
        self._last_ui_update: float = 0.0

        # ── CONSOLE BUFFER (V5.3 Turbo) ─────────────────────────────────────
        self.console_queue = asyncio.Queue()
        self.last_console_flush: float = 0.0
        self.console_lock = asyncio.Lock()
        self._console_task: Optional[asyncio.Task] = None

        # ── STATE MACHINE ─────────────────────────────────────────────────
        self.clone_states: Dict[str, CloneState] = {}

        # ── MAINTENANCE ───────────────────────────────────────────────────
        self.maintenance_enabled: bool = False
        self.maintenance_minutes: int  = 30
        self._maint_in_progress: bool  = False
        self._waiting_for_timer: bool  = False

        # Uptime tracking: {clone_name: timestamp when RUNNING reached}
        self.running_since: Dict[str, float] = {}

        # asyncio.Lock — only ONE clone in STARTING state at a time
        self._start_lock = asyncio.Lock()

        # Initialize all known clones to STOPPED
        for c in self.config.clones_data:
            n = c.get("name")
            if n:
                self.clone_states[n] = CloneState.STOPPED

    # ── State helpers ─────────────────────────────────────────────────────
    def set_state(self, name: str, state: CloneState):
        if ":" in name: return
        old = self.clone_states.get(name, CloneState.STOPPED)
        self.clone_states[name] = state
        if state == CloneState.RUNNING:
            self.running_since[name] = time.time()
        elif old == CloneState.RUNNING:
            self.running_since.pop(name, None)
        logger.info(f"State [{name}]: {str(old)} → {str(state)}")
        if state in [CloneState.RUNNING, CloneState.STOPPED]:
            self.config.update_clone_status(name, str(state))

    # ── Admin guard ───────────────────────────────────────────────────────
    async def _is_admin(self, uid: int) -> bool:
        return uid in self.config.admin_ids

    # ─────────────────────────────────────────────────────────────────────
    # Handlers
    # ─────────────────────────────────────────────────────────────────────
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

    async def _purge_restart(self, name: str, chat_id):
        suffix = name[-1].lower() if name.lower().startswith("clien") else name.lower()
        await run_bash(f"su -c 'am force-stop com.roblox.clien{suffix}'")
        await run_bash(f"su -c 'rm -rf /data/data/com.roblox.clien{suffix}/cache/*'")
        await run_bash(f"su -c 'rm -rf /data/data/com.roblox.clien{suffix}/code_cache/*'")
        await self._enqueue_start(name, chat_id)

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
        msg = await update.message.reply_text("🔄 <b>Hard Update Sequence Started...</b>", parse_mode="HTML")
        
        try:
            # 1. Hard Reset (V6.0 Robust)
            await msg.edit_text("🛰 (1/4) Fetching & Hard Resetting...")
            await run_bash(f"git -C {_bot_dir} fetch --all")
            await run_bash(f"git -C {_bot_dir} reset --hard origin/main")
            await run_bash(f"git -C {_bot_dir} pull origin main")

            # 2. Cache Clean
            await msg.edit_text("🧹 (2/4) Purging __pycache__ directories...")
            await run_bash(f'find {_bot_dir} -type d -name "__pycache__" -exec rm -rf {{}} +')

            # 3. Dependencies
            req_path = os.path.join(_bot_dir, "requirements.txt")
            if os.path.exists(req_path):
                await msg.edit_text("📦 (3/4) Syncing requirements.txt...")
                await run_bash(f"pip install -r {req_path}")

            # 4. Feedback & Final Restart
            await msg.edit_text("✅ <b>Файлы обновлены, кэш очищен. Перезагрузка...</b>", parse_mode="HTML")
            await asyncio.sleep(2)
            
            # Re-exec process to apply changes immediately
            os.execv(sys.executable, ['python'] + sys.argv)
            
        except Exception as e:
            logger.error(f"Update Module Error: {e}")
            if msg:
                try:
                    e_esc = html.escape(str(e))
                    await msg.edit_text(f"❌ <b>Update Failed</b>: <code>{e_esc}</code>", parse_mode="HTML")
                except Exception: pass

    async def cmd_mass_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update.effective_user.id): return
        chat = update.message.chat_id
        await update.message.reply_text("🚀 <b>Mass Start Initiated</b>\n⏳ Clones launch sequentially (10s gap).", parse_mode="HTML")
        asyncio.create_task(self._mass_start(chat))

    async def cmd_mass_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update.effective_user.id): return
        chat = update.message.chat_id
        for c in self.config.clones_data:
            asyncio.create_task(self._stop_clone(c.get("name"), chat))
        await update.message.reply_text("❄️ <b>Mass Stop issued.</b>", parse_mode="HTML")

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update.effective_user.id): return
        t = update.message.text
        if   t == "📱 DEVICE": await self._open_device(update)
        elif t == "🤖 CLONES": await self.open_clones_hub(update)
        elif t == "⚙️ SYSTEM": await self._open_system(update)
        elif t == "⚙️ Maintenance": await self._open_maintenance(update)
        elif self._waiting_for_timer: await self._handle_timer_input(update)

    async def _open_maintenance(self, update: Update):
        m_state = 'ENABLED' if self.maintenance_enabled else 'DISABLED'
        await update.message.reply_text(
            "🛠 <b>MAINTENANCE SETTINGS</b>\n"
            f"Auto-Purge: <code>{m_state}</code>\n"
            f"Interval: <code>{self.maintenance_minutes} min</code>",
            reply_markup=UIManager.get_maintenance_keyboard(self.maintenance_enabled, self.maintenance_minutes),
            parse_mode="HTML"
        )

    async def _handle_timer_input(self, update: Update):
        t = update.message.text
        if t.isdigit():
            mins = int(t)
            if 5 <= mins <= 1440:
                self.maintenance_minutes = mins
                self._waiting_for_timer = False
                await update.message.reply_text(f"✅ Timer set to <code>{mins}</code> minutes.", parse_mode="HTML")
                await self._open_maintenance(update)
            else:
                await update.message.reply_text("❌ Please enter a value between 5 and 1440.")
        else:
            await update.message.reply_text("❌ Invalid input. Please send only digits (minutes).")

    async def _open_device(self, update: Update):
        ram, cpu, temp = await MonitorEngine.get_system_stats()
        await update.message.reply_text(
            UIManager.format_dashboard(DEVICE_ID, ram, cpu, temp, VERSION),
            reply_markup=UIManager.get_device_keyboard(),
            parse_mode="HTML"
        )

    async def _open_system(self, update: Update):
        await update.message.reply_text(
            "⚙️ <b>SYSTEM</b>",
            reply_markup=UIManager.get_system_keyboard(self._console_on, self.persistence.auto_restore),
            parse_mode="HTML"
        )

    async def open_clones_hub(self, update: Update):
        try:
            self.config.reload()
            text = await self._build_hub_text()
            kb   = UIManager.get_clones_hub_keyboard(self.config.clones_data)
            self._dash_msg = await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"open_clones_hub error: {e}")
            await update.message.reply_text(f"❌ Hub error: {e}")

    async def _build_hub_text(self) -> str:
        tasks = []
        for c in self.config.clones_data:
            name = c.get("name")
            if not name: continue
            
            suffix = name[-1].lower() if name.lower().startswith("clien") else name.lower()
            
            async def update_clone_stats(n, sfx):
                pid = await MonitorEngine.get_pid(sfx)
                thr = await MonitorEngine.get_threads(pid) if pid else 0
                self.clone_states[f"{n}:threads"] = str(thr)
                
            tasks.append(update_clone_stats(name, suffix))
            
        if tasks:
            await asyncio.gather(*tasks)

        state_map = {}
        for n, s in self.clone_states.items():
            # Preserving ':' for status/threads mapping while sanitizing names
            if ":" in n:
                name_part, key_part = n.split(":", 1)
                clean_n = f"{name_part.replace('_', '-')}:{key_part}"
            else:
                clean_n = n.replace('_', '-')
            state_map[clean_n] = str(s)
            
        return UIManager.format_clones_hub(self.config.clones_data, state_map, VERSION)

    async def refresh_dashboard(self, force=False):
        if not self._dash_msg: return
        now = time.time()
        # UI Throttle: 60 seconds unless forced
        if not force and (now - self._last_ui_update < 60):
            return
            
        try:
            text = await self._build_hub_text()
            kb   = UIManager.get_clones_hub_keyboard(self.config.clones_data)
            await self._dash_msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
            self._last_ui_update = now
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────
    # Callback handler
    # ─────────────────────────────────────────────────────────────────────
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
                        UIManager.get_system_keyboard(self._console_on, self.persistence.auto_restore))
                except Exception: pass

            elif d == "toggle_console":
                await self._toggle_console(context, chat)
                try:
                    await q.edit_message_reply_markup(
                        UIManager.get_system_keyboard(self._console_on, self.persistence.auto_restore))
                except Exception: pass

            elif d == "sys_sync":  await self._git_sync(chat)
            elif d == "sys_screenshot": await self._take_screenshot(q.message)
            elif d == "sys_help": await q.message.reply_text(UIManager.get_help_text(), parse_mode="HTML")
            
            elif d == "mass_start":
                chat_id = q.message.chat.id
                asyncio.create_task(self._mass_start(chat_id))
                await q.message.reply_text("🚀 Запуск фермы инициирован... Чистка кэша завершена.", parse_mode="HTML")

            elif d == "mass_stop":
                asyncio.create_task(self._mass_stop(chat))
                await q.message.reply_text("🛑 Массовая остановка инициирована...", parse_mode="HTML")

            elif d.startswith("start_"):
                name = d[6:]
                asyncio.create_task(self._enqueue_start(name, chat))

            elif d.startswith("stop_"):
                asyncio.create_task(self._stop_clone(d[5:], chat))

            elif d.startswith("shot_"):
                await self._take_screenshot(q.message)

            elif d.startswith("clone_"):
                name_esc  = html.escape(name.replace('_', '-'))
                raw_state = self.clone_states.get(name, CloneState.STOPPED)
                state_val = str(raw_state)
                
                s_val_esc = html.escape(state_val)
                kb    = UIManager.get_clone_submenu(name, state_val)
                await context.bot.send_message(
                    chat,
                    f"⚙️ <b>{name_esc.upper()}</b>\nState: <code>{s_val_esc}</code>",
                    reply_markup=kb, parse_mode="HTML"
                )

            elif d == "maint_toggle":
                self.maintenance_enabled = not self.maintenance_enabled
                try:
                    await q.edit_message_reply_markup(
                        UIManager.get_maintenance_keyboard(self.maintenance_enabled, self.maintenance_minutes))
                except Exception: pass

            elif d == "maint_set_timer":
                self._waiting_for_timer = True
                await q.message.reply_text("⏱ <b>SET TIMER</b>\nSend the maintenance interval in minutes (e.g., <code>60</code>).", parse_mode="HTML")

            elif d == "maint_run_now":
                if self._maint_in_progress:
                    await q.message.reply_text("⚠️ Maintenance already in progress.")
                else:
                    asyncio.create_task(self.run_maintenance_cycle(chat))

        except Exception as e:
            logger.error(f"Callback [{d}] error: {e}")
            try:
                await context.bot.send_message(chat, f"❌ Error: {e}")
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────
    # STATE MACHINE — Clone startup / stop logic
    # ─────────────────────────────────────────────────────────────────────
    async def _enqueue_start(self, name: Optional[str], chat_id):
        """
        Acquires the global asyncio.Lock before starting any clone.
        Guarantees only ONE clone is in STARTING state at a time.
        After 4/4 completes, waits 300s BEFORE transitioning to RUNNING.
        """
        if not name: return
        ci = self.config.get_clone(name)
        if not ci: return

        # If already starting or running, skip
        current = self.clone_states.get(name, CloneState.STOPPED)
        if current == CloneState.STARTING:
            logger.info(f"_enqueue_start: [{name}] already STARTING. Skip.")
            return

        async with self._start_lock:
            # ── 1. Force Identity & Inject ──────────────────────────────
            self.set_state(name, CloneState.STARTING)
            
            # V5.6 "NATIVE SIGHT": Check if already running
            status_str = await MonitorEngine.get_clone_status(name)
            if "Offline" not in status_str:
                logger.info(f"Catch Running: {name} already has a PID. Skipping injection.")
                self.set_state(name, CloneState.RUNNING)
                app = self.application
                if chat_id and app:
                    try:
                        n_esc = html.escape(name)
                        await app.bot.send_message(
                            chat_id, f"👁 <code>{n_esc}</code>: Process detected. Attaching...", parse_mode="HTML")
                    except Exception: pass
                return

            sm = None
            app = self.application
            if chat_id and app:
                try:
                    name_esc = html.escape(name.replace('_', '-'))
                    sm = await app.bot.send_message(
                        chat_id, f"🚀 <code>{name_esc}</code>: Запуск...", parse_mode="HTML")
                except Exception:
                    pass

            # V6.0 Explicit Cache Wipe before start
            suffix = name[-1].lower() if name.startswith("clien") else name.lower()
            await run_bash(f"su -c 'rm -rf /data/data/com.roblox.clien{suffix}/cache/*'")

            # V5.0 Sequence: Cookie -> Launch only
            urls = self.config.servers_list
            ok = await InjectionEngine.inject_and_launch(
                name, ci.get("cookie"), urls[0] if urls else None, sm)

            if ok:
                self.set_state(name, CloneState.RUNNING)
                self.persistence.add_target(name, "RUNNING")
            else:
                self.set_state(name, CloneState.STOPPED)

            # Global 10s stagger delay between starts to avoid CPU spikes
            await asyncio.sleep(10)

        await self.refresh_dashboard(force=True)

    async def _mass_start(self, chat_id):
        """Sequential mass start via the _start_lock queue."""
        clones = [c for c in self.config.clones_data if c.get("active", True)]
        
        app = self.application
        if not (chat_id and app): return

        try:
            m = await app.bot.send_message(chat_id, "🚀 <b>Запуск фермы инициирован...</b>", parse_mode="HTML")
            
            # Step 1: Wipe Cache
            await m.edit_text("🚀 <b>Запуск фермы инициирован...</b>\n🧹 <i>Очистка кэша...</i>", parse_mode="HTML")
            for c in clones:
                name = c.get("name")
                if name:
                    suffix = name[-1].lower() if name.startswith("clien") else name.lower()
                    await run_bash(f"su -c 'rm -rf /data/data/com.roblox.clien{suffix}/cache/*'")
            
            await m.edit_text("🚀 <b>Запуск фермы инициирован... Чистка кэша завершена.</b>", parse_mode="HTML")
                
            for idx, c in enumerate(clones, 1):
                name = c.get("name")
                if not name: continue
                await app.bot.send_message(
                    chat_id,
                    f"🚀 <b>Queue [{idx}/{len(clones)}]</b>: <code>{html.escape(name.replace('_', '-'))}</code>",
                    parse_mode="HTML"
                )
                await self._enqueue_start(name, chat_id)
        except Exception as e:
            logger.error(f"Mass Start Error: {e}")

    async def _mass_stop(self, chat_id):
        """Sequential mass stop."""
        app = self.application
        if not (chat_id and app): return

        try:
            m = await app.bot.send_message(chat_id, "🛑 <b>МАСС СТОП: остановка всех...</b>", parse_mode="HTML")
            
            for c in self.config.clones_data:
                name = c.get("name")
                if not name: continue
                suffix = name[-1].lower() if name.startswith("clien") else name.lower()
                await run_bash(f"su -c 'am force-stop com.roblox.clien{suffix}'")
                self.set_state(name, CloneState.STOPPED)
                self.persistence.remove_target(name)
                self.config.update_clone_status(name, "OFFLINE")

            await m.edit_text("🛑 <b>МАСС СТОП: все процессы завершены.</b>", parse_mode="HTML")
        except Exception as e:
            logger.error(f"Mass Stop Error: {e}")

        await self.refresh_dashboard(force=True)

    async def _stop_clone(self, name: Optional[str], chat_id):
        if not name: return
        self.set_state(name, CloneState.STOPPED)
        self.persistence.remove_target(name)
        await InjectionEngine.stop(name)
        app = self.application
        if chat_id and app:
            try:
                await app.bot.send_message(
                    chat_id, f"🌑 <code>{html.escape(name.replace('_', '-'))}</code> stopped.", parse_mode="HTML")
            except Exception:
                pass
        await self.refresh_dashboard(force=True)

    # ─────────────────────────────────────────────────────────────────────
    # Auto-resume on startup
    # ─────────────────────────────────────────────────────────────────────
    async def _auto_resume(self):
        """
        V5.7 Smart Auto-Resume: Check PIDs first, then staggered boot.
        """
        await asyncio.sleep(10) # Initial wait for root/network
        admin_id = self.config.admin_ids[0] if self.config.admin_ids else None
        app = self.application
        
        for clone in self.config.clones_data:
            name = clone.get("name")
            expected = clone.get("status", "STOPPED").upper()
            if not name: continue
            
            # 1. System Check (V5.7 awk chain)
            status_str = await MonitorEngine.get_clone_status(name)
            if "Offline" not in status_str:
                logger.info(f"Auto-Resume: {name} already running. Attaching...")
                self.set_state(name, CloneState.RUNNING)
                continue
                
            # 2. Boot Enqueue (20s Stagger)
            if expected in ("RUNNING", "IDLE"):
                logger.warning(f"Auto-Resume: {name} expected {expected} but offline. Starting in 20s...")
                asyncio.create_task(self._enqueue_start(name, admin_id))
                await asyncio.sleep(20) # V5.7 Stagger

    # ─────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────
    # ── CONSOLE BATCHER (V5.7 Turbo 2.0) ──────────────────────────────────
    async def add_log_line(self, line: str):
        await self.console_queue.put(line)
        # V5.7 Priority: Flush immediately on ERROR, CRITICAL, or SUCCESS
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
        
        batch_str = "[BATCH]\n" + "\n".join(lines)
        if len(batch_str) > 4000: 
            batch_str = batch_str[:3900] + "\n[TRUNCATED...]"
        
        self.last_console_flush = time.time()

        # V5.6 Priority: If ERROR is in batch, send immediately (already here, but reinforcement)
        app = self.application
        if app:
            try:
                await app.bot.send_message(chat_id, f"<code>{batch_str}</code>", parse_mode="HTML")
            except TelegramError as e:
                # 429 Retry logic remains same
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
        """Adaptive flush (V5.7): 10 lines or 1.5s."""
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
            logger.info("📟 Console Stream: ON (Batched)")
        else:
            hdlr_to_rem = self._log_handler
            if hdlr_to_rem:
                logging.getLogger().removeHandler(hdlr_to_rem)
                self._log_handler = None
            task = self._console_task
            if task:
                task.cancel()
                self._console_task = None
            logger.info("📟 Console Stream: OFF")

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
        await self.add_log_line("📦 KERNEL SIGHT: Hard Update sequence start...")
        try:
            # V5.7 Hard Reset
            await run_bash('su -c "chmod -R 777 ."')
            await run_bash("git fetch --all")
            await run_bash("git reset --hard origin/main")
            
            # V5.7 Purge Cache & Reinstall
            await run_bash('find . -type d -name "__pycache__" -exec rm -rf {} +')
            await run_bash("pip install -r requirements.txt")
            
            if chat_id and self.application:
                await self.application.bot.send_message(
                    chat_id, "✅ <b>Hard Update Complete</b>. Purged __pycache__. Rebooting...", parse_mode="HTML")
            
            await asyncio.sleep(2)
            os.execv(sys.executable, ['python'] + sys.argv)
        except Exception as e:
            logger.error(f"V5.7 Update Error: {e}")
            if chat_id and self.application:
                await self.application.bot.send_message(chat_id, f"❌ Update failed: {e}")

    # ── MAINTENANCE CYCLE ────────────────────────────────────────────────
    async def run_maintenance_cycle(self, chat_id: Optional[int] = None):
        if self._maint_in_progress: return
        self._maint_in_progress = True
        
        target_chat = chat_id or (self.config.admin_ids[0] if self.config.admin_ids else None)

        async def notify(text):
            if target_chat and self.application:
                try:
                    await self.application.bot.send_message(target_chat, text, parse_mode="HTML")
                except Exception: pass

        await notify("🔄 <b>Starting scheduled maintenance...</b>\n<code>[THE PURGE]</code>")
        logger.warning("Maintenance: Starting purge cycle.")

        # 1. Kill all clones
        await run_bash('su -c "am force-stop com.roblox.client*"')
        
        # 2. Clear junk
        await run_bash('su -c "rm -rf /data/data/com.roblox.client*/cache/*"')
        await run_bash('su -c "rm -rf /data/data/com.roblox.client*/code_cache/*"')
        
        await notify("🧹 Cache purged. Waiting 5s...")
        await asyncio.sleep(5)

        # 3. Restart active clones (b, c, e, f, g, i)
        active_sequence = ["clienb", "clienc", "cliene", "clienf", "clieng", "clieni"]
        seq_str = ", ".join(active_sequence)
        await notify(f"🚀 Restarting sequence: <code>{html.escape(seq_str)}</code>")

        for idx, name in enumerate(active_sequence):
            # We don't use _enqueue_start directly because we want 15s stagger
            # But we SHOULD use the lock if it's available? 
            # The requirement says "15s stagger", let's use the sequence logic.
            asyncio.create_task(self._enqueue_start(name, target_chat))
            if idx < len(active_sequence) - 1:
                await asyncio.sleep(15)

        await notify("✅ <b>Maintenance Cycle Complete.</b>")
        self._maint_in_progress = False

    async def _maint_timer_loop(self):
        """Checks every minute if maintenance is due or RAM < 10%."""
        last_run = time.time()
        from memory_manager import MemoryManager
        while True:
            await asyncio.sleep(60)
            
            free_ram = MemoryManager.get_free_ram_percentage()
            if free_ram < 10.0 and not self._maint_in_progress:
                logger.warning(f"CRITICAL RAM ({free_ram}%). Triggering Smart RAM Cleanup.")
                MemoryManager.smart_ram_cleanup()
                await self.run_maintenance_cycle()
                last_run = time.time()
                continue
                
            if self.maintenance_enabled and not self._maint_in_progress:
                elapsed = (time.time() - last_run) / 60
                if elapsed >= self.maintenance_minutes:
                    MemoryManager.smart_ram_cleanup()
                    await self.run_maintenance_cycle()
                    last_run = time.time()

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)
        admin_id = self.config.admin_ids[0] if self.config.admin_ids else None
        if admin_id:
            try:
                err_esc = html.escape(str(context.error))
                await context.bot.send_message(
                    admin_id, f"🚨 <b>Global Error</b>\n<code>{err_esc}</code>", parse_mode="HTML")
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Entry-point
    # ─────────────────────────────────────────────────────────────────────
    async def run(self):
        # 1. CLEAN SLATE: NO PKILL. Bot assumes unique execution.
        logger.info(f"💎 PROJECT AEGIS V{VERSION} STARTING — {DEVICE_ID} (Clean Slate)")


        # 2. Build application
        self.application = ApplicationBuilder().token(self.config.bot_token).build()
        app = self.application

        # 3. Handlers
        app.add_handler(CommandHandler("start",   self.cmd_start))
        app.add_handler(CommandHandler("console", self.cmd_console))
        app.add_handler(CommandHandler("exec",    self.cmd_exec))
        app.add_handler(CommandHandler("update",  self.cmd_update))
        app.add_handler(CommandHandler("mass_start",  self.cmd_mass_start))
        app.add_handler(CommandHandler("mass_stop",   self.cmd_mass_stop))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        app.add_handler(CallbackQueryHandler(self.handle_callback))
        app.add_error_handler(self.error_handler)

        # 4. Start
        await app.initialize()
        await app.start()

        # 5. Launch Watchdog (state-gated, uses application explicitly)
        asyncio.create_task(watchdog_loop(app, self))

        # 5b. Launch Maintenance Timer
        asyncio.create_task(self._maint_timer_loop())

        # 6. Auto-resume (V5.7 Smart sequence)
        asyncio.create_task(self._auto_resume())

        logger.info(f"💎 PROJECT AEGIS V{VERSION} ONLINE — {DEVICE_ID}")

        # 7. Poll
        await app.updater.start_polling(drop_pending_updates=True)

        # 8. Block
        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await app.stop()
            await app.shutdown()


# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    try:
        asyncio.run(AegisBot().run())
    except Exception as e:
        logger.critical(f"Fatal startup error: {e}")
