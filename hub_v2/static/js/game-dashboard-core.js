/**
 * Game Dashboard — Core Alpine.js component (state, filters, KPI, DOM).
 * Mixes in gameDashboardModals() and gameDashboardCharts().
 */
function gameDashboard() {
  const numberFormatter = new Intl.NumberFormat('pt-BR');
  const compactFormatter = new Intl.NumberFormat('pt-BR', { notation: 'compact', maximumFractionDigits: 1 });

  const base = {
    filterNode: '',
    filterAccount: '',
    filterServer: '',
    filterCity: '',
    filterVisibleCount: null,
    filterSummary: '',
    kpiModal: null,
    historyMap: {},
    historyLoaded: false,
    historyPeriod: '24h',
    historyPeriods: [['24h', '24 horas', '24h'], ['7d', '7 dias', '7d'], ['30d', '30 dias', '30d'], ['60d', '60 dias', '60d'], ['90d', '90 dias', '90d'], ['all', 'todo o período guardado', 'Tudo']],
    // N-101: one colour and one image per KPI, used by the top cards, the modal headers and the charts
    kpiTheme: {
      gold: { label: 'Ouro', color: '#b8860b', soft: 'rgba(184,134,11,0.12)', icon: '/static/game/resources/icon_gold.png' },
      income: { label: 'Renda/h', color: '#2d7a2d', soft: 'rgba(45,122,45,0.12)', icon: '/static/game/resources/icon_gold.png' },
      cities: { label: 'Cidades', color: '#4f86b9', soft: 'rgba(79,134,185,0.14)', icon: '/static/game/buildings/townhall.png' },
      resources: { label: 'Recursos', color: '#8c6335', soft: 'rgba(140,99,53,0.13)', icon: '/static/game/resources/icon_wood.png' },
      troops: { label: 'Tropas', color: '#bb5143', soft: 'rgba(187,81,67,0.12)', icon: '/static/game/units/hoplita.png' },
      ships: { label: 'Navios', color: '#3f7397', soft: 'rgba(63,115,151,0.13)', icon: '/static/game/units/trieme.png' },
    },
    resourceTheme: [
      { key: 'wood', label: 'Madeira', color: '#8c6335', icon: '/static/game/resources/icon_wood.png' },
      { key: 'wine', label: 'Vinho', color: '#8f3c44', icon: '/static/game/resources/icon_wine.png' },
      { key: 'marble', label: 'Mármore', color: '#8a8a93', icon: '/static/game/resources/icon_marble.png' },
      { key: 'crystal', label: 'Cristal', color: '#3e84a8', icon: '/static/game/resources/icon_glass.png' },
      { key: 'sulfur', label: 'Enxofre', color: '#c29a2e', icon: '/static/game/resources/icon_sulfur.png' },
    ],
    _dayHistory: null,
    _historyByPeriod: {},
    _historyPromise: null,
    resourceModalData: [],
    accountDetailData: [],
    kpi: { gold: 0, income: 0, cities: 0, resources: 0, troops: 0, ships: 0, wood: 0, wine: 0, marble: 0, crystal: 0, sulfur: 0 },
    _projectionRAF: null,
    _lastProjectionUpdate: 0,
    _charts: {},
    _numberFormatter: numberFormatter,

    get historyLoading() {
      return Boolean(this._historyPromise && !this.historyLoaded);
    },

    init() {
      this.historyMap = this.loadHistoryMap();
      this.historyLoaded = Object.keys(this.historyMap || {}).length > 0;
      this.resourceModalData = this.loadJsonScript('game-dashboard-resource-data');
      this.accountDetailData = this.loadJsonScript('game-dashboard-account-detail-data');
      this.$watch('kpiModal', async (value) => {
        this.syncModalScrollLock(Boolean(value));
        if (value) {
          await this.ensureHistoryLoaded();
        }
        this.$nextTick(() => this.renderKpiCharts());
      });
      this.syncModalScrollLock(Boolean(this.kpiModal));
      this.readFiltersFromUrl();
      this.applyFilter();
      this.initProjectionValues();
      this.loadDayHistory();
    },

    // N-101: the last 24 hours, fetched quietly after the page is up, for the small
    // "24h" variation under each top number (and so the first modal opens with data).
    loadDayHistory() {
      window.setTimeout(() => {
        fetch('/game/history/?period=24h', { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
          .then((response) => (response.ok ? response.json() : null))
          .then((payload) => {
            if (!payload) return;
            this._historyByPeriod['24h'] = payload.history || {};
            this._dayHistory = this._historyByPeriod['24h'];
          })
          .catch(() => {});
      }, 400);
    },

    compactFmt(n) {
      return compactFormatter.format(Number(n || 0));
    },

    // {text, color}: how the number changed in 24 hours, for the accounts in the filter
    kpiTrend(metric) {
      const history = this._dayHistory;
      if (!history) return { text: '', color: 'transparent' };
      let before = 0;
      let known = 0;
      this.kpiCards().forEach((card) => {
        const points = history[String(card.gameAccountId)] || [];
        if (points.length) {
          before += Number(points[0][metric] || 0);
          known += 1;
        }
      });
      if (!known) return { text: '', color: 'transparent' };
      const delta = Math.round(Number(this.kpi[metric] || 0) - before);
      if (!delta) return { text: '24h  =', color: '#8a7a60' };
      const good = delta > 0;
      return { text: `24h  ${good ? '▲' : '▼'} ${this.compactFmt(Math.abs(delta))}`, color: good ? '#2d7a2d' : '#b3402f' };
    },

    syncModalScrollLock(locked) {
      const root = document.documentElement;
      const body = document.body;
      if (!root || !body) return;
      root.style.overflow = locked ? 'hidden' : '';
      body.style.overflow = locked ? 'hidden' : '';
    },

    // ── Formatters ──

    fmt(n) {
      return numberFormatter.format(Number(n || 0));
    },

    signedFmt(n) {
      const num = Number(n || 0);
      return (num > 0 ? '+' : '') + this.fmt(num);
    },

    fmtHours(h) {
      if (h == null || isNaN(h)) return '-';
      let totalHours = Math.abs(Number(h));
      const years = Math.floor(totalHours / 8760);
      totalHours %= 8760;
      const months = Math.floor(totalHours / 730);
      totalHours %= 730;
      const days = Math.floor(totalHours / 24);
      const hours = Math.floor(totalHours % 24);
      const mins = Math.round((totalHours % 1) * 60);
      const parts = [];
      if (years) parts.push(years + 'a');
      if (months) parts.push(months + 'M');
      if (days) parts.push(days + 'd');
      if (hours && !years) parts.push(hours + 'h');
      if (mins && !years && !months) parts.push(mins + 'min');
      return parts.length ? parts.join(' ') : '< 1min';
    },

    normalize(value) {
      return String(value || '').trim().toLowerCase();
    },

    // ── Data Loaders ──

    loadHistoryMap() {
      return this.loadJsonScript('game-dashboard-history');
    },

    async ensureHistoryLoaded() {
      if (this.historyLoaded) return this.historyMap;
      if (this._historyPromise) return this._historyPromise;

      const period = this.historyPeriod;
      if (this._historyByPeriod[period]) {
        this.historyMap = this._historyByPeriod[period];
        this.historyLoaded = true;
        this.$nextTick(() => this.renderKpiCharts());
        return this.historyMap;
      }

      this._historyPromise = fetch(`/game/history/?period=${encodeURIComponent(period)}`, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin',
      })
        .then((response) => {
          if (!response.ok) throw new Error(`History request failed: ${response.status}`);
          return response.json();
        })
        .then((payload) => {
          this._historyByPeriod[period] = payload?.history || {};
          if (period !== this.historyPeriod) return this.historyMap;   // the period changed while this one loaded
          this.historyMap = this._historyByPeriod[period];
          this.historyLoaded = true;
          this.$nextTick(() => this.renderKpiCharts());
          return this.historyMap;
        })
        .catch((error) => {
          console.error('Falha ao carregar histórico do dashboard', error);
          return {};
        })
        .finally(() => {
          this._historyPromise = null;
        });

      return this._historyPromise;
    },

    // N-101: the chart of a KPI modal over 24 hours, 7 days or 30 days
    async setHistoryPeriod(period) {
      if (period === this.historyPeriod) return;
      this.historyPeriod = period;
      this.historyLoaded = false;
      this._historyPromise = null;
      await this.ensureHistoryLoaded();
      this.$nextTick(() => this.renderKpiCharts());
    },

    loadJsonScript(id) {
      const node = document.getElementById(id);
      if (!node) return {};
      try {
        return JSON.parse(node.textContent || '{}');
      } catch (error) {
        console.error(`Falha ao carregar dados do dashboard: ${id}`, error);
        return {};
      }
    },

    // ── DOM & Filters ──

    allCardElements() {
      return Array.from(document.querySelectorAll('.game-card'));
    },

    visibleCardElements() {
      return this.allCardElements().filter((el) => el.dataset.filterVisible !== '0');
    },

    cardFromElement(el) {
      const d = el.dataset;
      const uid = d.gameAccount || [d.account || '', d.server || '', d.player || 'sem-nome'].join('-');
      return {
        uid,
        parentAccountId: d.account || '',
        gameAccountId: d.gameAccount || '',
        player: d.player || '',
        serverLabel: d.serverLabel || '',
        cityNames: (d.cityNames || '').split('|').map((name) => name.trim()).filter(Boolean),
        gold: parseInt(d.gold || 0, 10),
        income: parseInt(d.income || 0, 10),
        cities: parseInt(d.cities || 0, 10),
        resources: parseInt(d.resources || 0, 10),
        wood: parseInt(d.wood || 0, 10),
        wine: parseInt(d.wine || 0, 10),
        marble: parseInt(d.marble || 0, 10),
        crystal: parseInt(d.crystal || 0, 10),
        sulfur: parseInt(d.sulfur || 0, 10),
        troops: parseInt(d.troops || 0, 10),
        ships: parseInt(d.ships || 0, 10),
        grossIncome: parseInt(d.grossIncome || 0, 10),
        upkeep: parseInt(d.upkeep || 0, 10),
        scientists: parseInt(d.scientists || 0, 10),
        freeTransporters: parseInt(d.freeTransporters || 0, 10),
        maxTransporters: parseInt(d.maxTransporters || 0, 10),
        wineSpendings: parseInt(d.wineSpendings || 0, 10),
        netWineRate: parseFloat(d.netWineRate || 0),
        totalWineHours: d.totalWineHours ? parseInt(d.totalWineHours, 10) : null,
      };
    },

    kpiCards() {
      return this.visibleCardElements().map((el) => this.cardFromElement(el));
    },

    // ── Filters (N-128: node, account, player and city; the city narrows inside each account) ──

    filterKeys: [['no', 'filterNode'], ['conta', 'filterAccount'], ['jogador', 'filterServer'], ['cidade', 'filterCity']],

    hasFilter() {
      return Boolean(this.filterNode || this.filterAccount || this.filterServer || this.filterCity);
    },

    clearFilters() {
      this.filterNode = '';
      this.filterAccount = '';
      this.filterServer = '';
      this.filterCity = '';
      this.applyFilter();
    },

    readFiltersFromUrl() {
      let params;
      try { params = new URLSearchParams(window.location.search); } catch (e) { return; }
      this.filterKeys.forEach(([param, field]) => {
        const value = params.get(param);
        if (value) this[field] = value;
      });
    },

    writeFiltersToUrl() {
      try {
        const url = new URL(window.location.href);
        this.filterKeys.forEach(([param, field]) => {
          if (this[field]) url.searchParams.set(param, this[field]);
          else url.searchParams.delete(param);
        });
        if (url.href !== window.location.href) window.history.replaceState(window.history.state, '', url.href);
      } catch (e) { /* the filter still works without the address */ }
    },

    cardMatches(el, skip) {
      const d = el.dataset;
      const wantedCity = this.normalize(this.filterCity);
      const cityNames = (d.cityNames || '').split('|').map((name) => this.normalize(name)).filter(Boolean);
      return (skip === 'node' || !this.filterNode || String(d.node || '') === String(this.filterNode))
        && (skip === 'account' || !this.filterAccount || String(d.account || '') === String(this.filterAccount))
        && (skip === 'player' || !this.filterServer || String(d.gameAccount || '') === String(this.filterServer))
        && (skip === 'city' || !wantedCity || cityNames.includes(wantedCity));
    },

    // Each list offers only what still has accounts under the other filters.
    narrowOptions(cards) {
      const root = document.querySelector('[data-panel-filters]');
      if (!root) return;
      root.querySelectorAll('select[data-filter]').forEach((select) => {
        const kind = select.dataset.filter;
        const pool = cards.filter((el) => this.cardMatches(el, kind));
        Array.from(select.options).forEach((option) => {
          if (!option.value) return;
          let available;
          if (kind === 'node') available = pool.some((el) => String(el.dataset.node || '') === option.value);
          else if (kind === 'account') available = pool.some((el) => String(el.dataset.account || '') === option.value);
          else if (kind === 'player') available = pool.some((el) => String(el.dataset.gameAccount || '') === option.value);
          else {
            const owners = (option.dataset.gameAccounts || '').split('|');
            available = pool.some((el) => owners.includes(String(el.dataset.gameAccount || '')));
          }
          const keep = available || option.value === select.value;
          option.hidden = !keep;
          option.disabled = !keep;
        });
      });
    },

    applyFilter() {
      const wantedCity = this.normalize(this.filterCity);
      const cards = this.allCardElements();
      let shown = 0;

      cards.forEach((el) => {
        const visible = this.cardMatches(el);
        el.dataset.filterVisible = visible ? '1' : '0';
        el.classList.toggle('hidden', !visible);
        if (visible) shown += 1;

        // the city filter shows only that city inside the account, in every tab
        let cityRows = 0;
        el.querySelectorAll('[data-city-name]').forEach((part) => {
          const out = Boolean(wantedCity) && this.normalize(part.dataset.cityName) !== wantedCity;
          part.dataset.cityOut = out ? '1' : '0';
          part.style.display = out ? 'none' : '';
          if (!out && part.hasAttribute('data-city-troops')) cityRows += 1;
        });
        const count = el.querySelector('[data-city-count]');
        if (count) count.textContent = cityRows;
      });

      this.filterVisibleCount = shown;
      const summary = document.querySelector('[data-panel-filters] [data-of]');
      this.filterSummary = summary ? `${shown} ${summary.dataset.of} ${cards.length} ${summary.dataset.unit}` : '';
      this.narrowOptions(cards);
      this.writeFiltersToUrl();
      this.recalcKpi();
    },

    recalcKpi() {
      let gold = 0;
      let income = 0;
      let cities = 0;
      let resources = 0;
      let troops = 0;
      let ships = 0;
      let wood = 0;
      let wine = 0;
      let marble = 0;
      let crystal = 0;
      let sulfur = 0;

      const wantedCity = this.normalize(this.filterCity);
      this.kpiCards().forEach((card) => {
        if (wantedCity) {
          // N-128: with a city chosen, the cities, resources and forces are those of that city
          const el = this.visibleCardElements().find((node) => (node.dataset.gameAccount || '') === String(card.gameAccountId || ''));
          gold += card.gold;
          income += card.income;
          (el ? Array.from(el.querySelectorAll('tr[data-city-troops]')) : []).forEach((row) => {
            if (row.dataset.cityOut === '1') return;
            const values = Array.from(row.querySelectorAll('.js-projected-resource')).map((node) => parseInt(node.dataset.base || '0', 10) || 0);
            cities += 1;
            wood += values[0] || 0;
            wine += values[1] || 0;
            marble += values[2] || 0;
            crystal += values[3] || 0;
            sulfur += values[4] || 0;
            resources += values.reduce((sum, value) => sum + value, 0);
            troops += parseInt(row.dataset.cityTroops || '0', 10) || 0;
            ships += parseInt(row.dataset.cityShips || '0', 10) || 0;
          });
          return;
        }
        gold += card.gold;
        income += card.income;
        cities += card.cities;
        resources += card.resources;
        troops += card.troops;
        ships += card.ships;
        wood += card.wood;
        wine += card.wine;
        marble += card.marble;
        crystal += card.crystal;
        sulfur += card.sulfur;
      });

      this.kpi = { gold, income, cities, resources, troops, ships, wood, wine, marble, crystal, sulfur };
      this.renderKpiCharts();
    },

    // ── Tooltip helpers (used in account cards) ──

    tt: null,
    ttX: 0,
    ttY: 0,

    showTT(event, data) {
      this.tt = data;
      const rect = event.target.getBoundingClientRect();
      this.ttX = Math.min(rect.left, window.innerWidth - 220);
      this.ttY = rect.bottom + 6;
    },

    hideTT() {
      this.tt = null;
    },
  };

  // Mix in modal and chart functions
  Object.assign(base, gameDashboardModals());
  Object.assign(base, gameDashboardCharts());

  return base;
}

window.gameDashboard = gameDashboard;


function cityCountdown(endAt) {
  return {
    endAt: endAt,
    label: '',
    _timer: null,
    start() {
      if (!this.endAt) return;
      this._tick();
      this._timer = setInterval(() => this._tick(), 1000);
    },
    _tick() {
      const remaining = Math.max(0, this.endAt - Math.floor(Date.now() / 1000));
      if (remaining === 0) {
        this.label = '';
        clearInterval(this._timer);
        return;
      }
      const h = Math.floor(remaining / 3600);
      const m = Math.floor((remaining % 3600) / 60);
      const s = remaining % 60;
      if (h > 0) {
        this.label = h + 'h' + String(m).padStart(2, '0') + 'm';
      } else {
        this.label = String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
      }
    },
    destroy() { clearInterval(this._timer); },
  };
}
window.cityCountdown = cityCountdown;
