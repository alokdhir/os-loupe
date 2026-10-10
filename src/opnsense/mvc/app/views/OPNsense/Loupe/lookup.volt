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

<script>
    $(document).ready(function () {
        let hours = parseFloat(localStorage.getItem('loupe.hours') || '24');
        let q = '';
        const grids = {};
        const esc = s => $('<span>').text(s == null ? '' : String(s)).html();
        const dash = '<span class="text-muted">&mdash;</span>';
        const formatters = {
            device: function (column, row) {
                return '<a href="/ui/loupe/#device=' + esc(row.mac || row.ip) + '">' + esc(row.device) + '</a>'
                    + (row.device !== row.ip ? ' <span class="text-muted">' + esc(row.ip) + '</span>' : '')
                    + (row.type ? ' <span class="text-muted">· ' + esc(row.type) + '</span>' : '');
            },
            site: function (column, row) {
                return esc(row.name || (row.servers || []).join(', '));
            },
            service: function (column, row) {
                return row.service ? esc(row.service) : dash;
            }
        };

        function grid(id, endpoint) {
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
                        request.q = q;
                        return request;
                    },
                    formatters: formatters
                }
            });
        }

        function search() {
            q = $('#loupe-q').val().trim();
            if (!q) return;
            history.replaceState(null, '', '#q=' + encodeURIComponent(q));
            $('#loupe-results').show();
            grid('grid-traffic', '/api/loupe/report/search_lookup_traffic');
            grid('grid-names', '/api/loupe/report/search_lookup_names');
        }

        $('#loupe-period .btn').click(function () {
            hours = parseFloat($(this).data('hours'));
            localStorage.setItem('loupe.hours', hours);
            $('#loupe-period .btn').removeClass('active');
            $(this).addClass('active');
            search();
        }).filter('[data-hours="' + hours + '"]').addClass('active');
        $('#loupe-form').submit(function (e) { e.preventDefault(); search(); });
        const m = window.location.hash.match(/q=([^&]+)/);
        if (m) { $('#loupe-q').val(decodeURIComponent(m[1])); search(); }
    });
</script>

<div class="content-box">
    <div style="padding: 10px 15px">
        <form id="loupe-form" class="form-inline">
            <div class="input-group" style="width: 380px; display: inline-table; vertical-align: middle">
                <input id="loupe-q" type="text" class="form-control" placeholder="{{ lang._('Site or address, e.g. netflix or 17.250.96.102') }}"/>
                <span class="input-group-btn">
                    <button class="btn btn-primary" type="submit"><i class="fa fa-search"></i> {{ lang._('Look up') }}</button>
                </span>
            </div>
            <div class="btn-group" id="loupe-period" style="margin-left: 10px">
                <button type="button" class="btn btn-default btn-sm" data-hours="1">{{ lang._('1 hour') }}</button>
                <button type="button" class="btn btn-default btn-sm" data-hours="24">{{ lang._('24 hours') }}</button>
                <button type="button" class="btn btn-default btn-sm" data-hours="168">{{ lang._('7 days') }}</button>
                <button type="button" class="btn btn-default btn-sm" data-hours="720">{{ lang._('30 days') }}</button>
                <button type="button" class="btn btn-default btn-sm" data-hours="8760">{{ lang._('1 year') }}</button>
            </div>
        </form>
    </div>
    <div id="loupe-results" style="display: none">
        <ul class="nav nav-tabs" data-tabs="tabs">
            <li class="active"><a data-toggle="tab" href="#tab-traffic">{{ lang._('Traffic') }}</a></li>
            <li><a data-toggle="tab" href="#tab-names">{{ lang._('Looked up or connected to') }}</a></li>
        </ul>
        <div class="tab-content">
            <div id="tab-traffic" class="tab-pane fade in active">
                <table id="grid-traffic" class="table table-condensed table-hover table-striped table-responsive">
                    <thead><tr>
                        <th data-column-id="device" data-formatter="device">{{ lang._('Device') }}</th>
                        <th data-column-id="name" data-formatter="site">{{ lang._('Site') }}</th>
                        <th data-column-id="service" data-formatter="service">{{ lang._('Service') }}</th>
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
                        <th data-column-id="device" data-formatter="device">{{ lang._('Device') }}</th>
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
