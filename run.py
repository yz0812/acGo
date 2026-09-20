"""Production entry point: one process, bounded Waitress request threads."""
import os
import signal

from waitress import create_server
from src.app import app
from src import runtime, settings


def main():
    server = None

    def shutdown(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)
    runtime.start()
    try:
        server = create_server(app, host=os.getenv('HOST', '0.0.0.0'), port=int(os.getenv('PORT', '5000')),
                               threads=settings.WEB_THREADS, connection_limit=64, backlog=64,
                               channel_timeout=30, max_request_body_size=2 * 1024 * 1024,
                               inbuf_overflow=64 * 1024, outbuf_overflow=128 * 1024)
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.close()
            server.task_dispatcher.shutdown(timeout=35)
        runtime.stop()


if __name__ == '__main__':
    main()
