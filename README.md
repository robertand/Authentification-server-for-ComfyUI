ComfyUI Auth Server

Un server de autentificare multi-utilizator pentru ComfyUI, cu proxy complet, interfață admin și sistem de chat cu suport pentru fișiere.
🌟 Caracteristici
🔐 Autentificare și Securitate

    Multi-utilizator - Gestionare simultană a mai multor utilizatori

    Rate Limiting - Protecție împotriva atacurilor brute-force

    Hashing bcrypt - Parole stocate în siguranță

    Session Management - Timeout configurabil per utilizator

    IP Monitoring - Blocare temporară pentru încercări eșuate

🖥️ Proxy Complet

    Rescriere URL - Toate URL-urile sunt rescrise automat pentru a trece prin proxy

    WebSocket Proxy - Suport complet pentru WebSocket-urile ComfyUI

    Streaming - Suport pentru fișiere mari (până la 1GB)

    Nginx Auth - Suport pentru autentificare Basic Nginx (globală sau per utilizator)

💬 Sistem de Chat

    Comunicare în timp real - Chat între utilizatori și administratori

    Suport fișiere - Trimitere și primire de fișiere (imagini, documente, etc.)

    Notificări - Notificări pentru mesaje noi

    Typing indicators - Indicator când cineva sc

in engleza
ComfyUI Auth Server

A multi-user authentication server for ComfyUI with full proxy support, admin interface, and a file-sharing chat system.
🌟 Features
🔐 Authentication & Security

    Multi-user – Simultaneous management of multiple users

    Rate Limiting – Protection against brute-force attacks

    bcrypt Hashing – Securely stored passwords

    Session Management – Configurable timeouts per user

    IP Monitoring – Temporary blocking after failed login attempts

🖥️ Full Proxy Support

    URL Rewriting – All URLs are automatically rewritten to pass through the proxy

    WebSocket Proxy – Full support for ComfyUI WebSockets

    Streaming – Support for large files (up to 1GB)

    Nginx Auth – Support for Nginx Basic Authentication (global or per user)

💬 Chat System

    Real-time Communication – Chat between users and administrators

    File Sharing – Send and receive files (images, documents, etc.)

    Notifications – Alerts for new messages

    Typing Indicators – Shows when someone is typing

    Message History – Persistent message history

👨‍💼 Admin Interface

    Dashboard – Real‑time system status

    User Management – Add, edit, delete users

    Session Control – View and terminate active sessions

    Chat Management – Monitor and participate in user conversations

    Nginx Auth Configuration – Manage Nginx authentication settings

📋 Prerequisites

    Python 3.8 or higher

    One or more running ComfyUI instances

🚀 Installation

    Clone the repository
    bash

git clone https://github.com/yourusername/comfyui-auth-server.git
cd comfyui-auth-server

Install dependencies
bash

pip install tornado requests bcrypt

Create required directories
bash

mkdir -p chat_files static/css static/js templates

    Place the files

        auth_server.py in the root directory

        HTML templates in the templates/ folder

        main.js in static/js/

        styles.css in static/css/

⚙️ Configuration

The server uses a JSON configuration file (comfyui_auth_config.json) that is automatically created on first run.
Default users:

    user1 – Password: comfy.123 – URL: http://127.0.0.1:8188

    user2 – Password: comfy.123 – URL: http://10.129.131.12:8188

    Admin – Password: admin123

You can modify the configuration file directly or use the admin interface.
🏃 Running the Server
bash

python3 auth_server.py

The server will start two services:

    Auth Server – http://0.0.0.0:7861

    Admin Interface – http://0.0.0.0:8199/admin

🖱️ Usage
For Users

    Navigate to the auth server URL (e.g., http://your-server:7861)

    Log in with your username and password

    You will be automatically redirected to your assigned ComfyUI instance

    Use the chat button (bottom right) to communicate with administrators

    Attach files using the 📎 button

For Administrators

    Navigate to http://your-server:8199/admin

    Log in with the admin password (default: admin123)

    Use the dashboard to monitor system status

    Manage users, sessions, and Nginx authentication

    Use the chat tab to communicate with users and share files

📁 Project Structure
text

comfyui-auth-server/
├── auth_server.py              # Main server file
├── comfyui_auth_config.json    # Configuration file (auto-generated)
├── chat_files/                  # Uploaded chat files (auto-created)
├── static/
│   ├── css/
│   │   └── styles.css          # Styling
│   └── js/
│       └── main.js              # Client-side JavaScript
└── templates/
    ├── admin.html
    ├── admin_login.html
    ├── forced_logout.html
    ├── login.html
    ├── logout.html
    ├── session_expired.html
    ├── user_full.html
    └── waiting.html

🔧 Advanced Configuration
Nginx Authentication

You can enable Nginx Basic Authentication in two ways:

    Globally – Applies to all users

    Per user – Overrides global settings for specific users

Configure these settings in the admin interface under the Nginx Auth tab.
Session Timeouts

Each user can have a custom session timeout (in minutes).

    0 = infinite session

    Default: 60 minutes

Rate Limiting

    Max attempts: 5 failed logins

    Block time: 15 minutes

🛡️ Security Notes

    All passwords are hashed using bcrypt

    Rate limiting protects against brute‑force attacks

    Sessions are stored server‑side with secure cookies

    File uploads are automatically deleted after 24 hours

    Nginx Basic Authentication credentials are stored in plain text in the config file – use strong passwords

🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

    Fork the repository

    Create your feature branch (git checkout -b feature/AmazingFeature)

    Commit your changes (git commit -m 'Add some AmazingFeature')

    Push to the branch (git push origin feature/AmazingFeature)

    Open a Pull Request

📄 License

This project is licensed under the MIT License – see the LICENSE file for details.
📧 Contact

For questions or support, please open an issue on GitHub or contact the repository owner.

Made with ❤️ for the ComfyUI community
