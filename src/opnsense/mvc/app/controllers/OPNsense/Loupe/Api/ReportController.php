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

    private function grid(array $rows, string $defaultSort = 'down', $searchClauses = null)
    {
        // newest/biggest first unless the user sorts otherwise
        usort($rows, function ($a, $b) use ($defaultSort) {
            return ($b[$defaultSort] ?? 0) <=> ($a[$defaultSort] ?? 0);
        });
        return $this->searchRecordsetBase($rows, null, null, null, SORT_NATURAL | SORT_FLAG_CASE, $searchClauses);
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
        return $this->grid($rows);
    }

    private function deviceSection($section, $sort = 'down')
    {
        $id = $this->postDevice();
        if ($id === null) {
            return $this->searchRecordsetBase([]);
        }
        $data = $this->query(['device', $id, $this->postHours()]);
        return $this->grid($data[$section] ?? [], $sort);
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
    private const TEXT = '/^[a-zA-Z0-9 .:_+&()\/-]{1,253}$/';

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
        return $this->grid($data[$section] ?? [], $sort);
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
