import asyncio
import logging
import time
import json
import os
from bash_utils import run_bash

logger = logging.getLogger("InjectionEngine")

class InjectionEngine:
    @staticmethod
    async def inject_and_launch(clone_name: str, cookie: str, place_id: str = None, link_code: str = None, status_msg=None) -> bool:
        async def update_status(text: str):
            logger.info(text)
            if status_msg:
                try:
                    await status_msg.edit_text(text)
                except Exception:
                    pass

        try:
            server_link = None
            if os.path.exists("server.json"):
                with open("server.json", "r") as f:
                    data = json.load(f)
                    if isinstance(data, list) and len(data) > 0:
                        server_link = data[0]
            
            if not server_link:
                await update_status(f"❌ Ссылка на сервер не найдена в server.json")
                return False

            await update_status(f"⏳ ({clone_name}) Инъекция Cookie...")
            sqlite_bin = "/data/data/com.termux/files/usr/bin/sqlite3"
            db_path = f"/data/data/com.roblox.{clone_name}/app_webview/Default/Cookies"
            current_time = int(time.time() * 1000000)
            
            cookie_sql = cookie.replace("'", "''")
            sql_del = "DELETE FROM cookies;"
            sql_ins = (
                f"INSERT INTO cookies (creation_utc, host_key, top_frame_site_key, name, value, path, "
                f"expires_utc, is_secure, is_httponly, last_access_utc, has_expires, is_persistent, samesite, source_port) "
                f"VALUES ({current_time}, '.roblox.com', '', '.ROBLOSECURITY', '{cookie_sql}', '/', 253402300799000000, 1, 1, "
                f"{current_time}, 1, 1, -1, -1);"
            )
            
            await run_bash(f"su -c 'rm -f {db_path}-journal {db_path}-wal'")
            
            inj_cmd = f"su -c \"{sqlite_bin} {db_path} \\\"{sql_del} {sql_ins}\\\"\""
            ret, _, stderr = await run_bash(inj_cmd)
            if ret != 0:
                await update_status(f"❌ SQLite Ошибка: {stderr}")
                return False

            await update_status(f"⏳ ({clone_name}) Восстановление прав...")
            chown_cmd = f"su -c \"chown \\$(stat -c %u:%g /data/data/com.roblox.{clone_name}) {db_path}\""
            await run_bash(chown_cmd)

            await update_status(f"🚀 ({clone_name}) Запуск на сервер...")
            await run_bash(f"su -c 'am force-stop com.roblox.{clone_name}'")
            
            launch_cmd = f"su -c \"am start -a android.intent.action.VIEW -d '{server_link}' com.roblox.{clone_name}\""
            ret, _, stderr = await run_bash(launch_cmd)
            
            if ret == 0:
                await update_status(f"✅ Запущено ({clone_name})")
                return True
            else:
                await update_status(f"❌ Ошибка запуска: {stderr}")
                return False

        except Exception as e:
            await update_status(f"❌ Критическая ошибка: {str(e)}")
            return False

    @staticmethod
    async def stop(clone_name: str) -> bool:
        ret, _, _ = await run_bash(f"su -c 'am force-stop com.roblox.{clone_name}'")
        return ret == 0