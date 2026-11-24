"""Packaged entry point. Employees never need a terminal or a Python install."""
import os
import sys
import webbrowser
from pathlib import Path


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--check-installation':
        import installation_check
        installation_check.run(Path(sys.argv[2]))
        return
    if len(sys.argv) == 3 and sys.argv[1] == '--collect':
        import tiktok_collect
        tiktok_collect.main(sys.argv[2])
        return
    import runtime_paths
    import platform_support
    import desktop_runtime
    data = runtime_paths.data_directory(Path(__file__).parent)
    os.environ['TKSHANGJIA_DATA_DIR'] = str(data)
    lock = open(data / 'config' / 'desktop.lock', 'a+')
    log = open(data / 'logs' / 'desktop.log', 'a', encoding='utf-8', buffering=1)
    original_stdout, original_stderr = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = log
    try:
        try:
            platform_support.lock_file(lock)
        except OSError:
            port = runtime_paths.read_runtime(data).get('port')
            if port != desktop_runtime.UI_PORT:
                raise desktop_runtime.FixedPortError(
                    '已有软件正在启动，或旧版软件仍在使用其他端口。请稍后重试；'
                    '如仍无法打开，请退出旧版后重新启动，使地址统一为 ' + desktop_runtime.UI_URL
                ) from None
            if '--no-browser' not in sys.argv: webbrowser.open(desktop_runtime.UI_URL)
            return
        import server
        server.run(open_browser='--no-browser' not in sys.argv)
    except Exception as error:
        # Error details are intentionally omitted: exceptions can contain URLs.
        message = str(error) if isinstance(error, desktop_runtime.FixedPortError) else '启动失败。请检查端口、磁盘权限和程序文件是否完整。'
        log.write(message + '\n')
        if sys.platform == 'win32' and '--no-browser' not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message + '\n日志目录：' + str(data / 'logs'), 'TK 上架助手', 16)
        raise SystemExit(1) from None
    finally:
        lock.close()
        sys.stdout, sys.stderr = original_stdout, original_stderr
        log.close()


if __name__ == '__main__': main()
