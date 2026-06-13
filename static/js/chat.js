/**
 * ============================================================================
 * 聊天组件 — SSE 流式聊天 + 历史记录（按日期分组）
 * ============================================================================
 */

let isStreaming = false;
let abortController = null;
let messageHistory = [];
let autoScroll = true;
let chatChartIndex = 0;
let streamTimeout = null;
const STREAM_TIMEOUT_MS = 45000;

const CHAT_HISTORY_KEY = 'chat_message_history';
const SAVED_CONVERSATIONS_KEY = 'saved_conversations';

function saveMessages() {
    try {
        const clean = messageHistory.map(msg => ({
            role: msg.role, content: msg.content,
            sql: msg.sql || '', result: msg.result || [],
            columns: msg.columns || [], cacheHit: !!msg.cacheHit,
            executionTime: msg.executionTime || null,
        }));
        localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(clean));
    } catch (e) {}
}

function loadMessages() {
    try {
        const saved = localStorage.getItem(CHAT_HISTORY_KEY);
        if (saved) {
            const parsed = JSON.parse(saved);
            if (Array.isArray(parsed) && parsed.length > 0) {
                messageHistory = parsed; return;
            }
        }
    } catch (e) {}
    messageHistory = [{
        role: 'assistant',
        content: '你好！我是你的数据分析助手。请输入自然语言问题，我会帮你生成 SQL 并查询数据。',
    }];
}
loadMessages();

// ============================================================================
// 保存/读取/删除 历史对话（按日期分组）
// ============================================================================
function saveCurrentConversation() {
    const hasRealContent = messageHistory.some(m =>
        m.role === 'user' || (m.role === 'assistant' && m.sql)
    );
    if (!hasRealContent) return;
    try {
        const saved = JSON.parse(localStorage.getItem(SAVED_CONVERSATIONS_KEY) || '[]');
        const conversation = {
            id: Date.now(),
            savedAt: Date.now(),  // 用时间戳用于日期分组
            messages: messageHistory.map(msg => ({
                role: msg.role, content: msg.content, sql: msg.sql || '',
                result: msg.result || [], columns: msg.columns || [],
            })),
        };
        saved.unshift(conversation);
        if (saved.length > 30) saved.length = 30;
        localStorage.setItem(SAVED_CONVERSATIONS_KEY, JSON.stringify(saved));
    } catch (e) {}
}

function getSavedConversations() {
    try { return JSON.parse(localStorage.getItem(SAVED_CONVERSATIONS_KEY) || '[]'); }
    catch (e) { return []; }
}

// 全局暴露供 HTML 和 app.js 调用
let _activeConvId = null;
window.restoreConversation = function(id) {
    _activeConvId = id;
    try {
        const saved = JSON.parse(localStorage.getItem(SAVED_CONVERSATIONS_KEY) || '[]');
        const conv = saved.find(c => c.id === id);
        if (!conv) return;
        messageHistory = conv.messages.map(m => ({ ...m }));
        saveMessages();
        renderAllMessages();
        showToast('已恢复对话', 'success');
    } catch (e) {}
};
window.deleteSavedConversation = function(id) {
    try {
        const saved = JSON.parse(localStorage.getItem(SAVED_CONVERSATIONS_KEY) || '[]');
        const updated = saved.filter(c => c.id !== id);
        localStorage.setItem(SAVED_CONVERSATIONS_KEY, JSON.stringify(updated));
        renderSavedConversations();
    } catch (e) {}
};
window.renderSavedConversations = renderSavedConversations;

// ============================================================================
// 渲染历史对话（按日期分组）
// ============================================================================
function renderSavedConversations() {
    const container = document.getElementById('conversation-list');
    if (!container) return;
    try {
        const conversations = getSavedConversations();
        if (!conversations.length) {
            container.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text-muted);font-size:12px;">暂无历史</div>';
            return;
        }
        // 按日期分组
        const groups = {};
        const today = new Date();
        const todayStr = today.toLocaleDateString('zh-CN');
        const yesterdayStr = new Date(today.getTime() - 86400000).toLocaleDateString('zh-CN');

        conversations.forEach(conv => {
            const d = new Date(conv.savedAt);
            const dateStr = d.toLocaleDateString('zh-CN');
            let label;
            if (dateStr === todayStr) label = '今天';
            else if (dateStr === yesterdayStr) label = '昨天';
            else label = dateStr;
            if (!groups[label]) groups[label] = [];
            groups[label].push(conv);
        });

        let html = '';
        for (const [label, convs] of Object.entries(groups)) {
            html += `<div class="history-date-group"><div class="history-date-title">${label}</div>`;
            convs.forEach(conv => {
                const first = conv.messages.find(m => m.role === 'user');
                const preview = first ? first.content.slice(0, 28) + (first.content.length > 28 ? '...' : '') : '对话';
                html += `<div class="history-item" onclick="window.restoreConversation(${conv.id})">
                    <span class="question-text">${escapeHtml(preview)}</span>
                    <button class="delete-btn" onclick="event.stopPropagation();window.deleteSavedConversation(${conv.id})">×</button>
                </div>`;
            });
            html += '</div>';
        }
        container.innerHTML = html;
    } catch (e) { console.error('[Chat] renderSavedConversations:', e); }
}

// ============================================================================
// 聊天初始化
// ============================================================================
window.initChat = function initChat() {
    try {
        renderAllMessages();
        const textarea = document.getElementById('chat-textarea');
        const sendBtn = document.getElementById('send-btn');
        const msgContainer = document.getElementById('chat-messages');
        if (!textarea || !sendBtn || !msgContainer) return;

        msgContainer.addEventListener('scroll', () => {
            const threshold = 80;
            const atBottom = msgContainer.scrollHeight - msgContainer.scrollTop - msgContainer.clientHeight < threshold;
            autoScroll = atBottom;
        });

        const newChatBtn = document.getElementById('new-chat-btn');
        if (newChatBtn) newChatBtn.addEventListener('click', resetChat);

        textarea.addEventListener('input', () => {
            textarea.style.height = 'auto';
            textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
            sendBtn.disabled = !textarea.value.trim() || isStreaming;
        });
        textarea.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!sendBtn.disabled) sendMessage(); }
        });
        sendBtn.addEventListener('click', sendMessage);

        document.querySelectorAll('.quick-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.getElementById('chat-textarea').value = btn.dataset.q || btn.textContent;
                sendMessage();
            });
        });

        loadSuggestedQuestions();

        renderSavedConversations();
    } catch (e) { console.error('[Chat] init error:', e); }
}

// ============================================================================
// 基于 Schema 动态推荐问题
// ============================================================================
async function loadSuggestedQuestions() {
    try {
        const resp = await fetch('/api/db/status');
        const data = await resp.json();
        const tables = data.tables || [];
        if (tables.length === 0) return;

        let qBar = document.getElementById('quick-questions');
        if (!qBar) {
            qBar = document.createElement('div');
            qBar.id = 'quick-questions';
            qBar.style.cssText = 'padding:8px 16px;display:flex;gap:8px;flex-wrap:wrap;border-top:1px solid var(--border-color);background:var(--bg-secondary);flex-shrink:0;';
            const chatArea = document.getElementById('chat-input-area');
            if (chatArea) chatArea.parentNode.insertBefore(qBar, chatArea);
        }
        const suggestions = [];
        for (const table of tables.slice(0, 3)) {
            suggestions.push('查询' + table + '的所有数据');
            suggestions.push('统计' + table + '的总数');
        }
        suggestions.push('列出所有数据库表');

        qBar.innerHTML = suggestions.slice(0, 3).map(function(q) {
            return '<button class="quick-btn" data-q="' + q.replace(/"/g, '&quot;') + '" style="padding:5px 14px;font-size:12px;background:#fff;color:var(--accent-cat);border:1px solid var(--accent-cat);border-radius:16px;cursor:pointer;transition:all 0.2s;">' + q + '</button>';
        }).join('');
        qBar.querySelectorAll('.quick-btn').forEach(function(btn) {
            btn.addEventListener('mouseenter', function() { btn.style.background = 'var(--accent-cat)'; btn.style.color = '#fff'; });
            btn.addEventListener('mouseleave', function() { btn.style.background = '#fff'; btn.style.color = 'var(--accent-cat)'; });
            btn.addEventListener('click', function() {
                document.getElementById('chat-textarea').value = btn.dataset.q;
                sendMessage();
            });
        });
    } catch(e) { /* silent */ }
}

// ============================================================================
// 发送消息
// ============================================================================
async function sendMessage() {
    const textarea = document.getElementById('chat-textarea');
    const question = textarea.value.trim();
    if (!question || isStreaming) return;

    if (!autoScroll) autoScroll = true;
    textarea.value = '';
    textarea.style.height = 'auto';
    document.getElementById('send-btn').disabled = true;

    messageHistory.push({ role: 'user', content: question });
    saveMessages();
    renderAllMessages();

    let assistantMsg = null;
    function ensureMsg() {
        if (!assistantMsg) {
            assistantMsg = { role: 'assistant', content: '⏳ 思考中...', sql: '', result: null, columns: [], cacheHit: false };
            messageHistory.push(assistantMsg);
            saveMessages();
            renderAllMessages();
        }
        return assistantMsg;
    }

    showStreamStatus('检查缓存中...');
    isStreaming = true;
    abortController = new AbortController();

    function resetStreamTimeout() {
        if (streamTimeout) clearTimeout(streamTimeout);
        streamTimeout = setTimeout(() => {
            if (!isStreaming) return;
            try { abortController.abort(); } catch (e) {}
            isStreaming = false;
            document.getElementById('send-btn').disabled = false;
            hideStreamStatus();
            if (assistantMsg && assistantMsg.content === '⏳ 思考中...') {
                assistantMsg.content = '⚠️ 请求超时：45 秒内未收到完整响应';
                updateLastMessage(assistantMsg);
            }
            showToast('请求超时，请检查后端服务', 'error');
            saveMessages();
        }, STREAM_TIMEOUT_MS);
    }
    resetStreamTimeout();

    try {
        const response = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question }),
            signal: abortController.signal,
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '', currentEvent = '';

        function processLines() {
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            for (const line of lines) {
                if (line.startsWith('event: ')) currentEvent = line.slice(7).trim();
                else if (line.startsWith('data: ')) {
                    try { handleEvent(currentEvent, JSON.parse(line.slice(6)), ensureMsg()); } catch (e) {}
                }
            }
        }

        function handleEvent(event, data, msg) {
            switch (event) {
                case 'step': showStreamStatus(data.message || ''); break;
                case 'sql': if (msg.content === '⏳ 思考中...') msg.content = ''; msg.sql = data.sql || ''; break;
                case 'result':
                    if (msg.content === '⏳ 思考中...') msg.content = '';
                    hideStreamStatus();
                    if (data.clarification) { msg.content = data.clarification; msg.sql = ''; msg.result = []; msg.columns = []; }
                    else { msg.result = data.result || []; msg.columns = data.columns || []; if (data.sql) msg.sql = data.sql; if (data.cache_hit) msg.cacheHit = true; msg.executionTime = data.execution_time; }
                    updateLastMessage(msg);
                    break;
                case 'error':
                    hideStreamStatus();
                    msg.content += '\n\n**错误**: ' + (data.error || '');
                    updateLastMessage(msg);
                    showToast(data.error, 'error');
                    break;
                case 'done':
                    hideStreamStatus();
                    if (streamTimeout) clearTimeout(streamTimeout);
                    isStreaming = false; abortController = null;
                    document.getElementById('send-btn').disabled = false;
                    saveMessages();
                    break;
            }
        }

        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                processLines();
                if (isStreaming) {
                    console.warn('[Chat] stream closed without done event');
                    hideStreamStatus(); isStreaming = false;
                    document.getElementById('send-btn').disabled = false;
                    saveMessages();
                }
                if (streamTimeout) clearTimeout(streamTimeout);
                hideStreamStatus();
                break;
            }
            resetStreamTimeout();
            buffer += decoder.decode(value, { stream: true });
            processLines();
        }
        scrollToBottom();
    } catch (err) {
        if (streamTimeout) clearTimeout(streamTimeout);
        if (err.name === 'AbortError') return;
        hideStreamStatus(); isStreaming = false; abortController = null;
        document.getElementById('send-btn').disabled = false;
        const errMsg = err.message || '';
        let userMsg = '';
        if (errMsg.includes('Failed to fetch') || errMsg.includes('NetworkError')) userMsg = '网络连接失败';
        else if (errMsg.includes('500')) userMsg = '服务器内部错误';
        else if (errMsg.includes('timeout') || errMsg.includes('Timeout')) userMsg = '请求超时';
        else userMsg = '请求失败: ' + errMsg;
        if (assistantMsg) {
            if (assistantMsg.content === '⏳ 思考中...') assistantMsg.content = '⚠️ ' + userMsg;
            else assistantMsg.content += '\n\n⚠️ ' + userMsg;
            updateLastMessage(assistantMsg);
        }
        showToast(userMsg, 'error');
        saveMessages();
    }
}

// ============================================================================
// 消息渲染
// ============================================================================
function updateLastMessage(msg) {
    const c = document.getElementById('chat-messages');
    if (!c) return;
    try {
        const msgs = c.querySelectorAll('.message.assistant');
        const target = msgs[msgs.length - 1];
        if (!target) return;
        const contentDiv = target.querySelector('.message-content');
        if (!contentDiv) { target.replaceWith(createMessageElement(msg)); scrollToBottom(); return; }
        const newEl = createMessageElement(msg);
        const newContent = newEl.querySelector('.message-content');
        if (newContent) contentDiv.innerHTML = newContent.innerHTML;
        scrollToBottom();
    } catch (e) {}
}

function resetChat() {
    cancelStream();
    saveCurrentConversation();
    messageHistory = [{
        role: 'assistant',
        content: '你好！我是你的数据分析助手。请输入自然语言问题，我会帮你生成 SQL 并查询数据。',
    }];
    localStorage.removeItem(CHAT_HISTORY_KEY);
    renderAllMessages();
    const textarea = document.getElementById('chat-textarea');
    if (textarea) { textarea.value = ''; textarea.style.height = 'auto'; }
    const sendBtn = document.getElementById('send-btn');
    if (sendBtn) sendBtn.disabled = true;
    autoScroll = true;
    renderSavedConversations();
}

function cancelStream() {
    if (streamTimeout) clearTimeout(streamTimeout);
    if (abortController) {
        try { abortController.abort(); } catch (e) {}
        isStreaming = false; abortController = null;
        document.getElementById('send-btn').disabled = false;
        hideStreamStatus();
    }
}

function renderAllMessages() {
    const container = document.getElementById('chat-messages');
    if (!container) return;
    container.innerHTML = '';
    messageHistory.forEach(msg => container.appendChild(createMessageElement(msg)));
    scrollToBottom();
}

// ============================================================================
// createMessageElement — 渲染单条消息
// ============================================================================
function createMessageElement(msg) {
    const div = document.createElement('div');
    div.className = `message ${msg.role}`;

    // Avatar outside message bubble
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = msg.role === 'user' ? '🐱' : '🤖';

    const content = document.createElement('div');
    content.className = 'message-content';

    if (msg.content || msg.sql) {
        const rendered = renderMessageContent(msg.content || '', msg);
        if (typeof rendered === 'string') content.innerHTML = rendered;
        else content.appendChild(rendered);
    }

    // 结果表格 + 图表
    if (msg.sql && msg.columns && msg.columns.length > 0) {
        const layoutDiv = document.createElement('div');
        layoutDiv.className = 'chat-result-layout';
        const tableSide = document.createElement('div');
        tableSide.className = 'chat-result-table';
        tableSide.innerHTML = renderTable(msg.columns, msg.result);
        layoutDiv.appendChild(tableSide);

        if (msg.result && msg.result.length > 0) {
            const eb = document.createElement('div');
            eb.style.cssText = 'display:flex;gap:6px;margin-top:6px;';
            const csvBtn = document.createElement('button');
            csvBtn.textContent = '📥 CSV';
            csvBtn.style.cssText = 'padding:4px 10px;font-size:11px;background:#f0f4ff;color:#4a5a6a;border:1px solid #d0dce8;border-radius:4px;cursor:pointer;';
            csvBtn.onclick = () => exportTableCSV(msg.columns, msg.result, `data_${Date.now()}.csv`);
            eb.appendChild(csvBtn);
            const xlsBtn = document.createElement('button');
            xlsBtn.textContent = '📥 XLS';
            xlsBtn.style.cssText = 'padding:4px 10px;font-size:11px;background:#f0f4ff;color:#4a5a6a;border:1px solid #d0dce8;border-radius:4px;cursor:pointer;';
            xlsBtn.onclick = () => exportTableXLS(msg.columns, msg.result, `data_${Date.now()}.xls`);
            eb.appendChild(xlsBtn);
            tableSide.appendChild(eb);
        }

        // 图表选择器
        const chartSide = document.createElement('div');
        chartSide.className = 'chat-result-chart';
        if (msg.result && msg.result.length > 0) {
            const cIdx = ++chatChartIndex;
            const selBar = document.createElement('div');
            selBar.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 8px;align-items:center;';
            selBar.innerHTML = '<span style="font-size:12px;color:#7a8a9a;font-weight:600;">📊 图表:</span>';
            const types = [['bar','柱状'],['line','折线'],['pie','饼图'],['scatter','散点'],['funnel','漏斗'],['radar','雷达']];
            let activeType = 'bar';
            types.forEach(([id, name]) => {
                const btn = document.createElement('button');
                btn.textContent = name;
                btn.style.cssText = 'padding:3px 10px;font-size:11px;background:#f0f4ff;color:#4a5a6a;border:1px solid #d0dce8;border-radius:12px;cursor:pointer;';
                if (id === 'bar') { btn.style.background = '#4a9eff'; btn.style.color = '#fff'; }
                btn.onclick = () => {
                    selBar.querySelectorAll('button').forEach(b => { b.style.background = '#f0f4ff'; b.style.color = '#4a5a6a'; });
                    btn.style.background = '#4a9eff'; btn.style.color = '#fff';
                    activeType = id;
                    renderChatChart(cIdx, id, msg);
                };
                selBar.appendChild(btn);
            });
            chartSide.appendChild(selBar);

            const cw = document.createElement('div');
            cw.style.cssText = 'margin-top:4px;';
            const cd = document.createElement('div');
            cd.id = `chat-chart-${cIdx}`;
            cd.style.cssText = 'width:100%;height:220px;';
            cw.appendChild(cd);
            const dlBtn = document.createElement('button');
            dlBtn.textContent = '⬇ 下载';
            dlBtn.style.cssText = 'margin-top:6px;padding:4px 12px;background:#fff;color:#4a9eff;border:1px solid #4a9eff;border-radius:4px;cursor:pointer;font-size:11px;';
            dlBtn.onclick = () => downloadChart(`chat-chart-${cIdx}`, `chart_${Date.now()}.png`);
            cw.appendChild(dlBtn);

            // Refresh button
            const refreshBtn = document.createElement('button');
            refreshBtn.textContent = '🔄 刷新';
            refreshBtn.style.cssText = 'margin-top:6px;margin-left:6px;padding:4px 12px;background:#fff;color:#2ecc71;border:1px solid #2ecc71;border-radius:4px;cursor:pointer;font-size:11px;';
            refreshBtn.onclick = async () => {
                refreshBtn.textContent = '⏳ 刷新中...';
                refreshBtn.disabled = true;
                try {
                    // Find the user question from message history
                    const allMsgs = document.getElementById('chat-messages').querySelectorAll('.message.user');
                    const lastUserQ = allMsgs[allMsgs.length - 1]?.textContent || '';
                    const resp = await fetch('/api/chat/stream', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ question: lastUserQ }),
                    });
                    const reader = resp.body.getReader();
                    const dec = new TextDecoder();
                    let buf = '';
                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;
                        buf += dec.decode(value, { stream: true });
                    }
                    // Parse SSE for result data
                    const dataMatch = buf.match(/event:\s*result[\s\S]*?数据:\s*(\{.*?\})/);
                    if (dataMatch) {
                        const data = JSON.parse(dataMatch[1]);
                        if (data.result && data.columns) {
                            msg.result = data.result;
                            msg.columns = data.columns;
                            renderChatChart(cIdx, activeType || 'bar', msg);
                            showToast('✅ 已刷新', 'success');
                        }
                    }
                } catch(e) { showToast('刷新失败: ' + e.message, 'error'); }
                refreshBtn.textContent = '🔄 刷新';
                refreshBtn.disabled = false;
            };
            cw.appendChild(refreshBtn);

            chartSide.appendChild(cw);

            setTimeout(() => renderChatChart(cIdx, 'bar', msg), 50);
        }
        layoutDiv.appendChild(chartSide);
        content.appendChild(layoutDiv);
    }

    if (msg.cacheHit) {
        const badge = document.createElement('span');
        badge.style.cssText = 'display:inline-block;padding:2px 8px;border-radius:4px;background:#2ecc71;color:#fff;font-size:11px;margin-top:6px;';
        badge.textContent = '⚡ 缓存命中';
        content.appendChild(badge);
    }
    if (msg.executionTime) {
        const timeEl = document.createElement('div');
        timeEl.style.cssText = 'color:#8892a8;font-size:12px;margin-top:4px;';
        timeEl.textContent = `⏱️ ${msg.executionTime}s`;
        content.appendChild(timeEl);
    }

    div.appendChild(avatar);
    div.appendChild(content);
    return div;
}

// ============================================================================
// 渲染消息文本（思维链折叠 + SQL 显示）
// ============================================================================
function renderMessageContent(text, msg) {
    if (msg && msg.sql) {
        const cotText = text
            .replace(/\*\*错误\*\*.*/s, '')
            .replace(/```sql[\s\S]*?```/g, '')
            .replace(/```[\s\S]*?```/g, '')
            .replace(/SELECT\s.*?(?:LIMIT\s\d+)?;?/gis, '')
            .trim();
        let html = '';
        if (cotText) html += buildCollapsibleCot(`<p style="margin-bottom:8px;">${escapeHtml(cotText)}</p>`, false);
        else html += buildCollapsibleCot('<p style="margin-bottom:4px;color:#8892a8;font-size:12px;">✅ 分析完成</p>', false);
        html += `<pre style="background:#3d8cff;color:#fff;border-radius:6px;padding:12px;overflow-x:auto;border:none;margin-top:8px;"><code class="sql" style="color:#fff;background:transparent;">${escapeHtml(msg.sql)}</code></pre>`;
        return html;
    }
    return escapeHtml(text).replace(/\n/g, '<br>');
}

function buildCollapsibleCot(innerHtml, expanded) {
    const id = 'cot-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
    const d = expanded ? 'block' : 'none';
    const icon = expanded ? '▼' : '▶';
    const label = expanded ? '收起分析' : '展开分析';
    return `<div class="cot-wrapper"><button class="cot-toggle" onclick="toggleCot('${id}', this)"><span class="cot-icon">${icon}</span> ${label}</button><div id="${id}" class="cot-content" style="display:${d}">${innerHtml}</div></div>`;
}

window.toggleCot = function(id, btn) {
    const el = document.getElementById(id);
    if (!el) return;
    const hidden = el.style.display === 'none';
    el.style.display = hidden ? 'block' : 'none';
    btn.innerHTML = hidden ? '<span class="cot-icon">▼</span> 收起分析' : '<span class="cot-icon">▶</span> 展开分析';
};

function renderChatChart(index, type, msg) {
    const dom = document.getElementById(`chat-chart-${index}`);
    if (!dom || !msg.columns || !msg.result) return;
    try { const ex = echarts.getInstanceByDom(dom); if (ex) ex.dispose(); } catch(e) {}
    const cd = autoDetectChartData(msg);
    if (!cd) return;
    cd.chartType = type;
    const ed = (typeof chartDataToECharts === 'function') ? chartDataToECharts(type, cd) : { labels: cd.labels, values: cd.values };
    if (typeof renderChart === 'function') renderChart(dom, type, ed);
}

function autoDetectChartData(msg) {
    if (!msg.columns || !msg.result || msg.result.length === 0) return null;
    let labelCol = null, valueCol = null;
    if (Array.isArray(msg.columns)) {
        for (let i = 0; i < msg.columns.length; i++) {
            const col = msg.columns[i];
            const sample = msg.result[0];
            const val = Array.isArray(sample) ? sample[i] : sample[col];
            if (val != null && !isNaN(parseFloat(val)) && isFinite(val)) { if (!valueCol) valueCol = { index: i, name: col }; }
            else { if (!labelCol) labelCol = { index: i, name: col }; }
        }
    }
    if (!labelCol || !valueCol) return null;
    const labels = msg.result.map(r => String(Array.isArray(r) ? r[labelCol.index] : r[labelCol.name]));
    const values = msg.result.map(r => parseFloat(Array.isArray(r) ? r[valueCol.index] : r[valueCol.name]));
    if (labels.length === 0 || values.length === 0) return null;
    const unique = {};
    labels.forEach((l, i) => { if (!unique[l]) unique[l] = 0; unique[l] += values[i] || 0; });
    const dedupLabels = Object.keys(unique);
    const dedupValues = dedupLabels.map(l => unique[l]);
    const chartType = (typeof suggestChartType === 'function') ? suggestChartType(dedupLabels, dedupValues) : 'bar';
    return { labels: dedupLabels, values: dedupValues, chartType };
}

// ============================================================================
// 添加到大屏（全局暴露）
// ============================================================================
window.addChatChartToDashboard = function(config) {
    try {
        const key = 'dashboard_chat_charts';
        const saved = JSON.parse(localStorage.getItem(key) || '[]');
        const id = Date.now();
        const curStyle = typeof getActiveStyle === 'function' ? getActiveStyle() : 'blue';
        saved.push({ id, title: config.title || '来自查询的图表', chartType: config.chartType || 'bar',
            chartData: { labels: config.labels, values: config.values }, chartStyle: curStyle, source: "chat", dashId: "__default__" });
        localStorage.setItem(key, JSON.stringify(saved));
        showToast('✅ 已添加到大屏', 'success');
        if (typeof loadChatCharts === "function") setTimeout(() => loadChatCharts(), 200);
    } catch (e) { showToast('添加失败', 'error'); }
};

// ============================================================================
// 工具函数
// ============================================================================
function scrollToBottom() {
    const c = document.getElementById('chat-messages');
    if (c) setTimeout(() => { c.scrollTop = c.scrollHeight; }, 10);
}
function downloadChart(domId, filename) {
    const dom = document.getElementById(domId);
    if (!dom) return;
    try {
        const inst = echarts.getInstanceByDom(dom);
        if (!inst) return;
        const url = inst.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' });
        const a = document.createElement('a'); a.href = url; a.download = filename || 'chart.png';
        document.body.appendChild(a); a.click(); a.remove();
    } catch(e) { showToast('下载失败', 'error'); }
}
function exportTableCSV(columns, rows, filename) {
    if (!columns || !rows) return;
    const bom = '﻿';
    const header = columns.map(c => '"' + String(c).replace(/"/g, '""') + '"').join(',');
    const data = rows.map(r => columns.map(c => { const v = r[c] !== undefined ? r[c] : ''; return '"' + String(v).replace(/"/g, '""') + '"'; }).join(','));
    const csv = bom + header + '\n' + data.join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = filename || 'data.csv';
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
}
function exportTableXLS(columns, rows, filename) {
    if (!columns || !rows) return;
    let html = '<html><meta charset="utf-8"><body><table>';
    html += '<tr>' + columns.map(c => '<th>' + String(c).replace(/</g, '&lt;') + '</th>').join('') + '</tr>';
    html += rows.map(r => '<tr>' + columns.map(c => { const v = r[c] !== undefined ? r[c] : ''; return '<td>' + String(v).replace(/</g, '&lt;') + '</td>'; }).join('') + '</tr>').join('');
    html += '</table></body></html>';
    const blob = new Blob([html], { type: 'application/vnd.ms-excel;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = filename || 'data.xls';
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
}
function showToast(msg, type) {
    const bg = type === 'error' ? '#e74c3c' : type === 'success' ? '#2ecc71' : '#4a9eff';
    const el = document.createElement('div');
    el.style.cssText = `position:fixed;top:20px;right:20px;padding:10px 20px;border-radius:8px;color:#fff;font-size:13px;z-index:9999;background:${bg};box-shadow:0 4px 12px rgba(0,0,0,0.15);`;
    el.textContent = msg; document.body.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 400); }, 3000);
}
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text; return div.innerHTML;
}

// ============================================================================
// 流状态提示
// ============================================================================
function showStreamStatus(msg) {
    let el = document.getElementById('stream-status');
    if (!el) {
        el = document.createElement('div');
        el.id = 'stream-status';
        el.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:#4a9eff;color:#fff;padding:8px 20px;border-radius:20px;font-size:13px;z-index:9999;box-shadow:0 2px 12px rgba(74,158,255,0.3);';
        document.body.appendChild(el);
    }
    el.textContent = '⏳ ' + msg;
    el.style.display = '';
}
function hideStreamStatus() {
    const el = document.getElementById('stream-status');
    if (el) el.style.display = 'none';
}

// 安全复位
isStreaming = false;
const sendBtn = document.getElementById('send-btn');
if (sendBtn) sendBtn.disabled = false;

// ============================================================================
// 侧栏折叠
// ============================================================================
window.toggleSidebar = function(id) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('collapsed');
};
