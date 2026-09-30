(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    root.UnifiedDashboardContract = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const SIGNAL_TYPES = ['Dual', '20D_only', 'T1_only'];
    const DEFAULT_STRESS_TEST_REQUEST = Object.freeze({
        market_mode: 'volatile',
        horizon_days: 14,
        weights: Object.freeze({
            Dual: 3,
            '20D_only': 2,
            'T1_only': 1,
        }),
    });

    function round(value, digits = 2) {
        if (!Number.isFinite(value)) return null;
        const factor = 10 ** digits;
        return Math.round(value * factor) / factor;
    }

    function numberOrFallback(value, fallback = 0) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
    }

    function numberOrNull(value) {
        if (value === undefined || value === null || value === '') return null;
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : null;
    }

    function stringOrNull(value) {
        if (value === undefined || value === null || value === '') return null;
        return String(value);
    }

    function arrayOrEmpty(value) {
        return Array.isArray(value) ? value : [];
    }

    function cloneDefaultStressRequest() {
        return {
            market_mode: DEFAULT_STRESS_TEST_REQUEST.market_mode,
            horizon_days: DEFAULT_STRESS_TEST_REQUEST.horizon_days,
            weights: {
                Dual: DEFAULT_STRESS_TEST_REQUEST.weights.Dual,
                '20D_only': DEFAULT_STRESS_TEST_REQUEST.weights['20D_only'],
                'T1_only': DEFAULT_STRESS_TEST_REQUEST.weights['T1_only'],
            },
        };
    }

    function normalizeSignalType(value) {
        return SIGNAL_TYPES.includes(value) ? value : null;
    }

    function normalizePositionStatus(value) {
        return ['pending', 'open', 'closed', 'stopped_out'].includes(value) ? value : 'pending';
    }

    function normalizeExitPolicy(value) {
        return ['T1', '20D'].includes(value) ? value : null;
    }

    function normalizeRegimeSignal(value) {
        return ['green', 'light_green', 'yellow', 'red'].includes(value) ? value : null;
    }

    function normalizeMarketMode(value) {
        return ['bull', 'volatile', 'bear'].includes(value) ? value : DEFAULT_STRESS_TEST_REQUEST.market_mode;
    }

    function zeroSignalCounts() {
        return { Dual: 0, '20D_only': 0, 'T1_only': 0 };
    }

    function fullSignalWeights() {
        return { Dual: 0, '20D_only': 0, 'T1_only': 0, cash: 100 };
    }

    function normalizeSignalCounts(input) {
        const output = zeroSignalCounts();
        if (!input || typeof input !== 'object') return output;

        for (const key of SIGNAL_TYPES) {
            output[key] = Math.max(0, numberOrFallback(input[key], 0));
        }
        return output;
    }

    function normalizeSignalWeights(input) {
        const output = fullSignalWeights();
        if (!input || typeof input !== 'object') return output;

        for (const key of SIGNAL_TYPES) {
            output[key] = Math.max(0, round(numberOrFallback(input[key], 0), 2) || 0);
        }
        output.cash = Math.max(0, round(numberOrFallback(input.cash, 100), 2) || 0);
        return output;
    }

    function normalizePortfolioSummary(summary) {
        const filters = (summary && typeof summary.filters === 'object') ? summary.filters : {};
        return {
            run_count: Math.max(0, numberOrFallback(summary && summary.run_count, 0)),
            latest_run_date: stringOrNull(summary && summary.latest_run_date),
            latest_signal_date: stringOrNull(summary && summary.latest_signal_date),
            latest_mark_date: stringOrNull(summary && summary.latest_mark_date),
            total_positions: Math.max(0, numberOrFallback(summary && summary.total_positions, 0)),
            total_tickers: Math.max(0, numberOrFallback(summary && summary.total_tickers, 0)),
            active_positions: Math.max(0, numberOrFallback(summary && summary.active_positions, 0)),
            pending_positions: Math.max(0, numberOrFallback(summary && summary.pending_positions, 0)),
            open_positions: Math.max(0, numberOrFallback(summary && summary.open_positions, 0)),
            closed_positions: Math.max(0, numberOrFallback(summary && summary.closed_positions, 0)),
            stopped_out_positions: Math.max(0, numberOrFallback(summary && summary.stopped_out_positions, 0)),
            invested_weight_pct: round(numberOrFallback(summary && summary.invested_weight_pct, 0), 2) || 0,
            avg_open_return_pct: numberOrNull(summary && summary.avg_open_return_pct),
            avg_closed_return_pct: numberOrNull(summary && summary.avg_closed_return_pct),
            signal_type_counts: normalizeSignalCounts(summary && summary.signal_type_counts),
            filters: {
                status: arrayOrEmpty(filters.status).map(String),
                signal_type: normalizeSignalType(filters.signal_type),
            },
        };
    }

    function normalizePortfolioRow(row) {
        return {
            id: Math.max(0, numberOrFallback(row && row.id, 0)),
            prediction_date: stringOrNull(row && row.prediction_date),
            last_signal_date: stringOrNull(row && row.last_signal_date),
            ticker: stringOrNull(row && row.ticker) || '',
            name: stringOrNull(row && row.name) || '',
            sector: stringOrNull(row && row.sector),
            signal_type: normalizeSignalType(row && row.signal_type),
            entry_signal_type: normalizeSignalType(row && row.entry_signal_type),
            status: normalizePositionStatus(row && row.status),
            target_units: Math.max(0, numberOrFallback(row && row.target_units, 0)),
            target_weight_pct: round(numberOrFallback(row && row.target_weight_pct, 0), 2) || 0,
            planned_entry_ref_price: numberOrNull(row && row.planned_entry_ref_price),
            entry_date: stringOrNull(row && row.entry_date),
            entry_price: numberOrNull(row && row.entry_price),
            latest_mark_date: stringOrNull(row && row.latest_mark_date),
            latest_price: numberOrNull(row && row.latest_price),
            exit_date: stringOrNull(row && row.exit_date),
            exit_price: numberOrNull(row && row.exit_price),
            return_pct: numberOrNull(row && row.return_pct),
            max_drawdown_pct: numberOrNull(row && row.max_drawdown_pct),
            exit_policy: normalizeExitPolicy(row && row.exit_policy),
            hold_days_target: Math.max(0, numberOrFallback(row && row.hold_days_target, 0)),
            days_observed: Math.max(0, numberOrFallback(row && row.days_observed, 0)),
            upgrade_count: Math.max(0, numberOrFallback(row && row.upgrade_count, 0)),
        };
    }

    function normalizeSignalRow(row) {
        return {
            prediction_date: stringOrNull(row && row.prediction_date),
            ticker: stringOrNull(row && row.ticker) || '',
            name: stringOrNull(row && row.name) || '',
            sector: stringOrNull(row && row.sector),
            signal_type: normalizeSignalType(row && row.signal_type),
            target_units: Math.max(0, numberOrFallback(row && row.target_units, 0)),
            target_weight_pct: round(numberOrFallback(row && row.target_weight_pct, 0), 2) || 0,
            rank_20d: numberOrNull(row && row.rank_20d),
            rank_t1: numberOrNull(row && row.rank_t1),
            hit_prob_3pct: numberOrNull(row && row.hit_prob_3pct),
            pred_return_20d: numberOrNull(row && row.pred_return_20d),
            recommendation_20d: stringOrNull(row && row.recommendation_20d),
            recommendation_t1: stringOrNull(row && row.recommendation_t1),
            setup_tags_t1: stringOrNull(row && row.setup_tags_t1),
            risk_tags_20d: stringOrNull(row && row.risk_tags_20d),
            risk_tags_t1: stringOrNull(row && row.risk_tags_t1),
            close_ref: numberOrNull(row && row.close_ref),
        };
    }

    function normalizeSignalBoard(payload) {
        const rows = arrayOrEmpty(payload && payload.data).map(normalizeSignalRow);
        const filters = (payload && typeof payload.filters === 'object') ? payload.filters : {};
        return {
            prediction_date: stringOrNull(payload && payload.prediction_date),
            source_file: stringOrNull(payload && payload.source_file),
            total: Math.max(0, numberOrFallback(payload && payload.total, rows.length)),
            filtered_count: Math.max(0, numberOrFallback(payload && payload.filtered_count, rows.length)),
            signal_counts: normalizeSignalCounts(payload && payload.signal_counts),
            filters: {
                signal_type: normalizeSignalType(filters.signal_type),
            },
            rows,
        };
    }

    function normalizeRegime(payload) {
        const indicators = (payload && typeof payload.indicators === 'object') ? payload.indicators : {};
        return {
            signal: normalizeRegimeSignal(payload && payload.signal),
            label: stringOrNull(payload && payload.label),
            advice: stringOrNull(payload && payload.advice),
            reasons: arrayOrEmpty(payload && payload.reasons).map(String),
            indicators: {
                twii_date: stringOrNull(indicators.twii_date),
                twii: numberOrNull(indicators.twii),
                twii_ma20: numberOrNull(indicators.twii_ma20),
                twii_vs_ma20: numberOrNull(indicators.twii_vs_ma20),
                twii_5d_ret: numberOrNull(indicators.twii_5d_ret),
                twii_1d_ret: numberOrNull(indicators.twii_1d_ret),
                vix: numberOrNull(indicators.vix),
                vix_1d_chg: numberOrNull(indicators.vix_1d_chg),
                vix_5d_chg: numberOrNull(indicators.vix_5d_chg),
                sox_5d_ret: numberOrNull(indicators.sox_5d_ret),
            },
        };
    }

    function normalizeEquity(payload) {
        if (!payload || payload.status === 'error') {
            return {
                as_of_date: null,
                summary: {
                    start_nav_pct: 100,
                    latest_nav_pct: 100,
                    mdd_pct: 0,
                    realized_sharpe: null,
                    realized_calmar: null,
                    points: 0,
                },
                series: [],
            };
        }

        const summary = (payload && typeof payload.summary === 'object') ? payload.summary : {};
        const series = arrayOrEmpty(payload && payload.series).map(function (point) {
            return {
                date: stringOrNull(point && point.date),
                nav_pct: numberOrFallback(point && point.nav_pct, 100),
                drawdown_pct: numberOrFallback(point && point.drawdown_pct, 0),
                exposure_pct: numberOrFallback(point && point.exposure_pct, 0),
            };
        });

        return {
            as_of_date: stringOrNull(payload.as_of_date),
            summary: {
                start_nav_pct: numberOrFallback(summary.start_nav_pct, 100),
                latest_nav_pct: numberOrFallback(summary.latest_nav_pct, 100),
                mdd_pct: numberOrFallback(summary.mdd_pct, 0),
                realized_sharpe: numberOrNull(summary.realized_sharpe),
                realized_calmar: numberOrNull(summary.realized_calmar),
                points: Math.max(0, numberOrFallback(summary.points, series.length)),
            },
            series,
        };
    }

    function normalizeStressRequest(request) {
        const normalized = cloneDefaultStressRequest();
        if (!request || typeof request !== 'object') return normalized;

        const weights = (request && typeof request.weights === 'object') ? request.weights : {};
        normalized.market_mode = normalizeMarketMode(request.market_mode);
        normalized.horizon_days = Math.max(0, Math.trunc(numberOrFallback(request.horizon_days, normalized.horizon_days)));
        normalized.weights = {
            Dual: Math.max(0, numberOrFallback(weights.Dual, normalized.weights.Dual)),
            '20D_only': Math.max(0, numberOrFallback(weights['20D_only'], normalized.weights['20D_only'])),
            'T1_only': Math.max(0, numberOrFallback(weights['T1_only'], normalized.weights['T1_only'])),
        };
        return normalized;
    }

    function normalizeStressTest(payload, fallbackRequest) {
        const request = normalizeStressRequest((payload && payload.request) || fallbackRequest);
        if (!payload || payload.status === 'error') {
            return {
                request,
                summary: {
                    projected_nav_pct: 100,
                    projected_mdd_pct: 0,
                    expected_sharpe: null,
                    expected_calmar: null,
                },
                allocation: {
                    candidate_counts: zeroSignalCounts(),
                    allocated_weight_pct: fullSignalWeights(),
                },
                nav_projection: [{ day: 0, date: null, nav_pct: 100, drawdown_pct: 0, exposure_pct: 0 }],
            };
        }

        const summary = (payload && typeof payload.summary === 'object') ? payload.summary : {};
        const allocation = (payload && typeof payload.allocation === 'object') ? payload.allocation : {};
        const navProjection = arrayOrEmpty(payload && payload.nav_projection).map(function (point) {
            return {
                day: Math.max(0, Math.trunc(numberOrFallback(point && point.day, 0))),
                date: stringOrNull(point && point.date),
                nav_pct: numberOrFallback(point && point.nav_pct, 100),
                drawdown_pct: numberOrFallback(point && point.drawdown_pct, 0),
                exposure_pct: numberOrFallback(point && point.exposure_pct, 0),
            };
        });

        return {
            request,
            summary: {
                projected_nav_pct: numberOrFallback(summary.projected_nav_pct, 100),
                projected_mdd_pct: numberOrFallback(summary.projected_mdd_pct, 0),
                expected_sharpe: numberOrNull(summary.expected_sharpe),
                expected_calmar: numberOrNull(summary.expected_calmar),
            },
            allocation: {
                candidate_counts: normalizeSignalCounts(allocation.candidate_counts),
                allocated_weight_pct: normalizeSignalWeights(allocation.allocated_weight_pct),
            },
            nav_projection: navProjection.length
                ? navProjection
                : [{ day: 0, date: null, nav_pct: 100, drawdown_pct: 0, exposure_pct: 0 }],
        };
    }

    function computeExecutionGap(rows) {
        let weightedGapSum = 0;
        let sampleWeight = 0;

        for (const row of rows) {
            const entryPrice = numberOrNull(row.entry_price);
            const plannedEntryRefPrice = numberOrNull(row.planned_entry_ref_price);
            const weightPct = Math.max(0, numberOrFallback(row.target_weight_pct, 0));

            if (entryPrice === null || plannedEntryRefPrice === null || plannedEntryRefPrice <= 0 || weightPct <= 0) {
                continue;
            }

            const gapPct = ((entryPrice / plannedEntryRefPrice) - 1) * 100;
            weightedGapSum += gapPct * weightPct;
            sampleWeight += weightPct;
        }

        return {
            execution_gap_pct: sampleWeight > 0 ? round(weightedGapSum / sampleWeight, 2) : null,
            execution_gap_sample_weight_pct: sampleWeight > 0 ? round(sampleWeight, 2) || 0 : 0,
        };
    }

    function computeUpgradeMetrics(rows) {
        let upgradePositionCount = 0;
        let upgradeEventCount = 0;

        for (const row of rows) {
            const upgradeCount = Math.max(0, numberOrFallback(row.upgrade_count, 0));
            const entrySignalType = normalizeSignalType(row.entry_signal_type);
            const currentSignalType = normalizeSignalType(row.signal_type);
            const wasUpgraded = upgradeCount > 0 || (entrySignalType && currentSignalType && entrySignalType !== currentSignalType);

            if (wasUpgraded) upgradePositionCount += 1;
            upgradeEventCount += upgradeCount;
        }

        return {
            upgrade_position_count: upgradePositionCount,
            upgrade_event_count: upgradeEventCount,
        };
    }

    function computeStopLossMetrics(summary) {
        const closed = Math.max(0, numberOrFallback(summary.closed_positions, 0));
        const stoppedOut = Math.max(0, numberOrFallback(summary.stopped_out_positions, 0));
        const sampleCount = closed + stoppedOut;

        return {
            stop_loss_rate_pct: sampleCount > 0 ? round((stoppedOut / sampleCount) * 100, 2) : null,
            stop_loss_sample_count: sampleCount,
        };
    }

    function computeAllocation(activeRows, signalCounts, activeExposurePct) {
        const weights = fullSignalWeights();
        let allocatedTotal = 0;
        void activeExposurePct;

        for (const row of activeRows) {
            const signalType = normalizeSignalType(row.signal_type);
            if (!signalType) continue;

            const weightPct = Math.max(0, numberOrFallback(row.target_weight_pct, 0));
            weights[signalType] = round(weights[signalType] + weightPct, 2) || 0;
            allocatedTotal += weightPct;
        }

        const roundedAllocated = round(allocatedTotal, 2) || 0;
        weights.cash = round(Math.max(0, 100 - roundedAllocated), 2);
        if (weights.cash === null) weights.cash = 100;

        return {
            by_signal_type_count: normalizeSignalCounts(signalCounts),
            by_signal_type_weight_pct: normalizeSignalWeights(weights),
            donut_slices: [
                { key: 'Dual', label: 'Dual', value: weights.Dual },
                { key: '20D_only', label: '20D_only', value: weights['20D_only'] },
                { key: 'T1_only', label: 'T1_only', value: weights['T1_only'] },
                { key: 'cash', label: 'Cash', value: weights.cash },
            ],
        };
    }

    function emptyPayloadError(source, message) {
        return {
            source,
            message: message || (source + ' payload is missing'),
        };
    }

    function collectErrors(payloads) {
        const errors = [];
        const requiredPayloads = ['portfolioAll', 'portfolioActive', 'latestSignals', 'regime'];

        for (const key of requiredPayloads) {
            const payload = payloads[key];
            if (!payload) {
                errors.push(emptyPayloadError(key));
                continue;
            }

            if (payload.status === 'error') {
                errors.push(emptyPayloadError(key, payload.message || payload.error || (key + ' returned error')));
            }
        }

        return errors;
    }

    function buildDashboardContract(payloads, options) {
        const resolvedOptions = options || {};
        const portfolioAllPayload = payloads && payloads.portfolioAll;
        const portfolioActivePayload = payloads && payloads.portfolioActive;
        const latestSignalsPayload = payloads && payloads.latestSignals;
        const regimePayload = payloads && payloads.regime;
        const equityPayload = payloads && payloads.equity;
        const stressTestPayload = payloads && payloads.stressTest;
        const allRows = arrayOrEmpty(portfolioAllPayload && portfolioAllPayload.data).map(normalizePortfolioRow);
        const activeRows = arrayOrEmpty(portfolioActivePayload && portfolioActivePayload.data).map(normalizePortfolioRow);
        const allSummary = normalizePortfolioSummary(portfolioAllPayload && portfolioAllPayload.summary);
        const activeSummary = normalizePortfolioSummary(portfolioActivePayload && portfolioActivePayload.summary);
        const signalBoard = normalizeSignalBoard(latestSignalsPayload);
        const regime = normalizeRegime(regimePayload);
        const equity = normalizeEquity(equityPayload);
        const stressRequest = normalizeStressRequest((resolvedOptions && resolvedOptions.stressTestRequest) || (payloads && payloads.stressTestRequest));
        const stressTest = normalizeStressTest(stressTestPayload, stressRequest);
        const executionGap = computeExecutionGap(allRows);
        const stopLossMetrics = computeStopLossMetrics(allSummary);
        const upgradeMetrics = computeUpgradeMetrics(allRows);
        const allocation = computeAllocation(activeRows, signalBoard.signal_counts, activeSummary.invested_weight_pct);
        const tableMode = resolvedOptions.tableMode === 'active' ? 'active' : 'all';
        const portfolioRows = tableMode === 'active' ? activeRows : allRows;
        const upstreamErrors = collectErrors({
            portfolioAll: portfolioAllPayload,
            portfolioActive: portfolioActivePayload,
            latestSignals: latestSignalsPayload,
            regime: regimePayload,
        });

        return {
            status: upstreamErrors.length ? 'error' : 'success',
            generated_at: stringOrNull((payloads && payloads.generatedAt) || resolvedOptions.generatedAt) || new Date().toISOString(),
            live_tracker: {
                as_of_date: allSummary.latest_mark_date || allSummary.latest_signal_date || allSummary.latest_run_date,
                regime,
                kpis: {
                    total_positions: allSummary.total_positions,
                    active_positions: allSummary.active_positions,
                    pending_positions: allSummary.pending_positions,
                    open_positions: allSummary.open_positions,
                    closed_positions: allSummary.closed_positions,
                    stopped_out_positions: allSummary.stopped_out_positions,
                    active_exposure_pct: activeSummary.invested_weight_pct,
                    execution_gap_pct: executionGap.execution_gap_pct,
                    execution_gap_sample_weight_pct: executionGap.execution_gap_sample_weight_pct,
                    stop_loss_rate_pct: stopLossMetrics.stop_loss_rate_pct,
                    stop_loss_sample_count: stopLossMetrics.stop_loss_sample_count,
                    upgrade_position_count: upgradeMetrics.upgrade_position_count,
                    upgrade_event_count: upgradeMetrics.upgrade_event_count,
                },
                allocation,
                portfolio_table: {
                    total_rows: portfolioRows.length,
                    rows: portfolioRows,
                },
                signal_board: signalBoard,
            },
            equity,
            stress_test: stressTest,
            error: upstreamErrors.length
                ? {
                    code: 'UPSTREAM_PAYLOAD_ERROR',
                    message: upstreamErrors.map(function (item) {
                        return item.source + ': ' + item.message;
                    }).join(' | '),
                    upstream: upstreamErrors.map(function (item) {
                        return item.source;
                    }).join(','),
                }
                : null,
        };
    }

    async function safeFetch(fetchJsonImpl, url, init) {
        try {
            return await fetchJsonImpl(url, init);
        } catch (error) {
            const message = error && error.message ? error.message : String(error);
            return { status: 'error', message: message, error: message, data: [] };
        }
    }

    async function fetchAndBuild(fetchJsonImpl, options) {
        if (typeof fetchJsonImpl !== 'function') {
            throw new TypeError('fetchJsonImpl must be a function');
        }

        const resolvedOptions = options || {};
        const endpoints = resolvedOptions.endpoints || {};
        const stressTestRequest = normalizeStressRequest(resolvedOptions.stressTestRequest);
        const fetches = [
            safeFetch(fetchJsonImpl, endpoints.portfolioAll || '/api/unified/portfolio'),
            safeFetch(fetchJsonImpl, endpoints.portfolioActive || '/api/unified/portfolio?status=ACTIVE'),
            safeFetch(fetchJsonImpl, endpoints.latestSignals || '/api/unified/signals/latest'),
            safeFetch(fetchJsonImpl, endpoints.regime || '/api/market/regime'),
        ];

        if (resolvedOptions.equityPayload) {
            fetches.push(Promise.resolve(resolvedOptions.equityPayload));
        } else if (endpoints.equity) {
            fetches.push(safeFetch(fetchJsonImpl, endpoints.equity));
        } else {
            fetches.push(Promise.resolve(null));
        }

        if (resolvedOptions.stressTestPayload) {
            fetches.push(Promise.resolve(resolvedOptions.stressTestPayload));
        } else if (endpoints.stressTest) {
            fetches.push(safeFetch(fetchJsonImpl, endpoints.stressTest, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(stressTestRequest),
            }));
        } else {
            fetches.push(Promise.resolve(null));
        }

        const [portfolioAll, portfolioActive, latestSignals, regime, equity, stressTest] = await Promise.all(fetches);
        return buildDashboardContract({
            portfolioAll,
            portfolioActive,
            latestSignals,
            regime,
            equity,
            stressTest,
            stressTestRequest,
            generatedAt: resolvedOptions.generatedAt || new Date().toISOString(),
        }, resolvedOptions);
    }

    return {
        SIGNAL_TYPES,
        DEFAULT_STRESS_TEST_REQUEST: cloneDefaultStressRequest(),
        buildDashboardContract,
        fetchAndBuild,
    };
});
