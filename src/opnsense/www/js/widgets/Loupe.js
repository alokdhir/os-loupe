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
            <div class="loupe-widget" style="padding: 0 10px 10px">
                <style>
                    .loupe-widget .lw-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 0 8px; margin-top: 5px; }
                    .loupe-widget .lw-title { font-weight: bold; font-size: 12px; margin: 10px 0 2px; }
                    .loupe-widget .lw-row { display: flex; gap: 8px; align-items: flex-start; padding: 4px 0; border-top: 1px solid rgba(128,128,128,0.2); font-size: 12px; }
                    .loupe-widget .lw-main { flex: 1; min-width: 0; }
                    .loupe-widget .lw-main > div { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
                    .loupe-widget .lw-sub { color: #888; font-size: 11px; }
                    .loupe-widget .lw-val { white-space: nowrap; text-align: right; }
                </style>
                <div class="lw-head">
                    <strong>${this.translations.last24}</strong>
                    <span id="loupe-w-total" style="font-size: 12px; white-space: nowrap"></span>
                </div>
                <div class="canvas-container-noaspectratio" style="height: 60px; margin: 4px 0 10px">
                    <canvas id="loupe-w-spark"></canvas>
                </div>
                <div id="loupe-w-top"></div>
                <div id="loupe-w-now"></div>
                <div id="loupe-w-new"></div>
            </div>
        `);
    }

    static bytes(b) {
        if (!b) return '0';
        const u = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1000)));
        return (b / Math.pow(1000, i)).toFixed(i ? 1 : 0) + ' ' + u[i];
    }

    static rate(bps) {
        if (bps < 1000) return Math.round(bps) + ' b/s';
        const u = ['kb/s', 'Mb/s', 'Gb/s'];
        let i = -1;
        do { bps /= 1000; i++; } while (bps >= 1000 && i < u.length - 1);
        return bps.toFixed(1) + ' ' + u[i];
    }

    static esc(s) {
        return $('<span>').text(s == null ? '' : String(s)).html();
    }

    section(title, rows) {
        return `<div class="lw-title">${title}</div>${rows}`;
    }

    // one entry: name (and a grey second line) on the left, a value on the right; long text gets "..."
    row(main, sub, val) {
        return `<div class="lw-row"><div class="lw-main"><div>${main}</div>${sub ? `<div class="lw-sub">${sub}</div>` : ''}</div>`
            + (val ? `<div class="lw-val">${val}</div>` : '') + `</div>`;
    }

    link(r) {
        const id = r.mac || r.ip;
        return `<a href="/ui/loupe/#device=${Loupe.esc(id)}">${Loupe.esc(r.name || r.ip)}</a>`;
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
                scales: {x: {display: false, stacked: true}, y: {display: false, stacked: true}}
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

        $('#loupe-w-top').html(this.section(this.translations.top, d.top.map(r => this.row(
            this.link(r),
            Loupe.esc([r.model || r.type, (r.top || [])[0]].filter(Boolean).join(' · ')),
            `↓ ${Loupe.bytes(r.down)}`)).join('')));

        $('#loupe-w-now').html(this.section(this.translations.now, d.now.length ? d.now.map(r => this.row(
            this.link(r),
            Loupe.esc(r.service),
            `↓ ${Loupe.rate(r.down_rate)}<div class="lw-sub">↑ ${Loupe.rate(r.up_rate)}</div>`)).join('')
            : this.row(`<span class="lw-sub">${this.translations.quiet}</span>`)));

        let newRows;
        if (d.since && Date.now() / 1000 - d.since < 86400) {
            newRows = this.row(`<span class="lw-sub" style="white-space: normal">${this.translations.learning} ${new Date(d.since * 1000).toLocaleString()}</span>`);
        } else if (d.new.length) {
            newRows = d.new.map(r => this.row(this.link(r), Loupe.esc(r.model || r.type || r.vendor || ''),
                `<span class="lw-sub">${new Date(r.first_seen * 1000).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})}</span>`)).join('');
        } else {
            newRows = this.row(`<span class="lw-sub">${this.translations.nonew}</span>`);
        }
        $('#loupe-w-new').html(this.section(this.translations.new + (d.new_count > d.new.length ? ` (${d.new_count})` : ''), newRows));
    }

    onWidgetClose() {
        if (this.chart) this.chart.destroy();
    }
}
