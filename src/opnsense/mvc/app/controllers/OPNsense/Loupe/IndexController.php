<?php

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
 * POSSIBILITY OF SUCH DAMAGE.
 */

namespace OPNsense\Loupe;

class IndexController extends \OPNsense\Base\IndexController
{
    /**
     * One page with tabs (like Reporting: Unbound DNS): Devices, Services, Switches, Settings (General,
     * Service names, Device rules, Overrides, Switches). The old addresses open the matching tab. The Switches
     * tab is hidden until a switch is listed.
     */
    private function page($activetab)
    {
        // grid ids must not contain '-': mapDataToFormUI matches a dialog's form by the id before the first '-'
        $this->view->activetab = $activetab;
        $this->view->has_switches = count(iterator_to_array((new Loupe())->switches->switch->iterateItems())) > 0;
        $this->view->formSettings = $this->getForm('settings');
        $this->view->formDialogService = $this->getForm('dialogService');
        $this->view->formDialogDevice = $this->getForm('dialogDevice');
        $this->view->formGridServices = $this->getFormGrid('dialogService', 'gridServiceNames');
        $this->view->formGridOverrides = $this->getFormGrid('dialogDevice', 'gridOverrides');
        $this->view->formDialogModelName = $this->getForm('dialogModelName');
        $this->view->formGridModelNames = $this->getFormGrid('dialogModelName', 'gridModelNames');
        $this->view->formDialogSwitch = $this->getForm('dialogSwitch');
        // what the last read of a switch said: not a setting, so not in its form (search_switch adds it)
        $switchGrid = $this->getFormGrid('dialogSwitch', 'gridSwitches');
        $address = array_search('address', array_column($switchGrid['fields'], 'column-id'));
        array_splice($switchGrid['fields'], $address, 0, [[
            'column-id' => 'ports', 'label' => gettext('Ports'), 'visible' => 'true', 'sortable' => 'false',
            'identifier' => 'false', 'type' => 'string', 'width' => '6em',
        ]]);
        array_splice($switchGrid['fields'], -1, 0, [[
            'column-id' => 'last', 'label' => gettext('Last result'), 'visible' => 'true', 'sortable' => 'false',
            'identifier' => 'false', 'type' => 'string', 'formatter' => 'switchlast',
        ]]);
        $this->view->formGridSwitches = $switchGrid;
        $this->view->formDialogRule = $this->getForm('dialogRule');
        // a rule's clues are hidden columns; one column says what it matches (built by search_rule)
        $grid = $this->getFormGrid('dialogRule', 'gridRules');
        array_splice($grid['fields'], 1, 0, [[
            'column-id' => 'match', 'label' => gettext('Matches'), 'visible' => 'true', 'sortable' => 'false',
            'identifier' => 'false', 'type' => 'string',
        ]]);
        foreach ($grid['fields'] as &$field) {      // the icon it shows, also when Automatic (search_rule's icon_class)
            if (($field['column-id'] ?? '') === 'icon') {
                $field['formatter'] = 'ruleicon';
            }
        }
        unset($field);
        $this->view->formGridRules = $grid;
        $this->view->pick('OPNsense/Loupe/index');
    }

    public function indexAction()
    {
        $this->page('devices');
    }

    public function settingsAction()
    {
        $this->page('general');
    }
}
