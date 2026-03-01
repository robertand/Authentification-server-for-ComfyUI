#!/usr/bin/env python3
"""
Auth Server pentru ComfyUI - ADMIN INTERFACE
Cu securitate îmbunătățită și status utilizatori
"""

import tornado.ioloop
import tornado.web
import tornado.websocket
import tornado.httpclient
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
from urllib.parse import quote, unquote
from datetime import datetime, timedelta

# === LOGGING ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("AUTH")

# === CONFIGURARE ===
CONFIG_FILE = "comfyui_auth_config.json"
AUTH_PORT = 7860  # Auth server principal
ADMIN_PORT = 8199  # Admin interface

# Configurație implicită
DEFAULT_CONFIG = {
    "users": {
        "user1": {"password": "comfy.123", "comfy_url": "http://127.0.0.1:8184", "instances": 0, "max_instances": 2, "session_timeout": 60, "enabled": True},
        "user2": {"password": "comfy.123", "comfy_url": "http://127.0.0.1:8185", "instances": 0, "max_instances": 2, "session_timeout": 60, "enabled": True},
        "user3": {"password": "comfy.123", "comfy_url": "http://127.0.0.1:8186", "instances": 0, "max_instances": 2, "session_timeout": 60, "enabled": True},
        "user4": {"password": "comfy.123", "comfy_url": "http://127.0.0.1:8187", "instances": 0, "max_instances": 2, "session_timeout": 60, "enabled": True}
    },
    "admin": {
        "password": "admin123",
        "enabled": True
    },
    "workflow_root": "/mnt/prouser/spatiu/ComfyUI/workflows"  # Calea implicită pentru workflow-uri
}

# === SECURITATE - RATE LIMITING ===
FAILED_LOGIN_ATTEMPTS = {}  # {ip: {"count": 0, "reset_time": timestamp, "block_time": 0}}
MAX_ATTEMPTS_PER_5MIN = 5
BLOCK_TIME = 900  # 15 minute

# === CHAT SYSTEM ===
CHAT_MESSAGES = {}  # {username: [{"from": "admin/user", "message": "text", "timestamp": time, "read": False, "type": "text/file", "file_data": {}}]}
ADMIN_CHAT_WEBSOCKETS = set()  # WebSocket connections for admin chat
USER_CHAT_WEBSOCKETS = {}  # {username: [websocket_connections]}
USER_TYPING_STATUS = {}  # {username: {"typing": False, "last_typing_time": timestamp}}

# === FILE STORAGE FOR CHAT ===
CHAT_FILES = {}  # {file_id: {"filename": "", "data": b"", "content_type": "", "uploaded_by": "", "timestamp": 0}}
CHAT_FILES_DIR = "chat_files"  # Directory to store uploaded files

# === WORKFLOW BROWSER ===
# Variabila globală pentru folderul rădăcină al workflow-urilor
WORKFLOW_ROOT_DIR = "/mnt/prouser/spatiu/ComfyUI/workflows"  # Directorul pentru workflow-uri

# Create chat files directory if it doesn't exist
if not os.path.exists(CHAT_FILES_DIR):
    os.makedirs(CHAT_FILES_DIR, exist_ok=True)

# === STOCARE SESIUNI ===
sessions = {}
admin_sessions = {}  # Sesions separate pentru admin
DEFAULT_SESSION_TIMEOUT = 3600  # 1 oră implicită

# Instanțe externe adăugate prin admin interface
EXTERNAL_INSTANCES = {}
BLOCKED_USERS = {}  # Useri blocați temporar
FORCED_LOGOUT_SESSIONS = set()  # Sesions forțate să se delogheze

comfy_instances_ready = {}

class RateLimiter:
    @staticmethod
    def is_blocked(ip):
        if ip in FAILED_LOGIN_ATTEMPTS:
            data = FAILED_LOGIN_ATTEMPTS[ip]
            if time.time() - data["reset_time"] > 300:  # 5 minute - reset counter
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
    """Hash a password using bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(hashed_password, user_password):
    """Verify a password against its hash"""
    if not hashed_password or not user_password:
        return False
    try:
        return bcrypt.checkpw(user_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except:
        return False

def upgrade_passwords(config):
    """Upgrade plain text passwords to hashed versions"""
    for username, user_data in config["users"].items():
        if user_data["password"] and not user_data["password"].startswith("$2b$"):
            # Password is not hashed, hash it
            user_data["password"] = hash_password(user_data["password"])
            log.info(f"Upgraded password for user {username} to hashed version")
    
    # Upgrade admin password too
    if config["admin"]["password"] and not config["admin"]["password"].startswith("$2b$"):
        config["admin"]["password"] = hash_password(config["admin"]["password"])
        log.info("Upgraded admin password to hashed version")
    
    return config

# === VALIDARE INPUT ===
def validate_username(username):
    """Validate username format"""
    if not username or len(username) < 2 or len(username) > 20:
        return False
    # Allow only alphanumeric characters and underscores
    return bool(re.match(r'^[a-zA-Z0-9_]+$', username))

def validate_password(password):
    """Basic password validation"""
    if not password or len(password) < 3:
        return False
    return True

# === TEMP TOKEN FUNCTIONS ===
def generate_temp_token(session_id):
    """Generate temporary token for API calls"""
    key = "comfy_temp_key_2025"
    return hmac.new(key.encode(), (session_id + str(time.time())).encode(), hashlib.sha256).hexdigest()[:32]
# === FILE MANAGEMENT FOR CHAT ===
def save_chat_file(filename, file_data, content_type, uploaded_by):
    """Save a file uploaded through chat and return file ID"""
    file_id = str(uuid.uuid4())
    
    # Save file to disk
    filepath = os.path.join(CHAT_FILES_DIR, file_id)
    with open(filepath, 'wb') as f:
        f.write(file_data)
    
    # Store file metadata
    CHAT_FILES[file_id] = {
        "filename": filename,
        "filepath": filepath,
        "content_type": content_type,
        "uploaded_by": uploaded_by,
        "timestamp": time.time(),
        "size": len(file_data)
    }
    
    # Clean up old files (older than 24 hours)
    cleanup_old_chat_files()
    
    return file_id

def get_chat_file(file_id):
    """Get chat file data by ID"""
    if file_id in CHAT_FILES:
        file_info = CHAT_FILES[file_id]
        try:
            with open(file_info["filepath"], 'rb') as f:
                return f.read(), file_info
        except:
            return None, None
    return None, None

def cleanup_old_chat_files():
    """Remove chat files older than 24 hours"""
    current_time = time.time()
    expired_files = []
    
    for file_id, file_info in CHAT_FILES.items():
        if current_time - file_info["timestamp"] > 86400:  # 24 hours
            expired_files.append(file_id)
    
    for file_id in expired_files:
        try:
            os.remove(CHAT_FILES[file_id]["filepath"])
            del CHAT_FILES[file_id]
        except:
            pass

# === WORKFLOW FUNCTIONS ===
def get_user_workflow_dir(username):
    """Obține folderul de workflow-uri pentru un utilizator specific"""
    user_dir = os.path.join(WORKFLOW_ROOT_DIR, username)
    # Creează folderul dacă nu există
    if not os.path.exists(user_dir):
        os.makedirs(user_dir, exist_ok=True)
        log.info(f"Created workflow directory for user {username}: {user_dir}")
    return user_dir

def list_user_workflows(username):
    """Listează workflow-urile pentru un utilizator specific"""
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
    
    # Sortează după data modificării (cele mai recente primele)
    workflows.sort(key=lambda x: x['modified'], reverse=True)
    return workflows

# Încarcă configurația din fișier sau folosește cea implicită
def load_config():
    """Încarcă configurația din fișier"""
    global WORKFLOW_ROOT_DIR
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                log.info("✓ Configurație încărcată din fișier")
                
                # Upgrade passwords to hashed versions if needed
                config = upgrade_passwords(config)
                
                # Încarcă WORKFLOW_ROOT_DIR din configurație dacă există
                if "workflow_root" in config:
                    WORKFLOW_ROOT_DIR = config["workflow_root"]
                    log.info(f"✓ Workflow root directory loaded: {WORKFLOW_ROOT_DIR}")
                
                return config
        except Exception as e:
            log.error(f"Eroare la încărcarea configurației: {e}")
    
    # Dacă nu există fișierul sau apare o eroare, folosește configurația implicită
    log.info("✓ Folosind configurația implicită")
    config = DEFAULT_CONFIG.copy()
    
    # Setează WORKFLOW_ROOT_DIR din configurația implicită
    WORKFLOW_ROOT_DIR = config["workflow_root"]
    
    # Hash default passwords
    return upgrade_passwords(config)

def save_config():
    """Salvează configurația în fișier"""
    try:
        config = {
            "users": USERS,
            "admin": ADMIN_CONFIG,
            "workflow_root": WORKFLOW_ROOT_DIR
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        log.info("✓ Configurație salvată în fișier")
    except Exception as e:
        log.error(f"Eroare la salvarea configurației: {e}")

# === FUNCȚIE PENTRU CURĂȚAREA SESIUNILOR BLOCHATE ===
def cleanup_stuck_sessions():
    """Curăță sesiunile blocate care au rămas active după o oprire necurată a serverului"""
    log.info("Checking for stuck sessions...")
    
    # Reset all instance counts to 0
    for username in USERS:
        USERS[username]["instances"] = 0
    
    # Clear all sessions
    sessions.clear()
    FORCED_LOGOUT_SESSIONS.clear()
    
    # Clear all blocked users
    BLOCKED_USERS.clear()
    
    log.info("✓ Cleaned up stuck sessions and reset instance counts")

# Încarcă configurația inițială
config = load_config()
USERS = config["users"]
ADMIN_CONFIG = config["admin"]

# Curăță sesiunile blocate la pornire
cleanup_stuck_sessions()

# === HTML TEMPLATE CU ABOUT MODAL ȘI CHAT ===
ABOUT_MODAL_HTML = """
<style>
.about-modal { 
    display: none; 
    position: fixed; 
    z-index: 10001; 
    left: 0; 
    top: 0; 
    width: 100%; 
    height: 100%; 
    background-color: rgba(0,0,0,0.7); 
}
.about-modal-content { 
    background-color: #2a2a2a; 
    margin: 5% auto; 
    padding: 30px; 
    border-radius: 10px; 
    width: 500px; 
    max-height: 80vh;
    overflow-y: auto;
    text-align: center; 
    box-shadow: 0 0 20px rgba(0,0,0,0.5); 
}
.about-modal h2 { 
    color: #007bff; 
    margin-bottom: 20px; 
}
.about-modal p { 
    margin-bottom: 25px; 
    line-height: 1.6; 
    color: #fff; 
}
.about-close-btn { 
    background: #007bff; 
    color: white; 
    border: none; 
    padding: 12px 30px; 
    border-radius: 5px; 
    cursor: pointer; 
    font-size: 16px; 
}
.about-close-btn:hover { 
    background: #0056b3; 
}

/* Forced Logout Modal */
.forced-logout-modal { 
    display: none; 
    position: fixed; 
    z-index: 10002; 
    left: 0; 
    top: 0; 
    width: 100%; 
    height: 100%; 
    background-color: rgba(0,0,0,0.8); 
}
.forced-logout-modal-content { 
    background-color: #2a2a2a; 
    margin: 15% auto; 
    padding: 30px; 
    border-radius: 10px; 
    width: 500px; 
    text-align: center; 
    box-shadow: 0 0 20px rgba(0,0,0,0.5); 
    border: 2px solid #dc3545;
}
.forced-logout-modal h2 { 
    color: #dc3545; 
    margin-bottom: 20px; 
}
.forced-logout-modal p { 
    margin-bottom: 25px; 
    line-height: 1.6; 
    color: #fff; 
}
.forced-logout-btn { 
    background: #dc3545; 
    color: white; 
    border: none; 
    padding: 12px 30px; 
    border-radius: 5px; 
    cursor: pointer; 
    font-size: 16px; 
    margin: 5px;
}
.forced-logout-btn:hover { 
    background: #c82333; 
}
.forced-logout-info {
    background: #dc3545;
    color: white;
    padding: 10px;
    border-radius: 5px;
    margin: 10px 0;
}

/* Session Expiry Modal */
.session-logout-btn { 
    background: #6c757d; 
    color: white; 
    border: none; 
    padding: 12px 30px; 
    border-radius: 5px; 
    cursor: pointer; 
    font-size: 16px; 
    margin: 5px;
}
.session-logout-btn:hover { 
    background: #5a6268; 
}
.session-expiry-modal { 
    display: none; 
    position: fixed; 
    z-index: 10003; 
    left: 0; 
    top: 0; 
    width: 100%; 
    height: 100%; 
    background-color: rgba(0,0,0,0.8); 
}
.session-expiry-modal-content { 
    background-color: #2a2a2a; 
    margin: 15% auto; 
    padding: 30px; 
    border-radius: 10px; 
    width: 500px; 
    text-align: center; 
    box-shadow: 0 0 20px rgba(0,0,0,0.5); 
    border: 2px solid #ffc107;
}
.session-expiry-modal h2 { 
    color: #ffc107; 
    margin-bottom: 20px; 
}
.session-expiry-modal p { 
    margin-bottom: 25px; 
    line-height: 1.6; 
    color: #fff; 
}
.session-expiry-btn { 
    background: #ffc107; 
    color: #000; 
    border: none; 
    padding: 12px 30px; 
    border-radius: 5px; 
    cursor: pointer; 
    font-size: 16px; 
    margin: 5px;
}
.session-expiry-btn:hover { 
    background: #e0a800; 
}
.session-expiry-info {
    background: #ffc107;
    color: #000;
    padding: 10px;
    border-radius: 5px;
    margin: 10px 0;
    font-weight: bold;
}

/* Session timer - hidden by default */
.session-timer {
    display: none;
    position: fixed;
    top: 60px;
    left: 15px;
    color: #ffc107;
    background: rgba(0,0,0,0.7);
    padding: 8px 12px;
    border-radius: 5px;
    z-index: 10000;
    font-size: 12px;
    font-weight: bold;
    border: 1px solid #ffc107;
}

/* User Status Styles */
.user-status-container {
    background: #2a2a2a;
    border-radius: 10px;
    padding: 20px;
    margin: 20px 0;
    border-left: 4px solid #007bff;
}

.user-status-title {
    color: #007bff;
    margin-bottom: 15px;
    font-size: 18px;
    font-weight: bold;
}

.user-status-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 10px;
}

.user-status-item {
    background: #333;
    padding: 10px;
    border-radius: 5px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.user-status-username {
    font-weight: bold;
    color: #fff;
}

.user-status-info {
    font-size: 12px;
    color: #aaa;
}

.user-status-available {
    border-left: 3px solid #28a745;
}

.user-status-busy {
    border-left: 3px solid #ffc107;
}

.user-status-full {
    border-left: 3px solid #dc3545;
}

.user-status-offline {
    border-left: 3px solid #6c757d;
}

.user-status-disabled {
    border-left: 3px solid #6c757d;
    opacity: 0.6;
}
.user-status-disabled .user-status-username {
    color: #6c757d !important;
    text-decoration: line-through;
}

/* User Settings Modal */
.user-settings-modal { 
    display: none; 
    position: fixed; 
    z-index: 10004; 
    left: 0; 
    top: 0; 
    width: 100%; 
    height: 100%; 
    background-color: rgba(0,0,0,0.7); 
}
.user-settings-modal-content { 
    background-color: #2a2a2a; 
    margin: 10% auto; 
    padding: 30px; 
    border-radius: 10px; 
    width: 450px; 
    box-shadow: 0 0 20px rgba(0,0,0,0.5); 
    border: 2px solid #28a745;
}
.user-settings-modal h2 { 
    color: #28a745; 
    margin-bottom: 20px; 
    text-align: center;
}
.user-settings-modal .form-group { 
    margin-bottom: 15px; 
}
.user-settings-modal label { 
    display: block; 
    margin-bottom: 5px; 
    font-weight: bold; 
    color: #fff;
}
.user-settings-modal input { 
    width: 100%; 
    padding: 10px; 
    border: 1px solid #555; 
    background: #333; 
    color: #fff; 
    border-radius: 4px; 
    box-sizing: border-box;
}
.user-settings-modal-buttons { 
    display: flex; 
    justify-content: space-between; 
    margin-top: 25px; 
}
.user-settings-btn { 
    background: #28a745; 
    color: white; 
    border: none; 
    padding: 12px 25px; 
    border-radius: 5px; 
    cursor: pointer; 
    font-size: 16px;
    flex: 1;
    margin: 0 5px;
}
.user-settings-btn.cancel { 
    background: #6c757d; 
}
.user-settings-btn:hover { 
    opacity: 0.9; 
}
.user-settings-message {
    margin-top: 15px;
    padding: 10px;
    border-radius: 5px;
    text-align: center;
    display: none;
}
.user-settings-success {
    background: #28a745;
    color: white;
}
.user-settings-error {
    background: #dc3545;
    color: white;
}

/* Chat Modal Styles - Improved */
.chat-modal { 
    display: none; 
    position: fixed; 
    z-index: 10005; 
    right: 20px; 
    bottom: 20px; 
    width: 400px; 
    height: 500px; 
    background-color: #2a2a2a; 
    border-radius: 10px; 
    box-shadow: 0 0 20px rgba(0,0,0,0.5); 
    border: 2px solid #007bff;
    flex-direction: column;
}
.chat-header { 
    background: #007bff; 
    color: white; 
    padding: 15px; 
    border-radius: 8px 8px 0 0; 
    display: flex; 
    justify-content: space-between; 
    align-items: center; 
}
.chat-header h3 { 
    margin: 0; 
    font-size: 16px; 
}
.chat-close-btn { 
    background: none; 
    border: none; 
    color: white; 
    font-size: 20px; 
    cursor: pointer; 
    width: 30px;
    height: 30px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 3px;
}
.chat-close-btn:hover { 
    background: rgba(255,255,255,0.2); 
}
.chat-messages { 
    flex: 1; 
    padding: 15px; 
    overflow-y: auto; 
    display: flex; 
    flex-direction: column; 
    gap: 10px; 
}
.chat-message { 
    padding: 8px 12px; 
    border-radius: 8px; 
    max-width: 80%; 
    word-wrap: break-word; 
    cursor: pointer;
    user-select: text;
    transition: background-color 0.2s;
}
.chat-message:hover {
    background-color: rgba(255,255,255,0.05);
}
.chat-message.admin { 
    background: #007bff; 
    color: white; 
    align-self: flex-start; 
    border-bottom-left-radius: 2px; 
}
.chat-message.user { 
    background: #28a745; 
    color: white; 
    align-self: flex-end; 
    border-bottom-right-radius: 2px; 
}
.chat-message-time { 
    font-size: 10px; 
    opacity: 0.7; 
    margin-top: 2px; 
}
.chat-input-container { 
    padding: 15px; 
    border-top: 1px solid #444; 
    display: flex; 
    gap: 10px; 
    flex-direction: column;
}
.chat-input-row {
    display: flex;
    gap: 10px;
}
.chat-input { 
    flex: 1; 
    padding: 8px 12px; 
    border: 1px solid #555; 
    background: #333; 
    color: #fff; 
    border-radius: 4px; 
}
.chat-send-btn { 
    background: #007bff; 
    color: white; 
    border: none; 
    padding: 8px 15px; 
    border-radius: 4px; 
    cursor: pointer; 
}
.chat-send-btn:hover { 
    background: #0056b3; 
}
.chat-file-btn {
    background: #6c757d;
    color: white;
    border: none;
    padding: 8px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
}
.chat-file-btn:hover {
    background: #5a6268;
}
.chat-file-input {
    display: none;
}
.chat-typing-indicator {
    font-size: 11px;
    color: #aaa;
    padding: 5px 10px;
    font-style: italic;
    display: none;
}
.chat-btn { 
    position: fixed; 
    bottom: 20px; 
    right: 20px; 
    width: 50px; 
    height: 50px; 
    background: #007bff; 
    color: white; 
    border: none; 
    border-radius: 50%; 
    cursor: pointer; 
    font-size: 12px; 
    display: flex; 
    align-items: center; 
    justify-content: center; 
    box-shadow: 0 2px 10px rgba(0,0,0,0.3); 
    z-index: 10000; 
    transition: all 0.3s ease;
}
.chat-btn:hover { 
    background: #0056b3; 
}
.chat-btn.pulse {
    animation: pulse 1.5s infinite;
    box-shadow: 0 0 0 0 rgba(0, 123, 255, 0.7);
}
@keyframes pulse {
    0% {
        transform: scale(1);
        box-shadow: 0 0 0 0 rgba(0, 123, 255, 0.7);
    }
    70% {
        transform: scale(1.05);
        box-shadow: 0 0 0 10px rgba(0, 123, 255, 0);
    }
    100% {
        transform: scale(1);
        box-shadow: 0 0 0 0 rgba(0, 123, 255, 0);
    }
}
.chat-notification { 
    position: absolute; 
    top: -5px; 
    right: -5px; 
    background: #dc3545; 
    color: white; 
    border-radius: 50%; 
    width: 20px; 
    height: 20px; 
    font-size: 12px; 
    display: none; 
    align-items: center; 
    justify-content: center; 
    animation: bounce 1s infinite;
}
@keyframes bounce {
    0%, 100% { transform: scale(1); }
    50% { transform: scale(1.2); }
}

/* File message styles */
.chat-file-message {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px;
    background: rgba(255,255,255,0.1);
    border-radius: 5px;
    margin-top: 5px;
}
.chat-file-icon {
    font-size: 16px;
}
.chat-file-name {
    flex: 1;
    font-size: 12px;
    word-break: break-all;
}
.chat-file-download {
    background: #28a745;
    color: white;
    border: none;
    padding: 4px 8px;
    border-radius: 3px;
    cursor: pointer;
    font-size: 10px;
    text-decoration: none;
}
.chat-file-download:hover {
    background: #218838;
}

/* Workflow Browser Styles - Improved */
.workflow-modal { 
    display: none; 
    position: fixed; 
    z-index: 10006; 
    left: 0; 
    top: 0; 
    width: 100%; 
    height: 100%; 
    background-color: rgba(0,0,0,0.7); 
}
.workflow-modal-content { 
    background-color: #2a2a2a; 
    margin: 5% auto; 
    padding: 20px; 
    border-radius: 10px; 
    width: 700px; 
    max-height: 80vh;
    display: flex;
    flex-direction: column;
}
.workflow-header { 
    display: flex; 
    justify-content: space-between; 
    align-items: center; 
    margin-bottom: 15px;
    border-bottom: 1px solid #444;
    padding-bottom: 10px;
}
.workflow-header h2 { 
    color: #007bff; 
    margin: 0;
}
.workflow-close-btn { 
    background: none; 
    border: none; 
    color: white; 
    font-size: 20px; 
    cursor: pointer; 
    width: 30px;
    height: 30px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 3px;
}
.workflow-close-btn:hover { 
    background: rgba(255,255,255,0.2); 
}
.workflow-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 15px;
    padding: 10px;
    background: #333;
    border-radius: 5px;
}
.workflow-info {
    font-size: 12px;
    color: #aaa;
}
.workflow-refresh-btn {
    background: #28a745;
    color: white;
    border: none;
    padding: 8px 15px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 14px;
}
.workflow-refresh-btn:hover {
    background: #218838;
}
.workflow-list { 
    flex: 1; 
    overflow-y: auto; 
    background: #333; 
    border-radius: 5px; 
    padding: 10px;
    margin-bottom: 15px;
    min-height: 300px;
}
.workflow-item { 
    padding: 12px; 
    margin: 5px 0; 
    background: #444; 
    border-radius: 4px; 
    cursor: pointer; 
    display: flex;
    justify-content: space-between;
    align-items: center;
    transition: background 0.2s;
}
.workflow-item:hover { 
    background: #555; 
}
.workflow-item.selected { 
    background: #007bff; 
}
.workflow-item-info {
    display: flex;
    flex-direction: column;
    flex: 1;
}
.workflow-item-name {
    font-weight: bold;
    margin-bottom: 4px;
}
.workflow-item-details {
    font-size: 11px;
    color: #aaa;
    display: flex;
    gap: 15px;
}
.workflow-actions { 
    display: flex; 
    gap: 10px; 
    justify-content: flex-end;
}
.workflow-delete-btn {
    background: #dc3545;
    color: white;
    border: none;
    padding: 5px 10px;
    border-radius: 3px;
    cursor: pointer;
    font-size: 11px;
}
.workflow-delete-btn:hover {
    background: #c82333;
}
.workflow-delete-btn:disabled {
    background: #6c757d;
    cursor: not-allowed;
}

/* MODIFICARE: Butoanele de workflow plate cu animație */
.workflow-btn { 
    position: fixed; 
    bottom: 80px; 
    left: 100px;
    background: #3a3a3a; 
    color: #007bff; 
    border: none; 
    border-radius: 3px; 
    cursor: pointer; 
    font-size: 12px; 
    font-weight: 500;
    display: flex; 
    align-items: center; 
    justify-content: center; 
    box-shadow: 0 1px 3px rgba(0,0,0,0.3); 
    z-index: 10000; 
    padding: 8px 12px;
    height: 32px;
    transition: all 0.2s ease;
    letter-spacing: 0.5px;
}
.workflow-btn:hover { 
    background: #007bff; 
    color: white;
    transform: scale(1.05);
    box-shadow: 0 3px 8px rgba(0, 123, 255, 0.3);
}
.workflow-save-btn {
    position: fixed;
    bottom: 120px;
    left: 100px;
    background: #3a3a3a;
    color: #17a2b8;
    border: none;
    border-radius: 3px;
    cursor: pointer;
    font-size: 12px;
    font-weight: 500;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 1px 3px rgba(0,0,0,0.3);
    z-index: 10000;
    padding: 8px 12px;
    height: 32px;
    transition: all 0.2s ease;
    letter-spacing: 0.5px;
}
.workflow-save-btn:hover {
    background: #17a2b8;
    color: white;
    transform: scale(1.05);
    box-shadow: 0 3px 8px rgba(23, 162, 184, 0.3);
}
.workflow-empty {
    text-align: center;
    padding: 40px;
    color: #666;
    font-style: italic;
}

/* File upload preview */
.file-preview {
    background: #333;
    padding: 10px;
    border-radius: 5px;
    margin: 10px 0;
    display: none;
}
.file-preview-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 5px;
    margin: 5px 0;
}
.file-preview-name {
    flex: 1;
    font-size: 12px;
}
.file-preview-remove {
    background: #dc3545;
    color: white;
    border: none;
    padding: 2px 6px;
    border-radius: 3px;
    cursor: pointer;
    font-size: 10px;
}

/* About Drawer Styles */
.about-drawer {
    position: fixed;
    top: 0;
    right: -600px;
    width: 600px;
    height: 100%;
    background: #1e1e1e;
    z-index: 10010;
    transition: right 0.4s cubic-bezier(0.4, 0, 0.2, 1);
    box-shadow: -5px 0 25px rgba(0,0,0,0.5);
    overflow-y: auto;
}

.about-drawer.open {
    right: 0;
}

.about-drawer-content {
    padding: 30px;
    color: #e0e0e0;
}

.about-drawer h2 {
    color: #007bff;
    margin-bottom: 25px;
    font-size: 24px;
    border-bottom: 2px solid #007bff;
    padding-bottom: 10px;
}

.about-drawer h3 {
    color: #007bff;
    margin: 25px 0 15px 0;
    font-size: 18px;
}

.about-drawer ul {
    margin: 15px 0;
    padding-left: 20px;
}

.about-drawer li {
    margin-bottom: 8px;
    line-height: 1.5;
}

.about-drawer-close {
    position: absolute;
    top: 15px;
    right: 15px;
    background: #007bff;
    color: white;
    border: none;
    width: 30px;
    height: 30px;
    border-radius: 50%;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
}

.about-drawer-close:hover {
    background: #0056b3;
}

.login-container-shifted {
    transform: translateX(-300px);
    transition: transform 0.4s cubic-bezier(0.4, 0, 0.2, 1);
}
</style>

<!-- About Modal -->
<div id="aboutModal" class="about-modal">
    <div class="about-modal-content">
        <h2>ComfyUI Auth Server v0.3.1</h2>
        <p><strong>Advanced management and authentication system for multiple ComfyUI instances</strong></p>
        
        <h3>Key Features and Advantages:</h3>
        <ul style="text-align: left; margin: 20px 0;">
            <li><strong>Multi-User Architecture</strong> - Simultaneous management of multiple users with separate instances</li>
            <li><strong>Intelligent Load Balancing</strong> - Automatic workload distribution between ComfyUI instances</li>
            <li><strong>Enhanced Security</strong> - Rate limiting, bcrypt password hashing, customizable session timeouts</li>
            <li><strong>Advanced Admin Interface</strong> - Real-time monitoring, session management, full control</li>
            <li><strong>Integrated Chat System</strong> - Direct communication between users and administrators</li>
            <li><strong>Personalized Workflow Browser</strong> - Each user has their own workspace for workflows</li>
            <li><strong>Session Management</strong> - Customizable timeout, expiry notifications, session renewal</li>
            <li><strong>Performance Monitoring</strong> - Real-time instance status, maximum capacity alerts</li>
            <li><strong>Backup & Restore</strong> - Automatic configuration saving, easy recovery</li>
            <li><strong>Scalability</strong> - Easy addition of new users and resources</li>
        </ul>

        <h3>Security Features:</h3>
        <ul style="text-align: left; margin: 15px 0;">
            <li>Authentication with hashed passwords (bcrypt)</li>
            <li>Protection against brute-force attacks</li>
            <li>Configurable session timeout per user</li>
            <li>Temporary user blocking for security</li>
            <li>Suspicious IP monitoring</li>
        </ul>

        <h3>For Administrators:</h3>
        <ul style="text-align: left; margin: 15px 0;">
            <li>Real-time statistics dashboard</li>
            <li>Complete user management</li>
            <li>Flexible instance configuration</li>
            <li>Chat system with users</li>
            <li>Active session control</li>
            <li>Workflow directory configuration</li>
        </ul>

        <div style="background: #2a2a2a; border-left: 4px solid #007bff; padding: 15px; margin: 20px 0;">
            <h4>Main Benefits:</h4>
            <p><strong>Resource Optimization</strong> - Balanced distribution of computing load<br>
            <strong>User Isolation</strong> - Each user works in a secure space<br>
            <strong>Easy Management</strong> - Single interface for all instances<br>
            <strong>Scalability</strong> - Always add new users and resources</p>
        </div>

        <p><strong>Version:</strong> 0.3.1 - Report issues through the chat system to administrators</p>
        
        <p><em>Version 0.3.1 - In intensive testing phase<br>
        Report any issues or improvement suggestions through the chat system!</em></p>
        <button class="about-close-btn" onclick="closeAboutModal()">Understood</button>
    </div>
</div>

<!-- About Drawer -->
<div id="aboutDrawer" class="about-drawer">
    <button class="about-drawer-close" onclick="closeAboutDrawer()">×</button>
    <div class="about-drawer-content">
        <h2>ComfyUI Auth Server v0.3.1</h2>
        <p><strong>Advanced management and authentication system for multiple ComfyUI instances</strong></p>
        
        <h3>Key Features and Advantages:</h3>
        <ul>
            <li><strong>Multi-User Architecture</strong> - Simultaneous management of multiple users with separate instances</li>
            <li><strong>Intelligent Load Balancing</strong> - Automatic workload distribution between ComfyUI instances</li>
            <li><strong>Enhanced Security</strong> - Rate limiting, bcrypt password hashing, customizable session timeouts</li>
            <li><strong>Advanced Admin Interface</strong> - Real-time monitoring, session management, full control</li>
            <li><strong>Integrated Chat System</strong> - Direct communication between users and administrators</li>
            <li><strong>Personalized Workflow Browser</strong> - Each user has their own workspace for workflows</li>
            <li><strong>Session Management</strong> - Customizable timeout, expiry notifications, session renewal</li>
            <li><strong>Performance Monitoring</strong> - Real-time instance status, maximum capacity alerts</li>
            <li><strong>Backup & Restore</strong> - Automatic configuration saving, easy recovery</li>
            <li><strong>Scalability</strong> - Easy addition of new users and resources</li>
        </ul>

        <h3>Security Features:</h3>
        <ul>
            <li>Authentication with hashed passwords (bcrypt)</li>
            <li>Protection against brute-force attacks</li>
            <li>Configurable session timeout per user</li>
            <li>Temporary user blocking for security</li>
            <li>Suspicious IP monitoring</li>
        </ul>

        <h3>For Administrators:</h3>
        <ul>
            <li>Real-time statistics dashboard</li>
            <li>Complete user management</li>
            <li>Flexible instance configuration</li>
            <li>Chat system with users</li>
            <li>Active session control</li>
            <li>Workflow directory configuration</li>
        </ul>

        <div style="background: #2a2a2a; border-left: 4px solid #007bff; padding: 15px; margin: 20px 0;">
            <h4>Main Benefits:</h4>
            <p><strong>Resource Optimization</strong> - Balanced distribution of computing load<br>
            <strong>User Isolation</strong> - Each user works in a secure space<br>
            <strong>Easy Management</strong> - Single interface for all instances<br>
            <strong>Scalability</strong> - Always add new users and resources</p>
        </div>

        <p><strong>Version:</strong> 0.3.1 - Report issues through the chat system to administrators</p>
        
        <p><em>Version 0.3.1 - In intensive testing phase<br>
        Report any issues or improvement suggestions through the chat system!</em></p>
    </div>
</div>

<!-- Forced Logout Modal -->
<div id="forcedLogoutModal" class="forced-logout-modal">
    <div class="forced-logout-modal-content">
        <h2>Session Terminated</h2>
        <div class="forced-logout-info">
            <p><strong>Your session has been terminated by an administrator.</strong></p>
        </div>
        <p>You have been logged out of your ComfyUI session by an administrator. This may be due to maintenance, security reasons, or resource management.</p>
        <p>You will be redirected to the login page.</p>
        <button class="forced-logout-btn" onclick="redirectToLogin()">OK, Redirect to Login</button>
    </div>
</div>

<!-- Session Expiry Modal -->
<div id="sessionExpiryModal" class="session-expiry-modal">
    <div class="session-expiry-modal-content">
        <h2>Session Expiring Soon</h2>
        <div class="session-expiry-info">
            <p><strong>Your session will expire in <span id="expiryCountdown">60</span> seconds</strong></p>
        </div>
        <p>Your ComfyUI session is about to expire due to inactivity. Please save your work.</p>
        <p>You will be automatically redirected to the login page when the session expires.</p>
        <button class="session-expiry-btn" onclick="continueSession()">Continue Session (+60 min)</button>
        <button class="session-logout-btn" onclick="logoutNow()">Logout Now</button>
    </div>
</div>

<!-- User Settings Modal -->
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
                <label for="settingsNewPassword">New Password (leave empty to keep current):</label>
                <input type="password" id="settingsNewPassword" placeholder="Enter new password">
            </div>
            <div class="form-group">
                <label for="settingsConfirmPassword">Confirm New Password:</label>
                <input type="password" id="settingsConfirmPassword" placeholder="Confirm new password">
            </div>
            
            <div id="userSettingsMessage" class="user-settings-message"></div>
            
            <div class="user-settings-modal-buttons">
                <button type="button" class="user-settings-btn cancel" onclick="closeUserSettingsModal()">Cancel</button>
                <button type="submit" class="user-settings-btn">Save Changes</button>
            </div>
        </form>
    </div>
</div>

<!-- Chat Modal -->
<div id="chatModal" class="chat-modal">
    <div class="chat-header">
        <h3>Chat with Admin</h3>
        <button class="chat-close-btn" onclick="closeChatModal()">X</button>
    </div>
    <div class="chat-messages" id="chatMessages">
        <!-- Messages will be loaded here -->
    </div>
    <div class="chat-input-container">
        <div class="chat-typing-indicator" id="typingIndicator"></div>
        <div class="file-preview" id="filePreview"></div>
        <div class="chat-input-row">
            <input type="text" class="chat-input" id="chatInput" placeholder="Type your message..." oninput="handleTyping()">
            <button class="chat-send-btn" onclick="sendChatMessage()">Send</button>
        </div>
        <div class="chat-input-row">
            <input type="file" class="chat-file-input" id="chatFileInput" multiple accept="*/*">
            <button class="chat-file-btn" onclick="document.getElementById('chatFileInput').click()">Attach Files</button>
        </div>
    </div>
</div>

<!-- Workflow Browser Modal - Improved -->
<div id="workflowBrowserModal" class="workflow-modal">
    <div class="workflow-modal-content">
        <div class="workflow-header">
            <h2>Workflow Browser - <span id="currentUserFolder"></span></h2>
            <button class="workflow-close-btn" onclick="closeWorkflowBrowser()">X</button>
        </div>
        
        <div class="workflow-toolbar">
            <div class="workflow-info" id="workflowInfo">
                Loading your workflow files...
            </div>
            <button class="workflow-refresh-btn" onclick="loadWorkflowList()">Refresh</button>
        </div>
        
        <div class="workflow-list" id="workflowList">
            <!-- Workflow files will be loaded here -->
        </div>
        
        <div class="workflow-actions">
            <button onclick="loadSelectedWorkflow()" style="background: #007bff;">Load Selected Workflow</button>
            <button onclick="closeWorkflowBrowser()" style="background: #6c757d;">Cancel</button>
        </div>
    </div>
</div>

<!-- Save Workflow Button - buton plat cu animație -->
<button class="workflow-save-btn" onclick="saveCurrentWorkflow()" title="Save Workflow">
    SAVW
</button>

<!-- Workflow Browser Button - buton plat cu animație -->
<button class="workflow-btn" onclick="openWorkflowBrowser()" title="Workflow Browser">
    LODW
</button>

<!-- Chat Button - rămâne pe dreapta -->
<button class="chat-btn" onclick="toggleChatModal()" id="chatButton">
    MSG
    <div class="chat-notification" id="chatNotification">!</div>
</button>

<script>
// Global variables for session management
let sessionCheckInterval;
let timerUpdateInterval;
let expiryCountdownInterval;
let chatWebSocket = null;
let hasUnreadMessages = false;
let chatAutoRefreshInterval = null;
let selectedWorkflow = null;
let typingTimer = null;
let isTyping = false;
let attachedFiles = [];

function openAboutModal() {
    document.getElementById('aboutModal').style.display = 'block';
}

function closeAboutModal() {
    document.getElementById('aboutModal').style.display = 'none';
}

function openAboutDrawer() {
    document.getElementById('aboutDrawer').classList.add('open');
    document.querySelector('.container').classList.add('login-container-shifted');
}

function closeAboutDrawer() {
    document.getElementById('aboutDrawer').classList.remove('open');
    document.querySelector('.container').classList.remove('login-container-shifted');
}

function openForcedLogoutModal() {
    document.getElementById('forcedLogoutModal').style.display = 'block';
}

function openSessionExpiryModal() {
    document.getElementById('sessionExpiryModal').style.display = 'block';
    startExpiryCountdown();
}

function closeSessionExpiryModal() {
    document.getElementById('sessionExpiryModal').style.display = 'none';
    if (expiryCountdownInterval) {
        clearInterval(expiryCountdownInterval);
    }
}

function redirectToLogin() {
    window.location.href = '/login';
}

function logoutNow() {
    // Închide modal-ul de expirare sesiune
    closeSessionExpiryModal();
    
    // Face logout direct
    window.location.href = '/logout';
}

function continueSession() {
    // Refresh the session by making a request
    fetch('/refresh-session', {
        method: 'POST',
        credentials: 'include'
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            closeSessionExpiryModal();
            // Reset session status checking
            checkSessionStatus();
            // Show success message
            alert('✓ Session extended by 60 minutes!');
            
            // Force a status check to update the timer
            setTimeout(checkSessionStatus, 1000);
        } else {
            alert('Error extending session: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(error => {
        console.log('Session refresh error:', error);
        alert('Network error extending session');
    });
}

function closeSessionExpiryModal() {
    const modal = document.getElementById('sessionExpiryModal');
    if (modal) {
        modal.style.display = 'none';
    }
    if (expiryCountdownInterval) {
        clearInterval(expiryCountdownInterval);
        expiryCountdownInterval = null;
    }
}
function startExpiryCountdown() {
    let timeLeft = 60;
    const countdownElement = document.getElementById('expiryCountdown');
    
    if (expiryCountdownInterval) {
        clearInterval(expiryCountdownInterval);
    }
    
    expiryCountdownInterval = setInterval(() => {
        timeLeft--;
        countdownElement.textContent = timeLeft;
        
        if (timeLeft <= 0) {
            clearInterval(expiryCountdownInterval);
            redirectToLogin();
        }
    }, 1000);
}

// User Settings Modal Functions
function openUserSettingsModal() {
    // Pre-populează username-ul curent
    const usernameElement = document.querySelector('.comfy-user-info');
    const currentUsername = usernameElement ? usernameElement.textContent.replace('Welcome, ', '') : '';
    
    document.getElementById('settingsUsername').value = currentUsername;
    document.getElementById('settingsCurrentPassword').value = '';
    document.getElementById('settingsNewPassword').value = '';
    document.getElementById('settingsConfirmPassword').value = '';
    document.getElementById('userSettingsMessage').style.display = 'none';
    
    document.getElementById('userSettingsModal').style.display = 'block';
}

function closeUserSettingsModal() {
    document.getElementById('userSettingsModal').style.display = 'none';
    document.getElementById('userSettingsForm').reset();
}

function saveUserSettings() {
    const username = document.getElementById('settingsUsername').value;
    const currentPassword = document.getElementById('settingsCurrentPassword').value;
    const newPassword = document.getElementById('settingsNewPassword').value;
    const confirmPassword = document.getElementById('settingsConfirmPassword').value;
    const messageDiv = document.getElementById('userSettingsMessage');

    // Reset message
    messageDiv.style.display = 'none';
    messageDiv.className = 'user-settings-message';

    // Validare
    if (!username || !currentPassword) {
        showUserSettingsMessage('Username and current password are required!', 'error');
        return;
    }

    if (newPassword && newPassword !== confirmPassword) {
        showUserSettingsMessage('New passwords do not match!', 'error');
        return;
    }

    if (newPassword && newPassword.length < 3) {
        showUserSettingsMessage('New password must be at least 3 characters!', 'error');
        return;
    }

    // Trimite cererea către server
    fetch('/user-settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            username: username,
            current_password: currentPassword,
            new_password: newPassword
        }),
        credentials: 'include'
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showUserSettingsMessage('✓ Settings updated successfully!', 'success');
            // Actualizează afișajul username-ului
            const usernameElement = document.querySelector('.comfy-user-info');
            if (usernameElement) {
                usernameElement.textContent = `Welcome, ${username}`;
            }
            // Resetează formularul după 2 secunde
            setTimeout(() => {
                closeUserSettingsModal();
            }, 2000);
        } else {
            showUserSettingsMessage(data.error || 'Error updating settings!', 'error');
        }
    })
    .catch(error => {
        showUserSettingsMessage('Network error: ' + error, 'error');
    });
}

function showUserSettingsMessage(message, type) {
    const messageDiv = document.getElementById('userSettingsMessage');
    messageDiv.textContent = message;
    messageDiv.className = `user-settings-message user-settings-${type}`;
    messageDiv.style.display = 'block';
}

// Chat Functions - Improved
function toggleChatModal() {
    const chatModal = document.getElementById('chatModal');
    if (chatModal.style.display === 'flex') {
        closeChatModal();
    } else {
        openChatModal();
    }
}

function openChatModal() {
    const chatModal = document.getElementById('chatModal');
    chatModal.style.display = 'flex';
    // Clear notification when opening chat
    hasUnreadMessages = false;
    updateChatNotification();
    // Load chat messages
    loadChatMessages();
    // Connect to WebSocket if not already connected
    connectChatWebSocket();
    
    // Start auto-refresh for chat messages
    startChatAutoRefresh();
    
    // Mark messages as read when opening chat
    markMessagesAsRead();
}

function closeChatModal() {
    const chatModal = document.getElementById('chatModal');
    chatModal.style.display = 'none';
    
    // Stop auto-refresh when chat is closed
    stopChatAutoRefresh();
    
    // Stop typing when closing chat
    stopTyping();
    
    // Clear attached files
    attachedFiles = [];
    updateFilePreview();
}

function connectChatWebSocket() {
    if (chatWebSocket && chatWebSocket.readyState === WebSocket.OPEN) {
        return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/chat-ws`;
    
    chatWebSocket = new WebSocket(wsUrl);
    
    chatWebSocket.onopen = function() {
        console.log('Chat WebSocket connected');
    };
    
    chatWebSocket.onmessage = function(event) {
        const data = JSON.parse(event.data);
        if (data.type === 'new_message') {
            addMessageToChat(data.message, data.from, data.timestamp, data.message_type, data.file_data);
            // Show notification if chat is closed
            if (document.getElementById('chatModal').style.display !== 'flex') {
                hasUnreadMessages = true;
                updateChatNotification();
            }
        } else if (data.type === 'message_sent') {
            // Message sent successfully, no need to do anything
        } else if (data.type === 'user_typing') {
            showTypingIndicator(data.username, data.typing);
        } else if (data.type === 'unread_count') {
            updateUnreadCount(data.count);
        }
    };
    
    chatWebSocket.onclose = function() {
        console.log('Chat WebSocket disconnected');
        // Try to reconnect after 5 seconds
        setTimeout(connectChatWebSocket, 5000);
    };
    
    chatWebSocket.onerror = function(error) {
        console.log('Chat WebSocket error:', error);
    };
}

function loadChatMessages() {
    fetch('/chat-messages', {
        method: 'GET',
        credentials: 'include'
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            const chatMessages = document.getElementById('chatMessages');
            chatMessages.innerHTML = '';
            data.messages.forEach(msg => {
                addMessageToChat(msg.message, msg.from, msg.timestamp, msg.message_type, msg.file_data, false);
            });
            scrollChatToBottom();
            
            // Update unread count
            updateUnreadCount(data.unread_count || 0);
        }
    })
    .catch(error => {
        console.log('Error loading chat messages:', error);
    });
}

function addMessageToChat(message, from, timestamp, message_type = 'text', file_data = null, shouldScroll = true) {
    const chatMessages = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `chat-message ${from === 'admin' ? 'admin' : 'user'}`;
    
    const time = new Date(timestamp * 1000).toLocaleTimeString();
    
    let messageContent = message;
    if (message_type === 'file' && file_data) {
        messageContent = `
            <div>${message}</div>
            <div class="chat-file-message">
                <span class="chat-file-icon">📎</span>
                <span class="chat-file-name">${file_data.filename}</span>
                <a href="/download-file/${file_data.id}" class="chat-file-download" download="${file_data.filename}">Download</a>
            </div>
        `;
    }
    
    messageDiv.innerHTML = `
        <div>${messageContent}</div>
        <div class="chat-message-time">${from === 'admin' ? 'Admin' : 'You'} • ${time}</div>
    `;
    
    // Add copy to clipboard functionality
    messageDiv.onclick = function() {
        copyToClipboard(message);
    };
    
    chatMessages.appendChild(messageDiv);
    
    if (shouldScroll) {
        scrollChatToBottom();
    }
}

function scrollChatToBottom() {
    const chatMessages = document.getElementById('chatMessages');
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(function() {
        // Show temporary feedback
        const originalColor = event.target.style.backgroundColor;
        event.target.style.backgroundColor = '#28a745';
        setTimeout(() => {
            event.target.style.backgroundColor = originalColor;
        }, 300);
    }).catch(function(err) {
        console.error('Failed to copy text: ', err);
        // Fallback for older browsers
        const textArea = document.createElement('textarea');
        textArea.value = text;
        document.body.appendChild(textArea);
        textArea.select();
        try {
            document.execCommand('copy');
            const originalColor = event.target.style.backgroundColor;
            event.target.style.backgroundColor = '#28a745';
            setTimeout(() => {
                event.target.style.backgroundColor = originalColor;
            }, 300);
        } catch (err) {
            console.error('Fallback copy failed: ', err);
        }
        document.body.removeChild(textArea);
    });
}

function sendChatMessage() {
    const chatInput = document.getElementById('chatInput');
    const message = chatInput.value.trim();
    
    if (!message && attachedFiles.length === 0) return;
    
    // Upload files first if any
    if (attachedFiles.length > 0) {
        uploadFiles(message);
    } else {
        sendTextMessage(message);
    }
}

function sendTextMessage(message) {
    if (chatWebSocket && chatWebSocket.readyState === WebSocket.OPEN) {
        chatWebSocket.send(JSON.stringify({
            type: 'send_message',
            message: message,
            message_type: 'text'
        }));
        // Add message immediately to chat for better UX
        addMessageToChat(message, 'user', Date.now() / 1000);
        document.getElementById('chatInput').value = '';
        scrollChatToBottom();
        stopTyping();
    } else {
        // Fallback to HTTP if WebSocket is not available
        fetch('/send-message', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                message: message,
                message_type: 'text'
            }),
            credentials: 'include'
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                document.getElementById('chatInput').value = '';
                // Reload messages to show the new one
                loadChatMessages();
                stopTyping();
            }
        })
        .catch(error => {
            console.log('Error sending message:', error);
        });
    }
}

function uploadFiles(message) {
    const formData = new FormData();
    formData.append('message', message);
    
    attachedFiles.forEach((file, index) => {
        formData.append(`file${index}`, file);
    });
    
    fetch('/upload-chat-file', {
        method: 'POST',
        body: formData,
        credentials: 'include'
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Clear input and attached files
            document.getElementById('chatInput').value = '';
            attachedFiles = [];
            updateFilePreview();
            
            // Reload messages to show the new one with files
            loadChatMessages();
            stopTyping();
        } else {
            alert('Error uploading files: ' + data.error);
        }
    })
    .catch(error => {
        console.log('Error uploading files:', error);
        alert('Error uploading files: ' + error);
    });
}

function handleFileSelection() {
    const fileInput = document.getElementById('chatFileInput');
    const files = fileInput.files;
    
    for (let i = 0; i < files.length; i++) {
        attachedFiles.push(files[i]);
    }
    
    updateFilePreview();
    fileInput.value = ''; // Reset file input
}

function updateFilePreview() {
    const preview = document.getElementById('filePreview');
    
    if (attachedFiles.length === 0) {
        preview.style.display = 'none';
        preview.innerHTML = '';
        return;
    }
    
    preview.style.display = 'block';
    preview.innerHTML = '<strong>Attached files:</strong>';
    
    attachedFiles.forEach((file, index) => {
        const fileItem = document.createElement('div');
        fileItem.className = 'file-preview-item';
        fileItem.innerHTML = `
            <span class="file-preview-name">${file.name} (${formatFileSize(file.size)})</span>
            <button class="file-preview-remove" onclick="removeAttachedFile(${index})">Remove</button>
        `;
        preview.appendChild(fileItem);
    });
}

function removeAttachedFile(index) {
    attachedFiles.splice(index, 1);
    updateFilePreview();
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function handleTyping() {
    if (!isTyping) {
        isTyping = true;
        if (chatWebSocket && chatWebSocket.readyState === WebSocket.OPEN) {
            chatWebSocket.send(JSON.stringify({
                type: 'typing',
                typing: true
            }));
        }
    }
    
    // Clear existing timer
    if (typingTimer) {
        clearTimeout(typingTimer);
    }
    
    // Set timer to stop typing indicator after 2 seconds
    typingTimer = setTimeout(stopTyping, 2000);
}

function stopTyping() {
    isTyping = false;
    if (typingTimer) {
        clearTimeout(typingTimer);
    }
    
    if (chatWebSocket && chatWebSocket.readyState === WebSocket.OPEN) {
        chatWebSocket.send(JSON.stringify({
            type: 'typing',
            typing: false
        }));
    }
}

function showTypingIndicator(username, typing) {
    const indicator = document.getElementById('typingIndicator');
    if (typing) {
        indicator.textContent = `${username} is typing...`;
        indicator.style.display = 'block';
    } else {
        indicator.style.display = 'none';
    }
    scrollChatToBottom();
}

function updateChatNotification() {
    const notification = document.getElementById('chatNotification');
    const chatButton = document.getElementById('chatButton');
    
    if (hasUnreadMessages) {
        notification.style.display = 'flex';
        chatButton.classList.add('pulse');
    } else {
        notification.style.display = 'none';
        chatButton.classList.remove('pulse');
    }
}

function updateUnreadCount(count) {
    const notification = document.getElementById('chatNotification');
    hasUnreadMessages = count > 0;
    
    if (count > 0) {
        notification.textContent = count > 9 ? '9+' : count;
        notification.style.display = 'flex';
        document.getElementById('chatButton').classList.add('pulse');
    } else {
        notification.style.display = 'none';
        document.getElementById('chatButton').classList.remove('pulse');
    }
}

function markMessagesAsRead() {
    if (chatWebSocket && chatWebSocket.readyState === WebSocket.OPEN) {
        chatWebSocket.send(JSON.stringify({
            type: 'mark_read'
        }));
    } else {
        fetch('/mark-messages-read', {
            method: 'POST',
            credentials: 'include'
        });
    }
    hasUnreadMessages = false;
    updateChatNotification();
}

function startChatAutoRefresh() {
    // Refresh chat every 3 seconds when chat is open
    if (chatAutoRefreshInterval) {
        clearInterval(chatAutoRefreshInterval);
    }
    chatAutoRefreshInterval = setInterval(loadChatMessages, 3000);
}

function stopChatAutoRefresh() {
    if (chatAutoRefreshInterval) {
        clearInterval(chatAutoRefreshInterval);
        chatAutoRefreshInterval = null;
    }
}

// Workflow Browser Functions - Improved
function openWorkflowBrowser() {
    document.getElementById('workflowBrowserModal').style.display = 'block';
    loadWorkflowList();
}

function closeWorkflowBrowser() {
    document.getElementById('workflowBrowserModal').style.display = 'none';
    selectedWorkflow = null;
}

function loadWorkflowList() {
    const workflowList = document.getElementById('workflowList');
    const workflowInfo = document.getElementById('workflowInfo');
    
    workflowList.innerHTML = '<div class="workflow-empty">Loading workflows...</div>';
    
    fetch('/api/workflows/list')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                workflowList.innerHTML = '';
                document.getElementById('currentUserFolder').textContent = data.user_directory;
                
                if (data.workflows.length === 0) {
                    workflowList.innerHTML = '<div class="workflow-empty">No workflow files found. Save your first workflow using the SAVW button!</div>';
                    workflowInfo.textContent = 'No workflow files found';
                } else {
                    data.workflows.forEach(workflow => {
                        const item = document.createElement('div');
                        item.className = 'workflow-item';
                        item.innerHTML = `
                            <div class="workflow-item-info">
                                <div class="workflow-item-name">${workflow.name}</div>
                                <div class="workflow-item-details">
                                    <span>Modified: ${new Date(workflow.modified * 1000).toLocaleString()}</span>
                                    <span>Size: ${(workflow.size / 1024).toFixed(2)} KB</span>
                                </div>
                            </div>
                            <div class="workflow-actions">
                                <button class="workflow-delete-btn" onclick="deleteWorkflow('${workflow.name}', this)">Delete</button>
                            </div>
                        `;
                        item.onclick = (e) => {
                            // Don't select when clicking delete button
                            if (!e.target.classList.contains('workflow-delete-btn')) {
                                selectWorkflow(workflow, item);
                            }
                        };
                        workflowList.appendChild(item);
                    });
                    
                    workflowInfo.textContent = `Found ${data.workflows.length} workflow file(s)`;
                }
            } else {
                workflowList.innerHTML = '<div class="workflow-empty">Error loading workflows: ' + data.error + '</div>';
                workflowInfo.textContent = 'Error loading workflows';
            }
        })
        .catch(error => {
            console.error('Error loading workflows:', error);
            workflowList.innerHTML = '<div class="workflow-empty">Error loading workflows. Please check console.</div>';
            workflowInfo.textContent = 'Error loading workflows';
        });
}

function selectWorkflow(workflow, element) {
    // Remove previous selection
    document.querySelectorAll('.workflow-item').forEach(item => {
        item.classList.remove('selected');
    });
    
    // Select new one
    element.classList.add('selected');
    selectedWorkflow = workflow;
}

function loadSelectedWorkflow() {
    if (!selectedWorkflow) {
        alert('Please select a workflow first');       
        return;
    }
    
    fetch(`/api/workflows/load/${encodeURIComponent(selectedWorkflow.name)}`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Inject workflow into ComfyUI
                if (window.app && window.app.graph) {
                    window.app.loadGraphData(data.workflow);
                    closeWorkflowBrowser();
                    alert('Workflow loaded successfully!');
                } else {
                    alert('Workflow loaded successfully! Please inject it manually.');
                }
            } else {
                alert('Error loading workflow: ' + data.error);
            }
        })
        .catch(error => {
            console.error('Error loading workflow:', error);
            alert('Error loading workflow: ' + error);
        });
}

function deleteWorkflow(filename, button) {
    if (!confirm(`Are you sure you want to delete "${filename}"? This action cannot be undone!`)) {
        return;
    }
    
    // Show loading state
    button.textContent = 'Deleting...';
    button.disabled = true;
    
    fetch(`/api/workflows/delete/${encodeURIComponent(filename)}`, {
        method: 'DELETE'
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('✓ Workflow deleted successfully!');
            loadWorkflowList(); // Refresh the list
        } else {
            alert('Error deleting workflow: ' + data.error);
            button.textContent = 'Delete';
            button.disabled = false;
        }
    })
    .catch(error => {
        console.error('Error deleting workflow:', error);
        alert('Error deleting workflow: ' + error);
        button.textContent = 'Delete';
        button.disabled = false;
    });
}

function saveCurrentWorkflow() {
    const filename = prompt('Enter workflow filename (without .json extension):');
    if (!filename) return;
    
    if (window.app && window.app.graph) {
        const workflowData = window.app.graph.serialize();
        
        fetch('/api/workflows/save', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                filename: filename,
                workflow: workflowData
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                alert('Workflow saved successfully!');
                // Refresh the workflow list if browser is open
                if (document.getElementById('workflowBrowserModal').style.display === 'block') {
                    loadWorkflowList();
                }
            } else {
                alert('Error saving workflow: ' + data.error);
            }
        })
        .catch(error => {
            console.error('Error saving workflow:', error);
            alert('Error saving workflow: ' + error);
        });
    } else {
        alert('No workflow to save or ComfyUI not loaded properly.');
    }
}

// Handle Enter key in chat input
document.addEventListener('DOMContentLoaded', function() {
    const chatInput = document.getElementById('chatInput');
    if (chatInput) {
        chatInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                sendChatMessage();
            }
        });
    }
    
    // Handle file input change
    const fileInput = document.getElementById('chatFileInput');
    if (fileInput) {
        fileInput.addEventListener('change', function() {
            handleFileSelection();
        });
    }
    
    // Initialize chat button visibility based on authentication
    checkChatButtonVisibility();
    
    // Check for unread messages on page load
    checkUnreadMessages();
});

function checkChatButtonVisibility() {
    // Check if user is authenticated and show/hide chat button accordingly
    fetch('/check-session', {
        method: 'GET',
        credentials: 'include'
    })
    .then(response => response.json())
    .then(data => {
        const chatButton = document.getElementById('chatButton');
        const workflowButton = document.querySelector('.workflow-btn');
        const workflowSaveButton = document.querySelector('.workflow-save-btn');
        if (chatButton) {
            if (data.status === 'authenticated') {
                chatButton.style.display = 'flex';
                if (workflowButton) workflowButton.style.display = 'flex';
                if (workflowSaveButton) workflowSaveButton.style.display = 'flex';
                // Connect to chat WebSocket when authenticated
                connectChatWebSocket();
                // Check for unread messages
                checkUnreadMessages();
            } else {
                chatButton.style.display = 'none';
                if (workflowButton) workflowButton.style.display = 'none';
                if (workflowSaveButton) workflowSaveButton.style.display = 'none';
            }
        }
    })
    .catch(error => {
        console.log('Error checking session for chat button:', error);
    });
}

function checkUnreadMessages() {
    fetch('/unread-messages-count', {
        method: 'GET',
        credentials: 'include'
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            updateUnreadCount(data.unread_count);
        }
    })
    .catch(error => {
        console.log('Error checking unread messages:', error);
    });
}

// Close modal when clicking outside
window.onclick = function(event) {
    const aboutModal = document.getElementById('aboutModal');
    const forcedLogoutModal = document.getElementById('forcedLogoutModal');
    const sessionExpiryModal = document.getElementById('sessionExpiryModal');
    const userSettingsModal = document.getElementById('userSettingsModal');
    const chatModal = document.getElementById('chatModal');
    const workflowBrowserModal = document.getElementById('workflowBrowserModal');
    const aboutDrawer = document.getElementById('aboutDrawer');
    
    if (event.target == aboutModal) {
        closeAboutModal();
    }
    if (event.target == forcedLogoutModal) {
        redirectToLogin();
    }
    if (event.target == sessionExpiryModal) {
        closeSessionExpiryModal();
    }
    if (event.target == userSettingsModal) {
        closeUserSettingsModal();
    }
    if (event.target == chatModal) {
        closeChatModal();
    }
    if (event.target == workflowBrowserModal) {
        closeWorkflowBrowser();
    }
    if (event.target == aboutDrawer) {
        closeAboutDrawer();
    }
}

// Update session timer display
function updateSessionTimer(timeRemaining) {
    const timerElement = document.querySelector('.session-timer');
    if (!timerElement) return;
    
    if (timeRemaining && timeRemaining <= 60) {
        // Show timer only in last minute
        const minutes = Math.floor(timeRemaining / 60);
        const seconds = timeRemaining % 60;
        timerElement.innerHTML = `<strong>Session expires in:</strong> ${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        timerElement.style.display = 'block';
    } else {
        // Hide timer if more than 1 minute remaining
        timerElement.style.display = 'none';
    }
}

// Check if session was forcibly terminated or expiring soon
function checkSessionStatus() {
    fetch('/check-session', {
        method: 'GET',
        credentials: 'include'
    })
    .then(response => response.json())
    .then(data => {
        console.log('Session status:', data); // Debug log
        
        if (data.status === 'forced_logout') {
            openForcedLogoutModal();
        } else if (data.status === 'session_expiring_soon') {
            // Show expiry modal only if not already shown
            if (document.getElementById('sessionExpiryModal').style.display !== 'block') {
                openSessionExpiryModal();
            }
            updateSessionTimer(data.time_remaining);
        } else if (data.status === 'session_expired') {
            redirectToLogin();
        } else if (data.status === 'authenticated') {
            // Update timer display based on remaining time
            updateSessionTimer(data.time_remaining);
            // Ensure chat button is visible
            const chatButton = document.getElementById('chatButton');
            const workflowButton = document.querySelector('.workflow-btn');
            const workflowSaveButton = document.querySelector('.workflow-save-btn');
            if (chatButton) {
                chatButton.style.display = 'flex';
            }
            if (workflowButton) {
                workflowButton.style.display = 'flex';
            }
            if (workflowSaveButton) {
                workflowSaveButton.style.display = 'flex';
            }
        }
    })
    .catch(error => {
        console.log('Session check error:', error);
    });
}

// Initialize session monitoring
function initSessionMonitoring() {
    // Clear any existing intervals
    if (sessionCheckInterval) clearInterval(sessionCheckInterval);
    if (timerUpdateInterval) clearInterval(timerUpdateInterval);
    
    // Check session status every 5 seconds
    sessionCheckInterval = setInterval(checkSessionStatus, 5000);
    
    // Update timer display every second (only when visible)
    timerUpdateInterval = setInterval(() => {
        const timerElement = document.querySelector('.session-timer');
        if (timerElement && timerElement.style.display === 'block') {
            checkSessionStatus(); // This will update the timer
        }
    }, 1000);
    
    // Initial check
    checkSessionStatus();
}

// Start session monitoring when page loads
document.addEventListener('DOMContentLoaded', function() {
    initSessionMonitoring();
    
    // Add event listener for user settings form
    const userSettingsForm = document.getElementById('userSettingsForm');
    if (userSettingsForm) {
        userSettingsForm.addEventListener('submit', function(e) {
            e.preventDefault();
            saveUserSettings();
        });
    }
    
    // Connect to chat WebSocket
    connectChatWebSocket();
});

// Also start when window loads (fallback)
window.onload = function() {
    initSessionMonitoring();
    checkChatButtonVisibility();
};

// Function to autocomplete username when clicking on user status
function autocompleteUsername(username) {
    document.querySelector('input[name="username"]').value = username;
}
</script>
"""

# === HTML PENTRU INTERFAȚA PRINCIPALĂ ===
LOGIN_HTML = """<!DOCTYPE html>
<html><head><title>ComfyUI Login</title><meta charset="utf-8">
<style>
    body {{font-family: 'Segoe UI', Arial, sans-serif; text-align: center; margin-top: 50px; background: #1a1a1a; color: #e0e0e0;}}
    .container {{width: 400px; margin: 0 auto; background: #2a2a2a; padding: 30px; border-radius: 10px; box-shadow: 0 0 10px rgba(0,0,0,0.5); transition: transform 0.4s cubic-bezier(0.4, 0, 0.2, 1);}}
    .login-title {{color: #007bff; font-size: 28px; font-weight: 300; margin-bottom: 5px; letter-spacing: 1px;}}
    .login-subtitle {{color: #007bff; font-size: 14px; font-weight: 300; margin-bottom: 25px; letter-spacing: 2px;}}
    input, button {{width: 80%; padding: 12px; margin: 8px; font-size: 16px; border-radius: 5px;}}
    input {{border: 1px solid #444; background: #333; color: #e0e0e0; transition: border-color 0.3s;}}
    input:focus {{border-color: #007bff; outline: none;}}
    button {{background: #007bff; color: white; border: none; cursor: pointer; font-weight: 500; transition: background 0.3s;}}
    button:hover {{background: #0056b3;}}
    .error {{color: #ff6b6b; margin-top: 10px;}}
    .about-btn-login {{
        position: fixed;
        top: 15px;
        right: 15px;
        background: #007bff;
        color: white;
        border: none;
        padding: 8px 16px;
        border-radius: 5px;
        cursor: pointer;
        font-size: 16px;
        width: 40px;
        height: 40px;
        z-index: 1000;
    }}
    .about-btn-login:hover {{
        background: #0056b3;
    }}
    .user-status-container {{
        background: #2a2a2a;
        border-radius: 10px;
        padding: 20px;
        margin: 20px 0;
        border-left: 4px solid #007bff;
    }}
    .user-status-title {{
        color: #007bff;
        margin-bottom: 15px;
        font-size: 18px;
        font-weight: bold;
    }}
    .user-status-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 10px;
    }}
    .user-status-item {{
        background: #333;
        padding: 10px;
        border-radius: 5px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        cursor: pointer;
        transition: background 0.2s;
    }}
    .user-status-item:hover {{
        background: #3a3a3a;
    }}
    .user-status-username {{
        font-weight: bold;
        color: #e0e0e0;
    }}
    .user-status-info {{
        font-size: 12px;
        color: #aaa;
    }}
    .user-status-available {{
        border-left: 3px solid #28a745;
    }}
    .user-status-busy {{
        border-left: 3px solid #ffc107;
    }}
    .user-status-full {{
        border-left: 3px solid #dc3545;
    }}
    .user-status-offline {{
        border-left: 3px solid #6c757d;
    }}
    .user-status-disabled {{
        border-left: 3px solid #6c757d;
        opacity: 0.6;
    }}
    .user-status-disabled .user-status-username {{
        color: #6c757d !important;
        text-decoration: line-through;
    }}
</style></head>
<body>
    <button class="about-btn-login" onclick="openAboutDrawer()">?</button>
    <div class="container" id="loginContainer">
        <div class="login-title">ComfyUI Login</div>
        <div class="login-subtitle">PRO AI</div>
        <form method="post" action="/login">
            <input name="username" placeholder="Username" required><br>
            <input type="password" name="password" placeholder="Password" required><br>
            <button type="submit">Login</button>
        </form>
        {error}
        
        <!-- User Status Section -->
        <div class="user-status-container">
            <div class="user-status-title">User Status</div>
            <div class="user-status-grid" id="userStatusGrid">
                <!-- User status will be loaded here by JavaScript -->
            </div>
        </div>
    </div>
    {about_modal}
    
    <script>
    function loadUserStatus() {{
        fetch('/user-status')
            .then(response => response.json())
            .then(data => {{
                const grid = document.getElementById('userStatusGrid');
                grid.innerHTML = '';
                
                data.users.forEach(user => {{
                    const item = document.createElement('div');
                    let statusClass = 'user-status-offline';
                    let statusText = 'Offline';
                    
                    // Verifică dacă utilizatorul este dezactivat
                    if (!user.enabled) {{
                        statusClass = 'user-status-disabled';
                        statusText = 'Disabled';
                    }} else if (user.instances > 0 && user.instances < user.max_instances) {{
                        statusClass = 'user-status-busy';
                        statusText = user.instances + '/' + user.max_instances + ' sessions';
                    }} else if (user.instances >= user.max_instances && user.max_instances > 0) {{
                        statusClass = 'user-status-full';
                        statusText = 'Full';
                    }} else if (user.instances === 0) {{
                        statusClass = 'user-status-available';
                        statusText = 'Available';
                    }}
                    
                    item.className = 'user-status-item ' + statusClass;
                    
                    // Folosim concatenare de string-uri în loc de template literals
                    var userReadyText = user.ready ? 'Ready' : 'Not Ready';
                    item.innerHTML = 
                        '<div>' +
                            '<div class="user-status-username">' + user.username + '</div>' +
                            '<div class="user-status-info">' + statusText + '</div>' +
                        '</div>' +
                        '<div class="user-status-info">' +
                            userReadyText +
                        '</div>';
                    
                    item.onclick = function() {{
                        autocompleteUsername(user.username);
                    }};
                    grid.appendChild(item);
                }});
            }})
            .catch(error => {{
                console.log('Error loading user status:', error);
            }});
    }}
    
    // Load user status when page loads
    document.addEventListener('DOMContentLoaded', function() {{
        loadUserStatus();
        setInterval(loadUserStatus, 10000); // Refresh every 10 seconds
    }});
    
    // Function to autocomplete username when clicking on user status
    function autocompleteUsername(username) {{
        document.querySelector('input[name="username"]').value = username;
    }}
    
    // Also include the modal functions from ABOUT_MODAL_HTML
    function openAboutDrawer() {{
        document.getElementById('aboutDrawer').classList.add('open');
        document.getElementById('loginContainer').classList.add('login-container-shifted');
    }}
    </script>
</body></html>"""

LOGOUT_HTML = """<!DOCTYPE html>
<html><head><title>Logged Out</title><meta charset="utf-8">
<style>
    body {
        font-family: 'Segoe UI', Arial, sans-serif; 
        text-align: center; 
        margin-top: 100px; 
        background: #1a1a1a; 
        color: #e0e0e0;
    }
    .container {
        width: 400px; 
        margin: 0 auto; 
        background: #2a2a2a; 
        padding: 30px; 
        border-radius: 10px; 
        box-shadow: 0 0 10px rgba(0,0,0,0.5);
    }
    .success {
        color: #28a745; 
        font-size: 18px; 
        margin-bottom: 20px;
    }
    .btn {
        background: #007bff; 
        color: white; 
        border: none; 
        padding: 10px 20px; 
        border-radius: 5px; 
        cursor: pointer; 
        text-decoration: none; 
        display: inline-block;
    }
    .btn:hover {
        background: #0056b3;
    }
    .about-btn-login {
        position: fixed;
        top: 15px;
        right: 15px;
        background: #007bff;
        color: white;
        border: none;
        padding: 8px 16px;
        border-radius: 5px;
        cursor: pointer;
        font-size: 16px;
        width: 40px;
        height: 40px;
        z-index: 1000;
    }
    .about-btn-login:hover {
        background: #0056b3;
    }
</style></head>
<body>
    <button class="about-btn-login" onclick="openAboutModal()">?</button>
    <div class="container">
        <div class="success">You have been successfully logged out</div>
        <p>Thank you for using ComfyUI.</p>
        <a href="/login" class="btn">Login Again</a>
    </div>
    {about_modal}
</body></html>"""

WAITING_HTML = """<!DOCTYPE html><html><head><title>Loading...</title>
<meta http-equiv="refresh" content="3">
<style>
    body{font-family:'Segoe UI', Arial;text-align:center;margin-top:100px;background:#1a1a1a;color:#e0e0e0;}
    .about-btn-login {
        position: fixed;
        top: 15px;
        right: 15px;
        background: #007bff;
        color: white;
        border: none;
        padding: 8px 16px;
        border-radius: 5px;
        cursor: pointer;
        font-size: 16px;
        width: 40px;
        height: 40px;
        z-index: 1000;
    }
    .about-btn-login:hover {
        background: #0056b3;
    }
</style>
</head><body>
    <button class="about-btn-login" onclick="openAboutModal()">?</button>
    <h2>ComfyUI is loading...</h2>
    <p>Please wait 1-2 minutes. The page will refresh automatically.</p>
    {about_modal}
</body></html>"""

USER_FULL_HTML = """<!DOCTYPE html><html><head><title>User Limit Reached</title>
<meta charset="utf-8">
<style>
body{font-family:'Segoe UI', Arial;text-align:center;margin-top:100px;background:#1a1a1a;color:#e0e0e0;}
.container{width:500px;margin:0 auto;background:#2a2a2a;padding:30px;border-radius:10px;}
.warning{color:#ffc107;font-size:18px;margin-bottom:20px;}
.btn{background:#007bff;color:white;border:none;padding:10px 20px;border-radius:5px;cursor:pointer;text-decoration:none;display:inline-block;margin:5px;}
.about-btn-login {
    position: fixed;
    top: 15px;
    right: 15px;
    background: #007bff;
    color: white;
    border: none;
    padding: 8px 16px;
    border-radius: 5px;
    cursor: pointer;
    font-size: 16px;
    width: 40px;
    height: 40px;
    z-index: 1000;
}
.about-btn-login:hover {
    background: #0056b3;
}
</style></head>
<body>
    <button class="about-btn-login" onclick="openAboutModal()">?</button>
    <div class="container">
        <div class="warning">User Limit Reached</div>
        <p>User <strong>{username}</strong> has reached the maximum limit of {max_instances} simultaneous instances.</p>
        <p>Please try another user or wait for someone to log out.</p>
        <a href="/login" class="btn">Try Another User</a>
    </div>
    {about_modal}
</body></html>"""

FORCED_LOGOUT_HTML = """<!DOCTYPE html>
<html><head><title>Session Terminated</title><meta charset="utf-8">
<style>
    body {font-family: 'Segoe UI', Arial, sans-serif; text-align: center; margin-top: 100px; background: #1a1a1a; color: #e0e0e0;}
    .container {width: 500px; margin: 0 auto; background: #2a2a2a; padding: 30px; border-radius: 10px; box-shadow: 0 0 10px rgba(0,0,0,0.5);}
    .warning {color: #ffc107; font-size: 18px; margin-bottom: 20px;}
    .info {color: #17a2b8; margin-bottom: 20px;}
    .btn {background: #007bff; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; text-decoration: none; display: inline-block; margin: 5px;}
    .btn:hover {background: #0056b3;}
    .about-btn-login {
        position: fixed;
        top: 15px;
        right: 15px;
        background: #007bff;
        color: white;
        border: none;
        padding: 8px 16px;
        border-radius: 5px;
        cursor: pointer;
        font-size: 16px;
        width: 40px;
        height: 40px;
        z-index: 1000;
    }
    .about-btn-login:hover {
        background: #0056b3;
    }
</style></head>
<body>
    <button class="about-btn-login" onclick="openAboutModal()">?</button>
    <div class="container">
        <div class="warning">Session Terminated by Administrator</div>
        <div class="info">
            <p>Your session has been terminated by an administrator.</p>
            <p>You will be able to login again after the temporary restriction is lifted.</p>
            <p><strong>Remaining time: <span id="countdown">5:00</span> minutes</strong></p>
        </div>
        <a href="/login" class="btn" id="loginBtn" style="display:none;">Login Again</a>
    </div>
    {about_modal}
<script>
    let timeLeft = 300; // 5 minutes in seconds
    const countdownElement = document.getElementById('countdown');
    const loginBtn = document.getElementById('loginBtn');
    
    function updateCountdown() {
        const minutes = Math.floor(timeLeft / 60);
        const seconds = timeLeft % 60;
        countdownElement.textContent = minutes + ':' + (seconds.toString().padStart(2, '0'));
        
        if (timeLeft <= 0) {
            countdownElement.textContent = "0:00";
            loginBtn.style.display = 'inline-block';
        } else {
            timeLeft--;
            setTimeout(updateCountdown, 1000);
        }
    }
    
    updateCountdown();
</script>
</body></html>"""

SESSION_EXPIRED_HTML = """<!DOCTYPE html>
<html><head><title>Session Expired</title><meta charset="utf-8">
<style>
    body {font-family: 'Segoe UI', Arial, sans-serif; text-align: center; margin-top: 100px; background: #1a1a1a; color: #e0e0e0;}
    .container {width: 500px; margin: 0 auto; background: #2a2a2a; padding: 30px; border-radius: 10px; box-shadow: 0 0 10px rgba(0,0,0,0.5);}
    .warning {color: #ffc107; font-size: 18px; margin-bottom: 20px;}
    .info {color: #17a2b8; margin-bottom: 30px;}
    .btn {background: #007bff; color: white; border: none; padding: 15px 30px; border-radius: 5px; cursor: pointer; text-decoration: none; display: inline-block; font-size: 16px; margin-top: 20px;}
    .btn:hover {background: #0056b3;}
    .about-btn-login {
        position: fixed;
        top: 15px;
        right: 15px;
        background: #007bff;
        color: white;
        border: none;
        padding: 8px 16px;
        border-radius: 5px;
        cursor: pointer;
        font-size: 16px;
        width: 40px;
        height: 40px;
        z-index: 1000;
    }
    .about-btn-login:hover {
        background: #0056b3;
    }
</style></head>
<body>
    <button class="about-btn-login" onclick="openAboutModal()">?</button>
    <div class="container">
        <div class="warning">Session Expired</div>
        <div class="info">
            <p>Your session has expired due to inactivity.</p>
            <p>Please login again to continue using ComfyUI.</p>
        </div>
        <a href="/login" class="btn">Login Again</a>
    </div>
    {about_modal}
</body></html>"""

# === HTML PENTRU ADMIN LOGIN ===
ADMIN_LOGIN_HTML = """<!DOCTYPE html>
<html><head><title>Admin Login</title><meta charset="utf-8">
<style>
    body {{font-family: 'Segoe UI', Arial, sans-serif; text-align: center; margin-top: 100px; background: #1a1a1a; color: #e0e0e0;}}
    .container {{width: 350px; margin: 0 auto; background: #2a2a2a; padding: 30px; border-radius: 10px; box-shadow: 0 0 10px rgba(0,0,0,0.5);}}
    input, button {{width: 80%; padding: 12px; margin: 8px; font-size: 16px; border-radius: 5px;}}
    input {{border: 1px solid #444; background: #333; color: #e0e0e0;}}
    button {{background: #007bff; color: white; border: none; cursor: pointer;}}
    button:hover {{background: #0056b3;}}
    .error {{color: #ff6b6b; margin-top: 10px;}}
    .info {{color: #17a2b8; margin-top: 10px; font-size: 14px;}}
    .about-btn-login {{
        position: fixed;
        top: 15px;
        right: 15px;
        background: #007bff;
        color: white;
        border: none;
        padding: 8px 16px;
        border-radius: 5px;
        cursor: pointer;
        font-size: 16px;
        width: 40px;
        height: 40px;
        z-index: 1000;
    }}
    .about-btn-login:hover {{
        background: #0056b3;
    }}
</style></head>
<body>
    <button class="about-btn-login" onclick="openAboutModal()">?</button>
    <div class="container">
        <h2>Admin Dashboard</h2>
        <div class="info">Default password: admin123</div>
        <form method="post" action="/admin/login">
            <input type="password" name="password" placeholder="Admin Password" required><br>
            <button type="submit">Login</button>
        </form>
        {error}
    </div>
    {about_modal}
</body></html>"""

# === HTML PENTRU ADMIN INTERFACE ===
ADMIN_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>ComfyUI Admin</title>
    <meta charset="utf-8">
    <style>
        body { font-family: 'Segoe UI', Arial, sans-serif; margin: 0; padding: 20px; background: #1a1a1a; color: #e0e0e0; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .tabs { display: flex; margin-bottom: 20px; border-bottom: 1px solid #444; }
        .tab { padding: 10px 20px; cursor: pointer; border: 1px solid transparent; }
        .tab.active { background: #2a2a2a; border: 1px solid #444; border-bottom: none; border-radius: 5px 5px 0 0; }
        .tab-content { display: none; padding: 20px; background: #2a2a2a; border-radius: 0 5px 5px 5px; }
        .tab-content.active { display: block; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-weight: bold; color: #e0e0e0; }
        input, select { width: 100%; padding: 8px; border: 1px solid #444; background: #333; color: #e0e0e0; border-radius: 4px; }
        button { background: #007bff; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; margin: 5px; }
        button:hover { background: #0056b3; }
        button.delete { background: #dc3545; }
        button.delete:hover { background: #c82333; }
        button.edit { background: #ffc107; color: #000; }
        button.edit:hover { background: #e0a800; }
        button.security { background: #28a745; }
        button.security:hover { background: #218838; }
        .status-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 15px; }
        .status-card { background: #2a2a2a; padding: 15px; border-radius: 5px; border-left: 4px solid #28a745; }
        .status-card.full { border-left-color: #dc3545; }
        .status-card.limited { border-left-color: #ffc107; }
        .session-list { margin-top: 20px; }
        .session-item { background: #333; padding: 10px; margin: 5px 0; border-radius: 4px; display: flex; justify-content: space-between; align-items: center; }
        .session-info { flex-grow: 1; }
        .session-actions { flex-shrink: 0; }
        .remaining-time { font-size: 12px; color: #aaa; }
        .security-status { padding: 10px; border-radius: 5px; margin: 10px 0; }
        .security-on { background: #28a745; }
        .security-off { background: #dc3545; }
        .logout-btn { background: #6c757d; }
        .logout-btn:hover { background: #5a6268; }
        .help-text { font-size: 12px; color: #aaa; margin-top: 5px; }
        .save-notification { 
            position: fixed; 
            top: 20px; 
            right: 20px; 
            background: #28a745; 
            color: white; 
            padding: 10px 20px; 
            border-radius: 5px; 
            z-index: 1000;
            display: none;
        }
        .blocked-user-item { background: #dc3545; padding: 10px; margin: 5px 0; border-radius: 4px; display: flex; justify-content: space-between; align-items: center; }
        
        /* Chat Styles for Admin */
        .chat-section { margin-top: 30px; }
        .chat-user-list { background: #333; padding: 15px; border-radius: 5px; margin-bottom: 15px; }
        .chat-user-item { padding: 10px; margin: 5px 0; background: #444; border-radius: 4px; cursor: pointer; display: flex; justify-content: between; align-items: center; }
        .chat-user-item:hover { background: #555; }
        .chat-user-item.active { background: #007bff; }
        .chat-messages-admin { height: 300px; overflow-y: auto; background: #2a2a2a; padding: 15px; border-radius: 5px; margin-bottom: 15px; display: flex; flex-direction: column; gap: 10px; }
        .chat-message-admin { padding: 8px 12px; border-radius: 8px; max-width: 80%; word-wrap: break-word; }
        .chat-message-admin.admin { background: #007bff; color: white; align-self: flex-end; border-bottom-right-radius: 2px; }
        .chat-message-admin.user { background: #28a745; color: white; align-self: flex-start; border-bottom-left-radius: 2px; }
        .chat-message-time-admin { font-size: 10px; opacity: 0.7; margin-top: 2px; }
        .chat-input-admin { display: flex; gap: 10px; }
        .chat-input-admin input { flex: 1; }
        .unread-badge { background: #dc3545; color: white; border-radius: 50%; width: 20px; height: 20px; font-size: 12px; display: flex; align-items: center; justify-content: center; }
        
        /* About Modal Styles for Admin */
        .admin-about-modal { 
            display: none; 
            position: fixed; 
            z-index: 10000; 
            left: 0; 
            top: 0; 
            width: 100%; 
            height: 100%; 
            background-color: rgba(0,0,0,0.7); 
        }
        .admin-about-modal-content { 
            background-color: #2a2a2a; 
            margin: 5% auto; 
            padding: 30px; 
            border-radius: 10px; 
            width: 500px; 
            max-height: 80vh;
            overflow-y: auto;
            text-align: center; 
            box-shadow: 0 0 20px rgba(0,0,0,0.5); 
        }
        .admin-about-modal h2 { 
            color: #007bff; 
            margin-bottom: 20px; 
        }
        .admin-about-modal p { 
            margin-bottom: 25px; 
            line-height: 1.6; 
            color: #e0e0e0; 
        }
        .admin-about-close-btn { 
            background: #007bff; 
            color: white; 
            border: none; 
            padding: 12px 30px; 
            border-radius: 5px; 
            cursor: pointer; 
            font-size: 16px; 
        }
        .admin-about-close-btn:hover { 
            background: #0056b3; 
        }

        /* Edit Modal Styles */
        .edit-modal { 
            display: none; 
            position: fixed; 
            z-index: 10004; 
            left: 0; 
            top: 0; 
            width: 100%; 
            height: 100%; 
            background-color: rgba(0,0,0,0.7); 
        }
        .edit-modal-content { 
            background-color: #2a2a2a; 
            margin: 5% auto; 
            padding: 30px; 
            border-radius: 10px; 
            width: 500px; 
            box-shadow: 0 0 20px rgba(0,0,0,0.5); 
        }
        .edit-modal h2 { 
            color: #007bff; 
            margin-bottom: 20px; 
            text-align: center;
        }
        .edit-modal .form-group { 
            margin-bottom: 15px; 
        }
        .edit-modal label { 
            display: block; 
            margin-bottom: 5px; 
            font-weight: bold; 
            color: #e0e0e0;
        }
        .edit-modal input, .edit-modal select { 
            width: 100%; 
            padding: 8px; 
            border: 1px solid #444; 
            background: #333; 
            color: #e0e0e0; 
            border-radius: 4px; 
        }
        .edit-modal-buttons { 
            display: flex; 
            justify-content: space-between; 
            margin-top: 20px; 
        }
        .edit-modal-btn { 
            background: #007bff; 
            color: white; 
            border: none; 
            padding: 10px 20px; 
            border-radius: 5px; 
            cursor: pointer; 
        }
        .edit-modal-btn.save { 
            background: #28a745; 
        }
        .edit-modal-btn.cancel { 
            background: #6c757d; 
        }
        .edit-modal-btn:hover { 
            opacity: 0.9; 
        }
        
        /* File upload styles for admin chat */
        .file-preview-admin {
            background: #333;
            padding: 10px;
            border-radius: 5px;
            margin: 10px 0;
            display: none;
        }
        .file-preview-item-admin {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 5px;
            margin: 5px 0;
        }
        .file-preview-name-admin {
            flex: 1;
            font-size: 12px;
        }
        .file-preview-remove-admin {
            background: #dc3545;
            color: white;
            border: none;
            padding: 2px 6px;
            border-radius: 3px;
            cursor: pointer;
            font-size: 10px;
        }
        .chat-file-message-admin {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px;
            background: rgba(255,255,255,0.1);
            border-radius: 5px;
            margin-top: 5px;
        }
        .chat-file-icon-admin {
            font-size: 16px;
        }
        .chat-file-name-admin {
            flex: 1;
            font-size: 12px;
            word-break: break-all;
        }
        .chat-file-download-admin {
            background: #28a745;
            color: white;
            border: none;
            padding: 4px 8px;
            border-radius: 3px;
            cursor: pointer;
            font-size: 10px;
            text-decoration: none;
        }
        .chat-file-download-admin:hover {
            background: #218838;
        }
    </style>
</head>
<body>
    <div class="save-notification" id="saveNotification">Changes saved successfully!</div>
    
    <!-- About Modal for Admin -->
    <div id="aboutModal" class="admin-about-modal">
        <div class="admin-about-modal-content">
            <h2>ComfyUI Auth Server v0.3.1</h2>
            <p><strong>Advanced management and authentication system for multiple ComfyUI instances</strong></p>
            
            <h3>Key Features and Advantages:</h3>
            <ul style="text-align: left; margin: 20px 0;">
                <li><strong>Multi-User Architecture</strong> - Simultaneous management of multiple users with separate instances</li>
                <li><strong>Intelligent Load Balancing</strong> - Automatic workload distribution between ComfyUI instances</li>
                <li><strong>Enhanced Security</strong> - Rate limiting, bcrypt password hashing, customizable session timeouts</li>
                <li><strong>Advanced Admin Interface</strong> - Real-time monitoring, session management, full control</li>
                <li><strong>Integrated Chat System</strong> - Direct communication between users and administrators</li>
                <li><strong>Personalized Workflow Browser</strong> - Each user has their own workspace for workflows</li>
                <li><strong>Session Management</strong> - Customizable timeout, expiry notifications, session renewal</li>
                <li><strong>Performance Monitoring</strong> - Real-time instance status, maximum capacity alerts</li>
                <li><strong>Backup & Restore</strong> - Automatic configuration saving, easy recovery</li>
                <li><strong>Scalability</strong> - Easy addition of new users and resources</li>
            </ul>

            <h3>Security Features:</h3>
            <ul style="text-align: left; margin: 15px 0;">
                <li>Authentication with hashed passwords (bcrypt)</li>
                <li>Protection against brute-force attacks</li>
                <li>Configurable session timeout per user</li>
                <li>Temporary user blocking for security</li>
                <li>Suspicious IP monitoring</li>
            </ul>

            <h3>For Administrators:</h3>
            <ul style="text-align: left; margin: 15px 0;">
                <li>Real-time statistics dashboard</li>
                <li>Complete user management</li>
                <li>Flexible instance configuration</li>
                <li>Chat system with users</li>
                <li>Active session control</li>
                <li>Workflow directory configuration</li>
            </ul>

            <div style="background: #2a2a2a; border-left: 4px solid #007bff; padding: 15px; margin: 20px 0;">
                <h4>Main Benefits:</h4>
                <p><strong>Resource Optimization</strong> - Balanced distribution of computing load<br>
                <strong>User Isolation</strong> - Each user works in a secure space<br>
                <strong>Easy Management</strong> - Single interface for all instances<br>
                <strong>Scalability</strong> - Always add new users and resources</p>
            </div>

            <p><strong>Version:</strong> 0.3.1 - Report issues through the chat system to administrators</p>
            
            <p><em>Version 0.3.1 - In intensive testing phase<br>
            Report any issues or improvement suggestions through the chat system!</em></p>
            
            <button class="admin-about-close-btn" onclick="closeAboutModal()">Understood</button>
        </div>
    </div>

    <!-- Edit User Modal -->
    <div id="editUserModal" class="edit-modal">
        <div class="edit-modal-content">
            <h2>Edit User</h2>
            <form id="editUserForm">
                <input type="hidden" id="editOriginalUsername">
                <div class="form-group">
                    <label for="editUsername">Username:</label>
                    <input type="text" id="editUsername" required>
                </div>
                <div class="form-group">
                    <label for="editPassword">Password (leave empty to keep current):</label>
                    <input type="password" id="editPassword" placeholder="Leave empty to keep current password">
                </div>
                <div class="form-group">
                    <label for="editComfyUrl">ComfyUI URL:</label>
                    <input type="text" id="editComfyUrl" required>
                </div>
                <div class="form-group">
                    <label for="editMaxInstances">Max Instances:</label>
                    <input type="number" id="editMaxInstances" min="0" required>
                </div>
                <div class="form-group">
                    <label for="editSessionTimeout">Session Timeout (minutes):</label>
                    <input type="number" id="editSessionTimeout" min="0" required>
                </div>
                <div class="form-group">
                    <label for="editEnabled">Account Enabled:</label>
                    <select id="editEnabled" required>
                        <option value="true">Enabled</option>
                        <option value="false">Disabled</option>
                    </select>
                </div>
                <div class="edit-modal-buttons">
                    <button type="button" class="edit-modal-btn cancel" onclick="closeEditModal()">Cancel</button>
                    <button type="submit" class="edit-modal-btn save">Save Changes</button>
                </div>
            </form>
        </div>
    </div>
    
    <div class="container">
        <div class="header">
            <h1>ComfyUI Admin Dashboard</h1>
            <div>
                <button class="about-btn" onclick="openAboutModal()" style="background: #007bff; color: white; border: none; padding: 8px 16px; border-radius: 5px; cursor: pointer; margin-right: 10px; font-size: 16px; width: 40px; height: 40px;">?</button>
                <button class="logout-btn" onclick="adminLogout()">Logout</button>
            </div>
        </div>
        
        <div class="tabs">
            <div class="tab active" onclick="showTab('status')">Status</div>
            <div class="tab" onclick="showTab('settings')">Settings</div>
            <div class="tab" onclick="showTab('security')">Security</div>
            <div class="tab" onclick="showTab('chat')">Chat</div>
        </div>

        <div id="status" class="tab-content active">
            <h2>System Status</h2>
            <div id="statusGrid" class="status-grid">
                <!-- Status cards will be loaded here -->
            </div>
            
            <div class="session-list">
                <h3>Active Sessions</h3>
                <div id="sessionList">
                    <!-- Sessions will be loaded here -->
                </div>
            </div>
            
            <div class="session-list">
                <h3>Blocked Users</h3>
                <div id="blockedUsersList">
                    <!-- Blocked users will be loaded here -->
                </div>
            </div>
            
            <button onclick="loadStatus()">Refresh</button>
        </div>

        <div id="settings" class="tab-content">
            <h2>User Management</h2>
            <div class="form-group">
                <label>Username:</label>
                <input type="text" id="username" placeholder="Enter username">
            </div>
            <div class="form-group">
                <label>Password:</label>
                <input type="password" id="password" placeholder="Enter password">
            </div>
            <div class="form-group">
                <label>ComfyUI URL:</label>
                <input type="text" id="comfyUrl" placeholder="http://ip:port">
            </div>
            <div class="form-group">
                <label>Max Instances:</label>
                <input type="number" id="maxInstances" value="2" min="0">
                <div class="help-text">Maximum number of simultaneous sessions (0 = unlimited)</div>
            </div>
            <div class="form-group">
                <label>Session Timeout (minutes):</label>
                <input type="number" id="sessionTimeout" value="60" min="0">
                <div class="help-text">Session duration in minutes (0 = infinite session)</div>
            </div>
            <div class="form-group">
                <label>Account Enabled:</label>
                <select id="enabled">
                    <option value="true">Enabled</option>
                    <option value="false">Disabled</option>
                </select>
            </div>
            <button onclick="addUser()">Add User</button>

            <h3>Existing Users</h3>
            <div id="userList">
                <!-- User list will be loaded here -->
            </div>
            
            <h2>Workflow Settings</h2>
            <div class="form-group">
                <label>Workflow Root Directory:</label>
                <input type="text" id="workflowRoot" placeholder="/path/to/workflows" style="width: 100%;">
                <div class="help-text">Root directory where user workflow folders will be created</div>
            </div>
            <button onclick="saveWorkflowSettings()" class="security">Save Workflow Settings</button>
            <div id="workflowSettingsMessage" style="margin-top: 15px;"></div>
        </div>

        <div id="security" class="tab-content">
            <h2>Security Settings</h2>
            
            <div class="form-group">
                <label>Admin Password Protection:</label>
                <div id="securityStatus" class="security-status">
                    <!-- Security status will be loaded here -->
                </div>
                <button onclick="toggleSecurity()" id="toggleSecurityBtn">Toggle Protection</button>
            </div>

            <div class="form-group">
                <label>Current Admin Password:</label>
                <input type="password" id="currentPassword" placeholder="Enter current password">
            </div>
            <div class="form-group">
                <label>New Admin Password:</label>
                <input type="password" id="newPassword" placeholder="Enter new password">
            </div>
            <div class="form-group">
                <label>Confirm New Password:</label>
                <input type="password" id="confirmPassword" placeholder="Confirm new password">
            </div>
            <button class="security" onclick="changePassword()">Change Password</button>

            <div id="passwordMessage" style="margin-top: 15px;"></div>
        </div>

        <div id="chat" class="tab-content">
            <h2>User Chat</h2>
            <div class="chat-section">
                <div class="chat-user-list">
                    <h4>Active Users</h4>
                    <div id="chatUserList">
                        <!-- User list for chat will be loaded here -->
                    </div>
                </div>
                
                <div id="chatArea" style="display: none;">
                    <h4>Chat with <span id="chatWithUser"></span></h4>
                    <div class="chat-messages-admin" id="adminChatMessages">
                        <!-- Chat messages will be loaded here -->
                    </div>
                    
                    <div class="file-preview-admin" id="adminFilePreview"></div>
                    
                    <div class="chat-input-admin">
                        <input type="text" id="adminChatInput" placeholder="Type your message...">
                        <button onclick="sendAdminMessage()">Send</button>
                    </div>
                    <div style="margin-top: 10px;">
                        <input type="file" class="chat-file-input" id="adminChatFileInput" multiple accept="*/*" style="display: none;">
                        <button class="chat-file-btn" onclick="document.getElementById('adminChatFileInput').click()">Attach Files</button>
                    </div>
                </div>
                
                <div id="noUserSelected" style="text-align: center; padding: 40px; color: #666;">
                    <p>Select a user from the list to start chatting</p>
                </div>
            </div>
        </div>
    </div>

    <script>
        let selectedChatUser = null;
        let adminChatWebSocket = null;
        let adminChatAutoRefresh = null;
        let adminAttachedFiles = [];

        function showTab(tabName) {
            document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            
            event.target.classList.add('active');
            document.getElementById(tabName).classList.add('active');
            
            if (tabName === 'chat') {
                loadChatUsers();
                connectAdminChatWebSocket();
                startAdminChatAutoRefresh();
            } else {
                stopAdminChatAutoRefresh();
            }
        }

        function showSaveNotification() {
            const notification = document.getElementById('saveNotification');
            notification.style.display = 'block';
            setTimeout(() => {
                notification.style.display = 'none';
            }, 3000);
        }

        function openAboutModal() {
            document.getElementById('aboutModal').style.display = 'block';
        }

        function closeAboutModal() {
            document.getElementById('aboutModal').style.display = 'none';
        }

        function openEditModal(username, userData) {
            document.getElementById('editOriginalUsername').value = username;
            document.getElementById('editUsername').value = username;
            document.getElementById('editComfyUrl').value = userData.comfy_url;
            document.getElementById('editMaxInstances').value = userData.max_instances;
            document.getElementById('editSessionTimeout').value = userData.session_timeout;
            document.getElementById('editPassword').value = '';
            document.getElementById('editEnabled').value = userData.enabled ? 'true' : 'false';
            
            document.getElementById('editUserModal').style.display = 'block';
        }

        function closeEditModal() {
            document.getElementById('editUserModal').style.display = 'none';
            document.getElementById('editUserForm').reset();
        }

        // Close modal when clicking outside
        window.onclick = function(event) {
            const modal = document.getElementById('aboutModal');
            const editModal = document.getElementById('editUserModal');
            if (event.target == modal) {
                closeAboutModal();
            }
            if (event.target == editModal) {
                closeEditModal();
            }
        }

        // Handle edit form submission
        document.getElementById('editUserForm').addEventListener('submit', function(e) {
            e.preventDefault();
            
            const originalUsername = document.getElementById('editOriginalUsername').value;
            const userData = {
                username: document.getElementById('editUsername').value,
                password: document.getElementById('editPassword').value,
                comfy_url: document.getElementById('editComfyUrl').value,
                max_instances: parseInt(document.getElementById('editMaxInstances').value),
                session_timeout: parseInt(document.getElementById('editSessionTimeout').value),
                enabled: document.getElementById('editEnabled').value === 'true'
            };
            
            updateUser(originalUsername, userData);
        });

        function loadStatus() {
            fetch('/admin/api/status')
                .then(r => r.json())
                .then(data => updateStatus(data));
                
            fetch('/admin/api/sessions')
                .then(r => r.json())
                .then(data => updateSessions(data));

            fetch('/admin/api/security')
                .then(r => r.json())
                .then(data => updateSecurity(data));
                
            fetch('/admin/api/blocked-users')
                .then(r => r.json())
                .then(data => updateBlockedUsers(data));
        }

        function updateStatus(data) {
            const grid = document.getElementById('statusGrid');
            grid.innerHTML = '';
            
            for (const [username, userData] of Object.entries(data.users)) {
                const card = document.createElement('div');
                card.className = `status-card ${userData.instances >= userData.max_instances && userData.max_instances > 0 ? 'full' : 
                                 userData.instances > 0 ? 'limited' : ''}`;
                
                const timeoutText = userData.session_timeout === 0 ? 'Infinite' : userData.session_timeout + ' minutes';
                
                card.innerHTML = `
                    <h3>${username}</h3>
                    <p>Instances: ${userData.instances}/${userData.max_instances === 0 ? '∞' : userData.max_instances}</p>
                    <p>Session Timeout: ${timeoutText}</p>
                    <p>URL: ${userData.comfy_url}</p>
                    <p>Status: ${userData.ready ? 'Ready' : 'Not Ready'}</p>
                    <p>Enabled: ${userData.enabled ? 'Yes' : 'No'}</p>
                `;
                grid.appendChild(card);
            }
        }

        function updateSessions(data) {
            const list = document.getElementById('sessionList');
            list.innerHTML = '';
            
            data.sessions.forEach(session => {
                const item = document.createElement('div');
                item.className = 'session-item';
                item.innerHTML = `
                    <div class="session-info">
                        <strong>${session.user}</strong> - ${session.session_id.substring(0, 8)}...
                        <div class="remaining-time">${session.remaining_time}</div>
                    </div>
                    <div class="session-actions">
                        <button class="delete" onclick="forceLogout('${session.session_id}')">Force Logout</button>
                    </div>
                `;
                list.appendChild(item);
            });
        }

        function updateBlockedUsers(data) {
            const list = document.getElementById('blockedUsersList');
            list.innerHTML = '';
            
            if (Object.keys(data.blocked_users).length === 0) {
                list.innerHTML = '<p>No users are currently blocked.</p>';
                return;
            }
            
            for (const [username, userData] of Object.entries(data.blocked_users)) {
                const item = document.createElement('div');
                item.className = 'blocked-user-item';
                item.innerHTML = `
                    <div class="session-info">
                        <strong>${username}</strong> - Blocked for: ${userData.remaining_time}
                    </div>
                    <div class="session-actions">
                        <button class="security" onclick="unblockUser('${username}')">Unblock User</button>
                    </div>
                `;
                list.appendChild(item);
            }
        }

        function updateSecurity(data) {
            const statusDiv = document.getElementById('securityStatus');
            const toggleBtn = document.getElementById('toggleSecurityBtn');
            
            if (data.enabled) {
                statusDiv.innerHTML = 'Password Protection: ENABLED';
                statusDiv.className = 'security-status security-on';
                toggleBtn.textContent = 'Disable Protection';
            } else {
                statusDiv.innerHTML = 'Password Protection: DISABLED';
                statusDiv.className = 'security-status security-off';
                toggleBtn.textContent = 'Enable Protection';
            }
        }

        function toggleSecurity() {
            fetch('/admin/api/security/toggle', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    updateSecurity(data);
                    showSaveNotification();
                });
        }

        function changePassword() {
            const currentPassword = document.getElementById('currentPassword').value;
            const newPassword = document.getElementById('newPassword').value;
            const confirmPassword = document.getElementById('confirmPassword').value;
            const messageDiv = document.getElementById('passwordMessage');

            if (newPassword !== confirmPassword) {
                messageDiv.innerHTML = '<div style="color: #dc3545;">New passwords do not match!</div>';
                return;
            }

            if (newPassword.length < 3) {
                messageDiv.innerHTML = '<div style="color: #dc3545;">Password must be at least 3 characters!</div>';
                return;
            }

            fetch('/admin/api/security/password', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    current_password: currentPassword,
                    new_password: newPassword
                })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    messageDiv.innerHTML = '<div style="color: #28a745;">Password changed successfully!</div>';
                    document.getElementById('currentPassword').value = '';
                    document.getElementById('newPassword').value = '';
                    document.getElementById('confirmPassword').value = '';
                    showSaveNotification();
                } else {
                    messageDiv.innerHTML = '<div style="color: #dc3545;">' + data.error + '</div>';
                }
            });
        }

        function addUser() {
            const user = {
                username: document.getElementById('username').value,
                password: document.getElementById('password').value,
                comfy_url: document.getElementById('comfyUrl').value,
                max_instances: parseInt(document.getElementById('maxInstances').value),
                session_timeout: parseInt(document.getElementById('sessionTimeout').value),
                enabled: document.getElementById('enabled').value === 'true'
            };

            fetch('/admin/api/users', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(user)
            }).then(() => {
                loadUsers();
                clearForm();
                showSaveNotification();
            });
        }

        function loadUsers() {
            fetch('/admin/api/users')
                .then(r => r.json())
                .then(users => {
                    const list = document.getElementById('userList');
                    list.innerHTML = '';
                    
                    for (const [username, userData] of Object.entries(users)) {
                        const timeoutText = userData.session_timeout === 0 ? 'Infinite' : userData.session_timeout + ' minutes';
                        const maxInstancesText = userData.max_instances === 0 ? '∞' : userData.max_instances;
                        const enabledText = userData.enabled ? 'Enabled' : 'Disabled';
                        const enabledBtnText = userData.enabled ? 'Disable' : 'Enable';
                        const enabledBtnClass = userData.enabled ? 'delete' : 'security';
                        
                        const div = document.createElement('div');
                        div.className = 'user-item';
                        div.innerHTML = `
                            <div style="display: flex; justify-content: space-between; align-items: center; margin: 10px 0; padding: 10px; background: #333; ${!userData.enabled ? 'opacity: 0.7;' : ''}">
                                <div>
                                    <strong>${username}</strong> - ${userData.comfy_url} 
                                    (${userData.instances}/${maxInstancesText} instances, Timeout: ${timeoutText}, Status: ${enabledText})
                                </div>
                                <div>
                                    <button class="${enabledBtnClass}" onclick="toggleUserEnabled('${username}', ${!userData.enabled})">${enabledBtnText}</button>
                                    <button class="edit" onclick="openEditModal('${username}', ${JSON.stringify(userData).replace(/"/g, '&quot;')})">Edit</button>
                                    <button class="delete" onclick="deleteUser('${username}')">Delete</button>
                                </div>
                            </div>
                        `;
                        list.appendChild(div);
                    }
                });
        }

        function toggleUserEnabled(username, newState) {
            if (!username) return;
            
            fetch('/admin/api/users/' + username, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    username: username,
                    enabled: newState
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    loadUsers();
                    showSaveNotification();
                } else {
                    alert('Error updating user: ' + data.error);
                }
            })
            .catch(error => {
                alert('Error updating user: ' + error);
            });
        }

        function updateUser(originalUsername, userData) {
            fetch('/admin/api/users/' + originalUsername, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(userData)
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    closeEditModal();
                    loadUsers();
                    showSaveNotification();
                } else {
                    alert('Error updating user: ' + data.error);
                }
            })
            .catch(error => {
                alert('Error updating user: ' + error);
            });
        }

        function deleteUser(username) {
            if (confirm('Delete user ' + username + '?')) {
                fetch('/admin/api/users/' + encodeURIComponent(username), {
                    method: 'DELETE',
                    headers: {'Content-Type': 'application/json'}
                })
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        loadUsers();
                        showSaveNotification();
                        alert('User deleted successfully!');
                    } else {
                        alert('Error deleting user: ' + data.error);
                    }
                })
                .catch(error => {
                    alert('Error deleting user: ' + error);
                });
            }
        }

        function forceLogout(sessionId) {
            if (confirm('Force logout this session?')) {
                fetch('/admin/api/sessions/' + sessionId, {method: 'DELETE'})
                    .then(() => loadStatus());
            }
        }

        function unblockUser(username) {
            if (confirm('Unblock user ' + username + '?')) {
                fetch('/admin/api/blocked-users/' + username, {method: 'DELETE'})
                    .then(() => {
                        loadStatus();
                    });
            }
        }

        function adminLogout() {
            if (confirm('Logout from admin dashboard?')) {
                fetch('/admin/logout', {method: 'POST'})
                    .then(() => {
                        window.location.href = '/admin/login';
                    });
            }
        }

        function clearForm() {
            document.getElementById('username').value = '';
            document.getElementById('password').value = '';
            document.getElementById('comfyUrl').value = '';
            document.getElementById('maxInstances').value = '2';
            document.getElementById('sessionTimeout').value = '60';
            document.getElementById('enabled').value = 'true';
        }

        // Workflow Settings Functions
        function loadWorkflowSettings() {
            fetch('/admin/api/workflow-settings')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('workflowRoot').value = data.workflow_root;
                });
        }

        function saveWorkflowSettings() {
            const workflowRoot = document.getElementById('workflowRoot').value;
            const messageDiv = document.getElementById('workflowSettingsMessage');
            
            if (!workflowRoot) {
                messageDiv.innerHTML = '<div style="color: #dc3545;">Workflow root path is required!</div>';
                return;
            }
            
            fetch('/admin/api/workflow-settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    workflow_root: workflowRoot
                })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    messageDiv.innerHTML = '<div style="color: #28a745;">' + data.message + '</div>';
                    if (data.directory_exists) {
                        messageDiv.innerHTML += '<div style="color: #28a745;">Directory exists and is accessible</div>';
                    } else {
                        messageDiv.innerHTML += '<div style="color: #ffc107;">Directory does not exist or is not accessible</div>';
                    }
                    showSaveNotification();
                } else {
                    messageDiv.innerHTML = '<div style="color: #dc3545;">' + data.error + '</div>';
                }
            });
        }

        // Chat Functions for Admin
        function loadChatUsers() {
            fetch('/admin/api/chat/users')
                .then(r => r.json())
                .then(data => {
                    const userList = document.getElementById('chatUserList');
                    userList.innerHTML = '';
                    
                    if (data.users.length === 0) {
                        userList.innerHTML = '<p>No active users</p>';
                        return;
                    }
                    
                    data.users.forEach(user => {
                        const userItem = document.createElement('div');
                        userItem.className = 'chat-user-item';
                        userItem.onclick = () => selectChatUser(user.username);
                        userItem.innerHTML = `
                            <span>${user.username}</span>
                            ${user.unread_count > 0 ? '<div class="unread-badge">' + user.unread_count + '</div>' : ''}
                        `;
                        userList.appendChild(userItem);
                    });
                });
        }

        function selectChatUser(username) {
            selectedChatUser = username;
            
            // Update UI
            document.querySelectorAll('.chat-user-item').forEach(item => {
                item.classList.remove('active');
            });
            event.target.closest('.chat-user-item').classList.add('active');
            
            document.getElementById('chatWithUser').textContent = username;
            document.getElementById('chatArea').style.display = 'block';
            document.getElementById('noUserSelected').style.display = 'none';
            
            // Load chat history
            loadChatHistory(username);
        }

        function loadChatHistory(username) {
            fetch('/admin/api/chat/messages/' + username)
                .then(r => r.json())
                .then(data => {
                    const messagesDiv = document.getElementById('adminChatMessages');
                    messagesDiv.innerHTML = '';
                    
                    data.messages.forEach(msg => {
                        addAdminMessageToChat(msg.message, msg.from, msg.timestamp, msg.message_type, msg.file_data);
                    });
                    
                    scrollAdminChatToBottom();
                    
                    // Mark messages as read
                    fetch('/admin/api/chat/mark-read/' + username, {method: 'POST'});
                });
        }

        function addAdminMessageToChat(message, from, timestamp, message_type = 'text', file_data = null) {
            const messagesDiv = document.getElementById('adminChatMessages');
            const messageDiv = document.createElement('div');
            messageDiv.className = 'chat-message-admin ' + (from === 'admin' ? 'admin' : 'user');
            
            const time = new Date(timestamp * 1000).toLocaleTimeString();
            
            let messageContent = message;
            if (message_type === 'file' && file_data) {
                messageContent = `
                    <div>${message}</div>
                    <div class="chat-file-message-admin">
                        <span class="chat-file-icon-admin">📎</span>
                        <span class="chat-file-name-admin">${file_data.filename}</span>
                        <a href="/download-file/${file_data.id}" class="chat-file-download-admin" download="${file_data.filename}">Download</a>
                    </div>
                `;
            }
            
            messageDiv.innerHTML = `
                <div>${messageContent}</div>
                <div class="chat-message-time-admin">${from === 'admin' ? 'You' : from} • ${time}</div>
            `;
            
            messagesDiv.appendChild(messageDiv);
            scrollAdminChatToBottom();
        }

        function scrollAdminChatToBottom() {
            const messagesDiv = document.getElementById('adminChatMessages');
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }

        function sendAdminMessage() {
            if (!selectedChatUser) return;
            
            const input = document.getElementById('adminChatInput');
            const message = input.value.trim();
            
            if (!message && adminAttachedFiles.length === 0) return;
            
            // Upload files first if any
            if (adminAttachedFiles.length > 0) {
                uploadAdminFiles(message);
            } else {
                sendAdminTextMessage(message);
            }
        }

        function sendAdminTextMessage(message) {
            if (adminChatWebSocket && adminChatWebSocket.readyState === WebSocket.OPEN) {
                adminChatWebSocket.send(JSON.stringify({
                    type: 'admin_send_message',
                    to_user: selectedChatUser,
                    message: message
                }));
                // Add message immediately for better UX
                addAdminMessageToChat(message, 'admin', Date.now() / 1000);
                document.getElementById('adminChatInput').value = '';
                scrollAdminChatToBottom();
            } else {
                // Fallback to HTTP
                fetch('/admin/api/chat/send', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        to_user: selectedChatUser,
                        message: message
                    })
                })
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById('adminChatInput').value = '';
                        loadChatHistory(selectedChatUser);
                    }
                });
            }
        }

        function uploadAdminFiles(message) {
            const formData = new FormData();
            formData.append('message', message);
            formData.append('to_user', selectedChatUser);
            
            adminAttachedFiles.forEach((file, index) => {
                formData.append('file' + index, file);
            });
            
            fetch('/admin/api/chat/upload-file', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    // Clear input and attached files
                    document.getElementById('adminChatInput').value = '';
                    adminAttachedFiles = [];
                    updateAdminFilePreview();
                    
                    // Reload messages to show the new one with files
                    loadChatHistory(selectedChatUser);
                } else {
                    alert('Error uploading files: ' + data.error);
                }
            })
            .catch(error => {
                console.log('Error uploading files:', error);
                alert('Error uploading files: ' + error);
            });
        }

        function handleAdminFileSelection() {
            const fileInput = document.getElementById('adminChatFileInput');
            const files = fileInput.files;
            
            for (let i = 0; i < files.length; i++) {
                adminAttachedFiles.push(files[i]);
            }
            
            updateAdminFilePreview();
            fileInput.value = ''; // Reset file input
        }

        function updateAdminFilePreview() {
            const preview = document.getElementById('adminFilePreview');
            
            if (adminAttachedFiles.length === 0) {
                preview.style.display = 'none';
                preview.innerHTML = '';
                return;
            }
            
            preview.style.display = 'block';
            preview.innerHTML = '<strong>Attached files:</strong>';
            
            adminAttachedFiles.forEach((file, index) => {
                const fileItem = document.createElement('div');
                fileItem.className = 'file-preview-item-admin';
                fileItem.innerHTML = `
                    <span class="file-preview-name-admin">${file.name} (${formatFileSize(file.size)})</span>
                    <button class="file-preview-remove-admin" onclick="removeAdminAttachedFile(${index})">Remove</button>
                `;
                preview.appendChild(fileItem);
            });
        }

        function removeAdminAttachedFile(index) {
            adminAttachedFiles.splice(index, 1);
            updateAdminFilePreview();
        }

        function formatFileSize(bytes) {
            if (bytes === 0) return '0 Bytes';
            const k = 1024;
            const sizes = ['Bytes', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }

        function connectAdminChatWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = protocol + '//' + window.location.host + '/admin/chat-ws';
            
            adminChatWebSocket = new WebSocket(wsUrl);
            
            adminChatWebSocket.onopen = function() {
                console.log('Admin Chat WebSocket connected');
            };
            
            adminChatWebSocket.onmessage = function(event) {
                const data = JSON.parse(event.data);
                if (data.type === 'new_message') {
                    // If we're currently chatting with this user, show the message
                    if (selectedChatUser === data.from_user) {
                        addAdminMessageToChat(data.message, data.from_user, data.timestamp, data.message_type, data.file_data);
                    }
                    // Reload user list to update unread counts
                    loadChatUsers();
                }
            };
            
            adminChatWebSocket.onclose = function() {
                console.log('Admin Chat WebSocket disconnected');
                setTimeout(connectAdminChatWebSocket, 5000);
            };
        }

        function startAdminChatAutoRefresh() {
            // Refresh chat users and messages every 3 seconds
            if (adminChatAutoRefresh) {
                clearInterval(adminChatAutoRefresh);
            }
            adminChatAutoRefresh = setInterval(() => {
                loadChatUsers();
                if (selectedChatUser) {
                    loadChatHistory(selectedChatUser);
                }
            }, 3000);
        }

        function stopAdminChatAutoRefresh() {
            if (adminChatAutoRefresh) {
                clearInterval(adminChatAutoRefresh);
                adminChatAutoRefresh = null;
            }
        }

        // Handle Enter key in admin chat
        document.getElementById('adminChatInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                sendAdminMessage();
            }
        });

        // Handle file input change for admin chat
        document.getElementById('adminChatFileInput').addEventListener('change', function() {
            handleAdminFileSelection();
        });

        // Load initial data
        loadStatus();
        loadUsers();
        loadWorkflowSettings();
        setInterval(loadStatus, 5000); // Refresh every 5 seconds
    </script>
</body>
</html>"""

# === FUNCȚII UTILITARE ===
def get_local_ip():
    """Obține adresa IP locală"""
    try:
        # Conectare la un server extern pentru a afla IP-ul local
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def set_security_headers(handler):
    """Set security headers for all responses"""
    handler.set_header("X-Content-Type-Options", "nosniff")
    handler.set_header("X-Frame-Options", "DENY")
    handler.set_header("X-XSS-Protection", "1; mode=block")
    handler.set_header("Referrer-Policy", "strict-origin-when-cross-origin")

# === INITIALIZARE ===
def initialize_instances():
    """Initializează statusul instanțelor"""
    global comfy_instances_ready
    for user_data in USERS.values():
        comfy_instances_ready[user_data["comfy_url"]] = False

# === VERIFICARE INSTANȚE ===
def check_comfy_ready():
    """Verifică toate instanțele ComfyUI"""
    for user_data in USERS.values():
        threading.Thread(target=check_single_instance, args=(user_data["comfy_url"],), daemon=True).start()
    
    for instance_url in EXTERNAL_INSTANCES.values():
        threading.Thread(target=check_single_instance, args=(instance_url,), daemon=True).start()

def check_single_instance(comfy_url):
    """Verifică o singură instanță ComfyUI"""
    global comfy_instances_ready
    for i in range(60):  # 60 de încercări
        try:
            r = requests.get(f"{comfy_url}/", timeout=2)
            if r.status_code == 200:
                comfy_instances_ready[comfy_url] = True
                log.info(f"ComfyUI instance {comfy_url} is ready!")
                return
        except Exception as e:
            if i % 10 == 0:
                log.info(f"Waiting for ComfyUI {comfy_url}... ({i}/60)")
        time.sleep(1)
    log.error(f"ComfyUI instance {comfy_url} failed to start within timeout")

# === MANAGEMENT SESIUNI ===
def get_about_modal_without_buttons():
    """Returnează ABOUT_MODAL_HTML fără butoanele de workflow și chat"""
    about_modal = ABOUT_MODAL_HTML.replace(
        """<!-- Save Workflow Button - buton plat cu animație -->
<button class="workflow-save-btn" onclick="saveCurrentWorkflow()" title="Save Workflow">
    SAVW
</button>

<!-- Workflow Browser Button - buton plat cu animație -->
<button class="workflow-btn" onclick="openWorkflowBrowser()" title="Workflow Browser">
    LODW
</button>""", 
        ""
    )
    
    about_modal = about_modal.replace(
        """<!-- Chat Button - rămâne pe dreapta -->
<button class="chat-btn" onclick="toggleChatModal()" id="chatButton">
    MSG
    <div class="chat-notification" id="chatNotification">!</div>
</button>""",
        ""
    )
    
    return about_modal

def cleanup_sessions():
    """Curăță sesiunile expirate"""
    current_time = time.time()
    expired_sessions = []
    
    for session_id, session_data in sessions.items():
        username = session_data["user"]
        user_timeout = USERS.get(username, {}).get("session_timeout", 60)
        
        # Dacă timeout-ul este 0, sesiunea nu expiră niciodată
        if user_timeout > 0:
            timeout_seconds = user_timeout * 60
            if current_time - session_data["created"] > timeout_seconds:
                expired_sessions.append(session_id)
                if username in USERS:
                    USERS[username]["instances"] = max(0, USERS[username]["instances"] - 1)
    
    for session_id in expired_sessions:
        del sessions[session_id]

def cleanup_admin_sessions():
    """Curăță sesiunile admin expirate"""
    current_time = time.time()
    expired_sessions = []
    
    for session_id, session_data in admin_sessions.items():
        if current_time - session_data["created"] > DEFAULT_SESSION_TIMEOUT:
            expired_sessions.append(session_id)
    
    for session_id in expired_sessions:
        del admin_sessions[session_id]

def cleanup_blocked_users():
    """Curăță userii blocați care au expirat"""
    current_time = time.time()
    expired_users = []
    
    for username, expiry_time in BLOCKED_USERS.items():
        if current_time > expiry_time:
            expired_users.append(username)
    
    for username in expired_users:
        del BLOCKED_USERS[username]

def cleanup_forced_logout_sessions():
    """Curăță sesiunile forțate pentru delogare"""
    current_time = time.time()
    expired_sessions = []
    
    for session_id in FORCED_LOGOUT_SESSIONS:
        # Șterge sesiunea forțată după 1 minut
        if session_id not in sessions:
            expired_sessions.append(session_id)
    
    for session_id in expired_sessions:
        FORCED_LOGOUT_SESSIONS.discard(session_id)

def can_user_login(username):
    """Verifică dacă userul mai poate crea o instanță"""
    if username not in USERS:
        return False
    
    # Verifică dacă utilizatorul este activ
    if not USERS[username].get("enabled", True):
        return False
    
    # Curăță userii blocați expirați
    cleanup_blocked_users()
    
    # Verifică dacă userul este blocat
    if username in BLOCKED_USERS:
        if time.time() < BLOCKED_USERS[username]:
            return False
        else:
            # Șterge restricția după expirare
            del BLOCKED_USERS[username]
    
    user_data = USERS[username]
    # Dacă max_instances este 0, înseamnă nelimitat
    if user_data["max_instances"] == 0:
        return True
    
    return user_data["instances"] < user_data["max_instances"]

def create_session(username):
    """Creează o sesiune nouă"""
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
    """Creează o sesiune admin nouă"""
    session_id = str(uuid.uuid4())
    admin_sessions[session_id] = {
        "authenticated": True,
        "created": time.time()
    }
    return session_id

def get_session(session_id):
    """Obține sesiunea"""
    if not session_id:
        return None
    
    cleanup_sessions()
    cleanup_forced_logout_sessions()
    return sessions.get(session_id)

def get_admin_session(session_id):
    """Obține sesiunea admin"""
    if not session_id:
        return None
    
    cleanup_admin_sessions()
    return admin_sessions.get(session_id)

def is_authenticated(handler):
    """Verifică autentificarea"""
    session_id = handler.get_secure_cookie("session_id")
    if not session_id:
        return False
    
    session_data = get_session(session_id.decode())
    return session_data and session_data["authenticated"]

def is_admin_authenticated(handler):
    """Verifică autentificarea admin"""
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
        """Safe HTML rendering that replaces placeholders without .format() limitations"""
        result = template
        for key, value in kwargs.items():
            placeholder = "{" + key + "}"
            result = result.replace(placeholder, str(value))
        return result
    def prepare(self):
        """Set security headers for all requests"""
        set_security_headers(self)

class LoginHandler(BaseHandler):
    def get_client_ip(self):
        """Get client IP address considering proxies"""
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
        
        # Check if IP is blocked
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
        
        self.write(LOGIN_HTML.format(error="", about_modal=ABOUT_MODAL_HTML))

    def post(self):
        client_ip = self.get_client_ip()
        
        # Check rate limiting
        if RateLimiter.is_blocked(client_ip):
            self.set_status(429)
            self.write({"error": "Too many attempts"})
            return
        
        user = self.get_argument("username", "").strip()
        pwd = self.get_argument("password", "")

        # Input validation
        if not validate_username(user) or not validate_password(pwd):
            RateLimiter.record_failed_attempt(client_ip)
            log.warning(f"Invalid input format from IP {client_ip}")
            self.write(LOGIN_HTML.format(
                error='<p class="error">Invalid username or password format!</p>', 
                about_modal=ABOUT_MODAL_HTML
            ))
            return

        if user in USERS and check_password(USERS[user]["password"], pwd):
            # Verifică dacă utilizatorul este activ
            if not USERS[user].get("enabled", True):
                log.warning(f"Disabled user {user} tried to login from IP {client_ip}")
                self.write(LOGIN_HTML.format(
                    error='<p class="error">This user account is disabled!</p>', 
                    about_modal=ABOUT_MODAL_HTML
                ))
                return
                
            if can_user_login(user):
                session_id = create_session(user)
                self.set_secure_cookie("session_id", session_id, expires_days=1)
                
                # Clear failed attempts on successful login
                RateLimiter.clear_attempts(client_ip)
                
                log.info(f"User {user} logged in successfully from IP {client_ip}")
                self.redirect("/comfy/")
            else:
                # Check if user is blocked
                if user in BLOCKED_USERS:
                    remaining_time = BLOCKED_USERS[user] - time.time()
                    if remaining_time > 0:
                        minutes = int(remaining_time // 60)
                        seconds = int(remaining_time % 60)
                        self.write(FORCED_LOGOUT_HTML.replace("{about_modal}", ABOUT_MODAL_HTML))
                        return
                
                log.warning(f"User {user} tried to login but limit reached from IP {client_ip}")
                self.write(USER_FULL_HTML.format(
                    username=user, 
                    max_instances=USERS[user]["max_instances"], 
                    about_modal=ABOUT_MODAL_HTML
                ))
        else:
            RateLimiter.record_failed_attempt(client_ip)
            log.warning(f"Failed login attempt for user {user} from IP {client_ip}")
            self.write(LOGIN_HTML.format(
                error='<p class="error">Invalid credentials!</p>', 
                about_modal=ABOUT_MODAL_HTML
            ))

class UserStatusHandler(BaseHandler):
    def get(self):
        """Returnează statusul tuturor utilizatorilor pentru afișare la login"""
        user_status = []
        
        # Sortează utilizatorii alfabetic
        sorted_users = sorted(USERS.items(), key=lambda x: x[0].lower())
        
        for username, user_data in sorted_users:
            user_status.append({
                "username": username,
                "instances": user_data["instances"],
                "max_instances": user_data["max_instances"],
                "ready": comfy_instances_ready.get(user_data["comfy_url"], False),
                "enabled": user_data.get("enabled", True)  # Adaugă starea de activare
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
        
        # Validare
        if not new_username or not current_password:
            self.write({"success": False, "error": "Username and current password are required"})
            return
        
        if not validate_username(new_username):
            self.write({"success": False, "error": "Invalid username format"})
            return
        
        # Verifică parola curentă
        if not check_password(USERS[current_username]["password"], current_password):
            self.write({"success": False, "error": "Current password is incorrect"})
            return
        
        # Verifică dacă noul username există deja (dacă s-a schimbat)
        if new_username != current_username and new_username in USERS:
            self.write({"success": False, "error": "Username already exists"})
            return
        
        # Actualizează datele utilizatorului
        try:
            # Dacă username-ul s-a schimbat
            if new_username != current_username:
                # Mută userul la noul username
                USERS[new_username] = USERS.pop(current_username)
                # Actualizează sesiunile active
                for session_id, session_data in sessions.items():
                    if session_data["user"] == current_username:
                        session_data["user"] = new_username
            
            # Dacă s-a furnizat o nouă parolă, o hash-ui
            if new_password:
                USERS[new_username]["password"] = hash_password(new_password)
            
            # Salvează configurația
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
        
        # Create a safe version of the logout HTML without formatting issues
        safe_logout_html = LOGOUT_HTML.replace("{about_modal}", ABOUT_MODAL_HTML)
        self.write(safe_logout_html)

# Adaugă acest handler
class DebugHandler(BaseHandler):
    def get(self):
        self.write("""
        <html>
        <head>
            <title>Debug Test</title>
            <style>
                body { background: #1a1a1a; color: white; font-family: Arial; padding: 20px; }
                .test { background: #007bff; color: white; padding: 10px; margin: 10px; border-radius: 5px; }
            </style>
        </head>
        <body>
            <h1>Debug Test Page</h1>
            <div class="test">If you can see this, HTML is loading correctly</div>
            <button onclick="alert('JavaScript works!')">Test JavaScript</button>
            <script>
                console.log('Debug page loaded successfully');
                document.addEventListener('DOMContentLoaded', function() {
                    console.log('DOM fully loaded');
                });
            </script>
        </body>
        </html>
        """)

# Adaugă la rute:
(r"/debug", DebugHandler),

# === SIMPLE IFRAME SOLUTION ===
class ComfyUIIframeHandler(BaseHandler):
    def get(self):
        if not is_authenticated(self):
            self.redirect("/login")
            return
        
        session_id = self.get_secure_cookie("session_id").decode()
        session_data = get_session(session_id)
        username = session_data["user"]
        comfy_url = session_data["comfy_url"]
        
        # Generate a secure token for the iframe
        import hashlib
        import hmac
        token = hmac.new(
            b"comfyui_auth_token_2025",
            f"{session_id}{username}{int(time.time())}".encode(),
            hashlib.sha256
        ).hexdigest()[:32]
        
        self.write(f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>ComfyUI - {username}</title>
            <meta charset="utf-8">
            <style>
                body {{
                    margin: 0;
                    padding: 0;
                    overflow: hidden;
                    background: #1a1a1a;
                    font-family: 'Segoe UI', Arial, sans-serif;
                }}
                .auth-header {{
                    position: fixed;
                    top: 0;
                    left: 0;
                    right: 0;
                    background: rgba(42, 42, 42, 0.95);
                    padding: 10px 20px;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    z-index: 10000;
                    border-bottom: 1px solid #444;
                    backdrop-filter: blur(10px);
                }}
                .user-info {{
                    color: #007bff;
                    font-weight: bold;
                }}
                .auth-buttons {{
                    display: flex;
                    gap: 10px;
                }}
                .auth-btn {{
                    background: #007bff;
                    color: white;
                    border: none;
                    padding: 8px 15px;
                    border-radius: 5px;
                    cursor: pointer;
                    font-size: 14px;
                    text-decoration: none;
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                }}
                .auth-btn.logout {{
                    background: #dc3545;
                }}
                .auth-btn.settings {{
                    background: #28a745;
                }}
                .auth-btn:hover {{
                    opacity: 0.9;
                }}
                .chat-btn {{
                    position: fixed;
                    bottom: 20px;
                    right: 20px;
                    width: 50px;
                    height: 50px;
                    background: #007bff;
                    color: white;
                    border: none;
                    border-radius: 50%;
                    cursor: pointer;
                    z-index: 10000;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.3);
                }}
                .workflow-btn {{
                    position: fixed;
                    bottom: 80px;
                    left: 20px;
                    background: #3a3a3a;
                    color: #007bff;
                    border: none;
                    border-radius: 3px;
                    cursor: pointer;
                    padding: 8px 12px;
                    z-index: 10000;
                    font-size: 12px;
                    font-weight: 500;
                }}
                .workflow-save-btn {{
                    position: fixed;
                    bottom: 120px;
                    left: 20px;
                    background: #3a3a3a;
                    color: #17a2b8;
                    border: none;
                    border-radius: 3px;
                    cursor: pointer;
                    padding: 8px 12px;
                    z-index: 10000;
                    font-size: 12px;
                    font-weight: 500;
                }}
                #comfyIframe {{
                    position: fixed;
                    top: 50px;
                    left: 0;
                    width: 100%;
                    height: calc(100% - 50px);
                    border: none;
                    z-index: 1;
                }}
                .session-timer {{
                    position: fixed;
                    top: 60px;
                    left: 15px;
                    color: #ffc107;
                    background: rgba(0,0,0,0.7);
                    padding: 8px 12px;
                    border-radius: 5px;
                    z-index: 10001;
                    font-size: 12px;
                    font-weight: bold;
                    border: 1px solid #ffc107;
                    display: none;
                }}
            </style>
            {ABOUT_MODAL_HTML}
        </head>
        <body>
            <div class="auth-header">
                <div class="user-info">Welcome, {username}</div>
                <div class="auth-buttons">
                    <button class="auth-btn" onclick="openAboutModal()">About</button>
                    <button class="auth-btn settings" onclick="openUserSettingsModal()">Settings</button>
                    <a href="/logout" class="auth-btn logout">Logout</a>
                </div>
            </div>
            
            <div class="session-timer" id="sessionTimer"></div>
            
            <button class="workflow-save-btn" onclick="saveCurrentWorkflow()">Save Workflow</button>
            <button class="workflow-btn" onclick="openWorkflowBrowser()">Load Workflow</button>
            <button class="chat-btn" onclick="toggleChatModal()" id="chatButton">
                Chat
                <div class="chat-notification" id="chatNotification" style="display:none">!</div>
            </button>
            
            <iframe id="comfyIframe" src="{comfy_url}?auth_token={token}&embedded=true"></iframe>
            
            <script>
                // Session monitoring
                function checkSession() {{
                    fetch('/check-session')
                        .then(r => r.json())
                        .then(data => {{
                            if (data.status === 'authenticated' && data.time_remaining && data.time_remaining <= 60) {{
                                document.getElementById('sessionTimer').style.display = 'block';
                                document.getElementById('sessionTimer').innerHTML = 
                                    `<strong>Session expires in:</strong> ${{Math.floor(data.time_remaining/60)}}:${{(data.time_remaining%60).toString().padStart(2,'0')}}`;
                            }} else {{
                                document.getElementById('sessionTimer').style.display = 'none';
                            }}
                        }});
                }}
                
                // Chat functions
                function toggleChatModal() {{
                    const modal = document.getElementById('chatModal');
                    modal.style.display = modal.style.display === 'flex' ? 'none' : 'flex';
                }}
                
                function openUserSettingsModal() {{
                    document.getElementById('userSettingsModal').style.display = 'block';
                }}
                
                // Workflow functions
                function saveCurrentWorkflow() {{
                    const iframe = document.getElementById('comfyIframe');
                    iframe.contentWindow.postMessage({{type: 'saveWorkflow'}}, '*');
                }}
                
                function openWorkflowBrowser() {{
                    const iframe = document.getElementById('comfyIframe');
                    iframe.contentWindow.postMessage({{type: 'loadWorkflow'}}, '*');
                }}
                
                // Initialize
                setInterval(checkSession, 5000);
                checkSession();
                
                // Handle messages from iframe
                window.addEventListener('message', function(event) {{
                    if (event.data.type === 'workflowSaved') {{
                        alert('Workflow saved successfully!');
                    }} else if (event.data.type === 'workflowLoaded') {{
                        alert('Workflow loaded successfully!');
                    }}
                }});
            </script>
        </body>
        </html>
        """)

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
        
        # Securitate: verifică că filename nu conține path traversal
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
        
        # Asigură-te că filename se termină cu .json
        if not filename.endswith('.json'):
            filename += '.json'
        
        # Securitate: verifică că filename nu conține path traversal
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
        
        # Securitate: verifică că filename nu conține path traversal
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

# === CHAT HANDLERS - IMPROVED ===
class ChatMessagesHandler(BaseHandler):
    def get(self):
        if not is_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = self.get_secure_cookie("session_id").decode()
        session_data = get_session(session_id)
        username = session_data["user"]
        
        # Initialize chat for user if not exists
        if username not in CHAT_MESSAGES:
            CHAT_MESSAGES[username] = []
        
        # Calculate unread count
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
        
        # Initialize chat for user if not exists
        if username not in CHAT_MESSAGES:
            CHAT_MESSAGES[username] = []
        
        # Add message
        message_data = {
            "from": "user",
            "message": message,
            "timestamp": time.time(),
            "read": True,  # User's own messages are always read
            "message_type": message_type,
            "file_data": file_data
        }
        CHAT_MESSAGES[username].append(message_data)
        
        # Notify admin via WebSocket
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
        
        # Notify user via their WebSocket if connected
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
        
        # Initialize chat for user if not exists
        if username not in CHAT_MESSAGES:
            CHAT_MESSAGES[username] = []
        
        file_data_list = []
        
        # Process uploaded files
        for field_name, files in self.request.files.items():
            if field_name.startswith('file'):
                for file_info in files:
                    filename = file_info['filename']
                    file_body = file_info['body']
                    content_type = file_info['content_type']
                    
                    # Save file and get file ID
                    file_id = save_chat_file(filename, file_body, content_type, username)
                    
                    file_data_list.append({
                        "id": file_id,
                        "filename": filename,
                        "size": len(file_body),
                        "content_type": content_type
                    })
        
        # Create message text
        if not message:
            if len(file_data_list) == 1:
                message = f"Sent file: {file_data_list[0]['filename']}"
            else:
                message = f"Sent {len(file_data_list)} files"
        
        # Add message with file data
        message_data = {
            "from": "user",
            "message": message,
            "timestamp": time.time(),
            "read": True,  # User's own messages are always read
            "message_type": "file" if file_data_list else "text",
            "file_data": file_data_list[0] if len(file_data_list) == 1 else None
        }
        CHAT_MESSAGES[username].append(message_data)
        
        # Notify admin via WebSocket
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
        
        # Notify user via their WebSocket if connected
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
        
        # Set appropriate headers for file download
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
        
        # Initialize chat for user if not exists
        if self.username not in CHAT_MESSAGES:
            CHAT_MESSAGES[self.username] = []
        
        # Add WebSocket to user's connections
        if self.username not in USER_CHAT_WEBSOCKETS:
            USER_CHAT_WEBSOCKETS[self.username] = []
        USER_CHAT_WEBSOCKETS[self.username].append(self)
        
        # Send current unread count
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
                    # Add message
                    message_data = {
                        "from": "user",
                        "message": message_text,
                        "timestamp": time.time(),
                        "read": True,  # User's own messages are always read
                        "message_type": message_type,
                        "file_data": file_data
                    }
                    CHAT_MESSAGES[self.username].append(message_data)
                    
                    # Notify admin via WebSocket
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
                    
                    # Send confirmation to user
                    self.write_message(json.dumps({
                        "type": "message_sent",
                        "success": True
                    }))
            
            elif data.get("type") == "typing":
                # Handle typing indicator
                typing = data.get("typing", False)
                
                # Notify admin
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
                # Mark all admin messages as read
                if self.username in CHAT_MESSAGES:
                    for msg in CHAT_MESSAGES[self.username]:
                        if msg["from"] == "admin":
                            msg["read"] = True
                
                # Send updated unread count
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
        
        # Verifică dacă sesiunea a fost forțată să se delogheze
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
                
                # Verifică dacă mai este mai puțin de 1 minut până la expirare
                if 0 < time_remaining <= 60:
                    self.write({
                        "status": "session_expiring_soon", 
                        "time_remaining": int(time_remaining),
                        "user": username
                    })
                    return
                elif time_remaining <= 0:
                    # Sesiunea a expirat
                    if username in USERS:
                        USERS[username]["instances"] = max(0, USERS[username]["instances"] - 1)
                    del sessions[session_id]
                    self.clear_cookie("session_id")
                    self.write({"status": "session_expired"})
                    return
            
            self.write({
                "status": "authenticated", 
                "user": username, 
                "time_remaining": int(time_remaining) if user_timeout > 0 else None
            })
        else:
            self.write({"status": "not_authenticated"})

# === SESSION REFRESH HANDLER ===
class SessionRefreshHandler(BaseHandler):
    def post(self):
        session_id = self.get_secure_cookie("session_id")
        
        if not session_id:
            self.write({"success": False, "error": "Not authenticated"})
            return
        
        session_id = session_id.decode()
        session_data = get_session(session_id)
        
        if session_data:
            # Reînnoiește timestamp-ul sesiunii cu +60 minute
            session_data["created"] = time.time()
            
            # Actualizează și timeout-ul utilizatorului dacă este necesar
            username = session_data["user"]
            if username in USERS:
                user_timeout = USERS[username].get("session_timeout", 60)
                if user_timeout > 0:
                    # Resetează timeout-ul la valoarea inițială
                    session_data["timeout_seconds"] = user_timeout * 60
            
            log.info(f"Session refreshed for user {username}")
            self.write({"success": True, "message": "Session extended by 60 minutes"})
        else:
            self.write({"success": False, "error": "Session not found"})
# === ADMIN AUTH HANDLERS ===
class AdminLoginHandler(BaseHandler):
    def get_client_ip(self):
        """Get client IP address considering proxies"""
        real_ip = self.request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
        forwarded_for = self.request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return self.request.remote_ip

    def get(self):
        if is_admin_authenticated(self):
            self.redirect("/admin/")
            return
        
        client_ip = self.get_client_ip()
        
        # Check if IP is blocked
        if RateLimiter.is_blocked(client_ip):
            self.write("""
            <div style="text-align: center; margin-top: 100px;">
                <h2 style="color: #dc3545;">Too Many Failed Attempts</h2>
                <p>Your IP has been temporarily blocked due to too many failed login attempts.</p>
                <p>Please try again in {minutes} minutes and {seconds} seconds.</p>
                <a href="/admin/login">Try Again</a>
            </div>
            """)
            return
        
        # Folosim ABOUT_MODAL_HTML fără butoanele de workflow și chat
        self.write(ADMIN_LOGIN_HTML.format(error="", about_modal=get_about_modal_without_buttons()))
    
    def post(self):
        client_ip = self.get_client_ip()
        
        # Check rate limiting
        if RateLimiter.is_blocked(client_ip):
            self.set_status(429)
            self.write({"error": "Too many attempts"})
            return
        
        password = self.get_argument("password", "")
        
        if check_password(ADMIN_CONFIG["password"], password):
            session_id = create_admin_session()
            self.set_secure_cookie("admin_session_id", session_id, expires_days=1)
            
            # Clear failed attempts on successful login
            RateLimiter.clear_attempts(client_ip)
            
            log.info(f"Admin logged in successfully from IP {client_ip}")
            self.redirect("/admin/")
        else:
            RateLimiter.record_failed_attempt(client_ip)
            log.warning(f"Failed admin login attempt from IP {client_ip}")
            
            # Folosim ABOUT_MODAL_HTML fără butoanele de workflow și chat
            self.write(ADMIN_LOGIN_HTML.format(
                error='<p class="error">Invalid admin password!</p>', 
                about_modal=get_about_modal_without_buttons()
            ))

class AdminLogoutHandler(BaseHandler):
    def post(self):
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
        self.write(ADMIN_HTML)

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
                "enabled": user_data.get("enabled", True)
            }
        
        self.write({
            "users": user_status,
            "total_sessions": len(sessions)
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
            
            # Marchează sesiunea pentru delogare forțată
            FORCED_LOGOUT_SESSIONS.add(session_id)
            
            # Blochează userul pentru 5 minute
            BLOCKED_USERS[username] = time.time() + 300  # 5 minute
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
            # Hash the password before storing
            hashed_password = hash_password(data.get("password", "comfy.123"))
            
            USERS[username] = {
                "password": hashed_password,
                "comfy_url": data.get("comfy_url", f"http://127.0.0.1:8189"),
                "instances": 0,
                "max_instances": data.get("max_instances", 2),
                "session_timeout": data.get("session_timeout", 60),
                "enabled": data.get("enabled", True)
            }
            
            # Verifică noua instanță
            comfy_url = USERS[username]["comfy_url"]
            if comfy_url not in comfy_instances_ready:
                comfy_instances_ready[comfy_url] = False
                threading.Thread(target=check_single_instance, args=(comfy_url,), daemon=True).start()
            
            log.info(f"Admin added user: {username} (timeout: {USERS[username]['session_timeout']} minutes, enabled: {USERS[username]['enabled']})")
            
            # Salvează configurația
            save_config()
            
            self.set_status(201)
        else:
            self.set_status(400)
    
    def put(self, username):
        """Update an existing user"""
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
        
        # Dacă username-ul s-a schimbat, verifică dacă noul username există deja
        if new_username != username and new_username in USERS:
            self.set_status(400)
            self.write({"success": False, "error": "Username already exists"})
            return
        
        try:
            # Păstrează numărul de instanțe active
            instances = USERS[username]["instances"]
            
            # Actualizează datele utilizatorului
            user_data = USERS[username]
            user_data["comfy_url"] = data.get("comfy_url", user_data["comfy_url"])
            user_data["max_instances"] = data.get("max_instances", user_data["max_instances"])
            user_data["session_timeout"] = data.get("session_timeout", user_data["session_timeout"])
            user_data["enabled"] = data.get("enabled", user_data.get("enabled", True))
            
            # Dacă s-a furnizat o nouă parolă, o hash-ui
            new_password = data.get("password")
            if new_password:
                user_data["password"] = hash_password(new_password)
            
            # Dacă username-ul s-a schimbat, mută userul
            if new_username != username:
                USERS[new_username] = USERS.pop(username)
                # Actualizează sesiunile active
                for session_id, session_data in sessions.items():
                    if session_data["user"] == username:
                        session_data["user"] = new_username
                        session_data["comfy_url"] = user_data["comfy_url"]
            
            # Verifică noua instanță dacă URL-ul s-a schimbat
            comfy_url = user_data["comfy_url"]
            if comfy_url not in comfy_instances_ready:
                comfy_instances_ready[comfy_url] = False
                threading.Thread(target=check_single_instance, args=(comfy_url,), daemon=True).start()
            
            log.info(f"Admin updated user: {username} -> {new_username} (enabled: {user_data['enabled']})")
            
            # Salvează configurația
            save_config()
            
            self.write({"success": True, "message": "User updated successfully"})
            
        except Exception as e:
            log.error(f"Error updating user {username}: {e}")
            self.set_status(500)
            self.write({"success": False, "error": f"Internal server error: {str(e)}"})
    
    def delete(self, username):
        """Delete a user"""
        if not is_admin_authenticated(self):
            self.set_status(401)
            self.write({"success": False, "error": "Not authenticated"})
            return
            
        if username not in USERS:
            self.set_status(404)
            self.write({"success": False, "error": "User not found"})
            return
        
        try:
            # Verifică dacă utilizatorul are sesiuni active
            if USERS[username]["instances"] > 0:
                # Force logout all active sessions for this user
                sessions_to_delete = []
                for session_id, session_data in sessions.items():
                    if session_data["user"] == username:
                        sessions_to_delete.append(session_id)
                        FORCED_LOGOUT_SESSIONS.add(session_id)
                
                # Șterge sesiunile
                for session_id in sessions_to_delete:
                    del sessions[session_id]
            
            # Șterge utilizatorul
            del USERS[username]
            
            # Salvează configurația
            save_config()
            
            log.info(f"Admin deleted user: {username}")
            self.write({"success": True, "message": f"User {username} deleted successfully"})
            
        except Exception as e:
            log.error(f"Error deleting user {username}: {e}")
            self.set_status(500)
            self.write({"success": False, "error": f"Internal server error: {str(e)}"})

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
        
        # Salvează noua cale în configurație
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
        
        # Initialize chat for user if not exists
        if to_user not in CHAT_MESSAGES:
            CHAT_MESSAGES[to_user] = []
        
        # Add message
        message_data = {
            "from": "admin",
            "message": message,
            "timestamp": time.time(),
            "read": False  # Mark as unread until user reads it
        }
        CHAT_MESSAGES[to_user].append(message_data)
        
        # Notify user via WebSocket if connected
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
        
        # Initialize chat for user if not exists
        if to_user not in CHAT_MESSAGES:
            CHAT_MESSAGES[to_user] = []
        
        file_data_list = []
        
        # Process uploaded files
        for field_name, files in self.request.files.items():
            if field_name.startswith('file'):
                for file_info in files:
                    filename = file_info['filename']
                    file_body = file_info['body']
                    content_type = file_info['content_type']
                    
                    # Save file and get file ID
                    file_id = save_chat_file(filename, file_body, content_type, "admin")
                    
                    file_data_list.append({
                        "id": file_id,
                        "filename": filename,
                        "size": len(file_body),
                        "content_type": content_type
                    })
        
        # Create message text
        if not message:
            if len(file_data_list) == 1:
                message = f"Sent file: {file_data_list[0]['filename']}"
            else:
                message = f"Sent {len(file_data_list)} files"
        
        # Add message with file data
        message_data = {
            "from": "admin",
            "message": message,
            "timestamp": time.time(),
            "read": False,  # Mark as unread until user reads it
            "message_type": "file" if file_data_list else "text",
            "file_data": file_data_list[0] if len(file_data_list) == 1 else None
        }
        CHAT_MESSAGES[to_user].append(message_data)
        
        # Notify user via WebSocket if connected
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
                    # Initialize chat for user if not exists
                    if to_user not in CHAT_MESSAGES:
                        CHAT_MESSAGES[to_user] = []
                    
                    # Add message
                    message_data = {
                        "from": "admin",
                        "message": message_text,
                        "timestamp": time.time(),
                        "read": False  # Mark as unread until user reads it
                    }
                    CHAT_MESSAGES[to_user].append(message_data)
                    
                    # Notify user via WebSocket if connected
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
                    
                    # Send confirmation to admin
                    self.write_message(json.dumps({
                        "type": "message_sent",
                        "success": True
                    }))
            
            elif data.get("type") == "user_typing":
                # Handle typing indicator from user
                username = data.get("username")
                typing = data.get("typing", False)
                
                # Update typing status
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
            
        # Toggle security
        ADMIN_CONFIG["enabled"] = not ADMIN_CONFIG["enabled"]
        log.info(f"Admin security toggled to: {ADMIN_CONFIG['enabled']}")
        
        # Salvează configurația
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
        
        # Salvează configurația
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
        
        # Curăță userii blocați expirați
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
# === SIMPLE PROXY FOR STATIC FILES ===
class StaticFileProxyHandler(BaseHandler):
    async def get(self, path):
        """Simple proxy for static files that might be blocked by CSP"""
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
            response = await client.fetch(target_url, raise_error=False)
            
            self.set_status(response.code)
            
            # Copy headers
            for header, value in response.headers.get_all():
                if header.lower() not in ['content-length', 'content-encoding', 
                                        'transfer-encoding', 'connection']:
                    self.set_header(header, value)
            
            if response.code != 304:
                self.write(response.body)
                
        except Exception as e:
            log.error(f"Static proxy error: {e}")
            self.set_status(500)
# === PROXY HANDLER ===
class MultiInstanceProxyHandler(BaseHandler):
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "x-requested-with, content-type, authorization")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
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
    
    def get_user_comfy_url(self):
        session_id = self.get_secure_cookie("session_id")
        if not session_id:
            return None
        
        session_data = get_session(session_id.decode())
        return session_data.get("comfy_url") if session_data else None
    
    async def _proxy_request(self, method, path):
        session_id = self.get_secure_cookie("session_id")
        
        # Verifică dacă sesiunea a fost forțată să se delogheze
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
        
        # Verifică dacă sesiunea a expirat
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
                        self.write(SESSION_EXPIRED_HTML.replace("{about_modal}", ABOUT_MODAL_HTML))
                        return
        
        if not is_authenticated(self):
            if path and path.startswith(('api/', 'view/', 'upload/', 'websocket')):
                self.set_status(401)
                self.write({"error": "Not authenticated"})
                return
            elif not path or not path.startswith(('assets/', 'static/', 'favicon.', 'login', 'logout')):
                self.redirect("/login")
                return
        
        comfy_url = self.get_user_comfy_url()
        if not comfy_url:
            self.redirect("/login")
            return
            
        if not comfy_instances_ready.get(comfy_url, False) and not (path and path.startswith(('login', 'logout', 'health', 'waiting'))):
            self.write(WAITING_HTML.replace("{about_modal}", ABOUT_MODAL_HTML))
            return

        # DECODARE CORECTĂ A URL-ULUI
        try:
            if path:
                path = unquote(path)
                path = path.replace('%2F', '/')
                path = path.replace('%20', ' ')
                
        except Exception as e:
            log.warning(f"Error decoding path {path}: {e}")

        target_url = f"{comfy_url}/{path}" if path else comfy_url
        if self.request.query:
            target_url += "?" + self.request.query
        
        session_data = get_session(self.get_secure_cookie("session_id").decode())
        username = session_data["user"] if session_data else "unknown"
        
        log.info(f"Proxying {method} {path} for user {username} to {target_url}")
        
        client = tornado.httpclient.AsyncHTTPClient()
        
        try:
            # CRITICAL: Headers for new ComfyUI
            headers = {}
            
            # Copy ALL headers except problematic ones
            exclude_headers = ['host', 'content-length', 'connection', 'keep-alive']
            
            for header_name, header_value in self.request.headers.items():
                if header_name.lower() not in exclude_headers:
                    headers[header_name] = header_value
            
            # Add essential headers
            headers['Origin'] = f"{self.request.protocol}://{self.request.host}"
            headers['Referer'] = f"{self.request.protocol}://{self.request.host}/comfy/"
            headers['X-Forwarded-For'] = self.request.remote_ip
            headers['X-Forwarded-Host'] = self.request.host
            headers['X-Forwarded-Proto'] = self.request.protocol
            
            if session_id:
                headers['X-User-ID'] = username
                headers['X-Session-ID'] = session_id.decode()
            
            body = self.request.body if method in ["POST", "PUT", "DELETE"] else None
            
            req = tornado.httpclient.HTTPRequest(
                url=target_url,
                method=method,
                headers=headers,
                body=body,
                follow_redirects=False,
                connect_timeout=30,
                request_timeout=120,
                validate_cert=False,
                decompress_response=False,
                allow_nonstandard_methods=True
            )
            
            response = await client.fetch(req, raise_error=False)
            
            self.set_status(response.code)
            
            # Copy response headers
            for header, value in response.headers.get_all():
                header_lower = header.lower()
                if header_lower not in ['content-length', 'content-encoding', 
                                      'transfer-encoding', 'connection', 'keep-alive',
                                      'content-security-policy']:
                    self.set_header(header, value)
            
            # Set CORS headers
            self.set_header("Access-Control-Allow-Origin", "*")
            self.set_header("Access-Control-Allow-Credentials", "true")
            
            # Check if this is the main HTML page
            content_type = response.headers.get('Content-Type', '').lower()
            is_html_response = 'text/html' in content_type and response.code == 200
            is_root_path = (not path or path == "" or path == "comfy/" or 
                          path == "comfy" or (path.endswith('.html') and not path.startswith('api/')))
            
            if is_html_response and is_root_path and response.body:
                try:
                    # Detect encoding
                    encoding = 'utf-8'
                    if 'charset=' in content_type:
                        charset_match = re.search(r'charset=([\w-]+)', content_type, re.IGNORECASE)
                        if charset_match:
                            encoding = charset_match.group(1)
                    
                    html_content = response.body.decode(encoding, errors='replace')
                    
                    # DEBUG: Save original HTML for inspection
                    with open(f"/tmp/comfy_original_{username}.html", "w") as f:
                        f.write(html_content[:5000])  # First 5000 chars
                    
                    # CRITICAL FIX: Remove CSP headers from HTML
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
                    
                    # Find where to insert our code
                    # Method 1: Find the head tag
                    head_end_pos = html_content.find('</head>')
                    
                    if head_end_pos != -1:
                        # Insert our CSS and modal HTML before </head>
                        our_injection = f"""
                        {ABOUT_MODAL_HTML}
                        
                        <style id="comfy-auth-styles">
                        /* ComfyUI Auth Server Styles */
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
                            left: 50%;
                            transform: translateX(-50%);
                            color: white;
                            background: rgba(0,0,0,0.7);
                            padding: 5px 15px;
                            border-radius: 0 0 5px 5px;
                            z-index: 10001;
                            font-size: 12px;
                            text-align: center;
                            backdrop-filter: blur(5px);
                        }}
                        .comfy-auth-buttons {{
                            position: fixed;
                            top: 0;
                            right: 0;
                            z-index: 10001;
                            display: flex;
                            gap: 0;
                            height: 30px;
                        }}
                        .comfy-about-btn {{
                            background: #007bff;
                            color: white;
                            border: none;
                            padding: 0;
                            border-radius: 0 0 0 5px;
                            cursor: pointer;
                            font-size: 14px;
                            width: 30px;
                            height: 30px;
                            display: flex;
                            align-items: center;
                            justify-content: center;
                        }}
                        .comfy-settings-btn {{
                            background: #28a745;
                            color: white;
                            border: none;
                            padding: 0 8px;
                            border-radius: 0;
                            cursor: pointer;
                            font-size: 12px;
                            height: 30px;
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            min-width: 40px;
                        }}
                        .comfy-logout-btn {{
                            background: #dc3545;
                            color: white;
                            border: none;
                            padding: 0 10px;
                            border-radius: 0 0 5px 0;
                            cursor: pointer;
                            text-decoration: none;
                            font-size: 12px;
                            height: 30px;
                            display: flex;
                            align-items: center;
                        }}
                        .comfy-about-btn:hover {{ background: #0056b3; }}
                        .comfy-settings-btn:hover {{ background: #218838; }}
                        .comfy-logout-btn:hover {{ background: #c82333; }}
                        
                        .workflow-btn {{ 
                            position: fixed; 
                            bottom: 80px; 
                            left: 100px;
                            background: #3a3a3a; 
                            color: #007bff; 
                            border: none; 
                            border-radius: 3px; 
                            cursor: pointer; 
                            font-size: 12px; 
                            font-weight: 500;
                            display: flex; 
                            align-items: center; 
                            justify-content: center; 
                            box-shadow: 0 1px 3px rgba(0,0,0,0.3); 
                            z-index: 10001; 
                            padding: 8px 12px;
                            height: 32px;
                            transition: all 0.2s ease;
                            letter-spacing: 0.5px;
                        }}
                        .workflow-btn:hover {{ 
                            background: #007bff; 
                            color: white;
                            transform: scale(1.05);
                            box-shadow: 0 3px 8px rgba(0, 123, 255, 0.3);
                        }}
                        .workflow-save-btn {{
                            position: fixed;
                            bottom: 120px;
                            left: 100px;
                            background: #3a3a3a;
                            color: #17a2b8;
                            border: none;
                            border-radius: 3px;
                            cursor: pointer;
                            font-size: 12px;
                            font-weight: 500;
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            box-shadow: 0 1px 3px rgba(0,0,0,0.3);
                            z-index: 10001;
                            padding: 8px 12px;
                            height: 32px;
                            transition: all 0.2s ease;
                            letter-spacing: 0.5px;
                        }}
                        .workflow-save-btn:hover {{
                            background: #17a2b8;
                            color: white;
                            transform: scale(1.05);
                            box-shadow: 0 3px 8px rgba(23, 162, 184, 0.3);
                        }}
                        .chat-btn {{ 
                            position: fixed; 
                            bottom: 20px; 
                            right: 20px; 
                            width: 50px; 
                            height: 50px; 
                            background: #007bff; 
                            color: white; 
                            border: none; 
                            border-radius: 50%; 
                            cursor: pointer; 
                            font-size: 12px; 
                            display: flex; 
                            align-items: center; 
                            justify-content: center; 
                            box-shadow: 0 2px 10px rgba(0,0,0,0.3); 
                            z-index: 10001; 
                        }}
                        .chat-btn:hover {{ background: #0056b3; }}
                        .chat-notification {{ 
                            position: absolute; 
                            top: -5px; 
                            right: -5px; 
                            background: #dc3545; 
                            color: white; 
                            border-radius: 50%; 
                            width: 20px; 
                            height: 20px; 
                            font-size: 12px; 
                            display: none; 
                            align-items: center; 
                            justify-content: center; 
                        }}
                        .session-timer {{
                            position: fixed;
                            top: 60px;
                            left: 15px;
                            color: #ffc107;
                            background: rgba(0,0,0,0.7);
                            padding: 8px 12px;
                            border-radius: 5px;
                            z-index: 10001;
                            font-size: 12px;
                            font-weight: bold;
                            border: 1px solid #ffc107;
                            display: none;
                        }}
                        </style>
                        
                        <script id="comfy-auth-init">
                        // Wait for ComfyUI to load
                        document.addEventListener('DOMContentLoaded', function() {{
                            // Create our elements
                            const overlay = document.createElement('div');
                            overlay.className = 'comfy-auth-overlay';
                            
                            const userInfo = document.createElement('div');
                            userInfo.className = 'comfy-user-info';
                            userInfo.textContent = 'Welcome, {username}';
                            
                            const buttonsDiv = document.createElement('div');
                            buttonsDiv.className = 'comfy-auth-buttons';
                            buttonsDiv.innerHTML = `
                                <button class="comfy-about-btn" onclick="openAboutModal()">?</button>
                                <button class="comfy-settings-btn" onclick="openUserSettingsModal()" title="Settings">SET</button>
                                <a href="/logout" class="comfy-logout-btn">Logout</a>
                            `;
                            
                            const workflowSaveBtn = document.createElement('button');
                            workflowSaveBtn.className = 'workflow-save-btn user-only';
                            workflowSaveBtn.title = 'Save Workflow';
                            workflowSaveBtn.textContent = 'SAVW';
                            workflowSaveBtn.onclick = saveCurrentWorkflow;
                            
                            const workflowBtn = document.createElement('button');
                            workflowBtn.className = 'workflow-btn user-only';
                            workflowBtn.title = 'Workflow Browser';
                            workflowBtn.textContent = 'LODW';
                            workflowBtn.onclick = openWorkflowBrowser;
                            
                            const chatBtn = document.createElement('button');
                            chatBtn.className = 'chat-btn';
                            chatBtn.id = 'chatButton';
                            chatBtn.innerHTML = 'MSG <div class="chat-notification" id="chatNotification">!</div>';
                            chatBtn.onclick = toggleChatModal;
                            
                            // Add elements to body
                            document.body.appendChild(overlay);
                            document.body.appendChild(userInfo);
                            document.body.appendChild(buttonsDiv);
                            document.body.appendChild(workflowSaveBtn);
                            document.body.appendChild(workflowBtn);
                            document.body.appendChild(chatBtn);
                            
                            console.log('ComfyUI Auth Server UI injected successfully');
                            
                            // Start session monitoring
                            if (typeof initSessionMonitoring === 'function') {{
                                setTimeout(initSessionMonitoring, 1000);
                            }}
                            
                            // Connect to chat WebSocket
                            if (typeof connectChatWebSocket === 'function') {{
                                setTimeout(connectChatWebSocket, 2000);
                            }}
                        }});
                        
                        // Make functions available globally
                        window.openAboutModal = function() {{
                            document.getElementById('aboutModal').style.display = 'block';
                        }};
                        
                        window.openUserSettingsModal = function() {{
                            document.getElementById('userSettingsModal').style.display = 'block';
                        }};
                        
                        window.saveCurrentWorkflow = function() {{
                            alert('Save workflow function would be called here');
                        }};
                        
                        window.openWorkflowBrowser = function() {{
                            document.getElementById('workflowBrowserModal').style.display = 'block';
                        }};
                        
                        window.toggleChatModal = function() {{
                            const modal = document.getElementById('chatModal');
                            if (modal.style.display === 'flex') {{
                                modal.style.display = 'none';
                            }} else {{
                                modal.style.display = 'flex';
                            }}
                        }};
                        </script>
                        """
                        
                        html_content = html_content[:head_end_pos] + our_injection + html_content[head_end_pos:]
                        
                        # DEBUG: Save modified HTML
                        with open(f"/tmp/comfy_modified_{username}.html", "w") as f:
                            f.write(html_content[:5000])
                    
                    # Remove Content-Length header since we modified content
                    if 'Content-Length' in self._headers:
                        del self._headers['Content-Length']
                    
                    self.write(html_content.encode(encoding))
                    log.info(f"Successfully injected UI for user {username}")
                    
                except Exception as e:
                    log.error(f"Error modifying HTML: {str(e)}", exc_info=True)
                    # Fallback: send original content
                    self.write(response.body)
            else:
                # For non-HTML responses, send as-is
                if response.code != 304:
                    self.write(response.body)
                
        except Exception as e:
            log.error(f"Proxy error for {target_url}: {str(e)}", exc_info=True)
            self.set_status(502)
            self.write(f"Bad Gateway: {str(e)}")
# === WEBSOCKET PROXY ===
class MultiInstanceWebSocketProxy(tornado.websocket.WebSocketHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.comfy_ws = None
        self._running = True
        self.username = None
    
    def check_origin(self, origin):
        return True
        
    def get_user_comfy_ws_url(self):
        session_id = self.get_secure_cookie("session_id")
        if not session_id:
            return None
        
        session_data = get_session(session_id.decode())
        if not session_data:
            return None
        
        comfy_url = session_data.get("comfy_url", "http://127.0.0.1:8189")
        if comfy_url.startswith("https://"):
            return comfy_url.replace("https://", "wss://") + "/ws"
        else:
            return comfy_url.replace("http://", "ws://") + "/ws"
    
    async def open(self):
        if not is_authenticated(self):
            self.close(code=4001, reason="Not authenticated")
            return
        
        session_id = self.get_secure_cookie("session_id")
        session_data = get_session(session_id.decode())
        self.username = session_data["user"]
        
        comfy_ws_url = self.get_user_comfy_ws_url()
        if not comfy_ws_url:
            self.close(code=4002, reason="No ComfyUI instance")
            return
        
        try:
            # SIMPLE CONNECTION - no headers for compatibility
            self.comfy_ws = await tornado.websocket.websocket_connect(
                comfy_ws_url,
                ping_interval=30,  # Longer interval for stability
                connect_timeout=10,
                max_message_size=104857600,  # 100MB
            )
            
            log.info(f"WebSocket connected for user {self.username}")
            
            # Start bidirectional pipe
            asyncio.create_task(self._pipe_comfy_to_client())
            
        except Exception as e:
            log.error(f"WebSocket connection error: {e}")
            self.close(code=500, reason="Connection failed")
    
    async def _pipe_comfy_to_client(self):
        try:
            while self._running and self.comfy_ws:
                msg = await self.comfy_ws.read_message()
                if msg is None:
                    break
                if self.ws_connection and not self.ws_connection.is_closing():
                    await self.write_message(msg, isinstance(msg, bytes))
        except Exception as e:
            log.error(f"WebSocket pipe error: {e}")
        finally:
            self.close()
    
    async def on_message(self, message):
        if self.comfy_ws and self._running:
            try:
                await self.comfy_ws.write_message(message, isinstance(message, bytes))
            except Exception as e:
                log.error(f"WebSocket write error: {e}")
                self.close()
    
    def on_close(self):
        self._running = False
        if self.comfy_ws:
            try:
                self.comfy_ws.close()
            except:
                pass
        log.info(f"WebSocket closed for user {self.username}")

# === ROOT HANDLER ===
class RootHandler(BaseHandler):
    def get(self):
        if not is_authenticated(self):
            self.redirect("/login")
            return
        
        # Redirecționează către ComfyUI
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
            "users": user_status
        })

# === APLICAȚII ===

class SimpleComfyProxyHandler(BaseHandler):
    async def get(self, path=None):
        """Simple proxy that doesn't modify HTML"""
        if not is_authenticated(self):
            self.redirect("/login")
            return
        
        session_id = self.get_secure_cookie("session_id").decode()
        session_data = get_session(session_id)
        username = session_data["user"]
        comfy_url = session_data["comfy_url"]
        
        # If accessing root, redirect to our wrapper
        if not path or path == "":
            self.redirect("/comfy-ui")
            return
        
        # Proxy all other requests
        target_url = f"{comfy_url}/{path}" if path else comfy_url
        if self.request.query:
            target_url += "?" + self.request.query
        
        client = tornado.httpclient.AsyncHTTPClient()
        
        try:
            headers = {}
            for h, v in self.request.headers.items():
                if h.lower() not in ['host', 'content-length']:
                    headers[h] = v
            
            response = await client.fetch(
                tornado.httpclient.HTTPRequest(
                    url=target_url,
                    method="GET",
                    headers=headers,
                    follow_redirects=False,
                    validate_cert=False
                ),
                raise_error=False
            )
            
            self.set_status(response.code)
            for h, v in response.headers.get_all():
                if h.lower() not in ['content-length', 'content-encoding']:
                    self.set_header(h, v)
            
            if response.code != 304:
                self.write(response.body)
                
        except Exception as e:
            self.set_status(502)
            self.write(f"Bad Gateway: {str(e)}")
# === CSS FIX HANDLER ===
class CSSFixHandler(BaseHandler):
    def get(self, path=None):
        """Servește CSS gol pentru resurse lipsă"""
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
        
        # Main UI with iframe solution
        (r"/comfy-ui", ComfyUIIframeHandler),  # Our wrapper
        
        # Simple proxy for ComfyUI (no HTML modification)
        (r"/comfy/(.*)", SimpleComfyProxyHandler),
        
        # WebSocket
        (r"/ws", MultiInstanceWebSocketProxy),
        
        # Root redirects to wrapper
        (r"/", RootHandler),
        
        # Catch-all - try simple proxy
        (r"/(.*)", SimpleComfyProxyHandler),
    ], 
    compress_response=False,
    autoreload=False,
    serve_traceback=False,
    cookie_secret="comfyui_multi_user_secret_key_2024_change_this",
    login_url="/login"
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
        (r"/admin/api/workflow-settings", AdminWorkflowSettingsHandler),
        (r"/admin/api/chat/users", AdminChatUsersHandler),
        (r"/admin/api/chat/messages/(.*)", AdminChatMessagesHandler),
        (r"/admin/api/chat/send", AdminChatSendHandler),
        (r"/admin/api/chat/upload-file", AdminChatUploadFileHandler),
        (r"/admin/api/chat/mark-read/(.*)", AdminChatMarkReadHandler),
        (r"/admin/chat-ws", AdminChatWebSocketHandler),
        (r"/admin/.*", AdminHandler),
    ],
    compress_response=False,
    autoreload=False,
    serve_traceback=False,
    cookie_secret="comfyui_admin_secret_key_2024_change_this_too",
    login_url="/admin/login"
    )

if __name__ == "__main__":
    local_ip = get_local_ip()
    
    print("=== ComfyUI Multi-User Auth Server with Admin ===")
    print(f"Auth Server: http://0.0.0.0:{AUTH_PORT} (local)")
    print(f"Auth Server: http://{local_ip}:{AUTH_PORT} (network)")
    print(f"Admin Interface: http://0.0.0.0:{ADMIN_PORT}/admin/ (local)")
    print(f"Admin Interface: http://{local_ip}:{ADMIN_PORT}/admin/ (network)")
    print("Default Users: user1, user2, user3, user4")
    print("Password for all: comfy.123")
    print("Admin Password: admin123")
    print(f"Config file: {CONFIG_FILE}")
    print(f"Workflow Root Directory: {WORKFLOW_ROOT_DIR}")
    print("=================================================")

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
        
        log.info(f"Auth server started on port {AUTH_PORT}")
        log.info(f"Admin server started on port {ADMIN_PORT}")
        print(f"Auth server started on port {AUTH_PORT}")
        print(f"Admin interface started on port {ADMIN_PORT}")
        print("Multi-user system active")
        print(f"Configuration will be saved to: {CONFIG_FILE}")
        print(f"Workflow directories will be created in: {WORKFLOW_ROOT_DIR}")
        
        loop = tornado.ioloop.IOLoop.current()
        loop.start()
        
    except Exception as e:
        log.error(f"Failed to start server: {e}")
        print(f"Failed to start server: {e}")
