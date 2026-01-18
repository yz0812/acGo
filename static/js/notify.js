// 页面加载时初始化
document.addEventListener('DOMContentLoaded', function() {
    loadWebhookConfig();
    loadNotifyChannels();
});

// ==================== Webhook 配置 ====================

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

// 静默保存 Webhook 配置
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

    if (!confirm('确定要发送测试通知到 Webhook 吗？\n\n注意：将自动保存当前配置后再测试')) return;

    await saveWebhookQuiet();

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

// ==================== 通知渠道配置 ====================

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

// 静默保存通知渠道配置
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
