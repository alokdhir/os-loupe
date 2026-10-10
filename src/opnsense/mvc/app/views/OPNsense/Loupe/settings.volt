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
        mapDataToFormUI({'frm_settings': '/api/loupe/settings/get'}).done(function () {
            formatTokenizersUI();
            $('.selectpicker').selectpicker('refresh');
            updateServiceControlUI('loupe');
        });

        $('#grid-services').UIBootgrid({
            search: '/api/loupe/settings/search_service',
            get: '/api/loupe/settings/get_service/',
            set: '/api/loupe/settings/set_service/',
            add: '/api/loupe/settings/add_service/',
            del: '/api/loupe/settings/del_service/'
        });
        $('#grid-devices').UIBootgrid({
            search: '/api/loupe/settings/search_device',
            get: '/api/loupe/settings/get_device/',
            set: '/api/loupe/settings/set_device/',
            add: '/api/loupe/settings/add_device/',
            del: '/api/loupe/settings/del_device/'
        });

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
    });
</script>

<ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
    <li class="active"><a data-toggle="tab" href="#general">{{ lang._('General') }}</a></li>
    <li><a data-toggle="tab" href="#services">{{ lang._('Service names') }}</a></li>
    <li><a data-toggle="tab" href="#devices">{{ lang._('Device names') }}</a></li>
</ul>
<div class="tab-content content-box">
    <div id="general" class="tab-pane fade in active">
        {{ partial("layout_partials/base_form",['fields':formSettings,'id':'frm_settings']) }}
    </div>
    <div id="services" class="tab-pane fade in">
        <p style="padding: 10px 10px 0">{{ lang._('Your own names for domains. They add to and override the built-in list, and apply to all history.') }}</p>
        <table id="grid-services" class="table table-condensed table-hover table-striped table-responsive" data-editDialog="DialogService" data-editAlert="loupeChangeMessage">
            <thead>
            <tr>
                <th data-column-id="uuid" data-type="string" data-identifier="true" data-visible="false">{{ lang._('ID') }}</th>
                <th data-column-id="suffix" data-type="string">{{ lang._('Domain') }}</th>
                <th data-column-id="name" data-type="string">{{ lang._('Service') }}</th>
                <th data-column-id="commands" data-width="7em" data-formatter="commands" data-sortable="false">{{ lang._('Commands') }}</th>
            </tr>
            </thead>
            <tbody></tbody>
            <tfoot>
            <tr>
                <td></td>
                <td>
                    <button data-action="add" type="button" class="btn btn-xs btn-primary"><span class="fa fa-fw fa-plus"></span></button>
                    <button data-action="deleteSelected" type="button" class="btn btn-xs btn-default"><span class="fa fa-fw fa-trash-o"></span></button>
                </td>
            </tr>
            </tfoot>
        </table>
    </div>
    <div id="devices" class="tab-pane fade in">
        <p style="padding: 10px 10px 0">{{ lang._('Name a device or correct its detected type. Matched by MAC address.') }}</p>
        <table id="grid-devices" class="table table-condensed table-hover table-striped table-responsive" data-editDialog="DialogDevice" data-editAlert="loupeChangeMessage">
            <thead>
            <tr>
                <th data-column-id="uuid" data-type="string" data-identifier="true" data-visible="false">{{ lang._('ID') }}</th>
                <th data-column-id="mac" data-type="string">{{ lang._('MAC address') }}</th>
                <th data-column-id="name" data-type="string">{{ lang._('Name') }}</th>
                <th data-column-id="type" data-type="string">{{ lang._('Type') }}</th>
                <th data-column-id="commands" data-width="7em" data-formatter="commands" data-sortable="false">{{ lang._('Commands') }}</th>
            </tr>
            </thead>
            <tbody></tbody>
            <tfoot>
            <tr>
                <td></td>
                <td>
                    <button data-action="add" type="button" class="btn btn-xs btn-primary"><span class="fa fa-fw fa-plus"></span></button>
                    <button data-action="deleteSelected" type="button" class="btn btn-xs btn-default"><span class="fa fa-fw fa-trash-o"></span></button>
                </td>
            </tr>
            </tfoot>
        </table>
    </div>
</div>

<section class="page-content-main">
    <div class="content-box">
        <div class="col-md-12">
            <br/>
            <div id="loupeChangeMessage" class="alert alert-info" style="display: none" role="alert">
                {{ lang._('After changing settings, apply them with the button below.') }}
            </div>
            <button class="btn btn-primary" id="reconfigureAct"
                    data-endpoint="/api/loupe/service/reconfigure"
                    data-label="{{ lang._('Apply') }}"
                    data-service-widget="loupe"
                    data-error-title="{{ lang._('Error applying Loupe settings') }}"
                    type="button"></button>
            <br/><br/>
        </div>
    </div>
</section>

{{ partial("layout_partials/base_dialog",['fields':formDialogService,'id':'DialogService','label':lang._('Service name')]) }}
{{ partial("layout_partials/base_dialog",['fields':formDialogDevice,'id':'DialogDevice','label':lang._('Device')]) }}
