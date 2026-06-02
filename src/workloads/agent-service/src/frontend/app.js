// ---------- Auth Utilities ----------
function logout() {
  localStorage.removeItem('app_jwt');
  fetch('/auth/logout');
  window.location.href = '/';
}

async function getUser() {
  const token = localStorage.getItem('app_jwt');
  if (!token) return null;
  try {
    const resp = await fetch('/auth/me', { headers: { 'Authorization': 'Bearer ' + token } });
    if (!resp.ok) throw new Error('invalid');
    const data = await resp.json();
    return data.user;
  } catch (e) {
    localStorage.removeItem('app_jwt');
    return null;
  }
}

function escapeHtml(str) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(str).replace(/[&<>"']/g, c => map[c]);
}

// ---------- Support Widget ----------
document.addEventListener('DOMContentLoaded', async () => {
  const toggle = document.getElementById('support-toggle');
  const panel = document.getElementById('support-panel');
  const panelContent = document.getElementById('panel-content');
  const closeBtn = document.getElementById('panel-close');

  if (!toggle) return; // not on homepage

  toggle.addEventListener('click', async () => {
    panel.classList.toggle('hidden');
    if (!panel.classList.contains('hidden')) {
      await renderPanel();
    }
  });

  closeBtn.addEventListener('click', () => panel.classList.add('hidden'));

  async function renderPanel() {
    const user = await getUser();
    if (user) {
      panelContent.innerHTML = `
        <div class="widget-header">
          <span class="widget-user">👤 ${escapeHtml(user.name || user.email)}</span>
          <div class="widget-actions">
            <a href="/chat.html" target="_blank" class="widget-btn" title="Open in full page">⛶</a>
            <button class="widget-btn" onclick="logout()" title="Sign out">🚪</button>
          </div>
        </div>
        <div class="chat-messages" id="widget-messages">
          <div class="bubble agent">
            <div class="bubble-avatar">🤖</div>
            <div class="bubble-text">Hi ${escapeHtml(user.name || user.email)}, how can I help?</div>
          </div>
        </div>
        <div class="chat-input">
          <input id="widget-input" type="text" placeholder="Type a message...">
          <button id="widget-send">Send</button>
        </div>
      `;
      initWidgetChat();
    } else {
      panelContent.innerHTML = `
        <p style="margin-bottom:16px;">Sign in to get personalised support.</p>
        <div class="auth-buttons">
          <a href="/auth/login/start/google" class="btn-google">
            <svg width="18" height="18" viewBox="0 0 24 24"><path fill="#EA4335" d="M12 10.2v3.6h5.2c-.2 1.2-1.4 3.6-5.2 3.6-3.1 0-5.6-2.6-5.6-5.8S8.9 6.8 12 6.8c1.8 0 2.9.8 3.6 1.5l2.4-2.3C17.2 4 14.8 3 12 3 7.6 3 4 6.6 4 11s3.6 8 8 8c4.6 0 7-3.2 7-7.7 0-.5 0-.9-.1-1.1H12z"/></svg>
            Continue with Google
          </a>
          <a href="/auth/login/start/microsoft" class="btn-microsoft">
            <svg width="18" height="18" viewBox="0 0 24 24"><rect x="2" y="2" width="9" height="9" fill="#F35325"/><rect x="13" y="2" width="9" height="9" fill="#81BC06"/><rect x="2" y="13" width="9" height="9" fill="#05A6F0"/><rect x="13" y="13" width="9" height="9" fill="#FFBA08"/></svg>
            Continue with Microsoft
          </a>
        </div>
      `;
    }
  }

  function initWidgetChat() {
    const token = localStorage.getItem('app_jwt');
    const sessionId = 'widget-' + Math.random().toString(36).substr(2, 9);
    const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${wsProtocol}//${location.host}/ws/chat/${sessionId}?token=${token}`);
    const messagesDiv = document.getElementById('widget-messages');
    const input = document.getElementById('widget-input');
    const sendBtn = document.getElementById('widget-send');

    ws.onopen = () => sendBtn.disabled = false;
    ws.onmessage = (e) => {
      const data = JSON.parse(e.data);
      addBubble(data.response || data.error || 'Something went wrong', 'agent');
    };
    ws.onerror = () => addBubble('Connection error', 'agent');

    function addBubble(text, type) {
      const div = document.createElement('div');
      div.className = `bubble ${type}`;
      div.innerHTML = `<div class="bubble-avatar">${type === 'user' ? '👤' : '🤖'}</div><div class="bubble-text">${escapeHtml(text)}</div>`;
      messagesDiv.appendChild(div);
      messagesDiv.scrollTop = messagesDiv.scrollHeight;
    }

    sendBtn.addEventListener('click', () => {
      const msg = input.value.trim();
      if (!msg) return;
      ws.send(JSON.stringify({ query: msg }));
      addBubble(msg, 'user');
      input.value = '';
    });

    input.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') sendBtn.click();
    });
  }
});