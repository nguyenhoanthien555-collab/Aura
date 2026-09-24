"""
AURA Web Console Launcher.
Serves D:\AURA\aura_web_ui.html on http://localhost:5050 and opens the browser automatically.
"""
import http.server
import socketserver
import webbrowser
import os
import sys

PORT = 5050
DIRECTORY = r"D:\AURA"

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "":
            self.path = "/aura_web_ui.html"
        return super().do_GET()

if __name__ == "__main__":
    url = f"http://localhost:{PORT}/aura_web_ui.html"
    print(f"Starting AURA Web Console at {url}")
    print("Press Ctrl+C to stop.")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")