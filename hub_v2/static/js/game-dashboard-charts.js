/**
 * Game Dashboard — Charts, history, and real-time projection.
 * Mixed into the gameDashboard() Alpine component via Object.assign.
 */
function gameDashboardCharts() {
  return {
    historyBucketKey(date) {
      const pad = (value) => String(value).padStart(2, '0');
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}`;
    },

    formatHistoryLabel(date) {
      return date.toLocaleString('pt-BR', {
        day: '2-digit',
        month: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      });
    },

    historyScopeLabel(visibleCards) {
      if (!visibleCards.length) return 'Sem dados';
      if (this.filterCity) return `Cidade: ${this.filterCity}`;
      if (visibleCards.length === 1) return visibleCards[0].player;
      if (this.filterAccount) return 'Conta filtrada';
      if (this.filterServer) return 'Servidor filtrado';
      return 'Geral';
    },

    historyState(metric) {
      const visibleCards = this.kpiCards();
      if (!visibleCards.length) {
        return {
          available: false,
          reason: 'Nenhuma conta visivel no filtro atual.',
          scopeLabel: 'Sem dados',
        };
      }

      const grouped = new Map();
      visibleCards.forEach((card) => {
        const points = this.historyMap[String(card.gameAccountId)] || [];
        const perCardBuckets = new Map();
        points.forEach((point) => {
          const parsed = new Date(point.captured_at);
          if (Number.isNaN(parsed.getTime())) return;
          const bucketKey = this.historyBucketKey(parsed);
          const existing = perCardBuckets.get(bucketKey);
          const candidate = {
            ts: parsed.getTime(),
            label: this.formatHistoryLabel(parsed),
            value: Number(point[metric] || 0),
          };
          if (!existing || candidate.ts > existing.ts) {
            perCardBuckets.set(bucketKey, candidate);
          }
        });

        perCardBuckets.forEach((point, bucketKey) => {
          const existing = grouped.get(bucketKey) || {
            ts: point.ts,
            label: point.label,
            value: 0,
          };
          existing.ts = Math.max(existing.ts, point.ts);
          existing.label = point.label;
          existing.value += point.value;
          grouped.set(bucketKey, existing);
        });
      });

      const points = Array.from(grouped.values())
        .sort((a, b) => a.ts - b.ts)
        .slice(-12);

      if (!points.length) {
        return {
          available: false,
          reason: 'Nao ha historico salvo para o filtro atual.',
          scopeLabel: this.historyScopeLabel(visibleCards),
        };
      }

      const values = points.map((point) => Math.round(point.value));
      return {
        available: true,
        scopeLabel: this.historyScopeLabel(visibleCards),
        first: values[0],
        last: values[values.length - 1],
        delta: values[values.length - 1] - values[0],
        labels: points.map((point) => point.label),
        values,
      };
    },

    destroyChart(name) {
      if (this._charts[name]) {
        this._charts[name].destroy();
        delete this._charts[name];
      }
    },

    renderHistoryChart(metric, canvasId, palette) {
      const canvas = document.getElementById(canvasId);
      const chartName = `${metric}-${canvasId}`;
      this.destroyChart(chartName);

      if (!canvas || !window.Chart) return;
      const state = this.historyState(metric);
      if (!state.available || !state.values.length) return;

      const ctx = canvas.getContext('2d');
      const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height || 220);
      gradient.addColorStop(0, palette.fillTop);
      gradient.addColorStop(1, palette.fillBottom);

      this._charts[chartName] = new window.Chart(ctx, {
        type: 'line',
        data: {
          labels: state.labels,
          datasets: [{
            data: state.values,
            borderColor: palette.line,
            backgroundColor: gradient,
            borderWidth: 3,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.35,
            fill: true,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              displayColors: false,
              callbacks: {
                label: (context) => (metric === 'income' ? this.signedFmt(context.parsed.y) : this.fmt(context.parsed.y)),
              },
            },
          },
          scales: {
            x: {
              grid: { display: false },
              ticks: {
                color: palette.axis,
                maxRotation: 0,
                autoSkip: true,
                maxTicksLimit: 6,
              },
            },
            y: {
              beginAtZero: false,
              grid: { color: palette.grid },
              ticks: {
                color: palette.axis,
                callback: (value) => (metric === 'income' ? this.signedFmt(value) : this.fmt(value)),
              },
            },
          },
        },
      });
    },

    renderKpiCharts() {
      if (!window.Chart) return;

      const chartConfigs = {
        gold: { metric: 'gold', canvasId: 'gold-history-chart', palette: { line: '#a67c00', fillTop: 'rgba(166, 124, 0, 0.30)', fillBottom: 'rgba(166, 124, 0, 0.02)', axis: '#8b6914', grid: 'rgba(139, 105, 20, 0.14)' } },
        income: { metric: 'income', canvasId: 'income-history-chart', palette: { line: '#2d7a2d', fillTop: 'rgba(45, 122, 45, 0.28)', fillBottom: 'rgba(45, 122, 45, 0.03)', axis: '#5c7a3a', grid: 'rgba(45, 122, 45, 0.10)' } },
        resources: { metric: 'resources', canvasId: 'resources-history-chart', palette: { line: '#8c6335', fillTop: 'rgba(140, 99, 53, 0.24)', fillBottom: 'rgba(140, 99, 53, 0.03)', axis: '#7a5a2d', grid: 'rgba(122, 90, 45, 0.10)' } },
        cities: { metric: 'cities', canvasId: 'cities-history-chart', palette: { line: '#4f86b9', fillTop: 'rgba(79, 134, 185, 0.24)', fillBottom: 'rgba(79, 134, 185, 0.03)', axis: '#4a6d89', grid: 'rgba(79, 134, 185, 0.10)' } },
        troops: { metric: 'troops', canvasId: 'troops-history-chart', palette: { line: '#bb5143', fillTop: 'rgba(187, 81, 67, 0.24)', fillBottom: 'rgba(187, 81, 67, 0.03)', axis: '#8c4c45', grid: 'rgba(187, 81, 67, 0.10)' } },
        ships: { metric: 'ships', canvasId: 'ships-history-chart', palette: { line: '#4a7c9e', fillTop: 'rgba(74, 124, 158, 0.24)', fillBottom: 'rgba(74, 124, 158, 0.03)', axis: '#4a6d89', grid: 'rgba(74, 124, 158, 0.10)' } },
      };

      for (const [key, config] of Object.entries(chartConfigs)) {
        if (this.kpiModal === key) {
          this.$nextTick(() => {
            window.setTimeout(() => {
              this.renderHistoryChart(config.metric, config.canvasId, config.palette);
            }, 30);
          });
        } else {
          this.destroyChart(`${config.metric}-${config.canvasId}`);
        }
      }
    },

    // ── Real-time Resource Projection ──

    initProjectionValues() {
      const numberFormatter = this._numberFormatter;
      const nowSec = () => Date.now() / 1000;

      const updateNode = (node) => {
        const base = parseFloat(node.dataset.base || '0');
        const rate = parseFloat(node.dataset.rate || '0');
        const capacity = parseFloat(node.dataset.capacity || '0');
        const epoch = parseInt(node.dataset.snapshotEpoch || '0', 10);

        let value = base;
        if (epoch > 0 && rate !== 0) {
          const elapsed = Math.max(0, (nowSec() - epoch) / 3600);
          value = base + (rate * elapsed);
        }
        if (capacity > 0) value = Math.min(value, capacity);
        value = Math.max(0, value);

        const txt = numberFormatter.format(Math.floor(value));
        if (node.textContent !== txt) node.textContent = txt;
      };

      const parseNum = (s) => parseInt((s || '').replace(/\D/g, '') || '0', 10);

      const updateTotals = () => {
        document.querySelectorAll('.js-projected-total').forEach((node) => {
          const tr = node.closest('tr');
          if (!tr) return;
          let sum = 0;
          tr.querySelectorAll('.js-projected-resource').forEach((r) => { sum += parseNum(r.textContent); });
          const txt = numberFormatter.format(sum);
          if (node.textContent !== txt) node.textContent = txt;
        });

        document.querySelectorAll('.js-projected-col-total').forEach((node) => {
          const col = parseInt(node.dataset.col, 10);
          const table = node.closest('table');
          if (!table) return;
          let sum = 0;
          table.querySelectorAll('tbody tr .js-projected-resource').forEach((r) => {
            const tr = r.closest('tr');
            if (!tr || tr.querySelector('.js-projected-col-total')) return;
            const resources = tr.querySelectorAll('.js-projected-resource');
            if (resources[col] === r) sum += parseNum(r.textContent);
          });
          const txt = numberFormatter.format(sum);
          if (node.textContent !== txt) node.textContent = txt;
        });

        document.querySelectorAll('.js-projected-grand-total').forEach((node) => {
          const table = node.closest('table');
          if (!table) return;
          let sum = 0;
          table.querySelectorAll('.js-projected-total').forEach((t) => { sum += parseNum(t.textContent); });
          const txt = numberFormatter.format(sum);
          if (node.textContent !== txt) node.textContent = txt;
        });
      };

      const tick = (ts) => {
        if (ts - this._lastProjectionUpdate >= 1000) {
          document.querySelectorAll('.js-projected-resource').forEach(updateNode);
          updateTotals();
          this._lastProjectionUpdate = ts;
        }
        this._projectionRAF = requestAnimationFrame(tick);
      };

      if (this._projectionRAF) cancelAnimationFrame(this._projectionRAF);
      document.querySelectorAll('.js-projected-resource').forEach(updateNode);
      updateTotals();
      this._projectionRAF = requestAnimationFrame(tick);
    },
  };
}
