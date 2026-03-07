#!/usr/bin/env python3
"""
Plugin Server pentru ComfyUI - Aggregator
Colectează userii de la mai multe servere de autentificare și îi afișează într-o interfață unică.
"""

import tornado.ioloop
import tornado.web
import tornado.httpclient
import json
import logging
import os
import time

# === LOGGING ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("PLUGIN")

CONFIG_FILE = "plugin_config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Eroare la încărcarea configurației: {e}")

    return {
        "plugin_name": "PRO AI Plugin Server",
        "port": 8200,
        "servers": []
    }

config = load_config()

class AggregatedStatusHandler(tornado.web.RequestHandler):
    async def get(self):
        client = tornado.httpclient.AsyncHTTPClient()
        aggregated_users = []

        # Facem cereri în paralel către toate serverele
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
            else:
                log.warning(f"Serverul {server_info['url']} a răspuns cu codul {response.code}")

        self.set_header("Content-Type", "application/json")
        self.write({"users": aggregated_users})

class MainHandler(tornado.web.RequestHandler):
    def get(self):
        self.render("plugin_index.html", plugin_name=config["plugin_name"])

def make_app():
    return tornado.web.Application([
        (r"/", MainHandler),
        (r"/api/status", AggregatedStatusHandler),
        (r"/static/(.*)", tornado.web.StaticFileHandler, {'path': os.path.join(os.path.dirname(__file__), "static")}),
    ],
    template_path=os.path.join(os.path.dirname(__file__), "templates"),
    debug=True
    )

if __name__ == "__main__":
    app = make_app()
    port = config.get("port", 8200)
    app.listen(port)
    log.info(f"Plugin Server pornit pe portul {port}")
    print(f"Aggregator UI: http://localhost:{port}")
    tornado.ioloop.IOLoop.current().start()
