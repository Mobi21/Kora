"""Run the Kora CLI chat client: ``python -m kora_v2.cli``.

Auto-discovers the daemon port from ``data/kora.lock`` and the auth token
from ``data/.api_token``. Requires a running daemon (``kora``).
"""

import asyncio

from kora_v2.cli.app import KoraCLI

if __name__ == "__main__":
    asyncio.run(KoraCLI().run())
