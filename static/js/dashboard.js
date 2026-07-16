window.dashboardApp = function dashboardApp() {
  return {
    gap: [],
    loading: false,
    uploadCategory: 'openai',
    keysText: '',
    uploading: false,
    uploadFeedback: '',
    uploadFeedbackType: '',

    channels: [],
    channelsLoading: false,
    tagFilter: '',

    get sortedGap() {
      return [...this.gap].sort((a, b) => (b.gap_rpm ?? 0) - (a.gap_rpm ?? 0));
    },

    get channelTags() {
      const tags = [...new Set(this.channels.map((c) => c.tag).filter((t) => t && t !== '-'))];
      return tags.sort().reverse();
    },

    get filteredChannels() {
      if (!this.tagFilter) return this.channels;
      return this.channels.filter((c) => c.tag === this.tagFilter);
    },

    fmt(n) {
      if (n === null || n === undefined || n === '') return '—';
      const num = Number(n);
      if (Number.isNaN(num)) return String(n);
      return num.toLocaleString('en-US');
    },

    fmtUsage(n) {
      const num = Number(n);
      if (!Number.isFinite(num)) return '$0.00';
      return (
        '$' +
        num.toLocaleString(undefined, {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })
      );
    },

    async init() {
      await Promise.all([this.fetchGap({ quiet: true }), this.fetchChannels({ quiet: true })]);
    },

    async fetchGap({ quiet = false } = {}) {
      this.loading = true;
      try {
        const res = await KeysUI.apiFetch('/api/yunwu/gap');
        if (!res) return;
        const data = await res.json();
        this.gap = Array.isArray(data) ? data : [];
        if (!quiet && this.gap.length === 0) KeysUI.showToast('当前无缺口数据', 'success');
      } finally {
        this.loading = false;
      }
    },

    async fetchChannels({ quiet = false } = {}) {
      this.channelsLoading = true;
      try {
        const res = await KeysUI.apiFetch('/api/yunwu/channels');
        if (!res) return;
        const data = await res.json();
        this.channels = Array.isArray(data.items) ? data.items : [];
        if (this.tagFilter && !this.channelTags.includes(this.tagFilter)) {
          this.tagFilter = '';
        }
        if (!quiet && this.channels.length === 0) KeysUI.showToast('暂无已推送 Key', 'success');
      } finally {
        this.channelsLoading = false;
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
        await this.fetchChannels({ quiet: true });
        if (data.tag) this.tagFilter = data.tag;
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
