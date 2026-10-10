/*
 * Copyright (C) 2026 Alok K. Dhir
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 */

export default class Loupe extends BaseWidget {
    constructor() {
        super();
        this.tickTimeout = 60;
        this.chart = null;
    }

    getGridOptions() {
        return {sizeToContent: 650, w: 4, minW: 2};
    }

    getMarkup() {
        return $(`
            <div class="loupe-widget" style="padding: 0 10px 10px; text-align: left">
                <style>
                    .loupe-widget, .loupe-widget * { text-align: left; }
                    .loupe-widget .lw-head { display: flex; flex-wrap: wrap; gap: 0 10px; align-items: baseline; margin-top: 5px; }
                    .loupe-widget .lw-axis { display: flex; justify-content: space-between; color: #888; font-size: 10px; }
                    .loupe-widget .lw-axis span:last-child { text-align: right; }
                    .loupe-widget .lw-title { font-weight: bold; font-size: 12px; margin: 10px 0 2px; }
                    .loupe-widget .lw-row { display: flex; gap: 8px; align-items: flex-start; padding: 4px 0; border-top: 1px solid rgba(128,128,128,0.2); font-size: 12px; }
                    .loupe-widget .lw-main { flex: 1; min-width: 0; }
                    .loupe-widget .lw-main > div { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
                    .loupe-widget .lw-sub { color: #888; font-size: 11px; }
                    .loupe-widget .lw-val { white-space: nowrap; }
                    .loupe-widget .lw-val, .loupe-widget .lw-val * { text-align: right; }
                </style>
                <div class="lw-head">
                    <strong>${this.translations.last24}</strong>
                    <span id="loupe-w-total" style="font-size: 12px; white-space: nowrap"></span>
                </div>
                <div class="canvas-container-noaspectratio" style="height: 60px; margin: 6px 0 2px">
                    <canvas id="loupe-w-spark"></canvas>
                </div>
                <div class="lw-axis"><span>${this.translations.ago24}</span><span>${this.translations.now}</span></div>
                <div id="loupe-w-top"></div>
            </div>
        `);
    }

    static bytes(b) {
        if (!b) return '0';
        const u = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1000)));
        return (b / Math.pow(1000, i)).toFixed(i ? 1 : 0) + ' ' + u[i];
    }

    static esc(s) {
        return $('<span>').text(s == null ? '' : String(s)).html();
    }

    // one entry: name (and a grey second line) on the left, a value on the right; long text gets "..."
    row(main, sub, val) {
        return `<div class="lw-row"><div class="lw-main"><div>${main}</div>${sub ? `<div class="lw-sub">${sub}</div>` : ''}</div>`
            + (val ? `<div class="lw-val">${val}</div>` : '') + `</div>`;
    }

    link(r) {
        return `<a href="/ui/loupe/#device=${Loupe.esc(r.mac || r.ip)}">${Loupe.esc(r.name || r.ip)}</a>`;
    }

    async onMarkupRendered() {
        const blue = 'rgba(54,162,235,0.8)', orange = 'rgba(255,159,64,0.8)';
        this.chart = new Chart($('#loupe-w-spark')[0].getContext('2d'), {
            type: 'bar',
            data: {labels: [], datasets: [
                {label: this.translations.down, data: [], backgroundColor: blue, stack: 's'},
                {label: this.translations.up, data: [], backgroundColor: orange, stack: 's'}
            ]},
            options: {
                maintainAspectRatio: false, animation: false,
                plugins: {legend: {display: false}, tooltip: {callbacks: {
                    title: items => new Date(items[0].label * 1000).toLocaleTimeString([], {hour: 'numeric'}),
                    label: c => c.dataset.label + ': ' + Loupe.bytes(c.raw)
                }}},
                scales: {
                    x: {stacked: true, ticks: {display: false}, grid: {display: false},
                        border: {display: true, color: 'rgba(128,128,128,0.5)'}},
                    y: {display: false, stacked: true, beginAtZero: true}
                }
            }
        });
    }

    async onWidgetTick() {
        const d = await this.ajaxCall('/api/loupe/report/widget');
        if (!d || d.error || !d.hours) {
            $('#loupe-w-top').html(`<a href="/ui/loupe/index/settings">${this.translations.nodata}</a>`);
            return;
        }
        if (!this.dataChanged('loupe', d)) {
            return;
        }
        $('#loupe-w-total').text(`↓ ${Loupe.bytes(d.down)}  ↑ ${Loupe.bytes(d.up)}`);
        this.chart.data.labels = d.hours.map(h => h[0]);
        this.chart.data.datasets[0].data = d.hours.map(h => h[1]);
        this.chart.data.datasets[1].data = d.hours.map(h => h[2]);
        this.chart.update();

        $('#loupe-w-top').html(`<div class="lw-title">${this.translations.top}</div>` + d.top.map(r => this.row(
            this.link(r),
            Loupe.esc(r.model || r.type || '') + ' · ' + (Loupe.esc((r.top || [])[0] || '') || '—'),
            `↓ ${Loupe.bytes(r.down)}<div class="lw-sub">↑ ${Loupe.bytes(r.up)}</div>`)).join(''));
    }

    onWidgetClose() {
        if (this.chart) this.chart.destroy();
    }
}
