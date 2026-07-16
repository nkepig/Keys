window.keysApp = function keysApp() {
  const TIER_BADGE = {
    '1': 'badge badge-neutral',
    '2': 'badge badge-neutral',
    '3': 'badge badge-neutral',
    '4': 'badge badge-neutral',
    '5': 'badge',
  };

  return {
    keys: [],
    loading: false,
    searchQuery: '',
    providerFilter: 'all',
    statusFilter: '200',
    sortBy: 'id',
    sortDesc: true,
    currentPage: 1,
    pageSize: 20,
    copiedKey: null,
    refreshDone: false,
    batchVerifyModel: '',
    activeActionMenu: null,
    batchRunning: false,

    showModal: false,
    isEditing: false,
    saving: false,
    form: { id: null, provider: '', key: '', origin: '', tier: '', notes: '' },

    showUploadModal: false,
    uploadForm: { keys: '', origin: '' },
    uploading: false,
    uploadPhase: 'input',
    uploadProgress: 0,
    uploadResults: null,
    uploadFiltered: [],
    uploadingTotal: 0,
    showSection: { success: true, failed: true, filtered: false },

    showUrlBatchModal: false,
    urlBatchForm: { urlsText: '' },
    urlBatchScanning: false,
    urlBatchPhase: 'input',
    urlBatchProgress: 0,
    urlBatchResults: null,
    urlBatchTotal: 0,
    urlBatchScannedUrls: 0,
    urlBatchSection: { success: true, failed: true },

    modelsModal: { show: false, list: [], provider: '' },
    verifyResultModal: { show: false, key: null },
    verifyingMap: {},
    verifyCache: {},
    tooltip: { show: false, text: '', x: 0, y: 0 },

    get verifyKeySafe() {
      return this.verifyResultModal.key || {};
    },

    get validCount() {
      return this.keys.filter((k) => k.status_code === 200).length;
    },
    get invalidCount() {
      return this.keys.filter((k) => k.status_code != null && k.status_code !== 200).length;
    },
    get providerOptions() {
      const s = new Set(this.keys.map((k) => k.provider).filter(Boolean));
      ['OpenAI', 'Anthropic', 'Google', 'OpenRouter'].forEach((p) => s.add(p));
      return [...s].sort((a, b) => {
        const order = { Google: 0, OpenAI: 1, Anthropic: 2, OpenRouter: 3 };
        return (order[a] ?? 99) - (order[b] ?? 99) || a.localeCompare(b);
      });
    },
    get uploadLineCount() {
      return this.uploadForm.keys.split('\n').filter((l) => l.trim()).length;
    },
    get uploadDupCount() {
      const lines = this.uploadForm.keys.split('\n').map((l) => l.trim()).filter(Boolean);
      const seen = new Set();
      let dupes = 0;
      for (const l of lines) {
        if (seen.has(l)) dupes++;
        else seen.add(l);
      }
      return dupes;
    },
    get successResults() {
      return (this.uploadResults?.results || []).filter((r) => r.saved);
    },
    get failedResults() {
      return (this.uploadResults?.results || []).filter((r) => !r.saved);
    },
    get processedKeys() {
      let result = this.keys;
      if (this.providerFilter !== 'all') {
        result = result.filter((k) => k.provider === this.providerFilter);
      }
      if (this.statusFilter !== 'all') {
        const pinnedIds = new Set(Object.keys(this.verifyCache).map(Number));
        if (this.statusFilter === 'other') {
          result = result.filter(
            (k) =>
              pinnedIds.has(k.id) ||
              (k.status_code != null && ![200, 400, 401, 403, 429, 503].includes(k.status_code)),
          );
        } else {
          const code = parseInt(this.statusFilter, 10);
          result = result.filter((k) => pinnedIds.has(k.id) || k.status_code === code);
        }
      }
      const q = this.searchQuery.trim().toLowerCase();
      if (q) {
        result = result.filter(
          (k) =>
            String(k.id).includes(q) ||
            (k.key || '').toLowerCase().includes(q) ||
            (k.origin || '').toLowerCase().includes(q) ||
            (k.notes || '').toLowerCase().includes(q),
        );
      }
      return [...result].sort((a, b) => {
        let av;
        let bv;
        let cmp;
        switch (this.sortBy) {
          case 'provider':
            cmp = (a.provider || '').localeCompare(b.provider || '');
            return this.sortDesc ? -cmp : cmp;
          case 'key':
            cmp = (a.key || '').localeCompare(b.key || '');
            return this.sortDesc ? -cmp : cmp;
          case 'origin':
            cmp = (a.origin || '').localeCompare(b.origin || '');
            return this.sortDesc ? -cmp : cmp;
          case 'tier':
            av = parseFloat(a.tier) || 9999;
            bv = parseFloat(b.tier) || 9999;
            break;
          case 'status_code':
            av = a.status_code ?? -1;
            bv = b.status_code ?? -1;
            break;
          case 'models':
            av = this.modelsCount(a.models);
            bv = this.modelsCount(b.models);
            break;
          case 'create_time':
            av = a.create_time ? new Date(a.create_time).getTime() : 0;
            bv = b.create_time ? new Date(b.create_time).getTime() : 0;
            break;
          default:
            av = a.id || 0;
            bv = b.id || 0;
        }
        return this.sortDesc ? bv - av : av - bv;
      });
    },
    get totalPages() {
      return Math.max(1, Math.ceil(this.processedKeys.length / this.pageSize));
    },
    get paginatedKeys() {
      return this.processedKeys.slice(
        (this.currentPage - 1) * this.pageSize,
        this.currentPage * this.pageSize,
      );
    },
    get paginatedDisplayRows() {
      return this.paginatedKeys.map((k) =>
        this.verifyCache[k.id] != null ? { ...k, ...this.verifyCache[k.id] } : k,
      );
    },
    get displayPages() {
      const t = this.totalPages;
      const c = this.currentPage;
      if (t <= 7) return Array.from({ length: t }, (_, i) => i + 1);
      if (c <= 4) return [1, 2, 3, 4, 5, '…', t];
      if (c >= t - 3) return [1, '…', t - 4, t - 3, t - 2, t - 1, t];
      return [1, '…', c - 1, c, c + 1, '…', t];
    },
    get urlBatchLineCount() {
      return this.urlBatchForm.urlsText.split('\n').filter((l) => l.trim()).length;
    },
    get urlBatchSuccess() {
      return (this.urlBatchResults?.results || []).filter((r) => r.saved);
    },
    get urlBatchFailed() {
      return (this.urlBatchResults?.results || []).filter((r) => !r.saved);
    },
    get rangeStart() {
      if (!this.processedKeys.length) return 0;
      return (this.currentPage - 1) * this.pageSize + 1;
    },
    get rangeEnd() {
      return Math.min(this.currentPage * this.pageSize, this.processedKeys.length);
    },

    init() {
      this.$watch('totalPages', (v) => {
        if (this.currentPage > v) this.currentPage = v;
      });
      this.$watch('searchQuery', () => { this.currentPage = 1; });
      this.$watch('providerFilter', () => { this.currentPage = 1; });
      this.$watch('statusFilter', () => { this.currentPage = 1; });
      this.$watch('pageSize', () => { this.currentPage = 1; });

      const onDocClick = (e) => {
        if (!this.activeActionMenu) return;
        if (e.target.closest('[data-action-menu]')) return;
        this.activeActionMenu = null;
      };
      const onKey = (e) => {
        if (e.key !== 'Escape') return;
        if (this.saving || this.uploadPhase === 'uploading' || this.urlBatchPhase === 'scanning') return;
        if (this.showModal) this.closeModal();
        else if (this.showUploadModal) this.closeUploadModal();
        else if (this.showUrlBatchModal) this.closeUrlBatchModal();
        else if (this.modelsModal.show) this.modelsModal.show = false;
        else if (this.verifyResultModal.show) this.closeVerifyResult();
        else if (this.activeActionMenu) this.activeActionMenu = null;
      };
      document.addEventListener('click', onDocClick);
      document.addEventListener('keydown', onKey);
      this._cleanup = () => {
        document.removeEventListener('click', onDocClick);
        document.removeEventListener('keydown', onKey);
      };
      this.fetchKeys();
    },

    destroy() {
      if (this._cleanup) this._cleanup();
    },

    showTooltipAt(e, text) {
      if (!text) return;
      const rect = e.currentTarget.getBoundingClientRect();
      this.tooltip.x = rect.left + rect.width / 2;
      this.tooltip.y = rect.top;
      this.tooltip.text = text;
      this.tooltip.show = true;
    },
    hideTooltip() {
      this.tooltip.show = false;
    },

    maskKey(k) {
      if (!k) return '—';
      if (k.length <= 14) return k;
      return k.slice(0, 6) + '••••••' + k.slice(-6);
    },
    isUrl(s) {
      return s && /^https?:\/\//.test(s);
    },
    truncateUrl(url) {
      try {
        const u = new URL(url);
        return u.host + (u.pathname !== '/' ? u.pathname : '');
      } catch {
        return url.length > 28 ? url.slice(0, 28) + '…' : url;
      }
    },
    formatTier(t, provider) {
      if (!t) return '';
      if (provider === 'OpenRouter') return t;
      return /^\d+(\.\d+)?$/.test(String(t)) ? `T${t}` : t;
    },
    tierBadgeClass(t) {
      if (String(t) === '5') return 'badge bg-ink text-white border-ink';
      return TIER_BADGE[String(t)] ?? 'badge badge-neutral';
    },
    providerBadgeClass(provider) {
      const p = String(provider || '').toLowerCase();
      if (p.includes('openai')) return 'badge badge-provider-openai';
      if (p.includes('anthropic') || p.includes('claude')) return 'badge badge-provider-anthropic';
      if (p.includes('google') || p.includes('gemini')) return 'badge badge-provider-google';
      if (p.includes('openrouter')) return 'badge badge-provider-openrouter';
      if (p.includes('aws') || p.includes('bedrock')) return 'badge badge-provider-aws';
      if (p.includes('azure')) return 'badge badge-provider-azure';
      if (provider) return 'badge badge-provider-default';
      return 'badge badge-neutral';
    },
    statusCodeClass(code) {
      if (code === 200) return 'badge badge-ok';
      if (code === 400 || code === 401 || code === 403) return 'badge badge-bad';
      if (code === 404) return 'badge badge-neutral';
      if (code === 429) return 'badge badge-warn';
      if (code === 503) return 'badge badge-info';
      if (code != null) return 'badge badge-neutral';
      return 'badge badge-neutral';
    },
    modelsCount(str) {
      if (!str) return 0;
      try {
        return JSON.parse(str).length;
      } catch {
        return 0;
      }
    },
    sortAria(col) {
      if (this.sortBy !== col) return 'none';
      return this.sortDesc ? 'descending' : 'ascending';
    },
    formatTime(s) {
      if (!s) return '—';
      try {
        const d = new Date(s);
        const pad = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
      } catch {
        return String(s).slice(0, 16);
      }
    },
    fmtVerifyBody(body) {
      if (body == null) return '';
      if (typeof body === 'string') return body;
      try {
        return JSON.stringify(body, null, 2);
      } catch {
        return String(body);
      }
    },
    verifyStatusLabel(code) {
      if (code === 200) return '有效';
      if (code === 429) return '额度不足 / 速率限制';
      if (code === 503) return '服务不可用 (503)';
      if (code === 400) return '错误请求 (400)';
      if (code === 401) return '未授权 (401)';
      if (code === 403) return '禁止访问 (403)';
      return `状态码 ${code}`;
    },

    toggleSort(col) {
      if (this.sortBy === col) this.sortDesc = !this.sortDesc;
      else {
        this.sortBy = col;
        this.sortDesc = true;
      }
    },
    toggleActionMenu(menuName) {
      this.activeActionMenu = this.activeActionMenu === menuName ? null : menuName;
    },
    closeActionMenu() {
      this.activeActionMenu = null;
    },
    async runBatchAction(action) {
      this.closeActionMenu();
      await action.call(this);
    },

    async copyCell(text, id) {
      if (!text || text === '—') return;
      try {
        await navigator.clipboard.writeText(text);
        this.copiedKey = id;
        KeysUI.showToast('已复制', 'copy', 1500);
        setTimeout(() => {
          this.copiedKey = null;
        }, 1200);
      } catch {
        KeysUI.showToast('复制失败，请手动复制', 'error');
      }
    },
    async copyCurrentPageKeys() {
      const rows = this.paginatedDisplayRows;
      if (!rows || rows.length === 0) {
        KeysUI.showToast('当前页没有记录', 'error');
        return;
      }
      const lines = rows.map((k) => k && k.key).filter(Boolean);
      if (!lines.length) {
        KeysUI.showToast('没有可复制的内容', 'error');
        return;
      }
      try {
        await navigator.clipboard.writeText(lines.join('\n'));
        KeysUI.showToast(`已复制 ${lines.length} 条（一行一条）`, 'success', 2200);
      } catch {
        KeysUI.showToast('复制失败', 'error');
      }
    },

    openModelsModal(item) {
      try {
        this.modelsModal.list = JSON.parse(item.models || '[]');
      } catch {
        this.modelsModal.list = [];
      }
      this.modelsModal.provider = item.provider;
      this.modelsModal.show = true;
    },

    async fetchKeyRow(id) {
      const res = await KeysUI.apiFetch(`/api/keys/${id}`);
      return res ? await res.json() : null;
    },
    async updateRow(id) {
      const row = await this.fetchKeyRow(id);
      if (!row) return;
      const idx = this.keys.findIndex((k) => k.id === id);
      if (idx !== -1) this.keys[idx] = row;
      else this.keys.unshift(row);
    },
    async fetchKeys() {
      this.loading = true;
      try {
        const res = await KeysUI.apiFetch('/api/keys');
        if (res) {
          this.keys = await res.json();
          this.verifyCache = {};
        }
      } finally {
        this.loading = false;
      }
    },
    async doRefresh() {
      this.refreshDone = false;
      await this.fetchKeys();
      this.refreshDone = true;
      setTimeout(() => {
        this.refreshDone = false;
      }, 1800);
    },

    findKeyById(id) {
      const nid = Number(id);
      return (
        this.verifyCache[id] ||
        this.verifyCache[nid] ||
        this.keys.find((k) => k.id === nid || k.id === id) ||
        this.paginatedDisplayRows.find((k) => k.id === nid || k.id === id) ||
        null
      );
    },
    openModal(editMode, item = null) {
      this.isEditing = !!editMode;
      const next =
        editMode && item
          ? {
              id: item.id,
              provider: item.provider || '',
              key: item.key || '',
              origin: item.origin || '',
              tier: item.tier != null ? String(item.tier) : '',
              notes: item.notes || '',
            }
          : { id: null, provider: '', key: '', origin: '', tier: '', notes: '' };
      this.form.id = next.id;
      this.form.provider = next.provider;
      this.form.key = next.key;
      this.form.origin = next.origin;
      this.form.tier = next.tier;
      this.form.notes = next.notes;
      this.showModal = true;
    },
    openEdit(id) {
      const row = this.findKeyById(id);
      if (!row) {
        KeysUI.showToast('未找到该密钥', 'error');
        return;
      }
      this.openModal(true, row);
    },
    startVerify(id) {
      const row = this.findKeyById(id);
      if (!row) {
        KeysUI.showToast('未找到该密钥', 'error');
        return;
      }
      const model = (this.batchVerifyModel || '').trim();
      this.verifyKey(row, { model: model || null });
    },
    closeModal() {
      if (this.saving) return;
      this.showModal = false;
    },
    closeVerifyResult() {
      this.verifyResultModal.show = false;
      setTimeout(() => {
        if (!this.verifyResultModal.show) this.verifyResultModal.key = null;
      }, 280);
    },
    async saveKey() {
      if (this.saving) return;
      if (!this.form.key?.trim()) {
        KeysUI.showToast('密钥不能为空', 'error');
        return;
      }
      this.saving = true;
      const H = { 'Content-Type': 'application/json' };
      try {
        if (this.isEditing) {
          const res = await KeysUI.apiFetch(`/api/keys/${this.form.id}`, {
            method: 'PATCH',
            headers: H,
            body: JSON.stringify({
              provider: this.form.provider?.trim() || null,
              key: this.form.key.trim(),
              origin: this.form.origin?.trim() || null,
              tier: this.form.tier || null,
              notes: this.form.notes?.trim() || null,
            }),
          });
          if (res) {
            const updated = await res.json();
            const idx = this.keys.findIndex((k) => k.id === this.form.id);
            if (idx !== -1) this.keys.splice(idx, 1, updated);
            delete this.verifyCache[this.form.id];
            this.verifyCache = { ...this.verifyCache };
            this.showModal = false;
            KeysUI.showToast('修改已保存');
          }
        } else {
          const res = await KeysUI.apiFetch('/api/keys', {
            method: 'POST',
            headers: H,
            body: JSON.stringify({
              key: this.form.key.trim(),
              origin: this.form.origin?.trim() || null,
              notes: this.form.notes?.trim() || null,
            }),
          });
          if (res) {
            const data = await res.json();
            this.showModal = false;
            KeysUI.showToast('凭证已录入，校验完成');
            if (data.id) await this.updateRow(data.id);
          }
        }
      } finally {
        this.saving = false;
      }
    },
    async deleteKey(id) {
      if (!confirm('确认删除此凭证？')) return;
      const res = await KeysUI.apiFetch(`/api/keys/${id}`, { method: 'DELETE' });
      if (res) {
        this.keys = this.keys.filter((k) => k.id !== id);
        KeysUI.showToast('已删除');
      }
    },
    async verifyKey(item, options = {}) {
      if (this.verifyingMap[item.id]) return;
      const model = typeof options.model === 'string' ? options.model.trim() : '';
      const silent = options.silent === true;
      this.verifyingMap = { ...this.verifyingMap, [item.id]: true };
      try {
        const payload = {};
        if (model) payload.model = model;
        const res = await KeysUI.apiFetch(`/api/keys/${item.id}/verify`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!res) return;
        const updatedKey = await res.json();
        this.verifyCache = { ...this.verifyCache, [item.id]: updatedKey };
        if (!silent) {
          this.verifyResultModal.key = updatedKey;
          this.verifyResultModal.show = true;
        }
      } finally {
        const next = { ...this.verifyingMap };
        delete next[item.id];
        this.verifyingMap = next;
      }
    },
    async batchVerifyCurrentPage() {
      if (this.batchRunning) return;
      if (this.paginatedDisplayRows.length === 0) return;
      this.batchRunning = true;
      try {
        const items = this.paginatedDisplayRows.filter(
          (item) => item && item.id != null && !this.verifyingMap[item.id],
        );
        if (items.length === 0) return;
        const selectedModel = this.batchVerifyModel.trim();
        await Promise.allSettled(
          items.map((item) => this.verifyKey(item, { model: selectedModel || null, silent: true })),
        );
        KeysUI.showToast(
          selectedModel ? `本页批量校验完成（模型：${selectedModel}）` : '本页批量校验完成',
        );
      } finally {
        this.batchRunning = false;
      }
    },

    openUploadModal() {
      this.closeActionMenu();
      this.uploadPhase = 'input';
      this.uploadResults = null;
      this.uploadFiltered = [];
      this.showUploadModal = true;
    },
    closeUploadModal() {
      if (this.uploadPhase === 'uploading') return;
      this.showUploadModal = false;
      this.uploadForm.keys = '';
      this.uploadForm.origin = '';
      this.uploadPhase = 'input';
      this.uploadResults = null;
      this.uploadFiltered = [];
    },
    resetUpload() {
      this.uploadForm.keys = '';
      this.uploadForm.origin = '';
      this.uploadPhase = 'input';
      this.uploadResults = null;
      this.uploadFiltered = [];
    },
    async doUpload() {
      if (this.uploading) return;
      const rawLines = this.uploadForm.keys.split('\n').map((l) => l.trim()).filter(Boolean);
      const seen = new Set();
      const deduped = [];
      const dupes = [];
      for (const line of rawLines) {
        if (seen.has(line)) dupes.push(line);
        else {
          seen.add(line);
          deduped.push(line);
        }
      }
      this.uploadFiltered = dupes;
      if (!deduped.length) {
        KeysUI.showToast('没有有效的密钥', 'error');
        return;
      }
      this.uploading = true;
      this.uploadingTotal = deduped.length;
      this.uploadProgress = 0;
      this.uploadPhase = 'uploading';
      const estimated = deduped.length * 1800;
      const startTime = Date.now();
      const timer = setInterval(() => {
        this.uploadProgress = Math.min(92, ((Date.now() - startTime) / estimated) * 100);
      }, 120);
      try {
        const res = await KeysUI.apiFetch('/api/keys/upload', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            keys: deduped.join('\n'),
            origin: this.uploadForm.origin?.trim() || null,
            concurrent: 10,
          }),
        });
        clearInterval(timer);
        this.uploadProgress = 100;
        await new Promise((r) => setTimeout(r, 400));
        if (res) {
          this.uploadResults = await res.json();
          this.uploadPhase = 'done';
          this.uploading = false;
          this.showSection.success = true;
          this.showSection.failed = true;
          this.showSection.filtered = false;
          this.fetchKeys();
        } else {
          this.uploadPhase = 'input';
        }
      } catch {
        clearInterval(timer);
        this.uploadPhase = 'input';
      } finally {
        this.uploading = false;
      }
    },

    openUrlBatchModal() {
      this.closeActionMenu();
      this.urlBatchPhase = 'input';
      this.urlBatchResults = null;
      this.urlBatchForm.urlsText = '';
      this.urlBatchScannedUrls = 0;
      this.showUrlBatchModal = true;
    },
    closeUrlBatchModal() {
      if (this.urlBatchPhase === 'scanning') return;
      this.showUrlBatchModal = false;
    },
    closeUrlBatchModalAndRefresh() {
      this.showUrlBatchModal = false;
      this.fetchKeys();
    },
    resetUrlBatch() {
      this.urlBatchForm.urlsText = '';
      this.urlBatchPhase = 'input';
      this.urlBatchResults = null;
      this.urlBatchScannedUrls = 0;
    },
    async doUrlBatch() {
      if (this.urlBatchScanning) return;
      const lines = this.urlBatchForm.urlsText.split('\n').map((l) => l.trim()).filter(Boolean);
      if (!lines.length) {
        KeysUI.showToast('请输入至少一个 URL', 'error');
        return;
      }
      this.urlBatchScanning = true;
      this.urlBatchPhase = 'scanning';
      this.urlBatchProgress = 0;
      this.urlBatchTotal = lines.length;
      this.urlBatchScannedUrls = lines.length;
      const estimated = lines.length * 2500;
      const startTime = Date.now();
      const timer = setInterval(() => {
        this.urlBatchProgress = Math.min(92, ((Date.now() - startTime) / estimated) * 100);
      }, 120);
      try {
        const res = await KeysUI.apiFetch('/api/keys/batch_urls', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ urls_text: this.urlBatchForm.urlsText }),
        });
        clearInterval(timer);
        this.urlBatchProgress = 100;
        await new Promise((r) => setTimeout(r, 400));
        if (res) {
          this.urlBatchResults = await res.json();
          this.urlBatchPhase = 'done';
          this.urlBatchScanning = false;
          this.urlBatchSection.success = true;
          this.urlBatchSection.failed = true;
          this.fetchKeys();
        } else {
          this.urlBatchPhase = 'input';
        }
      } catch {
        clearInterval(timer);
        this.urlBatchPhase = 'input';
      } finally {
        this.urlBatchScanning = false;
      }
    },
  };
};

document.addEventListener('alpine:init', () => {
  Alpine.data('keysApp', window.keysApp);
});
