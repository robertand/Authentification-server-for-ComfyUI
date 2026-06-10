from http.server import HTTPServer, BaseHTTPRequestHandler
import socket

class Proxy(BaseHTTPRequestHandler):
    def do_GET(self):
        conn = socket.create_connection(('localhost', 8710))
        request = f"GET {self.path} HTTP/1.1\r\nHost: ro.ai.protv.ro\r\nConnection: close\r\n\r\n"
        conn.send(request.encode())
        response = conn.recv(4096)
        self.wfile.write(response)
        conn.close()

HTTPServer(('0.0.0.0', 8711), Proxy).serve_forever()
