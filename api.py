"""Local read-only API and website. Keys remain in the monitoring process."""
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from dashboard import snapshot


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            body = Path(__file__).with_name("website").joinpath("index.html").read_bytes()
            content_type = "text/html; charset=utf-8"
        elif self.path == "/api/dashboard":
            token = os.getenv("API_TOKEN", "")
            if token and not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.send_error(401)
                return
            body = json.dumps(snapshot(),ensure_ascii=False).encode()
            content_type = "application/json; charset=utf-8"
        elif self.path == "/health":
            body = b'{"status":"ok"}'
            content_type = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type",content_type)
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Cache-Control","no-store")
        origin = os.getenv("DASHBOARD_ORIGIN")
        if origin and self.headers.get("Origin") == origin:
            self.send_header("Access-Control-Allow-Origin",origin)
            self.send_header("Vary","Origin")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    address = (os.getenv("API_HOST","127.0.0.1"),int(os.getenv("PORT","8080")))
    print(f"Dashboard/API: http://{address[0]}:{address[1]}",flush=True)
    ThreadingHTTPServer(address,Handler).serve_forever()
