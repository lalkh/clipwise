// --- Settings / Claude Login ---
let _claudeConnected = false;

function requireLogin() {
    if (_claudeConnected) return true;
    document.getElementById('settingsModal').style.display = 'flex';
    checkStatus();
    return false;
}

async function checkStatus() {
    try {
        const resp = await fetch('/api/config/status');
        const data = await resp.json();
        const dot = document.getElementById('statusDot');
        const isConnected = data.status === 'connected';
        _claudeConnected = isConnected;
        dot.className = 'status-dot ' + (isConnected ? 'connected' : 'disconnected');
        dot.title = data.detail;
        const modalStatus = document.getElementById('settingsStatus');
        if (modalStatus) {
            modalStatus.querySelector('.status-dot').className = 'status-dot ' + (isConnected ? 'connected' : 'disconnected');
            modalStatus.querySelector('.status-text').textContent = data.detail;
        }
        const loginBtn = document.getElementById('startLogin');
        if (isConnected && loginBtn) {
            loginBtn.textContent = '已登录'; loginBtn.disabled = true; loginBtn.style.background = '#00b894';
            document.getElementById('loginArea').style.display = 'none';
        }
        return data;
    } catch(e) { console.error('Status check failed:', e); }
}

document.getElementById('settingsBtn').addEventListener('click', () => {
    document.getElementById('settingsModal').style.display = 'flex'; checkStatus();
});
document.getElementById('closeSettings').addEventListener('click', () => {
    document.getElementById('settingsModal').style.display = 'none';
});
document.getElementById('settingsModal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) e.currentTarget.style.display = 'none';
});

document.getElementById('startLogin').addEventListener('click', async () => {
    const btn = document.getElementById('startLogin');
    btn.textContent = '获取登录链接...'; btn.disabled = true;
    try {
        const resp = await fetch('/api/config/login', { method: 'POST' });
        const data = await resp.json();
        if (data.url) {
            document.getElementById('loginArea').style.display = 'block';
            const urlEl = document.getElementById('loginUrl');
            urlEl.href = data.url; urlEl.textContent = data.url;
            btn.textContent = '已打开授权页';
            window.open(data.url, '_blank');
        } else {
            alert(data.error || '获取链接失败');
            btn.textContent = '登录 Claude'; btn.disabled = false;
        }
    } catch(e) {
        alert('登录失败: ' + e.message);
        btn.textContent = '登录 Claude'; btn.disabled = false;
    }
});

document.getElementById('submitCode').addEventListener('click', async () => {
    const code = document.getElementById('authCodeInput').value.trim();
    if (!code) return;
    const btn = document.getElementById('submitCode');
    const msg = document.getElementById('codeResultMsg');
    btn.textContent = '验证中...'; btn.disabled = true;
    try {
        const resp = await fetch('/api/config/login-code', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({code}),
        });
        const data = await resp.json();
        msg.style.display = 'block';
        if (data.success) {
            msg.style.color = '#00b894'; msg.textContent = '登录成功！';
            await checkStatus();
            setTimeout(() => { document.getElementById('settingsModal').style.display = 'none'; }, 1500);
        } else {
            msg.style.color = '#ff7675'; msg.textContent = data.error || '验证失败';
        }
    } catch(e) {
        msg.style.display = 'block'; msg.style.color = '#ff7675'; msg.textContent = '失败: ' + e.message;
    }
    btn.textContent = '提交'; btn.disabled = false;
});
document.getElementById('authCodeInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') document.getElementById('submitCode').click();
});

checkStatus().then(data => {
    if (data && data.status !== 'connected') {
        document.getElementById('settingsModal').style.display = 'flex';
    }
});

// --- Tab switching ---
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(tab.dataset.panel).classList.add('active');

        if (tab.dataset.panel === 'auto-edit') {
            refreshTemplateList();
            checkExistingEditJobs();
        }
        if (tab.dataset.panel === 'analyze') {
            loadAnalysisHistory();
        }
    });
});
// --- Tab switching ---
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(tab.dataset.panel).classList.add('active');

        if (tab.dataset.panel === 'auto-edit') {
            refreshTemplateList();
            checkExistingEditJobs();
        }
        if (tab.dataset.panel === 'analyze') {
            loadAnalysisHistory();
        }
    });
});

// Load history on page load
setTimeout(loadAnalysisHistory, 500);

async function loadAnalysisHistory() {
    const historyDiv = document.getElementById('analysisHistory');
    if (!historyDiv) return;
    try {
        const resp = await fetch('/api/analyses');
        const analyses = await resp.json();
        const completed = analyses.filter(a => a.status === 'completed');
        if (completed.length === 0) {
            historyDiv.innerHTML = '';
            return;
        }
        let html = '<h3 style="color:#a29bfe;margin-bottom:12px">历史分析</h3>';
        html += '<div style="display:flex;flex-direction:column;gap:8px">';
        for (const a of completed) {
            const name = a.display_name || a.filename;
            html += `
            <div style="background:#1a1a2e;border-radius:8px;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;cursor:pointer;border:1px solid #2a2a3e"
                 onclick="loadAnalysisResult('${a.job_id}')">
                <div>
                    <span style="color:#fff">${name}</span>
                    <span style="color:#666;font-size:12px;margin-left:8px">${a.shot_count} 个镜头</span>
                </div>
                <span style="color:#6c5ce7;font-size:13px">查看</span>
            </div>`;
        }
        html += '</div>';
        historyDiv.innerHTML = html;
    } catch (e) {}
}

// --- Analysis ---
const analyzeUpload = document.getElementById('analyzeUpload');
const analyzeFile = document.getElementById('analyzeFile');

analyzeUpload.addEventListener('click', () => { if (requireLogin()) analyzeFile.click(); });
analyzeUpload.addEventListener('dragover', e => {
    e.preventDefault();
    analyzeUpload.classList.add('dragover');
});
analyzeUpload.addEventListener('dragleave', () => analyzeUpload.classList.remove('dragover'));
analyzeUpload.addEventListener('drop', e => {
    e.preventDefault();
    analyzeUpload.classList.remove('dragover');
    if (!requireLogin()) return;
    if (e.dataTransfer.files.length) {
        startAnalysis(e.dataTransfer.files[0]);
    }
});
analyzeFile.addEventListener('change', () => {
    if (analyzeFile.files.length) {
        if (!requireLogin()) return;
        startAnalysis(analyzeFile.files[0]);
        analyzeFile.value = '';  // reset so same file can be re-selected
    }
});

let currentJobId = null;

async function startAnalysis(file) {
    const progressContainer = document.getElementById('analyzeProgress');
    const progressFill = document.getElementById('analyzeProgressFill');
    const progressText = document.getElementById('analyzeProgressText');
    const resultDiv = document.getElementById('analyzeResult');

    // Show upload zone selected file name
    analyzeUpload.querySelector('h3').textContent = `已选择: ${file.name}`;
    analyzeUpload.querySelector('p').textContent = '正在上传...';

    progressContainer.style.display = 'block';
    resultDiv.innerHTML = '';
    progressFill.style.width = '2%';
    progressText.textContent = `上传 ${file.name}...`;

    const formData = new FormData();
    formData.append('video', file);
    formData.append('threshold', '0.3');

    try {
        const resp = await fetch('/api/analyze', { method: 'POST', body: formData });
        if (!resp.ok) {
            const err = await resp.text();
            progressText.textContent = `上传失败: ${err}`;
            resetUploadZone();
            return;
        }
        const data = await resp.json();
        currentJobId = data.job_id;

        progressFill.style.width = '5%';
        progressText.textContent = '上传完成，正在分析（这可能需要几分钟）...';
        analyzeUpload.querySelector('p').textContent = '分析进行中...';

        // Listen for SSE progress
        listenForProgress(currentJobId);
    } catch (err) {
        progressText.textContent = '上传失败: ' + err.message;
        resetUploadZone();
    }
}

function listenForProgress(jobId) {
    const progressFill = document.getElementById('analyzeProgressFill');
    const progressText = document.getElementById('analyzeProgressText');

    let lastUpdate = Date.now();
    let shimmerTimer = null;

    // If no progress update for 10s, switch to indeterminate animation
    shimmerTimer = setInterval(() => {
        if (Date.now() - lastUpdate > 10000) {
            progressFill.classList.add('indeterminate');
        }
    }, 5000);

    function cleanup() {
        if (shimmerTimer) clearInterval(shimmerTimer);
        progressFill.classList.remove('indeterminate');
    }

    const evtSource = new EventSource(`/api/analyze/${jobId}/status`);

    evtSource.addEventListener('progress', e => {
        lastUpdate = Date.now();
        progressFill.classList.remove('indeterminate');
        const evt = JSON.parse(e.data);
        const pct = Math.max(evt.progress * 100, 5);
        progressFill.style.width = pct + '%';
        progressText.textContent = evt.message || '分析中...';

        if (evt.done) {
            evtSource.close();
            cleanup();
            progressFill.style.width = '100%';
            progressText.textContent = '分析完成，正在加载结果...';
            loadAnalysisResult(jobId);
            resetUploadZone();
        }
        if (evt.error) {
            evtSource.close();
            cleanup();
            progressText.textContent = '分析失败: ' + evt.message;
            resetUploadZone();
        }
    });

    evtSource.addEventListener('ping', () => {
        // Connection alive - update timer so we don't think it's stuck
        lastUpdate = Date.now();
        progressText.textContent = 'Claude 正在分析中，请耐心等待...';
    });

    evtSource.onerror = () => {
        evtSource.close();
        // SSE disconnected — switch to polling until done
        progressText.textContent = '连接中断，轮询检查中...';
        const poll = setInterval(async () => {
            try {
                const resp = await fetch(`/api/analyze/${jobId}`);
                const data = await resp.json();
                if (data.status === 'completed') {
                    clearInterval(poll);
                    cleanup();
                    progressFill.style.width = '100%';
                    progressText.textContent = '分析完成，正在加载结果...';
                    loadAnalysisResult(jobId);
                    resetUploadZone();
                } else if (data.status === 'error') {
                    clearInterval(poll);
                    cleanup();
                    progressText.textContent = '分析失败: ' + (data.error || '未知错误');
                    resetUploadZone();
                } else {
                    progressFill.classList.add('indeterminate');
                    progressText.textContent = 'Claude 正在分析中，请耐心等待...';
                }
            } catch (e) {}
        }, 5000);
    };
}

function resetUploadZone() {
    analyzeUpload.querySelector('h3').textContent = '上传视频进行分镜分析';
    analyzeUpload.querySelector('p').textContent = '拖拽视频文件到此处，或点击选择文件';
}

async function loadAnalysisResult(jobId) {
    currentJobId = jobId;
    const resp = await fetch(`/api/analyze/${jobId}`);
    const data = await resp.json();
    renderAnalysis(data);
}

function formatTime(seconds) {
    const m = Math.floor(seconds / 60);
    const s = (seconds % 60).toFixed(2);
    return `${m}:${s.padStart(5, '0')}`;
}

function renderAnalysis(data) {
    const resultDiv = document.getElementById('analyzeResult');
    const info = data.video_info;

    let html = '';

    // Re-analyze button
    html += `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h2 style="font-size:18px;color:#fff">分析结果</h2>
        <div style="display:flex;gap:8px">
            <a href="/api/analyze/${data.job_id}/markdown" download="${data.job_id}_analysis.md"
               class="btn" style="background:#333;color:#ccc;font-size:13px;padding:6px 14px">
                下载 Markdown
            </a>
            <button class="btn btn-primary" style="font-size:13px;padding:6px 14px"
                    onclick="document.getElementById('analyzeFile').click()">
                重新分析新视频
            </button>
        </div>
    </div>`;

    html += `
    <div class="video-info">
        <h3>视频信息</h3>
        <div class="info-grid">
            <div class="info-item"><span class="info-label">文件名</span><span class="info-value">${info.filename}</span></div>
            <div class="info-item"><span class="info-label">分辨率</span><span class="info-value">${info.resolution}</span></div>
            <div class="info-item"><span class="info-label">帧率</span><span class="info-value">${info.fps}fps</span></div>
            <div class="info-item"><span class="info-label">时长</span><span class="info-value">${info.duration.toFixed(1)}s</span></div>
            <div class="info-item"><span class="info-label">编码</span><span class="info-value">${info.codec}</span></div>
            <div class="info-item"><span class="info-label">镜头数</span><span class="info-value">${info.shot_count}</span></div>
        </div>
    </div>`;

    if (data.overview) {
        html += `<div class="overview"><strong>概述：</strong>${data.overview}</div>`;
    }

    // View toggle
    html += `
    <div class="view-toggle">
        <button class="view-btn active" onclick="switchView('grid')">卡片视图</button>
        <button class="view-btn" onclick="switchView('table')">表格视图</button>
        <button class="view-btn" onclick="switchView('markdown')">Markdown 原文</button>
    </div>`;

    // Grid view
    html += '<div id="gridView" class="shots-grid">';
    if (data.shots.length === 0) {
        html += '<div class="empty-state"><h3>未解析到镜头数据</h3><p>请查看 Markdown 原文获取完整分析</p></div>';
    }
    for (const shot of data.shots) {
        html += `
        <div class="shot-card">
            ${shot.keyframe_path ? `<img src="${shot.keyframe_path}" alt="镜头 ${shot.number}" loading="lazy" onerror="this.style.display='none'">` : ''}
            <div class="shot-info">
                <div class="shot-header">
                    <span class="shot-number">#${shot.number}</span>
                    <span class="shot-time">${formatTime(shot.start_time)} - ${formatTime(shot.end_time)} (${shot.duration.toFixed(1)}s)</span>
                </div>
                <div class="shot-tags">
                    ${shot.composition ? `<span class="tag composition">${shot.composition}</span>` : ''}
                    ${shot.camera_movement ? `<span class="tag camera">${shot.camera_movement}</span>` : ''}
                    ${shot.lighting ? `<span class="tag lighting">${shot.lighting}</span>` : ''}
                </div>
                <div class="shot-desc">${shot.visual_description || shot.content || ''}</div>
                ${shot.focal_length ? `<div style="margin-top:4px;font-size:12px;color:#999">${shot.focal_length}</div>` : ''}
                ${shot.emotion ? `<div style="margin-top:4px;font-size:12px;color:#888">情绪: ${shot.emotion}</div>` : ''}
                ${shot.transition_from_prev ? `<div style="margin-top:4px;font-size:12px;color:#6c5ce7">转场: ${shot.transition_from_prev}</div>` : ''}
                ${shot.detail_text ? `<details style="margin-top:6px"><summary style="font-size:12px;color:#6c5ce7;cursor:pointer">详细分析</summary><pre style="font-size:11px;color:#aaa;white-space:pre-wrap;margin-top:4px;line-height:1.5">${shot.detail_text}</pre></details>` : ''}
                <div style="margin-top:8px;display:flex;gap:12px">
                    <span style="color:#6c5ce7;font-size:12px;cursor:pointer"
                          onclick="reanalyzeShot('${data.job_id}', ${shot.start_time}, ${shot.end_time})">
                        拆分镜头
                    </span>
                    <span style="color:#e17055;font-size:12px;cursor:pointer"
                          onclick="mergeWithNext('${data.job_id}', ${shot.number})">
                        合并到下一个
                    </span>
                </div>
            </div>
        </div>`;
    }
    html += '</div>';

    // Table view
    html += `
    <table id="tableView" class="shots-table" style="display:none">
        <thead>
            <tr>
                <th>#</th><th>时间</th><th>时长</th><th>构图</th><th>运镜</th>
                <th>光影</th><th>内容</th><th>转场</th>
            </tr>
        </thead>
        <tbody>`;
    for (const shot of data.shots) {
        html += `
        <tr>
            <td>${shot.number}</td>
            <td>${formatTime(shot.start_time)}</td>
            <td>${shot.duration.toFixed(1)}s</td>
            <td>${shot.composition}</td>
            <td>${shot.camera_movement}</td>
            <td>${shot.lighting}</td>
            <td>${shot.content}</td>
            <td>${shot.transition_from_prev}</td>
        </tr>`;
    }
    html += '</tbody></table>';

    // Markdown view (loaded on demand)
    html += '<div id="markdownView" style="display:none"></div>';

    resultDiv.innerHTML = html;
}

async function switchView(view) {
    document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');

    const grid = document.getElementById('gridView');
    const table = document.getElementById('tableView');
    const md = document.getElementById('markdownView');
    if (!grid) return;

    grid.style.display = view === 'grid' ? 'grid' : 'none';
    table.style.display = view === 'table' ? 'table' : 'none';
    md.style.display = view === 'markdown' ? 'block' : 'none';

    if (view === 'markdown' && !md.dataset.loaded && currentJobId) {
        md.innerHTML = '<p style="color:#888;padding:20px">加载中...</p>';
        try {
            const resp = await fetch(`/api/analyze/${currentJobId}/markdown`);
            const text = await resp.text();
            md.innerHTML = `<pre class="markdown-raw">${escapeHtml(text)}</pre>`;
            md.dataset.loaded = 'true';
        } catch (e) {
            md.innerHTML = '<p style="color:#ff6b6b;padding:20px">加载失败</p>';
        }
    }
}

function escapeHtml(str) {
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// --- Auto Edit ---
let selectedTemplate = null;
let materialFiles = [];

const materialUpload = document.getElementById('materialUpload');
const materialFileInput = document.getElementById('materialFiles');       // folder input
const materialFilesFlat = document.getElementById('materialFilesFlat');   // multi-file input

const MEDIA_EXTS = ['.mp4','.mov','.avi','.mkv','.webm','.jpg','.jpeg','.png','.bmp','.webp','.gif'];

function isMediaFile(name) {
    const ext = '.' + name.split('.').pop().toLowerCase();
    return MEDIA_EXTS.includes(ext);
}

// Drag & drop on upload zone (supports files and folders via dataTransfer.items)
materialUpload.addEventListener('dragover', e => {
    e.preventDefault();
    materialUpload.classList.add('dragover');
});
materialUpload.addEventListener('dragleave', () => materialUpload.classList.remove('dragover'));
materialUpload.addEventListener('drop', async e => {
    e.preventDefault();
    materialUpload.classList.remove('dragover');
    // Use items API to handle folders
    if (e.dataTransfer.items) {
        const entries = [];
        for (const item of e.dataTransfer.items) {
            const entry = item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
            if (entry) entries.push(entry);
        }
        const files = await readAllEntries(entries);
        addMaterials(files);
    } else {
        addMaterials(e.dataTransfer.files);
    }
});

// Recursively read folder entries
async function readAllEntries(entries) {
    const files = [];
    for (const entry of entries) {
        if (entry.isFile) {
            const file = await new Promise(resolve => entry.file(resolve));
            if (isMediaFile(file.name)) files.push(file);
        } else if (entry.isDirectory) {
            const reader = entry.createReader();
            const subEntries = await new Promise(resolve => reader.readEntries(resolve));
            const subFiles = await readAllEntries(subEntries);
            files.push(...subFiles);
        }
    }
    return files;
}

// Folder input (webkitdirectory)
materialFileInput.addEventListener('change', () => {
    const files = Array.from(materialFileInput.files).filter(f => isMediaFile(f.name));
    addMaterialArray(files);
    materialFileInput.value = '';
});

// Multi-file input
materialFilesFlat.addEventListener('change', () => {
    addMaterials(materialFilesFlat.files);
    materialFilesFlat.value = '';
});

function addMaterials(fileList) {
    const files = Array.from(fileList).filter(f => isMediaFile(f.name));
    addMaterialArray(files);
}

function addMaterialArray(files) {
    for (const f of files) {
        // Avoid duplicates by name
        if (!materialFiles.some(m => m.name === f.name && m.size === f.size)) {
            materialFiles.push(f);
        }
    }
    renderMaterialList();
    updateEditButton();
}

function removeMaterial(index) {
    materialFiles.splice(index, 1);
    renderMaterialList();
    updateEditButton();
}

function clearMaterials() {
    materialFiles = [];
    renderMaterialList();
    updateEditButton();
}

function renderMaterialList() {
    const list = document.getElementById('materialList');
    if (materialFiles.length === 0) {
        list.innerHTML = '';
        return;
    }
    let html = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <span style="color:#888;font-size:13px">${materialFiles.length} 个素材</span>
        <span style="color:#6c5ce7;font-size:13px;cursor:pointer" onclick="clearMaterials()">清空全部</span>
    </div>`;
    html += materialFiles.map((f, i) =>
        `<div class="material-chip">
            ${f.name}
            <span class="remove" onclick="removeMaterial(${i})">×</span>
        </div>`
    ).join('');
    list.innerHTML = html;
}

function updateEditButton() {
    const hasMaterials = materialFiles.length > 0;
    const ok = selectedTemplate && hasMaterials;
    document.getElementById('startEdit').disabled = !ok;
}

// Template dropdown
const templateSelect = document.getElementById('templateSelect');

async function refreshTemplateList() {
    try {
        const resp = await fetch('/api/analyses');
        const analyses = await resp.json();
        const completed = analyses.filter(a => a.status === 'completed');

        templateSelect.innerHTML = '<option value="">-- 请选择模板 --</option>';
        for (const a of completed) {
            const opt = document.createElement('option');
            opt.value = a.job_id;
            const name = a.display_name || a.filename;
            opt.textContent = `${name} (${a.shot_count} 个镜头)`;
            if (selectedTemplate === a.job_id) opt.selected = true;
            templateSelect.appendChild(opt);
        }
    } catch (err) {
        console.error('Failed to load templates:', err);
    }
}

templateSelect.addEventListener('change', async () => {
    selectedTemplate = templateSelect.value || null;
    updateEditButton();

    const infoDiv = document.getElementById('templateInfo');
    if (!selectedTemplate) {
        infoDiv.style.display = 'none';
        return;
    }

    // Load and show template info
    try {
        const resp = await fetch(`/api/analyze/${selectedTemplate}`);
        const data = await resp.json();
        const info = data.video_info;
        const displayName = data.display_name || info.filename;
        let html = `
            <div style="display:flex;align-items:center;gap:8px">
                <span class="label">名称:</span>
                <span class="value" id="templateName">${displayName}</span>
                <span style="color:#6c5ce7;font-size:12px;cursor:pointer" onclick="renameTemplate('${selectedTemplate}')">改名</span>
            </div>
            <div><span class="label">文件:</span><span class="value">${info.filename}</span></div>
            <div><span class="label">分辨率:</span><span class="value">${info.resolution}</span>
                 <span class="label" style="margin-left:16px">帧率:</span><span class="value">${info.fps}fps</span>
                 <span class="label" style="margin-left:16px">时长:</span><span class="value">${info.duration.toFixed(1)}s</span></div>
            <div><span class="label">镜头数:</span><span class="value">${info.shot_count}</span></div>`;
        if (data.overview) {
            html += `<div style="margin-top:8px;color:#999">${data.overview}</div>`;
        }
        infoDiv.innerHTML = html;
        infoDiv.style.display = 'block';
    } catch (e) {
        infoDiv.style.display = 'none';
    }
});

async function mergeWithNext(jobId, shotNumber) {
    if (!confirm(`将镜头 #${shotNumber} 和 #${shotNumber + 1} 合并？\nAI 将重新分析合并后的时间范围。`)) return;

    const progressContainer = document.getElementById('analyzeProgress');
    const progressFill = document.getElementById('analyzeProgressFill');
    const progressText = document.getElementById('analyzeProgressText');
    progressContainer.style.display = 'block';
    progressFill.classList.add('indeterminate');
    progressText.textContent = `正在合并镜头 #${shotNumber} 和 #${shotNumber + 1}，AI 重新分析中...`;

    try {
        const resp = await fetch(`/api/analyze/${jobId}/merge-shots`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({shot_number: shotNumber}),
        });
        const data = await resp.json();
        if (data.success) {
            // Poll until shot count changes
            const originalCount = (await (await fetch(`/api/analyze/${jobId}`)).json()).video_info.shot_count;
            const poll = setInterval(async () => {
                const r = await fetch(`/api/analyze/${jobId}`);
                const d = await r.json();
                if (d.video_info.shot_count < originalCount) {
                    clearInterval(poll);
                    progressFill.classList.remove('indeterminate');
                    progressContainer.style.display = 'none';
                    loadAnalysisResult(jobId);
                    loadAnalysisHistory();
                }
            }, 3000);
            setTimeout(() => { clearInterval(poll); progressContainer.style.display = 'none'; }, 300000);
        } else {
            progressFill.classList.remove('indeterminate');
            progressText.textContent = '合并失败: ' + (data.error || '');
        }
    } catch (e) {
        progressFill.classList.remove('indeterminate');
        progressText.textContent = '合并失败: ' + e.message;
    }
}

async function reanalyzeShot(jobId, startTime, endTime) {
    if (!confirm(`重新分析 ${startTime.toFixed(1)}s - ${endTime.toFixed(1)}s 这个时间范围？\n将检测此范围内是否有多个镜头。`)) return;

    const progressText = document.getElementById('analyzeProgressText');
    const progressContainer = document.getElementById('analyzeProgress');
    const progressFill = document.getElementById('analyzeProgressFill');
    progressContainer.style.display = 'block';
    progressFill.classList.add('indeterminate');
    progressText.textContent = `正在重新分析 ${startTime.toFixed(1)}s-${endTime.toFixed(1)}s...`;

    try {
        const resp = await fetch(`/api/analyze/${jobId}/reanalyze-range`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({start: startTime, end: endTime}),
        });
        const data = await resp.json();
        if (data.success) {
            // Poll until shots change
            const originalCount = (await (await fetch(`/api/analyze/${jobId}`)).json()).shots.length;
            const poll = setInterval(async () => {
                const r = await fetch(`/api/analyze/${jobId}`);
                const d = await r.json();
                if (d.shots.length !== originalCount || d.video_info.shot_count !== originalCount) {
                    clearInterval(poll);
                    progressFill.classList.remove('indeterminate');
                    progressContainer.style.display = 'none';
                    loadAnalysisResult(jobId);
                    loadAnalysisHistory();
                }
            }, 3000);
            // Timeout after 5 minutes
            setTimeout(() => {
                clearInterval(poll);
                progressFill.classList.remove('indeterminate');
                progressText.textContent = '重新分析超时，请刷新页面查看';
            }, 300000);
        }
    } catch (e) {
        progressFill.classList.remove('indeterminate');
        progressText.textContent = '重新分析失败: ' + e.message;
    }
}

async function renameTemplate(jobId) {
    const newName = prompt('输入新的模板名称:');
    if (!newName) return;
    try {
        const resp = await fetch(`/api/analyze/${jobId}/rename`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: newName}),
        });
        const data = await resp.json();
        if (data.success) {
            document.getElementById('templateName').textContent = newName;
            refreshTemplateList();
        }
    } catch (e) {
        console.error('Rename failed:', e);
    }
}

document.getElementById('startEdit').addEventListener('click', () => {
    if (requireLogin()) startAutoEdit();
});

async function startAutoEdit() {
    const progressContainer = document.getElementById('editProgress');
    const progressFill = document.getElementById('editProgressFill');
    const progressText = document.getElementById('editProgressText');
    const resultDiv = document.getElementById('editResult');

    progressContainer.style.display = 'block';
    resultDiv.innerHTML = '';
    progressFill.style.width = '0%';
    progressText.textContent = '上传素材中...';

    const formData = new FormData();
    formData.append('template_job_id', selectedTemplate);
    const editPrompt = document.getElementById('editPrompt').value.trim();
    if (editPrompt) {
        formData.append('user_prompt', editPrompt);
    }
    for (const f of materialFiles) {
        formData.append('materials', f);
    }

    try {
        const resp = await fetch('/api/auto-edit', { method: 'POST', body: formData });
        const data = await resp.json();
        const jobId = data.job_id;

        const evtSource = new EventSource(`/api/auto-edit/${jobId}/status`);
        evtSource.addEventListener('progress', e => {
            const evt = JSON.parse(e.data);
            progressFill.style.width = (evt.progress * 100) + '%';
            progressText.textContent = evt.message;

            if (evt.done) {
                evtSource.close();
                renderEditResult(jobId, evt.result);
            }
            if (evt.error) {
                evtSource.close();
                progressText.textContent = '剪映工程生成失败: ' + evt.message;
                if (evt.result) renderEditResult(jobId, evt.result);
            }
        });

        evtSource.onerror = () => {
            setTimeout(async () => {
                try {
                    const r = await fetch(`/api/auto-edit/${jobId}`);
                    const d = await r.json();
                    if (d.status === 'completed' || d.status === 'completed_with_warning' || d.status === 'error') {
                        evtSource.close();
                        if (d.status === 'completed' || d.status === 'completed_with_warning') {
                            renderEditResult(jobId, d);
                        } else {
                            progressText.textContent = '剪映工程生成失败: ' + (d.error || '未知错误');
                        }
                    }
                } catch (e) {}
            }, 2000);
        };
    } catch (err) {
        progressText.textContent = '启动失败: ' + err.message;
    }
}

async function checkExistingEditJobs() {
    try {
        const resp = await fetch('/api/auto-edits');
        const jobs = await resp.json();
        if (!jobs.length) return;

        // Find the most recent job
        const latest = jobs[jobs.length - 1];

        if (latest.status === 'processing') {
            // Reconnect to in-progress job
            const progressContainer = document.getElementById('editProgress');
            const progressFill = document.getElementById('editProgressFill');
            const progressText = document.getElementById('editProgressText');

            progressContainer.style.display = 'block';
            progressFill.style.width = (latest.progress * 100) + '%';
            progressFill.classList.add('indeterminate');
            progressText.textContent = '剪辑进行中（已恢复连接）...';

            // Try SSE first, fall back to polling
            const evtSource = new EventSource(`/api/auto-edit/${latest.job_id}/status`);
            evtSource.addEventListener('progress', e => {
                const evt = JSON.parse(e.data);
                progressFill.classList.remove('indeterminate');
                progressFill.style.width = (evt.progress * 100) + '%';
                progressText.textContent = evt.message;
                if (evt.done) {
                    evtSource.close();
                    renderEditResult(latest.job_id, evt.result);
                }
                if (evt.error) {
                    evtSource.close();
                    progressText.textContent = '剪映工程生成失败: ' + evt.message;
                    if (evt.result) renderEditResult(latest.job_id, evt.result);
                }
            });
            evtSource.onerror = () => {
                // SSE failed (queue exhausted), poll instead
                evtSource.close();
                pollEditJob(latest.job_id);
            };

        } else if (latest.status === 'completed' || latest.status === 'completed_with_warning') {
            const detail = await fetch(`/api/auto-edit/${latest.job_id}`).then(r => r.json());
            renderEditResult(latest.job_id, detail);

        } else if (latest.status === 'error') {
            const detail = await fetch(`/api/auto-edit/${latest.job_id}`).then(r => r.json());
            renderEditResult(latest.job_id, detail);
        }
    } catch (e) {
        // ignore
    }
}

async function pollEditJob(jobId) {
    const progressFill = document.getElementById('editProgressFill');
    const progressText = document.getElementById('editProgressText');
    progressFill.classList.add('indeterminate');

    const poll = setInterval(async () => {
        try {
            const resp = await fetch(`/api/auto-edit/${jobId}`);
            const data = await resp.json();

            if (data.status === 'completed' || data.status === 'completed_with_warning') {
                clearInterval(poll);
                progressFill.classList.remove('indeterminate');
                progressFill.style.width = '100%';
                progressText.textContent = data.warning ? '草稿已生成，自动打开失败' : '剪映工程已生成！';
                renderEditResult(jobId, data);
            } else if (data.status === 'error') {
                clearInterval(poll);
                progressFill.classList.remove('indeterminate');
                progressText.textContent = '剪映工程生成失败: ' + (data.error || '未知错误');
            } else {
                progressFill.style.width = (data.progress * 100) + '%';
                progressText.textContent = '剪辑进行中...';
            }
        } catch (e) {}
    }, 5000);
}

function renderEditResult(jobId, result) {
    const resultDiv = document.getElementById('editResult');
    const progressContainer = document.getElementById('editProgress');
    progressContainer.style.display = 'none';
    const matches = (result && (result.matches || result.match_results)) || [];

    let html = '';

    if (result && (result.status === 'completed' || result.status === 'completed_with_warning')) {
        const draftDir = result.draft_dir ? `<p style="color:#aaa;margin:8px 0;word-break:break-all">草稿目录：${result.draft_dir}</p>` : '';
        const warningHtml = result.warning
            ? `<p style="color:#f6c177;margin:8px 0">${result.warning}</p>`
            : '<p style="color:#aaa;margin:8px 0">已尝试自动打开剪映，请在剪映中查看并精调</p>';
        html += `
        <div class="download-area" style="background:#1a2e1a;border-color:#2a4a2a">
            <h3 style="color:#6ecf8e">剪映工程已生成！</h3>
            ${warningHtml}
            ${draftDir}
            <div style="display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin-top:12px">
                <a href="/api/auto-edit/${jobId}/capcut" class="btn" style="background:#6c5ce7;color:#fff;padding:12px 24px;font-size:14px;border-radius:8px;text-decoration:none" download>
                    下载剪映工程
                </a>
            </div>
        </div>`;
    }

    if (matches.length > 0) {
        html += '<div class="match-results"><h3 style="margin-bottom:12px;color:#a29bfe">匹配结果</h3>';
        for (const m of matches) {
            html += `
            <div class="match-item">
                <span class="match-shot">镜头 #${m.shot_number}</span>
                <span class="match-arrow">→</span>
                <span class="match-material">${m.material || '素材 ' + m.material_index}</span>
                <span class="match-reason">${m.reason || ''}</span>
            </div>`;
        }
        html += '</div>';
    }

    if (result && result.status === 'error') {
        const diagnostics = result.diagnostics || {};
        const detailLines = [
            result.stage ? `阶段：${result.stage}` : '',
            result.last_mcp_endpoint ? `接口：${result.last_mcp_endpoint}` : '',
            result.last_material ? `素材：${result.last_material}` : '',
            diagnostics.raw_error ? `底层错误：${diagnostics.raw_error}` : ''
        ].filter(Boolean);
        html += `<div style="color:#ff6b6b;padding:20px;background:#2a1a1a;border-radius:12px;margin-top:20px">
            <strong>错误：</strong>${result.error}
            ${detailLines.length ? `<div style="margin-top:10px;color:#ffb4b4;white-space:pre-wrap">${detailLines.join('\n')}</div>` : ''}
        </div>`;
    }

    resultDiv.innerHTML = html;
}
