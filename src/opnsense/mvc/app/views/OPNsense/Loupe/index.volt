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
    .loupe-bar { padding: 10px 15px; }
    /* the theme's Bootstrap reset hides the browser's clear (×) in search fields; bring it back in our grids */
    .bootgrid-header input[type=search]::-webkit-search-cancel-button { -webkit-appearance: searchfield-cancel-button; cursor: pointer; }
</style>
<script>
    $(document).ready(function () {
        let hours = parseFloat(localStorage.getItem('loupe.hours') || '24');
        let chart = null, current = null, q = '';
        const grids = {};
        const esc = s => $('<span>').text(s == null ? '' : String(s)).html();
        const ago = ts => ts ? moment(ts * 1000).fromNow() : '';
        const dash = '<span class="text-muted">&mdash;</span>';
        const dialog = '{{ formGridOverrides['edit_dialog_id'] }}';

        function editButton(r) {
            if (!r.mac) return '';
            return '<i class="fa fa-pencil loupe-edit" title="{{ lang._("Name this device") }}" data-mac="' + esc(r.mac)
                + '" data-name="' + esc(r.custom && r.name !== r.ip ? r.name : '') + '" data-type="' + esc(r.type_source === 'set by you' ? r.type : '') + '"></i>';
        }

        /* formatters for the report grids (device list, device detail, lookup) */
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
                return (row.top || []).length ? row.top.map(esc).join(', ') : dash;
            },
            service: function (column, row) {
                return (row.service && row.service !== 'Other' ? esc(row.service) : dash)
                    + (row.names && row.names.length ? ' <span class="text-muted">' + esc(row.names.slice(0, 3).join(', ')) + '</span>' : '');
            },
            site: function (column, row) {
                return '<a href="#q=' + encodeURIComponent(row.name) + '">' + esc(row.name) + '</a>';
            },
            sitesvc: function (column, row) {
                return row.service && row.service !== 'Other' ? esc(row.service) : dash;
            },
            ago: function (column, row) {
                return ago(row[column.id]);
            },
            lookupdevice: function (column, row) {
                return '<a href="#device=' + esc(row.mac || row.ip) + '">' + esc(row.device) + '</a>'
                    + (row.device !== row.ip ? ' <span class="text-muted">' + esc(row.ip) + '</span>' : '')
                    + (row.type ? ' <span class="text-muted">· ' + esc(row.type) + '</span>' : '');
            },
            lookupsite: function (column, row) {
                return esc(row.name || (row.servers || []).join(', '));
            },
            lookupservice: function (column, row) {
                return row.service ? esc(row.service) : dash;
            },
            housesvc: function (column, row) {
                return '<a href="#q=' + encodeURIComponent(row.service) + '">' + esc(row.service) + '</a>';
            },
            housedevices: function (column, row) {
                return esc(row.devices) + ' <span class="text-muted">' + esc((row.names || []).join(', '))
                    + (row.devices > (row.names || []).length ? ', …' : '') + '</span>';
            }
        };

        /* a clear (×) in the grids' search fields: the browser draws one in type=search fields (not Firefox);
           the grid searches on keyup, so a click on it is passed on as one */
        function clearable(id) {
            $('#' + id + '-search-field').attr('type', 'search');
        }
        $(document).on('search', '.bootgrid-header .search-field', function () { $(this).trigger('keyup'); });

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
            clearable(id);
        }

        /* Devices tab: the list, or one device */
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

        /* Services tab: every service the house used, or what matches the search */
        function showServices() {
            if (!q) {
                $('#loupe-results').hide();
                $('#loupe-overview').show();
                grid('grid-house', '/api/loupe/report/search_house');
                return;
            }
            $('#loupe-overview').hide();
            $('#loupe-results').show();
            $('#loupe-service-title').text(q);
            const query = () => ({q: q});
            grid('grid-traffic', '/api/loupe/report/search_lookup_traffic', query);
            grid('grid-names', '/api/loupe/report/search_lookup_names', query);
        }

        /* Settings tabs */
        /* Settings tab: one page at a time; each starts the first time it is shown (grids need to be visible) */
        const started = {};
        const startPage = {
            general: function () {
                mapDataToFormUI({'frm_settings': '/api/loupe/settings/get'}).done(function () {
                    formatTokenizersUI();
                    $('.selectpicker').selectpicker('refresh');
                });
            },
            servicenames: function () {
                $('#{{ formGridServices['table_id'] }}').UIBootgrid({
                    search: '/api/loupe/settings/search_service',
                    get: '/api/loupe/settings/get_service/',
                    set: '/api/loupe/settings/set_service/',
                    add: '/api/loupe/settings/add_service/',
                    del: '/api/loupe/settings/del_service/'
                });
                clearable('{{ formGridServices['table_id'] }}');
            },
            devicenames: function () {
                $('#{{ formGridOverrides['table_id'] }}').UIBootgrid({
                    search: '/api/loupe/settings/search_device',
                    get: '/api/loupe/settings/get_device/',
                    set: '/api/loupe/settings/set_device/',
                    add: '/api/loupe/settings/add_device/',
                    del: '/api/loupe/settings/del_device/'
                });
                clearable('{{ formGridOverrides['table_id'] }}');
            }
        };
        function showSettings(page) {
            $('.loupe-pages .btn').removeClass('active').filter('[data-page="' + page + '"]').addClass('active');
            $('.loupe-page').hide().filter('#page_' + page).show();
            if (!started[page]) {
                started[page] = true;
                startPage[page]();
            }
        }

        /* tabs <-> address: #device=…, #q=…, #services, and the settings pages #general, #servicenames, #devicenames */
        const TABS = {devices: '#tab_devices', services: '#tab_services', settings: '#tab_settings'};
        const PAGES = ['general', 'servicenames', 'devicenames'];

        function tabOf(hash) {
            if (/^#device=/.test(hash)) return 'devices';
            if (/^#(q=|lookup$|sites$)/.test(hash)) return 'services';
            const t = hash.replace(/^#/, '') || '{{ activetab }}';
            return PAGES.indexOf(t) >= 0 ? 'settings' : (TABS[t] ? t : 'devices');
        }

        function route() {
            const hash = window.location.hash;
            const tab = tabOf(hash);
            $('#maintabs a[href="' + TABS[tab] + '"]').tab('show');
            $('.loupe-apply').toggle(tab === 'settings');
            if (tab === 'devices') {
                const m = hash.match(/device=([^&]+)/);
                if (m) { showDevice(decodeURIComponent(m[1])); } else { showDevices(); }
            } else if (tab === 'services') {
                const m = hash.match(/q=([^&]+)/);
                q = m ? decodeURIComponent(m[1]) : '';
                showServices();
            } else {
                const page = hash.replace(/^#/, '') || '{{ activetab }}';
                showSettings(PAGES.indexOf(page) >= 0 ? page : PAGES[0]);
            }
        }

        $('#maintabs a[data-toggle="tab"][href]').on('click', function () {
            const tab = Object.keys(TABS).find(k => TABS[k] === $(this).attr('href'));
            const keep = (tab === 'services' && q) ? '#q=' + encodeURIComponent(q)
                : (tab === 'settings' ? '#' + PAGES[0] : '#' + tab);
            if (window.location.hash === keep) { route(); } else { window.location.hash = keep; }
        });
        $('.loupe-pages .btn').click(function () {
            window.location.hash = '#' + $(this).data('page');
        });
        $(window).on('hashchange', route);

        $('.loupe-period .btn').click(function () {
            hours = parseFloat($(this).data('hours'));
            localStorage.setItem('loupe.hours', hours);
            $('.loupe-period .btn').removeClass('active').filter('[data-hours="' + hours + '"]').addClass('active');
            route();
        });
        $('.loupe-period .btn').filter('[data-hours="' + hours + '"]').addClass('active');

        /* the pencil opens the same standard dialog as the Device names grid, saved per MAC */
        $(document).on('click', '.loupe-edit', function (e) {
            e.preventDefault();
            e.stopPropagation();
            const $b = $(this);
            clearFormValidation('frm_' + dialog);
            $('#device\\.mac').val($b.data('mac')).prop('readonly', true);
            $('#device\\.name').val($b.data('name'));
            $('#device\\.type').val($b.data('type'));
            $('#btn_' + dialog + '_save').unbind('click').click(function () {
                ajaxCall('/api/loupe/settings/override', {
                    mac: $('#device\\.mac').val(), name: $('#device\\.name').val(), type: $('#device\\.type').val()
                }, function (data) {
                    if (data.result === 'saved') {
                        $('#' + dialog).modal('hide');
                        if (started.devicenames) $('#{{ formGridOverrides['table_id'] }}').bootgrid('reload');
                        route();
                    } else if (data.validations) {
                        handleFormValidation('frm_' + dialog, data.validations);
                    }
                });
            });
            $('#' + dialog).modal('show');
        });
        $('#' + dialog).on('shown.bs.modal', function () { $('#device\\.name').focus().select(); })
            .on('hidden.bs.modal', function () { $('#device\\.mac').prop('readonly', false); });
        $('#frm_' + dialog).on('submit', function (e) { e.preventDefault(); $('#btn_' + dialog + '_save').click(); });

        $('#reconfigureAct').SimpleActionButton({
            onPreAction: function () {
                const dfObj = new $.Deferred();
                saveFormToEndpoint('/api/loupe/settings/set', 'frm_settings', function () { dfObj.resolve(); });
                return dfObj;
            },
            onAction: function () {
                updateServiceControlUI('loupe');
            }
        });
        updateServiceControlUI('loupe');
        route();
    });
</script>

{% set periods = [['1', lang._('1 hour')], ['24', lang._('24 hours')], ['168', lang._('7 days')], ['720', lang._('30 days')], ['8760', lang._('1 year')]] %}

<ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
    {{ partial("layout_partials/base_tabs_header", ['formData': [
        'activetab': activetab,
        'tabs': [
            ['tab_id': 'devices', 'tab_descr': lang._('Devices')],
            ['tab_id': 'services', 'tab_descr': lang._('Services')],
            ['tab_id': 'settings', 'tab_descr': lang._('Settings')]
        ]
    ]]) }}
</ul>
<div class="tab-content content-box">
    <div id="tab_devices" class="tab-pane fade in active">
        <div class="loupe-bar">
            <div class="btn-group loupe-period">
                {% for p in periods %}<button type="button" class="btn btn-default btn-sm" data-hours="{{ p[0] }}">{{ p[1] }}</button>{% endfor %}
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
                <a href="#devices">&larr; {{ lang._('All devices') }}</a>
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

    <div id="tab_services" class="tab-pane fade in">
        <div class="loupe-bar">
            <div class="btn-group loupe-period">
                {% for p in periods %}<button type="button" class="btn btn-default btn-sm" data-hours="{{ p[0] }}">{{ p[1] }}</button>{% endfor %}
            </div>
        </div>
        <div id="loupe-overview" style="display: none">
            <table id="grid-house" class="table table-condensed table-hover table-striped table-responsive">
                <thead><tr>
                    <th data-column-id="service" data-identifier="true" data-formatter="housesvc">{{ lang._('Service') }}</th>
                    <th data-column-id="devices" data-formatter="housedevices">{{ lang._('Devices') }}</th>
                    <th data-column-id="down" data-formatter="bytes" data-width="8em">{{ lang._('Down') }}</th>
                    <th data-column-id="up" data-formatter="bytes" data-width="8em">{{ lang._('Up') }}</th>
                    <th data-column-id="conns" data-visible="false" data-width="8em">{{ lang._('Connections') }}</th>
                    <th data-column-id="last" data-formatter="ago" data-width="9em">{{ lang._('Last seen') }}</th>
                </tr></thead>
                <tbody></tbody>
            </table>
        </div>
        <div id="loupe-results" style="display: none">
            <div style="padding: 0 15px 8px">
                <a href="#services">&larr; {{ lang._('All services') }}</a>
                <h2 id="loupe-service-title" class="loupe-detail-title"></h2>
            </div>
            <ul class="nav nav-tabs" data-tabs="tabs">
                <li class="active"><a data-toggle="tab" href="#tab-traffic">{{ lang._('Traffic') }}</a></li>
                <li><a data-toggle="tab" href="#tab-names">{{ lang._('Looked up or connected to') }}</a></li>
            </ul>
            <div class="tab-content">
                <div id="tab-traffic" class="tab-pane fade in active">
                    <table id="grid-traffic" class="table table-condensed table-hover table-striped table-responsive">
                        <thead><tr>
                            <th data-column-id="device" data-formatter="lookupdevice">{{ lang._('Device') }}</th>
                            <th data-column-id="name" data-formatter="lookupsite">{{ lang._('Site') }}</th>
                            <th data-column-id="service" data-formatter="lookupservice">{{ lang._('Service') }}</th>
                            <th data-column-id="down" data-formatter="bytes" data-width="8em">{{ lang._('Down') }}</th>
                            <th data-column-id="up" data-formatter="bytes" data-width="8em">{{ lang._('Up') }}</th>
                            <th data-column-id="conns" data-width="8em">{{ lang._('Connections') }}</th>
                            <th data-column-id="first" data-formatter="datetime">{{ lang._('First') }}</th>
                            <th data-column-id="last" data-formatter="datetime">{{ lang._('Last') }}</th>
                        </tr></thead>
                        <tbody></tbody>
                    </table>
                </div>
                <div id="tab-names" class="tab-pane fade in">
                    <table id="grid-names" class="table table-condensed table-hover table-striped table-responsive">
                        <thead><tr>
                            <th data-column-id="device" data-formatter="lookupdevice">{{ lang._('Device') }}</th>
                            <th data-column-id="name">{{ lang._('Name') }}</th>
                            <th data-column-id="source" data-width="7em">{{ lang._('Seen in') }}</th>
                            <th data-column-id="count" data-width="7em">{{ lang._('Times') }}</th>
                            <th data-column-id="first" data-formatter="datetime">{{ lang._('First') }}</th>
                            <th data-column-id="last" data-formatter="datetime">{{ lang._('Last') }}</th>
                        </tr></thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <div id="tab_settings" class="tab-pane fade in">
        <div class="loupe-bar">
            <div class="btn-group loupe-pages">
                <button type="button" class="btn btn-default btn-sm" data-page="general">{{ lang._('General') }}</button>
                <button type="button" class="btn btn-default btn-sm" data-page="servicenames">{{ lang._('Service names') }}</button>
                <button type="button" class="btn btn-default btn-sm" data-page="devicenames">{{ lang._('Device names') }}</button>
            </div>
        </div>
        <div id="page_general" class="loupe-page">
            {{ partial("layout_partials/base_form", ['fields': formSettings, 'id': 'frm_settings']) }}
        </div>
        <div id="page_servicenames" class="loupe-page">
            <p class="loupe-bar">{{ lang._('Your own names for domains. They add to and override the built-in list, and apply to all history.') }}</p>
            {{ partial('layout_partials/base_bootgrid_table', formGridServices) }}
        </div>
        <div id="page_devicenames" class="loupe-page">
            <p class="loupe-bar">{{ lang._('Name a device or correct its detected type. Matched by MAC address; the pencil next to a device does the same.') }}</p>
            {{ partial('layout_partials/base_bootgrid_table', formGridOverrides) }}
        </div>
    </div>
</div>

<div class="loupe-apply" style="display: none">
    {{ partial('layout_partials/base_apply_button', {'data_endpoint': '/api/loupe/service/reconfigure', 'data_service_widget': 'loupe', 'data_error_title': 'Error applying Loupe settings'}) }}
</div>

{{ partial("layout_partials/base_dialog", ['fields': formDialogService, 'id': formGridServices['edit_dialog_id'], 'label': lang._('Service name')]) }}
{{ partial("layout_partials/base_dialog", ['fields': formDialogDevice, 'id': formGridOverrides['edit_dialog_id'], 'label': lang._('Device')]) }}
