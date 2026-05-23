"""review_server.py — 审片工具 HTTP 服务器 (IPv4)"""
import http.server
import socket
import os, sys
from pathlib import Path
from urllib.parse import unquote

SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))
from config import get_project_config

_project = get_project_config()
CLIP_DIR = os.path.join(_project["work_dir"], "_review_clips")
PORT = 9999

class ReviewHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SKILL_DIR / "static"), **kwargs)

    def do_GET(self):
        if self.path.startswith('/clips/'):
            filename = unquote(self.path[len('/clips/'):])
            filepath = os.path.join(CLIP_DIR, filename)
            if os.path.exists(filepath):
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Content-Length', str(os.path.getsize(filepath)))
                self.end_headers()
                with open(filepath, 'rb') as f:
                    self.wfile.write(f.read())
                return
            self.send_error(404, f'Not found: {filename}')
            return
        return super().do_GET()

if __name__ == '__main__':
    server = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), ReviewHandler)
    print(f'审片服务器: http://127.0.0.1:{PORT}')
    print(f'视频目录: {CLIP_DIR}')
    print(f'按 Ctrl+C 停止')
    server.serve_forever()
