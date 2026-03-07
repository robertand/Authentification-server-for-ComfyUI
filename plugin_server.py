#!/usr/bin/env python3
"""
Plugin Server pentru ComfyUI - Aggregator & Full Proxy
Colectează userii de la mai multe servere de autentificare și routează tot traficul prin acest server.
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
import base64
from urllib.parse import urlparse, urlunparse, quote

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
save_config(config) # Salvează pentru a asigura secretele

# === SESSION STORAGE ===
# agg_session_id -> { "user": username, "server_url": url, "auth_session_id": sid, "created": time }
AGG_SESSIONS = {}

def get_agg_session(session_id):
    if not session_id: return None
    return AGG_SESSIONS.get(session_id)

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

        # Încercăm login pe serverul de backend
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

            # Verificăm dacă am primit cookie-ul de sesiune
            auth_session_id = None
            if "Set-Cookie" in response.headers:
                # Căutăm session_id în cookie-uri
                cookies = response.headers.get_list("Set-Cookie")
                for cookie in cookies:
                    if "session_id=" in cookie:
                        # Extragem valoarea cookie-ului (fără atributele secure, path etc.)
                        match = re.search(r'session_id=([^;]+)', cookie)
                        if match:
                            auth_session_id = match.group(1)
                            break

            if response.code in [302, 200] and auth_session_id:
                # Login succes
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

class AggregatorProxyHandler(AggregatorBaseHandler):
    async def _proxy(self, method, path):
        session = self.get_current_user()
        if not session:
            # Dacă e o cerere de API, dăm 401, altfel redirect la login
            if path.startswith('api/') or path.startswith('comfy/'):
                self.set_status(401)
                return
            self.redirect("/")
            return

        target_base = session["server_url"].rstrip('/')
        # Construim path-ul corect. Dacă path-ul original începe cu /comfy/, îl păstrăm.
        # Serverul original se așteaptă la rute ca /comfy/... sau /check-session etc.
        uri = self.request.uri
        target_url = f"{target_base}{uri}"

        client = tornado.httpclient.AsyncHTTPClient()
        headers = dict(self.request.headers)

        # Setează cookie-ul de backend
        headers["Cookie"] = f"session_id={session['auth_session_id']}"
        # Setează host-ul corect
        parsed_target = urlparse(target_url)
        headers["Host"] = parsed_target.netloc
        headers["X-Forwarded-Host"] = self.request.host

        try:
            req = tornado.httpclient.HTTPRequest(
                url=target_url,
                method=method,
                headers=headers,
                body=self.request.body if method in ["POST", "PUT", "PATCH"] else None,
                follow_redirects=False,
                request_timeout=300,
                decompress_response=True,
                validate_cert=False
            )

            response = await client.fetch(req, raise_error=False)
            self.set_status(response.code)

            for header, value in response.headers.get_all():
                if header.lower() not in ['content-length', 'content-encoding', 'transfer-encoding', 'connection']:
                    if header.lower() == 'location':
                        # Rescriem locația dacă e absolută către backend
                        if value.startswith(target_base):
                            value = value.replace(target_base, f"{self.request.protocol}://{self.request.host}")
                        self.set_header(header, value)
                    else:
                        self.set_header(header, value)

            if response.body:
                self.write(response.body)

        except Exception as e:
            log.error(f"Proxy error for {target_url}: {e}")
            self.set_status(502)
            self.write(f"Bad Gateway: {str(e)}")

    async def get(self, path=None): await self._proxy("GET", path or "")
    async def post(self, path=None): await self._proxy("POST", path or "")
    async def put(self, path=None): await self._proxy("PUT", path or "")
    async def delete(self, path=None): await self._proxy("DELETE", path or "")
    async def options(self, path=None): await self._proxy("OPTIONS", path or "")

class AggregatorWebSocketProxy(tornado.websocket.WebSocketHandler):
    async def open(self, path=None):
        sid = self.get_secure_cookie("agg_session_id")
        session = get_agg_session(sid.decode() if sid else None)
        if not session:
            self.close(code=4001, reason="Not authenticated")
            return

        target_base = session["server_url"].rstrip('/')
        if target_base.startswith("http://"): ws_base = target_base.replace("http://", "ws://")
        else: ws_base = target_base.replace("https://", "wss://")

        uri = self.request.uri
        self.target_url = f"{ws_base}{uri}"

        headers = dict(self.request.headers)
        headers["Cookie"] = f"session_id={session['auth_session_id']}"
        headers["Host"] = urlparse(self.target_url).netloc
        headers["X-Forwarded-Host"] = self.request.host

        request = tornado.httpclient.HTTPRequest(
            url=self.target_url,
            headers=headers,
            connect_timeout=60,
            request_timeout=600,
            validate_cert=False
        )

        try:
            self.backend_ws = await tornado.websocket.websocket_connect(request)
            asyncio.create_task(self._pipe_backend_to_client())
        except Exception as e:
            log.error(f"WS connect error to {self.target_url}: {e}")
            self.close()

    async def _pipe_backend_to_client(self):
        while True:
            try:
                msg = await self.backend_ws.read_message()
                if msg is None: break
                await self.write_message(msg, isinstance(msg, bytes))
            except: break
        self.close()

    async def on_message(self, message):
        if hasattr(self, 'backend_ws'):
            await self.backend_ws.write_message(message, isinstance(message, bytes))

    def on_close(self):
        if hasattr(self, 'backend_ws'): self.backend_ws.close()

# === ADMIN HANDLERE (Port 8201) ===

class AdminBaseHandler(tornado.web.RequestHandler):
    def get_current_user(self):
        return self.get_secure_cookie("agg_admin_session")

class AdminLoginHandler(AdminBaseHandler):
    def get(self):
        self.render("plugin_admin_login.html", error="")
    def post(self):
        password = self.get_argument("password", "")
        if password == config.get("admin_password", "admin"):
            self.set_secure_cookie("agg_admin_session", "logged_in")
            self.redirect("/admin")
        else:
            self.render("plugin_admin_login.html", error="Invalid password")

class AdminMainHandler(AdminBaseHandler):
    @tornado.web.authenticated
    def get(self):
        self.render("plugin_admin.html", config=config)

class AdminApiServersHandler(AdminBaseHandler):
    @tornado.web.authenticated
    def post(self):
        data = json.loads(self.request.body)
        config["servers"].append({
            "url": data["url"],
            "display_name": data["display_name"]
        })
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
    def get(self):
        self.render("plugin_index.html", plugin_name=config["plugin_name"])

class AggregatorRootHandler(AggregatorBaseHandler):
    def get(self):
        if not self.get_secure_cookie("agg_session_id"):
            self.render("plugin_index.html", plugin_name=config["plugin_name"])
        else:
            self.redirect("/comfy/")

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

class AdminRootHandler(AdminBaseHandler):
    def get(self):
        self.redirect("/admin")

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
    agg_app = make_aggregator_app()
    admin_app = make_admin_app()

    agg_port = config.get("port", 8200)
    admin_port = config.get("admin_port", 8201)

    agg_app.listen(agg_port)
    admin_app.listen(admin_port)

    log.info(f"Aggregator Server pornit pe portul {agg_port}")
    log.info(f"Aggregator Admin pornit pe portul {admin_port}")

    tornado.ioloop.IOLoop.current().start()
