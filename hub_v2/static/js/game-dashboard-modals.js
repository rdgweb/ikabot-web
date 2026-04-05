/**
 * Game Dashboard — Modal data functions (insights, rankings, compositions).
 * Mixed into the gameDashboard() Alpine component via Object.assign.
 */
function gameDashboardModals() {
  return {
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

    // ── Gold Modal ──

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

    // ── Income Modal ──

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

    // ── Resources Modal ──

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
      const totals = { wood: 0, wine: 0, marble: 0, crystal: 0, sulfur: 0 };
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

    // ── Cities Modal ──

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

    // ── Troops Modal ──

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

    // ── Ships Modal ──

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
  };
}
