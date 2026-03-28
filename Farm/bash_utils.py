# -*- coding: utf-8 -*-
import asyncio
import logging

logger = logging.getLogger("BashUtils")

async def run_bash(cmd: str, timeout: int = 30) -> tuple[int, str, str]:
    """Выполняет bash команду и возвращает (код_возврата, stdout, stderr).
    V12.4: Added timeout (default 30s) to prevent hanging under OOM."""
    try:
        process = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.error(f"Bash TIMEOUT ({timeout}s): {cmd}")
            try:
                process.kill()
            except Exception:
                pass
            return -1, "", f"TIMEOUT after {timeout}s"
        return process.returncode, stdout.decode().strip(), stderr.decode().strip()
    except Exception as e:
        logger.error(f"Bash Execution Error: {e} | CMD: {cmd}")
        return -1, "", str(e)
