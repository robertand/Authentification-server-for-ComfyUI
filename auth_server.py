#!/usr/bin/env python3
"""
Auth Server pentru ComfyUI - ADMIN INTERFACE
Proxy complet - tot traficul trece prin server cu rescriere completă de URL-uri
"""

import tornado.ioloop
import tornado.web
import tornado.websocket
import tornado.httpclient
import tornado.httputil
import time
import requests
import threading
import logging
import asyncio
import uuid
import re
import json
import hashlib
import hmac
import socket
import os
import bcrypt
import base64
from urllib.parse import quote, unquote, urlparse, urlunparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# === CONFIGURARE ===
CHUNK_SIZE = 64 * 1024  # 64KB chunks pentru streaming
MAX_BUFFER_SIZE = 1024 * 1024 * 1024  # 1GB pentru fișiere mari
MAX_CLIENTS = 100  # Număr maxim de conexiuni concurente

# === LOGGING ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("AUTH")

# === CONFIGURARE ===
CONFIG_FILE = "comfyui_auth_config.json"
AUTH_PORT = 7861  # Auth server principal
ADMIN_PORT = 8199  # Admin interface

# Configurație implicită
DEFAULT_CONFIG = {
    "users": {
        "user1": {
            "password": "comfy.123", 
            "comfy_url": "http://127.0.0.1:8188", 
            "instances": 0, 
            "max_instances": 2, 
            "session_timeout": 60, 
            "enabled": True,
            "nginx_auth": {
                "enabled": False,
                "username": "",
                "password": ""
            }
        },
        "user2": {
            "password": "comfy.123", 
            "comfy_url": "http://10.129.131.12:8188", 
            "instances": 0, 
            "max_instances": 2, 
            "session_timeout": 60, 
            "enabled": True,
            "nginx_auth": {
                "enabled": False,
                "username": "",
                "password": ""
            }
        }
    },
    "admin": {
        "password": "admin123",
        "enabled": True
    },
    "workflow_root": "/mnt/prouser/spatiu/ComfyUI/workflows",
    "global_nginx_auth": {
        "enabled": False,
        "username": "",
        "password": ""
    }
}

# === SECURITATE - RATE LIMITING ===
FAILED_LOGIN_ATTEMPTS = {}
MAX_ATTEMPTS_PER_5MIN = 5
BLOCK_TIME = 900  # 15 minute

# === CHAT SYSTEM ===
CHAT_MESSAGES = {}
ADMIN_CHAT_WEBSOCKETS = set()
USER_CHAT_WEBSOCKETS = {}
USER_TYPING_STATUS = {}

# === FILE STORAGE FOR CHAT ===
CHAT_FILES = {}
CHAT_FILES_DIR = "chat_files"

# === WORKFLOW BROWSER ===
WORKFLOW_ROOT_DIR = "/mnt/prouser/spatiu/ComfyUI/workflows"

# Create chat files directory if it doesn't exist
if not os.path.exists(CHAT_FILES_DIR):
    os.makedirs(CHAT_FILES_DIR, exist_ok=True)

# === STOCARE SESIUNI ===
sessions = {}
admin_sessions = {}
DEFAULT_SESSION_TIMEOUT = 3600

# Instanțe externe adăugate prin admin interface
EXTERNAL_INSTANCES = {}
BLOCKED_USERS = {}
FORCED_LOGOUT_SESSIONS = set()

comfy_instances_ready = {}

class RateLimiter:
    @staticmethod
    def is_blocked(ip):
        if ip in FAILED_LOGIN_ATTEMPTS:
            data = FAILED_LOGIN_ATTEMPTS[ip]
            if time.time() - data["reset_time"] > 300:
                del FAILED_LOGIN_ATTEMPTS[ip]
                return False
            if data["count"] >= MAX_ATTEMPTS_PER_5MIN:
                if time.time() - data["block_time"] < BLOCK_TIME:
                    return True
                else:
                    del FAILED_LOGIN_ATTEMPTS[ip]
        return False
    
    @staticmethod
    def record_failed_attempt(ip):
        if ip not in FAILED_LOGIN_ATTEMPTS:
            FAILED_LOGIN_ATTEMPTS[ip] = {
                "count": 0, 
                "reset_time": time.time() + 300,
                "block_time": 0
            }
        
        FAILED_LOGIN_ATTEMPTS[ip]["count"] += 1
        
        if FAILED_LOGIN_ATTEMPTS[ip]["count"] >= MAX_ATTEMPTS_PER_5MIN:
            FAILED_LOGIN_ATTEMPTS[ip]["block_time"] = time.time()
    
    @staticmethod
    def clear_attempts(ip):
        if ip in FAILED_LOGIN_ATTEMPTS:
            del FAILED_LOGIN_ATTEMPTS[ip]

# === SECURITATE - PASSWORD HASHING ===
def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(hashed_password, user_password):
    if not hashed_password or not user_password:
        return False
    try:
        return bcrypt.checkpw(user_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except:
        return False

def upgrade_passwords(config):
    for username, user_data in config["users"].items():
        if user_data["password"] and not user_data["password"].startswith("$2b$"):
            user_data["password"] = hash_password(user_data["password"])
            log.info(f"Upgraded password for user {username} to hashed version")
    
    if config["admin"]["password"] and not config["admin"]["password"].startswith("$2b$"):
        config["admin"]["password"] = hash_password(config["admin"]["password"])
        log.info("Upgraded admin password to hashed version")
    
    return config

# === VALIDARE INPUT ===
def validate_username(username):
    if not username or len(username) < 2 or len(username) > 20:
        return False
    return bool(re.match(r'^[a-zA-Z0-9_]+$', username))

def validate_password(password):
    if not password or len(password) < 3:
        return False
    return True

# === TEMP TOKEN FUNCTIONS ===
def generate_temp_token(session_id):
    key = "comfy_temp_key_2025"
    return hmac.new(key.encode(), (session_id + str(time.time())).encode(), hashlib.sha256).hexdigest()[:32]

# === FILE MANAGEMENT FOR CHAT ===
def save_chat_file(filename, file_data, content_type, uploaded_by):
    file_id = str(uuid.uuid4())
    filepath = os.path.join(CHAT_FILES_DIR, file_id)
    
    with open(filepath, 'wb') as f:
        f.write(file_data)
    
    CHAT_FILES[file_id] = {
        "filename": filename,
        "filepath": filepath,
        "content_type": content_type,
        "uploaded_by": uploaded_by,
        "timestamp": time.time(),
        "size": len(file_data)
    }
    
    cleanup_old_chat_files()
    return file_id

def get_chat_file(file_id):
    if file_id in CHAT_FILES:
        file_info = CHAT_FILES[file_id]
        try:
            with open(file_info["filepath"], 'rb') as f:
                return f.read(), file_info
        except:
            return None, None
    return None, None

def cleanup_old_chat_files():
    current_time = time.time()
    expired_files = []
    
    for file_id, file_info in CHAT_FILES.items():
        if current_time - file_info["timestamp"] > 86400:
            expired_files.append(file_id)
    
    for file_id in expired_files:
        try:
            os.remove(CHAT_FILES[file_id]["filepath"])
            del CHAT_FILES[file_id]
        except:
            pass

# === WORKFLOW FUNCTIONS ===
def get_user_workflow_dir(username):
    user_dir = os.path.join(WORKFLOW_ROOT_DIR, username)
    if not os.path.exists(user_dir):
        os.makedirs(user_dir, exist_ok=True)
        log.info(f"Created workflow directory for user {username}: {user_dir}")
    return user_dir

def list_user_workflows(username):
    user_dir = get_user_workflow_dir(username)
    workflows = []
    
    if os.path.exists(user_dir):
        for filename in os.listdir(user_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(user_dir, filename)
                if os.path.isfile(filepath):
                    stats = os.stat(filepath)
                    workflows.append({
                        'name': filename,
                        'path': filepath,
                        'modified': stats.st_mtime,
                        'size': stats.st_size
                    })
    
    workflows.sort(key=lambda x: x['modified'], reverse=True)
    return workflows

# Încarcă configurația din fișier sau folosește cea implicită
def load_config():
    global WORKFLOW_ROOT_DIR
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                log.info("✓ Configurație încărcată din fișier")
        except Exception as e:
            log.error(f"Eroare la încărcarea configurației: {e}")
            config = DEFAULT_CONFIG.copy()
    else:
        log.info("✓ Folosind configurația implicită")
        config = DEFAULT_CONFIG.copy()

    if "cookie_secret" not in config:
        log.info("Generating new cookie secret")
        config["cookie_secret"] = base64.b64encode(os.urandom(32)).decode()
    
    for username, user_data in config["users"].items():
        if "nginx_auth" not in user_data:
            user_data["nginx_auth"] = {
                "enabled": False,
                "username": "",
                "password": ""
            }
    
    if "global_nginx_auth" not in config:
        config["global_nginx_auth"] = {
            "enabled": False,
            "username": "",
            "password": ""
        }
    
    config = upgrade_passwords(config)
    
    if "workflow_root" in config:
        WORKFLOW_ROOT_DIR = config["workflow_root"]
        log.info(f"✓ Workflow root directory loaded: {WORKFLOW_ROOT_DIR}")
    
    return config

def save_config():
    try:
        config_data = {
            "users": USERS,
            "admin": ADMIN_CONFIG,
            "workflow_root": WORKFLOW_ROOT_DIR,
            "global_nginx_auth": GLOBAL_NGINX_AUTH,
            "cookie_secret": config.get("cookie_secret")
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        log.info("✓ Configurație salvată în fișier")
    except Exception as e:
        log.error(f"Eroare la salvarea configurației: {e}")

def cleanup_stuck_sessions():
    log.info("Checking for stuck sessions...")
    
    for username in USERS:
        USERS[username]["instances"] = 0
    
    sessions.clear()
    FORCED_LOGOUT_SESSIONS.clear()
    BLOCKED_USERS.clear()
    
    log.info("✓ Cleaned up stuck sessions and reset instance counts")

# Încarcă configurația inițială
config = load_config()
USERS = config["users"]
ADMIN_CONFIG = config["admin"]
GLOBAL_NGINX_AUTH = config.get("global_nginx_auth", {"enabled": False, "username": "", "password": ""})

save_config()
cleanup_stuck_sessions()

# === FUNCȚII UTILITARE ===
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def set_security_headers(handler):
    handler.set_header("X-Content-Type-Options", "nosniff")
    handler.set_header("X-Frame-Options", "DENY")
    handler.set_header("X-XSS-Protection", "1; mode=block")
    handler.set_header("Referrer-Policy", "strict-origin-when-cross-origin")

def add_nginx_auth_headers(headers, username):
    if username in USERS:
        user_auth = USERS[username].get("nginx_auth", {})
        if user_auth.get("enabled", False):
            auth_string = f"{user_auth['username']}:{user_auth['password']}"
            encoded_auth = base64.b64encode(auth_string.encode()).decode()
            headers['Authorization'] = f"Basic {encoded_auth}"
            log.info(f"Added nginx auth headers for user {username}")
            return True
    
    if GLOBAL_NGINX_AUTH.get("enabled", False):
        auth_string = f"{GLOBAL_NGINX_AUTH['username']}:{GLOBAL_NGINX_AUTH['password']}"
        encoded_auth = base64.b64encode(auth_string.encode()).decode()
        headers['Authorization'] = f"Basic {encoded_auth}"
        log.info("Added global nginx auth headers")
        return True
    
    return False

# === INITIALIZARE ===
def initialize_instances():
    global comfy_instances_ready
    for user_data in USERS.values():
        comfy_instances_ready[user_data["comfy_url"]] = False

# === VERIFICARE INSTANȚE ===
def check_comfy_ready():
    for user_data in USERS.values():
        threading.Thread(target=check_single_instance, args=(user_data["comfy_url"], user_data.get("nginx_auth", {})), daemon=True).start()

def check_single_instance(comfy_url, nginx_auth=None):
    global comfy_instances_ready
    for i in range(60):
        try:
            headers = {}
            
            if nginx_auth and nginx_auth.get("enabled", False):
                auth_string = f"{nginx_auth['username']}:{nginx_auth['password']}"
                encoded_auth = base64.b64encode(auth_string.encode()).decode()
                headers['Authorization'] = f"Basic {encoded_auth}"
            elif GLOBAL_NGINX_AUTH.get("enabled", False):
                auth_string = f"{GLOBAL_NGINX_AUTH['username']}:{GLOBAL_NGINX_AUTH['password']}"
                encoded_auth = base64.b64encode(auth_string.encode()).decode()
                headers['Authorization'] = f"Basic {encoded_auth}"
            
            r = requests.get(f"{comfy_url}/", timeout=2, headers=headers)
            if r.status_code in [200, 401]:
                comfy_instances_ready[comfy_url] = True
                log.info(f"ComfyUI instance {comfy_url} is ready!")
                return
        except Exception as e:
            if i % 10 == 0:
                log.info(f"Waiting for ComfyUI {comfy_url}... ({i}/60) - {str(e)}")
        time.sleep(1)
    log.error(f"ComfyUI instance {comfy_url} failed to start within timeout")

# === MANAGEMENT SESIUNI ===
def cleanup_sessions():
    current_time = time.time()
    expired_sessions = []
    
    for session_id, session_data in sessions.items():
        username = session_data["user"]
        user_timeout = USERS.get(username, {}).get("session_timeout", 60)
        
        if user_timeout > 0:
            timeout_seconds = user_timeout * 60
            if current_time - session_data["created"] > timeout_seconds:
                expired_sessions.append(session_id)
                if username in USERS:
                    USERS[username]["instances"] = max(0, USERS[username]["instances"] - 1)
    
    for session_id in expired_sessions:
        del sessions[session_id]

def cleanup_admin_sessions():
    current_time = time.time()
    expired_sessions = []
    
    for session_id, session_data in admin_sessions.items():
        if current_time - session_data["created"] > DEFAULT_SESSION_TIMEOUT:
            expired_sessions.append(session_id)
    
    for session_id in expired_sessions:
        del admin_sessions[session_id]

def cleanup_blocked_users():
    current_time = time.time()
    expired_users = []
    
    for username, expiry_time in BLOCKED_USERS.items():
        if current_time > expiry_time:
            expired_users.append(username)
    
    for username in expired_users:
        del BLOCKED_USERS[username]

def cleanup_forced_logout_sessions():
    current_time = time.time()
    expired_sessions = []
    
    for session_id in FORCED_LOGOUT_SESSIONS:
        if session_id not in sessions:
            expired_sessions.append(session_id)
    
    for session_id in expired_sessions:
        FORCED_LOGOUT_SESSIONS.discard(session_id)

def can_user_login(username):
    if username not in USERS:
        return False
    
    if not USERS[username].get("enabled", True):
        return False
    
    cleanup_blocked_users()
    
    if username in BLOCKED_USERS:
        if time.time() < BLOCKED_USERS[username]:
            return False
        else:
            del BLOCKED_USERS[username]
    
    user_data = USERS[username]
    if user_data["max_instances"] == 0:
        return True
    
    return user_data["instances"] < user_data["max_instances"]

def create_session(username):
    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        "authenticated": True,
        "user": username,
        "comfy_url": USERS[username]["comfy_url"],
        "created": time.time()
    }
    USERS[username]["instances"] += 1
    return session_id

def create_admin_session():
    session_id = str(uuid.uuid4())
    admin_sessions[session_id] = {
        "authenticated": True,
        "created": time.time()
    }
    return session_id

def get_session(session_id):
    if not session_id:
        return None
    
    cleanup_sessions()
    cleanup_forced_logout_sessions()
    return sessions.get(session_id)

def get_admin_session(session_id):
    if not session_id:
        return None
    
    cleanup_admin_sessions()
    return admin_sessions.get(session_id)

def is_authenticated(handler):
    session_id = handler.get_secure_cookie("session_id")
    if not session_id:
        return False
    
    session_data = get_session(session_id.decode())
    return session_data and session_data["authenticated"]

def is_admin_authenticated(handler):
    if not ADMIN_CONFIG["enabled"]:
        return True
    
    session_id = handler.get_secure_cookie("admin_session_id")
    if not session_id:
        return False
    
    session_data = get_admin_session(session_id.decode())
    return session_data and session_data["authenticated"]

# === HANDLERE PENTRU INTERFAȚA PRINCIPALĂ ===
class BaseHandler(tornado.web.RequestHandler):
    def render_html(self, template, **kwargs):
        result = template
        for key, value in kwargs.items():
            placeholder = "{" + key + "}"
            result = result.replace(placeholder, str(value))
        return result
    
    def prepare(self):
        set_security_headers(self)

class LoginHandler(BaseHandler):
    def get_client_ip(self):
        real_ip = self.request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
        forwarded_for = self.request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return self.request.remote_ip

    def get(self):
        if is_authenticated(self):
            self.redirect("/")
            return
        
        client_ip = self.get_client_ip()
        
        if RateLimiter.is_blocked(client_ip):
            remaining_time = BLOCK_TIME - (time.time() - FAILED_LOGIN_ATTEMPTS[client_ip]["block_time"])
            minutes = int(remaining_time // 60)
            seconds = int(remaining_time % 60)
            self.write(f"""
            <div style="text-align: center; margin-top: 100px;">
                <h2 style="color: #dc3545;">Too Many Failed Attempts</h2>
                <p>Your IP has been temporarily blocked due to too many failed login attempts.</p>
                <p>Please try again in {minutes} minutes and {seconds} seconds.</p>
                <a href="/login">Try Again</a>
            </div>
            """)
            return
        
        self.render("login.html", error="")

    def post(self):
        client_ip = self.get_client_ip()
        
        if RateLimiter.is_blocked(client_ip):
            self.set_status(429)
            self.write({"error": "Too many attempts"})
            return
        
        user = self.get_argument("username", "").strip()
        pwd = self.get_argument("password", "")

        if not validate_username(user) or not validate_password(pwd):
            RateLimiter.record_failed_attempt(client_ip)
            log.warning(f"Invalid input format from IP {client_ip}")
            self.render("login.html",
                error='Invalid username or password format!'
            )
            return

        if user in USERS and check_password(USERS[user]["password"], pwd):
            if not USERS[user].get("enabled", True):
                log.warning(f"Disabled user {user} tried to login from IP {client_ip}")
                self.render("login.html",
                    error='This user account is disabled!'
                )
                return
                
            if can_user_login(user):
                session_id = create_session(user)
                self.set_secure_cookie("session_id", session_id, expires_days=1)
                
                RateLimiter.clear_attempts(client_ip)
                
                log.info(f"User {user} logged in successfully from IP {client_ip}")
                self.redirect("/comfy/")
            else:
                if user in BLOCKED_USERS:
                    remaining_time = BLOCKED_USERS[user] - time.time()
                    if remaining_time > 0:
                        minutes = int(remaining_time // 60)
                        seconds = int(remaining_time % 60)
                        self.render("forced_logout.html")
                        return
                
                log.warning(f"User {user} tried to login but limit reached from IP {client_ip}")
                self.render("user_full.html",
                    username=user, 
                    max_instances=USERS[user]["max_instances"]
                )
        else:
            RateLimiter.record_failed_attempt(client_ip)
            log.warning(f"Failed login attempt for user {user} from IP {client_ip}")
            self.render("login.html",
                error='Invalid credentials!'
            )

class UserStatusHandler(BaseHandler):
    def get(self):
        user_status = []
        sorted_users = sorted(USERS.items(), key=lambda x: x[0].lower())
        
        for username, user_data in sorted_users:
            user_status.append({
                "username": username,
                "instances": user_data["instances"],
                "max_instances": user_data["max_instances"],
                "ready": comfy_instances_ready.get(user_data["comfy_url"], False),
                "enabled": user_data.get("enabled", True),
                "nginx_auth": user_data.get("nginx_auth", {"enabled": False})
            })
        
        self.set_header("Content-Type", "application/json")
        self.write({"users": user_status})

class UserSettingsHandler(BaseHandler):
    def post(self):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.get_secure_cookie("session_id").decode()
        session_data = get_session(session_id)
        current_username = session_data["user"]
        
        data = json.loads(self.request.body)
        new_username = data.get("username", "").strip()
        current_password = data.get("current_password", "")
        new_password = data.get("new_password", "")
        
        if not new_username or not current_password:
            self.write({"success": False, "error": "Username and current password are required"})
            return
        
        if not validate_username(new_username):
            self.write({"success": False, "error": "Invalid username format"})
            return
        
        if not check_password(USERS[current_username]["password"], current_password):
            self.write({"success": False, "error": "Current password is incorrect"})
            return
        
        if new_username != current_username and new_username in USERS:
            self.write({"success": False, "error": "Username already exists"})
            return
        
        try:
            if new_username != current_username:
                USERS[new_username] = USERS.pop(current_username)
                for session_id, session_data in sessions.items():
                    if session_data["user"] == current_username:
                        session_data["user"] = new_username
            
            if new_password:
                USERS[new_username]["password"] = hash_password(new_password)
            
            save_config()
            
            log.info(f"User {current_username} updated settings to username: {new_username}")
            self.write({"success": True, "message": "Settings updated successfully"})
            
        except Exception as e:
            log.error(f"Error updating user settings: {e}")
            self.write({"success": False, "error": "Internal server error"})

class LogoutHandler(BaseHandler):
    def get(self):
        session_id = self.get_secure_cookie("session_id")
        username = "Unknown"
        
        if session_id:
            session_id = session_id.decode()
            if session_id in sessions:
                username = sessions[session_id].get("user", "Unknown")
                if username in USERS:
                    USERS[username]["instances"] = max(0, USERS[username]["instances"] - 1)
                del sessions[session_id]
                log.info(f"User {username} logged out")
        
        self.clear_cookie("session_id")
        self.render("logout.html", about_modal="")

# === WORKFLOW HANDLERS ===
class WorkflowListHandler(BaseHandler):
    def get(self):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.get_secure_cookie("session_id").decode()
        session_data = get_session(session_id)
        username = session_data["user"]
        
        workflows = list_user_workflows(username)
        self.set_header("Content-Type", "application/json")
        self.write({
            "success": True,
            "workflows": workflows,
            "user_directory": get_user_workflow_dir(username)
        })

class WorkflowLoadHandler(BaseHandler):
    def get(self, filename):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.get_secure_cookie("session_id").decode()
        session_data = get_session(session_id)
        username = session_data["user"]
        
        if '..' in filename or '/' in filename or '\\' in filename:
            self.set_status(400)
            self.write({"success": False, "error": "Invalid filename"})
            return
        
        user_dir = get_user_workflow_dir(username)
        filepath = os.path.join(user_dir, filename)
        
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            self.set_status(404)
            self.write({"success": False, "error": "Workflow file not found"})
            return
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                workflow_data = json.load(f)
            
            self.set_header("Content-Type", "application/json")
            self.write({
                "success": True,
                "workflow": workflow_data,
                "filename": filename
            })
        except Exception as e:
            log.error(f"Error loading workflow {filename}: {e}")
            self.set_status(500)
            self.write({"success": False, "error": f"Error loading workflow: {str(e)}"})

class WorkflowSaveHandler(BaseHandler):
    def post(self):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.get_secure_cookie("session_id").decode()
        session_data = get_session(session_id)
        username = session_data["user"]
        
        data = json.loads(self.request.body)
        filename = data.get("filename", "").strip()
        workflow_data = data.get("workflow")
        
        if not filename or not workflow_data:
            self.set_status(400)
            self.write({"success": False, "error": "Filename and workflow data are required"})
            return
        
        if not filename.endswith('.json'):
            filename += '.json'
        
        if '..' in filename or '/' in filename or '\\' in filename:
            self.set_status(400)
            self.write({"success": False, "error": "Invalid filename"})
            return
        
        user_dir = get_user_workflow_dir(username)
        filepath = os.path.join(user_dir, filename)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(workflow_data, f, indent=2, ensure_ascii=False)
            
            log.info(f"User {username} saved workflow: {filename}")
            self.write({"success": True, "message": "Workflow saved successfully"})
        except Exception as e:
            log.error(f"Error saving workflow {filename}: {e}")
            self.set_status(500)
            self.write({"success": False, "error": f"Error saving workflow: {str(e)}"})

class WorkflowDeleteHandler(BaseHandler):
    def delete(self, filename):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.get_secure_cookie("session_id").decode()
        session_data = get_session(session_id)
        username = session_data["user"]
        
        if '..' in filename or '/' in filename or '\\' in filename:
            self.set_status(400)
            self.write({"success": False, "error": "Invalid filename"})
            return
        
        user_dir = get_user_workflow_dir(username)
        filepath = os.path.join(user_dir, filename)
        
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            self.set_status(404)
            self.write({"success": False, "error": "Workflow file not found"})
            return
        
        try:
            os.remove(filepath)
            log.info(f"User {username} deleted workflow: {filename}")
            self.write({"success": True, "message": "Workflow deleted successfully"})
        except Exception as e:
            log.error(f"Error deleting workflow {filename}: {e}")
            self.set_status(500)
            self.write({"success": False, "error": f"Error deleting workflow: {str(e)}"})

# === CHAT HANDLERS ===
class ChatMessagesHandler(BaseHandler):
    def get(self):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.get_secure_cookie("session_id").decode()
        session_data = get_session(session_id)
        username = session_data["user"]
        
        if username not in CHAT_MESSAGES:
            CHAT_MESSAGES[username] = []
        
        unread_count = sum(1 for msg in CHAT_MESSAGES[username] if msg["from"] == "admin" and not msg["read"])
        
        self.set_header("Content-Type", "application/json")
        self.write({
            "success": True,
            "messages": CHAT_MESSAGES[username],
            "unread_count": unread_count
        })

class SendMessageHandler(BaseHandler):
    def post(self):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.get_secure_cookie("session_id").decode()
        session_data = get_session(session_id)
        username = session_data["user"]
        
        data = json.loads(self.request.body)
        message = data.get("message", "").strip()
        message_type = data.get("message_type", "text")
        file_data = data.get("file_data")
        
        if not message:
            self.write({"success": False, "error": "Message cannot be empty"})
            return
        
        if username not in CHAT_MESSAGES:
            CHAT_MESSAGES[username] = []
        
        message_data = {
            "from": "user",
            "message": message,
            "timestamp": time.time(),
            "read": True,
            "message_type": message_type,
            "file_data": file_data
        }
        CHAT_MESSAGES[username].append(message_data)
        
        for ws in ADMIN_CHAT_WEBSOCKETS:
            try:
                ws.write_message(json.dumps({
                    "type": "new_message",
                    "from_user": username,
                    "message": message,
                    "timestamp": time.time(),
                    "message_type": message_type,
                    "file_data": file_data
                }))
            except:
                pass
        
        if username in USER_CHAT_WEBSOCKETS:
            for ws in USER_CHAT_WEBSOCKETS[username]:
                try:
                    ws.write_message(json.dumps({
                        "type": "new_message",
                        "from": "user",
                        "message": message,
                        "timestamp": time.time(),
                        "message_type": message_type,
                        "file_data": file_data
                    }))
                except:
                    pass
        
        self.write({"success": True})

class UploadChatFileHandler(BaseHandler):
    def post(self):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.get_secure_cookie("session_id").decode()
        session_data = get_session(session_id)
        username = session_data["user"]
        
        message = self.get_argument("message", "").strip()
        
        if username not in CHAT_MESSAGES:
            CHAT_MESSAGES[username] = []
        
        file_data_list = []
        
        for field_name, files in self.request.files.items():
            if field_name.startswith('file'):
                for file_info in files:
                    filename = file_info['filename']
                    file_body = file_info['body']
                    content_type = file_info['content_type']
                    
                    file_id = save_chat_file(filename, file_body, content_type, username)
                    
                    file_data_list.append({
                        "id": file_id,
                        "filename": filename,
                        "size": len(file_body),
                        "content_type": content_type
                    })
        
        if not message:
            if len(file_data_list) == 1:
                message = f"Sent file: {file_data_list[0]['filename']}"
            else:
                message = f"Sent {len(file_data_list)} files"
        
        message_data = {
            "from": "user",
            "message": message,
            "timestamp": time.time(),
            "read": True,
            "message_type": "file" if file_data_list else "text",
            "file_data": file_data_list[0] if len(file_data_list) == 1 else None
        }
        CHAT_MESSAGES[username].append(message_data)
        
        for ws in ADMIN_CHAT_WEBSOCKETS:
            try:
                ws.write_message(json.dumps({
                    "type": "new_message",
                    "from_user": username,
                    "message": message,
                    "timestamp": time.time(),
                    "message_type": "file" if file_data_list else "text",
                    "file_data": file_data_list[0] if len(file_data_list) == 1 else None
                }))
            except:
                pass
        
        if username in USER_CHAT_WEBSOCKETS:
            for ws in USER_CHAT_WEBSOCKETS[username]:
                try:
                    ws.write_message(json.dumps({
                        "type": "new_message",
                        "from": "user",
                        "message": message,
                        "timestamp": time.time(),
                        "message_type": "file" if file_data_list else "text",
                        "file_data": file_data_list[0] if len(file_data_list) == 1 else None
                    }))
                except:
                    pass
        
        self.write({"success": True})

class DownloadFileHandler(BaseHandler):
    def get(self, file_id):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"error": "Not authenticated"})
            return
        
        file_data, file_info = get_chat_file(file_id)
        
        if not file_data or not file_info:
            self.set_status(404)
            self.write({"error": "File not found"})
            return
        
        self.set_header("Content-Type", "application/octet-stream")
        self.set_header("Content-Disposition", f'attachment; filename="{file_info["filename"]}"')
        self.set_header("Content-Length", len(file_data))
        
        self.write(file_data)

class MarkMessagesReadHandler(BaseHandler):
    def post(self):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.get_secure_cookie("session_id").decode()
        session_data = get_session(session_id)
        username = session_data["user"]
        
        if username in CHAT_MESSAGES:
            for msg in CHAT_MESSAGES[username]:
                if msg["from"] == "admin":
                    msg["read"] = True
        
        self.write({"success": True})

class UnreadMessagesCountHandler(BaseHandler):
    def get(self):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.get_secure_cookie("session_id").decode()
        session_data = get_session(session_id)
        username = session_data["user"]
        
        unread_count = 0
        if username in CHAT_MESSAGES:
            unread_count = sum(1 for msg in CHAT_MESSAGES[username] if msg["from"] == "admin" and not msg["read"])
        
        self.write({"success": True, "unread_count": unread_count})

class ChatWebSocketHandler(tornado.websocket.WebSocketHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.username = None
    
    def check_origin(self, origin):
        return True
    
    def open(self):
        if not is_authenticated(self):
            self.close()
            return
        
        session_id = self.get_secure_cookie("session_id").decode()
        session_data = get_session(session_id)
        self.username = session_data["user"]
        
        if self.username not in CHAT_MESSAGES:
            CHAT_MESSAGES[self.username] = []
        
        if self.username not in USER_CHAT_WEBSOCKETS:
            USER_CHAT_WEBSOCKETS[self.username] = []
        USER_CHAT_WEBSOCKETS[self.username].append(self)
        
        unread_count = sum(1 for msg in CHAT_MESSAGES[self.username] if msg["from"] == "admin" and not msg["read"])
        self.write_message(json.dumps({
            "type": "unread_count",
            "count": unread_count
        }))
    
    def on_message(self, message):
        try:
            data = json.loads(message)
            
            if data.get("type") == "send_message":
                message_text = data.get("message", "").strip()
                message_type = data.get("message_type", "text")
                file_data = data.get("file_data")
                
                if message_text and self.username:
                    message_data = {
                        "from": "user",
                        "message": message_text,
                        "timestamp": time.time(),
                        "read": True,
                        "message_type": message_type,
                        "file_data": file_data
                    }
                    CHAT_MESSAGES[self.username].append(message_data)
                    
                    for ws in ADMIN_CHAT_WEBSOCKETS:
                        try:
                            ws.write_message(json.dumps({
                                "type": "new_message",
                                "from_user": self.username,
                                "message": message_text,
                                "timestamp": time.time(),
                                "message_type": message_type,
                                "file_data": file_data
                            }))
                        except:
                            pass
                    
                    self.write_message(json.dumps({
                        "type": "message_sent",
                        "success": True
                    }))
            
            elif data.get("type") == "typing":
                typing = data.get("typing", False)
                
                for ws in ADMIN_CHAT_WEBSOCKETS:
                    try:
                        ws.write_message(json.dumps({
                            "type": "user_typing",
                            "username": self.username,
                            "typing": typing
                        }))
                    except:
                        pass
            
            elif data.get("type") == "mark_read":
                if self.username in CHAT_MESSAGES:
                    for msg in CHAT_MESSAGES[self.username]:
                        if msg["from"] == "admin":
                            msg["read"] = True
                
                unread_count = sum(1 for msg in CHAT_MESSAGES[self.username] if msg["from"] == "admin" and not msg["read"])
                self.write_message(json.dumps({
                    "type": "unread_count",
                    "count": unread_count
                }))
        
        except Exception as e:
            log.error(f"Error in chat WebSocket: {e}")
    
    def on_close(self):
        if self.username and self.username in USER_CHAT_WEBSOCKETS:
            if self in USER_CHAT_WEBSOCKETS[self.username]:
                USER_CHAT_WEBSOCKETS[self.username].remove(self)
            if not USER_CHAT_WEBSOCKETS[self.username]:
                del USER_CHAT_WEBSOCKETS[self.username]

# === SESSION CHECK HANDLER ===
class SessionCheckHandler(BaseHandler):
    def get(self):
        session_id = self.get_secure_cookie("session_id")
        
        if not session_id:
            self.write({"status": "not_authenticated"})
            return
        
        session_id = session_id.decode()
        
        if session_id in FORCED_LOGOUT_SESSIONS:
            self.write({"status": "forced_logout"})
            return
        
        session_data = get_session(session_id)
        if session_data:
            username = session_data["user"]
            user_timeout = USERS.get(username, {}).get("session_timeout", 60)
            
            if user_timeout > 0:
                timeout_seconds = user_timeout * 60
                time_remaining = timeout_seconds - (time.time() - session_data["created"])
                
                if 0 < time_remaining <= 60:
                    self.write({
                        "status": "session_expiring_soon", 
                        "time_remaining": int(time_remaining),
                        "user": username
                    })
                    return
                elif time_remaining <= 0:
                    if username in USERS:
                        USERS[username]["instances"] = max(0, USERS[username]["instances"] - 1)
                    del sessions[session_id.decode()]
                    self.clear_cookie("session_id")
                    self.render("session_expired.html", about_modal="")
                    return
            
            self.write({
                "status": "authenticated", 
                "user": username, 
                "time_remaining": int(time_remaining) if user_timeout > 0 else None
            })
        else:
            self.write({"status": "not_authenticated"})

class SessionRefreshHandler(BaseHandler):
    def post(self):
        session_id = self.get_secure_cookie("session_id")
        
        if not session_id:
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = session_id.decode()
        session_data = get_session(session_id)
        
        if session_data:
            session_data["created"] = time.time()
            
            username = session_data["user"]
            if username in USERS:
                user_timeout = USERS[username].get("session_timeout", 60)
                if user_timeout > 0:
                    session_data["timeout_seconds"] = user_timeout * 60
            
            log.info(f"Session refreshed for user {username}")
            self.write({"success": True, "message": "Session extended by 60 minutes"})
        else:
            self.write({"success": False, "error": "Session not found"})

# === ADMIN AUTH HANDLERS - CORECTATE ===
class AdminLoginHandler(BaseHandler):
    def get_client_ip(self):
        real_ip = self.request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
        forwarded_for = self.request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return self.request.remote_ip

    def get(self):
        # Verifică dacă e deja autentificat
        if is_admin_authenticated(self):
            self.redirect("/admin/")
            return
        
        client_ip = self.get_client_ip()
        
        if RateLimiter.is_blocked(client_ip):
            self.write("""
            <div style="text-align: center; margin-top: 100px;">
                <h2 style="color: #dc3545;">Too Many Failed Attempts</h2>
                <p>Your IP has been temporarily blocked due to too many failed login attempts.</p>
                <p>Please try again later.</p>
                <a href="/admin/login">Try Again</a>
            </div>
            """)
            return
        
        # Afișează pagina de login
        self.render("admin_login.html", error="")

    def post(self):
        client_ip = self.get_client_ip()
        
        if RateLimiter.is_blocked(client_ip):
            self.set_status(429)
            self.write({"error": "Too many attempts"})
            return
        
        password = self.get_argument("password", "")
        
        # Verifică parola
        if check_password(ADMIN_CONFIG["password"], password):
            session_id = create_admin_session()
            self.set_secure_cookie("admin_session_id", session_id, expires_days=1)
            
            RateLimiter.clear_attempts(client_ip)
            
            log.info(f"Admin logged in successfully from IP {client_ip}")
            self.redirect("/admin/")
        else:
            RateLimiter.record_failed_attempt(client_ip)
            log.warning(f"Failed admin login attempt from IP {client_ip}")
            
            # Reafișează pagina cu eroare
            self.render("admin_login.html", error="Invalid admin password!")

class AdminLogoutHandler(BaseHandler):
    def get(self):
        session_id = self.get_secure_cookie("admin_session_id")
        
        if session_id:
            session_id = session_id.decode()
            if session_id in admin_sessions:
                del admin_sessions[session_id]
                log.info("Admin logged out")
        
        self.clear_cookie("admin_session_id")
        self.redirect("/admin/login")

# === HANDLERE PENTRU ADMIN INTERFACE ===
class AdminHandler(BaseHandler):
    def get(self):
        if not is_admin_authenticated(self):
            self.redirect("/admin/login")
            return
        self.render("admin.html")

class AdminStatusHandler(BaseHandler):
    def get(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"error": "Not authenticated"})
            return
            
        self.set_header("Content-Type", "application/json")
        
        user_status = {}
        for username, user_data in USERS.items():
            user_status[username] = {
                "instances": user_data["instances"],
                "max_instances": user_data["max_instances"],
                "session_timeout": user_data.get("session_timeout", 60),
                "comfy_url": user_data["comfy_url"],
                "ready": comfy_instances_ready.get(user_data["comfy_url"], False),
                "enabled": user_data.get("enabled", True),
                "nginx_auth": user_data.get("nginx_auth", {"enabled": False})
            }
        
        self.write({
            "users": user_status,
            "total_sessions": len(sessions),
            "global_nginx_auth": GLOBAL_NGINX_AUTH
        })

class AdminSessionsHandler(BaseHandler):
    def get(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"error": "Not authenticated"})
            return
            
        self.set_header("Content-Type", "application/json")
        
        session_list = []
        for session_id, session_data in sessions.items():
            username = session_data["user"]
            user_timeout = USERS.get(username, {}).get("session_timeout", 60)
            
            if user_timeout == 0:
                remaining_text = "Infinite session"
            else:
                timeout_seconds = user_timeout * 60
                remaining = timeout_seconds - (time.time() - session_data["created"])
                if remaining <= 0:
                    remaining_text = "Expired"
                else:
                    hours = int(remaining // 3600)
                    minutes = int((remaining % 3600) // 60)
                    seconds = int(remaining % 60)
                    remaining_text = f"Expires in: {hours}h {minutes}m {seconds}s"
            
            session_list.append({
                "session_id": session_id,
                "user": session_data["user"],
                "comfy_url": session_data["comfy_url"],
                "created": datetime.fromtimestamp(session_data["created"]).strftime("%Y-%m-%d %H:%M:%S"),
                "remaining_time": remaining_text
            })
        
        self.write({"sessions": session_list})
    
    def delete(self, session_id):
        if not is_admin_authenticated(self):
            self.set_status(401)
            return
            
        if session_id in sessions:
            username = sessions[session_id]["user"]
            if username in USERS:
                USERS[username]["instances"] = max(0, USERS[username]["instances"] - 1)
            
            FORCED_LOGOUT_SESSIONS.add(session_id)
            
            BLOCKED_USERS[username] = time.time() + 300
            del sessions[session_id]
            log.info(f"Admin forced logout for session {session_id}, user {username} blocked for 5 minutes")
        
        self.set_status(204)

class AdminUsersHandler(BaseHandler):
    def get(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"error": "Not authenticated"})
            return
            
        self.set_header("Content-Type", "application/json")
        self.write(USERS)
    
    def post(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            return
            
        data = json.loads(self.request.body)
        username = data.get("username")
        
        if username:
            hashed_password = hash_password(data.get("password", "comfy.123"))
            
            USERS[username] = {
                "password": hashed_password,
                "comfy_url": data.get("comfy_url", f"http://127.0.0.1:8189"),
                "instances": 0,
                "max_instances": data.get("max_instances", 2),
                "session_timeout": data.get("session_timeout", 60),
                "enabled": data.get("enabled", True),
                "nginx_auth": data.get("nginx_auth", {
                    "enabled": False,
                    "username": "",
                    "password": ""
                })
            }
            
            comfy_url = USERS[username]["comfy_url"]
            if comfy_url not in comfy_instances_ready:
                comfy_instances_ready[comfy_url] = False
                threading.Thread(target=check_single_instance, args=(comfy_url, USERS[username]["nginx_auth"]), daemon=True).start()
            
            log.info(f"Admin added user: {username}")
            
            save_config()
            
            self.set_status(201)
        else:
            self.set_status(400)
    
    def put(self, username):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
            
        if username not in USERS:
            self.set_status(404)
            self.write({"success": False, "error": "User not found"})
            return
            
        try:
            data = json.loads(self.request.body)
        except json.JSONDecodeError as e:
            self.set_status(400)
            self.write({"success": False, "error": f"Invalid JSON data: {str(e)}"})
            return
        
        new_username = data.get("username")
        
        if not new_username:
            self.set_status(400)
            self.write({"success": False, "error": "Username is required"})
            return
        
        if new_username != username and new_username in USERS:
            self.set_status(400)
            self.write({"success": False, "error": "Username already exists"})
            return
        
        try:
            instances = USERS[username]["instances"]
            
            user_data = USERS[username]
            user_data["comfy_url"] = data.get("comfy_url", user_data["comfy_url"])
            user_data["max_instances"] = data.get("max_instances", user_data["max_instances"])
            user_data["session_timeout"] = data.get("session_timeout", user_data["session_timeout"])
            user_data["enabled"] = data.get("enabled", user_data.get("enabled", True))
            
            if "nginx_auth" in data:
                user_data["nginx_auth"] = data["nginx_auth"]
            
            new_password = data.get("password")
            if new_password:
                user_data["password"] = hash_password(new_password)
            
            if new_username != username:
                USERS[new_username] = USERS.pop(username)
                for session_id, session_data in sessions.items():
                    if session_data["user"] == username:
                        session_data["user"] = new_username
                        session_data["comfy_url"] = user_data["comfy_url"]
            
            comfy_url = user_data["comfy_url"]
            if comfy_url not in comfy_instances_ready:
                comfy_instances_ready[comfy_url] = False
                threading.Thread(target=check_single_instance, args=(comfy_url, user_data["nginx_auth"]), daemon=True).start()
            
            log.info(f"Admin updated user: {username} -> {new_username}")
            
            save_config()
            
            self.write({"success": True, "message": "User updated successfully"})
            
        except Exception as e:
            log.error(f"Error updating user {username}: {e}")
            self.set_status(500)
            self.write({"success": False, "error": f"Internal server error: {str(e)}"})
    
    def delete(self, username):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
            
        if username not in USERS:
            self.set_status(404)
            self.write({"success": False, "error": "User not found"})
            return
        
        try:
            if USERS[username]["instances"] > 0:
                sessions_to_delete = []
                for session_id, session_data in sessions.items():
                    if session_data["user"] == username:
                        sessions_to_delete.append(session_id)
                        FORCED_LOGOUT_SESSIONS.add(session_id)
                
                for session_id in sessions_to_delete:
                    del sessions[session_id]
            
            del USERS[username]
            
            save_config()
            
            log.info(f"Admin deleted user: {username}")
            self.write({"success": True, "message": f"User {username} deleted successfully"})
            
        except Exception as e:
            log.error(f"Error deleting user {username}: {e}")
            self.set_status(500)
            self.write({"success": False, "error": f"Internal server error: {str(e)}"})

# === ADMIN NGINX AUTH HANDLERS ===
class AdminNginxAuthGlobalHandler(BaseHandler):
    def get(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"error": "Not authenticated"})
            return
        
        self.set_header("Content-Type", "application/json")
        self.write(GLOBAL_NGINX_AUTH)
    
    def post(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        try:
            data = json.loads(self.request.body)
        except json.JSONDecodeError as e:
            self.set_status(400)
            self.write({"success": False, "error": f"Invalid JSON data: {str(e)}"})
            return
        
        GLOBAL_NGINX_AUTH["enabled"] = data.get("enabled", False)
        GLOBAL_NGINX_AUTH["username"] = data.get("username", "")
        GLOBAL_NGINX_AUTH["password"] = data.get("password", "")
        
        save_config()
        
        log.info(f"Admin updated global nginx auth settings: enabled={GLOBAL_NGINX_AUTH['enabled']}")
        self.write({"success": True, "message": "Global nginx auth settings updated"})

class AdminNginxAuthUserHandler(BaseHandler):
    def get(self, username):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"error": "Not authenticated"})
            return
        
        if username not in USERS:
            self.set_status(404)
            self.write({"error": "User not found"})
            return
        
        self.set_header("Content-Type", "application/json")
        self.write(USERS[username].get("nginx_auth", {"enabled": False, "username": "", "password": ""}))
    
    def post(self, username):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        if username not in USERS:
            self.set_status(404)
            self.write({"success": False, "error": "User not found"})
            return
        
        try:
            data = json.loads(self.request.body)
        except json.JSONDecodeError as e:
            self.set_status(400)
            self.write({"success": False, "error": f"Invalid JSON data: {str(e)}"})
            return
        
        if "nginx_auth" not in USERS[username]:
            USERS[username]["nginx_auth"] = {}
        
        USERS[username]["nginx_auth"]["enabled"] = data.get("enabled", False)
        USERS[username]["nginx_auth"]["username"] = data.get("username", "")
        USERS[username]["nginx_auth"]["password"] = data.get("password", "")
        
        save_config()
        
        log.info(f"Admin updated nginx auth for user {username}: enabled={USERS[username]['nginx_auth']['enabled']}")
        self.write({"success": True, "message": f"Nginx auth settings updated for user {username}"})

# === ADMIN WORKFLOW SETTINGS ===
class AdminWorkflowSettingsHandler(BaseHandler):
    def get(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"error": "Not authenticated"})
            return
        
        self.set_header("Content-Type", "application/json")
        self.write({
            "workflow_root": WORKFLOW_ROOT_DIR,
            "current_directory": WORKFLOW_ROOT_DIR,
            "directory_exists": os.path.exists(WORKFLOW_ROOT_DIR)
        })
    
    def post(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        try:
            data = json.loads(self.request.body)
        except json.JSONDecodeError as e:
            self.set_status(400)
            self.write({"success": False, "error": f"Invalid JSON data: {str(e)}"})
            return
        
        new_root = data.get("workflow_root", "").strip()
        
        if not new_root:
            self.write({"success": False, "error": "Workflow root path is required"})
            return
        
        global WORKFLOW_ROOT_DIR
        WORKFLOW_ROOT_DIR = new_root
        
        if "workflow_root" not in config:
            config["workflow_root"] = WORKFLOW_ROOT_DIR
        else:
            config["workflow_root"] = WORKFLOW_ROOT_DIR
        
        save_config()
        
        log.info(f"Admin changed workflow root directory to: {WORKFLOW_ROOT_DIR}")
        self.write({
            "success": True, 
            "message": "Workflow root directory updated successfully",
            "workflow_root": WORKFLOW_ROOT_DIR,
            "directory_exists": os.path.exists(WORKFLOW_ROOT_DIR)
        })

# === ADMIN CHAT HANDLERS ===
class AdminChatUsersHandler(BaseHandler):
    def get(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"error": "Not authenticated"})
            return
            
        self.set_header("Content-Type", "application/json")
        
        users = []
        for username in USERS.keys():
            unread_count = 0
            if username in CHAT_MESSAGES:
                unread_count = sum(1 for msg in CHAT_MESSAGES[username] if msg["from"] == "user" and not msg["read"])
            
            users.append({
                "username": username,
                "unread_count": unread_count
            })
        
        self.write({"users": users})

class AdminChatMessagesHandler(BaseHandler):
    def get(self, username):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"error": "Not authenticated"})
            return
            
        self.set_header("Content-Type", "application/json")
        
        if username not in CHAT_MESSAGES:
            CHAT_MESSAGES[username] = []
        
        self.write({"messages": CHAT_MESSAGES[username]})

class AdminChatSendHandler(BaseHandler):
    def post(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"error": "Not authenticated"})
            return
            
        data = json.loads(self.request.body)
        to_user = data.get("to_user")
        message = data.get("message", "").strip()
        
        if not to_user or not message:
            self.write({"success": False, "error": "User and message are required"})
            return
        
        if to_user not in USERS:
            self.write({"success": False, "error": "User not found"})
            return
        
        if to_user not in CHAT_MESSAGES:
            CHAT_MESSAGES[to_user] = []
        
        message_data = {
            "from": "admin",
            "message": message,
            "timestamp": time.time(),
            "read": False
        }
        CHAT_MESSAGES[to_user].append(message_data)
        
        if to_user in USER_CHAT_WEBSOCKETS:
            for ws in USER_CHAT_WEBSOCKETS[to_user]:
                try:
                    ws.write_message(json.dumps({
                        "type": "new_message",
                        "from": "admin",
                        "message": message,
                        "timestamp": time.time()
                    }))
                except:
                    pass
        
        self.write({"success": True})

class AdminChatUploadFileHandler(BaseHandler):
    def post(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        to_user = self.get_argument("to_user", "").strip()
        message = self.get_argument("message", "").strip()
        
        if not to_user:
            self.write({"success": False, "error": "User is required"})
            return
        
        if to_user not in USERS:
            self.write({"success": False, "error": "User not found"})
            return
        
        if to_user not in CHAT_MESSAGES:
            CHAT_MESSAGES[to_user] = []
        
        file_data_list = []
        
        for field_name, files in self.request.files.items():
            if field_name.startswith('file'):
                for file_info in files:
                    filename = file_info['filename']
                    file_body = file_info['body']
                    content_type = file_info['content_type']
                    
                    file_id = save_chat_file(filename, file_body, content_type, "admin")
                    
                    file_data_list.append({
                        "id": file_id,
                        "filename": filename,
                        "size": len(file_body),
                        "content_type": content_type
                    })
        
        if not message:
            if len(file_data_list) == 1:
                message = f"Sent file: {file_data_list[0]['filename']}"
            else:
                message = f"Sent {len(file_data_list)} files"
        
        message_data = {
            "from": "admin",
            "message": message,
            "timestamp": time.time(),
            "read": False,
            "message_type": "file" if file_data_list else "text",
            "file_data": file_data_list[0] if len(file_data_list) == 1 else None
        }
        CHAT_MESSAGES[to_user].append(message_data)
        
        if to_user in USER_CHAT_WEBSOCKETS:
            for ws in USER_CHAT_WEBSOCKETS[to_user]:
                try:
                    ws.write_message(json.dumps({
                        "type": "new_message",
                        "from": "admin",
                        "message": message,
                        "timestamp": time.time(),
                        "message_type": "file" if file_data_list else "text",
                        "file_data": file_data_list[0] if len(file_data_list) == 1 else None
                    }))
                except:
                    pass
        
        self.write({"success": True})

class AdminChatMarkReadHandler(BaseHandler):
    def post(self, username):
        if not is_admin_authenticated(self):
            self.set_status(401)
            return
            
        if username in CHAT_MESSAGES:
            for msg in CHAT_MESSAGES[username]:
                if msg["from"] == "user":
                    msg["read"] = True
        
        self.set_status(200)

class AdminChatWebSocketHandler(tornado.websocket.WebSocketHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    def check_origin(self, origin):
        return True
    
    def open(self):
        if not is_admin_authenticated(self):
            self.close()
            return
        
        ADMIN_CHAT_WEBSOCKETS.add(self)
    
    def on_message(self, message):
        try:
            data = json.loads(message)
            
            if data.get("type") == "admin_send_message":
                to_user = data.get("to_user")
                message_text = data.get("message", "").strip()
                
                if to_user and message_text:
                    if to_user not in CHAT_MESSAGES:
                        CHAT_MESSAGES[to_user] = []
                    
                    message_data = {
                        "from": "admin",
                        "message": message_text,
                        "timestamp": time.time(),
                        "read": False
                    }
                    CHAT_MESSAGES[to_user].append(message_data)
                    
                    if to_user in USER_CHAT_WEBSOCKETS:
                        for ws in USER_CHAT_WEBSOCKETS[to_user]:
                            try:
                                ws.write_message(json.dumps({
                                    "type": "new_message",
                                    "from": "admin",
                                    "message": message_text,
                                    "timestamp": time.time()
                                }))
                            except:
                                pass
                    
                    self.write_message(json.dumps({
                        "type": "message_sent",
                        "success": True
                    }))
            
            elif data.get("type") == "user_typing":
                username = data.get("username")
                typing = data.get("typing", False)
                
                if username:
                    USER_TYPING_STATUS[username] = {
                        "typing": typing,
                        "last_typing_time": time.time()
                    }
        
        except Exception as e:
            log.error(f"Error in admin chat WebSocket: {e}")
    
    def on_close(self):
        if self in ADMIN_CHAT_WEBSOCKETS:
            ADMIN_CHAT_WEBSOCKETS.remove(self)

# === SECURITY HANDLERS ===
class AdminSecurityHandler(BaseHandler):
    def get(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"error": "Not authenticated"})
            return
            
        self.set_header("Content-Type", "application/json")
        self.write(ADMIN_CONFIG)
    
    def post(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"error": "Not authenticated"})
            return
            
        ADMIN_CONFIG["enabled"] = not ADMIN_CONFIG["enabled"]
        log.info(f"Admin security toggled to: {ADMIN_CONFIG['enabled']}")
        
        save_config()
        
        self.write(ADMIN_CONFIG)

class AdminPasswordHandler(BaseHandler):
    def post(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"error": "Not authenticated"})
            return
            
        data = json.loads(self.request.body)
        current_password = data.get("current_password")
        new_password = data.get("new_password")
        
        if not check_password(ADMIN_CONFIG["password"], current_password):
            self.write({"success": False, "error": "Current password is incorrect"})
            return
        
        if len(new_password) < 3:
            self.write({"success": False, "error": "New password must be at least 3 characters"})
            return
        
        ADMIN_CONFIG["password"] = hash_password(new_password)
        log.info("Admin password changed successfully")
        
        save_config()
        
        self.write({"success": True})

# === BLOCKED USERS HANDLER ===
class AdminBlockedUsersHandler(BaseHandler):
    def get(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"error": "Not authenticated"})
            return
            
        self.set_header("Content-Type", "application/json")
        
        cleanup_blocked_users()
        
        blocked_users = {}
        current_time = time.time()
        for username, expiry_time in BLOCKED_USERS.items():
            if expiry_time > current_time:
                remaining = int(expiry_time - current_time)
                minutes = remaining // 60
                seconds = remaining % 60
                blocked_users[username] = {
                    "remaining_time": f"{minutes}:{seconds:02d}",
                    "remaining_seconds": remaining
                }
        
        self.write({"blocked_users": blocked_users})
    
    def delete(self, username):
        if not is_admin_authenticated(self):
            self.set_status(401)
            return
            
        if username in BLOCKED_USERS:
            del BLOCKED_USERS[username]
            log.info(f"Admin removed restriction for user: {username}")
        
        self.set_status(204)

# === PROXY HANDLER COMPLET CU RESCRIERE URL ===
class MultiInstanceProxyHandler(BaseHandler):
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "x-requested-with, content-type, authorization, x-forwarded-for, x-real-ip, x-forwarded-proto, x-forwarded-host, cookie")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS, PATCH, HEAD")
        self.set_header("Access-Control-Allow-Credentials", "true")
    
    async def options(self, path=None):
        self.set_status(204)
        self.finish()
    
    async def get(self, path=None):
        await self._proxy_request("GET", path or "")
    
    async def post(self, path=None):
        await self._proxy_request("POST", path or "")
    
    async def put(self, path=None):
        await self._proxy_request("PUT", path or "")
    
    async def delete(self, path=None):
        await self._proxy_request("DELETE", path or "")
    
    async def head(self, path=None):
        await self._proxy_request("HEAD", path or "")
    
    async def patch(self, path=None):
        await self._proxy_request("PATCH", path or "")
    
    def get_user_comfy_url(self):
        session_id = self.get_secure_cookie("session_id")
        if not session_id:
            return None
        
        session_data = get_session(session_id.decode())
        return session_data.get("comfy_url") if session_data else None
    
    def _get_port_from_host(self):
        """Extrage portul din header-ul Host"""
        host = self.request.headers.get("Host", "")
        if ":" in host:
            try:
                return int(host.split(":")[1])
            except:
                pass
        # Port implicit pentru protocol
        if self.request.protocol == "https":
            return 443
        return 80
    
    def _rewrite_urls_in_html(self, html_content, comfy_url, proxy_base_url):
        """Rescrie toate URL-urile din HTML să pointeze către proxy"""
        
        # Parsează URL-urile ComfyUI pentru a extrage host-ul și portul
        parsed_comfy = urlparse(comfy_url)
        comfy_host = parsed_comfy.netloc
        comfy_scheme = parsed_comfy.scheme
        
        # Proxy base URL
        parsed_proxy = urlparse(proxy_base_url)
        proxy_host = parsed_proxy.netloc
        proxy_scheme = parsed_proxy.scheme
        
        # 1. Rescrie URL-uri absolute care încep cu http://comfy_host sau https://comfy_host
        patterns = [
            (fr'https?://{re.escape(comfy_host)}', f'{proxy_scheme}://{proxy_host}/comfy'),
            (fr'wss?://{re.escape(comfy_host)}', f'ws{"s" if proxy_scheme=="https" else ""}://{proxy_host}/comfy'),
        ]
        
        for pattern, replacement in patterns:
            html_content = re.sub(pattern, replacement, html_content, flags=re.IGNORECASE)
        
        # 2. Rescrie URL-uri relative care încep cu / (dar nu /static/)
        def replace_relative_url(match):
            full_match = match.group(0)
            quote_char = match.group(1)
            url = match.group(2)
            end_quote = match.group(3)
            
            if not url:
                return full_match
            
            # Nu modifica URL-urile care sunt deja pentru proxy
            if url.startswith('/comfy/'):
                return full_match
            
            # Nu modifica URL-urile pentru resursele noastre
            if url.startswith('/static/') or url.startswith('/css/') or url.startswith('/js/'):
                return full_match
            
            # Nu modifica URL-urile care sunt deja absolute
            if url.startswith('http://') or url.startswith('https://') or url.startswith('ws://') or url.startswith('wss://'):
                return full_match
            
            # Rescrie URL-ul relativ să pointeze către /comfy/
            if url.startswith('/'):
                new_url = f'/comfy{url}'
            else:
                new_url = f'/comfy/{url}'
            
            return f'{quote_char}{new_url}{end_quote}'
        
        # Găsește toate URL-urile în atributele src, href, action, etc.
        html_content = re.sub(
            r'(src=["\'])([^"\']*)(["\'])',
            replace_relative_url,
            html_content
        )
        html_content = re.sub(
            r'(href=["\'])([^"\']*)(["\'])',
            replace_relative_url,
            html_content
        )
        html_content = re.sub(
            r'(action=["\'])([^"\']*)(["\'])',
            replace_relative_url,
            html_content
        )
        
        # 3. Rescrie URL-uri WebSocket în JavaScript
        def replace_ws_url(match):
            full_match = match.group(0)
            ws_url = match.group(1)
            
            parsed_ws = urlparse(ws_url)
            if parsed_ws.netloc == comfy_host:
                new_ws_url = f'ws{"s" if proxy_scheme=="https" else ""}://{proxy_host}/comfy{parsed_ws.path}'
                if parsed_ws.query:
                    new_ws_url += '?' + parsed_ws.query
                return full_match.replace(ws_url, new_ws_url)
            return full_match
        
        # Găsește URL-uri WebSocket în JavaScript
        html_content = re.sub(
            r'["\'](wss?://[^"\']+)["\']',
            replace_ws_url,
            html_content
        )
        
        return html_content
    
    async def _proxy_request(self, method, path):
        session_id = self.get_secure_cookie("session_id")
        
        # Verifică session și forced logout
        if session_id:
            session_id_str = session_id.decode()
            if session_id_str in FORCED_LOGOUT_SESSIONS:
                self.set_header("Content-Type", "text/html")
                self.write("""
                <html>
                <body>
                <script>
                if (typeof openForcedLogoutModal === 'function') {
                    openForcedLogoutModal();
                } else {
                    window.location.href = '/login';
                }
                </script>
                </body>
                </html>""")
                return
        
        # Verifică expirarea sesiunii
        if session_id:
            session_data = get_session(session_id.decode())
            if session_data:
                username = session_data["user"]
                user_timeout = USERS.get(username, {}).get("session_timeout", 60)
                
                if user_timeout > 0:
                    timeout_seconds = user_timeout * 60
                    if time.time() - session_data["created"] > timeout_seconds:
                        if username in USERS:
                            USERS[username]["instances"] = max(0, USERS[username]["instances"] - 1)
                        del sessions[session_id.decode()]
                        self.clear_cookie("session_id")
                        self.render("session_expired.html", about_modal="")
                        return
        
        # Verifică autentificarea
        if not is_authenticated(self):
            if path and path.startswith(('api/', 'view/', 'upload/', 'websocket')):
                self.set_status(401)
                self.write({"error": "Not authenticated"})
                return
            elif not path or not path.startswith(('assets/', 'static/', 'favicon.', 'login', 'logout')):
                self.redirect("/login")
                return
        
        # Obține URL-ul ComfyUI
        comfy_url = self.get_user_comfy_url()
        if not comfy_url:
            self.redirect("/login")
            return
            
        # Verifică dacă instanța e gata
        if not comfy_instances_ready.get(comfy_url, False) and not (path and path.startswith(('login', 'logout', 'health', 'waiting'))):
            self.render("waiting.html", about_modal="")
            return

        # Decodifică path-ul
        try:
            if path:
                path = unquote(path)
                path = path.replace('%2F', '/')
                path = path.replace('%20', ' ')
        except Exception as e:
            log.warning(f"Error decoding path {path}: {e}")

        # Construiește URL-ul țintă
        comfy_url = comfy_url.rstrip('/')
        
        # Dacă path-ul începe cu 'comfy/', elimină prefixul
        if path and path.startswith('comfy/'):
            path = path[6:]  # Elimină 'comfy/'
        
        target_url = f"{comfy_url}/{path.lstrip('/')}" if path else comfy_url
        if self.request.query:
            target_url += "?" + self.request.query
        
        session_data = get_session(self.get_secure_cookie("session_id").decode())
        username = session_data["user"] if session_data else "unknown"
        
        log.info(f"Proxying {method} {path} for user {username} to {target_url}")
        
        # Pregătește clientul HTTP
        client = tornado.httpclient.AsyncHTTPClient()
        
        try:
            # Pregătește headerele
            headers = {}
            
            # Exclude headere care nu trebuie propagate
            exclude_headers = ['host', 'content-length', 'connection', 'keep-alive', 
                             'accept-encoding', 'content-encoding', 'transfer-encoding']
            
            for header_name, header_value in self.request.headers.items():
                if header_name.lower() not in exclude_headers:
                    headers[header_name] = header_value
            
            # Obține portul corect
            port = self._get_port_from_host()
            
            # Adaugă headere pentru a păstra informațiile originale
            headers['X-Forwarded-For'] = self.request.remote_ip
            headers['X-Forwarded-Host'] = self.request.host
            headers['X-Forwarded-Proto'] = self.request.protocol
            headers['X-Forwarded-Port'] = str(port)
            headers['X-Real-IP'] = self.request.remote_ip
            headers['X-Original-URI'] = self.request.uri
            
            # Adaugă cookie-ul de sesiune în headere
            if session_id:
                headers['X-User-ID'] = username
                headers['X-Session-ID'] = session_id.decode()
                headers['Cookie'] = f'session_id={session_id.decode()}'
            
            # Adaugă autentificare nginx dacă este necesară
            add_nginx_auth_headers(headers, username)
            
            # Pregătește body-ul cererii
            body = None
            if method in ["POST", "PUT", "DELETE", "PATCH"] and self.request.body:
                body = self.request.body
            
            # Pentru cereri mari, folosește streaming
            streaming_callback = None
            content_length = int(self.request.headers.get('Content-Length', 0))
            if content_length > 10 * 1024 * 1024:  # > 10MB
                streaming_callback = self._stream_response
            
            # Creează cererea
            req = tornado.httpclient.HTTPRequest(
                url=target_url,
                method=method,
                headers=headers,
                body=body,
                follow_redirects=False,
                connect_timeout=30,
                request_timeout=300,  # 5 minute pentru fișiere mari
                validate_cert=False,
                decompress_response=False,
                allow_nonstandard_methods=True,
                streaming_callback=streaming_callback
            )
            
            # Execută cererea
            response = await client.fetch(req, raise_error=False)
            
            # Setează status code
            self.set_status(response.code)
            
            # Copiază headerele de răspuns
            for header, value in response.headers.get_all():
                header_lower = header.lower()
                if header_lower not in ['content-length', 'content-encoding', 
                                      'transfer-encoding', 'connection', 
                                      'keep-alive', 'content-security-policy']:
                    # Pentru headerele de location, modifică URL-ul să pointeze către proxy
                    if header_lower == 'location':
                        location = value
                        if location.startswith(comfy_url):
                            location = location.replace(comfy_url, f"{self.request.protocol}://{self.request.host}/comfy")
                        self.set_header(header, location)
                    else:
                        self.set_header(header, value)
            
            # Setează headere CORS
            self.set_header("Access-Control-Allow-Origin", "*")
            self.set_header("Access-Control-Allow-Credentials", "true")
            
            # Dacă s-a folosit streaming, răspunsul a fost deja trimis
            if streaming_callback:
                return
            
            # Pentru răspunsuri HTML, rescrie URL-urile și injectează UI-ul
            content_type = response.headers.get('Content-Type', '').lower()
            is_html_response = 'text/html' in content_type and response.code == 200
            
            if is_html_response and response.body:
                try:
                    # Determină encoding-ul
                    encoding = 'utf-8'
                    if 'charset=' in content_type:
                        charset_match = re.search(r'charset=([\w-]+)', content_type, re.IGNORECASE)
                        if charset_match:
                            encoding = charset_match.group(1)
                    
                    html_content = response.body.decode(encoding, errors='replace')
                    
                    # Rescrie URL-urile în HTML
                    proxy_base_url = f"{self.request.protocol}://{self.request.host}"
                    html_content = self._rewrite_urls_in_html(html_content, comfy_url, proxy_base_url)
                    
                    # Injectează UI-ul nostru
                    html_content = self._inject_ui(html_content, username)
                    
                    self.write(html_content.encode(encoding))
                except Exception as e:
                    log.error(f"Error modifying HTML: {str(e)}", exc_info=True)
                    self.write(response.body)
            else:
                if response.code != 304 and response.body:
                    self.write(response.body)
                
        except tornado.httpclient.HTTPError as e:
            log.error(f"HTTP error for {target_url}: {str(e)}")
            if e.code == 599:  # Timeout
                self.set_status(504)
                self.write("Gateway Timeout - The upstream server took too long to respond")
            else:
                self.set_status(e.code or 502)
                self.write(f"Bad Gateway: {str(e)}")
        except Exception as e:
            log.error(f"Proxy error for {target_url}: {str(e)}", exc_info=True)
            self.set_status(502)
            self.write(f"Bad Gateway: {str(e)}")
    
    def _inject_ui(self, html_content, username):
        """Injectează UI-ul în răspunsurile HTML"""
        try:
            # Elimină CSP-ul care ar putea bloca resursele noastre
            html_content = re.sub(
                r'<meta[^>]*content-security-policy[^>]*>',
                '',
                html_content,
                flags=re.IGNORECASE
            )
            html_content = re.sub(
                r'<meta[^>]*http-equiv=["\']Content-Security-Policy["\'][^>]*>',
                '',
                html_content,
                flags=re.IGNORECASE
            )
            
            # Găsește poziția pentru injectare
            head_end_pos = html_content.find('</head>')
            
            if head_end_pos != -1:
                our_injection = f"""
                <link rel="stylesheet" type="text/css" href="/static/css/styles.css">
                <script src="/static/js/main.js"></script>
                
                <style id="comfy-auth-styles">
                .comfy-auth-overlay {{
                    position: fixed;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    z-index: 9998;
                    pointer-events: none;
                }}
                .comfy-user-info {{
                    position: fixed;
                    top: 0;
                    left: 10px;
                    color: white;
                    background: rgba(0,0,0,0.7);
                    padding: 2px 8px;
                    border-radius: 0 0 3px 3px;
                    z-index: 10001;
                    font-size: 11px;
                    text-align: center;
                    backdrop-filter: blur(5px);
                    height: 20px;
                    display: flex;
                    align-items: center;
                }}
                .server-title {{
                    position: fixed;
                    top: 0;
                    left: 50%;
                    transform: translateX(-50%);
                    color: white;
                    font-size: 14px;
                    font-weight: bold;
                    text-align: center;
                    z-index: 10001;
                    height: 25px;
                    display: flex;
                    align-items: center;
                }}
                .comfy-auth-buttons {{
                    position: fixed;
                    top: 0;
                    right: 0;
                    z-index: 10001;
                    display: flex;
                    gap: 0;
                    height: 25px;
                }}
                .comfy-about-btn {{
                    background: #007bff;
                    color: white;
                    border: none;
                    padding: 0;
                    border-radius: 0 0 0 3px;
                    cursor: pointer;
                    font-size: 12px;
                    width: 25px;
                    height: 25px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }}
                .comfy-settings-btn {{
                    background: #28a745;
                    color: white;
                    border: none;
                    padding: 0 6px;
                    border-radius: 0;
                    cursor: pointer;
                    font-size: 11px;
                    height: 25px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-width: 35px;
                }}
                .comfy-logout-btn {{
                    background: #dc3545;
                    color: white;
                    border: none;
                    padding: 0 8px;
                    border-radius: 0 0 3px 0;
                    cursor: pointer;
                    text-decoration: none;
                    font-size: 11px;
                    height: 25px;
                    display: flex;
                    align-items: center;
                }}
                .comfy-about-btn:hover {{ background: #0056b3; }}
                .comfy-settings-btn:hover {{ background: #218838; }}
                .comfy-logout-btn:hover {{ background: #c82333; }}
                </style>
                
                <script id="comfy-auth-init">
                document.addEventListener('DOMContentLoaded', function() {{
                    const overlay = document.createElement('div');
                    overlay.className = 'comfy-auth-overlay';
                    
                    const userInfo = document.createElement('div');
                    userInfo.className = 'comfy-user-info';
                    userInfo.textContent = 'Welcome, {username}';
                    
                    const serverTitle = document.createElement('div');
                    serverTitle.className = 'server-title';
                    serverTitle.textContent = 'PRO AI Server';
                    
                    const buttonsDiv = document.createElement('div');
                    buttonsDiv.className = 'comfy-auth-buttons';
                    buttonsDiv.innerHTML = `
                        <button class="comfy-about-btn" onclick="openAboutModal()">?</button>
                        <button class="comfy-settings-btn" onclick="openUserSettingsModal()" title="Settings">SET</button>
                        <a href="/logout" class="comfy-logout-btn">Logout</a>
                    `;
                    
                    document.body.appendChild(overlay);
                    document.body.appendChild(userInfo);
                    document.body.appendChild(serverTitle);
                    document.body.appendChild(buttonsDiv);
                    
                    console.log('ComfyUI Auth Server UI injected successfully');
                    
                    if (typeof initSessionMonitoring === 'function') {{
                        setTimeout(initSessionMonitoring, 1000);
                    }}
                    
                    if (typeof connectChatWebSocket === 'function') {{
                        setTimeout(connectChatWebSocket, 2000);
                    }}
                }});
                </script>
                """
                
                html_content = html_content[:head_end_pos] + our_injection + html_content[head_end_pos:]
            
            return html_content
            
        except Exception as e:
            log.error(f"Error in UI injection: {str(e)}", exc_info=True)
            return html_content
    
    async def _stream_response(self, chunk):
        """Streaming callback pentru răspunsuri mari"""
        try:
            self.write(chunk)
            await self.flush()
        except Exception as e:
            log.error(f"Error streaming response: {e}")

# === WEBSOCKET PROXY COMPLET - VERSIUNE CORECTATĂ ===
class MultiInstanceWebSocketProxy(tornado.websocket.WebSocketHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.comfy_ws = None
        self._running = True
        self.username = None
        self._ping_interval = None
    
    def check_origin(self, origin):
        return True
    
    def get_user_comfy_ws_url(self):
        session_id = self.get_secure_cookie("session_id")
        if not session_id:
            return None
        
        session_data = get_session(session_id.decode())
        if not session_data:
            return None
        
        comfy_url = session_data.get("comfy_url", "http://127.0.0.1:8188")
        # Construiește URL-ul WebSocket corect
        if comfy_url.startswith("https://"):
            base = comfy_url.replace("https://", "wss://")
        else:
            base = comfy_url.replace("http://", "ws://")
        
        # Adaugă /ws la sfârșit dacă nu există deja
        if not base.endswith('/ws'):
            if base.endswith('/'):
                return base + 'ws'
            else:
                return base + '/ws'
        return base
    
    async def open(self):
        if not is_authenticated(self):
            self.close(code=4001, reason="Not authenticated")
            return
        
        session_id = self.get_secure_cookie("session_id")
        if not session_id:
            self.close(code=4001, reason="Not authenticated")
            return
        
        session_data = get_session(session_id.decode())
        if not session_data:
            self.close(code=4002, reason="Session not found")
            return
        
        self.username = session_data["user"]
        
        comfy_ws_url = self.get_user_comfy_ws_url()
        if not comfy_ws_url:
            self.close(code=4002, reason="No ComfyUI instance")
            return
        
        log.info(f"WebSocket connecting for user {self.username} to {comfy_ws_url}")
        
        try:
            # Construim URL-ul cu parametrii în query string pentru a transmite sesiunea
            parsed_url = urlparse(comfy_ws_url)
            query_params = []
            if parsed_url.query:
                query_params.append(parsed_url.query)
            
            # Adăugăm session_id în query string
            if session_id:
                query_params.append(f"session_id={session_id.decode()}")
            
            # Reconstruim URL-ul cu parametrii
            new_query = "&".join(query_params) if query_params else ""
            comfy_ws_url_with_params = urlunparse((
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                parsed_url.params,
                new_query,
                parsed_url.fragment
            ))
            
            log.info(f"WebSocket connecting with session in URL: {comfy_ws_url_with_params}")
            
            # Conectează-te la WebSocket-ul destinație
            self.comfy_ws = await tornado.websocket.websocket_connect(
                comfy_ws_url_with_params,
                ping_interval=30,
                ping_timeout=10,
                connect_timeout=30,
                max_message_size=500 * 1024 * 1024  # 500MB pentru fișiere mari
            )
            
            log.info(f"WebSocket connected successfully for user {self.username}")
            
            # Pornește task-urile pentru pipe
            asyncio.create_task(self._pipe_comfy_to_client())
            asyncio.create_task(self._keep_alive())
            
        except Exception as e:
            log.error(f"WebSocket connection error for user {self.username}: {e}")
            self.close(code=500, reason=f"Connection failed: {str(e)}")
    
    async def _pipe_comfy_to_client(self):
        """Trimite mesajele de la ComfyUI către client"""
        try:
            while self._running and self.comfy_ws:
                try:
                    msg = await self.comfy_ws.read_message()
                    if msg is None:
                        log.info(f"WebSocket closed by server for user {self.username}")
                        break
                    
                    if self.ws_connection and not self.ws_connection.is_closing():
                        await self.write_message(msg, isinstance(msg, bytes))
                except tornado.websocket.WebSocketClosedError:
                    log.info(f"WebSocket connection closed for user {self.username}")
                    break
                except Exception as e:
                    log.error(f"WebSocket read error for user {self.username}: {e}")
                    break
        except Exception as e:
            log.error(f"WebSocket pipe error for user {self.username}: {e}")
        finally:
            self.close()
    
    async def on_message(self, message):
        """Primește mesaje de la client și le trimite către ComfyUI"""
        if self.comfy_ws and self._running:
            try:
                await self.comfy_ws.write_message(message, isinstance(message, bytes))
            except tornado.websocket.WebSocketClosedError:
                log.info(f"WebSocket closed while sending for user {self.username}")
                self.close()
            except Exception as e:
                log.error(f"WebSocket write error for user {self.username}: {e}")
                self.close()
    
    def on_close(self):
        """Când conexiunea se închide"""
        self._running = False
        if self.comfy_ws:
            try:
                self.comfy_ws.close()
            except:
                pass
        log.info(f"WebSocket closed for user {self.username}")
    
    async def _keep_alive(self):
        """Trimite ping-uri pentru a menține conexiunea vie"""
        while self._running:
            await asyncio.sleep(30)
            if self._running and self.comfy_ws:
                try:
                    await self.comfy_ws.write_message(b'', binary=True)
                except:
                    pass

# === SIMPLE PROXY FOR STATIC FILES ===
class StaticFileProxyHandler(BaseHandler):
    async def get(self, path):
        if not is_authenticated(self):
            self.set_status(401)
            return
        
        session_id = self.get_secure_cookie("session_id")
        session_data = get_session(session_id.decode())
        comfy_url = session_data.get("comfy_url") if session_data else None
        
        if not comfy_url:
            self.set_status(404)
            return
        
        target_url = f"{comfy_url}/{path}"
        
        client = tornado.httpclient.AsyncHTTPClient()
        try:
            headers = {}
            
            username = session_data["user"]
            add_nginx_auth_headers(headers, username)
            
            req = tornado.httpclient.HTTPRequest(
                url=target_url,
                method="GET",
                headers=headers,
                follow_redirects=False,
                connect_timeout=10,
                request_timeout=30,
                validate_cert=False
            )
            
            response = await client.fetch(req, raise_error=False)
            
            self.set_status(response.code)
            
            for header, value in response.headers.get_all():
                if header.lower() not in ['content-length', 'content-encoding', 
                                        'transfer-encoding', 'connection']:
                    self.set_header(header, value)
            
            if response.code != 304:
                self.write(response.body)
                
        except Exception as e:
            log.error(f"Static proxy error: {e}")
            self.set_status(500)

# === ROOT HANDLER ===
class RootHandler(BaseHandler):
    def get(self):
        if not is_authenticated(self):
            self.redirect("/login")
            return
        
        self.redirect("/comfy/")

# === HEALTH CHECK ===
class HealthHandler(BaseHandler):
    def get(self):
        self.set_header("Content-Type", "application/json")
        user_status = {}
        for username, data in USERS.items():
            user_status[username] = {
                "instances": data["instances"],
                "max_instances": data["max_instances"],
                "session_timeout": data.get("session_timeout", 60),
                "comfy_url": data["comfy_url"],
                "ready": comfy_instances_ready.get(data["comfy_url"], False),
                "enabled": data.get("enabled", True)
            }
        
        self.write({
            "status": "ok", 
            "sessions_active": len(sessions),
            "users": user_status,
            "global_nginx_auth_enabled": GLOBAL_NGINX_AUTH.get("enabled", False)
        })

# === CSS FIX HANDLER ===
class CSSFixHandler(BaseHandler):
    def get(self, path=None):
        self.set_header('Content-Type', 'text/css')
        self.set_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.set_header('Pragma', 'no-cache')
        self.set_header('Expires', '0')
        self.write("/* CSS placeholder - Auth Proxy Fix */\n")
        self.write("/* This file is served by ComfyUI Auth Proxy */")
        self.finish()

def make_auth_app():
    return tornado.web.Application([
        # Auth routes
        (r"/login", LoginHandler),
        (r"/logout", LogoutHandler),
        (r"/user-status", UserStatusHandler),
        (r"/user-settings", UserSettingsHandler),
        
        # Chat routes
        (r"/chat-messages", ChatMessagesHandler),
        (r"/send-message", SendMessageHandler),
        (r"/upload-chat-file", UploadChatFileHandler),
        (r"/download-file/(.*)", DownloadFileHandler),
        (r"/mark-messages-read", MarkMessagesReadHandler),
        (r"/unread-messages-count", UnreadMessagesCountHandler),
        (r"/chat-ws", ChatWebSocketHandler),
        
        # Session routes
        (r"/health", HealthHandler),
        (r"/check-session", SessionCheckHandler),
        (r"/refresh-session", SessionRefreshHandler),
        
        # Workflow routes
        (r"/api/workflows/list", WorkflowListHandler),
        (r"/api/workflows/load/(.*)", WorkflowLoadHandler),
        (r"/api/workflows/save", WorkflowSaveHandler),
        (r"/api/workflows/delete/(.*)", WorkflowDeleteHandler),
        
        # WebSocket proxy - rute explicite
        (r"/comfy/ws", MultiInstanceWebSocketProxy),
        (r"/ws", MultiInstanceWebSocketProxy),
        
        # Root handler
        (r"/", RootHandler),
        
        # CSS Fix for missing resources
        (r"/css/(.*)", CSSFixHandler),
        
        # Static file handler
        (r"/static/(.*)", tornado.web.StaticFileHandler, {'path': os.path.join(os.path.dirname(__file__), "static")}),
        
        # Main ComfyUI Proxy
        (r"/comfy/(.*)", MultiInstanceProxyHandler),
        
        # Catch-all for other ComfyUI resources
        (r"/(.*)", MultiInstanceProxyHandler),
    ], 
    template_path=os.path.join(os.path.dirname(__file__), "templates"),
    compress_response=False,
    autoreload=False,
    serve_traceback=False,
    cookie_secret=config["cookie_secret"],
    login_url="/login",
    websocket_ping_interval=30,
    websocket_ping_timeout=10
    )

def make_admin_app():
    return tornado.web.Application([
        (r"/admin/login", AdminLoginHandler),
        (r"/admin/logout", AdminLogoutHandler),
        (r"/admin/api/status", AdminStatusHandler),
        (r"/admin/api/sessions", AdminSessionsHandler),
        (r"/admin/api/sessions/(.*)", AdminSessionsHandler),
        (r"/admin/api/users", AdminUsersHandler),
        (r"/admin/api/users/(.*)", AdminUsersHandler),
        (r"/admin/api/blocked-users", AdminBlockedUsersHandler),
        (r"/admin/api/blocked-users/(.*)", AdminBlockedUsersHandler),
        (r"/admin/api/security", AdminSecurityHandler),
        (r"/admin/api/security/toggle", AdminSecurityHandler),
        (r"/admin/api/security/password", AdminPasswordHandler),
        (r"/admin/api/nginx-auth/global", AdminNginxAuthGlobalHandler),
        (r"/admin/api/nginx-auth/user/(.*)", AdminNginxAuthUserHandler),
        (r"/admin/api/workflow-settings", AdminWorkflowSettingsHandler),
        (r"/admin/api/chat/users", AdminChatUsersHandler),
        (r"/admin/api/chat/messages/(.*)", AdminChatMessagesHandler),
        (r"/admin/api/chat/send", AdminChatSendHandler),
        (r"/admin/api/chat/upload-file", AdminChatUploadFileHandler),
        (r"/admin/api/chat/mark-read/(.*)", AdminChatMarkReadHandler),
        (r"/admin/chat-ws", AdminChatWebSocketHandler),
        (r"/admin/?", AdminHandler),  # Ruta pentru /admin/
        (r"/admin", AdminHandler),    # Ruta pentru /admin fără slash
    ],
    template_path=os.path.join(os.path.dirname(__file__), "templates"),
    static_path=os.path.join(os.path.dirname(__file__), "static"),
    compress_response=False,
    autoreload=False,
    serve_traceback=False,
    cookie_secret=config["cookie_secret"],
    login_url="/admin/login"
    )

if __name__ == "__main__":
    local_ip = get_local_ip()
    
    print("=== ComfyUI Multi-User Auth Server with Admin - PROXY MODE ===")
    print(f"Auth Server: http://0.0.0.0:{AUTH_PORT} (local)")
    print(f"Auth Server: http://{local_ip}:{AUTH_PORT} (network)")
    print(f"Admin Interface: http://0.0.0.0:{ADMIN_PORT}/admin/ (local)")
    print(f"Admin Interface: http://{local_ip}:{ADMIN_PORT}/admin/ (network)")
    print("Default Users: user1, user2")
    print("Password for all: comfy.123")
    print("Admin Password: admin123")
    print(f"Config file: {CONFIG_FILE}")
    print(f"Workflow Root Directory: {WORKFLOW_ROOT_DIR}")
    print("=== PROXY MODE ACTIVATED ===")
    print("All traffic now goes through the auth server")
    print("Users can access ComfyUI instances regardless of network location")
    print("===========================================================")

    logging.getLogger("tornado.access").setLevel(logging.WARNING)
    logging.getLogger("tornado.application").setLevel(logging.WARNING)
    logging.getLogger("tornado.general").setLevel(logging.WARNING)

    # Initializează și verifică instanțele
    initialize_instances()
    check_comfy_ready()

    # Pornește ambele servere
    auth_app = make_auth_app()
    admin_app = make_admin_app()
    
    try:
        auth_app.listen(AUTH_PORT, "0.0.0.0")
        admin_app.listen(ADMIN_PORT, "0.0.0.0")
        
        log.info(f"Auth server started on port {AUTH_PORT} (PROXY MODE)")
        log.info(f"Admin server started on port {ADMIN_PORT}")
        print(f"Auth server started on port {AUTH_PORT} (PROXY MODE)")
        print(f"Admin interface started on port {ADMIN_PORT}")
        print("Multi-user system active with full proxy support")
        print(f"Configuration will be saved to: {CONFIG_FILE}")
        print(f"Workflow directories will be created in: {WORKFLOW_ROOT_DIR}")
        
        loop = tornado.ioloop.IOLoop.current()
        loop.start()
        
    except Exception as e:
        log.error(f"Failed to start server: {e}")
        print(f"Failed to start server: {e}")
