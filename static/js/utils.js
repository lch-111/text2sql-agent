/**
 * ============================================================================
 * 工具函数
 * ============================================================================
 */

/**
 * 格式化大数字，如 1234567 → 123.5万
 */
function formatNumber(n) {
    if (n === null || n === undefined) return '0';
    n = Number(n);
    if (n >= 100000000) return (n / 100000000).toFixed(1) + '亿';
    if (n >= 10000) return (n / 10000).toFixed(1) + '万';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
    return n.toLocaleString();
}

/**
 * 格式化金额
 */
function formatCurrency(n) {
    if (n === null || n === undefined) return '¥0';
    n = Number(n);
    if (n >= 100000000) return '¥' + (n / 100000000).toFixed(2) + '亿';
    if (n >= 10000) return '¥' + (n / 10000).toFixed(2) + '万';
    return '¥' + n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/**
 * 转义 HTML 特殊字符
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * 防抖
 */
function debounce(fn, delay = 300) {
    let timer;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}

/**
 * 将行数据渲染为 HTML 表格
 */
function renderTable(columns, rows, maxRows = 50) {
    if (!columns || columns.length === 0) return '<p style="color: #999;">无数据</p>';

    let html = '<div class="table-wrapper"><table class="data-table"><thead><tr>';
    html += columns.map(c => `<th>${escapeHtml(c)}</th>`).join('');
    html += '</tr></thead><tbody>';

    if (rows && rows.length > 0) {
        const displayRows = rows.slice(0, maxRows);
        html += displayRows.map(row => {
            const cells = columns.map((col, i) => {
                let val = row[col] !== undefined ? row[col] : (Array.isArray(row) ? row[i] : '');
                if (val === null) val = '';
                if (typeof val === 'number') {
                    val = Number.isInteger(val) ? val.toLocaleString() : val.toFixed(2);
                }
                return `<td>${escapeHtml(String(val))}</td>`;
            }).join('');
            return `<tr>${cells}</tr>`;
        }).join('');
        if (rows.length > maxRows) {
            html += `<tr><td colspan="${columns.length}" style="color:#8892a8;text-align:center;font-size:12px;padding:8px;">仅显示前 ${maxRows} 条，共 ${rows.length} 条</td></tr>`;
        }
    } else {
        html += `<tr><td colspan="${columns.length}" style="color:#8892a8;text-align:center;padding:16px;font-size:13px;">查询已执行，没有匹配数据</td></tr>`;
    }

    html += '</tbody></table></div>';
    return html;
}

/**
 * Fetch JSON helper
 */
async function apiGet(path) {
    const res = await fetch(path);
    if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
    return res.json();
}

async function apiPost(path, body) {
    const res = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`POST ${path} failed: ${res.status}`);
    return res.json();
}
