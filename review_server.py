"""review_server.py — 审片工具 HTTP 服务器"""
import http.server
import os, sys
from pathlib import Path
from urllib.parse import unquote

SKILL_DIR = Path(__file__).parent

# 视频目录来自 config
sys.path.insert(0, str(SKILL_DIR))
from config import get_project_config
_project = get_project_config()
CLIP_DIR = os.path.join(_project["work_dir"], "_review_clips")

PORT = 8888

class ReviewHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SKILL_DIR / "static"), **kwargs)

    def do_GET(self):
        # /clips/* → 映射到 _review_clips 目录
        if self.path.startswith('/clips/'):
            filename = unquote(self.path[len('/clips/'):])
            filepath = os.path.join(CLIP_DIR, filename)
            if os.path.exists(filepath):
                self.send_response(200)
                ext = os.path.splitext(filename)[1]
                ct = {'mp4':'video/mp4', 'jpg':'image/jpeg', 'png':'image/png'}.get(ext, 'application/octet-stream')
                self.send_header('Content-Type', ct)
                self.send_header('Content-Length', str(os.path.getsize(filepath)))
                self.end_headers()
                with open(filepath, 'rb') as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_error(404, f'Clip not found: {filename}')
                return
        return super().do_GET()

if __name__ == '__main__':
    print(f'审片服务器启动: http://localhost:{PORT}')
    print(f'视频目录: {CLIP_DIR}')
    print(f'按 Ctrl+C 停止')
    http.server.test(HandlerClass=ReviewHandler, port=PORT)
