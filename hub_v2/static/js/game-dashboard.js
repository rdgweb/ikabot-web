function gameDashboard() {
  const numberFormatter = new Intl.NumberFormat('pt-BR');

  return {
    filterNode: '',
    filterAccount: '',
    filterServer: '',
    filterCity: '',
    kpiModal: null,
    historyMap: {},
    resourceModalData: [],
    accountDetailData: [],
    kpi: { gold: 0, income: 0, cities: 0, resources: 0, troops: 0, ships: 0, wood: 0, wine: 0, marble: 0, crystal: 0, sulfur: 0 },
    _projectionRAF: null,
    _lastProjectionUpdate: 0,
    _charts: {},

    init() {
      this.historyMap = this.loadHistoryMap();
      this.resourceModalData = this.loadJsonScript('game-dashboard-resource-data');
      this.accountDetailData = this.loadJsonScript('game-dashboard-account-detail-data');
      this.$watch('kpiModal', (value) => {
        this.syncModalScrollLock(Boolean(value));
        this.$nextTick(() => this.renderKpiCharts());
      });
      this.syncModalScrollLock(Boolean(this.kpiModal));
      this.applyFilter();
      this.initProjectionValues();
      this.fetchHistory();
    },

    fetchHistory() {
      // History is expensive to compute — loaded async so the page renders fast.
      // If historyMap already has data (served inline), skip the fetch.
      if (Object.keys(this.historyMap).length > 0) return;
      fetch('/game/history/', { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then((r) => r.ok ? r.json() : null)
        .catch(() => null)
        .then((data) => {
          if (!data || !data.history) return;
          this.historyMap = data.history;
          // Re-render chart if a KPI modal is already open
          this.$nextTick(() => this.renderKpiCharts());
        });
    },

    syncModalScrollLock(locked) {
      const root = document.documentElement;
      const body = document.body;
      if (!root || !body) return;
      root.style.overflow = locked ? 'hidden' : '';
      body.style.overflow = locked ? 'hidden' : '';
    },

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

    loadHistoryMap() {
      return this.loadJsonScript('game-dashboard-history');
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

    enrichCardMetrics(card) {
      const gross = Math.max(card.grossIncome || 0, 0);
      const upkeepAbs = Math.abs(Math.min(card.upkeep || 0, 0));
      const scientistsAbs = Math.abs(Math.min(card.scientists || 0, 0));
      const expenseBase = gross + upkeepAbs + scientistsAbs;
      const delta24 = card.income * 24;
      let hoursToZero = null;
      if (card.income < 0 && card.gold > 0) {
        hoursToZero = card.gold / Math.abs(card.income);
      }
      return {
        ...card,
        delta24,
        hoursToZero,
        grossWidth: expenseBase ? Math.round((gross / expenseBase) * 100) : 0,
        upkeepWidth: expenseBase ? Math.round((upkeepAbs / expenseBase) * 100) : 0,
        scientistsWidth: expenseBase ? Math.round((scientistsAbs / expenseBase) * 100) : 0,
      };
    },

    goldInsights() {
      const cards = this.kpiCards().map((c) => this.enrichCardMetrics(c));
      const negative = cards.filter((c) => c.income < 0);
      const delta24 = cards.reduce((sum, c) => sum + c.delta24, 0);
      const critical = negative
        .filter((c) => c.hoursToZero !== null)
        .sort((a, b) => a.hoursToZero - b.hoursToZero)[0];
      return {
        delta24,
        negativeCount: negative.length,
        criticalName: critical ? critical.player : 'Nenhuma',
        criticalHours: critical ? critical.hoursToZero : null,
        criticalLabel: critical ? ('Zera em ' + this.fmtHours(critical.hoursToZero)) : 'Sem risco imediato',
      };
    },

    topGoldAccounts() {
      const totalGold = this.kpi.gold || 0;
      return this.kpiCards()
        .map((c) => this.enrichCardMetrics(c))
        .sort((a, b) => b.gold - a.gold)
        .map((c) => ({
          ...c,
          goldShare: totalGold > 0 ? Math.round((c.gold / totalGold) * 100) : 0,
        }));
    },

    goldRiskAccounts() {
      return this.topGoldAccounts()
        .filter((c) => c.income < 0)
        .sort((a, b) => {
          if (a.hoursToZero === null) return 1;
          if (b.hoursToZero === null) return -1;
          return a.hoursToZero - b.hoursToZero;
        })
        .slice(0, 5);
    },

    incomeRanking(direction = 'desc') {
      const cards = this.kpiCards().map((c) => this.enrichCardMetrics(c));
      const maxAbsIncome = Math.max(...cards.map((c) => Math.abs(c.income || 0)), 1);
      const ranked = cards.map((c) => ({
        ...c,
        incomeWidth: Math.round((Math.abs(c.income || 0) / maxAbsIncome) * 100),
      }));
      return ranked.sort((a, b) => (direction === 'asc' ? a.income - b.income : b.income - a.income));
    },

    topIncomeAccounts() {
      return this.incomeRanking('desc').slice(0, 5);
    },

    negativeIncomeAccounts() {
      return this.incomeRanking('asc').filter((c) => c.income < 0).slice(0, 5);
    },

    incomeInsights() {
      const cards = this.kpiCards().map((c) => this.enrichCardMetrics(c));
      return {
        delta24: cards.reduce((sum, c) => sum + c.delta24, 0),
        positiveCount: cards.filter((c) => c.income >= 0).length,
        negativeCount: cards.filter((c) => c.income < 0).length,
      };
    },

    resourceAccounts() {
      const wantedNode = String(this.filterNode || '');
      const wantedAccount = String(this.filterAccount || '');
      const wantedServer = String(this.filterServer || '');
      const wantedCity = this.normalize(this.filterCity);

      return (this.resourceModalData || [])
        .filter((account) => {
          const matchNode = !wantedNode || String(account.node_id || '') === wantedNode;
          const matchAccount = !wantedAccount || String(account.account_id || '') === wantedAccount;
          const matchServer = !wantedServer || String(account.server_id || '') === wantedServer;
          const cityNames = (account.city_names || []).map((name) => this.normalize(name));
          const matchCity = !wantedCity || cityNames.includes(wantedCity);
          return matchNode && matchAccount && matchServer && matchCity;
        })
        .map((account) => {
          const filteredCities = !wantedCity
            ? (account.cities || [])
            : (account.cities || []).filter((city) => this.normalize(city.name) === wantedCity);
          return {
            ...account,
            cities: filteredCities,
          };
        });
    },

    resourceSummary() {
      const accounts = this.resourceAccounts();
      const cities = accounts.flatMap((account) => account.cities || []);
      return {
        totalResources: accounts.reduce((sum, account) => sum + Number(account.resources || 0), 0),
        totalWine: accounts.reduce((sum, account) => sum + Number(account.totals?.wine || 0), 0),
        nearFullCities: cities.filter((city) => Number(city.cap_pct || 0) >= 85).length,
        wineRiskCities: cities.filter((city) => city.wine_hours !== null && city.wine_hours !== undefined && Number(city.wine_hours) < 72).length,
        trackedCities: cities.length,
      };
    },

    resourceTypeBreakdown() {
      const accounts = this.resourceAccounts();
      const totals = {
        wood: 0,
        wine: 0,
        marble: 0,
        crystal: 0,
        sulfur: 0,
      };
      accounts.forEach((account) => {
        totals.wood += Number(account.totals?.wood || 0);
        totals.wine += Number(account.totals?.wine || 0);
        totals.marble += Number(account.totals?.marble || 0);
        totals.crystal += Number(account.totals?.crystal || 0);
        totals.sulfur += Number(account.totals?.sulfur || 0);
      });
      const grandTotal = Object.values(totals).reduce((sum, value) => sum + value, 0);
      return [
        { key: 'wood', label: 'Madeira', short: 'Madeira', icon: '/static/game/resources/icon_wood.png', color: '#8c6335', value: totals.wood },
        { key: 'wine', label: 'Vinho', short: 'Vinho', icon: '/static/game/resources/icon_wine.png', color: '#8f3c44', value: totals.wine },
        { key: 'marble', label: 'Marmore', short: 'Marmore', icon: '/static/game/resources/icon_marble.png', color: '#7e7e86', value: totals.marble },
        { key: 'crystal', label: 'Cristal', short: 'Cristal', icon: '/static/game/resources/icon_glass.png', color: '#3e84a8', value: totals.crystal },
        { key: 'sulfur', label: 'Enxofre', short: 'Enxofre', icon: '/static/game/resources/icon_sulfur.png', color: '#b78c2a', value: totals.sulfur },
      ].map((item) => ({
        ...item,
        share: grandTotal > 0 ? Math.round((item.value / grandTotal) * 100) : 0,
      }));
    },

    resourceAccountRanking() {
      const accounts = this.resourceAccounts();
      const maxResources = Math.max(...accounts.map((account) => Number(account.resources || 0)), 1);
      return accounts
        .map((account) => ({
          ...account,
          resourceWidth: Math.round((Number(account.resources || 0) / maxResources) * 100),
        }))
        .sort((a, b) => Number(b.resources || 0) - Number(a.resources || 0));
    },

    richestResourceCities() {
      return this.resourceAccounts()
        .flatMap((account) => (account.cities || []).map((city) => ({
          ...city,
          player: account.player,
          serverLabel: account.server_label,
        })))
        .sort((a, b) => Number(b.resource_total || 0) - Number(a.resource_total || 0))
        .slice(0, 8);
    },

    criticalResourceCities() {
      return this.resourceAccounts()
        .flatMap((account) => (account.cities || []).map((city) => ({
          ...city,
          player: account.player,
          serverLabel: account.server_label,
          severity: (Number(city.cap_pct || 0) >= 95 ? 1000 : Number(city.cap_pct || 0))
            + ((city.wine_hours !== null && city.wine_hours !== undefined && Number(city.wine_hours) < 24) ? 500 : 0)
            + ((city.wine_hours !== null && city.wine_hours !== undefined && Number(city.wine_hours) < 72) ? 200 : 0),
        })))
        .filter((city) => Number(city.cap_pct || 0) >= 85 || (city.wine_hours !== null && city.wine_hours !== undefined && Number(city.wine_hours) < 72))
        .sort((a, b) => b.severity - a.severity || Number(b.resource_total || 0) - Number(a.resource_total || 0))
        .slice(0, 8);
    },

    detailAccounts() {
      const wantedNode = String(this.filterNode || '');
      const wantedAccount = String(this.filterAccount || '');
      const wantedServer = String(this.filterServer || '');
      const wantedCity = this.normalize(this.filterCity);

      return (this.accountDetailData || [])
        .filter((account) => {
          const matchNode = !wantedNode || String(account.node_id || '') === wantedNode;
          const matchAccount = !wantedAccount || String(account.account_id || '') === wantedAccount;
          const matchServer = !wantedServer || String(account.server_id || '') === wantedServer;
          const cityNames = (account.city_names || []).map((name) => this.normalize(name));
          const matchCity = !wantedCity || cityNames.includes(wantedCity);
          return matchNode && matchAccount && matchServer && matchCity;
        })
        .map((account) => ({
          ...account,
          cities: !wantedCity
            ? (account.cities || [])
            : (account.cities || []).filter((city) => this.normalize(city.name) === wantedCity),
        }));
    },

    citySummary() {
      const accounts = this.detailAccounts();
      const cities = accounts.flatMap((account) => account.cities || []);
      const avgCities = accounts.length ? Math.round((cities.length / accounts.length) * 10) / 10 : 0;
      const lowTransport = accounts.filter((account) => Number(account.free_transporters || 0) <= 0).length;
      const wineRisk = accounts.filter((account) => account.total_wine_hours !== null && account.total_wine_hours !== undefined && Number(account.total_wine_hours) < 72).length;
      return {
        totalCities: cities.length,
        accounts: accounts.length,
        avgCities,
        lowTransport,
        wineRisk,
      };
    },

    cityAccountRanking() {
      const accounts = this.detailAccounts();
      const maxCities = Math.max(...accounts.map((account) => Number(account.city_count || (account.cities || []).length || 0)), 1);
      return accounts
        .map((account) => ({
          ...account,
          cityCountResolved: Number(account.city_count || (account.cities || []).length || 0),
          cityWidth: Math.round((Number(account.city_count || (account.cities || []).length || 0) / maxCities) * 100),
        }))
        .sort((a, b) => b.cityCountResolved - a.cityCountResolved || Number(b.resources || 0) - Number(a.resources || 0));
    },

    cityFocusList() {
      return this.detailAccounts()
        .flatMap((account) => (account.cities || []).map((city) => ({
          ...city,
          player: account.player,
          serverLabel: account.server_label,
          severity: (city.wine_hours !== null && city.wine_hours !== undefined && Number(city.wine_hours) < 24 ? 500 : 0)
            + (city.wine_hours !== null && city.wine_hours !== undefined && Number(city.wine_hours) < 72 ? 200 : 0)
            + (Number(city.cap_pct || 0) >= 95 ? 300 : 0)
            + Number(city.troop_total || 0) / 100,
        })))
        .sort((a, b) => b.severity - a.severity || Number(b.resource_total || 0) - Number(a.resource_total || 0))
        .slice(0, 8);
    },

    troopSummary() {
      const accounts = this.detailAccounts();
      const maxTroops = accounts.reduce((best, account) => {
        if (!best || Number(account.troop_total || 0) > Number(best.troop_total || 0)) return account;
        return best;
      }, null);
      return {
        totalTroops: accounts.reduce((sum, account) => sum + Number(account.troop_total || 0), 0),
        accounts: accounts.length,
        avgPerAccount: accounts.length ? Math.round(accounts.reduce((sum, account) => sum + Number(account.troop_total || 0), 0) / accounts.length) : 0,
        topAccount: maxTroops?.player || 'Nenhuma',
        topTroops: Number(maxTroops?.troop_total || 0),
      };
    },

    troopAccountRanking() {
      const accounts = this.detailAccounts();
      const maxTroops = Math.max(...accounts.map((account) => Number(account.troop_total || 0)), 1);
      return accounts
        .map((account) => ({
          ...account,
          troopWidth: Math.round((Number(account.troop_total || 0) / maxTroops) * 100),
        }))
        .sort((a, b) => Number(b.troop_total || 0) - Number(a.troop_total || 0));
    },

    troopComposition() {
      const totals = new Map();
      this.detailAccounts().forEach((account) => {
        (account.troop_columns || []).forEach((unit) => {
          const key = unit.name || 'Desconhecida';
          totals.set(key, (totals.get(key) || 0) + Number(unit.total || 0));
        });
      });
      const grand = Array.from(totals.values()).reduce((sum, value) => sum + value, 0);
      return Array.from(totals.entries())
        .map(([name, total]) => ({
          name,
          total,
          share: grand > 0 ? Math.round((total / grand) * 100) : 0,
        }))
        .sort((a, b) => b.total - a.total)
        .slice(0, 8);
    },

    strongestTroopCities() {
      return this.detailAccounts()
        .flatMap((account) => (account.cities || []).map((city) => ({
          ...city,
          player: account.player,
          serverLabel: account.server_label,
        })))
        .sort((a, b) => Number(b.troop_total || 0) - Number(a.troop_total || 0))
        .slice(0, 8);
    },

    shipSummary() {
      const accounts = this.detailAccounts();
      const maxShips = accounts.reduce((best, account) => {
        if (!best || Number(account.ship_total || 0) > Number(best.ship_total || 0)) return account;
        return best;
      }, null);
      return {
        totalShips: accounts.reduce((sum, account) => sum + Number(account.ship_total || 0), 0),
        accounts: accounts.length,
        avgPerAccount: accounts.length ? Math.round(accounts.reduce((sum, account) => sum + Number(account.ship_total || 0), 0) / accounts.length) : 0,
        topAccount: maxShips?.player || 'Nenhuma',
        topShips: Number(maxShips?.ship_total || 0),
        lowTransport: accounts.filter((account) => Number(account.free_transporters || 0) <= 0).length,
      };
    },

    shipAccountRanking() {
      const accounts = this.detailAccounts();
      const maxShips = Math.max(...accounts.map((account) => Number(account.ship_total || 0)), 1);
      return accounts
        .map((account) => ({
          ...account,
          shipWidth: Math.round((Number(account.ship_total || 0) / maxShips) * 100),
        }))
        .sort((a, b) => Number(b.ship_total || 0) - Number(a.ship_total || 0));
    },

    shipComposition() {
      const totals = new Map();
      this.detailAccounts().forEach((account) => {
        (account.fleet_columns || []).forEach((unit) => {
          const key = unit.name || 'Desconhecida';
          totals.set(key, (totals.get(key) || 0) + Number(unit.total || 0));
        });
      });
      const grand = Array.from(totals.values()).reduce((sum, value) => sum + value, 0);
      return Array.from(totals.entries())
        .map(([name, total]) => ({
          name,
          total,
          share: grand > 0 ? Math.round((total / grand) * 100) : 0,
        }))
        .sort((a, b) => b.total - a.total)
        .slice(0, 8);
    },

    strongestFleetCities() {
      return this.detailAccounts()
        .flatMap((account) => (account.cities || []).map((city) => ({
          ...city,
          player: account.player,
          serverLabel: account.server_label,
          freeTransporters: account.free_transporters,
          maxTransporters: account.max_transporters,
        })))
        .sort((a, b) => Number(b.fleet_total || 0) - Number(a.fleet_total || 0))
        .slice(0, 8);
    },

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
      if (this.kpiModal === 'gold') {
        this.$nextTick(() => {
          window.setTimeout(() => {
            this.renderHistoryChart('gold', 'gold-history-chart', {
              line: '#a67c00',
              fillTop: 'rgba(166, 124, 0, 0.30)',
              fillBottom: 'rgba(166, 124, 0, 0.02)',
              axis: '#8b6914',
              grid: 'rgba(139, 105, 20, 0.14)',
            });
          }, 30);
        });
      } else {
        this.destroyChart('gold-gold-history-chart');
      }

      if (this.kpiModal === 'income') {
        this.$nextTick(() => {
          window.setTimeout(() => {
            this.renderHistoryChart('income', 'income-history-chart', {
              line: '#2d7a2d',
              fillTop: 'rgba(45, 122, 45, 0.28)',
              fillBottom: 'rgba(45, 122, 45, 0.03)',
              axis: '#5c7a3a',
              grid: 'rgba(45, 122, 45, 0.10)',
            });
          }, 30);
        });
      } else {
        this.destroyChart('income-income-history-chart');
      }

      if (this.kpiModal === 'resources') {
        this.$nextTick(() => {
          window.setTimeout(() => {
            this.renderHistoryChart('resources', 'resources-history-chart', {
              line: '#8c6335',
              fillTop: 'rgba(140, 99, 53, 0.24)',
              fillBottom: 'rgba(140, 99, 53, 0.03)',
              axis: '#7a5a2d',
              grid: 'rgba(122, 90, 45, 0.10)',
            });
          }, 30);
        });
      } else {
        this.destroyChart('resources-resources-history-chart');
      }

      if (this.kpiModal === 'cities') {
        this.$nextTick(() => {
          window.setTimeout(() => {
            this.renderHistoryChart('cities', 'cities-history-chart', {
              line: '#4f86b9',
              fillTop: 'rgba(79, 134, 185, 0.24)',
              fillBottom: 'rgba(79, 134, 185, 0.03)',
              axis: '#4a6d89',
              grid: 'rgba(79, 134, 185, 0.10)',
            });
          }, 30);
        });
      } else {
        this.destroyChart('cities-cities-history-chart');
      }

      if (this.kpiModal === 'troops') {
        this.$nextTick(() => {
          window.setTimeout(() => {
            this.renderHistoryChart('troops', 'troops-history-chart', {
              line: '#bb5143',
              fillTop: 'rgba(187, 81, 67, 0.24)',
              fillBottom: 'rgba(187, 81, 67, 0.03)',
              axis: '#8c4c45',
              grid: 'rgba(187, 81, 67, 0.10)',
            });
          }, 30);
        });
      } else {
        this.destroyChart('troops-troops-history-chart');
      }

      if (this.kpiModal === 'ships') {
        this.$nextTick(() => {
          window.setTimeout(() => {
            this.renderHistoryChart('ships', 'ships-history-chart', {
              line: '#4a7c9e',
              fillTop: 'rgba(74, 124, 158, 0.24)',
              fillBottom: 'rgba(74, 124, 158, 0.03)',
              axis: '#4a6d89',
              grid: 'rgba(74, 124, 158, 0.10)',
            });
          }, 30);
        });
      } else {
        this.destroyChart('ships-ships-history-chart');
      }
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

    initProjectionValues() {
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

window.gameDashboard = gameDashboard;
