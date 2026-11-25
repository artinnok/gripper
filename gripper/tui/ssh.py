import asyncio
import signal
import subprocess


async def run_ssh(argument_list: list[str]) -> int:
    """Give OpenSSH the terminal and its interrupts while gripper continues checking deadlines."""

    with subprocess.Popen(argument_list) as process:
        previous_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            return await asyncio.to_thread(process.wait)
        finally:
            signal.signal(signal.SIGINT, previous_handler)
            if process.poll() is None:
                process.kill()
