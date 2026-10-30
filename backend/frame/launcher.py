from __future__ import annotations

import threading
import webbrowser

import uvicorn


def main() -> None:
    url = "http://127.0.0.1:8000"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run("frame.main:app", host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__": main()
