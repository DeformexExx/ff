import html
from typing import Any
from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup


def _clone_letter(name: str) -> str:
    n = (name or "?").lower()
    if n.startswith("clien") and len(n) >= 1:
        return n[-1].upper()
    return name.replace("_", "-").upper()[:4]


def _hub_emoji(state: str, healthy: bool) -> str:
    s = str(state).upper()
    if s == "STARTING":
        return "⏳"
    if s == "RUNNING" and healthy:
        return "🟢"
    if s == "RUNNING":
        return "🟡"
    return "🔴"


class UIManager:

    @staticmethod
    def get_welcome_text(device_id: str, version: str) -> str:
        d_esc = html.escape(device_id.replace("_", "-"))
        v_esc = html.escape(version)
        return f"<b>Aegis</b> <code>{d_esc}</code> · v{v_esc}"

    @staticmethod
    def get_main_keyboard() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton("Клоны"), KeyboardButton("Устройство")],
                [KeyboardButton("Система")],
            ],
            resize_keyboard=True,
        )

    @staticmethod
    def format_dashboard(device_id: str, ram: str, cpu: str, temp: str, version: str) -> str:
        d_esc = html.escape(device_id.replace("_", "-"))
        return (
            f"<b>Устройство</b> <code>{d_esc}</code>\n"
            f"RAM {html.escape(ram)} · CPU {html.escape(cpu)} · {html.escape(temp)}"
        )

    @staticmethod
    def get_device_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📷 Снимок", callback_data="sys_screenshot")],
                [InlineKeyboardButton("⬅️ В меню", callback_data="nav_home")],
            ]
        )

    @staticmethod
    def get_system_keyboard(console_on: bool, restore_on: bool) -> InlineKeyboardMarkup:
        c = "вкл" if console_on else "выкл"
        r = "вкл" if restore_on else "выкл"
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"Консоль: {c}", callback_data="toggle_console")],
                [InlineKeyboardButton(f"Автовосстановление: {r}", callback_data="toggle_restore")],
                [InlineKeyboardButton("⬅️ В меню", callback_data="nav_home")],
            ]
        )

    @staticmethod
    def format_clones_hub(clones_data: list, state_map: dict, _version: str) -> str:
        if not clones_data:
            return "<i>Нет клонов в конфиге.</i>"

        lines = []
        for clone in clones_data:
            raw_name = clone.get("name", "?")
            name_disp = raw_name.replace("_", "-")
            nick = (clone.get("nickname") or clone.get("name") or "?").replace("_", "-")
            nick_esc = html.escape(nick)
            letter = _clone_letter(raw_name)
            thr_raw = state_map.get(f"{name_disp}:threads", "0")
            cpu_raw = state_map.get(f"{name_disp}:cpu", "—")
            healthy = state_map.get(f"{name_disp}:healthy", "0") == "1"
            state = str(state_map.get(name_disp, "STOPPED")).upper()
            try:
                thr_val = int(thr_raw)
            except ValueError:
                thr_val = 0
            emoji = _hub_emoji(state, healthy)
            lines.append(
                f"[{letter}] {emoji} {nick_esc} | {thr_val} th | {cpu_raw}% CPU"
            )

        return "\n".join(lines)

    @staticmethod
    def get_clones_hub_keyboard(clones_data: list, state_map: dict) -> InlineKeyboardMarkup:
        rows = []
        chunk: list = []
        for clone in clones_data:
            raw_name = clone.get("name")
            if not raw_name:
                continue
            name_disp = raw_name.replace("_", "-")
            nick = (clone.get("nickname") or raw_name).replace("_", "-")
            # Keep nick short to fit button (max ~10 chars)
            nick_short = nick[:10] if len(nick) > 10 else nick
            healthy = state_map.get(f"{name_disp}:healthy", "0") == "1"
            state = str(state_map.get(name_disp, "STOPPED")).upper()
            letter = _clone_letter(raw_name)
            emoji = _hub_emoji(state, healthy)
            thr_raw = state_map.get(f"{name_disp}:threads", "0")
            cpu_raw = state_map.get(f"{name_disp}:cpu", "—")
            try:
                thr_val = int(thr_raw)
            except (ValueError, TypeError):
                thr_val = 0
            # Format: [B] 🟢 Nick | 145th | 20% CPU
            label = f"[{letter}] {emoji} {nick_short} | {thr_val}th | {cpu_raw}%"
            chunk.append(
                InlineKeyboardButton(label, callback_data=f"clone_{raw_name}")
            )
            if len(chunk) >= 4:
                rows.append(chunk)
                chunk = []
        if chunk:
            rows.append(chunk)
        rows.append(
            [
                InlineKeyboardButton("▶️ Все", callback_data="mass_start"),
                InlineKeyboardButton("🛑 Все", callback_data="mass_stop"),
            ]
        )
        rows.append([InlineKeyboardButton("⬅️ В меню", callback_data="nav_home")])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def get_clone_submenu(name: str, _state: Any, _threads: int = 0) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("▶️ СТАРТ", callback_data=f"start_single_{name}"),
                    InlineKeyboardButton("🛑 СТОП", callback_data=f"stop_single_{name}"),
                ],
                [
                    InlineKeyboardButton("🧹 ОЧИСТКА", callback_data=f"purge_cache_{name}"),
                    InlineKeyboardButton("⬅️ НАЗАД", callback_data="hub_clones"),
                ],
            ]
        )

    @staticmethod
    def get_help_text() -> str:
        return "Команды: /start, /console, /exec, /update, /mass_start, /mass_stop"
