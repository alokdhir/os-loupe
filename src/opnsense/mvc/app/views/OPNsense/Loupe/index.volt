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
<script src="{{ cache_safe('/ui/js/chartjs-plugin-colorschemes.min.js') }}"></script>
<script src="{{ cache_safe('/ui/js/chartjs-plugin-zoom.min.js') }}"></script>
<style>
    /* the AI Rule Generator's confidence pills: small, beside their label */
    .loupe-pill { font-size: 75%; font-weight: 600; padding: 1px 6px; border-radius: 8px; vertical-align: 1px; }
    .loupe-edit { color: #999; cursor: pointer; visibility: hidden; padding-left: 8px; }
    .tabulator-cell:hover .loupe-edit, #loupe-detail-title:hover .loupe-edit { visibility: visible; }
    @media (hover: none) { .loupe-edit { visibility: visible; } }
    .loupe-edit:hover { color: #337ab7; }
    .loupe-copy { display: block; cursor: pointer; overflow: hidden; text-overflow: ellipsis; }
    .loupe-copied { color: #3c763d; transition: color 0.2s; }
    .loupe-detail-title { font-size: 24px; margin: 8px 0 4px; }
    .loupe-detail-title small { display: block; margin-top: 6px; font-size: 13px; }
    .loupe-bar { padding: 10px 15px; }
    .loupe-netpick { display: inline-block; margin-left: 10px; vertical-align: middle; }
    .loupe-net + .loupe-net { margin-left: 3px; }
    .loupe-rebuild { margin-top: 6px; }
</style>
<script>
    $(document).ready(function () {
        let hours = parseFloat(localStorage.getItem('loupe.hours') || '24');
        /* networks (several interfaces): a filter and a pill per network, coloured as Reporting -> Traffic does
           (its colour scheme, by the network's place in Traffic's interface list) */
        let networks = {}, picked = [], syncing = false;
        const allPicked = () => picked.length === Object.keys(networks).length;
        /* the filter sent: '' for all networks (one added later is included too), 'none' for none */
        const netFilter = () => allPicked() ? '' : (picked.join(',') || 'none');
        let current = null, q = '';
        const grids = {};
        const fresh = new Set();      /* lists to fetch anew (not configd's cached copy) on their next request */
        /* values from the network (host names, vendor classes) also land in attributes: escape quotes too.
           The API HTML-escapes what it sends the GUI (& arrives as &amp;): decode first, so it isn't escaped twice */
        const ESC = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
        const esc = s => (s == null ? '' : String(s).includes('&') ? htmlDecode(String(s)) : String(s))
            .replace(/[&<>"']/g, c => ESC[c]);
        const ago = ts => ts ? moment(ts * 1000).fromNow() : '';
        const dash = '<span class="text-muted">&mdash;</span>';
        /* named from the same device's other traffic to the same server (query.py Labels) */
        const inferred = r => r.inferred ? ' <span class="text-muted" title="{{ lang._("Named from other traffic between this device and the same server") }}">({{ lang._("same server") }})</span>' : '';
        const dialog = '{{ formGridOverrides['edit_dialog_id'] }}';

        function editButton(r) {        /* on hover, beside a device's name and its type */
            if (!r.mac) return '';
            /* data-name/type/ic: the override now (empty where Loupe's detection shows); shown-*: what the list shows */
            return '<i class="fa fa-pencil loupe-edit" title="{{ lang._("Edit name, type and icon") }}" data-mac="' + esc(r.mac)
                + '" data-name="' + esc(r.custom && r.name !== r.ip ? r.name : '') + '" data-type="' + esc(r.type_source === 'set by you' ? r.type : '') + '" data-ic="' + esc(r.icon_set || '')
                + '" data-shown-name="' + esc(r.name !== r.ip ? r.name || '' : '') + '" data-shown-type="' + esc(r.type || '') + '" data-shown-ic="' + esc(r.icon || '')
                + '" data-source="' + esc(r.type_source || '') + '"></i>';
        }

        /* click a cell to copy its value (the cursor says so); it flashes green when copied */
        const copyable = v => v ? '<span class="loupe-copy" title="{{ lang._("Click to copy") }}" data-copy="' + esc(v) + '">' + esc(v) + '</span>' : '';
        $(document).on('click', '.loupe-copy', function (e) {
            e.preventDefault();
            e.stopPropagation();
            const $i = $(this), text = String($i.data('copy'));
            const done = () => { $i.addClass('loupe-copied'); setTimeout(() => $i.removeClass('loupe-copied'), 700); };
            if (navigator.clipboard && window.isSecureContext) {
                navigator.clipboard.writeText(text).then(done);
            } else {                                        /* plain http: the older way */
                const $t = $('<textarea>').val(text).css({position: 'fixed', opacity: 0}).appendTo('body').select();
                document.execCommand('copy');
                $t.remove();
                done();
            }
        });

        const COLORS = Chart.colorschemes.tableau.Classic10;
        const pill = id => networks[id] ? '<span class="badge loupe-net" style="background:' + COLORS[networks[id].color % COLORS.length]
            + '">' + esc(networks[id].name) + '</span>' : '';

        /* formatters for the report grids (device list, device detail, service) */
        const formatters = {
            networks: function (column, row) {
                return (row.networks || []).map(pill).join('');
            },
            device: function (column, row) {
                return '<a href="#device=' + esc(row.id) + '">' + esc(row.name) + '</a>' + editButton(row);
            },
            devtype: function (column, row) {           /* the type, its model (when known) after it */
                if (!row.type && !row.model) return dash + editButton(row);
                return (row.icon ? '<i class="' + esc(row.icon) + ' fa-fw text-muted"></i> ' : '<i class="fa fa-fw"></i> ')
                    + '<span title="' + esc(row.type_source || '') + '">' + esc(row.type || '') + '</span>'
                    + (row.model ? ' <span class="text-muted">' + esc(row.model) + '</span>' : '') + editButton(row);
            },
            vendor: function (column, row) {
                if (!row.vendor_note) return esc(row.vendor);
                return '<span class="text-muted" title="' + esc(row.vendor_note) + '">' + esc(row.vendor || row.vendor_note) + '</span>';
            },
            services: function (column, row) {
                return (row.top || []).length ? row.top.map(esc).join(', ') : dash;
            },
            devtraffic: function (column, row) {      /* a service row, or one of its sites (its addresses on hover) */
                if (!row.servers) {
                    return row.service === 'Other' ? '<span class="text-muted">{{ lang._('Other') }}</span>'
                        : '<a href="#q=' + encodeURIComponent(row.service) + '">' + esc(row.service) + '</a>';
                }
                return '<a href="#q=' + encodeURIComponent(row.label) + '" title="' + esc(row.servers.join(', ')) + '">'
                    + esc(row.label) + '</a>' + inferred(row)
                    + ((row.ports || []).length ? ' <span class="text-muted">' + row.ports.map(esc).join(', ') + '</span>' : '');
            },
            pct: function (column, row) {             /* the number, on a bar as long as its share */
                const p = Math.max(0, Math.min(100, +row.pct || 0));
                return '<div style="background: linear-gradient(90deg, rgba(54,162,235,0.25) ' + p + '%, transparent ' + p
                    + '%); padding: 0 4px">' + (p > 0 && p < 0.05 ? '&lt;0.1' : p.toFixed(1)) + '%</div>';
            },
            ago: function (column, row) {
                return ago(row[column.id]);
            },
            servicedevice: function (column, row) {
                return (row.icon ? '<i class="' + esc(row.icon) + ' fa-fw text-muted"></i> ' : '<i class="fa fa-fw"></i> ')
                    + '<a href="#device=' + esc(row.mac || row.ip) + '">' + esc(row.device) + '</a>'
                    + (row.type ? ' <span class="text-muted">· ' + esc(row.type) + '</span>' : '');
            },
            servicesite: function (column, row) {
                return copyable(row.name || (row.servers || []).join(', ')) + inferred(row);
            },
            copy: function (column, row) {
                return copyable(row[column.id]);
            },
            ruletype: function (column, row) {
                return (row.icon ? '<i class="' + esc(row.icon) + ' fa-fw text-muted"></i> ' : '') + esc(row.type);
            },
            housesvc: function (column, row) {           /* matched through its sites only: which ones */
                return '<a href="#q=' + encodeURIComponent(row.service) + '">' + esc(row.service) + '</a>'
                    + ((row.matched || []).length ? ' <span class="text-muted">' + esc(row.matched.join(', '))
                        + (row.sites > row.matched.length ? ', …' : '') + '</span>' : '');
            },
            housedevices: function (column, row) {
                return esc(row.devices) + ' <span class="text-muted">' + esc((row.names || []).join(', '))
                    + (row.devices > (row.names || []).length ? ', …' : '') + '</span>';
            }
        };

        function grid(id, endpoint, extra, tabulatorOptions) {
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
                        request.networks = netFilter();
                        if (fresh.delete(id)) request.fresh = '1';
                        return Object.assign(request, extra ? extra() : {});
                    },
                    formatters: formatters
                },
                tabulatorOptions: tabulatorOptions || {}
            });
        }

        /* a device's traffic: each service, with its sites when opened (core's tree, as Unbound's overrides) */
        const TREE = {dataTree: true, dataTreeElementColumn: 'label',
            dataTreeStartExpanded: (row) => !!row.getData()._open,      /* what a search found */
            dataTreeCollapseElement: '<i class="fa-regular fa-square-minus" style="margin-right: 6px"></i>',
            dataTreeExpandElement: '<i class="fa-regular fa-square-plus" style="margin-right: 6px"></i>'};

        /* a chart of a device's or a service's traffic: Mbps down and up, each point the average over its bucket */
        const charts = {};
        /* drag across a chart to zoom into that time (the points it has; a smooth line between them), × to go back */
        function zoomReset(canvas) {
            let $b = $('#' + canvas + '-reset');
            if (!$b.length) {
                $b = $('<button type="button" class="btn btn-default btn-xs" style="position: absolute; top: 0; right: 15px; display: none">'
                    + '<i class="fa fa-times fa-fw"></i> {{ lang._("Reset zoom") }}</button>').attr('id', canvas + '-reset');
                $('#' + canvas).parent().css('position', 'relative').append($b);
                $b.on('click', function () {
                    if (charts[canvas]) charts[canvas].resetZoom();
                    $b.hide();
                });
            }
            return $b;
        }
        function clearChart(canvas) {
            zoomReset(canvas).hide();
            if (charts[canvas]) charts[canvas].destroy();
            delete charts[canvas];
        }
        function drawChart(canvas, data) {
            const tl = data.timeline || [], step = data.step || 300;
            const mbps = b => b * 8 / step / 1e6;
            if (charts[canvas]) charts[canvas].destroy();
            zoomReset(canvas).hide();
            charts[canvas] = new Chart(document.getElementById(canvas), {
                type: 'line',
                data: {
                    labels: tl.map(p => (p[0] + step / 2) * 1000),     /* each point at the middle of its bucket */
                    datasets: [
                        {label: '{{ lang._("Down") }}', data: tl.map(p => mbps(p[1])), bytes: tl.map(p => p[1]),
                         borderColor: 'rgba(54,162,235,1)', backgroundColor: 'rgba(54,162,235,0.25)', fill: 'origin'},
                        {label: '{{ lang._("Up") }}', data: tl.map(p => mbps(p[2])), bytes: tl.map(p => p[2]),
                         borderColor: 'rgba(255,159,64,1)', backgroundColor: 'rgba(255,159,64,0.25)', fill: 'origin'}
                    ]
                },
                options: {
                    maintainAspectRatio: false, animation: false,
                    interaction: {mode: 'index', intersect: false},
                    elements: {
                        line: {borderWidth: 1.5, cubicInterpolationMode: 'monotone'},   /* smooth, never overshoots */
                        point: {radius: 0, hoverRadius: 3}
                    },
                    scales: {
                        x: {type: 'time', time: {tooltipFormat: step >= 86400 ? 'll' : 'lll'}},
                        y: {beginAtZero: true, title: {display: true, text: 'Mbps'}, ticks: {callback: v => +v.toFixed(2)}}
                    },
                    plugins: {
                        tooltip: {callbacks: {label: c => c.dataset.label + ': ' + c.raw.toFixed(c.raw < 10 ? 2 : 1)
                            + ' Mbps (' + byteFormat(c.dataset.bytes[c.dataIndex], 1) + ')'}},
                        zoom: {
                            zoom: {mode: 'x', drag: {enabled: true, backgroundColor: 'rgba(54,162,235,0.15)',
                                                     borderColor: 'rgba(54,162,235,0.6)', borderWidth: 1},
                                   onZoomComplete: () => zoomReset(canvas).show()},
                            limits: {x: {minRange: 2 * step * 1000}}       /* at least two of its points */
                        }
                    }
                }
            });
        }

        /* Devices tab: the list, or one device */
        function showDevices() {
            $('#loupe-detail').hide();
            $('#loupe-list').show();
            $('#tab_devices .loupe-netpick').toggle(Object.keys(networks).length > 1);
            grid('grid-devices', '/api/loupe/report/search_devices');
        }

        /* a device page's Network columns: only for a device that was on several networks in the period */
        let deviceNetworks = 0;
        const DEVICE_GRIDS = '#grid-devtraffic';
        function deviceColumns($grids) {
            $grids.each(function () {
                try {
                    $(this).bootgrid(deviceNetworks > 1 ? 'setColumns' : 'unsetColumns', ['networks']);
                } catch (e) { /* not built yet: its loaded event does it */ }
            });
        }
        $(document).on('loaded.rs.jquery.bootgrid', DEVICE_GRIDS, function () { deviceColumns($(this)); });

        function showDevice(id) {
            current = id;
            $('#tab_devices .loupe-netpick').hide();       /* a device's page shows all its traffic: no filter */
            $('#loupe-list').hide();
            $('#loupe-detail').show();
            /* the header runs the report and configd keeps it: the lists ask once it has, and get that copy */
            ajaxGet('/api/loupe/report/device/' + id.replace(/[^0-9a-fA-F:.]/g, '') + '/' + hours, {}, function (data) {
                grid('grid-devtraffic', '/api/loupe/report/search_device_traffic', () => ({device: current}), TREE);
                if (!data || data.error) { $('#loupe-detail-title').text((data || {}).error || ''); return; }
                const d = data.device || {};
                deviceNetworks = (data.networks || []).length;
                deviceColumns($(DEVICE_GRIDS));
                $('#loupe-detail-title').html((d.icon ? '<i class="' + esc(d.icon) + ' fa-fw text-muted"></i> ' : '')
                    + esc(d.name || d.ip || id) + editButton(d)
                    + ' <small class="text-muted">' + esc([d.model, d.type, d.vendor, d.fqdn, d.ip, d.mac].filter(Boolean).join(' · '))
                    /* one device seen with several MACs (a bridge and its NIC, wired and Wi-Fi): the others too */
                    + ((d.macs || []).length > 1 ? ' <span title="{{ lang._('Loupe saw these MACs as one device') }}">('
                        + esc('{{ lang._('also') }} ' + d.macs.filter(m => m !== d.mac).join(', ')) + ')</span>' : '')
                    + ' ' + (data.networks || []).map(n => pill(n.id)).join('') + '</small>');
                drawChart('loupe-chart', data);
            });
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
            grid('grid-traffic', '/api/loupe/report/search_service_traffic', () => ({q: q}));
            /* only the answer to the latest request is drawn; until it comes, and if it fails, no chart (not the last one) */
            const asked = q + '|' + hours + '|' + netFilter();
            clearChart('loupe-service-chart');
            ajaxCall('/api/loupe/report/service_chart', {q: q, hours: hours, networks: netFilter()}, function (data) {
                if (asked === q + '|' + hours + '|' + netFilter() && data && !data.error) drawChart('loupe-service-chart', data);
            });
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
            },
            rules: function () {
                const id = '{{ formGridRules['table_id'] }}';
                $('#' + id).UIBootgrid({
                    search: '/api/loupe/settings/search_rule',
                    get: '/api/loupe/settings/get_rule/',
                    set: '/api/loupe/settings/set_rule/',
                    add: '/api/loupe/settings/add_rule/',
                    del: '/api/loupe/settings/del_rule/',
                    options: {
                        initialSearchPhrase: rulesSearch,       /* opened from a device's "edit the rule" link */
                        requestHandler: (request) => Object.assign(request, {source: $('#rule-source-filter').val()}),
                        formatters: {ruleicon: (column, row) => row.icon_class
                            ? '<i class="' + esc(row.icon_class) + ' fa-fw text-muted"></i>' : ''}
                    }
                });
                /* the Source filter sits after the grid's search box, as core's dnsmasq page places its tag filter */
                $('#rule-source-container').detach().insertAfter($('#' + id + '-header .actionBar .search')).show();
                $('#rule-source-filter').selectpicker().on('changed.bs.select', () => $('#' + id).bootgrid('reload'));
            },
            overrides: function () {
                $('#{{ formGridOverrides['table_id'] }}').UIBootgrid({
                    search: '/api/loupe/settings/search_device',
                    get: '/api/loupe/settings/get_device/',
                    set: '/api/loupe/settings/set_device/',
                    add: '/api/loupe/settings/add_device/',
                    del: '/api/loupe/settings/del_device/',
                    options: {formatters: {
                        copy: formatters.copy,
                        devtype: (column, row) => (row.icon_class ? '<i class="' + esc(row.icon_class) + ' fa-fw text-muted"></i> '
                            : '<i class="fa fa-fw"></i> ') + esc(row.type)
                    }}
                });
            }
        };
        /* Custom | Built-in on Service names and Device rules: the shipped rules, read-only */
        const builtin = {
            'grid-builtin-services': '/api/loupe/report/search_builtin_services',
            'grid-builtin-rules': '/api/loupe/report/search_builtin_rules'
        };
        $('.loupe-source .btn').click(function () {
            const $page = $(this).closest('.loupe-page');
            const which = $(this).data('source');
            $(this).siblings().removeClass('active');
            $(this).addClass('active');
            $page.find('[data-source-pane]').hide().filter('[data-source-pane="' + which + '"]').show();
            $page.find('.loupe-source-help').hide().filter('[data-for="' + which + '"]').show();
            applyButton();
            const $grid = $page.find('[data-source-pane="' + which + '"] table[id^="grid-builtin-"]');
            if ($grid.length && !grids[$grid.attr('id')]) {
                const id = $grid.attr('id');
                grids[id] = $grid.UIBootgrid({
                    search: builtin[id],
                    options: {selection: false, multiSelect: false, formatters: formatters}
                });
            }
        });

        /* Apply on the settings tab, but not where there is nothing to apply: the read-only Built-in lists, and the
           AI Rule Generator (its Save and apply does it) */
        let rulesSearch = '';           /* the type a device's "edit the rule" link looks for in Custom rules */
        function showRulesFor(type) {
            rulesSearch = type;
            const id = '{{ formGridRules['table_id'] }}';
            $('#page_rules .loupe-source .btn[data-source="mine"]').click();
            if (started.rules) $('#' + id + '-search-field').val(type).trigger('keyup');
            window.location.hash = '#rules';
        }
        let currentTab = '';            /* the tab route() showed: the panes' classes lag behind their fade */
        let pending = false;            /* a list edit saved but not applied yet (core's grids say "settings-changed") */
        function applyButton() {
            const page = $('.loupe-page').filter(function () { return this.style.display !== 'none'; }).first();
            const source = page.find('.loupe-source .btn.active').data('source');
            $('.loupe-apply').toggle(currentTab === 'settings' && (pending || (source !== 'builtin' && source !== 'ai')));
        }
        $(document).on('settings-changed', function () { pending = true; applyButton(); });
        function showSettings(page) {
            $('.loupe-pages .btn').removeClass('active').filter('[data-page="' + page + '"]').addClass('active');
            $('.loupe-page').hide().filter('#page_' + page).show();
            applyButton();
            if (!started[page]) {
                started[page] = true;
                startPage[page]();
            }
        }

        /* tabs <-> address: #device=…, #q=…, #services, and the settings pages #general, #servicenames, #overrides */
        const TABS = {devices: '#tab_devices', services: '#tab_services', settings: '#tab_settings'};
        const PAGES = ['general', 'servicenames', 'rules', 'overrides'];

        function tabOf(hash) {
            if (/^#device=/.test(hash)) return 'devices';
            if (/^#(q=|sites$)/.test(hash)) return 'services';
            const t = (hash.replace(/^#/, '') || '{{ activetab }}').replace(/^devicenames$/, 'overrides');
            return PAGES.indexOf(t) >= 0 ? 'settings' : (TABS[t] ? t : 'devices');
        }

        /* after an update or a service names change loupd builds each day's summary again, newest first: until a
           day is built, periods reaching it are read the slow way */
        function rebuildNote() {
            ajaxGet('/api/loupe/report/summary', {}, function (data) {
                const left = data.waiting || 0;
                $('.loupe-rebuild').toggle(left > 0).text('{{ lang._("Rebuilding summaries: %s of %s days left (%s and older). Periods that include those days load slower until then.") }}'
                    .replace('%s', left).replace('%s', data.days).replace('%s', data.newest_waiting));
            });
        }

        /* the address is anyone's typing: a stray % must not stop the page */
        const decode = s => { try { return decodeURIComponent(s); } catch (e) { return ''; } };
        function route() {
            const hash = window.location.hash;
            fresh.add('grid-devices').add('grid-house').add('grid-traffic');    /* opened, period changed or saved: not the cached copy */
            rebuildNote();
            const tab = tabOf(hash);
            $('#maintabs a[href="' + TABS[tab] + '"]').tab('show');
            currentTab = tab;
            applyButton();
            if (tab === 'devices') {
                const m = hash.match(/device=([^&]+)/);
                if (m) { showDevice(decode(m[1])); } else { showDevices(); }
            } else if (tab === 'services') {
                const m = hash.match(/q=([^&]+)/);
                q = m ? decode(m[1]) : '';
                showServices();
            } else {
                const page = (hash.replace(/^#/, '') || '{{ activetab }}').replace(/^devicenames$/, 'overrides');
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

        /* the network picker (on Devices and Services, kept in step): shown when Loupe watches several networks */
        function loadNetworks() {
            return ajaxGet('/api/loupe/report/networks', {}, function (data) {
                const list = data.networks || [];
                networks = {};
                list.forEach(n => { networks[n.id] = n; });
                const saved = localStorage.getItem('loupe.networks');
                picked = !saved || saved === 'all' ? list.map(n => n.id)
                    : saved === 'none' ? [] : saved.split(',').filter(id => networks[id]);
                $('.loupe-netpick').toggle(list.length > 1);    /* first: a hidden picker measures its width as 0 */
                $('select.loupe-networks').each(function () {
                    const $s = $(this).empty();
                    list.forEach(n => $s.append($('<option/>').val(n.id).attr('data-content', pill(n.id))
                        .prop('selected', picked.includes(n.id))));
                    $s.selectpicker('refresh');
                    $s.data('selectpicker').options.countSelectedText = (n, total) => n === total
                        ? '{{ lang._("All networks") }}' : '{{ lang._("%s networks") }}'.replace('%s', n);
                    $s.selectpicker('render');
                });
            });
        }
        /* the Network column: shown whenever there are several networks, hidden for one. Set after every load (not
           saved as the user's choice): the grid restores a layout it remembers, which may be from before this column */
        $(document).on('loaded.rs.jquery.bootgrid', '#grid-devices', function () {
            $(this).bootgrid(Object.keys(networks).length > 1 ? 'setColumns' : 'unsetColumns', ['networks']);
        });
        $('select.loupe-networks').on('changed.bs.select', function () {
            if (syncing) return;            /* the other picker being kept in step (that fires this event too) */
            picked = $(this).val() || [];
            localStorage.setItem('loupe.networks', allPicked() ? 'all' : (picked.join(',') || 'none'));
            syncing = true;
            $('select.loupe-networks').not(this).selectpicker('val', picked);
            syncing = false;
            route();
        });

        /* the pencil opens the same standard dialog as the Overrides grid, saved per MAC */
        $(document).on('click', '.loupe-edit', function (e) {
            e.preventDefault();
            e.stopPropagation();
            const $b = $(this);
            clearFormValidation('frm_' + dialog);
            /* a blank record first, for the icon choices */
            /* opens with what the list shows; Save keeps what wasn't changed as it was (detected or set before) and
               saves nothing at all when nothing changed */
            const shown = {name: String($b.data('shown-name') || ''), type: String($b.data('shown-type') || ''), icon: ''};
            const kept = {name: String($b.data('name') || ''), type: String($b.data('type') || ''), icon: String($b.data('ic') || '')};
            mapDataToFormUI({['frm_' + dialog]: '/api/loupe/settings/get_device'}).done(function () {
                $('#device\\.mac').val($b.data('mac')).prop('readonly', true);
                $('#device\\.name').val(shown.name);
                $('#device\\.type').val(shown.type);
                const $icon = $('#device\\.icon').selectpicker('refresh');
                const ic = String($b.data('shown-ic') || '');
                shown.icon = $icon.find('option').filter(function () { return this.value === ic; }).length ? ic : '';
                $icon.selectpicker('val', shown.icon);
                /* where the type comes from, under its box: a rule's type is changed for every device in the rule */
                const src = String($b.data('source') || '');
                let $why = $('#device-type-source');
                if (!$why.length) $why = $('<div id="device-type-source" class="text-muted" style="margin-top: 4px"></div>').insertAfter('#device\\.type');
                const rule = src.match(/^(AI rule|custom rule): (.*)$/);
                $why.empty();
                if (rule) {         /* a link to the rule: Custom rules, searched for this type */
                    const $link = $('<a href="#"></a>').text('{{ lang._("edit the rule") }}').click(function (e) {
                        e.preventDefault();
                        $('#' + dialog).modal('hide');
                        showRulesFor(shown.type);
                    });
                    $why.append(document.createTextNode((rule[1] === 'AI rule' ? '{{ lang._("From your AI rule:") }} '
                        : '{{ lang._("From your rule:") }} ') + rule[2] + '. {{ lang._("Changing it here sets it for this device only;") }} '),
                        $link, document.createTextNode(' {{ lang._("to change every device it matches.") }}'));
                } else {
                    $why.text(!src ? '' : src === 'set by you' ? '{{ lang._("Set by you for this device.") }}'
                        : '{{ lang._("Detected from:") }} ' + src);
                }
                $('#' + dialog).modal('show');
            });
            $('#btn_' + dialog + '_save').unbind('click').click(function () {
                const now = {name: $('#device\\.name').val(), type: $('#device\\.type').val(), icon: $('#device\\.icon').val() || ''};
                if (now.name === shown.name && now.type === shown.type && now.icon === shown.icon) {
                    $('#' + dialog).modal('hide');
                    return;
                }
                const pick = (f) => now[f] === shown[f] ? kept[f] : now[f];
                ajaxCall('/api/loupe/settings/override', {
                    mac: $('#device\\.mac').val(), name: pick('name'), type: pick('type'), icon: pick('icon')
                }, function (data) {
                    if (data.result === 'saved') {
                        $('#' + dialog).modal('hide');
                        if (started.overrides) $('#{{ formGridOverrides['table_id'] }}').bootgrid('reload');
                        route();
                    } else if (data.validations) {
                        handleFormValidation('frm_' + dialog, data.validations);
                    }
                });
            });
        });
        $('#' + dialog).on('shown.bs.modal', function () { $('#device\\.name').focus().select(); })
            .on('hidden.bs.modal', function () { $('#device\\.mac').prop('readonly', false); });
        /* a rule shows only the clues it uses; "Match on" adds one, the × beside a clue removes it */
        const CLUES = ['vendor', 'hostname', 'mdns_model', 'mdns_name', 'category', 'mdns_service', 'dhcp_vendor',
            'talks_to', 'mac_prefix', 'private_mac'];
        const $ruleDlg = $('#{{ formGridRules['edit_dialog_id'] }}');
        const clueRow = (c) => $ruleDlg.find('#row_rule\\.' + c);
        const used = (c) => clueRow(c).css('display') !== 'none';      /* also while the dialog itself is hidden */
        const $addClue = $('<tr id="row_rule_addclue"><td><div class="control-label">'
            + '<i class="fa fa-info-circle fa-fw text-muted"></i> <b></b></div></td><td>'
            + '<select class="selectpicker" data-width="100%" title="{{ lang._('Choose a clue') }}"></select></td><td></td></tr>');
        clueRow(CLUES[CLUES.length - 1]).after($addClue);
        $addClue.find('select').selectpicker();
        CLUES.forEach(function (c) {
            clueRow(c).find('.control-label').append(' <a href="#" class="loupe-clue-remove" title="{{ lang._('Remove this clue') }}">'
                + '<i class="fa fa-times fa-fw text-muted"></i></a>');
        });
        function showClues() {
            const unused = CLUES.filter((c) => !used(c));
            const $s = $addClue.find('select').empty();
            unused.forEach((c) => $s.append($('<option>').val(c).text(clueRow(c).find('.control-label b').text())));
            $addClue.toggle(unused.length > 0)
                .find('b').text(unused.length === CLUES.length ? '{{ lang._('Match on') }}' : '{{ lang._('And also') }}');
            $s.selectpicker('refresh');
        }
        $ruleDlg.on('opnsense_bootgrid_mapped', function () {
            CLUES.forEach((c) => clueRow(c).toggle($('#rule\\.' + c).val() !== ''));
            showClues();
        });
        $addClue.find('select').on('changed.bs.select', function () {
            const c = $(this).val();
            if (!c) return;
            clueRow(c).show().find('input').focus();
            if (c === 'private_mac') $('#rule\\.private_mac').selectpicker('val', '1');     /* "Either" is no clue */
            showClues();
        });
        $ruleDlg.on('click', '.loupe-clue-remove', function (e) {
            e.preventDefault();
            const $row = $(this).closest('tr');
            $row.find('input').val('');
            $row.find('select').selectpicker('val', '');
            $row.hide();
            showClues();
        });
        /* icon choices show the icon itself: added here from each option's value, since API output is
           HTML-escaped and the form code decodes option labels but not their data attributes */
        $(document).on('refreshed.bs.select', '#device\\.icon, #rule\\.icon', function () {
            let added = false;
            $(this).find('option').each(function () {
                if (this.value && !this.hasAttribute('data-content')) {
                    this.setAttribute('data-content', '<i class="' + esc(this.value) + ' fa-fw"></i> ' + esc(this.text));
                    added = true;
                }
            });
            if (added) $(this).selectpicker('refresh');
        });
        /* Enter in a text field saves any of the page's dialogs (OPNsense's dialogs have no submit button) */
        $(document).on('keydown', '.modal form input', function (e) {
            if (e.key !== 'Enter' || e.isComposing || !$(this).is('[type=text], [type=number], [type=email], :not([type])')
                || $(this).closest('.dropdown-menu, .tokenize').length) return;          /* a dropdown's own search box */
            e.preventDefault();
            const id = $(this).closest('.modal').attr('id');
            $('#btn_' + id + '_save').click();
        });
        $(document).on('submit', '.modal form', function (e) { e.preventDefault(); });

        /* AI Rule Generator: ask (the table and prompt), paste the answer, review what each rule would change, save */
        const ai = {prompt: '', loaded: false};
        const AI_FIELDS = {vendor: '{{ lang._("MAC vendor") }}', hostname: '{{ lang._("Host name") }}',
            mdns_name: '{{ lang._("Bonjour name") }}', mdns_model: '{{ lang._("Bonjour model") }}',
            mdns_service: '{{ lang._("Bonjour service") }}', category: '{{ lang._("Category code") }}',
            dhcp_vendor: '{{ lang._("DHCP vendor class") }}', talks_to: '{{ lang._("Talks to") }}'};
        /* the AI's confidence in a rule, as a pill: high green, medium orange, low red */
        const AI_CONFIDENCE = {
            high: '<span class="label label-success loupe-pill">{{ lang._("high") }}</span>',
            medium: '<span class="label label-warning loupe-pill">{{ lang._("medium") }}</span>',
            low: '<span class="label label-danger loupe-pill">{{ lang._("low") }}</span>'};
        /* core escapes &, < and > in API answers to the browser (Mvc\Response); this page shows text with esc() and
           copies the prompt as plain text, so take the strings back to what they are */
        function aiPlain(v) {
            if (typeof v === 'string') return v.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
            if (Array.isArray(v)) return v.map(aiPlain);
            if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, aiPlain(x)]));
            return v;
        }
        function aiLoad() {
            ai.loaded = true;
            ajaxCall('/api/loupe/settings/ai_table', {scope: $('input[name="ai-scope"]:checked').val()}, function (data) {
                data = aiPlain(data);
                if (!data || data.error) { $('#ai-copy').prop('disabled', true); return; }
                ai.prompt = data.prompt || '';
                $('#ai-count-unidentified').text(data.unidentified);
                $('#ai-cut').text(data.asked < data.chosen ? '{{ lang._("The prompt covers the first") }} ' + data.asked
                    + ' {{ lang._("of") }} ' + data.chosen + ' {{ lang._("devices (it would be too long for an AI chat).") }}' : '');
                $('#ai-count-all').text(data.all);
                $('#ai-copy').prop('disabled', !ai.prompt).find('i').removeClass('fa-check').addClass('fa-copy');
                $('#ai-prompt').text(ai.prompt);
            });
        }
        $('input[name="ai-scope"]').change(aiLoad);
        $('#ai-show').click(function (e) {          /* slides like the answer box folds */
            e.preventDefault();
            const $p = $('#ai-prompt'), opening = !$p.is(':visible');
            $(this).text(opening ? '{{ lang._("Hide prompt") }}' : '{{ lang._("Show prompt") }}');
            $p.stop(true, true).slideToggle(300);
        });
        $('#ai-copy').click(function () {
            const done = () => $(this).find('i').removeClass('fa-copy').addClass('fa-check');
            if (navigator.clipboard) {
                navigator.clipboard.writeText(ai.prompt).then(done);
            } else {
                $('#ai-prompt').show();
                const range = document.createRange();
                range.selectNodeContents($('#ai-prompt')[0]);
                window.getSelection().removeAllRanges();
                window.getSelection().addRange(range);
                document.execCommand('copy');
                done();
            }
        });
        function aiWhen(when) {
            return Object.entries(when || {}).map(([f, v]) => (AI_FIELDS[f] || f) + ' ' + (Array.isArray(v) ? v.join(' or ') : v))
                .join(' {{ lang._("and") }} ');
        }
        let aiChecked = [];
        let aiIcons = null;             /* the rule dialog's icon choices, for new types in the review */
        function aiIconChoices() {
            if (aiIcons) return $.Deferred().resolve(aiIcons);
            return ajaxGet('/api/loupe/settings/get_rule', {}, function (data) {
                aiIcons = Object.entries(((data || {}).rule || {}).icon || {}).map(([v, o]) => [v, aiPlain(o.value)]);
            }).then(() => aiIcons);
        }
        $('#ai-check').click(function () {
            $('#ai-saved').text('');
            aiIconChoices().always(() => ajaxCall('/api/loupe/settings/ai_preview', {reply: $('#ai-reply').val()}, function (data) {
                data = aiPlain(data);
                const $body = $('#ai-rules tbody').empty();
                aiChecked = (data && data.rules) || [];
                if (!aiChecked.length) {        /* nothing to review: say so where the answer goes */
                    $('#ai-review').hide();
                    $('#ai-paste-problem').text((data && (data.error || data.problem)) || '{{ lang._("No answer from Loupe.") }}').show();
                    $('html, body').animate({scrollTop: $('#ai-paste-problem').offset().top - 90}, 400);
                    return;
                }
                $('#ai-paste-problem').hide();
                $('#ai-review').show();
                $('#ai-problem').text(data.problem || '').toggle(!!data.problem);
                aiChecked.forEach(function (r, i) {
                    const flags = [];
                    if (r.invalid) flags.push('<span class="text-danger">' + esc(r.invalid) + '</span>');
                    if (r.spills) {         /* the devices it would change besides its own, by name */
                        const others = r.others || [];
                        flags.push('<span class="text-danger" title="' + esc(others.join(', ')) + '">{{ lang._("also changes") }} ' + r.spills + ' '
                            + (r.spills === 1 ? '{{ lang._("other device") }}' : '{{ lang._("other devices") }}') + ': '
                            + esc(others.slice(0, 3).join(', ')) + (others.length > 3 ? ', …' : '') + '</span>');
                    }
                    if (r.nothing && !r.invalid) {          /* changes nothing: say why */
                        flags.push('<span class="text-warning">' + (r.duplicate ? '{{ lang._("you already have this rule") }}'
                            : r.already ? '{{ lang._("already") }} ' + esc(r.type)
                            : (r.unknown_rows || []).length ? '{{ lang._("its device is not in the list (an older prompt?)") }}'
                            : '{{ lang._("matches none of its devices") }}') + '</span>');
                    }
                    if (r.vaguer) flags.push('<span class="text-warning">{{ lang._("makes a type vaguer") }}</span>');
                    const changes = (r.changes || []).map(c => c[2] + ' × ' + esc(c[0] || '{{ lang._("no type") }}') + ' → ' + esc(c[1])).join('<br>');
                    const rows = (r.for || []).map(n => n === '?' ? '<span class="text-muted">{{ lang._("not in the list") }}</span>' : esc(n)).join(', ');
                    const icon = r.icon ? '<i class="' + esc(r.icon) + ' fa-fw text-muted"></i> '
                        : '';
                    const pickIcon = (!r.icon && r.fields) ? '<br><select class="form-control input-sm ai-icon" data-i="' + i
                        + '" style="width: auto; display: inline-block; margin-top: 4px"><option value="">{{ lang._("Icon...") }}</option>'
                        + (aiIcons || []).filter(([v]) => v).map(([v, label]) => '<option value="' + esc(v) + '">' + esc(label) + '</option>').join('')
                        + '</select>' : '';
                    $body.append('<tr><td><input type="checkbox" data-i="' + i + '"' + (r.pick ? ' checked' : '') + (r.fields ? '' : ' disabled') + '></td>'
                        + '<td>' + icon + esc(r.type) + (r.vendor ? ' <span class="text-muted">' + esc(r.vendor) + '</span>' : '')
                        + (r.model ? ' <span class="text-muted">' + esc(r.model) + '</span>' : '') + pickIcon + '</td>'
                        + '<td>' + esc(aiWhen(r.when)) + '</td><td>' + rows + '</td><td>' + (changes || '—') + '</td>'
                        + '<td>' + [AI_CONFIDENCE[(r.confidence || '').toLowerCase()]
                            ? '{{ lang._("Confidence") }} ' + AI_CONFIDENCE[r.confidence.toLowerCase()] : '']
                            .concat(flags, r.note ? [esc(r.note)] : []).filter(Boolean).join('<br>') + '</td></tr>');
                });
                const unknown = (data.unknown || []).map(u => esc((u.for || []).join(', ')) + (u.note ? ': ' + esc(u.note) : ''));
                $('#ai-unknown').html(unknown.length ? '<b>{{ lang._("Not identified") }}</b><br>' + unknown.join('<br>') : '');
                /* the answer box folds down so the review is in view (it opens up again when clicked) */
                const $reply = $('#ai-reply');
                if (!aiChecked.length) return;          /* nothing read: leave the box open to paste again */
                $reply.data('tall', $reply.data('tall') || $reply.outerHeight());
                $reply.stop().animate({height: '7em'}, 300, function () {
                    $('html, body').animate({scrollTop: $('#ai-review').offset().top - 70}, 400);
                });
            }));
        });
        $('#ai-reply').on('focus', function () {
            if ($(this).data('tall')) $(this).stop().animate({height: $(this).data('tall')}, 300);
        });
        $('#ai-save').click(function () {
            const picked = $('#ai-rules input:checked').map(function () { return aiChecked[$(this).data('i')]; }).get();
            if (!picked.length) { $('#ai-saved').text('{{ lang._("Tick the rules to save.") }}'); return; }
            const rules = picked.map(r => Object.assign({}, r.fields,
                {description: [r.vendor, r.model, r.note].filter(Boolean).join(' · '),
                 icon: $('#ai-rules select.ai-icon[data-i="' + aiChecked.indexOf(r) + '"]').val() || ''}));
            const $btn = $(this).prop('disabled', true);
            ajaxCall('/api/loupe/settings/ai_save', {rules: rules}, function (data) {
                data = aiPlain(data);
                if (!data || data.result !== 'saved') {
                    $btn.prop('disabled', false);
                    $('#ai-saved').addClass('text-danger').text((data || {}).error || '{{ lang._("Not saved.") }}');
                    return;
                }
                $('#ai-rules input:checked').prop('checked', false).prop('disabled', true);
                $('#{{ formGridRules['table_id'] }}').bootgrid('reload');
                $('#ai-saved').removeClass('text-danger').text(data.saved + ' {{ lang._("rules saved; applying...") }}');
                /* apply as the page's Apply button does: loupd reloads the rules and reclassifies */
                ajaxCall('/api/loupe/service/reconfigure', {}, function (applied) {
                    $btn.prop('disabled', false);
                    const ok = applied && applied.status === 'ok';
                    $('#ai-saved').toggleClass('text-danger', !ok).text(data.saved + ' ' + (ok
                        ? '{{ lang._("rules saved and applied (Custom, source AI): devices are reclassified within a minute.") }}'
                        : '{{ lang._("rules saved, but applying failed: use Apply below.") }}'));
                });
            });
        });
        $('.loupe-source .btn[data-source="ai"]').click(function () { if (!ai.loaded) aiLoad(); });

        $('#reconfigureAct').SimpleActionButton({
            onPreAction: function () {
                const dfObj = new $.Deferred();
                if (!started.general) {
                    return dfObj.resolve();     /* General never loaded on this visit: nothing of it to save */
                }
                saveFormToEndpoint('/api/loupe/settings/set', 'frm_settings', function () { dfObj.resolve(); });
                return dfObj;
            },
            onAction: function () {
                pending = false;
                applyButton();
                updateServiceControlUI('loupe');
                loadNetworks();         /* the interfaces may have changed */
            }
        });
        updateServiceControlUI('loupe');
        loadNetworks().always(route);     /* the grids need to know whether to show the Network column */
    });
</script>

{% set periods = [['1', lang._('1 hour')], ['3', lang._('3 hours')], ['24', lang._('24 hours')], ['168', lang._('7 days')], ['720', lang._('30 days')], ['8760', lang._('1 year')]] %}

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
            <div class="loupe-netpick" style="display: none">
                <select class="selectpicker loupe-networks" multiple="multiple" data-style="btn-default btn-sm"
                        data-actions-box="true" data-select-all-text="{{ lang._('All networks') }}"
                        data-deselect-all-text="{{ lang._('None') }}" data-selected-text-format="count > 1"
                        title="{{ lang._('No networks') }}"></select>
            </div>
            <div class="loupe-rebuild text-muted" style="display: none"></div>
        </div>
        <div id="loupe-list">
            <table id="grid-devices" class="table table-condensed table-hover table-striped table-responsive">
                <thead>
                <tr>
                    <th data-column-id="id" data-identifier="true" data-visible="false">{{ lang._('ID') }}</th>
                    <th data-column-id="name" data-formatter="device">{{ lang._('Device') }}</th>
                    <th data-column-id="type" data-formatter="devtype">{{ lang._('Type') }}</th>
                    <th data-column-id="ip" data-width="10em" data-formatter="copy">{{ lang._('Address') }}</th>
                    <th data-column-id="networks" data-formatter="networks" data-sortable="false" data-visible="false">{{ lang._('Network') }}</th>
                    <th data-column-id="vendor" data-formatter="vendor">{{ lang._('Vendor') }}</th>
                    <th data-column-id="mac" data-visible="false" data-formatter="copy">{{ lang._('MAC address') }}</th>
                    <th data-column-id="down" data-formatter="bytes" data-width="8em">{{ lang._('Down') }}</th>
                    <th data-column-id="up" data-formatter="bytes" data-width="8em">{{ lang._('Up') }}</th>
                    <th data-column-id="conns" data-visible="false" data-width="6em">{{ lang._('Conns') }}</th>
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
            <table id="grid-devtraffic" class="table table-condensed table-hover table-striped table-responsive">
                <thead><tr>
                    <th data-column-id="id" data-identifier="true" data-visible="false">{{ lang._('ID') }}</th>
                    <th data-column-id="label" data-formatter="devtraffic">{{ lang._('Service / Site') }}</th>
                    <th data-column-id="networks" data-formatter="networks" data-sortable="false" data-visible="false">{{ lang._('Network') }}</th>
                    <th data-column-id="pct" data-formatter="pct" data-width="10em">{{ lang._('% of total') }}</th>
                    <th data-column-id="down" data-formatter="bytes" data-width="8em">{{ lang._('Down') }}</th>
                    <th data-column-id="up" data-formatter="bytes" data-width="8em">{{ lang._('Up') }}</th>
                    <th data-column-id="conns" data-width="6em">{{ lang._('Conns') }}</th>
                    <th data-column-id="first" data-formatter="datetime" data-width="11em">{{ lang._('First seen') }}</th>
                    <th data-column-id="last" data-formatter="ago" data-width="9em">{{ lang._('Last seen') }}</th>
                </tr></thead>
                <tbody></tbody>
            </table>
        </div>
    </div>

    <div id="tab_services" class="tab-pane fade in">
        <div class="loupe-bar">
            <div class="btn-group loupe-period">
                {% for p in periods %}<button type="button" class="btn btn-default btn-sm" data-hours="{{ p[0] }}">{{ p[1] }}</button>{% endfor %}
            </div>
            <div class="loupe-netpick" style="display: none">
                <select class="selectpicker loupe-networks" multiple="multiple" data-style="btn-default btn-sm"
                        data-actions-box="true" data-select-all-text="{{ lang._('All networks') }}"
                        data-deselect-all-text="{{ lang._('None') }}" data-selected-text-format="count > 1"
                        title="{{ lang._('No networks') }}"></select>
            </div>
            <div class="loupe-rebuild text-muted" style="display: none"></div>
        </div>
        <div id="loupe-overview" style="display: none">
            <table id="grid-house" class="table table-condensed table-hover table-striped table-responsive">
                <thead><tr>
                    <th data-column-id="service" data-identifier="true" data-formatter="housesvc" data-width="16em">{{ lang._('Service') }}</th>
                    <th data-column-id="devices" data-formatter="housedevices">{{ lang._('Devices') }}</th>
                    <th data-column-id="down" data-formatter="bytes" data-width="8em">{{ lang._('Down') }}</th>
                    <th data-column-id="up" data-formatter="bytes" data-width="8em">{{ lang._('Up') }}</th>
                    <th data-column-id="conns" data-visible="false" data-width="6em">{{ lang._('Conns') }}</th>
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
            <div style="height: 220px; padding: 0 15px 10px"><canvas id="loupe-service-chart"></canvas></div>
            <table id="grid-traffic" class="table table-condensed table-hover table-striped table-responsive">
                <thead><tr>
                    <th data-column-id="device" data-formatter="servicedevice">{{ lang._('Device') }}</th>
                    <th data-column-id="ip" data-width="10em" data-formatter="copy">{{ lang._('Address') }}</th>
                    <th data-column-id="name" data-formatter="servicesite">{{ lang._('Site') }}</th>
                    <th data-column-id="pct" data-formatter="pct" data-width="10em">{{ lang._('% of total') }}</th>
                    <th data-column-id="down" data-formatter="bytes" data-width="8em">{{ lang._('Down') }}</th>
                    <th data-column-id="up" data-formatter="bytes" data-width="8em">{{ lang._('Up') }}</th>
                    <th data-column-id="conns" data-width="6em">{{ lang._('Conns') }}</th>
                    <th data-column-id="first" data-formatter="datetime" data-width="11em">{{ lang._('First seen') }}</th>
                    <th data-column-id="last" data-formatter="ago" data-width="9em">{{ lang._('Last seen') }}</th>
                </tr></thead>
                <tbody></tbody>
            </table>
        </div>
    </div>

    <div id="tab_settings" class="tab-pane fade in">
        <div class="loupe-bar">
            <div class="btn-group loupe-pages">
                <button type="button" class="btn btn-default btn-sm" data-page="general">{{ lang._('General') }}</button>
                <button type="button" class="btn btn-default btn-sm" data-page="servicenames">{{ lang._('Service names') }}</button>
                <button type="button" class="btn btn-default btn-sm" data-page="rules">{{ lang._('Device rules') }}</button>
                <button type="button" class="btn btn-default btn-sm" data-page="overrides">{{ lang._('Overrides') }}</button>
            </div>
        </div>
        <div id="page_general" class="loupe-page">
            {{ partial("layout_partials/base_form", ['fields': formSettings, 'id': 'frm_settings']) }}
        </div>
        <div id="page_servicenames" class="loupe-page">
            <div class="loupe-bar">
                <div class="btn-group btn-group-xs loupe-source">
                    <button type="button" class="btn btn-default active" data-source="mine">{{ lang._('Custom') }}</button>
                    <button type="button" class="btn btn-default" data-source="builtin">{{ lang._('Built-in') }}</button>
                </div>
                <p class="loupe-source-help" data-for="mine" style="margin: 8px 0 0">{{ lang._('Your own names for domains. They add to and override the built-in list, and apply to all history.') }}</p>
                <p class="loupe-source-help" data-for="builtin" style="margin: 8px 0 0; display: none">{{ lang._('The names Loupe ships with: domains (a domain also covers its subdomains), address ranges and ports for traffic with no name, and the domains VPN apps use. Add your own under Custom to change one.') }}</p>
            </div>
            <div data-source-pane="mine">
                {{ partial('layout_partials/base_bootgrid_table', formGridServices) }}
            </div>
            <div data-source-pane="builtin" style="display: none">
                <table id="grid-builtin-services" class="table table-condensed table-hover table-striped table-responsive">
                    <thead><tr>
                        <th data-column-id="pattern" data-identifier="true">{{ lang._('Domain, range or port') }}</th>
                        <th data-column-id="name">{{ lang._('Service') }}</th>
                        <th data-column-id="kind">{{ lang._('Kind') }}</th>
                    </tr></thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>
        <div id="page_rules" class="loupe-page">
            <div class="loupe-bar">
                <div class="btn-group btn-group-xs loupe-source">
                    <button type="button" class="btn btn-default active" data-source="mine">{{ lang._('Custom') }}</button>
                    <button type="button" class="btn btn-default" data-source="builtin">{{ lang._('Built-in') }}</button>
                    <button type="button" class="btn btn-default" data-source="ai">{{ lang._('AI Rule Generator') }}</button>
                </div>
                <p class="loupe-source-help" data-for="mine" style="margin: 8px 0 0">{{ lang._('Set the type of every device that matches, e.g. all devices whose MAC vendor is Tuya are Window shades. Your rules are checked before the built-in ones; an override for one device still wins. Your rules are tried by their strongest clue (MAC prefix, Bonjour model, host name, DHCP vendor class, domain, Bonjour service, MAC vendor), then in the order you added them; the first that matches decides. Apply to use them: devices are reclassified within a minute.') }}</p>
                <p class="loupe-source-help" data-for="builtin" style="margin: 8px 0 0; display: none">{{ lang._('The rules Loupe ships with, in the order they are tried within each kind of evidence (Bonjour model, host name, DHCP, domains it talks to, MAC vendor). The first one that matches decides the type.') }}</p>
                <p class="loupe-source-help" data-for="ai" style="margin: 8px 0 0; display: none">{{ lang._('Ask any AI chat (Claude, ChatGPT, Gemini...) what the devices Loupe can only guess at are, and turn its answer into rules. Loupe sends nothing anywhere: you copy the prompt and paste the answer. The prompt carries what the devices announce and the domains they use, without MAC or IP addresses.') }}</p>
            </div>
            <div data-source-pane="mine">
                {{ partial('layout_partials/base_bootgrid_table', formGridRules) }}
                <div id="rule-source-container" class="btn-group" style="display: none">
                    <select id="rule-source-filter" data-width="140px" title="{{ lang._('Which rules to list') }}">
                        <option value="">{{ lang._('All rules') }}</option>
                        <option value="manual">{{ lang._('Manual') }}</option>
                        <option value="ai">{{ lang._('AI') }}</option>
                    </select>
                </div>
            </div>
            <div data-source-pane="builtin" style="display: none">
                <table id="grid-builtin-rules" class="table table-condensed table-hover table-striped table-responsive">
                    <thead><tr>
                        <th data-column-id="order" data-identifier="true" data-type="numeric" data-width="5em">#</th>
                        <th data-column-id="match">{{ lang._('Matches') }}</th>
                        <th data-column-id="type" data-formatter="ruletype">{{ lang._('Type') }}</th>
                        <th data-column-id="note" data-visible="false">{{ lang._('Note') }}</th>
                    </tr></thead>
                    <tbody></tbody>
                </table>
            </div>
            <div data-source-pane="ai" style="display: none">
                <div class="loupe-ai-step">
                    <h4>{{ lang._('1. Ask') }}</h4>
                    <p class="text-muted">{{ lang._('Copy the prompt into an AI chat, ideally one that can search the web. By default it asks only about the devices Loupe could not identify from their own evidence: those with no type, or only a generic one such as IoT device (ESP).') }}</p>
                    <div style="margin-bottom: 6px">
                        <label class="radio-inline"><input type="radio" name="ai-scope" value="" checked> <span id="ai-count-unidentified"></span> {{ lang._('unidentified or generic devices') }}</label>
                        <label class="radio-inline"><input type="radio" name="ai-scope" value="all"> {{ lang._('All') }} <span id="ai-count-all"></span> {{ lang._('devices') }}</label>
                    </div>
                    <p id="ai-cut" class="text-warning"></p>
                    <div style="margin-bottom: 6px">
                        <button type="button" class="btn btn-primary" id="ai-copy"><i class="fa fa-copy fa-fw"></i> {{ lang._('Copy prompt') }}</button>
                        <a href="#" id="ai-show">{{ lang._('Show prompt') }}</a>
                    </div>
                    <pre id="ai-prompt" style="display: none; max-height: 24em; overflow: auto; white-space: pre-wrap"></pre>
                </div>
                <div class="loupe-ai-step">
                    <h4>{{ lang._('2. Paste the answer') }}</h4>
                    <p class="text-muted">{{ lang._('Paste the whole reply; the rules are read from it, even when the reply is cut off.') }}</p>
                    <div id="ai-paste-problem" class="alert alert-warning" style="display: none; margin-bottom: 6px"></div>
                    <textarea id="ai-reply" class="form-control" rows="20" style="font-family: monospace; width: 100%; max-width: none; resize: vertical"></textarea>
                    <button type="button" class="btn btn-primary" id="ai-check" style="margin-top: 6px"><i class="fa fa-wand-magic-sparkles fa-fw"></i> {{ lang._('Generate rules') }}</button>
                </div>
                <div class="loupe-ai-step" id="ai-review" style="display: none">
                    <h4>{{ lang._('3. Review') }}</h4>
                    <p class="text-muted">{{ lang._('What each rule would change on your network. Rules that reach other devices, change nothing or make a type vaguer start unticked.') }}</p>
                    <div id="ai-problem" class="alert alert-warning" style="display: none"></div>
                    <table class="table table-condensed table-striped" id="ai-rules">
                        <thead><tr>
                            <th style="width: 2em"></th><th>{{ lang._('Type') }}</th><th>{{ lang._('Matches') }}</th>
                            <th>{{ lang._('For') }}</th><th>{{ lang._('Would change') }}</th><th>{{ lang._('Notes') }}</th>
                        </tr></thead>
                        <tbody></tbody>
                    </table>
                    <p id="ai-unknown" class="text-muted"></p>
                    <button type="button" class="btn btn-primary" id="ai-save"><i class="fa fa-save fa-fw"></i> {{ lang._('Save and apply') }}</button>
                    <span id="ai-saved" style="margin-left: 10px"></span>
                </div>
            </div>
        </div>
        <div id="page_overrides" class="loupe-page">
            <p class="loupe-bar">{{ lang._('Your name or type for one device, matched by its MAC address. They win over everything Loupe detects. The pencil next to a device on the main Devices tab does the same.') }}</p>
            {{ partial('layout_partials/base_bootgrid_table', formGridOverrides) }}
        </div>
    </div>
</div>

<div class="loupe-apply" style="display: none">
    {{ partial('layout_partials/base_apply_button', {'data_endpoint': '/api/loupe/service/reconfigure', 'data_service_widget': 'loupe', 'data_error_title': 'Error applying Loupe settings', 'data_change_message_content': 'Changes saved but not in use yet: Apply to use them.'}) }}
</div>

{{ partial("layout_partials/base_dialog", ['fields': formDialogService, 'id': formGridServices['edit_dialog_id'], 'label': lang._('Service name')]) }}
{{ partial("layout_partials/base_dialog", ['fields': formDialogDevice, 'id': formGridOverrides['edit_dialog_id'], 'label': lang._('Device')]) }}
{{ partial("layout_partials/base_dialog", ['fields': formDialogRule, 'id': formGridRules['edit_dialog_id'], 'label': lang._('Device rule')]) }}
