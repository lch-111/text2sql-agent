/**
 * ============================================================================
 * 主应用逻辑 — 标签切换、图表构建器、状态管理
 * ============================================================================
 */

// 用户添加的图表
let userCharts = [];
let chartIdCounter = 0;
const CHART_STORAGE_KEY = 'dashboard_user_charts';
const CHAT_CHART_STORAGE_KEY = 'dashboard_chat_charts';

// 缓存的 ECharts 实例
let chartInstances = {};

/**
 * 应用初始化
 */
document.addEventListener('DOMContentLoaded', () => {
    initTabSwitching();
    initChat();
    initSidebarControls();
    initFileUpload();
    initSystemStatus();
    initDbConnection();
    initTableBrowser();
    initChartBuilder();
});

// ============================================================================
// 标签切换
// ============================================================================

function initTabSwitching() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const tabName = btn.dataset.tab;
            if (!tabName) return;

            // 更新标签状态
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            // 切换面板
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
            const pane = document.getElementById(`tab-${tabName}`);
            if (pane) {
                pane.classList.add('active');
                // 触发懒加载
                onTabActivated(tabName);
            }

            // ECharts 重新适配
            setTimeout(() => {
                Object.values(chartInstances).forEach(c => { if (c && c.resize) c.resize(); });
            }, 100);
        });
    });
}

function onTabActivated(tabName) {
    switch (tabName) {
        case 'dashboard':
            loadTableList();
            break;
        case 'tables':
            if (typeof window.loadTableBrowser === 'function') {
                window.loadTableBrowser();
            }
            break;
        case 'monitor':
            loadMonitorData();
            break;
        case 'eval':
            loadEvalData();
            break;
    }
}

// ============================================================================
// 图表构建器 — 用户自定义图表
// ============================================================================

function initChartBuilder() {
    const toggleBtn = document.getElementById('btn-toggle-builder');
    const form = document.getElementById('builder-form');
    const tableSelect = document.getElementById('builder-table');
    const xSelect = document.getElementById('builder-x-column');
    const ySelect = document.getElementById('builder-y-column');
    const addBtn = document.getElementById('btn-add-chart');

    if (!toggleBtn) return;

    // Toggle form
    toggleBtn.addEventListener('click', () => {
        const hidden = form.style.display === 'none';
        form.style.display = hidden ? 'block' : 'none';
        toggleBtn.textContent = hidden ? '− 收起' : '+ 添加图表';
        if (hidden) loadTableList();
    });

    // Table changed → load columns
    tableSelect.addEventListener('change', () => {
        const table = tableSelect.value;
        if (table) {
            loadColumnList(table);
        } else {
            xSelect.innerHTML = '<option value="">请先选择表</option>';
            ySelect.innerHTML = '<option value="">请先选择表</option>';
            xSelect.disabled = true;
            ySelect.disabled = true;
        }
    });

    // Auto-fill title from selection
    function autoFillTitle() {
        const table = tableSelect.value;
        const xCol = xSelect.value;
        const yCol = ySelect.value;
        const chartType = document.getElementById('builder-chart-type').value;
        const titleInput = document.getElementById('builder-title');
        if (!titleInput.value && table && xCol && yCol) {
            const typeNames = { bar: '柱状图', line: '折线图', pie: '饼图', scatter: '散点图', funnel: '漏斗图', treemap: '树图' };
            titleInput.value = `${table} - ${xCol} × ${yCol} (${typeNames[chartType] || chartType})`;
        }
    }
    xSelect.addEventListener('change', autoFillTitle);
    ySelect.addEventListener('change', autoFillTitle);

    // Add chart
    addBtn.addEventListener('click', addUserChart);

    // Load saved charts from localStorage
    loadSavedCharts();
    loadChatCharts();
}

async function loadTableList() {
    const select = document.getElementById('builder-table');
    const info = document.getElementById('builder-table-count');
    if (!select) return;

    try {
        const data = await apiGet('/api/dashboard/tables');
        const tables = data.tables || [];

        // Update count display
        if (info) {
            info.textContent = tables.length > 0 ? `${tables.length} 个表可用` : '';
        }

        // Preserve current selection
        const currentVal = select.value;
        select.innerHTML = '<option value="">-- 选择表 --</option>';
        tables.forEach(t => {
            const opt = document.createElement('option');
            opt.value = t;
            opt.textContent = t;
            select.appendChild(opt);
        });
        if (currentVal && tables.includes(currentVal)) {
            select.value = currentVal;
        }
    } catch (e) {
        select.innerHTML = '<option value="">加载失败，请检查数据库连接</option>';
        if (info) info.textContent = '未连接数据库';
    }
}

async function loadColumnList(table) {
    const xSelect = document.getElementById('builder-x-column');
    const ySelect = document.getElementById('builder-y-column');
    if (!xSelect || !ySelect) return;

    try {
        const data = await apiGet(`/api/dashboard/table-schema?table=${encodeURIComponent(table)}`);
        const columns = data.columns || [];

        xSelect.innerHTML = '<option value="">-- 选择列 --</option>';
        ySelect.innerHTML = '<option value="">-- 选择列 --</option>';

        columns.forEach(col => {
            const isNumeric = /int|float|double|decimal|number|real|numeric/i.test(col.type);

            // X column: all columns
            const xOpt = document.createElement('option');
            xOpt.value = col.name;
            xOpt.textContent = `${col.name} (${col.type})`;
            xSelect.appendChild(xOpt);

            // Y column: numeric only (for aggregation)
            if (isNumeric) {
                const yOpt = document.createElement('option');
                yOpt.value = col.name;
                yOpt.textContent = `${col.name} (${col.type})`;
                ySelect.appendChild(yOpt);
            }
        });

        xSelect.disabled = false;
        ySelect.disabled = false;
    } catch (e) {
        xSelect.innerHTML = '<option value="">加载失败</option>';
        ySelect.innerHTML = '<option value="">加载失败</option>';
    }
}

async function addUserChart() {
    const table = document.getElementById('builder-table').value;
    const xCol = document.getElementById('builder-x-column').value;
    const yCol = document.getElementById('builder-y-column').value;
    const chartType = document.getElementById('builder-chart-type').value;
    const chartStyle = document.getElementById('builder-chart-style')?.value || 'blue';
    let title = document.getElementById('builder-title').value.trim();

    if (!table || !xCol || !yCol) {
        showToast('请选择表、X轴和Y轴列', 'error');
        return;
    }

    if (!title) {
        const typeNames = { bar:'柱状图', line:'折线图', pie:'饼图', scatter:'散点图', funnel:'漏斗图', radar:'雷达图', treemap:'树图', heatmap:'热力图', sankey:'桑基图', gauge:'仪表盘', boxplot:'箱线图', parallel:'平行坐标', sunburst:'旭日图' };
        title = `${table} - ${xCol} × ${yCol} (${typeNames[chartType] || chartType})`;
    }

    if (typeof setChartStyle === 'function') setChartStyle(chartStyle);

    const addBtn = document.getElementById('btn-add-chart');
    addBtn.disabled = true;
    addBtn.textContent = '⏳ 加载中...';

    try {
        const data = await apiPost('/api/dashboard/chart-data', {
            table,
            x_column: xCol,
            y_column: yCol,
            chart_type: chartType,
            limit: 1000,
        });

        if (data.error) {
            showToast(data.error, 'error');
            return;
        }

        const id = ++chartIdCounter;
        const domId = `user-chart-${id}`;

        // Create chart container
        const grid = document.getElementById('dashboard-grid');
        const emptyState = document.getElementById('dashboard-empty');
        if (emptyState) emptyState.style.display = 'none';

        const chartBox = document.createElement('div');
        chartBox.className = 'user-chart-box';
        chartBox.id = `chart-box-${id}`;
        chartBox.innerHTML = `
            <div class="user-chart-header">
                <span class="user-chart-title">${escapeHtml(title)}</span>
                <div class="user-chart-meta">
                    <span class="user-chart-info">${escapeHtml(table)} · ${escapeHtml(xCol)} × ${escapeHtml(yCol)}</span>
                    <button class="user-chart-remove" data-id="${id}" title="移除图表">✕</button>
                    <button class="chart-download-btn" data-chart-id="${id}" title="下载图表">⬇</button>
                </div>
            </div>
            <div class="chart-container" id="${domId}"></div>
        `;
        grid.appendChild(chartBox);

        // Transform data for chart type
        const chartData = chartDataToECharts(chartType, data);
        if (chartData) {
            chartInstances[`user_${id}`] = renderChart(domId, chartType, chartData);
        }

        // Store config
        const chartConfig = { id, title, table, xColumn: xCol, yColumn: yCol, chartType, chartStyle };
        userCharts.push(chartConfig);
        saveCharts();

        // Wire remove button
        chartBox.querySelector('.user-chart-remove').addEventListener('click', () => removeUserChart(id));
        chartBox.querySelector('.chart-download-btn')?.addEventListener('click', () => downloadChart(`user-chart-${id}`, `chart_${id}.png`));

        // Reset form (keep table selected)
        document.getElementById('builder-x-column').value = '';
        document.getElementById('builder-y-column').value = '';
        document.getElementById('builder-title').value = '';
        document.getElementById('builder-chart-type').value = 'bar';

        showToast('图表添加成功', 'success');

    } catch (e) {
        showToast('图表加载失败: ' + e.message, 'error');
    } finally {
        addBtn.disabled = false;
        addBtn.textContent = '➕ 添加图表';
    }
}

function removeUserChart(id) {
    // Dispose chart instance
    if (chartInstances[`user_${id}`]) {
        chartInstances[`user_${id}`].dispose();
        delete chartInstances[`user_${id}`];
    }

    // Remove DOM
    const box = document.getElementById(`chart-box-${id}`);
    if (box) box.remove();

    // Remove from array
    userCharts = userCharts.filter(c => c.id !== id);
    saveCharts();

    // Show empty state if no charts left
    if (userCharts.length === 0) {
        const emptyState = document.getElementById('dashboard-empty');
        if (emptyState) emptyState.style.display = 'flex';
    }
}

function saveCharts() {
    try {
        const configs = userCharts.map(c => ({
            id: c.id, title: c.title, table: c.table,
            xColumn: c.xColumn, yColumn: c.yColumn, chartType: c.chartType,
            chartStyle: c.chartStyle || 'blue',
        }));
        localStorage.setItem(CHART_STORAGE_KEY, JSON.stringify(configs));
        // Also track highest id
        localStorage.setItem(CHART_STORAGE_KEY + '_counter', String(chartIdCounter));
    } catch (e) { /* localStorage full or unavailable */ }
}

function loadSavedCharts() {
    try {
        // Restore counter
        const savedCounter = localStorage.getItem(CHART_STORAGE_KEY + '_counter');
        if (savedCounter) chartIdCounter = parseInt(savedCounter, 10);

        // Restore configs
        const saved = localStorage.getItem(CHART_STORAGE_KEY);
        if (!saved) return;

        const configs = JSON.parse(saved);
        if (!configs.length) return;

        // Re-create each chart (will re-fetch data)
        configs.forEach(async (cfg) => {
            const id = cfg.id || ++chartIdCounter;
            try {
                const data = await apiPost('/api/dashboard/chart-data', {
                    table: cfg.table,
                    x_column: cfg.xColumn,
                    y_column: cfg.yColumn,
                    chart_type: cfg.chartType,
                    limit: 1000,
                });

                if (data.error) return;

                // Apply saved chart style
                if (cfg.chartStyle && typeof setChartStyle === 'function') setChartStyle(cfg.chartStyle);

                const domId = `user-chart-${id}`;
                const grid = document.getElementById('dashboard-grid');
                const emptyState = document.getElementById('dashboard-empty');
                if (emptyState) emptyState.style.display = 'none';

                const chartBox = document.createElement('div');
                chartBox.className = 'user-chart-box';
                chartBox.id = `chart-box-${id}`;
                chartBox.innerHTML = `
                    <div class="user-chart-header">
                        <span class="user-chart-title">${escapeHtml(cfg.title)}</span>
                        <div class="user-chart-meta">
                            <span class="user-chart-info">${escapeHtml(cfg.table)} · ${escapeHtml(cfg.xColumn)} × ${escapeHtml(cfg.yColumn)}</span>
                            <button class="user-chart-remove" data-id="${id}" title="移除图表">✕</button>
                    <button class="chart-download-btn" data-chart-id="${id}" title="下载图表">⬇</button>
                        </div>
                    </div>
                    <div class="chart-container" id="${domId}"></div>
                `;
                grid.appendChild(chartBox);

                const chartData = chartDataToECharts(cfg.chartType, data);
                if (chartData) {
                    chartInstances[`user_${id}`] = renderChart(domId, cfg.chartType, chartData);
                }

                userCharts.push({ id, ...cfg });
                chartBox.querySelector('.user-chart-remove').addEventListener('click', () => removeUserChart(id));
        chartBox.querySelector('.chart-download-btn')?.addEventListener('click', () => downloadChart(`user-chart-${id}`, `chart_${id}.png`));

            } catch (e) { /* skip failed charts on reload */ }
        });
    } catch (e) { /* ignore localStorage errors */ }
}

/**
 * 加载聊天中添加的图表（来自 localStorage 的静态数据）
 */
function loadChatCharts() {
    try {
        const saved = localStorage.getItem(CHAT_CHART_STORAGE_KEY);
        if (!saved) return;
        const configs = JSON.parse(saved);
        if (!configs.length) return;

        const grid = document.getElementById('dashboard-grid');
        const emptyState = document.getElementById('dashboard-empty');
        if (emptyState) emptyState.style.display = 'none';

        // Add section header
        const header = document.createElement('div');
        header.className = 'chat-charts-section-header';
        header.textContent = '📊 来自查询的图表';
        header.id = 'chat-charts-header';
        grid.appendChild(header);

        configs.forEach((cfg, idx) => {
            if (cfg.source !== 'chat') return;
            const id = cfg.id || Date.now() + idx;
            const domId = `chat-chart-dash-${id}`;

            const chartBox = document.createElement('div');
            chartBox.className = 'user-chart-box';
            chartBox.id = `chat-chart-box-${id}`;
            chartBox.innerHTML = `
                <div class="user-chart-header">
                    <span class="user-chart-title">${escapeHtml(cfg.title || '来自查询的图表')}</span>
                    <div class="user-chart-meta">
                        <span class="user-chart-info">💬 查询结果 · ${cfg.chartType}</span>
                        <button class="user-chart-remove" data-id="${id}" data-source="chat" title="移除图表">✕</button>
                    </div>
                </div>
                <div class="chart-container" id="${domId}"></div>
            `;
            grid.appendChild(chartBox);

            // Render chart from saved data
            const chartData = cfg.chartData || { labels: [], values: [] };
            if (cfg.chartStyle && typeof setChartStyle === 'function') setChartStyle(cfg.chartStyle);
            const echartFormatted = chartDataToECharts(cfg.chartType, chartData);
            if (echartFormatted) {
                chartInstances[`chat_${id}`] = renderChart(domId, cfg.chartType, echartFormatted);
            }

            chartBox.querySelector('.user-chart-remove').addEventListener('click', () => {
                removeChatChart(id);
            });
            chartBox.querySelector('.chart-download-btn')?.addEventListener('click', () => downloadChart(`chat-chart-${id}`, `chat_chart_${id}.png`));

            // Track separately
            if (!window._chatChartIds) window._chatChartIds = [];
            window._chatChartIds.push(id);
        });
    } catch (e) {
        console.warn('加载聊天图表失败:', e);
    }
}

function removeChatChart(id) {
    // Dispose chart instance
    if (chartInstances[`chat_${id}`]) {
        chartInstances[`chat_${id}`].dispose();
        delete chartInstances[`chat_${id}`];
    }
    // Remove DOM
    const box = document.getElementById(`chat-chart-box-${id}`);
    if (box) box.remove();
    // Remove from localStorage
    try {
        const saved = JSON.parse(localStorage.getItem(CHAT_CHART_STORAGE_KEY) || '[]');
        const filtered = saved.filter(c => c.id !== id);
        localStorage.setItem(CHAT_CHART_STORAGE_KEY, JSON.stringify(filtered));
        // Hide header if no more chat charts
        if (filtered.length === 0) {
            const header = document.getElementById('chat-charts-header');
            if (header) header.remove();
        }
    } catch (e) { /* ignore */ }
}

/**
 * Transform chart-data API response to ECharts-compatible format
 */
function chartDataToECharts(chartType, data) {
    if (!data || !data.labels || !data.values) return null;

    const { labels, values } = data;

    switch (chartType) {
        case 'bar':
        case 'line':
            return {
                labels,
                values,
                seriesNames: ['数值'],
                series: [{ name: '数值', data: values }],
            };

        case 'pie':
        case 'funnel':
            return { labels, values };

        case 'scatter': {
            const points = labels.map((x, i) => [
                typeof x === 'number' ? x : i,
                values[i] || 0,
            ]);
            return { points, symbolSize: 10 };
        }

        case 'treemap':
            return {
                children: labels.map((name, i) => ({
                    name: String(name),
                    value: values[i] || 0,
                })),
            };

        default:
            return { labels, values };
    }
}

// ============================================================================
// 监控数据
// ============================================================================

async function loadMonitorData() {
    try {
        const stats = await apiGet('/api/cache/stats');
        document.getElementById('monitor-hit-rate').textContent = (stats.hit_rate || 0).toFixed(1) + '%';
        document.getElementById('monitor-l1-rate').textContent = (stats.l1_hit_rate || 0).toFixed(1) + '%';
        document.getElementById('monitor-l2-rate').textContent = (stats.l2_hit_rate || 0).toFixed(1) + '%';
        document.getElementById('monitor-queries').textContent = stats.total_queries || 0;

        // Gauge chart
        if (chartInstances.cacheGauge) chartInstances.cacheGauge.dispose();
        chartInstances.cacheGauge = renderChart('chart-cache-gauge', 'gauge', {
            value: stats.hit_rate || 0,
            name: '缓存命中率',
            unit: '%',
            max: 100,
        });

    } catch (e) {
        document.querySelectorAll('#tab-monitor .metric-value').forEach(el => el.textContent = '—');
    }
}

// ============================================================================
// 评估数据
// ============================================================================

async function loadEvalData() {
    try {
        const report = await apiGet('/api/eval/report');
        if (report.error) {
            document.getElementById('eval-content').innerHTML =
                `<div class="empty-state"><p>${escapeHtml(report.error)}</p></div>`;
            return;
        }

        const metrics = report.overall_metrics || {};
        document.getElementById('eval-accuracy').textContent = (metrics.execution_accuracy || 0).toFixed(1) + '%';
        document.getElementById('eval-validity').textContent = (metrics.sql_syntax_validity || 0).toFixed(1) + '%';
        document.getElementById('eval-total').textContent = metrics.total_valid || 0;

        // Category bar chart
        const catMetrics = report.category_metrics || {};
        const catNames = Object.keys(catMetrics);
        const catValues = catNames.map(n => catMetrics[n].accuracy || 0);

        if (catNames.length > 0 && chartInstances.evalChart) chartInstances.evalChart.dispose();
        if (catNames.length > 0) {
            chartInstances.evalChart = renderChart('chart-eval-category', 'bar', {
                labels: catNames,
                values: catValues,
                seriesNames: ['准确率'],
                series: [{ name: '准确率', data: catValues }],
            }, {
                extraOptions: {
                    yAxis: { max: 100, splitLine: { lineStyle: { color: '#2d3650' } }, axisLabel: { color: '#8892a8', formatter: '{value}%' } },
                }
            });
        }
    } catch (e) {
        document.getElementById('eval-content').innerHTML =
            `<div class="empty-state"><p>评估报告加载失败</p></div>`;
    }
}

// ============================================================================
// 侧边栏控制
// ============================================================================

function initSidebarControls() {
    // 清空缓存
    const clearCacheBtn = document.getElementById('btn-clear-cache');
    if (clearCacheBtn) {
        clearCacheBtn.addEventListener('click', async () => {
            try {
                await apiPost('/api/cache/clear', {});
                showToast('缓存已清空', 'success');
            } catch (e) {
                showToast('清空失败', 'error');
            }
        });
    }

    // 重建索引
    const rebuildBtn = document.getElementById('btn-rebuild-index');
    if (rebuildBtn) {
        rebuildBtn.addEventListener('click', async () => {
            try {
                await apiPost('/api/vector-store/rebuild', {});
                showToast('向量索引重建完成', 'success');
            } catch (e) {
                showToast('重建失败', 'error');
            }
        });
    }
}

// ============================================================================
// 文件上传
// ============================================================================

function initFileUpload() {
    const uploadZone = document.getElementById('upload-zone');
    const fileInput = document.getElementById('file-input');
    if (!uploadZone || !fileInput) return;

    uploadZone.addEventListener('click', () => fileInput.click());

    uploadZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadZone.classList.add('dragover');
    });
    uploadZone.addEventListener('dragleave', () => {
        uploadZone.classList.remove('dragover');
    });
    uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadZone.classList.remove('dragover');
        const files = e.dataTransfer.files;
        if (files.length > 0) handleFileUpload(files[0]);
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length > 0) handleFileUpload(fileInput.files[0]);
    });

    const clearBtn = document.getElementById('btn-clear-file');
    if (clearBtn) {
        clearBtn.addEventListener('click', async () => {
            try {
                await apiDelete('/api/upload');
                document.getElementById('upload-info').innerHTML = '';
                document.getElementById('file-input').value = '';
                showToast('文件已清除', 'success');
            } catch (e) {
                showToast('清除失败', 'error');
            }
        });
    }
}

async function handleFileUpload(file) {
    const maxSize = 10 * 1024 * 1024; // 10MB
    if (file.size > maxSize) {
        showToast('文件大小不能超过 10MB', 'error');
        return;
    }

    const uploadInfo = document.getElementById('upload-info');
    uploadInfo.innerHTML = '<div class="stream-status"><span class="spinner"></span> 上传中...</div>';

    try {
        const formData = new FormData();
        formData.append('file', file);

        const res = await fetch('/api/upload', { method: 'POST', body: formData });
        if (!res.ok) throw new Error(`上传失败: ${res.status}`);

        const data = await res.json();

        let html = `<div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:var(--radius);padding:12px;margin-top:8px;">`;
        html += `<p style="color:var(--accent-green);font-weight:600;">✅ ${escapeHtml(data.filename)}</p>`;
        html += `<p style="font-size:12px;color:var(--text-secondary);">类型: ${data.file_type} | 行数: ${data.row_count}</p>`;
        html += `<div style="font-size:12px;color:var(--text-muted);margin-top:4px;">${escapeHtml(data.summary || '').replace(/\n/g, '<br>')}</div>`;
        html += '</div>';

        // Preview tables
        if (data.sheets) {
            for (const [name, sheet] of Object.entries(data.sheets)) {
                if (sheet.preview_rows && sheet.preview_rows.length > 0) {
                    const previewRows = sheet.preview_rows.map(row => {
                        const obj = {};
                        sheet.columns.forEach((col, i) => { obj[col] = row[i]; });
                        return obj;
                    });
                    html += `<div style="margin-top:8px;"><p style="font-size:12px;color:var(--text-secondary);font-weight:600;">📋 ${escapeHtml(name)} (前10行)</p>`;
                    html += renderTable(sheet.columns, previewRows, 10);
                    html += '</div>';
                }
            }
        }

        uploadInfo.innerHTML = html;
        showToast('文件解析成功', 'success');

    } catch (e) {
        uploadInfo.innerHTML = `<p style="color:var(--accent-red);font-size:13px;">❌ ${escapeHtml(e.message)}</p>`;
        showToast(e.message, 'error');
    }
}

// ============================================================================
// 系统状态
// ============================================================================

async function initSystemStatus() {
    try {
        const stats = await apiGet('/api/cache/stats');
        document.getElementById('status-cache').innerHTML =
            '<span class="status-dot green"></span> 运行中 ' + (stats.hit_rate || 0).toFixed(1) + '%';
    } catch (e) {
        document.getElementById('status-cache').innerHTML =
            '<span class="status-dot gray"></span> 未连接';
    }

    // DB status from /api/db/status
    try {
        const dbStatus = await apiGet('/api/db/status');
        if (dbStatus.connected) {
            const typeLabel = { mysql: 'MySQL', postgres: 'PostgreSQL', sqlite: 'SQLite' }[dbStatus.db_type] || dbStatus.db_type;
            document.getElementById('status-db').innerHTML =
                `<span class="status-dot green"></span> ${typeLabel} ${dbStatus.active_tables > 0 ? dbStatus.active_tables + '表' : '已连接'}`;
        } else {
            document.getElementById('status-db').innerHTML =
                '<span class="status-dot gray"></span> 未连接';
        }
    } catch (e) {
        document.getElementById('status-db').innerHTML =
            '<span class="status-dot gray"></span> 未连接';
    }
}

// ============================================================================
// 数据库连接 UI
// ============================================================================

function initDbConnection() {
    const dbTypeSelect = document.getElementById('db-type');
    const connFields = document.getElementById('db-connection-fields');
    const sqliteFields = document.getElementById('db-sqlite-fields');
    const testBtn = document.getElementById('btn-test-connection');
    const connectBtn = document.getElementById('btn-connect-db');
    const statusDiv = document.getElementById('db-connection-status');

    if (!dbTypeSelect) return;

    // 切换数据库类型 → 显示/隐藏对应字段
    function toggleDbFields() {
        const type = dbTypeSelect.value;
        if (type === 'sqlite') {
            connFields.style.display = 'none';
            sqliteFields.style.display = 'block';
        } else {
            connFields.style.display = 'block';
            sqliteFields.style.display = 'none';
            // 更新端口占位符
            const portInput = document.getElementById('db-port');
            if (type === 'mysql') {
                portInput.placeholder = '3306';
                if (portInput.value === '5432') portInput.value = '3306';
            } else {
                portInput.placeholder = '5432';
                if (portInput.value === '3306') portInput.value = '5432';
            }
        }
    }
    dbTypeSelect.addEventListener('change', toggleDbFields);

    // 获取连接参数
    function getConnectionParams() {
        const dbType = dbTypeSelect.value;
        if (dbType === 'sqlite') {
            return {
                db_type: 'sqlite',
                database: document.getElementById('db-sqlite-path').value || 'data/retail_warehouse.db',
            };
        }
        return {
            db_type: dbType,
            host: document.getElementById('db-host').value || 'localhost',
            port: parseInt(document.getElementById('db-port').value) || 3306,
            database: document.getElementById('db-database').value || '',
            user: document.getElementById('db-user').value || 'root',
            password: document.getElementById('db-password').value || '',
        };
    }

    // 显示连接状态
    function setStatus(type, message) {
        statusDiv.style.display = 'block';
        if (type === 'ok') {
            statusDiv.innerHTML = `<span class="status-ok">✅ ${escapeHtml(message)}</span>`;
        } else if (type === 'err') {
            statusDiv.innerHTML = `<span class="status-err">❌ ${escapeHtml(message)}</span>`;
        } else if (type === 'info') {
            statusDiv.innerHTML = `<span class="status-info">${escapeHtml(message)}</span>`;
        }
    }

    // 测试连接
    testBtn.addEventListener('click', async () => {
        const params = getConnectionParams();
        testBtn.disabled = true;
        testBtn.textContent = '⏳ 测试中...';
        setStatus('info', '正在测试连接...');

        try {
            const res = await fetch('/api/db/test-connection', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(params),
            });
            const data = await res.json();

            if (data.success) {
                const versionInfo = data.version ? ` (${data.version})` : '';
                setStatus('ok', `连接成功${versionInfo} — ${data.latency_ms}ms`);
            } else {
                setStatus('err', data.message || '连接失败');
            }
        } catch (e) {
            setStatus('err', '请求失败: ' + e.message);
        } finally {
            testBtn.disabled = false;
            testBtn.textContent = '🔌 测试连接';
        }
    });

    // 连接数据库
    connectBtn.addEventListener('click', async () => {
        const params = getConnectionParams();
        connectBtn.disabled = true;
        connectBtn.textContent = '⏳ 连接中...';
        setStatus('info', '正在连接数据库...');

        try {
            const res = await fetch('/api/db/connect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(params),
            });
            const data = await res.json();

            if (res.ok && data.success) {
                const tableInfo = data.tables && data.tables.length > 0
                    ? ` — ${data.tables.length} 个表: ${data.tables.join(', ')}`
                    : '';
                setStatus('ok', data.message + tableInfo);

                // 刷新系统状态和表格信息
                initSystemStatus();
                if (typeof window.loadTableBrowser === 'function') {
                    window.loadTableBrowser();
                }
                // 清空缓存的聊天消息提示（连接变了，旧数据可能不适用）
                showToast('数据库连接已切换', 'success');
            } else {
                const errMsg = data.detail || data.message || '连接失败';
                setStatus('err', errMsg);
                showToast(errMsg, 'error');
            }
        } catch (e) {
            setStatus('err', '请求失败: ' + e.message);
            showToast(e.message, 'error');
        } finally {
            connectBtn.disabled = false;
            connectBtn.textContent = '🔗 连接';
        }
    });

    // 初始化：加载当前连接状态
    (async function loadCurrentStatus() {
        try {
            const info = await apiGet('/api/db/status');
            if (info.connected) {
                setStatus('ok', `已连接 ${info.db_type} · ${info.active_tables || 0} 个表`);
                // 加载表格列表
                if (typeof window.loadTableBrowser === 'function') {
                    window.loadTableBrowser();
                }
                // 同步下拉框
                if (info.db_type && dbTypeSelect.value !== info.db_type) {
                    dbTypeSelect.value = info.db_type;
                    toggleDbFields();
                }
                // 同步字段值
                if (info.host) document.getElementById('db-host').value = info.host;
                if (info.port) document.getElementById('db-port').value = info.port;
                if (info.database) {
                    const dbField = info.db_type === 'sqlite' ? 'db-sqlite-path' : 'db-database';
                    document.getElementById(dbField).value = info.database;
                }
                if (info.user) document.getElementById('db-user').value = info.user;
            } else {
                setStatus('err', '数据库未连接');
            }
        } catch (e) {
            // 忽略首次加载错误
        }
    })();

    // 重置为默认连接
    const resetBtn = document.getElementById('btn-reset-db');
    if (resetBtn) {
        resetBtn.addEventListener('click', async () => {
            resetBtn.disabled = true;
            resetBtn.textContent = '⏳ 重置中...';
            try {
                const res = await fetch('/api/db/reset', { method: 'POST' });
                const data = await res.json();
                if (res.ok && data.success) {
                    const tableInfo = data.tables && data.tables.length > 0
                        ? ` — ${data.tables.length} 个表`
                        : '';
                    setStatus('ok', '已重置为默认连接' + tableInfo);
                    initSystemStatus();
                    if (typeof window.loadTableBrowser === 'function') window.loadTableBrowser();
                    showToast('已重置为默认数据库', 'success');
                    resetBtn.style.display = 'none';
                } else {
                    setStatus('err', data.detail || '重置失败');
                }
            } catch (e) {
                setStatus('err', '重置失败: ' + e.message);
            } finally {
                resetBtn.disabled = false;
                resetBtn.textContent = '↩️ 重置为默认连接';
            }
        });
    }
}


// ============================================================================
// 表格信息浏览 (Table Info Browser)
// ============================================================================

function initTableBrowser() {
    const section = document.getElementById('table-info-section');
    const tableList = document.getElementById('table-list');
    const loading = document.getElementById('table-list-loading');
    const detail = document.getElementById('table-detail');
    const detailName = document.getElementById('table-detail-name');
    const detailSchema = document.getElementById('table-detail-schema');
    const detailPreview = document.getElementById('table-detail-preview');
    const closeBtn = document.getElementById('btn-close-table-detail');

    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            detail.style.display = 'none';
        });
    }

    // Public: load table list from connected DB
    let _loadingBrowser = false;
    let _lastBrowserTables = null;
    window.loadTableBrowser = async function () {
        if (!section || _loadingBrowser) return;
        _loadingBrowser = true;
        try {
            console.log('[TableBrowser] Fetching DB status...');
            const status = await apiGet('/api/db/status');
            console.log('[TableBrowser] Status response:', status);
            if (!status.connected || !status.tables || status.tables.length === 0) {
                console.log('[TableBrowser] DB not connected or no tables found');
                loading.textContent = '请先连接数据库';
                loading.style.display = 'block';
                tableList.style.display = 'none';
                detail.style.display = 'none';
                return;
            }
            console.log('[TableBrowser] Tables found:', status.tables);

            // Skip if tables haven't changed
            const joined = status.tables.join(',');
            if (_lastBrowserTables === joined && tableList.children.length > 0) {
                _loadingBrowser = false;
                return;
            }
            _lastBrowserTables = joined;

            loading.textContent = '加载中...';
            loading.style.display = 'block';
            tableList.style.display = 'none';

            const tables = status.tables || [];
            tableList.innerHTML = '';
            tables.forEach(t => {
                const btn = document.createElement('button');
                btn.className = 'table-info-btn';
                btn.innerHTML = `
                    <span class="table-icon">📄</span>
                    <span class="table-name">${escapeHtml(t)}</span>
                    <span class="table-arrow">▸</span>
                `;
                btn.addEventListener('click', () => loadTableDetail(t));
                tableList.appendChild(btn);
            });

            loading.style.display = 'none';
            tableList.style.display = 'block';
        } catch (e) {
            loading.textContent = '加载失败: ' + e.message;
            loading.style.display = 'block';
            tableList.style.display = 'none';
        } finally {
            _loadingBrowser = false;
        }
    };

    async function loadTableDetail(tableName) {
        if (!detail) return;
        console.log('[TableBrowser] Loading detail for table:', tableName);
        detailName.textContent = `📄 ${tableName}`;
        detailSchema.innerHTML = '<div style="color:var(--text-muted);font-size:12px;">加载 schema 中...</div>';
        detailPreview.innerHTML = '';
        detail.style.display = 'block';

        try {
            // Load schema
            const schemaData = await apiGet(`/api/dashboard/table-schema?table=${encodeURIComponent(tableName)}`);
            console.log('[TableBrowser] Schema response:', schemaData);
            const cols = schemaData.columns || [];
            let schemaHtml = '<table><thead><tr><th>列名</th><th>类型</th></tr></thead><tbody>';
            cols.forEach(c => {
                schemaHtml += `<tr><td>${escapeHtml(c.name)}</td><td>${escapeHtml(c.type)}</td></tr>`;
            });
            schemaHtml += '</tbody></table>';
            detailSchema.innerHTML = schemaHtml;

            // Load preview data
            const previewData = await apiGet(`/api/dashboard/table-preview?table=${encodeURIComponent(tableName)}`);
            console.log('[TableBrowser] Preview response:', previewData);
            const rows = previewData.rows || [];
            const columns = previewData.columns || [];
            if (rows.length > 0) {
                let previewHtml = '<div style="color:var(--text-muted);font-size:11px;margin-bottom:4px;">预览 (前 ' + rows.length + ' 行):</div>';
                previewHtml += '<table><thead><tr>';
                columns.forEach(c => { previewHtml += `<th>${escapeHtml(c)}</th>`; });
                previewHtml += '</tr></thead><tbody>';
                rows.forEach(row => {
                    previewHtml += '<tr>';
                    row.forEach(cell => { previewHtml += `<td>${escapeHtml(String(cell ?? ''))}</td>`; });
                    previewHtml += '</tr>';
                });
                previewHtml += '</tbody></table>';
                detailPreview.innerHTML = previewHtml;
            } else {
                detailPreview.innerHTML = '<div style="color:var(--text-muted);font-size:12px;">空表，无数据预览</div>';
            }
        } catch (e) {
            console.error('[TableBrowser] Failed to load table detail:', e);
            console.error('[TableBrowser] Stack:', e.stack);
            const isConnLost = e.message.includes('400') && e.message.includes('不存在');
            if (isConnLost) {
                detailSchema.innerHTML = `<div style="color:var(--accent-red);font-size:13px;line-height:1.6;">
                    <p>⚠️ 表数据加载失败</p>
                    <p style="margin-top:4px;font-size:12px;">可能是数据库连接已断开，请重新连接数据库后重试。</p>
                </div>`;
            } else {
                detailSchema.innerHTML = `<div style="color:var(--accent-red);font-size:12px;">加载失败: ${escapeHtml(e.message)}</div>`;
            }
            detailPreview.innerHTML = '';
        }
    }
}

// ============================================================================
// Helper: DELETE request
// ============================================================================

async function apiDelete(path) {
    const res = await fetch(path, { method: 'DELETE' });
    if (!res.ok) throw new Error(`DELETE ${path} failed: ${res.status}`);
    return res.json();
}
