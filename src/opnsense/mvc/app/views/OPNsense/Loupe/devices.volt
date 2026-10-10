{#
Copyright (C) 2026 Alok K. Dhir
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.
#}

<script src="{{ cache_safe('/ui/js/chart.umd.min.js') }}"></script>
<script src="{{ cache_safe('/ui/js/moment-with-locales.min.js') }}"></script>
<script src="{{ cache_safe('/ui/js/chartjs-adapter-moment.min.js') }}"></script>
<style>
    .loupe-bar { padding: 10px 15px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .loupe-bar .btn-group .btn.active { font-weight: bold; }
    .loupe-table th { cursor: pointer; white-space: nowrap; user-select: none; }
    .loupe-table th.sorted:after { content: " \25BE"; }
    .loupe-table th.sorted.asc:after { content: " \25B4"; }
    .loupe-table td.num, .loupe-table th.num { text-align: right; white-space: nowrap; }
    .loupe-table tr.clickable { cursor: pointer; }
    .loupe-muted { color: #888; }
    .loupe-type { cursor: help; border-bottom: 1px dotted #999; }
    .loupe-section { padding: 0 15px 15px; }
    .loupe-section h3 { margin-top: 15px; font-size: 16px; }
    #loupe-chart-wrap { height: 220px; padding: 0 15px; }
    #loupe-empty { padding: 20px 15px; }
    .loupe-edit { color: #999; cursor: pointer; visibility: hidden; float: right; padding: 3px 0 0 8px; }
    #loupe-detail-title .loupe-edit { float: none; font-size: 16px; padding-left: 10px; }
    .loupe-table tr:hover .loupe-edit, #loupe-detail-title:hover .loupe-edit { visibility: visible; }
    .loupe-edit:hover { color: #337ab7; }
    #loupe-edit-dialog .modal-header { padding: 15px 20px; }
    #loupe-edit-dialog .modal-body { padding: 20px 20px 5px; }
    #loupe-edit-dialog .modal-footer { padding: 15px 20px; }
    #loupe-edit-dialog .form-group { margin-bottom: 18px; }
    #loupe-edit-dialog label { display: block; margin-bottom: 6px; font-weight: bold; }
    #loupe-edit-dialog .form-control { width: 100%; }
    #loupe-edit-mac { margin-left: 8px; }
</style>
<script>
    const loupe = {
        bytes: function (b) {
            if (!b) return '0';
            const u = ['B', 'KB', 'MB', 'GB', 'TB'];
            let i = Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1000)));
            return (b / Math.pow(1000, i)).toFixed(i ? 1 : 0) + ' ' + u[i];
        },
        ago: function (ts) {
            if (!ts) return '';
            const s = Date.now() / 1000 - ts;
            if (s < 600) return '{{ lang._("now") }}';
            if (s < 3600) return Math.round(s / 60) + ' min';
            if (s < 86400) return Math.round(s / 3600) + ' h';
            return Math.round(s / 86400) + ' d';
        },
        esc: function (s) { return $('<span>').text(s == null ? '' : String(s)).html(); },
        // sortable table: cols = [{key, label, num, fmt(row)}]
        table: function ($el, cols, rows, sortKey, onClick) {
            let key = sortKey, asc = false;
            const draw = function () {
                const sorted = rows.slice().sort(function (a, b) {
                    const x = a[key], y = b[key];
                    const c = (typeof x === 'number' && typeof y === 'number') ? x - y : String(x).localeCompare(String(y));
                    return asc ? c : -c;
                });
                let h = '<thead><tr>' + cols.map(function (c) {
                    return '<th data-key="' + c.key + '" class="' + (c.num ? 'num ' : '') + (c.key === key ? 'sorted' + (asc ? ' asc' : '') : '') + '">' + c.label + '</th>';
                }).join('') + '</tr></thead><tbody>';
                sorted.forEach(function (r, i) {
                    h += '<tr' + (onClick ? ' class="clickable" data-i="' + rows.indexOf(r) + '"' : '') + '>' + cols.map(function (c) {
                        return '<td' + (c.num ? ' class="num"' : '') + '>' + (c.fmt ? c.fmt(r) : loupe.esc(r[c.key])) + '</td>';
                    }).join('') + '</tr>';
                });
                $el.html(h + '</tbody>');
                $el.find('th').click(function () {
                    const k = $(this).data('key');
                    if (k === key) { asc = !asc; } else { key = k; asc = !cols.find(c => c.key === k).num; }
                    draw();
                });
                if (onClick) $el.find('tr.clickable').click(function (e) {
                    if ($(e.target).closest('.loupe-edit, a').length) return;   // the edit button / links do their own thing
                    onClick(rows[$(this).data('i')]);
                });
            };
            draw();
        }
    };

    $(document).ready(function () {
        let hours = parseFloat(localStorage.getItem('loupe.hours') || '24');
        let chart = null;

        function periodButtons() {
            $('#loupe-period .btn').removeClass('active').filter('[data-hours="' + hours + '"]').addClass('active');
        }

        function typeCell(r) {
            if (!r.type) return '<span class="loupe-muted">?</span>';
            return '<span class="loupe-type" title="' + loupe.esc(r.type_source || '') + '">' + loupe.esc(r.type) + '</span>';
        }

        const commonTypes = ['Computer', 'Mac', 'Windows PC', 'iPhone', 'iPad', 'Android phone', 'Phone/tablet/laptop',
            'Apple TV', 'HomePod', 'TV', 'Roku', 'Google Cast device', 'Sonos speaker', 'Speaker / streamer', 'AV receiver',
            'PlayStation', 'Xbox', 'Nintendo Switch', 'Printer', 'Camera', 'Thermostat', 'Smart plug', 'Smart home device',
            'Smart home hub', 'Ceiling fan', 'Media server', 'Server', 'NAS', 'Network gear', 'Wi-Fi access point',
            'Appliance', 'Virtual machine', 'Watch', 'Car'];
        let knownTypes = [];

        // edit button: name + type for a MAC, stored in the plugin settings
        function editButton(r) {
            if (!r.mac) return '';
            return '<i class="fa fa-pencil loupe-edit" title="{{ lang._("Name this device") }}" data-mac="' + loupe.esc(r.mac)
                + '" data-name="' + loupe.esc(r.name !== r.ip ? r.name : '') + '" data-type="' + loupe.esc(r.type || '')
                + '" data-custom="' + (r.custom ? '1' : '') + '"></i>';
        }
        $(document).on('click', '.loupe-edit', function (e) {
            e.stopPropagation();
            const $b = $(this);
            $('#loupe-edit-mac').text($b.data('mac'));
            $('#loupe-edit-name').val($b.data('name'));
            $('#loupe-edit-type').val($b.data('type'));
            $('#loupe-types').html(Array.from(new Set(commonTypes.concat(knownTypes))).sort()
                .map(t => '<option value="' + loupe.esc(t) + '">').join(''));
            $('#loupe-edit-error').hide();
            $('#loupe-edit-reset').toggle(!!$b.data('custom'));
            $('#loupe-edit-dialog').modal('show');
        });
        $('#loupe-edit-save, #loupe-edit-reset').click(function () {
            const reset = this.id === 'loupe-edit-reset';
            ajaxCall('/api/loupe/settings/override', {
                mac: $('#loupe-edit-mac').text(),
                name: reset ? '' : $('#loupe-edit-name').val(),
                type: reset ? '' : $('#loupe-edit-type').val()
            }, function (data) {
                if (data.result === 'saved') {
                    $('#loupe-edit-dialog').modal('hide');
                    route();
                } else {
                    $('#loupe-edit-error').text(data.message || JSON.stringify(data.validations || data)).show();
                }
            });
        });

        function showDevices() {
            $('#loupe-detail').hide();
            $('#loupe-list').show();
            ajaxGet('/api/loupe/report/devices/' + hours, {}, function (data) {
                if (data.error) { $('#loupe-empty').text(data.error).show(); return; }
                $('#loupe-empty').toggle(!data.rows.length);
                knownTypes = data.rows.map(r => r.type).filter(Boolean);
                const total = data.rows.reduce((a, r) => [a[0] + r.down, a[1] + r.up], [0, 0]);
                $('#loupe-total').text(data.rows.length + ' {{ lang._("devices") }} · ' + loupe.bytes(total[0]) + ' {{ lang._("down") }} · ' + loupe.bytes(total[1]) + ' {{ lang._("up") }}');
                loupe.table($('#loupe-devices'), [
                    {key: 'name', label: '{{ lang._("Device") }}', fmt: r => loupe.esc(r.name) + (r.name !== r.ip ? ' <span class="loupe-muted">' + loupe.esc(r.ip) + '</span>' : '') + editButton(r)},
                    {key: 'type', label: '{{ lang._("Type") }}', fmt: typeCell},
                    {key: 'vendor', label: '{{ lang._("Vendor") }}'},
                    {key: 'down', label: '{{ lang._("Down") }}', num: true, fmt: r => loupe.bytes(r.down)},
                    {key: 'up', label: '{{ lang._("Up") }}', num: true, fmt: r => loupe.bytes(r.up)},
                    {key: 'top', label: '{{ lang._("Top services") }}', fmt: r => loupe.esc(r.top.join(', '))},
                    {key: 'last', label: '{{ lang._("Last seen") }}', num: true, fmt: r => loupe.ago(r.last)}
                ], data.rows, 'down', function (r) {
                    window.location.hash = 'device=' + (r.mac || r.ip);
                });
            });
        }

        function showDevice(id) {
            $('#loupe-list').hide();
            $('#loupe-detail').show();
            ajaxGet('/api/loupe/report/device/' + id.replace(/[^0-9a-fA-F:.]/g, '') + '/' + hours, {}, function (data) {
                if (data.error) { $('#loupe-detail-title').text(data.error); return; }
                const d = data.device || {};
                $('#loupe-detail-title').html(loupe.esc(d.name || d.ip || id)
                    + editButton({mac: d.mac, ip: d.ip, name: d.name || d.ip, type: d.type, custom: d.custom})
                    + ' <small>' + loupe.esc([d.type, d.vendor, d.ip, d.mac].filter(Boolean).join(' · ')) + '</small>'
                    + (d.type_source ? ' <small class="loupe-muted">(' + loupe.esc(d.type_source) + ')</small>' : ''));
                const tl = data.timeline;
                if (chart) chart.destroy();
                chart = new Chart(document.getElementById('loupe-chart'), {
                    type: 'bar',
                    data: {
                        labels: tl.map(p => p[0] * 1000),
                        datasets: [
                            {label: '{{ lang._("Down") }}', data: tl.map(p => p[1]), backgroundColor: 'rgba(54,162,235,0.7)', stack: 's'},
                            {label: '{{ lang._("Up") }}', data: tl.map(p => p[2]), backgroundColor: 'rgba(255,159,64,0.7)', stack: 's'}
                        ]
                    },
                    options: {
                        maintainAspectRatio: false, animation: false,
                        scales: {
                            x: {type: 'time', stacked: true, time: {tooltipFormat: 'lll'}},
                            y: {stacked: true, ticks: {callback: v => loupe.bytes(v)}}
                        },
                        plugins: {tooltip: {callbacks: {label: c => c.dataset.label + ': ' + loupe.bytes(c.raw)}}}
                    }
                });
                loupe.table($('#loupe-services'), [
                    {key: 'service', label: '{{ lang._("Service") }}', fmt: r => loupe.esc(r.service) + ' <span class="loupe-muted">' + loupe.esc(r.names.slice(0, 3).join(', ')) + '</span>'},
                    {key: 'down', label: '{{ lang._("Down") }}', num: true, fmt: r => loupe.bytes(r.down)},
                    {key: 'up', label: '{{ lang._("Up") }}', num: true, fmt: r => loupe.bytes(r.up)},
                    {key: 'conns', label: '{{ lang._("Connections") }}', num: true}
                ], data.services, 'down');
                loupe.table($('#loupe-sites'), [
                    {key: 'name', label: '{{ lang._("Site") }}', fmt: r => '<a href="/ui/loupe/index/lookup#q=' + encodeURIComponent(r.name) + '">' + loupe.esc(r.name) + '</a>'},
                    {key: 'service', label: '{{ lang._("Service") }}'},
                    {key: 'down', label: '{{ lang._("Down") }}', num: true, fmt: r => loupe.bytes(r.down)},
                    {key: 'up', label: '{{ lang._("Up") }}', num: true, fmt: r => loupe.bytes(r.up)},
                    {key: 'conns', label: '{{ lang._("Connections") }}', num: true},
                    {key: 'last', label: '{{ lang._("Last seen") }}', num: true, fmt: r => loupe.ago(r.last)}
                ], data.sites, 'down');
                loupe.table($('#loupe-ports'), [
                    {key: 'port', label: '{{ lang._("Protocol / port") }}'},
                    {key: 'down', label: '{{ lang._("Down") }}', num: true, fmt: r => loupe.bytes(r.down)},
                    {key: 'up', label: '{{ lang._("Up") }}', num: true, fmt: r => loupe.bytes(r.up)},
                    {key: 'conns', label: '{{ lang._("Connections") }}', num: true}
                ], data.ports, 'down');
            });
        }

        function route() {
            const m = window.location.hash.match(/device=([^&]+)/);
            if (m) { showDevice(decodeURIComponent(m[1])); } else { showDevices(); }
        }

        $('#loupe-period .btn').click(function () {
            hours = parseFloat($(this).data('hours'));
            localStorage.setItem('loupe.hours', hours);
            periodButtons();
            route();
        });
        $('#loupe-back').click(function (e) { e.preventDefault(); window.location.hash = ''; });
        $(window).on('hashchange', route);
        periodButtons();
        route();
    });
</script>

<div class="content-box">
    <div class="loupe-bar">
        <div class="btn-group" id="loupe-period">
            <button class="btn btn-default btn-sm" data-hours="1">{{ lang._('1 hour') }}</button>
            <button class="btn btn-default btn-sm" data-hours="24">{{ lang._('24 hours') }}</button>
            <button class="btn btn-default btn-sm" data-hours="168">{{ lang._('7 days') }}</button>
            <button class="btn btn-default btn-sm" data-hours="720">{{ lang._('30 days') }}</button>
            <button class="btn btn-default btn-sm" data-hours="8760">{{ lang._('1 year') }}</button>
        </div>
        <span id="loupe-total" class="loupe-muted"></span>
    </div>

    <div id="loupe-list">
        <div id="loupe-empty" style="display: none">{{ lang._('No traffic recorded in this period yet. Data appears a minute after Loupe starts.') }}</div>
        <table id="loupe-devices" class="table table-condensed table-hover table-striped loupe-table"></table>
    </div>

    <div id="loupe-detail" style="display: none">
        <div class="loupe-section">
            <a href="#" id="loupe-back">&larr; {{ lang._('All devices') }}</a>
            <h2 id="loupe-detail-title" style="font-size: 20px"></h2>
        </div>
        <div id="loupe-chart-wrap"><canvas id="loupe-chart"></canvas></div>
        <div class="loupe-section"><h3>{{ lang._('Services') }}</h3>
            <table id="loupe-services" class="table table-condensed table-striped loupe-table"></table></div>
        <div class="loupe-section"><h3>{{ lang._('Sites') }}</h3>
            <table id="loupe-sites" class="table table-condensed table-striped loupe-table"></table></div>
        <div class="loupe-section"><h3>{{ lang._('Protocols and ports') }}</h3>
            <table id="loupe-ports" class="table table-condensed table-striped loupe-table"></table></div>
    </div>
</div>

<div class="modal fade" id="loupe-edit-dialog" tabindex="-1" role="dialog">
    <div class="modal-dialog" role="document">
        <div class="modal-content">
            <div class="modal-header">
                <button type="button" class="close" data-dismiss="modal">&times;</button>
                <h4 class="modal-title">{{ lang._('Name this device') }} <small id="loupe-edit-mac"></small></h4>
            </div>
            <div class="modal-body">
                <div class="form-group">
                    <label for="loupe-edit-name">{{ lang._('Name') }}</label>
                    <input type="text" class="form-control" id="loupe-edit-name" placeholder="{{ lang._('e.g. Kitchen iPad') }}"/>
                </div>
                <div class="form-group">
                    <label for="loupe-edit-type">{{ lang._('Type') }}</label>
                    <input type="text" class="form-control" id="loupe-edit-type" list="loupe-types" placeholder="{{ lang._('Leave empty to keep the detected type') }}"/>
                    <datalist id="loupe-types"></datalist>
                </div>
                <div id="loupe-edit-error" class="alert alert-danger" style="display: none"></div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-default pull-left" id="loupe-edit-reset">{{ lang._('Reset to automatic') }}</button>
                <button type="button" class="btn btn-default" data-dismiss="modal">{{ lang._('Cancel') }}</button>
                <button type="button" class="btn btn-primary" id="loupe-edit-save">{{ lang._('Save') }}</button>
            </div>
        </div>
    </div>
</div>
