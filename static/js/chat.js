/**
 * ============================================================================
 * 聊天组件 — SSE 流式聊天 + 打字机效果
 * ============================================================================
 */

let isStreaming = false;
let abortController = null;
let messageHistory = [];
let autoScroll = true;  // 是否自动滚动到底部
let chatChartIndex = 0; // 聊天图表的唯一 ID 计数器

// ============================================================================
// 会话持久化 — 刷新不丢消息
// ============================================================================
const CHAT_HISTORY_KEY = 'chat_message_history';

function saveMessages() {
    try {
        // 只存可序列化的字段，去掉 DOM 标记
        const clean = messageHistory.map(msg => ({
            role: msg.role,
            content: msg.content,
            sql: msg.sql || '',
            result: msg.result || [],
            columns: msg.columns || [],
            cacheHit: !!msg.cacheHit,
            executionTime: msg.executionTime || null,
        }));
        localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(clean));
    } catch (e) { /* localStorage full */ }
}

function loadMessages() {
    try {
        const saved = localStorage.getItem(CHAT_HISTORY_KEY);
        if (saved) {
            const parsed = JSON.parse(saved);
            if (Array.isArray(parsed) && parsed.length > 0) {
                messageHistory = parsed;
                return;
            }
        }
    } catch (e) { /* ignore corrupt data */ }
    // 默认欢迎消息
    messageHistory = [{
        role: 'assistant',
        content: '你好！我是你的数据分析助手。请输入自然语言问题，我会帮你生成 SQL 并查询数据。',
    }];
}

// 初始化加载历史
loadMessages();

/**
 * 初始化聊天组件
 */
function initChat() {
    try {
    renderAllMessages();

    const textarea = document.getElementById('chat-textarea');
    const sendBtn = document.getElementById('send-btn');
    const messagesContainer = document.getElementById('chat-messages');
    if (!textarea || !sendBtn || !messagesContainer) { console.warn('[Chat] init aborted'); return; }

    // Scroll listener — detect user scrolling up to read history
    messagesContainer.addEventListener('scroll', () => {
        const threshold = 80;
        const atBottom = messagesContainer.scrollHeight - messagesContainer.scrollTop - messagesContainer.clientHeight < threshold;
        autoScroll = atBottom;
        const scrollBtn = document.getElementById('scroll-bottom-btn');
        if (scrollBtn) {
            scrollBtn.style.display = atBottom ? 'none' : 'flex';
        }
    });

    // New Chat button
    const newChatBtn = document.getElementById('new-chat-btn');
    if (newChatBtn) {
        newChatBtn.addEventListener('click', resetChat);
    }

    // Scroll-to-bottom button
    const scrollBtn = document.getElementById('scroll-bottom-btn');
    if (scrollBtn) {
        scrollBtn.addEventListener('click', () => {
            autoScroll = true;
            scrollToBottom();
            scrollBtn.style.display = 'none';
        });
    }

    // Auto-resize textarea
    textarea.addEventListener('input', () => {
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 150) + 'px';
        sendBtn.disabled = !textarea.value.trim() || isStreaming;
    });

    // Enter to send (Shift+Enter for newline)
    textarea.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (!sendBtn.disabled) sendMessage();
        }
    });

    sendBtn.addEventListener('click', sendMessage);

    // Reset stuck streaming state on new chat
    if (newChatBtn && !newChatBtn._fixed) {
        newChatBtn._fixed = true;
        newChatBtn.addEventListener('click', () => { isStreaming = false; document.getElementById('send-btn').disabled = false; });
    }

    // Quick questions
    document.querySelectorAll('.quick-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.getElementById('chat-textarea').value = btn.dataset.q || btn.textContent;
            sendMessage();
        });
    });
    } catch(e) { console.error('[Chat] init error:', e); }
}

/**
 * 发送消息
 */
async function sendMessage() {
    const textarea = document.getElementById('chat-textarea');
    const question = textarea.value.trim();
    if (!question || isStreaming) return;

    // User sent a message — re-enable auto-scroll
    if (!autoScroll) {
        autoScroll = true;
        const scrollBtn = document.getElementById('scroll-bottom-btn');
        if (scrollBtn) scrollBtn.style.display = 'none';
    }

    textarea.value = '';
    textarea.style.height = 'auto';
    document.getElementById('send-btn').disabled = true;

    // Add user message
    messageHistory.push({ role: 'user', content: question });
    saveMessages();
    renderAllMessages();

    // Assistant message — created when first real data arrives
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

    // Show status
    showStreamStatus('检查缓存中...');

    isStreaming = true;
    abortController = new AbortController();

    try {
        const response = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question }),
            signal: abortController.signal,
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let currentEvent = '';
        let fullSql = '';
        let lastMsgElement = null;

        function processLines() {
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('event: ')) {
                    currentEvent = line.slice(7).trim();
                } else if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        handleEvent(currentEvent, data, ensureMsg());
                    } catch (e) {
                        // Skip malformed JSON
                    }
                }
            }
        }

        function handleEvent(event, data, msg) {
            switch (event) {
                case 'step':
                    showStreamStatus(data.message || '');
                    break;

                case 'token':
                    if (msg.content === '⏳ 思考中...') msg.content = '';
                    msg.content += data.text || '';
                    if (data.text) { fullSql += data.text; }
                    updateLastMessage(msg);
                    break;

                case 'sql':
                    if (msg.content === '⏳ 思考中...') msg.content = '';
                    msg.sql = data.sql || '';
                    break;

                case 'result':
                    if (msg.content === '⏳ 思考中...') msg.content = '';
                    hideStreamStatus();
                    if (data.clarification) {
                        // 澄清请求 — 直接显示问题
                        msg.content = data.clarification;
                        msg.sql = '';
                        msg.result = [];
                        msg.columns = [];
                    } else {
                        msg.result = data.result || [];
                        msg.columns = data.columns || [];
                        if (data.sql) msg.sql = data.sql;  // 缓存命中时 SQL 在 result 事件中
                        if (data.cache_hit) msg.cacheHit = true;
                        msg.executionTime = data.execution_time;
                    }
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
                    isStreaming = false;
                    abortController = null;
                    document.getElementById('send-btn').disabled = false;
                    saveMessages();
                    break;
            }
        }

        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                processLines();
                hideStreamStatus();
                isStreaming = false;
                abortController = null;
                document.getElementById('send-btn').disabled = false;
                saveMessages();
                break;
            }
            buffer += decoder.decode(value, { stream: true });
            processLines();
        }

        scrollToBottom();

    } catch (err) {
        if (err.name === 'AbortError') return;
        hideStreamStatus();
        isStreaming = false;
        abortController = null;
        document.getElementById('send-btn').disabled = false;
        const errMsg = err.message || '';
        if (errMsg.includes('Failed to fetch') || errMsg.includes('NetworkError')) {
            showToast('网络连接失败，请检查后端服务是否正常运行', 'error');
        } else if (errMsg.includes('500') || errMsg.includes('Internal Server')) {
            showToast('服务器内部错误，请查看后端日志', 'error');
        } else if (errMsg.includes('timeout') || errMsg.includes('Timeout')) {
            showToast('请求超时，请稍后重试', 'error');
        } else {
            showToast('请求失败: ' + errMsg, 'error');
        }
    }
}

// Update last message content only (avoids avatar re-render/flash)
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
    } catch(e) {}
}

/**
 * 开启新对话 — 清除历史、重置状态
 */
function resetChat() {
    cancelStream();
    messageHistory = [
        {
            role: 'assistant',
            content: '你好！我是你的数据分析助手。请输入自然语言问题，我会帮你生成 SQL 并查询数据。',
        },
    ];
    localStorage.removeItem(CHAT_HISTORY_KEY);
    renderAllMessages();
    const textarea = document.getElementById('chat-textarea');
    textarea.value = '';
    textarea.style.height = 'auto';
    document.getElementById('send-btn').disabled = true;
    autoScroll = true;
    const scrollBtn = document.getElementById('scroll-bottom-btn');
    if (scrollBtn) scrollBtn.style.display = 'none';
}

/**
 * 取消当前流式请求
 */
function cancelStream() {
    if (abortController) {
        abortController.abort();
        isStreaming = false;
        abortController = null;
        document.getElementById('send-btn').disabled = false;
        hideStreamStatus();
    }
}

/**
 * 渲染所有消息
 */
function renderAllMessages() {
    const container = document.getElementById('chat-messages');
    container.innerHTML = '';
    messageHistory.forEach(msg => {
        container.appendChild(createMessageElement(msg));
    });
    scrollToBottom();
}

/**
 * 创建消息 DOM 元素
 */
function createMessageElement(msg) {
    const div = document.createElement('div');
    div.className = `message ${msg.role}`;
    div.style.position = 'relative';
    div.style.zIndex = '1';
    div.dataset.role = msg.role;

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = msg.role === 'user' ? '🐱' : '🤖';

    const content = document.createElement('div');
    content.className = 'message-content';

    // Message text with markdown-like rendering
    if (msg.content || msg.sql) {
        const rendered = renderMessageContent(msg.content || '', msg);
        if (typeof rendered === 'string') {
            content.innerHTML = rendered;
        } else {
            content.appendChild(rendered);
        }
    }

    // Result table + chart selector (show even for 0 rows if columns exist)
    if (msg.sql && msg.columns && msg.columns.length > 0) {
        const hasData = msg.result && msg.result.length > 0;

        const layoutDiv = document.createElement('div');
        layoutDiv.className = 'chat-result-layout';

        // ---- TABLE ----
        const tableSide = document.createElement('div');
        tableSide.className = 'chat-result-table';
        const tableHtml = renderTable(msg.columns, msg.result);
        tableSide.innerHTML = tableHtml;
        layoutDiv.appendChild(tableSide);

        // Export CSV/XLS buttons
        if (hasData && msg.columns) {
            const eb = document.createElement('div');
            eb.style.cssText = 'display:flex;gap:6px;margin-top:6px;';
            const c = document.createElement('button');
            c.textContent = '📥 CSV'; c.style.cssText = 'padding:4px 10px;font-size:11px;background:#f0f4ff;color:#4a5a6a;border:1px solid #d0dce8;border-radius:4px;cursor:pointer;';
            c.onclick = () => exportTableCSV(msg.columns, msg.result, `data_${Date.now()}.csv`);
            eb.appendChild(c);
            const x = document.createElement('button');
            x.textContent = '📥 XLS'; x.style.cssText = 'padding:4px 10px;font-size:11px;background:#f0f4ff;color:#4a5a6a;border:1px solid #d0dce8;border-radius:4px;cursor:pointer;';
            x.onclick = () => exportTableXLS(msg.columns, msg.result, `data_${Date.now()}.xls`);
            eb.appendChild(x);
            tableSide.appendChild(eb);
        }

        // ---- CHART SELECTOR (only when data exists) ----
        const chartSide = document.createElement('div');
        chartSide.className = 'chat-result-chart';
        if (hasData) {
            const chartIndex = ++chatChartIndex;

            // Selector bar
            const selectorBar = document.createElement('div');
            selectorBar.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 8px;align-items:center;position:relative;z-index:10;';
            const sLabel = document.createElement('span');
            sLabel.textContent = '📊 图表:';
            sLabel.style.cssText = 'font-size:12px;color:#7a8a9a;font-weight:600;margin-right:4px;';
            selectorBar.appendChild(sLabel);

            const chartTypes = [
                ['bar','柱状图'],['line','折线图'],['pie','饼图'],
                ['scatter','散点图'],['funnel','漏斗图'],['radar','雷达图'],
                ['treemap','树图'],['heatmap','热力图'],['sankey','桑基图'],
                ['gauge','仪表盘'],['boxplot','箱线图'],['parallel','平行坐标'],
                ['sunburst','旭日图'],['effectScatter','涟漪散点'],['candlestick','K线图'],
                ['pictorialBar','象形柱图'],['graph','关系图'],['themeRiver','主题河流'],
            ];
            let activeType = 'bar';

            chartTypes.forEach(([id, name]) => {
                const btn = document.createElement('button');
                btn.dataset.type = id;
                btn.textContent = name;
                btn.style.cssText = 'padding:4px 10px;font-size:11px;background:#f0f4ff;color:#4a5a6a;border:1px solid #d0dce8;border-radius:12px;cursor:pointer;transition:all 0.2s;white-space:nowrap;';
                if (id === 'bar') { btn.style.background='#4a9eff';btn.style.color='#fff';btn.style.borderColor='#4a9eff'; }
                btn.onmouseover = () => { if (btn.style.background !== 'rgb(74, 158, 255)') btn.style.background='#e0e8f2'; };
                btn.onmouseout = () => { if (btn.style.background !== 'rgb(74, 158, 255)') btn.style.background='#f0f4ff'; };
                btn.onclick = () => {
                    selectorBar.querySelectorAll('[data-type]').forEach(b => { b.style.background='#f0f4ff';b.style.color='#4a5a6a';b.style.borderColor='#d0dce8'; });
                    btn.style.background='#4a9eff';btn.style.color='#fff';btn.style.borderColor='#4a9eff';
                    activeType = id;
                    renderChatChart(chartIndex, id, msg);
                    const ab = document.getElementById(`add-chart-btn-${chartIndex}`);
                    if (ab) ab.style.display = '';
                };
                selectorBar.appendChild(btn);
            });

            // Style selector
            const styleSel = document.createElement('select');
            styleSel.style.cssText = 'margin-left:4px;padding:3px 8px;font-size:11px;border:1px solid #d0dce8;border-radius:4px;background:#fff;color:#4a5a6a;cursor:pointer;outline:none;';
            [ ['blue','蓝'],['green','绿'],['red','红'],['orange','橙'],['purple','紫'],['cyan','青'],['gray','灰'],
              ['lightBlue','浅蓝'],['lightGreen','浅绿'],['lightPink','浅粉'],['lightOrange','浅橙'],['lightPurple','浅紫'],['lightCyan','浅青'],['lightGray','浅灰'],
              ['darkBlue','深蓝'],['darkGreen','深绿'],['darkRed','深红'],['darkOrange','深橙'],['darkPurple','深紫'],['darkCyan','深青'],['darkGray','深灰'],
            ].forEach(([v,t]) => { const o=document.createElement('option');o.value=v;o.textContent=t;styleSel.appendChild(o); });
            styleSel.onchange = () => { if (typeof setChartStyle === 'function') setChartStyle(styleSel.value); renderChatChart(chartIndex, activeType, msg); };
            selectorBar.appendChild(styleSel);

            chartSide.appendChild(selectorBar);

            // Chart container
            const cw = document.createElement('div');
            cw.style.cssText = 'margin-top:4px;position:relative;z-index:0;';
            const cd = document.createElement('div');
            cd.id = `chat-chart-${chartIndex}`;
            cd.style.cssText = 'width:100%;height:220px;';
            cw.appendChild(cd);
            const ab = document.createElement('button');
            ab.id = `add-chart-btn-${chartIndex}`;
            ab.textContent = '📊 添加到大屏';
            ab.style.cssText = 'margin-top:6px;padding:4px 12px;background:#4a9eff;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:11px;display:none;';
            ab.onclick = () => {
                const cd2 = autoDetectChartData(msg);
                if (cd2) { cd2.chartType = activeType; showAddChartConfirm(cd2); }
            };
            cw.appendChild(ab);
            const dlBtn = document.createElement('button');
            dlBtn.textContent = '⬇ 下载';
            dlBtn.style.cssText = 'margin-top:6px;margin-left:6px;padding:4px 12px;background:#fff;color:#4a9eff;border:1px solid #4a9eff;border-radius:4px;cursor:pointer;font-size:11px;';
            dlBtn.onclick = () => downloadChart(`chat-chart-${chartIndex}`, `chart_${Date.now()}.png`);
            cw.appendChild(dlBtn);
            chartSide.appendChild(cw);

            // Auto-render default chart (bar) and show add button
            setTimeout(() => { renderChatChart(chartIndex, 'bar', msg); const b = document.getElementById(`add-chart-btn-${chartIndex}`); if (b) b.style.display = ''; }, 50);
        }
        layoutDiv.appendChild(chartSide);
        content.appendChild(layoutDiv);
    }

    // Cache badge
    if (msg.cacheHit) {
        const badge = document.createElement('span');
        badge.style.cssText = 'display:inline-block;padding:2px 8px;border-radius:4px;background:#2ecc71;color:#fff;font-size:11px;margin-top:6px;';
        badge.textContent = '⚡ 缓存命中';
        content.appendChild(badge);
    }

    // Execution time
    if (msg.executionTime) {
        const timeEl = document.createElement('div');
        timeEl.style.cssText = 'color:#8892a8;font-size:12px;margin-top:4px;';
        timeEl.textContent = `⏱️ ${msg.executionTime}s`;
        content.appendChild(timeEl);
    }

    if (msg.role === 'user') {
        div.appendChild(content);
        div.appendChild(avatar);
    } else {
        div.appendChild(avatar);
        div.appendChild(content);
    }
    return div;
}

/**
 * Rendering chart in chat message
 */
function renderChatChart(index, type, msg) {
    const dom = document.getElementById(`chat-chart-${index}`);
    if (!dom || !msg.columns || !msg.result) return;
    try { const ex = echarts.getInstanceByDom(dom); if (ex) ex.dispose(); } catch(e) {}
    const cd = autoDetectChartData(msg);
    if (!cd) return;
    cd.chartType = type;
    const ed = (typeof chartDataToECharts === 'function') ? chartDataToECharts(type, cd) : { labels: cd.labels, values: cd.values };
    if (typeof renderChart === 'function') {
        renderChart(dom, type, ed);
    }
}

/**
 * 生成可折叠的思维链 HTML
 */
function buildCollapsibleCot(innerHtml, expanded = false) {
    const id = 'cot-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
    const display = expanded ? 'block' : 'none';
    const icon = expanded ? '▼' : '▶';
    const label = expanded ? '收起思维链' : '展开思维链';
    return `<div class="cot-wrapper">
        <button class="cot-toggle" onclick="toggleCot('${id}', this)">
            <span class="cot-icon">${icon}</span> ${label}
        </button>
        <div id="${id}" class="cot-content" style="display:${display}">
            ${innerHtml}
        </div>
    </div>`;
}

/**
 * 切换思维链展开/收起
 */
function toggleCot(id, btn) {
    const content = document.getElementById(id);
    if (!content) return;
    const isHidden = content.style.display === 'none';
    content.style.display = isHidden ? 'block' : 'none';
    btn.innerHTML = isHidden
        ? '<span class="cot-icon">▼</span> 收起思维链'
        : '<span class="cot-icon">▶</span> 展开思维链';
}

/**
 * 渲染消息内容（思维链默认折叠，SQL 始终可见）
 */
function renderMessageContent(text, msg) {
    // 有 SQL => 推理过程折叠，SQL 直接显示（即使 text 为空也显示占位）
    if (msg && msg.sql) {
        // 从原文中移除 SQL 代码块，只保留自然语言推理过程
        const cotText = text
            .replace(/\*\*错误\*\*.*/s, '')
            .replace(/```sql[\s\S]*?```/g, '')
            .replace(/```[\s\S]*?```/g, '')
            .replace(/SELECT\s.*?(?:LIMIT\s\d+)?;?/gis, '')
            .trim();
        const errorPart = text.includes('**错误**') ? text.substring(text.indexOf('**错误**')) : '';

        let html = '';
        // 推理文本可折叠（只包含自然语言分析，不含 SQL）
        if (cotText) {
            html += buildCollapsibleCot(`<p style="margin-bottom:8px;">${escapeHtml(cotText)}</p>`, false);
        } else {
            html += buildCollapsibleCot(`<p style="margin-bottom:4px;color:#8892a8;font-size:12px;">✅ 分析完成</p>`, false);
        }
        // SQL 始终可见
        html += `<pre style="background:#3d8cff;color:#ffffff;border-radius:6px;padding:12px;overflow-x:auto;border:none;margin-top:${cotText ? '8px' : '0'};"><code class="sql" style="color:#ffffff;background:transparent;">${escapeHtml(msg.sql)}</code></pre>`;
        if (errorPart) {
            html += `<p style="color:#e74c3c;margin-top:8px;">${escapeHtml(errorPart)}</p>`;
        }
        return html;
    }

    // Simple text
    return escapeHtml(text).replace(/\n/g, '<br>');
}

/**
 * 自动检测图表数据 — 从查询结果中提取 X/Y 列并推断图表类型
 */
function autoDetectChartData(msg) {
    if (!msg.columns || !msg.result || msg.result.length === 0) return null;
    let labelCol = null, valueCol = null;
    // 找到第一个文本列作为 X 轴，第一个数值列作为 Y 轴
    if (Array.isArray(msg.columns)) {
        for (let i = 0; i < msg.columns.length; i++) {
            const col = msg.columns[i];
            // 检测是否为数值（第一行数据的该列值）
            const sample = msg.result[0];
            const val = Array.isArray(sample) ? sample[i] : sample[col];
            if (val != null && !isNaN(parseFloat(val)) && isFinite(val)) {
                if (!valueCol) valueCol = { index: i, name: col };
            } else {
                if (!labelCol) labelCol = { index: i, name: col };
            }
        }
    }
    if (!labelCol || !valueCol) return null;
    // 提取数据
    const labels = msg.result.map(r => String(Array.isArray(r) ? r[labelCol.index] : r[labelCol.name]));
    const values = msg.result.map(r => parseFloat(Array.isArray(r) ? r[valueCol.index] : r[valueCol.name]));
    if (labels.length === 0 || values.length === 0) return null;
    // 去重 labels 并合并 values
    const unique = {};
    labels.forEach((l, i) => {
        if (!unique[l]) unique[l] = 0;
        unique[l] += values[i] || 0;
    });
    const dedupLabels = Object.keys(unique);
    const dedupValues = dedupLabels.map(l => unique[l]);
    // 推断图表类型
    const chartType = (typeof suggestChartType === 'function')
        ? suggestChartType(dedupLabels, dedupValues)
        : 'bar';
    return { labels: dedupLabels, values: dedupValues, chartType };
}

/**
 * 弹出确认对话框，询问用户是否将图表添加到数据大屏
 */
function showAddChartConfirm(chartData) {
    // 移除已存在的确认框
    const existing = document.querySelector('.chart-confirm-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.className = 'chart-confirm-overlay';
    overlay.innerHTML = `
        <div class="chart-confirm-box">
            <div class="chart-confirm-icon">📊</div>
            <div class="chart-confirm-text">查询结果图表已生成，是否添加到数据大屏？</div>
            <div class="chart-confirm-buttons">
                <button class="chart-confirm-btn btn-cancel">不添加</button>
                <button class="chart-confirm-btn btn-confirm">✅ 添加到大屏</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    // 点击"添加"按钮
    overlay.querySelector('.btn-confirm').addEventListener('click', () => {
        const title = `查询结果 (${chartData.chartType})`;
        addChatChartToDashboard({ ...chartData, title });
        overlay.remove();
    });

    // 点击"不添加"按钮或点击遮罩层外部关闭
    overlay.querySelector('.btn-cancel').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) overlay.remove();
    });
}

/**
 * 将聊天中的图表添加到数据大屏
 */
function addChatChartToDashboard(config) {
    try {
        const key = 'dashboard_chat_charts';
        const saved = JSON.parse(localStorage.getItem(key) || '[]');
        const id = Date.now();
        const curStyle = typeof getActiveStyle === 'function' ? getActiveStyle() : 'blue';
        saved.push({
            id,
            title: config.title || '来自查询的图表',
            chartType: config.chartType || 'bar',
            chartData: { labels: config.labels, values: config.values },
            chartStyle: curStyle,
            source: 'chat',
        });
        localStorage.setItem(key, JSON.stringify(saved));
        showToast('✅ 已添加到大屏！切换到「数据大屏」查看', 'success');
    } catch (e) {
        showToast('添加失败: ' + e.message, 'error');
    }
}

// Safe scroll to bottom
function scrollToBottom() {
    const c = document.getElementById('chat-messages');
    if (c) setTimeout(() => { c.scrollTop = c.scrollHeight; }, 10);
}
// Download chart as PNG
function downloadChart(domId, filename) {
    const dom = document.getElementById(domId);
    if (!dom) return;
    try {
        const inst = echarts.getInstanceByDom(dom);
        if (!inst) return;
        const url = inst.getDataURL({ type:'png', pixelRatio:2, backgroundColor:'#fff' });
        const a = document.createElement('a');
        a.href = url; a.download = filename || 'chart.png';
        document.body.appendChild(a); a.click(); a.remove();
    } catch(e) { showToast('下载失败', 'error'); }
}
// Export table as CSV
function exportTableCSV(columns, rows, filename) {
    if (!columns || !rows) return;
    const bom = '﻿';
    const header = columns.map(c => '"' + String(c).replace(/"/g,'""') + '"').join(',');
    const data = rows.map(r => columns.map(c => {
        const v = r[c] !== undefined ? r[c] : '';
        return '"' + String(v).replace(/"/g,'""') + '"';
    }).join(','));
    const csv = bom + header + '\n' + data.join('\n');
    const blob = new Blob([csv], { type:'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename || 'data.csv';
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
}
// Export table as XLS (HTML table format, opens in Excel)
function exportTableXLS(columns, rows, filename) {
    if (!columns || !rows) return;
    let html = '<html><meta charset="utf-8"><body><table>';
    html += '<tr>' + columns.map(c => '<th>' + String(c).replace(/</g,'&lt;') + '</th>').join('') + '</tr>';
    html += rows.map(r => '<tr>' + columns.map(c => {
        const v = r[c] !== undefined ? r[c] : '';
        return '<td>' + String(v).replace(/</g,'&lt;') + '</td>';
    }).join('') + '</tr>').join('');
    html += '</table></body></html>';
    const blob = new Blob([html], { type:'application/vnd.ms-excel;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename || 'data.xls';
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
}
// Toast notification
function showToast(msg, type) {
    const bg = type === 'error' ? '#e74c3c' : type === 'success' ? '#2ecc71' : '#4a9eff';
    const el = document.createElement('div');
    el.style.cssText = `position:fixed;top:20px;right:20px;padding:10px 20px;border-radius:8px;color:#fff;font-size:13px;z-index:9999;background:${bg};box-shadow:0 4px 12px rgba(0,0,0,0.15);`;
    el.textContent = msg; document.body.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 400); }, 3000);
}

// ============================================================================
// Stream status
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

// Safety: force reset streaming state at page load
isStreaming = false;
if (document.getElementById('send-btn')) document.getElementById('send-btn').disabled = false;
