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

// Workflow Browser Functions - Improved with file handling
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
                // Procesează workflow-ul pentru a rescrie path-urile de fișiere
                const workflow = data.workflow;
                
                // Funcție recursivă pentru a găsi și înlocui path-uri de fișiere
                function rewriteFilePaths(obj) {
                    if (!obj || typeof obj !== 'object') return obj;
                    
                    if (Array.isArray(obj)) {
                        return obj.map(item => rewriteFilePaths(item));
                    }
                    
                    const newObj = {};
                    for (const [key, value] of Object.entries(obj)) {
                        if (typeof value === 'string') {
                            // Verifică dacă e un path de fișier
                            if (value.includes('/mnt/') || value.includes('/home/') || 
                                value.match(/\.(png|jpg|jpeg|gif|svg|webp|mp4|json|txt)$/i)) {
                                // Extrage numele fișierului
                                const filename = value.split('/').pop();
                                // Rescrie path-ul să pointeze către serverul nostru
                                newObj[key] = `/workflow-files/${filename}`;
                            } else {
                                newObj[key] = value;
                            }
                        } else if (typeof value === 'object' && value !== null) {
                            newObj[key] = rewriteFilePaths(value);
                        } else {
                            newObj[key] = value;
                        }
                    }
                    return newObj;
                }
                
                const processedWorkflow = rewriteFilePaths(workflow);
                
                // Injectează workflow-ul în ComfyUI
                if (window.app && window.app.graph) {
                    window.app.loadGraphData(processedWorkflow);
                    closeWorkflowBrowser();
                    
                    // Afișează un mesaj de succes
                    showNotification('✓ Workflow loaded successfully!', 'success');
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

// Funcție pentru a afișa notificări
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

// Adaugă stilurile pentru animații
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
            showNotification('✓ Workflow deleted successfully!', 'success');
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
                showNotification('✓ Workflow saved successfully!', 'success');
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

// Funcție pentru a încărca imagini din workflow
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