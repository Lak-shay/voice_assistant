"""LiveKit Voice Assistant Main Entrypoint

Runs startup API readiness and health checks across dev and prod environments
before starting the real-time WebRTC worker process.
"""

import asyncio
import sys
from livekit.agents import JobContext, WorkerOptions, cli

from config import settings
from logger import logger
from startup import run_startup_checks


async def entrypoint(ctx: JobContext) -> None:
    """Main voice session entrypoint for incoming WebRTC calls."""
    trace_id = ctx.room.name
    logger.info("[%s] Inbound call received. Connecting to room...", trace_id)
    await ctx.connect()
    logger.info("[%s] Connected to room successfully. Waiting for participants...", trace_id)


if __name__ == "__main__":
    is_ready = asyncio.run(run_startup_checks())
    if not is_ready:
        logger.error("Startup readiness verification failed; aborting worker.")
        sys.exit(1)

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            ws_url=settings.LIVEKIT_URL,
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
        )
    )
