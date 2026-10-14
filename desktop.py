"""Windows portable launcher: embedded server, browser UI, and system tray."""
import argparse
import ctypes
import json
import logging
import logging.handlers
import os
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from resume_tool.paths import default_data_root


def main():
    parser = argparse.ArgumentParser(description="Resume Workbench")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-tray", action="store_true")
    args = parser.parse_args()
    data_root = Path(args.data_dir).resolve() if args.data_dir else default_data_root()
    try:
        data_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        if args.data_dir:
            raise
        data_root = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "ResumeWorkbench"
        data_root.mkdir(parents=True, exist_ok=True)
    # Windowed executables have no console streams. Third-party loggers need valid streams.
    stream = open(data_root / "运行日志.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    logging.basicConfig(level=logging.WARNING, handlers=[logging.handlers.RotatingFileHandler(
        data_root / "应用日志.log", maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")])
    url = f"http://127.0.0.1:{args.port}"
    try:
        with urllib.request.urlopen(url + "/health", timeout=1) as response:
            health = json.load(response)
        if health.get("application") == "resume-workbench":
            if not args.no_browser:
                webbrowser.open(url)
            return
    except (OSError, ValueError):
        pass

    from app import create_app
    from waitress import create_server
    application = create_app(data_root)
    server = create_server(application, host="127.0.0.1", port=args.port, threads=8,
                           max_request_body_size=140 * 1024 * 1024, channel_timeout=180)
    if args.no_tray:
        if not args.no_browser:
            threading.Timer(1, lambda: webbrowser.open(url)).start()
        server.run()
        return

    import pystray
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((1, 1, 63, 63), radius=13, fill="#18584a")
    draw.rounded_rectangle((19, 12, 45, 51), radius=3, outline="white", width=3)
    for y in (24, 32, 40):
        draw.line((25, y, 39, y), fill="white", width=3)

    def open_page(*_):
        webbrowser.open(url)

    def open_data(*_):
        os.startfile(str(data_root))

    def open_notices(*_):
        from resume_tool.paths import RESOURCE_ROOT
        os.startfile(str(RESOURCE_ROOT / "licenses"))

    def exit_app(icon, _):
        manager = application.extensions["resume_manager"]
        if manager.active:
            answer = ctypes.windll.user32.MessageBoxW(None,
                "还有简历正在处理。退出会终止未完成的任务，已完成结果会保留。确定退出？", "简历工作台", 0x24)
            if answer != 6:
                return
            manager.cancel.set()
        server.close()
        icon.stop()

    icon = pystray.Icon("resume_workbench", image, "简历工作台 · 每批最多 50 份", menu=pystray.Menu(
        pystray.MenuItem("打开简历工作台", open_page, default=True),
        pystray.MenuItem("打开数据文件夹", open_data), pystray.Menu.SEPARATOR,
        pystray.MenuItem("开源组件许可", open_notices),
        pystray.MenuItem("退出", exit_app)))
    threading.Thread(target=server.run, daemon=True).start()

    def setup(tray):
        tray.visible = True
        if not args.no_browser:
            open_page()

    icon.run(setup=setup)
    stream.flush()
    # Outstanding API workers may be waiting for HTTP timeouts. DB commits are atomic;
    # unfinished jobs will be recovered on the next launch.
    os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        logging.exception("Desktop launcher failed")
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, "启动失败：" + str(error) + "\n请检查程序所在目录是否可写、端口是否被占用。", "简历工作台", 0x10)
        raise
