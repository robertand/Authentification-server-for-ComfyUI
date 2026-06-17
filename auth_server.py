#!/usr/bin/env python3
"""
Auth Server for ComfyUI - ADMIN INTERFACE
Full Proxy - all traffic passes through the server with full URL rewriting
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
import sys
import bcrypt
import base64
from urllib.parse import quote, unquote, urlparse, urlunparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# === MODAL COMPONENTS ===
ABOUT_DRAWER_HTML = """
<div id="aboutDrawer" class="about-drawer">
    <div class="about-drawer-content">
        <h2>ADA Server v1.2.3h2>
        <div class="about-glass-card">
            <p>Advanced management and authentication system for multiple ComfyUI instances.</p>
            <p>All systems are operational. High-performance GPU nodes are active.</p>
        </div>
        <div style="margin-top: 30px; text-align: center; opacity: 0.7; font-size: 12px;">
            <p>Version 1.2.3 - Created for ADA AI teams</p>
        </div>
    </div>
</div>
"""

CHAT_UI_HTML = """
<button class="chat-btn" onclick="toggleChatModal()" id="chatButton">
    <span id="chatNotification" class="chat-notification"></span>
    CHAT
</button>
<div id="chatModal" class="chat-modal">
    <div class="chat-header">
        <h3>Communication Center</h3>
        <div style="display: flex; gap: 10px; align-items: center;">
            <select id="chatRecipient" class="chat-recipient-select" onchange="switchChatRecipient()">
                <option value="admin">Administrator</option>
            </select>
            <button class="chat-close-btn" onclick="closeChatModal()">&times;</button>
        </div>
    </div>
    <div class="chat-messages" id="chatMessages"></div>
    <div id="typingIndicator" class="chat-typing-indicator"></div>
    <div id="filePreview" class="file-preview"></div>
    <div class="chat-input-container">
        <div class="chat-input-row">
            <input type="text" id="chatInput" class="chat-input" placeholder="Type a message..." onkeypress="if(event.key==='Enter') sendChatMessage()">
            <button class="chat-send-btn" onclick="sendChatMessage()">Send</button>
        </div>
        <div style="margin-top: 8px;">
            <input type="file" id="chatFileInput" class="chat-file-input" multiple onchange="handleFileSelection()">
            <button class="chat-file-btn" onclick="document.getElementById('chatFileInput').click()">📎 Attach Files</button>
        </div>
    </div>
</div>
"""

USER_SETTINGS_MODAL_HTML = """
<div id="userSettingsModal" class="user-settings-modal">
    <div class="user-settings-modal-content">
        <h2>User Settings</h2>
        <form id="userSettingsForm">
            <div class="form-group">
                <label for="settingsUsername">Username:</label>
                <input type="text" id="settingsUsername" required>
            </div>
            <div class="form-group">
                <label for="settingsCurrentPassword">Current Password:</label>
                <input type="password" id="settingsCurrentPassword" required>
            </div>
            <div class="form-group">
                <label for="settingsNewPassword">New Password (optional):</label>
                <input type="password" id="settingsNewPassword">
            </div>
            <div class="form-group">
                <label for="settingsConfirmPassword">Confirm New Password:</label>
                <input type="password" id="settingsConfirmPassword">
            </div>
            <div id="userSettingsMessage" class="user-settings-message"></div>
            <div class="user-settings-modal-buttons">
                <button type="button" class="user-settings-btn cancel" onclick="closeUserSettingsModal()">Cancel</button>
                <button type="button" class="user-settings-btn" onclick="saveUserSettings()">Save Changes</button>
            </div>
        </form>
    </div>
</div>
"""

SESSION_MODALS_HTML = """
<div id="forcedLogoutModal" class="forced-logout-modal">
    <div class="forced-logout-modal-content">
        <h2>Session Closed</h2>
        <p>Your session has been terminated by the administrator.</p>
        <div class="forced-logout-info">The administrator has closed this work session.</div>
        <button class="forced-logout-btn" onclick="redirectToLogin()">OK</button>
    </div>
</div>

<div id="sessionExpiryModal" class="session-expiry-modal">
    <div class="session-expiry-modal-content">
        <h2>Session Expiring</h2>
        <p>Your session will expire in <span id="expiryCountdown">60</span> seconds due to inactivity.</p>
        <div class="session-expiry-info">Would you like to extend your session?</div>
        <div style="display: flex; gap: 10px; justify-content: center;">
            <button class="session-logout-btn" onclick="logoutNow()">Logout</button>
            <button class="session-expiry-btn" onclick="continueSession()">Extend Session</button>
        </div>
    </div>
</div>

<div id="concurrentUserModal" class="concurrent-user-modal">
    <div class="concurrent-user-modal-content">
        <h2>New User Connected</h2>
        <div class="concurrent-user-icon">&#128100;</div>
        <p id="concurrentUserMessage">Another user has connected to the server.</p>
        <div class="concurrent-user-info" id="concurrentUserInfo"></div>
        <button class="concurrent-user-btn" onclick="closeConcurrentUserModal()">OK</button>
    </div>
</div>
"""

WORKFLOW_BROWSER_HTML = """
<div id="workflowBrowserModal" class="wf-modal">
    <div class="wf-modal-content">
        <div class="wf-header">
            <h2><span class="wf-icon">📂</span> Workflow Manager</h2>
            <button class="wf-close-btn" onclick="closeWorkflowBrowser()">&times;</button>
        </div>
        <div class="wf-layout">
            <div class="wf-sidebar">
                <div class="wf-sidebar-header">
                    <span>Folders</span>
                    <button class="wf-sidebar-btn" onclick="wfNewFolder()" title="New Folder">+</button>
                </div>
                <div class="wf-tree" id="wfTree"></div>
            </div>
            <div class="wf-main">
                <div class="wf-breadcrumb" id="wfBreadcrumb"></div>
                <div class="wf-toolbar">
                    <span class="wf-current-path" id="wfCurrentPath">/</span>
                    <button class="wf-refresh-btn" onclick="loadWorkflowList()">&#x21bb;</button>
                </div>
                <div class="wf-list" id="workflowList">
                    <div class="wf-empty">No saved workflows</div>
                </div>
                <div class="wf-save-section">
                    <div class="wf-save-row">
                        <input type="text" id="workflowSaveName" placeholder="Workflow name..." class="wf-input">
                        <button class="wf-btn wf-btn-primary" onclick="saveCurrentWorkflow()">Save</button>
                    </div>
                    <div id="workflowMessage" class="wf-message"></div>
                </div>
            </div>
        </div>
    </div>
</div>
"""

# === CONFIGURARE ===
CHUNK_SIZE = 64 * 1024  # 64KB chunks pentru streaming
MAX_BUFFER_SIZE = 1024 * 1024 * 1024  # 1GB pentru fișiere mari
MAX_CLIENTS = 100  # Număr maxim de conexiuni concurente

# === LOGGING ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("AUTH")

# === CONFIGURARE ASYNC CLIENT ===
tornado.httpclient.AsyncHTTPClient.configure(None, max_buffer_size=MAX_BUFFER_SIZE, max_body_size=MAX_BUFFER_SIZE)

# === CONFIGURARE ===
CONFIG_FILE = "comfyui_auth_config.json"

# Configurație implicită
DEFAULT_CONFIG = {
    "auth_port": 7861,
    "admin_port": 8199,
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
LAST_PLUGIN_ACTIVITY = {}

# === WORKFLOW BROWSER ===
WORKFLOW_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflows")

# === USAGE TRACKING ===
USAGE_STATS_FILE = "comfyui_usage_stats.json"
USAGE_STATS = {
    "active_jobs": {},
    "history": [],
    "active_sessions": {},
    "session_history": []
}

def load_usage_stats():
    global USAGE_STATS
    if os.path.exists(USAGE_STATS_FILE):
        try:
            with open(USAGE_STATS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                USAGE_STATS["history"] = data.get("history", [])[-1000:] # Keep last 1000
                USAGE_STATS["session_history"] = data.get("session_history", [])[-1000:] # Keep last 1000
                log.info("✓ Usage stats loaded")
        except:
            log.error("Failed to load usage stats")

def save_usage_stats():
    try:
        with open(USAGE_STATS_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                "history": USAGE_STATS["history"],
                "session_history": USAGE_STATS["session_history"]
            }, f, indent=2)
    except:
        pass

def record_job_start(user, prompt_id, server_url):
    USAGE_STATS["active_jobs"][prompt_id] = {
        "user": user,
        "server": server_url,
        "start_time": time.time(),
        "prompt_id": prompt_id
    }
    log.info(f"Job started: {prompt_id} for user {user} on {server_url}")

def record_job_end(prompt_id):
    if prompt_id in USAGE_STATS["active_jobs"]:
        job = USAGE_STATS["active_jobs"].pop(prompt_id)
        job["end_time"] = time.time()
        job["duration"] = job["end_time"] - job["start_time"]
        USAGE_STATS["history"].append(job)
        save_usage_stats()
        log.info(f"Job finished: {prompt_id} (Duration: {job['duration']:.2f}s)")

def record_session_start(user, session_id):
    USAGE_STATS["active_sessions"][session_id] = {
        "user": user,
        "start_time": time.time(),
        "session_id": session_id
    }
    log.info(f"Session started for {user}: {session_id}")

def record_session_end(session_id):
    if session_id in USAGE_STATS["active_sessions"]:
        sess = USAGE_STATS["active_sessions"].pop(session_id)
        sess["end_time"] = time.time()
        sess["duration"] = sess["end_time"] - sess["start_time"]
        USAGE_STATS["session_history"].append(sess)
        save_usage_stats()
        log.info(f"Session ended for {sess['user']} (Duration: {sess['duration']:.2f}s)")

def migrate_user_stats(old_username, new_username):
    """Migrate all usage statistics from old_username to new_username when a user is renamed."""
    count = 0
    for job in USAGE_STATS["history"]:
        if job.get("user") == old_username:
            job["user"] = new_username
            count += 1
    for sess in USAGE_STATS["session_history"]:
        if sess.get("user") == old_username:
            sess["user"] = new_username
            count += 1
    for job in USAGE_STATS["active_jobs"].values():
        if job.get("user") == old_username:
            job["user"] = new_username
            count += 1
    for sess in USAGE_STATS["active_sessions"].values():
        if sess.get("user") == old_username:
            sess["user"] = new_username
            count += 1
    save_usage_stats()
    log.info(f"Migrated {count} stats entries from '{old_username}' to '{new_username}'")

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
LOCKED_USERS = set()
ALIASES = []
USER_FIRST_SESSION = {}

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
def get_user_workflow_dir(username, alias=None):
    if alias:
        user_dir = os.path.join(WORKFLOW_ROOT_DIR, alias)
    else:
        user_dir = os.path.join(WORKFLOW_ROOT_DIR, username)

    if not os.path.exists(user_dir):
        os.makedirs(user_dir, exist_ok=True)
        log.info(f"Created workflow directory for user {username} (alias: {alias}): {user_dir}")
    return user_dir

def scan_workflow_dir(user_dir, prefix=""):
    entries = []
    if not os.path.exists(user_dir):
        return entries
    try:
        for name in sorted(os.listdir(user_dir), key=lambda x: (not os.path.isdir(os.path.join(user_dir, x)), x.lower())):
            full = os.path.join(user_dir, name)
            rel = (prefix + "/" + name) if prefix else name
            if os.path.isdir(full):
                children = scan_workflow_dir(full, rel)
                entries.append({
                    "type": "directory",
                    "name": name,
                    "path": rel,
                    "children": children
                })
            elif name.endswith('.json') and os.path.isfile(full):
                stats = os.stat(full)
                entries.append({
                    "type": "file",
                    "name": name,
                    "path": rel,
                    "modified": stats.st_mtime,
                    "size": stats.st_size
                })
    except Exception as e:
        log.error(f"Error scanning {user_dir}: {e}")
    return entries

def list_user_workflows(username, alias=None):
    user_dir = get_user_workflow_dir(username, alias)
    return {
        "root": scan_workflow_dir(user_dir),
        "user_directory": user_dir
    }

# Încarcă configurația din fișier sau folosește cea implicită
def load_config():
    global WORKFLOW_ROOT_DIR, AUTH_PORT, ADMIN_PORT
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                log.info("✓ Configuration loaded from file")
        except Exception as e:
            log.error(f"Error loading configuration: {e}")
            config = DEFAULT_CONFIG.copy()
    else:
        log.info("✓ Using default configuration")
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
    
    AUTH_PORT = config.get("auth_port", 7861)
    ADMIN_PORT = config.get("admin_port", 8199)

    if "aliases" in config:
        global ALIASES
        ALIASES = config["aliases"]

    return config

def save_config():
    try:
        config_data = {
            "auth_port": AUTH_PORT,
            "admin_port": ADMIN_PORT,
            "users": USERS,
            "admin": ADMIN_CONFIG,
            "workflow_root": WORKFLOW_ROOT_DIR,
            "global_nginx_auth": GLOBAL_NGINX_AUTH,
            "cookie_secret": config.get("cookie_secret"),
            "aliases": ALIASES
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        log.info("✓ Configuration saved to file")
    except Exception as e:
        log.error(f"Error saving configuration: {e}")

def cleanup_stuck_sessions():
    log.info("Checking for stuck sessions...")
    
    for username in USERS:
        USERS[username]["instances"] = 0
    
    sessions.clear()
    USER_FIRST_SESSION.clear()
    FORCED_LOGOUT_SESSIONS.clear()
    BLOCKED_USERS.clear()
    LOCKED_USERS.clear()
    if 'ALIAS_LOCKS' in globals():
        ALIAS_LOCKS.clear()
    
    log.info("✓ Cleaned up stuck sessions and reset instance counts")

# Încarcă configurația inițială
config = load_config()
load_usage_stats()
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
    # Minimal security headers to avoid breaking external integrations like Firebase
    handler.set_header("X-Content-Type-Options", "nosniff")

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
    log.info(f"Starting continuous health monitoring for ComfyUI: {comfy_url}")

    while True:
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
            
            r = requests.get(f"{comfy_url}/", timeout=5, headers=headers)
            if r.status_code in [200, 401]:
                if not comfy_instances_ready.get(comfy_url):
                    log.info(f"✓ ComfyUI instance {comfy_url} is now READY!")
                comfy_instances_ready[comfy_url] = True
            else:
                if comfy_instances_ready.get(comfy_url):
                    log.warning(f"! ComfyUI instance {comfy_url} returned status {r.status_code}")
                comfy_instances_ready[comfy_url] = False
        except Exception as e:
            if comfy_instances_ready.get(comfy_url, False):
                log.error(f"! ComfyUI instance {comfy_url} is now OFFLINE: {str(e)}")
            comfy_instances_ready[comfy_url] = False

        time.sleep(60)

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
        username = sessions[session_id]["user"]
        record_session_end(session_id)
        clear_user_first_session(session_id)
        del sessions[session_id]
        renumber_session_indices(username)

def create_session(user, alias=None):
    import uuid
    session_id = str(uuid.uuid4())
    user_sessions = [s for s in sessions.values() if s["user"] == user]
    max_index = max((s["session_index"] for s in user_sessions), default=0)
    sessions[session_id] = {
        "user": user,
        "comfy_url": USERS[user]["comfy_url"],
        "created": time.time(),
        "session_index": max_index + 1,
        "alias": alias or "",
        "display_name": USERS[user].get("display_name", user)
    }
    USERS[user]["instances"] += 1
    save_config()
    record_session_start(user, session_id)
    if alias and user not in USER_FIRST_SESSION:
        USER_FIRST_SESSION[user] = session_id
    return session_id

def get_session(session_id):
    if not session_id:
        return None
    return sessions.get(session_id)

def can_user_login(user):
    if user in BLOCKED_USERS:
        if BLOCKED_USERS[user] > time.time():
            return False
        del BLOCKED_USERS[user]
    if user in LOCKED_USERS:
        return False
    user_data = USERS.get(user)
    if not user_data:
        return False
    if not user_data.get("enabled", True):
        return False
    if user_data["instances"] >= user_data["max_instances"]:
        return False
    return True

def is_authenticated(handler):
    session_id = handler.current_user
    return session_id is not None and session_id in sessions

def is_admin_authenticated(handler):
    admin_session_id = handler.get_secure_cookie("admin_session_id")
    if not admin_session_id:
        return False
    if isinstance(admin_session_id, bytes):
        admin_session_id = admin_session_id.decode()
    return admin_session_id in admin_sessions

def renumber_session_indices(username):
    user_sessions = [(sid, sdata) for sid, sdata in sessions.items() if sdata["user"] == username]
    user_sessions.sort(key=lambda x: x[1].get("created", 0))
    for i, (sid, sdata) in enumerate(user_sessions, 1):
        sdata["session_index"] = i

def clear_user_first_session(session_id):
    for user, sid in list(USER_FIRST_SESSION.items()):
        if sid == session_id:
            del USER_FIRST_SESSION[user]
            LOCKED_USERS.discard(user)
            return

def is_handover_lock_available(session_data, session_id):
    alias = session_data.get("alias", "")
    if not alias:
        return False
    username = session_data["user"]
    first_session_id = USER_FIRST_SESSION.get(username)
    return first_session_id == session_id

def create_admin_session():
    import uuid
    session_id = str(uuid.uuid4())
    admin_sessions[session_id] = {"created": time.time()}
    return session_id

def cleanup_blocked_users():
    current_time = time.time()
    for username in list(BLOCKED_USERS.keys()):
        if BLOCKED_USERS[username] <= current_time:
            del BLOCKED_USERS[username]

# === HANDLERE PENTRU INTERFAȚA PRINCIPALĂ ===
class BaseHandler(tornado.web.RequestHandler):
    def get_current_user(self):
        # Try getting session from secure cookie
        session_id = self.get_secure_cookie("session_id")

        # Fallback to session_id in query parameters (for Plugin Aggregator WebSockets and API calls)
        if not session_id:
            try:
                # We use raw URL parsing because self.get_argument might fail for non-standard requests
                # or during certain lifecycle phases
                from urllib.parse import parse_qs, urlparse
                query = urlparse(self.request.uri).query
                qs = parse_qs(query)
                qs_session = qs.get("session_id", [None])[0]

                if qs_session:
                    decoded = tornado.web.decode_signed_value(
                        self.application.settings["cookie_secret"],
                        "session_id",
                        qs_session
                    )
                    if decoded:
                        session_id = decoded
            except Exception as e:
                log.debug(f"Query string session decode failed: {e}")

        # Fallback to X-Session-ID header for trusted plugins
        if not session_id and self.request.headers.get("X-Plugin-Name"):
            session_id = self.request.headers.get("X-Session-ID")

        if not session_id:
            return None

        # Ensure session_id is a string
        return session_id.decode() if isinstance(session_id, bytes) else session_id

    def render_html(self, template, **kwargs):
        result = template
        for key, value in kwargs.items():
            placeholder = "{" + key + "}"
            result = result.replace(placeholder, str(value))
        return result
    
    def check_xsrf_cookie(self):
        # Permit requests from trusted inter-server agents to bypass CSRF
        trusted_headers = ["X-Plugin-Name", "X-Admin-Password", "X-Session-ID", "X-User-ID"]
        if any(self.request.headers.get(h) for h in trusted_headers):
            return

        # Relaxed XSRF: bypass for API routes to avoid breaking proxied requests
        if self.request.path.startswith(("/comfy/api/", "/api/")):
            return

        super().check_xsrf_cookie()

    def prepare(self):
        # Force populate current_user
        _ = self.current_user
        set_security_headers(self)
        # Prevent caching of dynamic authenticated content
        self.set_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.set_header("Pragma", "no-cache")
        self.set_header("Expires", "0")

    def get_client_ip(self):
        # Prioritize X-Forwarded-For as it's standard for multiple proxies
        forwarded_for = self.request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        # Then X-Real-IP
        real_ip = self.request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        return self.request.remote_ip

class LoginHandler(BaseHandler):
    def get(self):
        session_id = self.current_user
        if session_id and session_id in FORCED_LOGOUT_SESSIONS:
            FORCED_LOGOUT_SESSIONS.discard(session_id)
            self.clear_cookie("session_id", path="/")
            self.redirect("/login")
            return

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
        
        self.render("login.html", error="", about_modal=ABOUT_DRAWER_HTML, session_modals=SESSION_MODALS_HTML)

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
                error='Invalid username or password format!',
                about_modal=ABOUT_DRAWER_HTML
            )
            return

        if user in USERS and check_password(USERS[user]["password"], pwd):
            if not USERS[user].get("enabled", True):
                log.warning(f"Disabled user {user} tried to login from IP {client_ip}")
                self.render("login.html",
                    error='This user account is disabled!',
                    about_modal=ABOUT_DRAWER_HTML
                )
                return
                
            alias = self.get_argument("alias", "").strip()
            if alias and alias not in ALIASES:
                alias = ""

            if can_user_login(user):
                session_id = create_session(user, alias=alias if alias else None)
                self.set_secure_cookie("session_id", session_id, expires_days=1, path="/")
                
                # Dacă cererea vine de la un aggregator, trimitem și ID-ul brut pentru management la distanță
                if self.request.headers.get("X-Plugin-Name"):
                    self.set_header("X-Raw-Session-ID", session_id)

                RateLimiter.clear_attempts(client_ip)
                
                log.info(f"User {user} logged in successfully from IP {client_ip}")

                # Notify existing sessions of the same user
                same_user_sessions = [sid for sid, sdata in sessions.items() if sdata["user"] == user and sid != session_id]
                if same_user_sessions:
                    for sid in same_user_sessions:
                        if sid in USER_CHAT_WEBSOCKETS:
                            for ws in USER_CHAT_WEBSOCKETS[sid]:
                                try:
                                    ws.write_message(json.dumps({
                                        "type": "concurrent_user",
                                        "username": user,
                                        "session_index": sessions[session_id].get('session_index', 1),
                                        "alias": sessions[session_id].get("alias", ""),
                                        "message": f"New session opened for {user}" + (f" as {sessions[session_id].get('alias', '')}" if sessions[session_id].get('alias') else f" #{sessions[session_id].get('session_index', 1)}")
                                    }))
                                except: pass

                self.redirect("/comfy/")
            else:
                if user in BLOCKED_USERS:
                    remaining_time = BLOCKED_USERS[user] - time.time()
                    if remaining_time > 0:
                        minutes = int(remaining_time // 60)
                        seconds = int(remaining_time % 60)
                        self.render("forced_logout.html", about_modal=ABOUT_DRAWER_HTML)
                        return
                
                is_locked = user in LOCKED_USERS
                if is_locked:
                    log.warning(f"User {user} tried to login but account is locked from IP {client_ip}")
                else:
                    log.warning(f"User {user} tried to login but limit reached from IP {client_ip}")
                self.render("user_full.html",
                    username=user, 
                    max_instances=USERS[user]["max_instances"],
                    about_modal=ABOUT_DRAWER_HTML,
                    locked=is_locked
                )
        else:
            RateLimiter.record_failed_attempt(client_ip)
            log.warning(f"Failed login attempt for user {user} from IP {client_ip}")
            self.render("login.html",
                error='Invalid credentials!',
                about_modal=ABOUT_DRAWER_HTML
            )

class UserStatusHandler(BaseHandler):
    def get(self):
        # External plugin activity tracking
        plugin_name = self.request.headers.get("X-Plugin-Name")
        if plugin_name:
            LAST_PLUGIN_ACTIVITY[plugin_name] = time.time()
            log.info(f"Activity detected from Plugin Server: {plugin_name}")

        # Colectează alias-urile active per utilizator
        active_aliases_per_user = {}
        for s in sessions.values():
            user = s["user"]
            alias = s.get("alias", "")
            if alias:
                if user not in active_aliases_per_user:
                    active_aliases_per_user[user] = []
                if alias not in active_aliases_per_user[user]:
                    active_aliases_per_user[user].append(alias)

        user_status = []
        sorted_users = sorted(USERS.items(), key=lambda x: x[0].lower())
        
        for username, user_data in sorted_users:
            user_status.append({
                "username": username,
                "instances": user_data["instances"],
                "max_instances": user_data["max_instances"],
                "ready": comfy_instances_ready.get(user_data["comfy_url"], False),
                "enabled": user_data.get("enabled", True),
                "nginx_auth": user_data.get("nginx_auth", {"enabled": False}),
                "locked": username in LOCKED_USERS,
                "active_aliases": active_aliases_per_user.get(username, [])
            })
        
        # Filter active plugins in the last 60 seconds
        current_time = time.time()
        active_plugins = [name for name, last_seen in LAST_PLUGIN_ACTIVITY.items() if current_time - last_seen < 60]

        self.set_header("Content-Type", "application/json")
        self.write({
            "users": user_status,
            "plugins_active": active_plugins
        })

class UserSettingsHandler(BaseHandler):
    def post(self):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.current_user
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
                migrate_user_stats(current_username, new_username)
            
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
        session_id = self.current_user
        username = "Unknown"
        
        if session_id:
            if session_id in FORCED_LOGOUT_SESSIONS:
                FORCED_LOGOUT_SESSIONS.discard(session_id)
            if session_id in sessions:
                username = sessions[session_id].get("user", "Unknown")
                if username in USERS:
                    USERS[username]["instances"] = max(0, USERS[username]["instances"] - 1)

                record_session_end(session_id)
                clear_user_first_session(session_id)
                del sessions[session_id]
                renumber_session_indices(username)
                log.info(f"User {username} logged out")
        
        self.clear_cookie("session_id", path="/")
        self.render("logout.html", about_modal=ABOUT_DRAWER_HTML)

# === WORKFLOW HANDLERS ===
class WorkflowListHandler(BaseHandler):
    def get(self):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.current_user
        session_data = get_session(session_id)
        username = session_data["user"]
        alias = session_data.get("alias")
        
        try:
            result = list_user_workflows(username, alias)
            self.set_header("Content-Type", "application/json")
            self.write({
                "success": True,
                "tree": result["root"],
                "user_directory": result["user_directory"]
            })
        except Exception as e:
            log.error(f"Error listing workflows: {e}")
            self.set_status(500)
            self.write({"success": False, "error": f"Error listing workflows: {str(e)}"})

class WorkflowMkdirHandler(BaseHandler):
    def post(self):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        session_id = self.current_user
        session_data = get_session(session_id)
        username = session_data["user"]
        alias = session_data.get("alias")
        try:
            data = json.loads(self.request.body)
        except Exception as e:
            self.set_status(400)
            self.write({"success": False, "error": f"Invalid JSON: {str(e)}"})
            return
        folder_path = data.get("path", "").strip()
        if not folder_path:
            self.set_status(400)
            self.write({"success": False, "error": "Folder path is required"})
            return
        user_dir = os.path.join(os.path.abspath(get_user_workflow_dir(username, alias)), "")
        abs_path = os.path.abspath(os.path.join(user_dir, folder_path))
        if not abs_path.startswith(user_dir):
            self.set_status(400)
            self.write({"success": False, "error": "Invalid path"})
            return
        try:
            os.makedirs(abs_path, exist_ok=True)
            log.info(f"User {username} created folder: {folder_path}")
            self.write({"success": True, "message": "Folder created"})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class WorkflowRenameHandler(BaseHandler):
    def post(self):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        session_id = self.current_user
        session_data = get_session(session_id)
        username = session_data["user"]
        alias = session_data.get("alias")
        try:
            data = json.loads(self.request.body)
        except Exception as e:
            self.set_status(400)
            self.write({"success": False, "error": f"Invalid JSON: {str(e)}"})
            return
        old_path = data.get("oldPath", "").strip()
        new_path = data.get("newPath", "").strip()
        if not old_path or not new_path:
            self.set_status(400)
            self.write({"success": False, "error": "oldPath and newPath are required"})
            return
        user_dir = os.path.join(os.path.abspath(get_user_workflow_dir(username, alias)), "")
        abs_old = os.path.abspath(os.path.join(user_dir, old_path))
        abs_new = os.path.abspath(os.path.join(user_dir, new_path))
        if not abs_old.startswith(user_dir) or not abs_new.startswith(user_dir):
            self.set_status(400)
            self.write({"success": False, "error": "Invalid path"})
            return
        if not os.path.exists(abs_old):
            self.set_status(404)
            self.write({"success": False, "error": "Source not found"})
            return
        try:
            os.makedirs(os.path.dirname(abs_new), exist_ok=True)
            os.rename(abs_old, abs_new)
            log.info(f"User {username} renamed {old_path} -> {new_path}")
            self.write({"success": True, "message": "Renamed successfully"})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class WorkflowLoadHandler(BaseHandler):
    def get(self, filename):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.current_user
        session_data = get_session(session_id)
        username = session_data["user"]
        alias = session_data.get("alias")
        
        user_dir = os.path.join(os.path.abspath(get_user_workflow_dir(username, alias)), "")
        filepath = os.path.abspath(os.path.join(user_dir, filename))

        if not filepath.startswith(user_dir):
            self.set_status(400)
            self.write({"success": False, "error": "Invalid filename or path traversal detected"})
            return
        
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
        log.info(f"WorkflowSaveHandler.post() called - path: {self.request.path}")
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.current_user
        session_data = get_session(session_id)
        username = session_data["user"]
        alias = session_data.get("alias")
        
        try:
            data = json.loads(self.request.body)
        except Exception as e:
            log.error(f"JSON parse error in WorkflowSaveHandler: {e}")
            self.set_status(400)
            self.write({"success": False, "error": f"Invalid JSON: {str(e)}"})
            return
        filename = data.get("filename", "").strip()
        workflow_data = data.get("workflow")
        
        if not filename or not workflow_data:
            self.set_status(400)
            self.write({"success": False, "error": "Filename and workflow data are required"})
            return
        
        if not filename.endswith('.json'):
            filename += '.json'
        
        user_dir = os.path.join(os.path.abspath(get_user_workflow_dir(username, alias)), "")
        filepath = os.path.abspath(os.path.join(user_dir, filename))

        if not filepath.startswith(user_dir):
            self.set_status(400)
            self.write({"success": False, "error": "Invalid filename or path traversal detected"})
            return
        
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
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
        
        session_id = self.current_user
        session_data = get_session(session_id)
        username = session_data["user"]
        alias = session_data.get("alias")
        
        user_dir = os.path.join(os.path.abspath(get_user_workflow_dir(username, alias)), "")
        filepath = os.path.abspath(os.path.join(user_dir, filename))

        if not filepath.startswith(user_dir):
            self.set_status(400)
            self.write({"success": False, "error": "Invalid filename or path traversal detected"})
            return
        
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
        
        session_id = self.current_user
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
        
        session_id = self.current_user
        session_data = get_session(session_id)
        username = session_data["user"]
        session_index = session_data.get("session_index", 1)
        alias_sender = session_data.get("alias", "")
        
        data = json.loads(self.request.body)
        message = data.get("message", "").strip()
        to_id = data.get("to_user", "admin")
        message_type = data.get("message_type", "text")
        file_data = data.get("file_data")
        
        if not message:
            self.write({"success": False, "error": "Message cannot be empty"})
            return
        
        base_display = f"{username} #{session_index}"
        from_display = f"{base_display} ({alias_sender})" if alias_sender else base_display
        message_data = {
            "from": username,
            "from_id": session_id,
            "from_display": from_display,
            "to": to_id,
            "message": message,
            "timestamp": time.time(),
            "read": False,
            "message_type": message_type,
            "file_data": file_data
        }

        if username not in CHAT_MESSAGES: CHAT_MESSAGES[username] = []
        CHAT_MESSAGES[username].append(message_data)
        
        if to_id != "admin" and to_id in sessions:
            target_user = sessions[to_id]["user"]
            if target_user != username:
                if target_user not in CHAT_MESSAGES: CHAT_MESSAGES[target_user] = []
                CHAT_MESSAGES[target_user].append(message_data)

        for ws in ADMIN_CHAT_WEBSOCKETS:
            try:
                ws.write_message(json.dumps({
                    "type": "new_message",
                    "from_user": username,
                    "from_id": session_id,
                    "from_display": from_display,
                    "to_user": to_id,
                    "message": message,
                    "timestamp": time.time(),
                    "message_type": message_type,
                    "file_data": file_data
                }))
            except: pass
        
        if to_id in USER_CHAT_WEBSOCKETS:
            for ws in USER_CHAT_WEBSOCKETS[to_id]:
                try:
                    ws.write_message(json.dumps({
                        "type": "new_message",
                        "from_user": username,
                        "from_id": session_id,
                        "from_display": from_display,
                        "message": message,
                        "timestamp": time.time(),
                        "message_type": message_type,
                        "file_data": file_data
                    }))
                except: pass
        
        self.write({"success": True})

class UploadChatFileHandler(BaseHandler):
    def post(self):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.current_user
        session_data = get_session(session_id)
        username = session_data["user"]
        session_index = session_data.get("session_index", 1)
        
        message = self.get_argument("message", "").strip()
        to_id = self.get_argument("to_user", "admin")
        
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
        
        from_display = f"{username} #{session_index}"
        message_data = {
            "from": username,
            "from_id": session_id,
            "from_display": from_display,
            "to": to_id,
            "message": message,
            "timestamp": time.time(),
            "read": False,
            "message_type": "file" if file_data_list else "text",
            "file_data": file_data_list[0] if len(file_data_list) == 1 else None
        }

        if username not in CHAT_MESSAGES: CHAT_MESSAGES[username] = []
        CHAT_MESSAGES[username].append(message_data)
        
        if to_id != "admin" and to_id in sessions:
            target_user = sessions[to_id]["user"]
            if target_user != username:
                if target_user not in CHAT_MESSAGES: CHAT_MESSAGES[target_user] = []
                CHAT_MESSAGES[target_user].append(message_data)

        for ws in ADMIN_CHAT_WEBSOCKETS:
            try:
                ws.write_message(json.dumps({
                    "type": "new_message",
                    "from_user": username,
                    "from_id": session_id,
                    "from_display": from_display,
                    "to_user": to_id,
                    "message": message,
                    "timestamp": time.time(),
                    "message_type": "file" if file_data_list else "text",
                    "file_data": file_data_list[0] if len(file_data_list) == 1 else None
                }))
            except: pass
        
        if to_id in USER_CHAT_WEBSOCKETS:
            for ws in USER_CHAT_WEBSOCKETS[to_id]:
                try:
                    ws.write_message(json.dumps({
                        "type": "new_message",
                        "from_user": username,
                        "from_id": session_id,
                        "from_display": from_display,
                        "message": message,
                        "timestamp": time.time(),
                        "message_type": "file" if file_data_list else "text",
                        "file_data": file_data_list[0] if len(file_data_list) == 1 else None
                    }))
                except: pass
        
        self.write({"success": True})

class DownloadFileHandler(BaseHandler):
    def get(self, file_id):
        if not is_authenticated(self) and not is_admin_authenticated(self):
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
        
        session_id = self.current_user
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
        
        session_id = self.current_user
        session_data = get_session(session_id)
        username = session_data["user"]
        
        unread_count = 0
        if username in CHAT_MESSAGES:
            unread_count = sum(1 for msg in CHAT_MESSAGES[username] if msg["from"] != username and not msg["read"])
        
        self.write({"success": True, "unread_count": unread_count})

class ChatUsersListHandler(BaseHandler):
    def get(self):
        if not is_authenticated(self):
            self.set_status(401)
            return

        current_session_id = self.current_user
        user_list = []

        # Add Administrator
        user_list.append({
            "username": "admin",
            "display_name": "Administrator",
            "online": len(ADMIN_CHAT_WEBSOCKETS) > 0,
            "is_session": False
        })

        # Add sessions
        for sid, sdata in sessions.items():
            if sid == current_session_id:
                continue

            alias_str = sdata.get("alias", "")
            display = f"{sdata['user']} #{sdata.get('session_index', 1)}"
            if alias_str:
                display += f" ({alias_str})"
            user_list.append({
                "username": sdata["user"],
                "alias": alias_str,
                "session_id": sid,
                "display_name": display,
                "online": sid in USER_CHAT_WEBSOCKETS,
                "is_session": True
            })

        self.write({"success": True, "users": user_list})

class WebSocketBaseHandler(tornado.websocket.WebSocketHandler):
    def get_current_user(self):
        # Try getting session from secure cookie
        session_id = self.get_secure_cookie("session_id")

        # Fallback to session_id in query parameters
        if not session_id:
            try:
                from urllib.parse import parse_qs, urlparse
                query = urlparse(self.request.uri).query
                qs = parse_qs(query)
                qs_session = qs.get("session_id", [None])[0]

                if qs_session:
                    decoded = tornado.web.decode_signed_value(
                        self.application.settings["cookie_secret"],
                        "session_id",
                        qs_session
                    )
                    if decoded:
                        session_id = decoded
            except Exception as e:
                log.debug(f"WS Query string session decode failed: {e}")

        # Fallback to X-Session-ID header for trusted plugins
        if not session_id and self.request.headers.get("X-Plugin-Name"):
            session_id = self.request.headers.get("X-Session-ID")

        if not session_id:
            return None

        # Ensure session_id is a string
        return session_id.decode() if isinstance(session_id, bytes) else session_id

class ChatWebSocketHandler(WebSocketBaseHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.username = None
        self.session_id = None
        self.session_index = 1
    
    def check_origin(self, origin):
        return True
    
    def open(self):
        if not is_authenticated(self):
            self.close()
            return
        
        self.session_id = self.current_user
        session_data = get_session(self.session_id)
        self.username = session_data["user"]
        self.session_index = session_data.get("session_index", 1)
        
        if self.username not in CHAT_MESSAGES:
            CHAT_MESSAGES[self.username] = []
        
        if self.session_id not in USER_CHAT_WEBSOCKETS:
            USER_CHAT_WEBSOCKETS[self.session_id] = []
        USER_CHAT_WEBSOCKETS[self.session_id].append(self)
        
        unread_count = sum(1 for msg in CHAT_MESSAGES[self.username] if msg["from"] != self.username and not msg["read"])
        self.write_message(json.dumps({
            "type": "unread_count",
            "count": unread_count
        }))
    
    def on_message(self, message):
        try:
            data = json.loads(message)
            
            if data.get("type") == "send_message":
                message_text = data.get("message", "").strip()
                to_id = data.get("to_user", "admin") # Can be 'admin' or session_id
                message_type = data.get("message_type", "text")
                file_data = data.get("file_data")
                
                if message_text and self.username:
                    session_data = get_session(self.session_id)
                    self_alias = session_data.get("alias", "") if session_data else ""
                    base_display = f"{self.username} #{self.session_index}"
                    from_display = f"{base_display} ({self_alias})" if self_alias else base_display

                    message_data = {
                        "from": self.username,
                        "from_id": self.session_id,
                        "from_display": from_display,
                        "to": to_id,
                        "message": message_text,
                        "timestamp": time.time(),
                        "read": False,
                        "message_type": message_type,
                        "file_data": file_data
                    }

                    # Store message in history (global per user for now, but with session info)
                    if self.username not in CHAT_MESSAGES: CHAT_MESSAGES[self.username] = []
                    CHAT_MESSAGES[self.username].append(message_data)
                    
                    # If recipient is another session of a different user, store there too
                    if to_id != "admin" and to_id in sessions:
                        target_user = sessions[to_id]["user"]
                        if target_user != self.username:
                            if target_user not in CHAT_MESSAGES: CHAT_MESSAGES[target_user] = []
                            CHAT_MESSAGES[target_user].append(message_data)

                    # Notify Admin if relevant
                    if to_id == "admin":
                        for ws in ADMIN_CHAT_WEBSOCKETS:
                            try:
                                ws.write_message(json.dumps({
                                    "type": "new_message",
                                    "from_user": self.username,
                                    "from_id": self.session_id,
                                    "from_display": from_display,
                                    "to_user": to_id,
                                    "message": message_text,
                                    "timestamp": time.time(),
                                    "message_type": message_type,
                                    "file_data": file_data
                                }))
                            except: pass

                    # Notify Recipient Session
                    if to_id in USER_CHAT_WEBSOCKETS:
                        for ws in USER_CHAT_WEBSOCKETS[to_id]:
                            try:
                                ws.write_message(json.dumps({
                                    "type": "new_message",
                                    "from_user": self.username,
                                    "from_id": self.session_id,
                                    "from_display": from_display,
                                    "message": message_text,
                                    "timestamp": time.time(),
                                    "message_type": message_type,
                                    "file_data": file_data
                                }))
                            except: pass
                    
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
                        if msg["from"] != self.username:
                            msg["read"] = True
                
                unread_count = sum(1 for msg in CHAT_MESSAGES[self.username] if msg["from"] != self.username and not msg["read"])
                self.write_message(json.dumps({
                    "type": "unread_count",
                    "count": unread_count
                }))
        
        except Exception as e:
            log.error(f"Error in chat WebSocket: {e}")
    
    def on_close(self):
        if self.session_id and self.session_id in USER_CHAT_WEBSOCKETS:
            if self in USER_CHAT_WEBSOCKETS[self.session_id]:
                USER_CHAT_WEBSOCKETS[self.session_id].remove(self)
            if not USER_CHAT_WEBSOCKETS[self.session_id]:
                del USER_CHAT_WEBSOCKETS[self.session_id]

# === SESSION CHECK HANDLER ===
class SessionCheckHandler(BaseHandler):
    def get(self):
        session_id = self.current_user
        
        if not session_id:
            self.write({"status": "not_authenticated"})
            return
        
        if session_id in FORCED_LOGOUT_SESSIONS:
            self.write({"status": "forced_logout"})
            return
        
        # We need to check if it's a forced logout even if it's not in the sessions dict anymore
        # because current_user might still return the old session_id from cookie

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
                    clear_user_first_session(session_id)
                    del sessions[session_id]
                    renumber_session_indices(username)
                    self.clear_cookie("session_id", path="/")
                    self.render("session_expired.html", about_modal=ABOUT_DRAWER_HTML)
                    return
            
            concurrent_sessions = [sid for sid, sdata in sessions.items() if sdata["user"] == username]
            self.write({
                "status": "authenticated", 
                "user": username, 
                "time_remaining": int(time_remaining) if user_timeout > 0 else None,
                "session_index": session_data.get("session_index", 1),
                "alias": session_data.get("alias", ""),
                "has_concurrent_sessions": len(concurrent_sessions) > 1,
                "handover_lock_available": is_handover_lock_available(session_data, session_id)
            })
        else:
            self.write({"status": "not_authenticated"})

class SessionRefreshHandler(BaseHandler):
    def post(self):
        session_id = self.current_user
        
        if not session_id:
            self.write({"success": False, "error": "Not authenticated"})
            return
        
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

class AliasListHandler(BaseHandler):
    def get(self):
        self.write({"aliases": ALIASES})

    def post(self):
        try:
            data = json.loads(self.request.body)
            alias = data.get("alias", "").strip()
            if not alias:
                self.write({"success": False, "error": "Alias cannot be empty"})
                return
            if alias in ALIASES:
                self.write({"success": False, "error": "Alias already exists"})
                return
            ALIASES.append(alias)
            save_config()
            self.write({"success": True, "aliases": ALIASES})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

    def delete(self):
        session_id = self.current_user
        if not session_id:
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        try:
            data = json.loads(self.request.body)
            alias = data.get("alias", "").strip()
            if alias in ALIASES:
                ALIASES.remove(alias)
                ALIAS_LOCKS.discard(alias)
                save_config()
            self.write({"success": True, "aliases": ALIASES})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class SingleUserLockHandler(BaseHandler):
    def get(self):
        session_id = self.current_user
        if not session_id:
            self.write({"locked": False, "session_index": 0, "handover_lock_available": False})
            return
        session_data = get_session(session_id)
        if not session_data:
            self.write({"locked": False, "session_index": 0, "handover_lock_available": False})
            return
        username = session_data["user"]
        alias = session_data.get("alias", "")
        locked = username in LOCKED_USERS
        self.write({
            "locked": locked,
            "session_index": session_data.get("session_index", 1),
            "alias": alias,
            "handover_lock_available": is_handover_lock_available(session_data, session_id)
        })

    def post(self):
        session_id = self.current_user
        if not session_id:
            self.write({"success": False, "error": "Not authenticated"})
            return
        session_data = get_session(session_id)
        if not session_data:
            self.write({"success": False, "error": "Session not found"})
            return

        if not is_handover_lock_available(session_data, session_id):
            self.write({"success": False, "error": "Lock is not available for this session"})
            return

        alias = session_data.get("alias", "")
        username = session_data["user"]

        if username in LOCKED_USERS:
            LOCKED_USERS.discard(username)
            log.info(f"User {username} unlocked")
            self.write({"success": True, "locked": False, "alias": alias, "session_index": session_data.get("session_index", 1), "handover_lock_available": True, "message": "User unlocked"})
        else:
            LOCKED_USERS.add(username)
            # Terminate all other sessions of the same user
            other_sessions = [sid for sid, sdata in sessions.items()
                              if sdata["user"] == username and sid != session_id]
            for sid in other_sessions:
                if sid in USER_CHAT_WEBSOCKETS:
                    for ws in list(USER_CHAT_WEBSOCKETS[sid]):
                        try:
                            ws.write_message(json.dumps({
                                "type": "system_notification",
                                "message": f"Session terminated because user {username} locked the account."
                            }))
                            ws.close()
                        except:
                            pass
                if sid in sessions:
                    user_of_sid = sessions[sid]["user"]
                    FORCED_LOGOUT_SESSIONS.add(sid)
                    # Don't clear_user_first_session here to avoid removing lock if this was first session
                    del sessions[sid]
                    if user_of_sid in USERS:
                        USERS[user_of_sid]["instances"] = max(0, USERS[user_of_sid]["instances"] - 1)
                    record_session_end(sid)
                    renumber_session_indices(user_of_sid)

            log.info(f"User {username} locked (alias: {alias}), terminated {len(other_sessions)} other session(s)")
            self.write({"success": True, "locked": True, "alias": alias, "session_index": session_data.get("session_index", 1), "handover_lock_available": True, "message": "Account locked exclusively for this session, other sessions terminated"})

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
            self.write(f"""
            <div style="text-align: center; margin-top: 100px;">
                <h2 style="color: #dc3545;">Too Many Failed Attempts</h2>
                <p>Your IP has been temporarily blocked due to too many failed login attempts.</p>
                <p>Please try again later.</p>
                <a href="/admin/login">Try Again</a>
            </div>
            {ABOUT_DRAWER_HTML}
            """)
            return
        
        # Afișează pagina de login
        self.render("admin_login.html", error="", about_modal=ABOUT_DRAWER_HTML)

    def post(self):
        client_ip = self.get_client_ip()
        
        if RateLimiter.is_blocked(client_ip):
            self.set_status(429)
            self.write({"error": "Too many attempts"})
            return
        
        password = self.get_argument("password", "")
        
        # Check password
        if check_password(ADMIN_CONFIG["password"], password):
            session_id = create_admin_session()
            # Set path="/" to ensure it's sent for all /admin/api/ requests
            self.set_secure_cookie("admin_session_id", session_id, expires_days=1, path="/")
            
            RateLimiter.clear_attempts(client_ip)
            
            log.info(f"Admin logged in successfully from IP {client_ip}")
            self.redirect("/admin/")
        else:
            RateLimiter.record_failed_attempt(client_ip)
            log.warning(f"Failed admin login attempt from IP {client_ip}")
            
            # Re-render page with error
            self.render("admin_login.html", error="Invalid admin password!", about_modal=ABOUT_DRAWER_HTML)

class AdminLogoutHandler(BaseHandler):
    def get(self):
        self.perform_logout()
        self.redirect("/admin/login")

    def post(self):
        self.perform_logout()
        self.write({"success": True})

    def perform_logout(self):
        session_id = self.get_secure_cookie("admin_session_id")
        if session_id:
            if session_id in admin_sessions:
                del admin_sessions[session_id]
                log.info("Admin logged out")
        self.clear_cookie("admin_session_id", path="/")

# === HANDLERE PENTRU ADMIN INTERFACE ===
class AdminHandler(BaseHandler):
    def get(self):
        if not is_admin_authenticated(self):
            self.redirect("/admin/login")
            return
        self.render("admin.html", about_modal=ABOUT_DRAWER_HTML)

class AdminUsageStatsHandler(BaseHandler):
    def get(self):
        # Permitem accesul și prin X-Admin-Password pentru Aggregator
        admin_pass_header = self.request.headers.get("X-Admin-Password")
        authorized = False

        if admin_pass_header:
            expected_pass = ADMIN_CONFIG["password"]
            if isinstance(expected_pass, str) and expected_pass.startswith("$2b$"):
                try:
                    if bcrypt.checkpw(admin_pass_header.encode(), expected_pass.encode()):
                        authorized = True
                except:
                    pass
            elif admin_pass_header == expected_pass:
                authorized = True

        if not authorized and not is_admin_authenticated(self):
            self.set_status(401)
            return

        self.set_header("Content-Type", "application/json")
        self.write(USAGE_STATS)

class AdminStatusHandler(BaseHandler):
    def get(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"error": "Not authenticated"})
            return
            
        self.set_header("Content-Type", "application/json")
        
        user_status = {}
        for username, user_data in USERS.items():
            unread_count = 0
            if username in CHAT_MESSAGES:
                unread_count = sum(1 for msg in CHAT_MESSAGES[username] if msg["from"] == username and not msg.get("read", False))

            user_status[username] = {
                "instances": user_data["instances"],
                "max_instances": user_data["max_instances"],
                "session_timeout": user_data.get("session_timeout", 60),
                "comfy_url": user_data["comfy_url"],
                "ready": comfy_instances_ready.get(user_data["comfy_url"], False),
                "enabled": user_data.get("enabled", True),
                "nginx_auth": user_data.get("nginx_auth", {"enabled": False}),
                "unread_count": unread_count
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
            record_session_end(session_id)
            del sessions[session_id]
            renumber_session_indices(username)
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
                migrate_user_stats(username, new_username)
            
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
                    clear_user_first_session(session_id)
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
class AdminServerSettingsHandler(BaseHandler):
    def get(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            return

        self.set_header("Content-Type", "application/json")
        self.write({
            "auth_port": AUTH_PORT,
            "admin_port": ADMIN_PORT
        })

    def post(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            return

        try:
            data = json.loads(self.request.body)
            global AUTH_PORT, ADMIN_PORT
            AUTH_PORT = int(data.get("auth_port", AUTH_PORT))
            ADMIN_PORT = int(data.get("admin_port", ADMIN_PORT))

            save_config()
            log.info(f"Admin updated server ports: Auth={AUTH_PORT}, Admin={ADMIN_PORT}")
            self.write({"success": True, "message": "Ports have been updated. Please restart the server."})
        except Exception as e:
            log.error(f"Error updating server ports: {e}")
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class AdminTerminateSessionHandler(BaseHandler):
    def check_xsrf_cookie(self):
        # API endpoint secured with password
        return

    def set_default_headers(self):
        self.set_header("Content-Type", "application/json")

    def post(self):
        # Secured with admin_password to allow plugin_server to call
        try:
            data = tornado.escape.json_decode(self.request.body)
            admin_pass = data.get("admin_password")
            session_id = data.get("session_id")

            expected_pass = ADMIN_CONFIG["password"]
            authorized = False
            if isinstance(expected_pass, str) and expected_pass.startswith("$2b$"):
                try:
                    if admin_pass and bcrypt.checkpw(admin_pass.encode(), expected_pass.encode()):
                        authorized = True
                except:
                    pass
            elif admin_pass == expected_pass:
                authorized = True

            if not authorized:
                self.set_status(403)
                self.write({"error": "Unauthorized"})
                return

            log.info(f"Session termination request: {session_id}. Available sessions: {list(sessions.keys())}")
            # Normalize session_id (remove quotes if present from raw cookie)
            if session_id and (session_id.startswith('"') or session_id.startswith('%22')):
                session_id = unquote(session_id).strip('"')

            if session_id in sessions:
                user = sessions[session_id]["user"]
                log.info(f"Remote session termination: {session_id} for user {user}")

                # Decrement instance count
                if user in USERS:
                    USERS[user]["instances"] = max(0, USERS[user]["instances"] - 1)

                # Close associated WebSockets if any
                if session_id in USER_CHAT_WEBSOCKETS:
                    for ws in list(USER_CHAT_WEBSOCKETS[session_id]):
                        ws.close()

                # Log logout in usage stats
                FORCED_LOGOUT_SESSIONS.add(session_id)
                record_session_end(session_id)
                clear_user_first_session(session_id)
                del sessions[session_id]
                renumber_session_indices(user)
                save_config()
                self.write({"success": True, "message": f"Session {session_id} was terminated"})
            else:
                self.write({"success": False, "error": "Session not found"})
        except Exception as e:
            log.error(f"Error terminating remote session: {e}")
            self.set_status(500)
            self.write({"error": str(e)})

class AdminRestartHandler(BaseHandler):
    def post(self):
        if not is_admin_authenticated(self):
            self.set_status(401)
            return

        log.warning("Admin requested server restart...")
        self.write({"success": True, "message": "Server is restarting..."})

        # Schedule restart after a short delay to allow response to be sent
        tornado.ioloop.IOLoop.current().add_timeout(
            time.time() + 1,
            self._restart_server
        )

    def _restart_server(self):
        log.warning("Restarting process now!")
        # Re-execute the current script
        python = sys.executable
        os.execl(python, python, *sys.argv)

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
                unread_count = sum(1 for msg in CHAT_MESSAGES[username] if msg["from"] == username and not msg.get("read", False))
            
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
        
        # Notify all sessions of this user
        for sid, sdata in sessions.items():
            if sdata["user"] == to_user and sid in USER_CHAT_WEBSOCKETS:
                for ws in USER_CHAT_WEBSOCKETS[sid]:
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
        
        # Notify all sessions of this user
        for sid, sdata in sessions.items():
            if sdata["user"] == to_user and sid in USER_CHAT_WEBSOCKETS:
                for ws in USER_CHAT_WEBSOCKETS[sid]:
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
                if msg["from"] == username:
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
                    
                    # Notify all sessions of this user
                    for sid, sdata in sessions.items():
                        if sdata["user"] == to_user and sid in USER_CHAT_WEBSOCKETS:
                            for ws in USER_CHAT_WEBSOCKETS[sid]:
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
        origin = self.request.headers.get("Origin")
        if origin:
            self.set_header("Access-Control-Allow-Origin", origin)

        self.set_header("Access-Control-Allow-Headers", "x-requested-with, content-type, authorization, x-forwarded-for, x-real-ip, x-forwarded-proto, x-forwarded-host, cookie, x-xsrftoken")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS, PATCH, HEAD")
        self.set_header("Access-Control-Allow-Credentials", "true")
    
    async def options(self, path=None):
        await self._proxy_request("OPTIONS", path or "")
    
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
        session_id = self.current_user
        if not session_id:
            return None
        
        session_data = get_session(session_id)
        return session_data.get("comfy_url") if session_data else None
    
    def _modify_prompt_for_alias(self, body, alias):
        """Modifică prompt-ul pentru a include alias-ul în prefixul fișierelor de ieșire"""
        try:
            data = json.loads(body)
            modified = False
            if "prompt" in data:
                for node_id, node_data in data["prompt"].items():
                    if "inputs" in node_data:
                        inputs = node_data["inputs"]
                        # Prepend alias to common output prefix fields
                        for field in ["filename_prefix", "prefix", "output_path"]:
                            if field in inputs and isinstance(inputs[field], str):
                                # Avoid double prepending
                                if not inputs[field].startswith(f"{alias}/"):
                                    inputs[field] = f"{alias}/{inputs[field]}"
                                    modified = True

            if modified:
                log.info(f"Modified prompt to use alias subfolder: {alias}")
                return json.dumps(data).encode('utf-8')
            return body
        except Exception as e:
            log.error(f"Error modifying prompt for alias: {e}")
            return body

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
    
    def _rewrite_urls(self, content, comfy_url, proxy_base_url):
        """Rescrie toate URL-urile din conținut să pointeze către proxy"""
        if not content: return content

        # Parsează URL-urile ComfyUI pentru a extrage host-ul și portul
        parsed_comfy = urlparse(comfy_url)
        comfy_host = parsed_comfy.netloc
        comfy_scheme = parsed_comfy.scheme
        comfy_port = parsed_comfy.port
        
        # Proxy base URL
        parsed_proxy = urlparse(proxy_base_url)
        proxy_host = parsed_proxy.netloc
        proxy_scheme = parsed_proxy.scheme
        
        # Identifică variante de host intern (localhost, 127.0.0.1, IP local)
        local_ip = get_local_ip()
        internal_hosts = {comfy_host, "localhost", "127.0.0.1", local_ip}
        if comfy_port:
            internal_hosts.add(f"localhost:{comfy_port}")
            internal_hosts.add(f"127.0.0.1:{comfy_port}")
            internal_hosts.add(f"{local_ip}:{comfy_port}")

        # Rute interne care NU trebuie rescrise/prefixate
        internal_routes = (
            '/comfy/', '/static/', '/css/', '/js/', '/login', '/logout',
            '/user-status', '/user-settings', '/chat-', '/send-message',
            '/upload-chat', '/download-file', '/mark-messages', '/unread-messages',
            '/chat-ws', '/health', '/check-session', '/refresh-session', '/api/workflows',
            'http://', 'https://', 'ws://', 'wss://', 'data:', 'blob:'
        )

        # 1. Rescrie URL-uri absolute
        for host in internal_hosts:
            # HTTP/HTTPS
            pattern = fr'https?://{re.escape(host)}'
            replacement = f'{proxy_scheme}://{proxy_host}/comfy'
            content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)

            # WS/WSS
            pattern = fr'wss?://{re.escape(host)}'
            replacement = f'ws{"s" if proxy_scheme=="https" else ""}://{proxy_host}/comfy'
            content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)

        # 2. Rescrie URL-uri relative care încep cu /
        def replace_relative_url(match):
            full_match = match.group(0)
            prefix = match.group(1)
            url = match.group(2)
            suffix = match.group(3)
            
            if not url: return full_match
            
            # Nu modifica URL-urile interne
            if url.startswith(internal_routes):
                return full_match
            
            # Rescrie URL-ul relativ să pointeze către /comfy/
            new_url = f'/comfy{url}' if url.startswith('/') else f'/comfy/{url}'
            return f'{prefix}{new_url}{suffix}'

        # Atribute HTML comune care conțin URL-uri
        attrs = ['src', 'href', 'action', 'data-src', 'data-href']
        for attr in attrs:
            content = re.sub(
                fr'({attr}=["\'])([^"\']*)(["\'])',
                replace_relative_url,
                content
            )
        
        # 3. Rescrie URL-uri absolute în șiruri de caractere (JSON sau JS)
        for host in internal_hosts:
            # Înlocuim ://host cu ://proxy_host/comfy
            # Dar atenție să nu dublăm /comfy dacă e deja acolo
            content = content.replace(f'://{host}/comfy', f'://{proxy_host}/comfy')
            content = content.replace(f'://{host}', f'://{proxy_host}/comfy')

        return content
    
    async def _proxy_request(self, method, path):
        session_id = self.current_user
        
        # Verifică session și forced logout
        if session_id:
            session_id_str = session_id
            if session_id_str in FORCED_LOGOUT_SESSIONS:
                self.set_header("Content-Type", "text/html")
                self.write("""
                <html>
                <body>
                <script>
                if (typeof openForcedLogoutModal === 'function') {
                    openForcedLogoutModal();
                } else {
                    window.location.href = '/logout';
                }
                </script>
                </body>
                </html>""")
                return
        
        # Verifică expirarea sesiunii
        if session_id:
            session_id_val = session_id
            session_data = get_session(session_id_val)
            if session_data:
                username = session_data["user"]
                user_timeout = USERS.get(username, {}).get("session_timeout", 60)
                
                if user_timeout > 0:
                    timeout_seconds = user_timeout * 60
                    if time.time() - session_data["created"] > timeout_seconds:
                        if username in USERS:
                            USERS[username]["instances"] = max(0, USERS[username]["instances"] - 1)
                        record_session_end(session_id_val)
                        clear_user_first_session(session_id_val)
                        del sessions[session_id_val]
                        renumber_session_indices(username)
                        self.clear_cookie("session_id", path="/")
                        self.render("session_expired.html", about_modal=ABOUT_DRAWER_HTML)
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
            self.render("waiting.html", about_modal=ABOUT_DRAWER_HTML)
            return

        # Obține path-ul brut din URI pentru a păstra encodarea (important pentru workflow-uri în subfoldere)
        # Folosim self.request.uri și separăm query string pentru a obține path-ul brut, încă encodat
        raw_uri = self.request.uri
        if '?' in raw_uri:
            raw_path = raw_uri.split('?')[0]
        else:
            raw_path = raw_uri
        
        # Elimină prefixul /comfy/ sau /comfy dacă există
        if raw_path.startswith('/comfy/'):
            raw_path = raw_path[7:]
        elif raw_path == '/comfy':
            raw_path = ''
        elif raw_path.startswith('/'):
            raw_path = raw_path[1:]

        # Construiește URL-ul țintă. Păstrăm slash-ul dacă raw_path este gol dar cererea originală avea unul
        comfy_url = comfy_url.rstrip('/')
        target_url = f"{comfy_url}/{raw_path}"
        
        if self.request.query:
            target_url += "?" + self.request.query
        
        session_data = get_session(self.current_user)
        proxy_username = session_data["user"] if session_data else "unknown"
        
        log.info(f"Proxying {method} {path} for user {proxy_username} to {target_url}")
        
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
            
            # Setează Host header corect pentru instanța internă
            parsed_target = urlparse(target_url)
            target_netloc = parsed_target.netloc
            headers['Host'] = target_netloc

            # Rescrie Origin și Referer pentru a părea că vin de la instanța internă
            auth_host = self.request.host
            if 'Origin' in headers:
                # Spoof exact origin - always match target scheme and netloc
                headers['Origin'] = f"{parsed_target.scheme}://{target_netloc}"

            if 'Referer' in headers:
                # Spoof referer to match target, stripping /comfy prefix if present
                ref_parsed = urlparse(headers['Referer'])
                ref_path = ref_parsed.path
                if ref_path.startswith('/comfy/'):
                    ref_path = ref_path[6:] # Keep the leading /
                elif ref_path == '/comfy':
                    ref_path = '/'

                headers['Referer'] = urlunparse((
                    parsed_target.scheme,
                    target_netloc,
                    ref_path,
                    ref_parsed.params,
                    ref_parsed.query,
                    ref_parsed.fragment
                ))

            # Force 'same-origin' since we are proxying to an internal address
            # This helps avoid 403 Forbidden on some setups
            headers['Sec-Fetch-Site'] = 'same-origin'

            # Adaugă headere standard de proxy
            client_ip = self.get_client_ip()
            headers['X-Forwarded-For'] = client_ip
            # We don't set X-Forwarded-Host to target_netloc because it might confuse some apps
            # We already set the 'Host' header which is more critical.
            headers['X-Forwarded-Proto'] = self.request.protocol
            headers['X-Forwarded-Port'] = str(port)
            headers['X-Real-IP'] = client_ip
            
            # Adăugă headere de identificare pentru uz intern
            if session_id:
                headers['X-User-ID'] = proxy_username
                headers['X-Session-ID'] = session_id

            # Curăță Cookie header de cookie-urile noastre interne
            if 'Cookie' in headers:
                cookies = headers['Cookie'].split('; ')
                filtered_cookies = [c for c in cookies if not c.startswith(('session_id=', 'admin_session_id=', '_xsrf='))]
                if filtered_cookies:
                    headers['Cookie'] = '; '.join(filtered_cookies)
                else:
                    del headers['Cookie']
            
            # Adaugă autentificare nginx dacă este necesară
            add_nginx_auth_headers(headers, proxy_username)
            
            # Pregătește body-ul cererii
            body = None
            if method in ["POST", "PUT", "DELETE", "PATCH"] and self.request.body:
                body = self.request.body

                # Interceptare prompt pentru a injecta alias-ul în folderele de output
                if method == "POST" and "prompt" in raw_path and session_data:
                    alias = session_data.get("alias")
                    if alias:
                        body = self._modify_prompt_for_alias(body, alias)
            
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
                decompress_response=True,
                allow_nonstandard_methods=True
            )
            
            # Execută cererea
            response = await client.fetch(req, raise_error=False)
            
            # Logare pentru depanare 403
            if response.code == 403:
                log.warning(f"403 Forbidden from upstream for {target_url}")
                log.debug(f"Request Headers: {headers}")
                log.debug(f"Response Headers: {response.headers}")
                if response.body:
                    log.debug(f"Response Body: {response.body[:500]}")

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
                            host = self.request.headers.get("X-Forwarded-Host", self.request.host)
                            location = location.replace(comfy_url, f"{self.request.protocol}://{host}/comfy")
                        self.set_header(header, location)
                    elif header_lower.startswith('access-control-allow-'):
                        # Păstrăm headerul de CORS de la upstream
                        self.set_header(header, value)
                    else:
                        self.set_header(header, value)
            
            # Asigură headere CORS minime dacă lipsesc
            if "Access-Control-Allow-Origin" not in self._headers:
                origin = self.request.headers.get("Origin")
                if origin:
                    self.set_header("Access-Control-Allow-Origin", origin)
            if "Access-Control-Allow-Credentials" not in self._headers:
                self.set_header("Access-Control-Allow-Credentials", "true")
            
            # Pentru răspunsuri HTML sau JSON, rescrie URL-urile și injectează UI-ul
            content_type = response.headers.get('Content-Type', '').lower()
            # Allow rewriting for error codes (e.g., 403) if it's HTML/JSON
            is_html = 'text/html' in content_type and response.code not in [204, 304]
            is_json = 'application/json' in content_type and response.code not in [204, 304]
            
            if (is_html or is_json) and response.body:
                # Intercept prompt_id for usage monitoring
                if is_json and "prompt" in raw_path and method == "POST" and response.code == 200:
                    try:
                        prompt_resp = json.loads(response.body)
                        prompt_id = prompt_resp.get("prompt_id")
                        if prompt_id:
                            record_job_start(proxy_username, prompt_id, comfy_url)
                    except:
                        pass

                try:
                    # Determine encoding
                    encoding = 'utf-8'
                    if 'charset=' in content_type:
                        charset_match = re.search(r'charset=([\w-]+)', content_type, re.IGNORECASE)
                        if charset_match:
                            encoding = charset_match.group(1)
                    
                    content = response.body.decode(encoding, errors='replace')
                    
                    # Rewrite URLs - supports X-Forwarded-Host for aggregator
                    host = self.request.headers.get("X-Forwarded-Host", self.request.host)
                    proxy_base_url = f"{self.request.protocol}://{host}"
                    content = self._rewrite_urls(content, comfy_url, proxy_base_url)
                    
                    # Inject our UI only into HTML (only on success)
                    if is_html and response.code == 200:
                        session_index = session_data.get("session_index", 1) if session_data else 1
                        alias = session_data.get("alias", "") if session_data else ""
                        content = self._inject_ui(content, proxy_username, session_index, alias)
                    
                    self.write(content.encode(encoding))
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
    
    def _inject_ui(self, html_content, username, session_index=1, alias=""):
        """Injects UI into HTML responses"""
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
            
            # Găsește poziția pentru injectare (case-insensitive)
            match = re.search(r'</head>', html_content, re.IGNORECASE)
            
            if match:
                head_end_pos = match.start()
                escaped_about = ABOUT_DRAWER_HTML.replace('`', '\\`').replace('\n', ' ')
                escaped_chat = CHAT_UI_HTML.replace('`', '\\`').replace('\n', ' ')
                escaped_settings = USER_SETTINGS_MODAL_HTML.replace('`', '\\`').replace('\n', ' ')
                escaped_session = SESSION_MODALS_HTML.replace('`', '\\`').replace('\n', ' ')
                escaped_workflow = WORKFLOW_BROWSER_HTML.replace('`', '\\`').replace('\n', ' ')
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
                    z-index: 20001;
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
                    z-index: 20001;
                    height: 25px;
                    display: flex;
                    align-items: center;
                }}
                .comfy-auth-buttons {{
                    position: fixed;
                    top: 0;
                    right: 0;
                    z-index: 20001;
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

                .comfy-lock-btn {{
                    background: none;
                    color: #aaa;
                    border: none;
                    cursor: pointer;
                    font-size: 13px;
                    padding: 0 4px;
                    margin-left: 3px;
                    line-height: 1;
                    transition: all 0.2s;
                }}
                .comfy-lock-btn:hover {{ color: #ffc107; }}
                .comfy-lock-btn.locked {{ color: #ffc107; }}
                .comfy-lock-btn.locked:hover {{ color: #dc3545; }}

                .workflow-dot {{
                    position: fixed;
                    bottom: 15px;
                    left: 15px;
                    width: 20px;
                    height: 20px;
                    background: #28a745;
                    border: 2px solid #1e7e34;
                    border-radius: 50%;
                    cursor: pointer;
                    z-index: 20002;
                    box-shadow: 0 0 8px rgba(40,167,69,0.5);
                    transition: all 0.2s;
                }}
                .workflow-dot:hover {{
                    transform: scale(1.2);
                    box-shadow: 0 0 12px rgba(40,167,69,0.8);
                }}
                .workflow-browser-modal {{
                    display: none;
                    position: fixed;
                    top: 0; left: 0; width: 100%; height: 100%;
                    background: rgba(0,0,0,0.7);
                    z-index: 20003;
                    justify-content: center;
                    align-items: center;
                }}
                .workflow-browser-content {{
                    background: #222;
                    border-radius: 8px;
                    width: 500px;
                    max-width: 90%;
                    max-height: 80vh;
                    display: flex;
                    flex-direction: column;
                    box-shadow: 0 5px 25px rgba(0,0,0,0.5);
                }}
                .workflow-browser-header {{
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    padding: 15px 20px;
                    border-bottom: 1px solid #444;
                }}
                .workflow-browser-header h2 {{ margin: 0; font-size: 16px; color: #e0e0e0; }}
                .workflow-close-btn {{
                    background: none; border: none; color: #999;
                    font-size: 24px; cursor: pointer; padding: 0 5px;
                }}
                .workflow-close-btn:hover {{ color: #fff; }}
                .workflow-browser-body {{
                    padding: 20px;
                    overflow-y: auto;
                    flex: 1;
                }}
                .workflow-browser-section {{
                    margin-bottom: 20px;
                }}
                .workflow-browser-section h3 {{
                    margin: 0 0 10px 0; font-size: 14px; color: #aaa;
                }}
                .workflow-list {{
                    display: flex;
                    flex-direction: column;
                    gap: 6px;
                    max-height: 250px;
                    overflow-y: auto;
                }}
                .workflow-item {{
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    background: #2a2a2a;
                    padding: 8px 12px;
                    border-radius: 4px;
                    border-left: 3px solid #28a745;
                }}
                .workflow-item-name {{
                    font-size: 13px; color: #e0e0e0;
                    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;
                }}
                .workflow-item-date {{
                    font-size: 11px; color: #777; margin: 0 10px; white-space: nowrap;
                }}
                .workflow-item-actions {{
                    display: flex; gap: 5px;
                }}
                .workflow-item-btn {{
                    padding: 3px 8px; border: none; border-radius: 3px;
                    cursor: pointer; font-size: 11px;
                }}
                .workflow-load-btn {{ background: #007bff; color: white; }}
                .workflow-load-btn:hover {{ background: #0056b3; }}
                .workflow-inject-btn {{ background: #28a745; color: white; }}
                .workflow-inject-btn:hover {{ background: #1e7e34; }}
                .workflow-del-btn {{ background: #dc3545; color: white; }}
                .workflow-del-btn:hover {{ background: #c82333; }}
                .workflow-empty {{
                    text-align: center; color: #666; font-size: 13px; padding: 20px;
                }}
                .workflow-save-row {{
                    display: flex; gap: 10px;
                }}
                .workflow-save-input {{
                    flex: 1; padding: 8px 12px; border: 1px solid #444;
                    background: #333; color: #e0e0e0; border-radius: 4px; font-size: 13px;
                }}
                .workflow-save-btn {{
                    padding: 8px 16px; background: #28a745; color: white;
                    border: none; border-radius: 4px; cursor: pointer; font-size: 13px;
                }}
                .workflow-save-btn:hover {{ background: #1e7e34; }}
                .workflow-message {{
                    margin-top: 8px; font-size: 12px; padding: 4px 8px;
                    border-radius: 3px; display: none;
                }}
                .workflow-message.success {{ display: block; color: #28a745; background: rgba(40,167,69,0.1); }}
                .workflow-message.error {{ display: block; color: #dc3545; background: rgba(220,53,69,0.1); }}

                /* Hide native load/save buttons that conflict with our workflow manager */
                .comfy-workflow-btn, button[onclick*="queuePrompt"] {{ display: none !important; }}
                </style>
                
                <script id="comfy-auth-init">
                function initComfyAuthUI() {{
                    if (document.getElementById('comfy-auth-overlay-added')) return;

                    const overlay = document.createElement('div');
                    overlay.id = 'comfy-auth-overlay-added';
                    overlay.className = 'comfy-auth-overlay';
                    
                    const userInfo = document.createElement('div');
                    userInfo.className = 'comfy-user-info';
                    var aliasDisplay = '{alias}';
                    var displaySuffix = aliasDisplay ? ' <span style="color: red; font-weight: bold; margin-left: 5px;">' + aliasDisplay + '</span>' : ' <span style="color: red; font-weight: bold; margin-left: 5px;">#{session_index}</span>';
                    userInfo.innerHTML = `Welcome, {username}` + displaySuffix + ` <button class="comfy-lock-btn" id="comfyLockBtn" onclick="toggleUserLock()" title="Lock this user - prevent other logins">&#128275;</button>`;
                    
                    const serverTitle = document.createElement('div');
                    serverTitle.className = 'server-title';
                    serverTitle.textContent = 'ADA Server';
                    
                    const buttonsDiv = document.createElement('div');
                    buttonsDiv.className = 'comfy-auth-buttons';
                    buttonsDiv.innerHTML = `
                        <button class="comfy-about-btn" onclick="openAboutModal()">?</button>
                        <button class="comfy-settings-btn" onclick="openUserSettingsModal()" title="Settings">SET</button>
                        <a href="/logout" class="comfy-logout-btn">Logout</a>
                    `;
                    
                    document.body.insertAdjacentHTML('beforeend', `{escaped_about}`);
                    document.body.insertAdjacentHTML('beforeend', `{escaped_chat}`);
                    document.body.insertAdjacentHTML('beforeend', `{escaped_settings}`);
                    document.body.insertAdjacentHTML('beforeend', `{escaped_session}`);
                    document.body.insertAdjacentHTML('beforeend', `{escaped_workflow}`);

                    document.body.appendChild(overlay);
                    document.body.appendChild(userInfo);
                    document.body.appendChild(serverTitle);
                    document.body.appendChild(buttonsDiv);

                    const workflowDot = document.createElement('div');
                    workflowDot.className = 'workflow-dot';
                    workflowDot.title = 'Workflow Manager';
                    workflowDot.onclick = function() {{ window.openWorkflowBrowser(); }};
                    document.body.appendChild(workflowDot);
                    
                    console.log('ComfyUI Auth Server UI injected successfully');
                    
                    if (typeof initSessionMonitoring === 'function') {{
                        setTimeout(initSessionMonitoring, 1000);
                    }}
                    
                    if (typeof connectChatWebSocket === 'function') {{
                        setTimeout(connectChatWebSocket, 2000);
                    }}
                }}

                if (document.readyState === 'loading') {{
                    document.addEventListener('DOMContentLoaded', initComfyAuthUI);
                }} else {{
                    initComfyAuthUI();
                }}
                </script>
                """
                
                # Pass session_index to our_injection template
                our_injection = our_injection.replace("{session_index}", str(session_index))
                html_content = html_content[:head_end_pos] + our_injection + html_content[head_end_pos:]
            else:
                our_injection = our_injection.replace("{session_index}", str(session_index))
                # Dacă nu găsim </head>, injectăm la începutul body sau la începutul documentului
                body_match = re.search(r'<body>', html_content, re.IGNORECASE)
                if body_match:
                    pos = body_match.end()
                    html_content = html_content[:pos] + our_injection + html_content[pos:]
                else:
                    html_content = our_injection + html_content
            
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
class MultiInstanceWebSocketProxy(WebSocketBaseHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.comfy_ws = None
        self._running = True
        self.username = None
        self._ping_interval = None
    
    def check_origin(self, origin):
        return True
    
    def get_user_comfy_ws_url(self):
        session_id = self.current_user
        if not session_id:
            return None
        
        session_data = get_session(session_id)
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
        # Force populate current_user
        _ = self.current_user

        if not is_authenticated(self):
            self.close(code=4001, reason="Not authenticated")
            return
        
        session_id = self.current_user
        if not session_id:
            self.close(code=4001, reason="Not authenticated")
            return
        
        session_data = get_session(session_id)
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
            # Build URL with parameters in query string to transmit session
            parsed_url = urlparse(comfy_ws_url)
            query_params = []
            if parsed_url.query:
                query_params.append(parsed_url.query)
            
            # Includem parametrii originali de la client (ex: clientId)
            if self.request.query:
                query_params.append(self.request.query)

            # Adăugăm session_id în query string
            if session_id:
                query_params.append(f"session_id={session_id}")
            
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
            
            # Pregătește headerele pentru WebSocket
            ws_headers = {}
            for h in ['User-Agent', 'Accept-Language', 'Cookie']:
                if h in self.request.headers:
                    ws_headers[h] = self.request.headers[h]

            # Adaugă Referer rewriten pentru WebSocket
            if 'Referer' in self.request.headers:
                ref_parsed = urlparse(self.request.headers['Referer'])
                ref_path = ref_parsed.path
                if ref_path.startswith('/comfy/'):
                    ref_path = ref_path[6:]
                elif ref_path == '/comfy':
                    ref_path = '/'

                ws_headers['Referer'] = urlunparse((
                    'http' if parsed_url.scheme == 'ws' else 'https',
                    parsed_url.netloc,
                    ref_path,
                    ref_parsed.params,
                    ref_parsed.query,
                    ref_parsed.fragment
                ))

            ws_headers['Host'] = parsed_url.netloc
            ws_headers['Origin'] = f"{'https' if parsed_url.scheme == 'wss' else 'http'}://{parsed_url.netloc}"
            ws_headers['Sec-Fetch-Site'] = 'same-origin'

            # Forward client IP
            ws_headers['X-Forwarded-For'] = self.request.headers.get("X-Forwarded-For", self.request.remote_ip)
            ws_headers['X-Real-IP'] = self.request.headers.get("X-Real-IP", self.request.remote_ip)

            # Conectează-te la WebSocket-ul destinație folosind HTTPRequest pentru a include headerele
            request = tornado.httpclient.HTTPRequest(
                url=comfy_ws_url_with_params,
                headers=ws_headers,
                connect_timeout=60,
                request_timeout=600,
                validate_cert=False
            )

            self.comfy_ws = await tornado.websocket.websocket_connect(
                request,
                ping_interval=20,
                ping_timeout=30,
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
                        # Monitorizare terminare job via WebSocket
                        if not isinstance(msg, bytes):
                            try:
                                ws_data = json.loads(msg)
                                # "executing" cu node: null înseamnă că coada e goală sau job-ul s-a terminat
                                if ws_data.get("type") == "executing" and ws_data.get("data", {}).get("node") is None:
                                    prompt_id = ws_data.get("data", {}).get("prompt_id")
                                    if prompt_id: record_job_end(prompt_id)
                                # Alternativ, mesajul "executed" confirmă finalizarea unui nod terminal
                                elif ws_data.get("type") == "executed":
                                    prompt_id = ws_data.get("data", {}).get("prompt_id")
                                    if prompt_id: record_job_end(prompt_id)
                            except:
                                pass

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
        """Trimite ping-uri pentru a menține conexiunea vie (beat la 20s pentru a preveni timeout Nginx)"""
        while self._running:
            await asyncio.sleep(20)
            if not self._running:
                break

            # Ping backend (ComfyUI)
            if self.comfy_ws:
                try:
                    self.comfy_ws.ping(b'ping')
                except:
                    pass

            # Ping client (browser via Nginx)
            try:
                if self.ws_connection and not self.ws_connection.is_closing():
                    self.ping(b'ping')
            except:
                pass

# === SIMPLE PROXY FOR STATIC FILES ===
class StaticFileProxyHandler(BaseHandler):
    async def get(self, path):
        if not is_authenticated(self):
            self.set_status(401)
            return
        
        session_id = self.current_user
        session_data = get_session(session_id)
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
        (r"/admin/api/usage-stats", AdminUsageStatsHandler),
        (r"/logout", LogoutHandler),
        (r"/user-status", UserStatusHandler),
        (r"/user-settings", UserSettingsHandler),
        
        # Chat routes
        (r"/chat-messages", ChatMessagesHandler),
        (r"/chat-users", ChatUsersListHandler),
        (r"/send-message", SendMessageHandler),
        (r"/upload-chat-file", UploadChatFileHandler),
        (r"/download-file/(.*)", DownloadFileHandler),
        (r"/mark-messages-read", MarkMessagesReadHandler),
        (r"/unread-messages-count", UnreadMessagesCountHandler),
        (r"/chat-ws", ChatWebSocketHandler),
        
        # Session routes
        (r"/health", HealthHandler),
        (r"/admin/api/terminate-session", AdminTerminateSessionHandler),
        (r"/check-session", SessionCheckHandler),
        (r"/refresh-session", SessionRefreshHandler),
        (r"/single-user-lock", SingleUserLockHandler),
        (r"/aliases", AliasListHandler),
        
        # Workflow routes
        (r"/api/workflows/list", WorkflowListHandler),
        (r"/api/workflows/load/(.*)", WorkflowLoadHandler),
        (r"/api/workflows/save", WorkflowSaveHandler),
        (r"/api/workflows/delete/(.*)", WorkflowDeleteHandler),
        (r"/api/workflows/mkdir", WorkflowMkdirHandler),
        (r"/api/workflows/rename", WorkflowRenameHandler),
        
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
    xsrf_cookies=False,
    websocket_ping_interval=20,
    websocket_ping_timeout=30,
    websocket_max_message_size=500 * 1024 * 1024
    )

def make_admin_app():
    return tornado.web.Application([
        (r"/admin/login", AdminLoginHandler),
        (r"/admin/logout", AdminLogoutHandler),
        (r"/admin/api/status", AdminStatusHandler),
        (r"/admin/api/usage-stats", AdminUsageStatsHandler),
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
        (r"/admin/api/server-settings", AdminServerSettingsHandler),
        (r"/admin/api/restart", AdminRestartHandler),
        (r"/admin/api/terminate-session", AdminTerminateSessionHandler),
        (r"/admin/api/workflow-settings", AdminWorkflowSettingsHandler),
        (r"/admin/api/chat/users", AdminChatUsersHandler),
        (r"/admin/api/chat/messages/(.*)", AdminChatMessagesHandler),
        (r"/admin/api/chat/send", AdminChatSendHandler),
        (r"/admin/api/chat/upload-file", AdminChatUploadFileHandler),
        (r"/admin/api/chat/mark-read/(.*)", AdminChatMarkReadHandler),
        (r"/download-file/(.*)", DownloadFileHandler),
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
    login_url="/admin/login",
    xsrf_cookies=False
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
        # Use config ports directly
        final_auth_port = config.get("auth_port", 7861)
        final_admin_port = config.get("admin_port", 8199)

        auth_app.listen(final_auth_port, "0.0.0.0", max_body_size=MAX_BUFFER_SIZE, max_buffer_size=MAX_BUFFER_SIZE)
        admin_app.listen(final_admin_port, "0.0.0.0", max_body_size=MAX_BUFFER_SIZE, max_buffer_size=MAX_BUFFER_SIZE)
        
        log.info(f"Auth server started on port {final_auth_port} (PROXY MODE)")
        log.info(f"Admin server started on port {final_admin_port}")
        print(f"Auth server started on port {final_auth_port} (PROXY MODE)")
        print(f"Admin interface started on port {final_admin_port}")
        print("Multi-user system active with full proxy support")
        print(f"Configuration will be saved to: {CONFIG_FILE}")
        print(f"Workflow directories will be created in: {WORKFLOW_ROOT_DIR}")
        
        loop = tornado.ioloop.IOLoop.current()
        loop.start()
        
    except Exception as e:
        log.error(f"Failed to start server: {e}")
        print(f"Failed to start server: {e}")
