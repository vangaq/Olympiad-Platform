"""Run the platform with: python main.py."""
from check_python import require_supported_python

require_supported_python()

import os
from olympiad import create_app

app = create_app()

if __name__ == "__main__":
    from waitress import serve
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    print(f"Olympiad Platform: http://{host}:{port}", flush=True)
    serve(app, host=host, port=port, threads=8, max_request_body_size=65536)
