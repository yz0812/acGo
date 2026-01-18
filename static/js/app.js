// 全局变量
let currentPage = 1;
let totalPages = 1;

// HTML 转义函数（防止 XSS）
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML.replace(/'/g, '&#39;').replace(/"/g, '&quot;');
}

// 格式化响应码（带样式徽章）
function formatResponseCode(code) {
    if (!code) {
        return '<span class="badge badge-code-none">-</span>';
    }
    const codeNum = parseInt(code);
    let badgeClass = 'badge-code-none';
    if (codeNum >= 200 && codeNum < 300) {
        badgeClass = 'badge-code-2xx';
    } else if (codeNum >= 300 && codeNum < 400) {
        badgeClass = 'badge-code-3xx';
    } else if (codeNum >= 400 && codeNum < 500) {
        badgeClass = 'badge-code-4xx';
    } else if (codeNum >= 500) {
        badgeClass = 'badge-code-5xx';
    }
    return `<span class="badge ${badgeClass}">${code}</span>`;
}

// 显示响应详情
function showResponseDetail(element) {
    const content = element.getAttribute('data-content');
    // 解码 HTML 实体
    const textarea = document.createElement('textarea');
    textarea.innerHTML = content;
    const decoded = textarea.value;

    document.getElementById('responseContent').textContent = decoded;
    document.getElementById('responseModal').style.display = 'block';
    document.body.style.overflow = 'hidden';  // 禁止背景滚动
}

// 关闭响应详情模态框
function closeResponseModal() {
    document.getElementById('responseModal').style.display = 'none';
    document.body.style.overflow = '';  // 恢复滚动
}

// 显示系统设置模态框
function showSystemSettingsModal() {
    loadSystemConfig();
    document.getElementById('systemSettingsModal').style.display = 'block';
    document.body.style.overflow = 'hidden';  // 禁止背景滚动
}

// 关闭系统设置模态框
function closeSystemSettingsModal() {
    document.getElementById('systemSettingsModal').style.display = 'none';
    document.body.style.overflow = '';  // 恢复滚动
    // 清空密码字段
    document.getElementById('oldPassword').value = '';
    document.getElementById('newPassword').value = '';
    document.getElementById('confirmPassword').value = '';
}

// 页面加载时初始化
document.addEventListener('DOMContentLoaded', function() {
    loadStats();
    loadAccounts();
    loadLogsPage(1);
    loadWebhookConfig();
    loadNotifyChannels();
});

// 加载统计数据
async function loadStats() {
    try {
        const res = await fetch('/api/stats');
        const data = await res.json();

        if (data.success) {
            document.getElementById('totalAccounts').textContent = data.data.total_accounts;
            document.getElementById('enabledAccounts').textContent = data.data.enabled_accounts;
            document.getElementById('totalLogs').textContent = data.data.total_logs;
            document.getElementById('successLogs').textContent = data.data.success_logs;
        }
    } catch (error) {
        console.error('加载统计失败:', error);
    }
}

// 加载账号列表
async function loadAccounts() {
    try {
        const res = await fetch('/api/accounts');
        const data = await res.json();

        if (data.success) {
            const tbody = document.getElementById('accountsBody');

            if (data.data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;">暂无账号</td></tr>';
                return;
            }

            tbody.innerHTML = data.data.map(acc => `
                <tr>
                    <td>${acc.id}</td>
                    <td>${acc.name}</td>
                    <td>${acc.cron_expr}</td>
                    <td>${acc.retry_count}</td>
                    <td>${acc.retry_interval}</td>
                    <td>
                        <span class="badge ${acc.enabled ? 'badge-success' : 'badge-danger'}">
                            ${acc.enabled ? '启用' : '禁用'}
                        </span>
                    </td>
                    <td>${acc.created_at}</td>
                    <td>
                        <button onclick="showRequestPreview(${acc.id})" class="btn btn-sm btn-secondary">查看详情</button>
                        <button onclick="manualCheckin(${acc.id})" class="btn btn-sm btn-success">立即签到</button>
                        <button onclick="editAccount(${acc.id})" class="btn btn-sm btn-primary">编辑</button>
                        <button onclick="deleteAccount(${acc.id})" class="btn btn-sm btn-danger">删除</button>
                    </td>
                </tr>
            `).join('');
        }
    } catch (error) {
        console.error('加载账号失败:', error);
    }
}

// 加载签到记录（分页）
async function loadLogsPage(page) {
    if (page < 1) return;

    try {
        const statusFilter = document.getElementById('statusFilter').value;
        const url = `/api/logs?page=${page}&page_size=10${statusFilter ? '&status=' + statusFilter : ''}`;
        const res = await fetch(url);
        const data = await res.json();

        if (data.success) {
            const tbody = document.getElementById('logsBody');

            if (data.data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;">暂无记录</td></tr>';
                document.getElementById('logsPagination').style.display = 'none';
                return;
            }

            tbody.innerHTML = data.data.map(log => `
                <tr>
                    <td>${log.id}</td>
                    <td>${log.account_name}</td>
                    <td>
                        <span class="badge ${log.status === 'success' ? 'badge-success' : 'badge-danger'}">
                            ${log.status === 'success' ? '成功' : '失败'}
                        </span>
                    </td>
                    <td>${formatResponseCode(log.response_code)}</td>
                    <td>
                        ${log.response_body ?
                            `<span class="clickable" data-content="${escapeHtml(log.response_body)}" onclick="showResponseDetail(this)" title="点击查看完整内容">${escapeHtml(log.response_body.substring(0, 50))}...</span>`
                            : '-'}
                    </td>
                    <td>
                        ${log.error_message ?
                            `<span class="clickable" data-content="${escapeHtml(log.error_message)}" onclick="showResponseDetail(this)" title="点击查看完整内容">${escapeHtml(log.error_message.substring(0, 30))}...</span>`
                            : '-'}
                    </td>
                    <td>${log.executed_at}</td>
                    <td>
                        <button onclick="showLogRequestPreview(${log.id})" class="btn btn-sm btn-secondary">查看详情</button>
                    </td>
                </tr>
            `).join('');

            // 更新分页信息
            currentPage = page;
            totalPages = Math.ceil(data.total / data.page_size);

            document.getElementById('pageInfo').textContent = `第 ${currentPage} 页 / 共 ${totalPages} 页 (总计 ${data.total} 条)`;
            document.getElementById('prevBtn').disabled = currentPage <= 1;
            document.getElementById('nextBtn').disabled = currentPage >= totalPages;
            document.getElementById('logsPagination').style.display = 'flex';
        }
    } catch (error) {
        console.error('加载日志失败:', error);
    }
}

// 状态筛选
function filterLogsByStatus() {
    loadLogsPage(1);  // 筛选后重置到第一页
}

// 兼容旧的 loadLogs 函数
function loadLogs() {
    loadLogsPage(1);
}

// 显示添加模态框
function showAddModal() {
    document.getElementById('modalTitle').textContent = '添加账号';
    document.getElementById('accountForm').reset();
    document.getElementById('accountId').value = '';
    document.getElementById('accountModal').style.display = 'block';
    document.body.style.overflow = 'hidden';  // 禁止背景滚动
}

// 编辑账号
async function editAccount(id) {
    try {
        const res = await fetch('/api/accounts');
        const data = await res.json();

        if (data.success) {
            const account = data.data.find(acc => acc.id === id);

            if (account) {
                document.getElementById('modalTitle').textContent = '编辑账号';
                document.getElementById('accountId').value = account.id;
                document.getElementById('accountName').value = account.name;
                document.getElementById('curlCommand').value = account.curl_command;
                document.getElementById('cronExpr').value = account.cron_expr;
                document.getElementById('retryCount').value = account.retry_count;
                document.getElementById('retryInterval').value = account.retry_interval;
                document.getElementById('enabled').checked = account.enabled;
                document.getElementById('accountModal').style.display = 'block';
                document.body.style.overflow = 'hidden';  // 禁止背景滚动
            }
        }
    } catch (error) {
        console.error('加载账号失败:', error);
    }
}

// 关闭模态框
function closeModal() {
    document.getElementById('accountModal').style.display = 'none';
    document.body.style.overflow = '';  // 恢复滚动
}

// 提交表单
document.getElementById('accountForm').addEventListener('submit', async function(e) {
    e.preventDefault();

    const id = document.getElementById('accountId').value;
    const formData = {
        name: document.getElementById('accountName').value,
        curl_command: document.getElementById('curlCommand').value,
        cron_expr: document.getElementById('cronExpr').value,
        retry_count: parseInt(document.getElementById('retryCount').value),
        retry_interval: parseInt(document.getElementById('retryInterval').value),
        enabled: document.getElementById('enabled').checked
    };

    try {
        const url = id ? `/api/accounts/${id}` : '/api/accounts';
        const method = id ? 'PUT' : 'POST';

        const res = await fetch(url, {
            method: method,
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(formData)
        });

        const data = await res.json();

        if (data.success) {
            alert(data.message);
            closeModal();
            loadAccounts();
            loadStats();
        } else {
            alert('操作失败: ' + data.message);
        }
    } catch (error) {
        alert('操作失败: ' + error.message);
    }
});

// 删除账号
async function deleteAccount(id) {
    if (!confirm('确定要删除这个账号吗？')) return;

    try {
        const res = await fetch(`/api/accounts/${id}`, {method: 'DELETE'});
        const data = await res.json();

        if (data.success) {
            alert(data.message);
            loadAccounts();
            loadStats();
        } else {
            alert('删除失败: ' + data.message);
        }
    } catch (error) {
        alert('删除失败: ' + error.message);
    }
}

// 手动签到
async function manualCheckin(id) {
    if (!confirm('确定要立即执行签到吗？')) return;

    try {
        const res = await fetch(`/api/checkin/${id}`, {method: 'POST'});
        const data = await res.json();

        alert(data.message);

        if (data.success) {
            loadLogs();
            loadStats();
        }
    } catch (error) {
        alert('签到失败: ' + error.message);
    }
}

// 清除7天前的日志
async function clearOldLogs() {
    if (!confirm('确定要清除7天前的签到记录吗？\n\n此操作不可恢复！')) return;

    try {
        const res = await fetch('/api/logs/clear?days=7', {method: 'DELETE'});
        const data = await res.json();

        alert(data.message);

        if (data.success) {
            loadLogs();
            loadStats();
        }
    } catch (error) {
        alert('清除失败: ' + error.message);
    }
}

// 清除全部日志
async function clearAllLogs() {
    if (!confirm('⚠️ 警告：确定要清除全部签到记录吗？\n\n此操作不可恢复！')) return;

    try {
        const res = await fetch('/api/logs/clear', {method: 'DELETE'});
        const data = await res.json();

        alert(data.message);

        if (data.success) {
            loadLogs();
            loadStats();
        }
    } catch (error) {
        alert('清除失败: ' + error.message);
    }
}

// 点击模态框外部关闭
window.onclick = function(event) {
    const accountModal = document.getElementById('accountModal');
    const responseModal = document.getElementById('responseModal');
    const systemSettingsModal = document.getElementById('systemSettingsModal');
    const requestPreviewModal = document.getElementById('requestPreviewModal');
    const appSelectionModal = document.getElementById('appSelectionModal');
    const customAccountModal = document.getElementById('customAccountModal');

    if (event.target === accountModal) {
        closeModal();
    }
    if (event.target === responseModal) {
        closeResponseModal();
    }
    if (event.target === systemSettingsModal) {
        closeSystemSettingsModal();
    }
    if (event.target === requestPreviewModal) {
        closeRequestPreviewModal();
    }
    if (event.target === appSelectionModal) {
        closeAppSelectionModal();
    }
    if (event.target === customAccountModal) {
        closeCustomAccountModal();
    }
}

// 加载 Webhook 配置
async function loadWebhookConfig() {
    try {
        const res = await fetch('/api/webhook/config');
        const data = await res.json();

        if (data.success) {
            document.getElementById('webhookEnabled').checked = data.data.enabled || false;
            document.getElementById('webhookIncludeResponse').checked = data.data.include_response || false;
            document.getElementById('webhookUrl').value = data.data.url || '';
            document.getElementById('webhookMethod').value = data.data.method || 'POST';
            document.getElementById('webhookHeaders').value = data.data.headers || '';
        }
    } catch (error) {
        console.error('加载 Webhook 配置失败:', error);
    }
}

// 保存 Webhook 配置
async function saveWebhook() {
    const result = await saveWebhookQuiet();
    if (result.success) {
        alert('Webhook 配置保存成功');
    } else {
        alert('保存失败: ' + result.message);
    }
}

// 静默保存 Webhook 配置（不弹出提示）
async function saveWebhookQuiet() {
    const config = {
        enabled: document.getElementById('webhookEnabled').checked,
        include_response: document.getElementById('webhookIncludeResponse').checked,
        url: document.getElementById('webhookUrl').value,
        method: document.getElementById('webhookMethod').value,
        headers: document.getElementById('webhookHeaders').value
    };

    // 验证 Headers JSON 格式
    if (config.headers) {
        try {
            JSON.parse(config.headers);
        } catch (e) {
            return {success: false, message: '自定义请求头格式错误，请输入有效的 JSON'};
        }
    }

    try {
        const res = await fetch('/api/webhook/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(config)
        });

        const data = await res.json();
        return {success: data.success, message: data.message || ''};
    } catch (error) {
        return {success: false, message: error.message};
    }
}

// 测试 Webhook
async function testWebhook() {
    const url = document.getElementById('webhookUrl').value;

    if (!url) {
        alert('请先输入 Webhook URL');
        return;
    }

    if (!confirm('确定要发送测试通知到 Webhook 吗？')) return;

    try {
        const res = await fetch('/api/webhook/test', {method: 'POST'});
        const data = await res.json();

        if (data.success) {
            alert('测试通知发送成功！\n\n请检查 Webhook 接收端是否收到测试数据。');
        } else {
            alert('测试失败: ' + data.message);
        }
    } catch (error) {
        alert('测试失败: ' + error.message);
    }
}

// 导出账号
async function exportAccounts() {
    try {
        const res = await fetch('/api/accounts/export');
        const data = await res.json();

        if (data.success) {
            // 创建 Blob 并下载
            const blob = new Blob([JSON.stringify(data.data, null, 2)], {type: 'application/json'});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `acgo_accounts_${new Date().toISOString().split('T')[0]}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            alert(`成功导出 ${data.data.length} 个账号`);
        } else {
            alert('导出失败: ' + data.message);
        }
    } catch (error) {
        alert('导出失败: ' + error.message);
    }
}

// 导入账号
async function importAccounts(event) {
    const file = event.target.files[0];
    if (!file) return;

    // 验证文件类型
    if (!file.name.endsWith('.json')) {
        alert('请选择 JSON 文件');
        event.target.value = '';
        return;
    }

    try {
        const text = await file.text();
        const accounts = JSON.parse(text);

        // 验证数据格式
        if (!Array.isArray(accounts)) {
            alert('JSON 格式错误：根元素必须是数组');
            event.target.value = '';
            return;
        }

        if (accounts.length === 0) {
            alert('文件中没有账号数据');
            event.target.value = '';
            return;
        }

        // 确认导入
        if (!confirm(`确定要导入 ${accounts.length} 个账号吗？\n\n重名账号将自动重命名。`)) {
            event.target.value = '';
            return;
        }

        // 发送导入请求
        const res = await fetch('/api/accounts/import', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({accounts: accounts})
        });

        const data = await res.json();

        if (data.success) {
            alert(`导入完成！\n\n成功: ${data.imported} 个\n失败: ${data.failed} 个\n重命名: ${data.renamed} 个`);
            loadAccounts();
            loadStats();
        } else {
            alert('导入失败: ' + data.message);
        }
    } catch (error) {
        if (error instanceof SyntaxError) {
            alert('JSON 格式错误: ' + error.message);
        } else {
            alert('导入失败: ' + error.message);
        }
    } finally {
        // 清空文件选择，允许重复导入同一文件
        event.target.value = '';
    }
}
// 加载系统配置
async function loadSystemConfig() {
    try {
        const res = await fetch('/api/system/config');
        const data = await res.json();

        if (data.success) {
            document.getElementById('autoCleanLogs').checked = data.data.auto_clean_logs || false;
            document.getElementById('maxLogsCount').value = data.data.max_logs_count || 500;
        }
    } catch (error) {
        console.error('加载系统配置失败:', error);
    }
}

// 保存系统配置
async function saveSystemConfig() {
    const config = {
        auto_clean_logs: document.getElementById('autoCleanLogs').checked,
        max_logs_count: parseInt(document.getElementById('maxLogsCount').value)
    };

    // 验证最大记录数
    if (config.max_logs_count < 100) {
        alert('最大记录数不能小于 100');
        return;
    }

    try {
        const res = await fetch('/api/system/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(config)
        });

        const data = await res.json();

        if (data.success) {
            alert('系统配置保存成功');
            closeSystemSettingsModal();
        } else {
            alert('保存失败: ' + data.message);
        }
    } catch (error) {
        alert('保存失败: ' + error.message);
    }
}

// 修改密码
async function changePassword() {
    const oldPassword = document.getElementById('oldPassword').value;
    const newPassword = document.getElementById('newPassword').value;
    const confirmPassword = document.getElementById('confirmPassword').value;

    // 验证输入
    if (!oldPassword || !newPassword || !confirmPassword) {
        alert('请填写所有密码字段');
        return;
    }

    if (newPassword.length < 6) {
        alert('新密码长度不能少于 6 位');
        return;
    }

    if (newPassword !== confirmPassword) {
        alert('两次输入的新密码不一致');
        return;
    }

    if (!confirm('确定要修改管理员密码吗？修改后需要重新登录。')) {
        return;
    }

    try {
        const res = await fetch('/api/system/password', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                old_password: oldPassword,
                new_password: newPassword
            })
        });

        const data = await res.json();

        if (data.success) {
            alert(data.message);
            // 清空密码字段
            document.getElementById('oldPassword').value = '';
            document.getElementById('newPassword').value = '';
            document.getElementById('confirmPassword').value = '';
            // 跳转到登录页
            window.location.href = '/logout';
        } else {
            alert('修改失败: ' + data.message);
        }
    } catch (error) {
        alert('修改失败: ' + error.message);
    }
}

// 显示请求预览
async function showRequestPreview(accountId) {
    try {
        const res = await fetch(`/api/accounts/${accountId}/preview`);
        const data = await res.json();

        if (data.success) {
            const preview = data.data;

            // 填充请求方式
            document.getElementById('previewMethod').textContent = preview.method;

            // 填充请求地址
            document.getElementById('previewUrl').textContent = preview.url;

            // 填充请求头（包含 Cookies）
            const headersEl = document.getElementById('previewHeaders');
            let headersText = '';

            // 先添加普通请求头
            if (preview.headers && Object.keys(preview.headers).length > 0) {
                for (const [key, value] of Object.entries(preview.headers)) {
                    headersText += `${key}: ${value}\n`;
                }
            }

            // 将 Cookies 转换为 Cookie 请求头并添加
            if (preview.cookies && Object.keys(preview.cookies).length > 0) {
                const cookieHeader = Object.entries(preview.cookies)
                    .map(([k, v]) => `${k}=${v}`)
                    .join('; ');
                headersText += `Cookie: ${cookieHeader}\n`;
            }

            headersEl.textContent = headersText.trim() || '无';

            // 填充请求体
            const dataEl = document.getElementById('previewData');
            if (preview.data) {
                // 尝试格式化 JSON
                try {
                    const jsonData = JSON.parse(preview.data);
                    dataEl.textContent = JSON.stringify(jsonData, null, 2);
                } catch (e) {
                    // 不是 JSON，直接显示
                    dataEl.textContent = preview.data;
                }
            } else {
                dataEl.textContent = '无';
            }

            // 显示模态框
            document.getElementById('requestPreviewModal').style.display = 'block';
            document.body.style.overflow = 'hidden';  // 禁止背景滚动
        } else {
            alert('获取请求详情失败: ' + data.message);
        }
    } catch (error) {
        alert('获取请求详情失败: ' + error.message);
    }
}

// 关闭请求预览模态框
function closeRequestPreviewModal() {
    document.getElementById('requestPreviewModal').style.display = 'none';
    document.body.style.overflow = '';  // 恢复滚动
}

// 显示日志请求预览
async function showLogRequestPreview(logId) {
    try {
        const res = await fetch(`/api/logs/${logId}/preview`);
        const data = await res.json();

        if (data.success) {
            const preview = data.data;

            // 填充请求方式
            document.getElementById('previewMethod').textContent = preview.method || '未记录';

            // 填充请求地址
            document.getElementById('previewUrl').textContent = preview.url || '未记录';

            // 填充请求头（包含 Cookies）
            const headersEl = document.getElementById('previewHeaders');
            let headersText = '';

            // 先添加普通请求头
            if (preview.headers && Object.keys(preview.headers).length > 0) {
                for (const [key, value] of Object.entries(preview.headers)) {
                    headersText += `${key}: ${value}\n`;
                }
            }

            // 将 Cookies 转换为 Cookie 请求头并添加
            if (preview.cookies && Object.keys(preview.cookies).length > 0) {
                const cookieHeader = Object.entries(preview.cookies)
                    .map(([k, v]) => `${k}=${v}`)
                    .join('; ');
                headersText += `Cookie: ${cookieHeader}\n`;
            }

            headersEl.textContent = headersText.trim() || '无';

            // 填充请求体
            const dataEl = document.getElementById('previewData');
            if (preview.data) {
                // 尝试格式化 JSON
                try {
                    const jsonData = JSON.parse(preview.data);
                    dataEl.textContent = JSON.stringify(jsonData, null, 2);
                } catch (e) {
                    // 不是 JSON，直接显示
                    dataEl.textContent = preview.data;
                }
            } else {
                dataEl.textContent = '无';
            }

            // 显示模态框
            document.getElementById('requestPreviewModal').style.display = 'block';
            document.body.style.overflow = 'hidden';  // 禁止背景滚动
        } else {
            alert('获取请求详情失败: ' + data.message);
        }
    } catch (error) {
        alert('获取请求详情失败: ' + error.message);
    }
}

// 显示应用选择模态框
function showAppSelectionModal() {
    document.getElementById('appSelectionModal').style.display = 'block';
    document.body.style.overflow = 'hidden';  // 禁止背景滚动
}

// 关闭应用选择模态框
function closeAppSelectionModal() {
    document.getElementById('appSelectionModal').style.display = 'none';
    document.body.style.overflow = '';  // 恢复滚动
}

// 显示自定义账号模态框
function showCustomAccountModal(appType) {
    // 关闭应用选择模态框
    closeAppSelectionModal();

    // 直接打开通用的账号添加框
    showAddModal();
}

// 关闭自定义账号模态框
function closeCustomAccountModal() {
    document.getElementById('customAccountModal').style.display = 'none';
    document.body.style.overflow = '';  // 恢复滚动
}

// ==================== 推送通知渠道 ====================

// 加载通知渠道配置
async function loadNotifyChannels() {
    try {
        const res = await fetch('/api/notify/config');
        const data = await res.json();

        if (data.success) {
            const cfg = data.data;

            // Telegram
            document.getElementById('telegramEnabled').checked = cfg.telegram_enabled || false;
            document.getElementById('telegramBotToken').value = cfg.telegram_bot_token || '';
            document.getElementById('telegramUserId').value = cfg.telegram_user_id || '';
            document.getElementById('telegramApiUrl').value = cfg.telegram_api_url || '';

            // 企业微信
            document.getElementById('wecomEnabled').checked = cfg.wecom_enabled || false;
            document.getElementById('wecomWebhookKey').value = cfg.wecom_webhook_key || '';
            document.getElementById('wecomApiUrl').value = cfg.wecom_api_url || '';

            // 钉钉
            document.getElementById('dingtalkEnabled').checked = cfg.dingtalk_enabled || false;
            document.getElementById('dingtalkAccessToken').value = cfg.dingtalk_access_token || '';
            document.getElementById('dingtalkSecret').value = cfg.dingtalk_secret || '';
            document.getElementById('dingtalkApiUrl').value = cfg.dingtalk_api_url || '';

            // 飞书
            document.getElementById('feishuEnabled').checked = cfg.feishu_enabled || false;
            document.getElementById('feishuWebhookUrl').value = cfg.feishu_webhook_url || '';
            document.getElementById('feishuSecret').value = cfg.feishu_secret || '';
        }
    } catch (error) {
        console.error('加载通知渠道配置失败:', error);
    }
}

// 保存所有通知渠道配置（包括 Webhook）
async function saveAllNotifyChannels() {
    // 先保存 Webhook 配置
    const webhookResult = await saveWebhookQuiet();
    // 再保存其他通知渠道配置
    const notifyResult = await saveNotifyChannelsQuiet();

    if (webhookResult.success && notifyResult.success) {
        alert('所有通知渠道配置保存成功');
    } else {
        let errorMsg = '保存失败:\n';
        if (!webhookResult.success) errorMsg += '- Webhook: ' + webhookResult.message + '\n';
        if (!notifyResult.success) errorMsg += '- 其他渠道: ' + notifyResult.message;
        alert(errorMsg);
    }
}

// 保存所有通知渠道配置
async function saveNotifyChannels() {
    const result = await saveNotifyChannelsQuiet();
    if (result.success) {
        alert('通知渠道配置保存成功');
    } else {
        alert('保存失败: ' + result.message);
    }
}

// 静默保存通知渠道配置（不弹出提示，供测试函数调用）
async function saveNotifyChannelsQuiet() {
    const config = {
        // Telegram
        telegram_enabled: document.getElementById('telegramEnabled').checked,
        telegram_bot_token: document.getElementById('telegramBotToken').value.trim(),
        telegram_user_id: document.getElementById('telegramUserId').value.trim(),
        telegram_api_url: document.getElementById('telegramApiUrl').value.trim(),

        // 企业微信
        wecom_enabled: document.getElementById('wecomEnabled').checked,
        wecom_webhook_key: document.getElementById('wecomWebhookKey').value.trim(),
        wecom_api_url: document.getElementById('wecomApiUrl').value.trim(),

        // 钉钉
        dingtalk_enabled: document.getElementById('dingtalkEnabled').checked,
        dingtalk_access_token: document.getElementById('dingtalkAccessToken').value.trim(),
        dingtalk_secret: document.getElementById('dingtalkSecret').value.trim(),
        dingtalk_api_url: document.getElementById('dingtalkApiUrl').value.trim(),

        // 飞书
        feishu_enabled: document.getElementById('feishuEnabled').checked,
        feishu_webhook_url: document.getElementById('feishuWebhookUrl').value.trim(),
        feishu_secret: document.getElementById('feishuSecret').value.trim()
    };

    try {
        const res = await fetch('/api/notify/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(config)
        });

        const data = await res.json();
        return {success: data.success, message: data.message || ''};
    } catch (error) {
        return {success: false, message: error.message};
    }
}

// 保存 Telegram 配置
async function saveTelegram() {
    const result = await saveNotifyChannelsQuiet();
    if (result.success) {
        alert('Telegram 配置保存成功');
    } else {
        alert('保存失败: ' + result.message);
    }
}

// 保存企业微信配置
async function saveWecom() {
    const result = await saveNotifyChannelsQuiet();
    if (result.success) {
        alert('企业微信配置保存成功');
    } else {
        alert('保存失败: ' + result.message);
    }
}

// 保存钉钉配置
async function saveDingtalk() {
    const result = await saveNotifyChannelsQuiet();
    if (result.success) {
        alert('钉钉配置保存成功');
    } else {
        alert('保存失败: ' + result.message);
    }
}

// 保存飞书配置
async function saveFeishu() {
    const result = await saveNotifyChannelsQuiet();
    if (result.success) {
        alert('飞书配置保存成功');
    } else {
        alert('保存失败: ' + result.message);
    }
}

// 测试 Telegram
async function testTelegram() {
    const botToken = document.getElementById('telegramBotToken').value.trim();
    const userId = document.getElementById('telegramUserId').value.trim();

    if (!botToken || !userId) {
        alert('请先填写 Bot Token 和 User ID');
        return;
    }

    if (!confirm('确定要发送测试通知到 Telegram 吗？\n\n注意：将自动保存当前配置后再测试')) return;

    // 先保存配置
    await saveNotifyChannelsQuiet();

    try {
        const res = await fetch('/api/notify/test/telegram', {method: 'POST'});
        const data = await res.json();

        if (data.success) {
            alert('Telegram 测试通知发送成功！');
        } else {
            alert('测试失败: ' + data.message);
        }
    } catch (error) {
        alert('测试失败: ' + error.message);
    }
}

// 测试企业微信
async function testWecom() {
    const webhookKey = document.getElementById('wecomWebhookKey').value.trim();

    if (!webhookKey) {
        alert('请先填写 Webhook Key');
        return;
    }

    if (!confirm('确定要发送测试通知到企业微信吗？\n\n注意：将自动保存当前配置后再测试')) return;

    // 先保存配置
    await saveNotifyChannelsQuiet();

    try {
        const res = await fetch('/api/notify/test/wecom', {method: 'POST'});
        const data = await res.json();

        if (data.success) {
            alert('企业微信测试通知发送成功！');
        } else {
            alert('测试失败: ' + data.message);
        }
    } catch (error) {
        alert('测试失败: ' + error.message);
    }
}

// 测试钉钉
async function testDingtalk() {
    const accessToken = document.getElementById('dingtalkAccessToken').value.trim();

    if (!accessToken) {
        alert('请先填写 Access Token');
        return;
    }

    if (!confirm('确定要发送测试通知到钉钉吗？\n\n注意：将自动保存当前配置后再测试')) return;

    // 先保存配置
    await saveNotifyChannelsQuiet();

    try {
        const res = await fetch('/api/notify/test/dingtalk', {method: 'POST'});
        const data = await res.json();

        if (data.success) {
            alert('钉钉测试通知发送成功！');
        } else {
            alert('测试失败: ' + data.message);
        }
    } catch (error) {
        alert('测试失败: ' + error.message);
    }
}

// 测试飞书
async function testFeishu() {
    const webhookUrl = document.getElementById('feishuWebhookUrl').value.trim();

    if (!webhookUrl) {
        alert('请先填写 Webhook 地址');
        return;
    }

    if (!confirm('确定要发送测试通知到飞书吗？\n\n注意：将自动保存当前配置后再测试')) return;

    // 先保存配置
    await saveNotifyChannelsQuiet();

    try {
        const res = await fetch('/api/notify/test/feishu', {method: 'POST'});
        const data = await res.json();

        if (data.success) {
            alert('飞书测试通知发送成功！');
        } else {
            alert('测试失败: ' + data.message);
        }
    } catch (error) {
        alert('测试失败: ' + error.message);
    }
}
