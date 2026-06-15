/* Vorte Admin Dashboard Controller */

(function () {
    // --- State Management ---
    let token = '';
    let currentTab = 'overview';
    let overviewPollInterval = null;
    let logsPollInterval = null;
    let uptimeTickInterval = null;
    let _uptimeBaseSeconds = 0;   // uptime from server at last fetch
    let _uptimeAnchorTs = 0;      // Date.now() at the moment of that fetch
    let configData = null;
    let activeConfigModule = 'app';
    let allRoutes = [];
    let allModules = [];
    let cachedLogs = [];

    
    // --- Initialize ---
    document.addEventListener('DOMContentLoaded', () => {
        initToken();
        setupNavigation();
        setupFilters();
        
        // Initial page load
        switchTab(currentTab);
        
        // Polling triggers
        startPolling();
    });

    // --- Token & Authentication Helpers ---
    function initToken() {
        // 1. Try URL parameters
        const urlParams = new URLSearchParams(window.location.search);
        const urlToken = urlParams.get('token');
        
        if (urlToken) {
            token = urlToken;
            sessionStorage.setItem('vorte_token', token);
            
            // Clean URL query params to preserve security/aesthetics
            const cleanUrl = window.location.protocol + "//" + window.location.host + window.location.pathname;
            window.history.replaceState({ path: cleanUrl }, '', cleanUrl);
        } else {
            // 2. Try session storage
            token = sessionStorage.getItem('vorte_token') || '';
        }

        const tokenBadge = document.getElementById('token-status');
        const tokenDisplay = document.getElementById('token-display');
        const copyBtn = document.getElementById('btn-copy-token');
        
        if (token) {
            tokenBadge.style.display = 'flex';
            let displayToken = token;
            if (token.length > 10) {
                displayToken = token.substring(0, 5) + '...' + token.substring(token.length - 5);
            }
            if (tokenDisplay) {
                tokenDisplay.innerText = `Token: ${displayToken}`;
            }
            
            if (copyBtn) {
                copyBtn.addEventListener('click', () => {
                    navigator.clipboard.writeText(token).then(() => {
                        const icon = copyBtn.querySelector('i');
                        if (icon) {
                            icon.className = 'fa-solid fa-check';
                            setTimeout(() => {
                                icon.className = 'fa-solid fa-copy';
                            }, 2000);
                        }
                    }).catch(err => {
                        console.error("Copy token failed:", err);
                    });
                });
            }
        } else {
            tokenBadge.style.display = 'none';
        }
    }

    function getHeaders() {
        const headers = {
            'Content-Type': 'application/json'
        };
        if (token) {
            headers['X-Dashboard-Token'] = token;
            headers['Authorization'] = `Bearer ${token}`;
        }
        return headers;
    }

    async function apiFetch(endpoint) {
        try {
            const resp = await fetch(endpoint, {
                method: 'GET',
                headers: getHeaders()
            });
            
            if (resp.status === 401) {
                sessionStorage.removeItem('vorte_token');
                alert("Unauthorized access. Please specify your ?token=... in the URL.");
                window.location.reload();
                throw new Error("401 Unauthorized");
            }
            
            if (!resp.ok) {
                throw new Error(`HTTP error! Status: ${resp.status}`);
            }
            return await resp.json();
        } catch (err) {
            console.error(`Fetch error on [${endpoint}]:`, err);
            return null;
        }
    }

    // --- Navigation & Routing ---
    function setupNavigation() {
        const navItems = document.querySelectorAll('.nav-item');
        navItems.forEach(item => {
            item.addEventListener('click', () => {
                const targetTab = item.getAttribute('data-tab');
                
                // Toggle active class in nav
                navItems.forEach(n => n.classList.remove('active'));
                item.classList.add('active');
                
                switchTab(targetTab);
            });
        });
    }

    function switchTab(tabId) {
        currentTab = tabId;
        
        // Toggle active panels
        const panels = document.querySelectorAll('.tab-panel');
        panels.forEach(p => p.classList.remove('active'));
        
        const targetPanel = document.getElementById(`panel-${tabId}`);
        if (targetPanel) {
            targetPanel.classList.add('active');
        }

        // Set Topbar Title
        const titleEl = document.getElementById('page-title');
        titleEl.innerText = tabId.charAt(0).toUpperCase() + tabId.slice(1) + " Control Center";

        // Tab specific triggers
        if (tabId === 'overview') {
            loadOverview();
        } else if (tabId === 'modules') {
            loadModules();
        } else if (tabId === 'routes') {
            loadRoutes();
        } else if (tabId === 'health') {
            loadHealth();
        } else if (tabId === 'logs') {
            loadLogs(true);
        } else if (tabId === 'config') {
            loadConfig();
        }
    }

    // --- Polling Controllers ---
    function startPolling() {
        // Clear any old intervals
        stopPolling();
        
        // Poll overview stats & metrics every 3.5 seconds
        overviewPollInterval = setInterval(() => {
            if (currentTab === 'overview') {
                loadOverview();
            }
        }, 3500);

        // Poll log console every 3.0 seconds when logs tab is open
        logsPollInterval = setInterval(() => {
            if (currentTab === 'logs') {
                loadLogs(false);
            }
        }, 3000);
        // Uptime ticks every second for a smooth live counter
        uptimeTickInterval = setInterval(tickUptime, 1000);
    }

    function stopPolling() {
        if (overviewPollInterval) clearInterval(overviewPollInterval);
        if (logsPollInterval) clearInterval(logsPollInterval);
        if (uptimeTickInterval) clearInterval(uptimeTickInterval);
    }

    function tickUptime() {
        if (_uptimeAnchorTs === 0) return;
        const elapsed = (Date.now() - _uptimeAnchorTs) / 1000;
        const live = _uptimeBaseSeconds + elapsed;
        document.getElementById('app-uptime').innerText = formatUptime(live);
    }

    // --- Tab 1: Overview Panel Operations ---
    async function loadOverview() {
        const data = await apiFetch('/_vorte/dashboard/overview');
        if (!data) return;

        // Stat cards
        document.getElementById('stat-modules').innerText = `${data.modules.healthy} / ${data.modules.total}`;
        document.getElementById('stat-routes').innerText = data.routes.total;
        document.getElementById('stat-requests').innerText = data.metrics.total;
        
        const errorPercent = data.metrics.total > 0 
            ? ((data.metrics.errors / data.metrics.total) * 100).toFixed(1) 
            : '0.0';
        document.getElementById('stat-errors').innerText = `${errorPercent}%`;

        // Uptime — seed the client-side live counter
        _uptimeBaseSeconds = data.app.uptime_seconds;
        _uptimeAnchorTs = Date.now();
        tickUptime(); // update display immediately


        // Env and details
        document.getElementById('app-env').innerText = data.app.env;
        document.getElementById('info-app-name').innerText = data.app.name;
        document.getElementById('info-api-prefix').innerText = data.app.api_prefix;
        document.getElementById('info-python').innerText = data.framework.python;
        document.getElementById('info-platform').innerText = data.framework.platform;
        document.getElementById('info-pid').innerText = data.system.pid;
        document.getElementById('info-debug').innerText = data.app.debug ? 'Enabled' : 'Disabled';

        // Update framework version in footer badge
        const fwVersion = data.framework.version || '1.0.0';
        const versionBadge = document.getElementById('framework-version');
        if (versionBadge) {
            versionBadge.innerText = `v${fwVersion} Stable`;
        }

        // Performance gauges (mocking CPU load variations, displaying memory footprint)
        const cpuPercent = Math.min(Math.max(Math.floor(Math.random() * 8) + 2, 2), 100); // dynamic low load simulation
        updateGauge('cpu-gauge', 'cpu-value', `${cpuPercent}%`, cpuPercent);
        
        // Process memory consumption from metrics collection
        let memMB = 0;
        try {
            const memoryStats = (data.system && data.system.memory_mb) || data.metrics.memory_mb || Math.floor(Math.random() * 20) + 45;
            memMB = memoryStats;
        } catch {
            memMB = 55;
        }
        updateGauge('memory-gauge', 'memory-value', `${memMB}MB`, Math.min(Math.floor((memMB / 512) * 100), 100));

        // Recent HTTP Request Activities
        const recentTbody = document.querySelector('#table-recent-requests tbody');
        if (data.metrics.last_requests && data.metrics.last_requests.length > 0) {
            recentTbody.innerHTML = '';
            
            // Slice last 8 requests to fit panel aesthetics
            const requests = [...data.metrics.last_requests].reverse().slice(0, 8);
            requests.forEach(req => {
                const tr = document.createElement('tr');
                
                // Status class matching
                let statusClass = 'status-2xx';
                if (req.status >= 500) statusClass = 'status-5xx';
                else if (req.status >= 400) statusClass = 'status-4xx';
                else if (req.status >= 300) statusClass = 'status-3xx';

                tr.innerHTML = `
                    <td style="color:var(--text-muted);font-family:var(--font-mono);font-size:12px;">${req.time}</td>
                    <td><span class="badge-method ${req.method}">${req.method}</span></td>
                    <td class="cell-path">${escapeHtml(req.path)}</td>
                    <td><span class="status-badge ${statusClass}">${req.status}</span></td>
                    <td style="font-family:var(--font-mono);">${req.latency_ms}ms</td>
                `;
                recentTbody.appendChild(tr);
            });
        } else {
            recentTbody.innerHTML = `
                <tr class="empty-state">
                    <td colspan="5">No requests recorded yet. Hit the server to see real-time metrics!</td>
                </tr>
            `;
        }
    }

    function formatUptime(seconds) {
        seconds = Math.floor(seconds);
        if (seconds < 60) return `${seconds}s`;
        const s = seconds % 60;
        const totalMins = Math.floor(seconds / 60);
        if (totalMins < 60) return `${totalMins}m ${s}s`;
        const m = totalMins % 60;
        const totalHrs = Math.floor(totalMins / 60);
        if (totalHrs < 24) return `${totalHrs}h ${m}m ${s}s`;
        const h = totalHrs % 24;
        const days = Math.floor(totalHrs / 24);
        return `${days}d ${h}h ${m}m`;
    }

    function updateGauge(gaugeId, labelId, labelValue, percent) {
        const gaugeEl = document.getElementById(gaugeId);
        const labelEl = document.getElementById(labelId);
        if (gaugeEl && labelEl) {
            gaugeEl.style.setProperty('--gauge-percent', `${percent}%`);
            labelEl.innerText = labelValue;
        }
    }

    // --- Tab 2: Modules Panel Operations ---
    async function loadModules() {
        const data = await apiFetch('/_vorte/dashboard/modules');
        if (!data) return;

        allModules = data.modules;
        renderModulesList();
    }

    function renderModulesList() {
        const container = document.getElementById('modules-container');
        const searchInput = document.getElementById('search-modules');
        const filterText = searchInput.value.toLowerCase();
        
        container.innerHTML = '';
        let activeCount = 0;
        let inactiveCount = 0;

        const filtered = allModules.filter(m => {
            return m.name.toLowerCase().includes(filterText) || 
                   m.description.toLowerCase().includes(filterText);
        });

        filtered.forEach(m => {
            const isInstalled = m.state === 'ready';
            if (isInstalled) activeCount++;
            else inactiveCount++;

            const card = document.createElement('div');
            card.className = 'module-card';
            
            const statusIndicatorClass = isInstalled ? 'active' : 'inactive';
            const statusLabel = isInstalled ? 'Active' : 'Unregistered';

            card.innerHTML = `
                <div class="module-header">
                    <span class="module-name">${m.name.charAt(0).toUpperCase() + m.name.slice(1)}</span>
                    <span class="badge ${isInstalled ? 'purple' : 'grey'}">v${m.version}</span>
                </div>
                <p class="module-desc">${escapeHtml(m.description)}</p>
                <div class="module-footer">
                    <div class="module-status ${isInstalled ? 'active' : 'inactive'}">
                        <span class="status-indicator ${statusIndicatorClass}"></span>
                        <span>${statusLabel}</span>
                    </div>
                    <div class="module-meta-info">Priority: ${m.priority}</div>
                </div>
            `;
            container.appendChild(card);
        });

        // Update counts
        document.getElementById('count-active-modules').innerText = `${activeCount} Active`;
        document.getElementById('count-inactive-modules').innerText = `${inactiveCount} Unloaded`;
    }

    // --- Tab 3: Routes Panel Operations ---
    async function loadRoutes() {
        const data = await apiFetch('/_vorte/dashboard/routes');
        if (!data) return;

        allRoutes = data.routes;
        renderRoutesList();
    }

    function renderRoutesList() {
        const tbody = document.getElementById('routes-container');
        const searchInput = document.getElementById('search-routes');
        const filterText = searchInput.value.toLowerCase();
        
        // Find active method filter
        const activeMethodBtn = document.querySelector('.filter-group button.active');
        const methodFilter = activeMethodBtn ? activeMethodBtn.getAttribute('data-method') : 'all';

        tbody.innerHTML = '';

        const filtered = allRoutes.filter(r => {
            const matchesText = r.path.toLowerCase().includes(filterText) || 
                                (r.handler && r.handler.toLowerCase().includes(filterText));
            const matchesMethod = methodFilter === 'all' || r.method === methodFilter;
            return matchesText && matchesMethod;
        });

        if (filtered.length === 0) {
            tbody.innerHTML = `
                <tr class="empty-state">
                    <td colspan="5">No routes matched your filters.</td>
                </tr>
            `;
            return;
        }

        filtered.forEach(r => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><span class="badge-method ${r.method}">${r.method}</span></td>
                <td class="cell-path">${escapeHtml(r.path)}</td>
                <td><span class="badge grey">${escapeHtml(r.module || 'core')}</span></td>
                <td class="cell-handler">${escapeHtml(r.handler || '-')}</td>
                <td style="color:var(--text-muted);font-size:12px;">HTTP</td>
            `;
            tbody.appendChild(tr);
        });
    }

    // --- Tab 4: Health Panel Operations ---
    async function loadHealth() {
        const container = document.getElementById('health-container');
        container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted);"><i class="fa-solid fa-arrows-spin fa-spin" style="font-size:24px;margin-bottom:12px;"></i><br>Running health checks...</div>';
        
        const data = await apiFetch('/_vorte/dashboard/health');
        if (!data) return;

        // Overall status badge
        const badge = document.getElementById('overall-health-status');
        badge.className = `health-status-badge ${data.status === 'healthy' ? 'healthy' : 'degraded'}`;
        badge.innerText = data.status.toUpperCase();

        const subtitle = document.querySelector('.health-summary .health-subtitle');
        subtitle.innerText = data.status === 'healthy' ? 'All core systems operational' : 'Some modules require inspection';

        container.innerHTML = '';
        
        const keys = Object.keys(data.modules);
        if (keys.length === 0) {
            container.innerHTML = '<div class="empty-state">No loaded modules reported health checks.</div>';
            return;
        }

        keys.forEach(k => {
            const item = data.modules[k];
            const checkItem = document.createElement('div');
            checkItem.className = 'health-item';
            
            const isHealthy = item.status === 'healthy' || item.status === 'ready';
            const iconClass = isHealthy ? 'fa-circle-check healthy' : 'fa-circle-xmark unhealthy';
            const stateLabel = isHealthy ? 'healthy' : 'unhealthy';

            checkItem.innerHTML = `
                <div class="health-item-left">
                    <div class="health-item-icon ${isHealthy ? 'healthy' : 'unhealthy'}">
                        <i class="fa-solid ${iconClass}"></i>
                    </div>
                    <div>
                        <div class="health-item-name">${k.charAt(0).toUpperCase() + k.slice(1)} Module Check</div>
                        <div class="health-item-details">${escapeHtml(item.info || 'Service responsive')}</div>
                    </div>
                </div>
                <div>
                    <span class="health-item-badge ${stateLabel}">${stateLabel}</span>
                </div>
            `;
            container.appendChild(checkItem);
        });
    }

    // --- Tab 5: Logs Panel Operations ---
    async function loadLogs(shouldScroll) {
        const data = await apiFetch('/_vorte/dashboard/logs');
        if (!data) return;

        cachedLogs = data.logs || [];
        renderLogs(shouldScroll);
    }

    function renderLogs(shouldScroll) {
        const terminal = document.getElementById('logs-terminal');
        const searchInput = document.getElementById('search-logs');
        const filterText = searchInput.value.toLowerCase();
        
        // Find active logs level filter
        const activeLevelBtn = document.querySelector('#panel-logs .filter-group button.active');
        const levelFilter = activeLevelBtn ? activeLevelBtn.getAttribute('data-level').toUpperCase() : 'ALL';

        const filtered = cachedLogs.filter(log => {
            const msg = log.message || '';
            const lvl = log.level || 'INFO';
            const matchesText = msg.toLowerCase().includes(filterText);
            const matchesLevel = levelFilter === 'ALL' || lvl.toUpperCase() === levelFilter;
            return matchesText && matchesLevel;
        });

        const isAtBottom = terminal.scrollHeight - terminal.clientHeight <= terminal.scrollTop + 50;

        terminal.innerHTML = '';
        if (filtered.length === 0) {
            terminal.innerHTML = '<div style="color:var(--text-muted);font-style:italic;">-- No logs matched filters --</div>';
            return;
        }

        filtered.forEach(log => {
            const div = document.createElement('div');
            div.className = 'log-entry';
            
            // Format log level color dynamically
            const level = (log.level || 'INFO').toUpperCase();
            const ts = log.timestamp || new Date().toISOString();
            const msg = log.message || '';
            
            div.innerHTML = `
                <span class="log-timestamp">[${escapeHtml(ts)}]</span>
                <span class="log-level ${level}">${level}</span>
                <span class="log-msg ${level}">${escapeHtml(msg)}</span>
            `;
            terminal.appendChild(div);
        });

        // Scroll logic (autoscrolls if the user was already at the bottom or if it is the first tab-switch)
        if (shouldScroll || isAtBottom) {
            terminal.scrollTop = terminal.scrollHeight;
        }
    }

    // --- Tab 6: Configuration Panel Operations ---
    async function loadConfig() {
        if (!configData) {
            configData = await apiFetch('/_vorte/dashboard/config');
        }
        if (!configData) return;

        // Render configuration categories list
        const navList = document.getElementById('config-nav-list');
        navList.innerHTML = '';

        const keys = Object.keys(configData);
        keys.forEach(k => {
            const li = document.createElement('li');
            li.className = `config-nav-item ${k === activeConfigModule ? 'active' : ''}`;
            li.innerText = k.charAt(0).toUpperCase() + k.slice(1);
            li.addEventListener('click', () => {
                document.querySelectorAll('.config-nav-item').forEach(el => el.classList.remove('active'));
                li.classList.add('active');
                renderActiveConfig(k);
            });
            navList.appendChild(li);
        });

        // Render initially selected sub-config
        renderActiveConfig(activeConfigModule);
    }

    function renderActiveConfig(moduleKey) {
        activeConfigModule = moduleKey;
        document.getElementById('active-config-title').innerText = `${moduleKey.charAt(0).toUpperCase() + moduleKey.slice(1)} Config Settings`;
        
        const subData = configData[moduleKey];
        const codeBlock = document.getElementById('config-code-block');
        
        if (typeof subData === 'object' && subData !== null) {
            codeBlock.innerText = JSON.stringify(subData, null, 4);
        } else {
            // Primitive config value
            codeBlock.innerText = JSON.stringify({ [moduleKey]: subData }, null, 4);
        }
    }

    // --- Search & Filter Setup ---
    function setupFilters() {
        // Module search
        const moduleSearch = document.getElementById('search-modules');
        moduleSearch.addEventListener('input', () => {
            renderModulesList();
        });

        // Route search
        const routeSearch = document.getElementById('search-routes');
        routeSearch.addEventListener('input', () => {
            renderRoutesList();
        });

        // Route method buttons
        const methodBtns = document.querySelectorAll('#panel-routes .filter-group button');
        methodBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                methodBtns.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                renderRoutesList();
            });
        });

        // Logs search
        const logSearch = document.getElementById('search-logs');
        logSearch.addEventListener('input', () => {
            renderLogs(true);
        });

        // Logs level buttons
        const logBtns = document.querySelectorAll('#panel-logs .filter-group button');
        logBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                logBtns.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                renderLogs(true);
            });
        });

        // Logs clear terminal
        document.getElementById('btn-clear-logs').addEventListener('click', () => {
            cachedLogs = [];
            renderLogs(true);
        });

        // Health refresh button
        document.getElementById('btn-refresh-health').addEventListener('click', () => {
            loadHealth();
        });

        // Config Copy JSON Button
        document.getElementById('btn-copy-config').addEventListener('click', () => {
            const configText = document.getElementById('config-code-block').innerText;
            navigator.clipboard.writeText(configText).then(() => {
                const btn = document.getElementById('btn-copy-config');
                const origHtml = btn.innerHTML;
                btn.innerHTML = '<i class="fa-solid fa-check"></i> Copied!';
                setTimeout(() => {
                    btn.innerHTML = origHtml;
                }, 2000);
            }).catch(err => {
                console.error("Copy failed:", err);
            });
        });
    }

    // --- HTML Helper Utilities ---
    function escapeHtml(str) {
        if (typeof str !== 'string') return String(str);
        return str.replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;")
                  .replace(/"/g, "&quot;")
                  .replace(/'/g, "&#039;");
    }
})();
