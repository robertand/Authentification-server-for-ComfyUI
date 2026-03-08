#!/usr/bin/env python3
"""
Plugin Server pentru ComfyUI - Aggregator & High-Performance Proxy (Synced with Auth Server)
Colectează userii de la mai multe servere de autentificare și routează tot traficul prin acest server.
Folosește aceeași logică robustă de proxy ca și serverul de autentificare original.
"""

import tornado.ioloop
import tornado.web
import tornado.websocket
import tornado.httpclient
import tornado.httputil
import json
import logging
import os
import time
import uuid
import re
import socket
import base64
from urllib.parse import urlparse, urlunparse, quote, unquote

# === LOGGING ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("AGGREGATOR")

CONFIG_FILE = "plugin_config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                conf = json.load(f)
                if "servers" not in conf: conf["servers"] = []
                if "plugin_name" not in conf: conf["plugin_name"] = "PRO AI Aggregator"
                if "port" not in conf: conf["port"] = 8200
                if "admin_port" not in conf: conf["admin_port"] = 8201
                if "cookie_secret" not in conf:
                    conf["cookie_secret"] = base64.b64encode(os.urandom(32)).decode()
                if "admin_password" not in conf:
                    conf["admin_password"] = "admin"
                return conf
        except Exception as e:
            log.error(f"Eroare la încărcarea configurației: {e}")

    return {
        "plugin_name": "PRO AI Aggregator",
        "port": 8200,
        "admin_port": 8201,
        "servers": [],
        "cookie_secret": base64.b64encode(os.urandom(32)).decode(),
        "admin_password": "admin"
    }

def save_config(conf):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(conf, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log.error(f"Eroare la salvarea configurației: {e}")

config = load_config()
save_config(config)

# === SESSION STORAGE ===
AGG_SESSIONS = {}

def get_agg_session(session_id):
    if not session_id: return None
    return AGG_SESSIONS.get(session_id)

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

# === HANDLERE AGGREGATOR ===

class AggregatorBaseHandler(tornado.web.RequestHandler):
    def get_current_user(self):
        session_id = self.get_secure_cookie("agg_session_id")
        if not session_id: return None
        return get_agg_session(session_id.decode())

    def get_client_ip(self):
        forwarded_for = self.request.headers.get("X-Forwarded-For")
        if forwarded_for: return forwarded_for.split(",")[0].strip()
        return self.request.remote_ip

class AggregatedStatusHandler(AggregatorBaseHandler):
    async def get(self):
        client = tornado.httpclient.AsyncHTTPClient()
        aggregated_users = []

        responses = []
        for server in config["servers"]:
            req = tornado.httpclient.HTTPRequest(
                url=f"{server['url'].rstrip('/')}/user-status",
                method="GET",
                headers={"X-Plugin-Name": config["plugin_name"]},
                request_timeout=5
            )
            responses.append((server, client.fetch(req, raise_error=False)))

        for server_info, future in responses:
            response = await future
            if response.code == 200:
                try:
                    data = json.loads(response.body)
                    users = data.get("users", [])
                    for user in users:
                        user["server_url"] = server_info["url"]
                        user["server_name"] = server_info["display_name"]
                    aggregated_users.extend(users)
                except Exception as e:
                    log.error(f"Eroare la procesarea răspunsului de la {server_info['url']}: {e}")

        self.set_header("Content-Type", "application/json")
        self.write({"users": aggregated_users})

class AggregatorLoginHandler(AggregatorBaseHandler):
    def get(self):
        username = self.get_argument("username", "")
        server_url = self.get_argument("server_url", "")
        self.render("plugin_login.html", plugin_name=config["plugin_name"], username=username, server_url=server_url, error="")

    async def post(self):
        username = self.get_argument("username", "")
        password = self.get_argument("password", "")
        server_url = self.get_argument("server_url", "")

        if not server_url:
            self.render("plugin_login.html", plugin_name=config["plugin_name"], username=username, server_url=server_url, error="Server URL is missing")
            return

        client = tornado.httpclient.AsyncHTTPClient()
        body = f"username={quote(username)}&password={quote(password)}"

        try:
            req = tornado.httpclient.HTTPRequest(
                url=f"{server_url.rstrip('/')}/login",
                method="POST",
                body=body,
                follow_redirects=False,
                request_timeout=10
            )
            response = await client.fetch(req, raise_error=False)

            auth_session_id = None
            if "Set-Cookie" in response.headers:
                cookies = response.headers.get_list("Set-Cookie")
                for cookie in cookies:
                    if "session_id=" in cookie:
                        match = re.search(r'session_id=([^;]+)', cookie)
                        if match:
                            auth_session_id = match.group(1)
                            break

            if response.code in [302, 200] and auth_session_id:
                agg_sid = str(uuid.uuid4())
                AGG_SESSIONS[agg_sid] = {
                    "user": username,
                    "server_url": server_url,
                    "auth_session_id": auth_session_id,
                    "created": time.time()
                }
                self.set_secure_cookie("agg_session_id", agg_sid, expires_days=1, path="/")
                log.info(f"User {username} logged in via Aggregator for server {server_url}")
                self.redirect("/")
            else:
                self.render("plugin_login.html", plugin_name=config["plugin_name"], username=username, server_url=server_url, error="Invalid credentials or server error")
        except Exception as e:
            log.error(f"Login error for {server_url}: {e}")
            self.render("plugin_login.html", plugin_name=config["plugin_name"], username=username, server_url=server_url, error=f"Connection error: {str(e)}")

class AggregatorLogoutHandler(AggregatorBaseHandler):
    def get(self):
        session_id = self.get_secure_cookie("agg_session_id")
        if session_id:
            session_id = session_id.decode()
            if session_id in AGG_SESSIONS:
                del AGG_SESSIONS[session_id]
        self.clear_cookie("agg_session_id", path="/")
        self.redirect("/")

# === ROBUST PROXY HANDLER (Synced with auth_server.py) ===

class AggregatorProxyHandler(AggregatorBaseHandler):
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "x-requested-with, content-type, authorization, x-forwarded-for, x-real-ip, x-forwarded-proto, x-forwarded-host, cookie")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS, PATCH, HEAD")
        self.set_header("Access-Control-Allow-Credentials", "true")

    async def options(self, path=None): await self._proxy_request("OPTIONS", path or "")
    async def get(self, path=None): await self._proxy_request("GET", path or "")
    async def post(self, path=None): await self._proxy_request("POST", path or "")
    async def put(self, path=None): await self._proxy_request("PUT", path or "")
    async def delete(self, path=None): await self._proxy_request("DELETE", path or "")
    async def head(self, path=None): await self._proxy_request("HEAD", path or "")
    async def patch(self, path=None): await self._proxy_request("PATCH", path or "")

    def _get_port_from_host(self):
        host = self.request.headers.get("Host", "")
        if ":" in host:
            try: return int(host.split(":")[1])
            except: pass
        return 443 if self.request.protocol == "https" else 80

    def _rewrite_urls(self, content, backend_url, aggregator_base_url):
        if not content: return content
        parsed_backend = urlparse(backend_url)
        backend_host = parsed_backend.netloc
        backend_scheme = parsed_backend.scheme
        backend_port = parsed_backend.port

        parsed_agg = urlparse(aggregator_base_url)
        agg_host = parsed_agg.netloc
        agg_scheme = parsed_agg.scheme

        local_ip = get_local_ip()
        internal_hosts = {backend_host, "localhost", "127.0.0.1", local_ip}
        if backend_port:
            internal_hosts.add(f"localhost:{backend_port}")
            internal_hosts.add(f"127.0.0.1:{backend_port}")
            internal_hosts.add(f"{local_ip}:{backend_port}")

        # Identic cu auth_server.py
        internal_routes = (
            '/comfy/', '/static/', '/css/', '/js/', '/login', '/logout',
            '/user-status', '/user-settings', '/chat-', '/send-message',
            '/upload-chat', '/download-file', '/mark-messages', '/unread-messages',
            '/chat-ws', '/health', '/check-session', '/refresh-session', '/api/workflows',
            'http://', 'https://', 'ws://', 'wss://', 'data:', 'blob:'
        )

        for host in internal_hosts:
            pattern = fr'https?://{re.escape(host)}'
            replacement = f'{agg_scheme}://{agg_host}/comfy' # Aggregator mapaza /comfy la backend
            content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)
            pattern = fr'wss?://{re.escape(host)}'
            replacement = f'ws{"s" if agg_scheme=="https" else ""}://{agg_host}/comfy'
            content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)

        def replace_relative_url(match):
            full_match = match.group(0)
            prefix = match.group(1)
            url = match.group(2)
            suffix = match.group(3)
            if not url or url.startswith(internal_routes): return full_match
            new_url = f'/comfy{url}' if url.startswith('/') else f'/comfy/{url}'
            return f'{prefix}{new_url}{suffix}'

        attrs = ['src', 'href', 'action', 'data-src', 'data-href']
        for attr in attrs:
            content = re.sub(fr'({attr}=["\'])([^"\']*)(["\'])', replace_relative_url, content)

        for host in internal_hosts:
            content = content.replace(f'://{host}/comfy', f'://{agg_host}/comfy')
            content = content.replace(f'://{host}', f'://{agg_host}/comfy')
        return content

    async def _proxy_request(self, method, path):
        session = self.get_current_user()
        if not session:
            if path and path.startswith(('api/', 'view/', 'upload/', 'websocket', 'comfy/')):
                self.set_status(401)
                return
            self.redirect("/")
            return

        backend_base = session["server_url"].rstrip('/')
        raw_uri = self.request.uri
        target_url = f"{backend_base}{raw_uri}"

        log.info(f"Aggregator Proxy: {method} {raw_uri} -> {target_url}")
        client = tornado.httpclient.AsyncHTTPClient()

        try:
            headers = {}
            exclude = ['host', 'content-length', 'connection', 'keep-alive', 'accept-encoding', 'content-encoding', 'transfer-encoding', 'cookie']
            for h, v in self.request.headers.items():
                if h.lower() not in exclude: headers[h] = v

            headers['Host'] = urlparse(target_url).netloc
            headers['Cookie'] = f"session_id={session['auth_session_id']}"
            headers['X-Forwarded-For'] = self.get_client_ip()
            headers['X-Forwarded-Proto'] = self.request.protocol
            headers['X-Forwarded-Host'] = self.request.host
            headers['Sec-Fetch-Site'] = 'same-origin'

            # Spoof Origin/Referer
            if 'Origin' in self.request.headers:
                headers['Origin'] = f"{urlparse(backend_base).scheme}://{headers['Host']}"
            if 'Referer' in self.request.headers:
                ref_parsed = urlparse(self.request.headers['Referer'])
                headers['Referer'] = urlunparse((urlparse(backend_base).scheme, headers['Host'], ref_parsed.path, ref_parsed.params, ref_parsed.query, ref_parsed.fragment))

            streaming_callback = self._stream_response if int(self.request.headers.get('Content-Length', 0)) > 10*1024*1024 else None

            req = tornado.httpclient.HTTPRequest(
                url=target_url,
                method=method,
                headers=headers,
                body=self.request.body if method in ["POST", "PUT", "DELETE", "PATCH"] else None,
                follow_redirects=False,
                connect_timeout=30,
                request_timeout=300,
                validate_cert=False,
                decompress_response=True,
                allow_nonstandard_methods=True,
                streaming_callback=streaming_callback
            )

            response = await client.fetch(req, raise_error=False)
            self.set_status(response.code)

            for h, v in response.headers.get_all():
                hl = h.lower()
                if hl not in ['content-length', 'content-encoding', 'transfer-encoding', 'connection', 'keep-alive', 'content-security-policy']:
                    if hl == 'location' and v.startswith(backend_base):
                        v = v.replace(backend_base, f"{self.request.protocol}://{self.request.host}")
                    self.set_header(h, v)

            if "Access-Control-Allow-Origin" not in self._headers: self.set_header("Access-Control-Allow-Origin", "*")
            if streaming_callback: return

            content_type = response.headers.get('Content-Type', '').lower()
            is_html = 'text/html' in content_type and response.code not in [204, 304]
            is_json = 'application/json' in content_type and response.code not in [204, 304]

            if (is_html or is_json) and response.body:
                encoding = 'utf-8'
                if 'charset=' in content_type:
                    m = re.search(r'charset=([\w-]+)', content_type, re.IGNORECASE)
                    if m: encoding = m.group(1)

                content = response.body.decode(encoding, errors='replace')
                agg_base_url = f"{self.request.protocol}://{self.request.host}"
                content = self._rewrite_urls(content, backend_base, agg_base_url)
                self.write(content.encode(encoding))
            elif response.code != 304 and response.body:
                self.write(response.body)

        except Exception as e:
            log.error(f"Aggregator Proxy Error: {e}", exc_info=True)
            self.set_status(502)
            self.write(f"Bad Gateway: {str(e)}")

    async def _stream_response(self, chunk):
        try:
            self.write(chunk)
            await self.flush()
        except: pass

# === SYNCED WEBSOCKET PROXY ===

class AggregatorWebSocketProxy(tornado.websocket.WebSocketHandler):
    async def open(self, path=None):
        sid = self.get_secure_cookie("agg_session_id")
        session = get_agg_session(sid.decode() if sid else None)
        if not session:
            self.close(code=4001, reason="Not authenticated")
            return

        backend_base = session["server_url"].rstrip('/')
        ws_scheme = "wss" if backend_base.startswith("https") else "ws"
        backend_netloc = urlparse(backend_base).netloc

        # Includem session_id în query string pentru backend (comportament synced)
        uri_parts = list(urlparse(self.request.uri))
        query = uri_parts[4]
        new_query = f"{query}&session_id={session['auth_session_id']}" if query else f"session_id={session['auth_session_id']}"
        uri_parts[0] = ws_scheme
        uri_parts[1] = backend_netloc
        uri_parts[4] = new_query

        self.target_url = urlunparse(uri_parts)
        log.info(f"Aggregator WS connecting: {self.target_url}")

        ws_headers = {}
        for h in ['User-Agent', 'Accept-Language', 'Cookie']:
            if h in self.request.headers: ws_headers[h] = self.request.headers[h]

        ws_headers['Host'] = backend_netloc
        ws_headers['Origin'] = f"{'https' if ws_scheme == 'wss' else 'http'}://{backend_netloc}"
        ws_headers['Cookie'] = f"session_id={session['auth_session_id']}"
        ws_headers['X-Forwarded-For'] = self.request.headers.get("X-Forwarded-For", self.request.remote_ip)
        ws_headers['X-Forwarded-Host'] = self.request.host

        request = tornado.httpclient.HTTPRequest(
            url=self.target_url,
            headers=ws_headers,
            connect_timeout=60,
            request_timeout=600,
            validate_cert=False
        )

        try:
            self.backend_ws = await tornado.websocket.websocket_connect(request, ping_interval=20, ping_timeout=30)
            self._running = True
            asyncio.create_task(self._pipe_backend_to_client())
            asyncio.create_task(self._keep_alive())
        except Exception as e:
            log.error(f"Aggregator WS connect error: {e}")
            self.close()

    async def _pipe_backend_to_client(self):
        while self._running:
            try:
                msg = await self.backend_ws.read_message()
                if msg is None: break
                if self.ws_connection and not self.ws_connection.is_closing():
                    await self.write_message(msg, isinstance(msg, bytes))
            except: break
        self.close()

    async def on_message(self, message):
        if self._running and hasattr(self, 'backend_ws'):
            try: await self.backend_ws.write_message(message, isinstance(message, bytes))
            except: self.close()

    def on_close(self):
        self._running = False
        if hasattr(self, 'backend_ws'): self.backend_ws.close()

    async def _keep_alive(self):
        while self._running:
            await asyncio.sleep(20)
            if not self._running: break
            try: self.backend_ws.ping(b'ping')
            except: pass
            try:
                if self.ws_connection and not self.ws_connection.is_closing():
                    self.ping(b'ping')
            except: pass

# === ADMIN HANDLERE (Port 8201) ===

class AdminBaseHandler(tornado.web.RequestHandler):
    def get_current_user(self): return self.get_secure_cookie("agg_admin_session")

class AdminLoginHandler(AdminBaseHandler):
    def get(self): self.render("plugin_admin_login.html", error="")
    def post(self):
        if self.get_argument("password", "") == config.get("admin_password", "admin"):
            self.set_secure_cookie("agg_admin_session", "logged_in")
            self.redirect("/admin")
        else: self.render("plugin_admin_login.html", error="Invalid password")

class AdminMainHandler(AdminBaseHandler):
    @tornado.web.authenticated
    def get(self): self.render("plugin_admin.html", config=config)

class AdminApiServersHandler(AdminBaseHandler):
    @tornado.web.authenticated
    def post(self):
        data = json.loads(self.request.body)
        config["servers"].append({"url": data["url"], "display_name": data["display_name"]})
        save_config(config)
        self.write({"success": True})
    @tornado.web.authenticated
    def delete(self):
        idx = int(self.get_argument("index"))
        if 0 <= idx < len(config["servers"]):
            config["servers"].pop(idx)
            save_config(config)
        self.write({"success": True})

class AggregatorDashboardHandler(AggregatorBaseHandler):
    def get(self): self.render("plugin_index.html", plugin_name=config["plugin_name"])

class AggregatorRootHandler(AggregatorBaseHandler):
    def get(self):
        if not self.get_secure_cookie("agg_session_id"): self.render("plugin_index.html", plugin_name=config["plugin_name"])
        else: self.redirect("/comfy/")

class AdminRootHandler(AdminBaseHandler):
    def get(self): self.redirect("/admin")

def make_aggregator_app():
    return tornado.web.Application([
        (r"/", AggregatorRootHandler),
        (r"/dashboard", AggregatorDashboardHandler),
        (r"/login", AggregatorLoginHandler),
        (r"/logout", AggregatorLogoutHandler),
        (r"/api/status", AggregatedStatusHandler),
        (r"/comfy/ws", AggregatorWebSocketProxy),
        (r"/ws", AggregatorWebSocketProxy),
        (r"/static/(.*)", tornado.web.StaticFileHandler, {'path': os.path.join(os.path.dirname(__file__), "static")}),
        (r"/(.*)", AggregatorProxyHandler),
    ],
    template_path=os.path.join(os.path.dirname(__file__), "templates"),
    cookie_secret=config["cookie_secret"],
    login_url="/login"
    )

def make_admin_app():
    return tornado.web.Application([
        (r"/login", AdminLoginHandler),
        (r"/admin", AdminMainHandler),
        (r"/api/servers", AdminApiServersHandler),
        (r"/static/(.*)", tornado.web.StaticFileHandler, {'path': os.path.join(os.path.dirname(__file__), "static")}),
        (r"/", AdminRootHandler),
    ],
    template_path=os.path.join(os.path.dirname(__file__), "templates"),
    cookie_secret=config["cookie_secret"],
    login_url="/login"
    )

if __name__ == "__main__":
    agg_app, admin_app = make_aggregator_app(), make_admin_app()
    agg_port, admin_port = config.get("port", 8200), config.get("admin_port", 8201)
    agg_app.listen(agg_port)
    admin_app.listen(admin_port)
    log.info(f"Aggregator Server pornit pe portul {agg_port} (Synced High-Performance Proxy)")
    log.info(f"Aggregator Admin pornit pe portul {admin_port}")
    tornado.ioloop.IOLoop.current().start()
