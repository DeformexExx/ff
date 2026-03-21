# -*- coding: utf-8 -*-
# Aegis bot — V8.5
import os
import sys
import enum
import asyncio
import logging
import time
import tempfile
import re
import html
from typing import Optional, Dict, Tuple

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

VERSION = "8.5"

if len(sys.argv) < 2:
    print("❌  Usage: python main.py <DEVICE_ID>")
    sys.exit(1)

DEVICE_ID = sys.argv[1]
FARM_DIR  = _bot_dir
BOOT_LOG  = os.path.join(FARM_DIR, "boot_log.txt")

logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [{DEVICE_ID}/V{VERSION}] [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BOOT_LOG, encoding="utf-8"),
    ]
)
logger = logging.getLogger("AegisV40")
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


async def watchdog_safe_restart(
    application: Application,
    bot_instance: "AegisBot",
    name: str,
    reason: str,
) -> None:
    """Изолированный рестарт одного клона: force-stop → drop_caches → _enqueue_start."""
    suffix = name[-1].lower() if name.lower().startswith("clien") else name.lower()
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

    await run_bash(f"su -c 'am force-stop com.roblox.clien{suffix}'")
    await run_bash('su -c "echo 3 > /proc/sys/vm/drop_caches"')
    await asyncio.sleep(2)
    bot_instance.set_state(name, CloneState.STOPPED)
    asyncio.create_task(bot_instance._enqueue_start(name, admin))


async def watchdog_loop(application: Application, bot_instance: "AegisBot"):
    """
    Тройная проверка (RUNNING):
    1) Liveness — pgrep не находит процесс
    2) Threads — th < 130 (после grace 120 с)
    3) CPU — < 2% непрерывно 3 минуты при th >= 130
    """
    GRACE_SEC = 120
    MIN_THREADS = 130
    CPU_FREEZE_PCT = 2.0
    CPU_FREEZE_SEC = 180

    while True:
        await asyncio.sleep(30)
        try:
            now = time.time()
            for name, state in list(bot_instance.clone_states.items()):
                if ":" in name:
                    continue
                if state != CloneState.RUNNING:
                    continue

                suffix = name[-1].lower() if name.lower().startswith("clien") else name.lower()
                uptime = now - bot_instance.running_since.get(name, now)

                alive = await MonitorEngine.clone_pgrep_alive(suffix)
                if not alive:
                    logger.warning(f"Watchdog [{name}]: liveness fail")
                    await watchdog_safe_restart(
                        application, bot_instance, name, "нет процесса (pgrep)"
                    )
                    continue

                st = await MonitorEngine.get_clone_status(name)
                m_thr = re.search(r"Thr:\s*(\d+)", st)
                thr = int(m_thr.group(1)) if m_thr else 0
                bot_instance.clone_states[f"{name}:threads"] = str(thr)

                if uptime >= GRACE_SEC and thr < MIN_THREADS:
                    logger.warning(f"Watchdog [{name}]: threads {thr} < {MIN_THREADS}")
                    await watchdog_safe_restart(
                        application, bot_instance, name, f"потоки {thr} < {MIN_THREADS}"
                    )
                    continue

                if uptime >= GRACE_SEC and thr >= MIN_THREADS:
                    cpu = await MonitorEngine.get_clone_cpu_percent(suffix)
                    if cpu < 0:
                        bot_instance.watchdog_cpu_low_since.pop(name, None)
                    elif cpu < CPU_FREEZE_PCT:
                        t0 = bot_instance.watchdog_cpu_low_since.get(name)
                        if t0 is None:
                            bot_instance.watchdog_cpu_low_since[name] = now
                        elif now - t0 >= CPU_FREEZE_SEC:
                            logger.warning(
                                f"Watchdog [{name}]: CPU {cpu}% < {CPU_FREEZE_PCT}% {CPU_FREEZE_SEC}s"
                            )
                            await watchdog_safe_restart(
                                application,
                                bot_instance,
                                name,
                                f"CPU < {CPU_FREEZE_PCT}% {CPU_FREEZE_SEC}s",
                            )
                            continue
                    else:
                        bot_instance.watchdog_cpu_low_since.pop(name, None)

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
        self.watchdog_cpu_low_since: Dict[str, Optional[float]] = {}

        for c in self.config.clones_data:
            n = c.get("name")
            if n:
                self.clone_states[n] = CloneState.STOPPED

    def reset_all_states(self):
        """Старт: все клоны в простое (IDLE), без автозапуска."""
        self.is_mass_starting = False
        self.watchdog_cpu_low_since.clear()
        for c in self.config.clones_data:
            n = c.get("name")
            if n:
                self.set_state(n, CloneState.STOPPED)

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
        elif state == CloneState.STARTING:
            self.config.update_clone_status(name, "STARTING")
        elif state == CloneState.STOPPED:
            self.config.update_clone_status(name, "IDLE")

    async def _is_admin(self, uid: int) -> bool:
        return uid in self.config.admin_ids

    async def _apply_immortality(self) -> None:
        """OOM shield, max priority, wake lock (Termux)."""
        pid = os.getpid()
        r, _, err = await run_bash(f"su -c 'echo -1000 > /proc/{pid}/oom_score_adj'")
        if r != 0:
            logger.warning(f"oom_score_adj: {err}")
        try:
            os.nice(-20)
        except Exception as e:
            logger.warning(f"nice(-20): {e}")
        await run_bash("termux-wake-lock 2>/dev/null || true")
        tw = "/data/data/com.termux/files/usr/bin/termux-wake-lock"
        if os.path.isfile(tw):
            await run_bash(f"\"{tw}\" 2>/dev/null || true")

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
        for c in self.config.clones_data:
            asyncio.create_task(self._stop_clone(c.get("name"), chat))
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
            reply_markup=UIManager.get_system_keyboard(self._console_on, self.persistence.auto_restore),
            parse_mode="HTML"
        )

    async def _hub_view(self) -> Tuple[str, Dict]:
        async def collect_one(clone_name: str) -> Tuple[str, str, str, str, str]:
            name_disp = clone_name.replace("_", "-")
            sfx = clone_name[-1].lower() if clone_name.lower().startswith("clien") else clone_name.lower()
            alive, st, cpu = await asyncio.gather(
                MonitorEngine.clone_pgrep_alive(sfx),
                MonitorEngine.get_clone_status(clone_name),
                MonitorEngine.get_clone_cpu_percent(sfx),
            )
            m_thr = re.search(r"Thr:\s*(\d+)", st)
            thr = int(m_thr.group(1)) if m_thr else 0
            cpu_str = f"{cpu:.0f}" if cpu >= 0 else "—"
            self.clone_states[f"{clone_name}:threads"] = str(thr)
            self.clone_states[f"{clone_name}:cpu"] = cpu_str
            cstate = self.clone_states.get(clone_name, CloneState.STOPPED)
            healthy = (
                cstate == CloneState.RUNNING
                and alive
                and thr >= 130
                and cpu >= 2.0
            )
            return (
                name_disp,
                str(cstate),
                str(thr),
                cpu_str,
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
            MonitorEngine.clone_pgrep_alive(pid_sfx),
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
                asyncio.create_task(self._enqueue_start(name, chat))

            elif d.startswith("stop_single_"):
                name = d[12:]
                asyncio.create_task(self._stop_clone(name, chat))

            elif d.startswith("purge_cache_"):
                name = d[12:]
                await self._purge_clone_cache(name, chat)

            elif d.startswith("start_"):
                name = d[6:]
                asyncio.create_task(self._enqueue_start(name, chat))

            elif d.startswith("stop_"):
                asyncio.create_task(self._stop_clone(d[5:], chat))

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
        ci = self.config.get_clone(name)
        if not ci: return

        current = self.clone_states.get(name, CloneState.STOPPED)
        if current == CloneState.STARTING:
            logger.info(f"_enqueue_start: [{name}] already STARTING. Skip.")
            return

        async with self._start_lock:
            self.set_state(name, CloneState.STARTING)
            status_str = await MonitorEngine.get_clone_status(name)
            if "Offline" not in status_str:
                logger.info(f"{name}: процесс уже есть, пропуск инъекции.")
                self.set_state(name, CloneState.RUNNING)
                app = self.application
                if chat_id and app:
                    try:
                        n_esc = html.escape(name)
                        await app.bot.send_message(
                            chat_id, f"👁 <code>{n_esc}</code> уже запущен.", parse_mode="HTML")
                    except Exception: pass
                return

            sm = None
            app = self.application
            if chat_id and app:
                try:
                    name_esc = html.escape(name.replace('_', '-'))
                    sm = await app.bot.send_message(
                        chat_id, f"▶️ <code>{name_esc}</code>", parse_mode="HTML")
                except Exception:
                    pass

            suffix = name[-1].lower() if name.startswith("clien") else name.lower()
            await run_bash(f"su -c 'rm -rf /data/data/com.roblox.clien{suffix}/cache/*'")

            import json, os
            p_id, l_code = None, None
            try:
                cfg_path = os.path.join(FARM_DIR, f"{DEVICE_ID}.json")
                if os.path.exists(cfg_path):
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cdata = json.load(f)
                        p_id = cdata.get("placeID")
                        l_code = cdata.get("linkCode") or cdata.get("privateServerLink")
            except Exception as e:
                logger.error(f"Failed parsing server config: {e}")

            ok = await InjectionEngine.inject_and_launch(
                name, ci.get("cookie"), p_id, l_code, sm)

            if ok:
                self.set_state(name, CloneState.RUNNING)
                self.persistence.add_target(name, "RUNNING")
            else:
                self.set_state(name, CloneState.STOPPED)

        await asyncio.sleep(10)

        await self.refresh_dashboard(force=True)

    async def _mass_start(self, chat_id):
        """Sequential mass start via the _start_lock queue."""
        self.is_mass_starting = True
        clones = [c for c in self.config.clones_data if c.get("active", True)]
        
        app = self.application
        if not (chat_id and app): return

        try:
            m = await app.bot.send_message(chat_id, "▶️ Массовый запуск…", parse_mode="HTML")
            await m.edit_text("▶️ Очистка кэша…", parse_mode="HTML")
            for c in clones:
                name = c.get("name")
                if name:
                    suffix = name[-1].lower() if name.startswith("clien") else name.lower()
                    await run_bash(f"su -c 'rm -rf /data/data/com.roblox.clien{suffix}/cache/*'")
            await m.edit_text("▶️ Запуск по очереди…", parse_mode="HTML")
            for idx, c in enumerate(clones, 1):
                if not self.is_mass_starting:
                    break
                name = c.get("name")
                if not name:
                    continue
                await app.bot.send_message(
                    chat_id,
                    f"[{idx}/{len(clones)}] <code>{html.escape(name.replace('_', '-'))}</code>",
                    parse_mode="HTML",
                )
                await self._enqueue_start(name, chat_id)
        except Exception as e:
            logger.error(f"Mass Start Error: {e}")
        finally:
            self.is_mass_starting = False

    async def _mass_stop(self, chat_id):
        """Sequential mass stop with instant interrupt."""
        self.is_mass_starting = False
        app = self.application
        if not (chat_id and app): return

        try:
            m = await app.bot.send_message(chat_id, "🛑 Остановка…", parse_mode="HTML")
            
            await run_bash('su -c "pkill -9 com.roblox"')
            
            for c in self.config.clones_data:
                name = c.get("name")
                if not name: continue
                suffix = name[-1].lower() if name.startswith("clien") else name.lower()
                await run_bash(f"su -c 'am force-stop com.roblox.clien{suffix}'")
                self.set_state(name, CloneState.STOPPED)
                self.persistence.remove_target(name)

            await m.edit_text("🛑 Готово.", parse_mode="HTML")
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
                    chat_id, f"🛑 <code>{html.escape(name.replace('_', '-'))}</code>", parse_mode="HTML")
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

        await self._apply_immortality()

        asyncio.create_task(watchdog_loop(app, self))

        self.reset_all_states()

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


if __name__ == "__main__":
    try:
        asyncio.run(AegisBot().run())
    except Exception as e:
        logger.critical(f"Fatal startup error: {e}")
