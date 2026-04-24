/**
 * Game Dashboard — Core Alpine.js component (state, filters, KPI, DOM).
 * Mixes in gameDashboardModals() and gameDashboardCharts().
 */
function gameDashboard() {
  const numberFormatter = new Intl.NumberFormat('pt-BR');

  const base = {
    filterNode: '',
    filterAccount: '',
    filterServer: '',
    filterCity: '',
    kpiModal: null,
    historyMap: {},
    historyLoaded: false,
    _historyPromise: null,
    resourceModalData: [],
    accountDetailData: [],
    kpi: { gold: 0, income: 0, cities: 0, resources: 0, troops: 0, ships: 0, wood: 0, wine: 0, marble: 0, crystal: 0, sulfur: 0 },
    _projectionRAF: null,
    _lastProjectionUpdate: 0,
    _charts: {},
    _numberFormatter: numberFormatter,

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
      this.applyFilter();
      this.initProjectionValues();
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

      this._historyPromise = fetch('/game/history/', {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin',
      })
        .then((response) => {
          if (!response.ok) throw new Error(`History request failed: ${response.status}`);
          return response.json();
        })
        .then((payload) => {
          this.historyMap = payload?.history || {};
          this.historyLoaded = true;
          return this.historyMap;
        })
        .catch((error) => {
          console.error('Falha ao carregar historico do dashboard', error);
          return {};
        })
        .finally(() => {
          this._historyPromise = null;
        });

      return this._historyPromise;
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

    applyFilter() {
      const wantedNode = String(this.filterNode || '');
      const wantedAccount = String(this.filterAccount || '');
      const wantedServer = String(this.filterServer || '');
      const wantedCity = this.normalize(this.filterCity);

      this.allCardElements().forEach((el) => {
        const cityNames = (el.dataset.cityNames || '')
          .split('|')
          .map((name) => this.normalize(name))
          .filter(Boolean);

        const matchNode = !wantedNode || String(el.dataset.node || '') === wantedNode;
        const matchAccount = !wantedAccount || String(el.dataset.account || '') === wantedAccount;
        const matchServer = !wantedServer || String(el.dataset.server || '') === wantedServer;
        const matchCity = !wantedCity || cityNames.includes(wantedCity);
        const visible = matchNode && matchAccount && matchServer && matchCity;

        el.dataset.filterVisible = visible ? '1' : '0';
        el.classList.toggle('hidden', !visible);
      });

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

      this.kpiCards().forEach((card) => {
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
