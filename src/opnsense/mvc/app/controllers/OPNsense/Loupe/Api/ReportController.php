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
 */

namespace OPNsense\Loupe\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;

class ReportController extends ApiControllerBase
{
    private function query(array $args)
    {
        $out = (new Backend())->configdpRun('loupe query', $args);
        $data = json_decode($out ?? '', true);
        return is_array($data) ? $data : ['error' => 'no response from loupe'];
    }

    private function hours($hours)
    {
        $hours = (float)$hours;
        return ($hours > 0 && $hours <= 87600) ? (string)$hours : '24';
    }

    public function devicesAction($hours = 24)
    {
        return $this->query(['devices', $this->hours($hours)]);
    }

    public function deviceAction($id = '', $hours = 24)
    {
        $id = rawurldecode($id);
        if (!preg_match('/^[0-9a-fA-F:.]{2,45}$/', $id)) {
            return ['error' => 'invalid device'];
        }
        return $this->query(['device', $id, $this->hours($hours)]);
    }

    public function lookupAction($text = '', $hours = 24)
    {
        $text = rawurldecode($text);
        if (!preg_match(self::TEXT, $text)) {
            return ['error' => 'enter a service, site or address'];
        }
        return $this->query(['lookup', $text, $this->hours($hours)]);
    }

    public function widgetAction()
    {
        return $this->query(['widget']);
    }

    /* grid endpoints (UIBootgrid search): sort, search and paging via searchRecordsetBase() */

    private function postHours()
    {
        return $this->hours($this->request->getPost('hours', null, 24));
    }

    private function postDevice()
    {
        $id = (string)$this->request->getPost('device', null, '');
        return preg_match('/^[0-9a-fA-F:.]{2,45}$/', $id) ? $id : null;
    }

    /* what each grid's search box matches: the columns it shows, never the explanations behind them */
    private const SEARCH = [
        'devices' => ['name', 'shown_type', 'vendor'],
        'services' => ['service', 'names'],
        'sites' => ['name', 'service', 'servers'],
        'ports' => ['port'],
        'traffic' => ['device', 'ip', 'name', 'service', 'servers'],
        'lookups' => ['device', 'ip', 'name', 'source'],
    ];

    private function grid(array $rows, string $defaultSort = 'down', $searchClauses = null, $fields = null)
    {
        // newest/biggest first unless the user sorts otherwise
        usort($rows, function ($a, $b) use ($defaultSort) {
            return ($b[$defaultSort] ?? 0) <=> ($a[$defaultSort] ?? 0);
        });
        return $this->searchRecordsetBase($rows, $fields, null, null, SORT_NATURAL | SORT_FLAG_CASE, $searchClauses);
    }

    public function searchDevicesAction()
    {
        $data = $this->query(['devices', $this->postHours()]);
        $rows = [];
        foreach ($data['rows'] ?? [] as $r) {
            $r['id'] = $r['mac'] ?: $r['ip'];
            $r['shown_type'] = ($r['type_source'] ?? '') === 'set by you' ? $r['type'] : ($r['model'] ?: $r['type']);
            $rows[] = $r;
        }
        return $this->grid($rows, 'down', null, self::SEARCH['devices']);
    }

    private function deviceSection($section, $sort = 'down')
    {
        $id = $this->postDevice();
        if ($id === null) {
            return $this->searchRecordsetBase([]);
        }
        $data = $this->query(['device', $id, $this->postHours()]);
        return $this->grid($data[$section] ?? [], $sort, null, self::SEARCH[$section]);
    }

    public function searchServicesAction()
    {
        return $this->deviceSection('services');
    }

    public function searchSitesAction()
    {
        return $this->deviceSection('sites');
    }

    public function searchPortsAction()
    {
        return $this->deviceSection('ports');
    }

    /* a service, site or address: letters, digits, spaces and the punctuation service names use */
    private const TEXT = '/^[^\p{C}"\\\\`]{1,253}$/u';     // printable, no quotes/backslash/backtick

    /* the services list; its search box matches services, sites and addresses (in query.py, not row text) */
    public function searchHouseAction()
    {
        $text = trim((string)$this->request->getPost('searchPhrase', null, ''));
        $args = ['sites', $this->postHours()];
        if ($text !== '') {
            if (!preg_match(self::TEXT, $text)) {
                return $this->searchRecordsetBase([]);
            }
            $args[] = $text;
        }
        $data = $this->query($args);
        return $this->grid($data['rows'] ?? [], 'down', ['']);
    }

    private function lookupSection($section, $sort)
    {
        $text = trim((string)$this->request->getPost('q', null, ''));
        if (!preg_match(self::TEXT, $text)) {
            return $this->searchRecordsetBase([]);
        }
        $data = $this->query(['lookup', $text, $this->postHours(), 'exact']);
        return $this->grid($data[$section] ?? [], $sort, null, self::SEARCH[$section]);
    }

    /* the shipped rule files, read-only (Settings: Built-in) */

    private const DATA = '/usr/local/opnsense/scripts/loupe/data/';

    private const FIELDS = [
        'vendor' => 'MAC vendor', 'hostname' => 'Host name', 'mdns_model' => 'Bonjour model',
        'mdns_service' => 'Bonjour service', 'dhcp_vendor' => 'DHCP vendor class', 'dhcp_params' => 'DHCP request list',
        'talks_to' => 'Talks to', 'mac_prefix' => 'MAC prefix', 'private_mac' => 'Private MAC',
    ];

    private function dataFile($name)
    {
        $data = json_decode((string)@file_get_contents(self::DATA . $name), true);
        return is_array($data) ? $data : [];
    }

    public function searchBuiltinServicesAction()
    {
        $data = $this->dataFile('services.json');
        $rows = [];
        foreach ($data['suffixes'] ?? [] as $suffix => $name) {
            $rows[] = ['pattern' => $suffix, 'name' => $name, 'kind' => gettext('Domain')];
        }
        foreach ($data['ranges'] ?? [] as $r) {
            $rows[] = ['pattern' => $r[0], 'name' => $r[1], 'kind' => gettext('Address range')];
        }
        foreach ($data['ports'] ?? [] as $p) {
            $rows[] = ['pattern' => "{$p[0]}/{$p[1]}", 'name' => $p[2], 'kind' => gettext('Port, when nothing else names it')];
        }
        foreach ($data['vpn_providers'] ?? [] as $domain => $provider) {
            $rows[] = ['pattern' => $domain, 'name' => "VPN ({$provider})", 'kind' => gettext('VPN app domain')];
        }
        return $this->searchRecordsetBase($rows, ['pattern', 'name', 'kind'], 'pattern');
    }

    private function describeCondition(array $cond)
    {
        $parts = [];
        foreach ($cond as $field => $want) {
            $label = self::FIELDS[$field] ?? $field;
            if (is_bool($want)) {
                $parts[] = $want ? $label : "not {$label}";
            } else {
                $parts[] = $label . ' ' . implode(' or ', (array)$want);
            }
        }
        return implode(' and ', $parts);
    }

    public function searchBuiltinRulesAction()
    {
        $data = $this->dataFile('devices.json');
        $rows = [];
        foreach ($data['rules'] ?? [] as $i => $rule) {
            $conds = isset($rule['all']) ? $rule['all'] : [$rule['when'] ?? []];
            $rows[] = [
                'order' => $i + 1,
                'match' => implode(' and ', array_map([$this, 'describeCondition'], $conds)),
                'type' => $rule['type'] ?? '',
                'icon' => $data['types'][$rule['type'] ?? '']['icon'] ?? '',
                'note' => $rule['note'] ?? '',
            ];
        }
        return $this->searchRecordsetBase($rows, ['match', 'type', 'note'], 'order', null, SORT_NUMERIC);
    }

    public function searchLookupTrafficAction()
    {
        return $this->lookupSection('traffic', 'down');
    }

    public function searchLookupNamesAction()
    {
        return $this->lookupSection('lookups', 'last');
    }
}
