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
    .loupe-edit { color: #999; cursor: pointer; visibility: hidden; padding-left: 8px; }
    .tabulator-row:hover .loupe-edit, #loupe-detail-title:hover .loupe-edit { visibility: visible; }
    @media (hover: none) { .loupe-edit { visibility: visible; } }
    .loupe-edit:hover { color: #337ab7; }
    .loupe-detail-title { font-size: 24px; margin: 8px 0 4px; }
    .loupe-detail-title small { display: block; margin-top: 6px; font-size: 13px; }
</style>
<script>
    $(document).ready(function () {
        let hours = parseFloat(localStorage.getItem('loupe.hours') || '24');
        let chart = null, current = null, grids = {};
        const esc = s => $('<span>').text(s == null ? '' : String(s)).html();
        const ago = ts => ts ? moment(ts * 1000).fromNow() : '';
        const dash = '<span class="text-muted" title="{{ lang._("No name seen for this traffic") }}">&mdash;</span>';

        function editButton(r) {
            if (!r.mac) return '';
            return '<i class="fa fa-pencil loupe-edit" title="{{ lang._("Name this device") }}" data-mac="' + esc(r.mac)
                + '" data-name="' + esc(r.custom && r.name !== r.ip ? r.name : '') + '" data-type="' + esc(r.type_source === 'set by you' ? r.type : '') + '"></i>';
        }

        const formatters = {
            device: function (column, row) {
                return '<a href="#device=' + esc(row.id) + '">' + esc(row.name) + '</a>'
                    + (row.name !== row.ip ? ' <span class="text-muted">' + esc(row.ip) + '</span>' : '') + editButton(row);
            },
            devtype: function (column, row) {
                if (!row.shown_type) return dash;
                const tip = (row.model && row.type_source !== 'set by you' ? row.type + ' · ' : '') + (row.type_source || '');
                return (row.icon ? '<i class="' + esc(row.icon) + ' fa-fw text-muted"></i> ' : '<i class="fa fa-fw"></i> ')
                    + '<span title="' + esc(tip) + '">' + esc(row.shown_type) + '</span>';
            },
            vendor: function (column, row) {
                if (!row.vendor_note) return esc(row.vendor);
                return '<span class="text-muted" title="' + esc(row.vendor_note) + '">' + esc(row.vendor || row.vendor_note) + '</span>';
            },
            services: function (column, row) {
                return (row.top || []).map(t => t ? esc(t) : dash).join(', ');
            },
            service: function (column, row) {
                return (row.service && row.service !== 'Other' ? esc(row.service) : dash)
                    + (row.names && row.names.length ? ' <span class="text-muted">' + esc(row.names.slice(0, 3).join(', ')) + '</span>' : '');
            },
            site: function (column, row) {
                return '<a href="/ui/loupe/index/lookup#q=' + encodeURIComponent(row.name) + '">' + esc(row.name) + '</a>';
            },
            sitesvc: function (column, row) {
                return row.service && row.service !== 'Other' ? esc(row.service) : dash;
            },
            ago: function (column, row) {
                return ago(row[column.id]);
            }
        };

        function grid(id, endpoint, extra) {
            if (grids[id]) {
                $('#' + id).bootgrid('reload');
                return;
            }
            grids[id] = $('#' + id).UIBootgrid({
                search: endpoint,
                options: {
                    selection: false,
                    multiSelect: false,
                    requestHandler: function (request) {
                        request.hours = hours;
                        return Object.assign(request, extra ? extra() : {});
                    },
                    formatters: formatters
                }
            });
        }

        function periodButtons() {
            $('#loupe-period .btn').removeClass('active').filter('[data-hours="' + hours + '"]').addClass('active');
        }

        function showDevices() {
            $('#loupe-detail').hide();
            $('#loupe-list').show();
            grid('grid-devices', '/api/loupe/report/search_devices');
        }

        function showDevice(id) {
            current = id;
            $('#loupe-list').hide();
            $('#loupe-detail').show();
            ajaxGet('/api/loupe/report/device/' + id.replace(/[^0-9a-fA-F:.]/g, '') + '/' + hours, {}, function (data) {
                if (data.error) { $('#loupe-detail-title').text(data.error); return; }
                const d = data.device || {};
                $('#loupe-detail-title').html((d.icon ? '<i class="' + esc(d.icon) + ' fa-fw text-muted"></i> ' : '')
                    + esc(d.name || d.ip || id) + editButton(d)
                    + ' <small class="text-muted">' + esc([d.model, d.type, d.vendor, d.ip, d.mac].filter(Boolean).join(' · '))
                    + (d.type_source ? ' (' + esc(d.type_source) + ')' : '') + '</small>');
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
                            y: {stacked: true, ticks: {callback: v => byteFormat(v, 1)}}
                        },
                        plugins: {tooltip: {callbacks: {label: c => c.dataset.label + ': ' + byteFormat(c.raw, 2)}}}
                    }
                });
            });
            const dev = () => ({device: current});
            grid('grid-services', '/api/loupe/report/search_services', dev);
            grid('grid-sites', '/api/loupe/report/search_sites', dev);
            grid('grid-ports', '/api/loupe/report/search_ports', dev);
        }

        function route() {
            const m = window.location.hash.match(/device=([^&]+)/);
            if (m) { showDevice(decodeURIComponent(m[1])); } else { showDevices(); }
        }

        /* name / type dialog (standard base_dialog, saved per MAC) */
        $(document).on('click', '.loupe-edit', function (e) {
            e.preventDefault();
            e.stopPropagation();
            const $b = $(this);
            clearFormValidation('frm_DialogDevice');
            $('#device\\.mac').val($b.data('mac')).prop('readonly', true);
            $('#device\\.name').val($b.data('name'));
            $('#device\\.type').val($b.data('type'));
            $('#DialogDevice').modal('show');
        });
        $('#DialogDevice').on('shown.bs.modal', function () { $('#device\\.name').focus().select(); });
        $('#frm_DialogDevice').on('submit', function (e) { e.preventDefault(); $('#btn_DialogDevice_save').click(); });
        $('#btn_DialogDevice_save').unbind('click').click(function () {
            ajaxCall('/api/loupe/settings/override', {
                mac: $('#device\\.mac').val(), name: $('#device\\.name').val(), type: $('#device\\.type').val()
            }, function (data) {
                if (data.result === 'saved') {
                    $('#DialogDevice').modal('hide');
                    route();
                } else if (data.validations) {
                    handleFormValidation('frm_DialogDevice', data.validations);
                }
            });
        });

        $('#loupe-period .btn').click(function () {
            hours = parseFloat($(this).data('hours'));
            localStorage.setItem('loupe.hours', hours);
            periodButtons();
            route();
        });
        $(window).on('hashchange', route);
        periodButtons();
        route();
    });
</script>

<div class="content-box">
    <div style="padding: 10px 15px">
        <div class="btn-group" id="loupe-period">
            <button class="btn btn-default btn-sm" data-hours="1">{{ lang._('1 hour') }}</button>
            <button class="btn btn-default btn-sm" data-hours="24">{{ lang._('24 hours') }}</button>
            <button class="btn btn-default btn-sm" data-hours="168">{{ lang._('7 days') }}</button>
            <button class="btn btn-default btn-sm" data-hours="720">{{ lang._('30 days') }}</button>
            <button class="btn btn-default btn-sm" data-hours="8760">{{ lang._('1 year') }}</button>
        </div>
    </div>

    <div id="loupe-list">
        <table id="grid-devices" class="table table-condensed table-hover table-striped table-responsive">
            <thead>
            <tr>
                <th data-column-id="id" data-identifier="true" data-visible="false">{{ lang._('ID') }}</th>
                <th data-column-id="name" data-formatter="device">{{ lang._('Device') }}</th>
                <th data-column-id="shown_type" data-formatter="devtype">{{ lang._('Type') }}</th>
                <th data-column-id="vendor" data-formatter="vendor">{{ lang._('Vendor') }}</th>
                <th data-column-id="mac" data-visible="false">{{ lang._('MAC address') }}</th>
                <th data-column-id="down" data-formatter="bytes" data-width="8em">{{ lang._('Down') }}</th>
                <th data-column-id="up" data-formatter="bytes" data-width="8em">{{ lang._('Up') }}</th>
                <th data-column-id="conns" data-visible="false" data-width="8em">{{ lang._('Connections') }}</th>
                <th data-column-id="top" data-formatter="services" data-sortable="false">{{ lang._('Top services') }}</th>
                <th data-column-id="last" data-formatter="ago" data-width="9em">{{ lang._('Last seen') }}</th>
            </tr>
            </thead>
            <tbody></tbody>
        </table>
    </div>

    <div id="loupe-detail" style="display: none">
        <div style="padding: 0 15px">
            <a href="#">&larr; {{ lang._('All devices') }}</a>
            <h2 id="loupe-detail-title" class="loupe-detail-title"></h2>
        </div>
        <div style="height: 220px; padding: 0 15px 10px"><canvas id="loupe-chart"></canvas></div>
        <ul class="nav nav-tabs" data-tabs="tabs">
            <li class="active"><a data-toggle="tab" href="#tab-services">{{ lang._('Services') }}</a></li>
            <li><a data-toggle="tab" href="#tab-sites">{{ lang._('Sites') }}</a></li>
            <li><a data-toggle="tab" href="#tab-ports">{{ lang._('Protocols and ports') }}</a></li>
        </ul>
        <div class="tab-content">
            <div id="tab-services" class="tab-pane fade in active">
                <table id="grid-services" class="table table-condensed table-hover table-striped table-responsive">
                    <thead><tr>
                        <th data-column-id="service" data-identifier="true" data-formatter="service">{{ lang._('Service') }}</th>
                        <th data-column-id="down" data-formatter="bytes" data-width="8em">{{ lang._('Down') }}</th>
                        <th data-column-id="up" data-formatter="bytes" data-width="8em">{{ lang._('Up') }}</th>
                        <th data-column-id="conns" data-width="8em">{{ lang._('Connections') }}</th>
                    </tr></thead>
                    <tbody></tbody>
                </table>
            </div>
            <div id="tab-sites" class="tab-pane fade in">
                <table id="grid-sites" class="table table-condensed table-hover table-striped table-responsive">
                    <thead><tr>
                        <th data-column-id="name" data-identifier="true" data-formatter="site">{{ lang._('Site') }}</th>
                        <th data-column-id="service" data-formatter="sitesvc">{{ lang._('Service') }}</th>
                        <th data-column-id="down" data-formatter="bytes" data-width="8em">{{ lang._('Down') }}</th>
                        <th data-column-id="up" data-formatter="bytes" data-width="8em">{{ lang._('Up') }}</th>
                        <th data-column-id="conns" data-width="8em">{{ lang._('Connections') }}</th>
                        <th data-column-id="last" data-formatter="ago" data-width="9em">{{ lang._('Last seen') }}</th>
                    </tr></thead>
                    <tbody></tbody>
                </table>
            </div>
            <div id="tab-ports" class="tab-pane fade in">
                <table id="grid-ports" class="table table-condensed table-hover table-striped table-responsive">
                    <thead><tr>
                        <th data-column-id="port" data-identifier="true">{{ lang._('Protocol / port') }}</th>
                        <th data-column-id="down" data-formatter="bytes" data-width="8em">{{ lang._('Down') }}</th>
                        <th data-column-id="up" data-formatter="bytes" data-width="8em">{{ lang._('Up') }}</th>
                        <th data-column-id="conns" data-width="8em">{{ lang._('Connections') }}</th>
                    </tr></thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>
    </div>
</div>

{{ partial("layout_partials/base_dialog",['fields':formDialogDevice,'id':'DialogDevice','label':lang._('Name this device')]) }}
