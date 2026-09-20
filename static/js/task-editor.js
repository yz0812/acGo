// Samples call an HTTP echo endpoint; replace it with your own check-in API.
const TASK_LABELS = {curl: 'Curl', javascript: 'JavaScript', python: 'Python'};
const TASK_DEMOS = {
    curl: "curl 'https://httpbin.org/get' -H 'Accept: application/json'",
    javascript: `// Node.js 18+，演示请求；请替换成实际签到接口。
async function main() {
    const response = await fetch('https://httpbin.org/get', {
        headers: { 'Accept': 'application/json' },
        signal: AbortSignal.timeout(30000)
    });
    const body = await response.text();
    console.log(body);
    if (!response.ok) throw new Error('HTTP ' + response.status);
    // 如接口用 JSON 字段表示业务失败，请在这里判断并抛出 Error。
}
main().catch(error => {
    console.error(error.message);
    process.exitCode = 1;
});`,
    python: `# 使用当前服务的 Python 虚拟环境（已安装 requests）。
# 演示请求；请替换成实际签到接口。
import requests

response = requests.get(
    "https://httpbin.org/get",
    headers={"Accept": "application/json"},
    timeout=30,
)
print(response.text)
response.raise_for_status()
# 如接口用 JSON 字段表示业务失败，请在这里判断并 raise RuntimeError。
`
};
let taskDrafts = {};
let activeTaskType = 'curl';

function resetTaskEditor(type = 'curl', curl = '', script = '') {
    taskDrafts = {curl, javascript: '', python: ''};
    if (type !== 'curl') taskDrafts[type] = script;
    activeTaskType = type;
    document.getElementById('taskType').value = type;
    renderTaskEditor();
}

function changeTaskType() {
    const editor = document.getElementById(activeTaskType === 'curl' ? 'curlCommand' : 'scriptContent');
    taskDrafts[activeTaskType] = editor.value;
    activeTaskType = document.getElementById('taskType').value;
    renderTaskEditor();
}

function renderTaskEditor() {
    const isCurl = activeTaskType === 'curl';
    document.getElementById('curlEditor').hidden = !isCurl;
    document.getElementById('scriptEditor').hidden = isCurl;
    document.getElementById('curlCommand').required = isCurl;
    document.getElementById('scriptContent').required = !isCurl;
    document.getElementById('curlCommand').value = taskDrafts.curl;
    document.getElementById('scriptContent').value = isCurl ? '' : taskDrafts[activeTaskType];
    document.getElementById('scriptLabel').textContent = `${TASK_LABELS[activeTaskType]} 脚本 *`;
    document.getElementById('taskDemo').textContent = TASK_DEMOS[activeTaskType];
    document.getElementById('taskHelp').textContent = isCurl
        ? '粘贴浏览器复制的 Curl 命令；HTTP 2xx 表示成功。'
        : `${activeTaskType === 'python' ? '使用服务当前 Python 环境' : '使用服务器 Node.js（示例需要 18+）'}；退出码 0 表示成功，非 0 或超时会按配置重试。单次最多 60 秒，输出写入签到记录。脚本以服务权限运行，仅填写可信代码。`;
}

function insertTaskDemo() {
    const editor = document.getElementById(activeTaskType === 'curl' ? 'curlCommand' : 'scriptContent');
    if (editor.value.trim() && !confirm('用示例替换当前内容？')) return;
    editor.value = TASK_DEMOS[activeTaskType];
    editor.focus();
    editor.setSelectionRange(0, 0);
    editor.scrollTop = 0;
}

function showScriptPreview(preview) {
    document.getElementById('scriptPreviewTitle').textContent = `${TASK_LABELS[preview.task_type]} 脚本详情`;
    document.getElementById('scriptPreviewContent').textContent = preview.script_content || '';
    document.getElementById('scriptPreviewModal').style.display = 'block';
    document.body.style.overflow = 'hidden';
}

function closeScriptPreview() {
    document.getElementById('scriptPreviewModal').style.display = 'none';
    document.body.style.overflow = '';
}
