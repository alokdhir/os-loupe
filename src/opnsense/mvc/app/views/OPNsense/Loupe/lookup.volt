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

<style>
    .loupe-bar { padding: 10px 15px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .loupe-bar .btn-group .btn.active { font-weight: bold; }
    .loupe-table th { white-space: nowrap; }
    .loupe-table td.num, .loupe-table th.num { text-align: right; white-space: nowrap; }
    .loupe-muted { color: #888; }
    .loupe-section { padding: 0 15px 15px; }
    .loupe-section h3 { margin-top: 15px; font-size: 16px; }
</style>
<script>
    $(document).ready(function () {
        let hours = parseFloat(localStorage.getItem('loupe.hours') || '24');
        const bytes = function (b) {
            if (!b) return '0';
            const u = ['B', 'KB', 'MB', 'GB', 'TB'];
            let i = Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1000)));
            return (b / Math.pow(1000, i)).toFixed(i ? 1 : 0) + ' ' + u[i];
        };
        const esc = s => $('<span>').text(s == null ? '' : String(s)).html();
        const when = ts => ts ? new Date(ts * 1000).toLocaleString() : '';
        const dev = r => '<a href="/ui/loupe/#device=' + encodeURIComponent(r.mac || r.ip) + '">' + esc(r.device) + '</a>'
            + (r.device !== r.ip ? ' <span class="loupe-muted">' + esc(r.ip) + '</span>' : '')
            + (r.type ? ' <span class="loupe-muted">· ' + esc(r.type) + '</span>' : '');

        function search() {
            const q = $('#loupe-q').val().trim();
            if (!q) return;
            window.location.hash = 'q=' + encodeURIComponent(q);
            $('#loupe-status').text('{{ lang._("Searching...") }}');
            ajaxGet('/api/loupe/report/lookup/' + encodeURIComponent(q) + '/' + hours, {}, function (data) {
                if (data.error) { $('#loupe-status').text(data.error); return; }
                $('#loupe-status').text(data.traffic.length + ' {{ lang._("device/site pairs with traffic") }}, ' + data.lookups.length + ' {{ lang._("DNS lookups") }}');
                let h = '<thead><tr><th>{{ lang._("Device") }}</th><th>{{ lang._("Site") }}</th><th>{{ lang._("Service") }}</th>'
                    + '<th class="num">{{ lang._("Down") }}</th><th class="num">{{ lang._("Up") }}</th><th class="num">{{ lang._("Connections") }}</th>'
                    + '<th>{{ lang._("First") }}</th><th>{{ lang._("Last") }}</th></tr></thead><tbody>';
                data.traffic.forEach(function (r) {
                    h += '<tr><td>' + dev(r) + '</td><td>' + esc(r.name || r.servers.join(', ')) + '</td><td>' + esc(r.service)
                        + '</td><td class="num">' + bytes(r.down) + '</td><td class="num">' + bytes(r.up) + '</td><td class="num">' + r.conns
                        + '</td><td>' + when(r.first) + '</td><td>' + when(r.last) + '</td></tr>';
                });
                $('#loupe-traffic').html(h + '</tbody>');
                h = '<thead><tr><th>{{ lang._("Device") }}</th><th>{{ lang._("Name") }}</th><th>{{ lang._("Seen in") }}</th>'
                    + '<th class="num">{{ lang._("Times") }}</th><th>{{ lang._("First") }}</th><th>{{ lang._("Last") }}</th></tr></thead><tbody>';
                data.lookups.forEach(function (r) {
                    h += '<tr><td>' + dev(r) + '</td><td>' + esc(r.name) + '</td><td>' + esc(r.source) + '</td><td class="num">' + r.count
                        + '</td><td>' + when(r.first) + '</td><td>' + when(r.last) + '</td></tr>';
                });
                $('#loupe-lookups').html(h + '</tbody>');
                $('#loupe-lookups-section').toggle(data.lookups.length > 0);
            });
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
    <div class="loupe-bar">
        <form id="loupe-form" class="form-inline" style="display: flex; gap: 6px">
            <input id="loupe-q" type="text" class="form-control" style="width: 320px" placeholder="{{ lang._('Site or address, e.g. netflix or 17.250.96.102') }}"/>
            <button class="btn btn-primary btn-sm" type="submit"><i class="fa fa-search"></i> {{ lang._('Look up') }}</button>
        </form>
        <div class="btn-group" id="loupe-period">
            <button class="btn btn-default btn-sm" data-hours="1">{{ lang._('1 hour') }}</button>
            <button class="btn btn-default btn-sm" data-hours="24">{{ lang._('24 hours') }}</button>
            <button class="btn btn-default btn-sm" data-hours="168">{{ lang._('7 days') }}</button>
            <button class="btn btn-default btn-sm" data-hours="720">{{ lang._('30 days') }}</button>
            <button class="btn btn-default btn-sm" data-hours="8760">{{ lang._('1 year') }}</button>
        </div>
        <span id="loupe-status" class="loupe-muted"></span>
    </div>
    <div class="loupe-section"><h3>{{ lang._('Traffic') }}</h3>
        <table id="loupe-traffic" class="table table-condensed table-striped loupe-table"></table></div>
    <div class="loupe-section" id="loupe-lookups-section" style="display: none"><h3>{{ lang._('Looked up or connected to') }}</h3>
        <table id="loupe-lookups" class="table table-condensed table-striped loupe-table"></table></div>
</div>
