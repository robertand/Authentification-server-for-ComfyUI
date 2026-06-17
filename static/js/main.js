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
    if (document.getElementById('aboutDrawer')) {
        openAboutDrawer();
    } else if (document.getElementById('aboutModal')) {
        document.getElementById('aboutModal').style.display = 'block';
    }
}

function closeAboutModal() {
    if (document.getElementById('aboutDrawer')) {
        closeAboutDrawer();
    } else if (document.getElementById('aboutModal')) {
        document.getElementById('aboutModal').style.display = 'none';
    }
}

function openAboutDrawer() {
    const drawer = document.getElementById('aboutDrawer');
    if (drawer) drawer.classList.add('open');

    const container = document.querySelector('.container');
    if (container) container.classList.add('login-container-shifted');
}

function closeAboutDrawer() {
    const drawer = document.getElementById('aboutDrawer');
    if (drawer) drawer.classList.remove('open');

    const container = document.querySelector('.container');
    if (container) container.classList.remove('login-container-shifted');
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
    window.location.href = '/logout';
}

function logoutNow() {
    // Close session expiry modal
    closeSessionExpiryModal();
    
    // Logout directly
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
    // Pre-populate current username
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

    // Send request to server
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
            // Update username display
            const usernameElement = document.querySelector('.comfy-user-info');
            if (usernameElement) {
                usernameElement.textContent = `Welcome, ${username}`;
            }
            // Reset form after 2 seconds
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
    
    // Load users for chat selection
    loadChatUsersList();

    // Mark messages as read when opening chat
    markMessagesAsRead();
}

function loadChatUsersList() {
    fetch('/chat-users')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const select = document.getElementById('chatRecipient');
                const currentRecipient = select.value;

                select.innerHTML = '';

                data.users.forEach(user => {
                    const option = document.createElement('option');
                    option.value = user.is_session ? user.session_id : user.username;
                    option.textContent = user.display_name + (user.online ? ' (Online)' : '');
                    select.appendChild(option);
                });

                select.value = currentRecipient;
            }
        })
        .catch(err => console.log('Error loading chat users:', err));
}

function switchChatRecipient() {
    // Reload messages for the selected conversation or just filter them
    loadChatMessages();
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
            const from_display = data.from_display || data.from;
            addMessageToChat(data.message, from_display, data.timestamp, data.message_type, data.file_data);
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
        } else if (data.type === 'system_notification') {
            showNotification(data.message, 'info');
        } else if (data.type === 'concurrent_user') {
            openConcurrentUserModal(data.username, data.session_index, data.alias);
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
    const recipient = document.getElementById('chatRecipient').value;
    const usernameElement = document.querySelector('.comfy-user-info');
    const myUsername = usernameElement ? usernameElement.textContent.replace(/Welcome, | #\d+/g, '') : '';

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
                // Filter messages: only show if I'm sender/receiver and the other person is the selected recipient
                let show = false;
                if (recipient === 'admin') {
                    if (msg.from === 'admin' || msg.to === 'admin') show = true;
                } else {
                    // Match by session_id (to_id/from_id) or username if it was an old style message
                    if ((msg.from_id && (msg.from_id === recipient || msg.to === recipient)) ||
                        (!msg.from_id && (msg.from === recipient || msg.to === recipient))) {
                        show = true;
                    }
                }

                if (show) {
                    const from_display = msg.from_display || msg.from;
                    addMessageToChat(msg.message, from_display, msg.timestamp, msg.message_type, msg.file_data, false);
                }
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
    const usernameElement = document.querySelector('.comfy-user-info');
    const myUsername = usernameElement ? usernameElement.textContent.replace(/Welcome, | #\d+/g, '').trim() : '';
    const cleanFrom = from.replace(/ #\d+/g, '').replace(/\s*\([^)]*\)/g, '').trim();

    const messageDiv = document.createElement('div');
    messageDiv.className = `chat-message ${cleanFrom === myUsername ? 'user' : 'admin'}`;
    
    const time = new Date(timestamp * 1000).toLocaleTimeString();
    
    const textWrapper = document.createElement('div');
    textWrapper.textContent = message;
    messageDiv.appendChild(textWrapper);

    if (message_type === 'file' && file_data) {
        const fileDiv = document.createElement('div');
        fileDiv.className = 'chat-file-message';

        const icon = document.createElement('span');
        icon.className = 'chat-file-icon';
        icon.textContent = '📎';

        const fileName = document.createElement('span');
        fileName.className = 'chat-file-name';
        fileName.textContent = file_data.filename;

        const downloadLink = document.createElement('a');
        downloadLink.href = `/download-file/${file_data.id}`;
        downloadLink.className = 'chat-file-download';
        downloadLink.textContent = 'Download';
        downloadLink.setAttribute('download', file_data.filename);

        fileDiv.appendChild(icon);
        fileDiv.appendChild(fileName);
        fileDiv.appendChild(downloadLink);
        messageDiv.appendChild(fileDiv);
    }
    
    const timeDiv = document.createElement('div');
    timeDiv.className = 'chat-message-time';
    timeDiv.textContent = `${cleanFrom === myUsername ? 'You' : from} • ${time}`;
    messageDiv.appendChild(timeDiv);
    
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
    const recipient = document.getElementById('chatRecipient').value;
    const usernameElement = document.querySelector('.comfy-user-info');
    const myDisplay = usernameElement ? usernameElement.textContent.replace('Welcome, ', '') : 'user';

    if (chatWebSocket && chatWebSocket.readyState === WebSocket.OPEN) {
        chatWebSocket.send(JSON.stringify({
            type: 'send_message',
            to_user: recipient,
            message: message,
            message_type: 'text'
        }));
        // Add message immediately to chat for better UX
        addMessageToChat(message, myDisplay, Date.now() / 1000);
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
                to_user: recipient,
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
    const recipient = document.getElementById('chatRecipient').value;
    const formData = new FormData();
    formData.append('message', message);
    formData.append('to_user', recipient);
    
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


// User Lock Functions
function toggleUserLock() {
    fetch('/single-user-lock', {
        method: 'POST',
        credentials: 'include'
    })
    .then(response => response.json())
    .then(data => {
        if (data.success === false) {
            showNotification(data.error || 'Cannot toggle lock.', 'error');
            return;
        }
        updateLockButton(data.locked, data.session_index, data.alias, data.handover_lock_available);
        if (data.locked) {
            showNotification('Account locked. Other users cannot login as you.', 'info');
        } else {
            showNotification('Account unlocked.', 'info');
        }
    })
    .catch(error => {
        console.log('Lock toggle error:', error);
    });
}

function checkUserLockStatus() {
    fetch('/single-user-lock', {
        method: 'GET',
        credentials: 'include'
    })
    .then(response => response.json())
    .then(data => {
        updateLockButton(data.locked, data.session_index, data.alias, data.handover_lock_available);
        updateSessionIndexDisplay(data.session_index, data.alias);
    })
    .catch(error => {
        console.log('Lock status error:', error);
    });
}

function updateLockButton(locked, sessionIndex, alias, handoverLockAvailable) {
    const btn = document.getElementById('comfyLockBtn');
    if (!btn) return;
    if (alias && handoverLockAvailable) {
        btn.style.display = '';
    } else {
        btn.style.display = 'none';
        return;
    }
    if (locked) {
        btn.classList.add('locked');
        btn.textContent = '\u{1F512}';
        btn.title = 'Unlock this user - allow other logins';
    } else {
        btn.classList.remove('locked');
        btn.textContent = '\u{1F513}';
        btn.title = 'Lock this user - prevent other logins';
    }
}

function updateSessionIndexDisplay(sessionIndex, alias) {
    const userInfo = document.querySelector('.comfy-user-info');
    if (!userInfo) return;
    const span = userInfo.querySelector('span[style*="color: red"]');
    if (!span) return;
    if (alias) {
        span.textContent = alias;
    } else if (sessionIndex) {
        span.textContent = '#' + sessionIndex;
    }
}

// Concurrent User Modal Functions
function openConcurrentUserModal(username, sessionIndex, alias) {
    const modal = document.getElementById('concurrentUserModal');
    const message = document.getElementById('concurrentUserMessage');
    const info = document.getElementById('concurrentUserInfo');
    if (username) {
        message.textContent = `User ${username} has connected to the server.`;
        if (alias) {
            info.textContent = `${username} as ${alias}`;
        } else if (sessionIndex) {
            info.textContent = `${username} #${sessionIndex}`;
        } else {
            info.textContent = username;
        }
    } else {
        message.textContent = 'Another user has connected to the server.';
        info.textContent = '';
    }
    modal.style.display = 'block';
    setTimeout(() => { modal.style.display = 'none'; }, 10000);
}

function closeConcurrentUserModal() {
    document.getElementById('concurrentUserModal').style.display = 'none';
}

// Function to show notifications
function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.style.cssText = `
        position: fixed;
        top: 50px;
        right: 20px;
        background: ${type === 'success' ? '#28a745' : type === 'error' ? '#dc3545' : '#007bff'};
        color: white;
        padding: 12px 24px;
        border-radius: 5px;
        z-index: 10002;
        box-shadow: 0 2px 10px rgba(0,0,0,0.3);
        animation: slideIn 0.3s ease;
    `;
    notification.textContent = message;
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}

// Add styles for animations
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from { transform: translateX(100%); opacity: 0; }
        to { transform: translateX(0); opacity: 1; }
    }
    @keyframes slideOut {
        from { transform: translateX(0); opacity: 1; }
        to { transform: translateX(100%); opacity: 0; }
    }
`;
document.head.appendChild(style);


// Function to load workflow images
function loadWorkflowImage(filename) {
    return fetch(`/workflow-files/${encodeURIComponent(filename)}`)
        .then(response => {
            if (!response.ok) throw new Error('Image not found');
            return response.blob();
        })
        .then(blob => URL.createObjectURL(blob))
        .catch(error => {
            console.error('Error loading workflow image:', error);
            return null;
        });
}

// Helper to get cookie value
function getCookie(name) {
    var r = document.cookie.match("\\b" + name + "=([^;]*)\\b");
    return r ? r[1] : undefined;
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
        if (chatButton) {
            if (data.status === 'authenticated') {
                chatButton.style.display = 'flex';
                // Connect to chat WebSocket when authenticated
                connectChatWebSocket();
                // Check for unread messages
                checkUnreadMessages();
            } else {
                chatButton.style.display = 'none';
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

    // Handle aboutDrawer specifically for "click outside"
    if (aboutDrawer && aboutDrawer.classList.contains('open')) {
        const isClickInside = aboutDrawer.contains(event.target);
        const isAboutBtn = event.target.closest('.comfy-about-btn') || event.target.closest('.about-btn-login');

        if (!isClickInside && !isAboutBtn) {
            closeAboutDrawer();
        }
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
            if (chatButton) {
                chatButton.style.display = 'flex';
                if (data.has_concurrent_sessions) {
                    chatButton.classList.add('concurrent-session');
                } else {
                    chatButton.classList.remove('concurrent-session');
                }
            }
            // Update lock button visibility and alias/index display
            if (data.session_index) {
                updateSessionIndexDisplay(data.session_index, data.alias);
                const lockBtn = document.getElementById('comfyLockBtn');
                if (lockBtn) {
                    if (data.alias && data.handover_lock_available) {
                        lockBtn.style.display = '';
                    } else {
                        lockBtn.style.display = 'none';
                    }
                }
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
    checkUserLockStatus();
}

// Make chat button draggable
function makeChatButtonDraggable() {
    const btn = document.getElementById('chatButton');
    if (!btn) return;
    let isDragging = false;
    let dragOffsetX, dragOffsetY;
    btn.addEventListener('mousedown', function(e) {
        if (e.target !== btn && e.target.id !== 'chatButton') return;
        isDragging = false;
        const rect = btn.getBoundingClientRect();
        dragOffsetX = e.clientX - rect.left;
        dragOffsetY = e.clientY - rect.top;
        function onMouseMove(e) {
            isDragging = true;
            btn.style.left = (e.clientX - dragOffsetX) + 'px';
            btn.style.top = (e.clientY - dragOffsetY) + 'px';
            btn.style.right = 'auto';
            btn.style.bottom = 'auto';
        }
        function onMouseUp(e) {
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
            if (isDragging) {
                e.stopPropagation();
                e.preventDefault();
            }
        }
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
    });
    btn.addEventListener('click', function(e) {
        if (isDragging) {
            e.stopPropagation();
            e.preventDefault();
            isDragging = false;
        }
    });
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
    
    // Make chat button draggable
    setTimeout(makeChatButtonDraggable, 2000);
    
    // Check lock status on page load
    setTimeout(checkUserLockStatus, 1000);
});

// Also start when window loads (fallback)
window.onload = function() {
    initSessionMonitoring();
    checkChatButtonVisibility();
};

setTimeout(dumpPiniaStores, 3000);

// Function to autocomplete username when clicking on user status
function autocompleteUsername(username) {
    document.querySelector('input[name="username"]').value = username;
}

// === WORKFLOW BROWSER (MODERN) ===
var wfCurrentFolder = "";

window.openWorkflowBrowser = function() {
    var modal = document.getElementById('workflowBrowserModal');
    if (!modal) return;
    modal.style.display = 'flex';
    wfCurrentFolder = "";
    loadWorkflowList();
};
function openWorkflowBrowser() { window.openWorkflowBrowser(); }

function closeWorkflowBrowser() {
    document.getElementById('workflowBrowserModal').style.display = 'none';
    document.getElementById('workflowMessage').style.display = 'none';
}

function showWorkflowMessage(text, type) {
    var msg = document.getElementById('workflowMessage');
    msg.textContent = text;
    msg.className = 'wf-message ' + type;
    msg.style.display = 'block';
    setTimeout(function() { msg.style.display = 'none'; }, 3000);
}

function escHtml(s) {
    return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function escJsStr(s) {
    return (s || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"');
}

function loadWorkflowList() {
    fetch('/api/workflows/list', { credentials: 'include' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.success) return;
            var tree = data.tree || [];
            renderTree(tree, document.getElementById('wfTree'));
            renderFileList(tree, wfCurrentFolder);
            renderBreadcrumb(wfCurrentFolder);
            document.getElementById('wfCurrentPath').textContent = '/' + wfCurrentFolder;
        })
        .catch(function(e) { console.log('Error loading workflows:', e); });
}

function renderTree(entries, parentEl, prefix) {
    prefix = prefix || "";
    parentEl.innerHTML = "";
    
    // Root entry
    var rootItem = document.createElement('div');
    rootItem.className = 'wf-tree-item' + (wfCurrentFolder === "" ? ' active' : '');
    rootItem.innerHTML = '<span class="wf-tree-icon">📁</span><span class="wf-tree-label">Root</span>';
    rootItem.onclick = function() { wfNavigate(""); };
    parentEl.appendChild(rootItem);
    
    function addEntries(list, container, pathPrefix) {
        for (var i = 0; i < list.length; i++) {
            var entry = list[i];
            if (entry.type === 'directory') {
                var item = document.createElement('div');
                item.className = 'wf-tree-item' + (wfCurrentFolder === entry.path ? ' active' : '');
                item.innerHTML = '<span class="wf-tree-icon">📁</span><span class="wf-tree-label">' + escHtml(entry.name) + '</span>';
                item.onclick = (function(p) { return function() { wfNavigate(p); }; })(entry.path);
                container.appendChild(item);
                
                if (entry.children && entry.children.length > 0) {
                    var childContainer = document.createElement('div');
                    childContainer.className = 'wf-tree-children';
                    container.appendChild(childContainer);
                    addEntries(entry.children, childContainer, entry.path);
                }
            }
        }
    }
    addEntries(entries, parentEl, "");
}

function renderBreadcrumb(path) {
    var el = document.getElementById('wfBreadcrumb');
    if (!el) return;
    var parts = path ? path.split('/') : [];
    var html = '<span onclick="wfNavigate(\'\')">Root</span>';
    var cumulative = "";
    for (var i = 0; i < parts.length; i++) {
        html += '<span class="sep">/</span>';
        cumulative += (i > 0 ? '/' : '') + parts[i];
        html += '<span onclick="wfNavigate(\'' + escJsStr(cumulative) + '\')">' + escHtml(parts[i]) + '</span>';
    }
    el.innerHTML = html;
}

function renderFileList(tree, path) {
    var list = document.getElementById('workflowList');
    if (!list) return;
    
    // Navigate tree to find entries for current path
    var entries = findInTree(tree, path);
    if (!entries) {
        list.innerHTML = '<div class="wf-empty">Folder not found</div>';
        return;
    }
    // Filter out directories - show them as navigation items
    var files = entries.filter(function(e) { return e.type === 'file'; });
    var dirs = entries.filter(function(e) { return e.type === 'directory'; });
    
    if (dirs.length === 0 && files.length === 0) {
        list.innerHTML = '<div class="wf-empty">Empty folder</div>';
        return;
    }
    
    list.innerHTML = "";
    
    // Show sub-folders first
    for (var i = 0; i < dirs.length; i++) {
        var d = dirs[i];
        var item = document.createElement('div');
        item.className = 'wf-file-item';
        item.innerHTML =
            '<span class="wf-file-icon">📁</span>' +
            '<span class="wf-file-name">' + escHtml(d.name) + '</span>' +
            '<span class="wf-file-date">folder</span>' +
            '<div class="wf-file-actions">' +
                '<button class="wf-file-btn load" onclick="wfNavigate(\'' + escJsStr(d.path) + '\')">Open</button>' +
            '</div>';
        list.appendChild(item);
    }
    
    // Show files
    for (var i = 0; i < files.length; i++) {
        var f = files[i];
        var d = new Date(f.modified * 1000);
        var dateStr = d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
        var relPath = f.path;
        var displayName = f.name;
        
        // If workflow name has .json extension, we can use full path
        var loadPath = escJsStr(relPath);
        
        var item = document.createElement('div');
        item.className = 'wf-file-item';
        item.innerHTML =
            '<span class="wf-file-icon">📄</span>' +
            '<span class="wf-file-name">' + escHtml(displayName) + '</span>' +
            '<span class="wf-file-date">' + dateStr + '</span>' +
            '<div class="wf-file-actions">' +
                '<button class="wf-file-btn load" onclick="injectWorkflow(\'' + loadPath + '\')">Load</button>' +
                '<button class="wf-file-btn del" onclick="deleteWorkflow(\'' + loadPath + '\')">&#10005;</button>' +
            '</div>';
        list.appendChild(item);
    }
}

function findInTree(tree, path) {
    if (!path) return tree;
    var parts = path.split('/');
    var current = tree;
    for (var i = 0; i < parts.length; i++) {
        if (!current || !Array.isArray(current)) return null;
        var found = null;
        for (var j = 0; j < current.length; j++) {
            if (current[j].type === 'directory' && current[j].name === parts[i]) {
                found = current[j].children;
                break;
            }
        }
        if (!found) return null;
        current = found;
    }
    return current;
}

function wfNavigate(path) {
    wfCurrentFolder = path || "";
    loadWorkflowList();
}

function wfNewFolder() {
    var name = prompt('Enter folder name:');
    if (!name || !name.trim()) return;
    name = name.trim();
    var fullPath = wfCurrentFolder ? wfCurrentFolder + '/' + name : name;
    fetch('/api/workflows/mkdir', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: fullPath }),
        credentials: 'include'
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.success) {
            showWorkflowMessage('Folder created', 'success');
            loadWorkflowList();
        } else {
            showWorkflowMessage(data.error || 'Failed to create folder', 'error');
        }
    })
    .catch(function(e) {
        showWorkflowMessage('Error: ' + e.message, 'error');
    });
}

function saveCurrentWorkflow() {
    var name = document.getElementById('workflowSaveName').value.trim();
    if (!name) { showWorkflowMessage('Enter a workflow name', 'error'); return; }
    
    // Prepend current folder path if we're not in root
    var fullName = wfCurrentFolder ? wfCurrentFolder + '/' + name : name;
    if (!fullName.endsWith('.json')) fullName += '.json';

    try {
        if (typeof window.app !== 'undefined' && window.app.graphToPromise) {
            window.app.graphToPromise().then(function(apiJson) {
                var promptData = apiJson.output || apiJson;
                var workflowData = promptData.workflow || promptData;
                if (!workflowData || Object.keys(workflowData).length === 0) {
                    showWorkflowMessage('Could not extract workflow data', 'error');
                    return;
                }
                injectWorkflowName(workflowData, fullName);
                sendSaveWorkflow(fullName, workflowData);
            }).catch(function(e) {
                sendSaveFallback(fullName);
            });
        } else if (typeof window.app !== 'undefined' && window.app.graphToPrompt) {
            var result = window.app.graphToPrompt();
            if (result && result.workflow) {
                var wf = result.workflow;
                injectWorkflowName(wf, fullName);
                sendSaveWorkflow(fullName, wf);
            } else if (result && result.output) {
                var out = result.output;
                injectWorkflowName(out, fullName);
                sendSaveWorkflow(fullName, out);
            } else {
                sendSaveFallback(fullName);
            }
        } else {
            sendSaveFallback(fullName);
        }
    } catch(e) {
        console.log('Error extracting workflow:', e);
        sendSaveFallback(fullName);
    }
}

function injectWorkflowName(wf, name) {
    var cleanName = name.replace(/\.json$/i, '').split('/').pop();
    if (wf.extra) {
        wf.extra.workflow = wf.extra.workflow || {};
        wf.extra.workflow.name = cleanName;
    } else {
        wf.extra = { workflow: { name: cleanName } };
    }
}

function sendSaveWorkflow(name, workflowData) {
    fetch('/api/workflows/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: name, workflow: workflowData }),
        credentials: 'include'
    })
    .then(function(r) {
        if (!r.ok) console.log('Save response status:', r.status);
        return r.text().then(function(text) {
            try { return JSON.parse(text); }
            catch(e) { throw new Error('Server returned HTML (status ' + r.status + '): ' + text.substring(0, 100)); }
        });
    })
    .then(function(data) {
        if (data.success) {
            showWorkflowMessage('Workflow saved successfully', 'success');
            document.getElementById('workflowSaveName').value = '';
            loadWorkflowList();
        } else {
            showWorkflowMessage(data.error || 'Save failed', 'error');
        }
    })
    .catch(function(e) {
        showWorkflowMessage('Error: ' + e.message, 'error');
    });
}

function sendSaveFallback(name) {
    var fallbackData = { nodes: [], links: [] };
    try {
        if (window.app && window.app.graph) {
            var ser = window.app.graph.serialize();
            if (ser && ser.nodes) fallbackData = ser;
        }
    } catch(e) {}
    sendSaveWorkflow(name, fallbackData);
}

function getExistingTabNames() {
    var names = [];
    try {
        document.querySelectorAll('[class*="tab"]:not([class*="workflow-"]) .tab-label, [role="tab"] span, .p-tabview-title, .tab-name').forEach(function(el) {
            var t = (el.textContent || '').trim();
            if (t && t !== '+' && t !== '×') names.push(t);
        });
    } catch(e) {}
    return names;
}

function getUniqueTabName(baseName) {
    var cleanName = baseName.replace(/\.json$/i, '');
    var existing = getExistingTabNames();
    if (existing.indexOf(cleanName) === -1) return cleanName;
    var counter = 1;
    while (existing.indexOf(cleanName + ' (' + counter + ')') !== -1) counter++;
    return cleanName + ' (' + counter + ')';
}

function getVueApp() {
    var el = document.getElementById('vue-app') || document.querySelector('#vue-app');
    if (el && el.__vue_app__) return el.__vue_app__;
    var found = document.querySelector('[__vue_app__]');
    if (found) return found.__vue_app__;
    var all = document.querySelectorAll('body *');
    for (var ai = 0; ai < all.length; ai++) {
        if (all[ai].__vue_app__) return all[ai].__vue_app__;
    }
    return null;
}

function getPinia() {
    var app = getVueApp();
    return app && app.config && app.config.globalProperties && app.config.globalProperties.$pinia;
}

function getWorkflowStore() {
    var pinia = getPinia();
    if (!pinia) return null;
    for (var key in pinia._s) {
        var store = pinia._s[key];
        if (store.activeWorkflow) return store;
    }
    return null;
}

function renameActiveWorkflow(newName) {
    try {
        if (window.app && window.app.graph) {
            window.app.graph.extra = window.app.graph.extra || {};
            window.app.graph.extra.workflow = window.app.graph.extra.workflow || {};
            window.app.graph.extra.workflow.name = newName;
        }
        var store = getWorkflowStore();
        if (!store) { console.log('workflowStore not found'); return false; }
        var wf = store.activeWorkflow;
        if (!wf) { console.log('activeWorkflow not found'); return false; }
        if (wf.rename && typeof wf.rename === 'function') {
            wf.rename(newName);
            console.log('workflow.rename() called with:', newName);
        }
        wf.name = newName;
        wf.filename = newName;
        return true;
    } catch(e) { console.log('renameActiveWorkflow error:', e); return false; }
}

function dumpPiniaStores() {
    try {
        var pinia = getPinia();
        if (!pinia) { console.log('Pinia not found'); return; }
        for (var key in pinia._s) {
            var store = pinia._s[key];
            var props = [];
            for (var p in store) {
                if (p.startsWith('$') || typeof store[p] === 'function') continue;
                var val = store[p];
                if (typeof val === 'string') props.push(p + '="' + val + '"');
                else if (typeof val === 'number' || typeof val === 'boolean') props.push(p + '=' + val);
                else if (Array.isArray(val)) props.push(p + '[]=' + val.length);
                else if (val && typeof val === 'object') props.push(p + '{}');
                else props.push(p + ': ' + typeof val);
            }
            if (store.activeWorkflow && store.activeWorkflow.filename) {
                props.push('activeWorkflow.filename="' + store.activeWorkflow.filename + '"');
                props.push('activeWorkflow.name="' + (store.activeWorkflow.name || '') + '"');
            }
            console.log('Pinia store [' + key + ']:', props.join(', '));
        }
    } catch(e) { console.log('dump error:', e); }
}

function forceSetTabName(name) {
    if (!name) return;
    var cleanName = name.replace(/\.json$/i, '');
    document.title = cleanName + ' | ComfyUI';
    try {
        // Set in graph data
        if (window.app && window.app.graph) {
            window.app.graph.extra = window.app.graph.extra || {};
            window.app.graph.extra.workflow = window.app.graph.extra.workflow || {};
            window.app.graph.extra.workflow.name = cleanName;
        }
        
        // Try to rename via Pinia store
        renameActiveWorkflow(cleanName);
        
        // DOM manipulation as fallback
        document.querySelectorAll('[class*="tab"] .tab-label, [role="tab"] span, .p-tabview-title').forEach(function(el) {
            var txt = el.textContent.trim();
            if (txt === 'Unsaved Workflow' || txt === 'Workflow' || txt.indexOf('Unsaved') === 0) {
                el.textContent = cleanName;
            }
        });
    } catch(e) { console.log('setTabName error:', e); }
}

function injectWorkflow(filename) {
    fetch('/api/workflows/load/' + encodeURIComponent(filename), { credentials: 'include' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.success) { showWorkflowMessage(data.error || 'Load failed', 'error'); return; }
            var wf = data.workflow;
            var baseName = filename.split('/').pop().replace(/\.json$/i, '');
            if (typeof window.app === 'undefined') { showWorkflowMessage('ComfyUI not available', 'error'); return; }

            if (wf.extra) { wf.extra.workflow = wf.extra.workflow || {}; wf.extra.workflow.name = baseName; }
            else { wf.extra = { workflow: { name: baseName } }; }

            closeWorkflowBrowser();
            console.log('Loading workflow:', baseName);

            try {
                if (window.app.loadGraphData) {
                    window.app.loadGraphData(wf, true, true, baseName);
                } else if (window.app.loadApiJson) {
                    window.app.loadApiJson(wf, baseName);
                }
                setTimeout(function() { forceSetTabName(baseName); }, 1000);
            } catch(e) { console.log('Inject error:', e); showNotification('Error loading workflow', 'error'); }
        })
        .catch(function(e) { showWorkflowMessage('Error: ' + e.message, 'error'); });
}

function deleteWorkflow(filename) {
    if (!confirm('Delete ' + filename + '?')) return;
    fetch('/api/workflows/delete/' + encodeURIComponent(filename), {
        method: 'DELETE',
        credentials: 'include'
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.success) {
            loadWorkflowList();
        } else {
            showWorkflowMessage(data.error || 'Delete failed', 'error');
        }
    })
    .catch(function(e) {
        showWorkflowMessage('Network error: ' + e, 'error');
    });
}