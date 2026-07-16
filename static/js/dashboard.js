window.dashboardApp = function dashboardApp() {
  return {
    gap: [],
    loading: false,
    uploadCategory: 'openai',
    keysText: '',
    uploading: false,
    uploadFeedback: '',
    uploadFeedbackType: '',

    get sortedGap() {
      return [...this.gap].sort((a, b) => (b.gap_rpm ?? 0) - (a.gap_rpm ?? 0));
    },

    fmt(n) {
      if (n === null || n === undefined || n === '') return '—';
      const num = Number(n);
      if (Number.isNaN(num)) return String(n);
      return num.toLocaleString('en-US');
    },

    async init() {
      await this.fetchGap();
    },

    async fetchGap() {
      this.loading = true;
      try {
        const res = await KeysUI.apiFetch('/api/yunwu/gap');
        if (!res) return;
        const data = await res.json();
        this.gap = Array.isArray(data) ? data : [];
        if (this.gap.length === 0) KeysUI.showToast('当前无缺口数据', 'success');
      } finally {
        this.loading = false;
      }
    },

    async submitUpload() {
      this.uploadFeedback = '';
      this.uploadFeedbackType = '';
      if (!this.keysText.trim()) {
        this.uploadFeedback = '请输入至少一个 Key，每行一个。';
        this.uploadFeedbackType = 'error';
        KeysUI.showToast('请输入至少一个 Key', 'error');
        return;
      }
      if (this.uploading) return;
      this.uploading = true;
      try {
        const res = await KeysUI.apiFetch('/api/yunwu/upload', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ category: this.uploadCategory, keys_text: this.keysText }),
        });
        if (!res) {
          this.uploadFeedback = '推送失败，请检查输入后重试。';
          this.uploadFeedbackType = 'error';
          return;
        }
        const data = await res.json();
        if (!data.ok) {
          this.uploadFeedback = '推送失败，请检查输入后重试。';
          this.uploadFeedbackType = 'error';
          KeysUI.showToast('推送失败，请重试', 'error');
          return;
        }
        this.uploadFeedback = `推送完成：共 ${data.total}，成功 ${data.success}，跳过 ${data.skipped}，无效 ${data.invalid}，失败 ${data.failed}；Tag ${data.tag}`;
        this.uploadFeedbackType = 'success';
        this.keysText = '';
        KeysUI.showToast(`成功推送 ${data.success} 个 Key`);
      } catch {
        this.uploadFeedback = '推送失败，请稍后重试。';
        this.uploadFeedbackType = 'error';
        KeysUI.showToast('推送失败，请稍后重试', 'error');
      } finally {
        this.uploading = false;
      }
    },
  };
};

document.addEventListener('alpine:init', () => {
  Alpine.data('dashboardApp', window.dashboardApp);
});
