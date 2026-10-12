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

namespace OPNsense\Loupe\Api;

use OPNsense\Base\ApiMutableModelControllerBase;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;

class SettingsController extends ApiMutableModelControllerBase
{
    protected static $internalModelName = 'loupe';
    protected static $internalModelClass = 'OPNsense\Loupe\Loupe';

    private const SERVICE_KINDS = ['domain' => 'Domain', 'address' => 'Address or range', 'port' => 'Port',
                                   'vpn' => 'VPN app domain'];

    public function searchServiceAction()
    {
        $result = $this->searchBase('services.service', ['kind', 'suffix', 'name'], 'suffix');
        foreach ($result['rows'] as &$row) {
            $row['kind'] = gettext(self::SERVICE_KINDS[$row['kind']] ?? self::SERVICE_KINDS['domain']);
        }
        return $result;
    }

    public function getServiceAction($uuid = null)
    {
        return $this->getBase('service', 'services.service', $uuid);
    }

    /**
     * Check a service entry for its kind while the dialog can still show what is wrong: a domain, a valid
     * IP address or range, or tcp|udp/port.
     */
    private function checkService()
    {
        $svc = $this->request->getPost('service');
        $kind = is_array($svc) ? (string)($svc['kind'] ?? 'domain') : 'domain';
        $text = is_array($svc) ? trim((string)($svc['suffix'] ?? '')) : '';
        if ($text === '') {
            return null;                        // the model reports the empty field
        }
        $error = null;
        if ($kind === 'address') {
            [$ip, $bits] = array_pad(explode('/', $text, 2), 2, null);
            $v6 = filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_IPV6) !== false;
            if (filter_var($ip, FILTER_VALIDATE_IP) === false
                || ($bits !== null && !(ctype_digit($bits) && (int)$bits <= ($v6 ? 128 : 32)))) {
                $error = gettext('Enter a valid IP address or range, e.g. 192.0.2.10 or 203.0.113.0/24.');
            }
        } elseif ($kind === 'port') {
            if (!preg_match('/^(tcp|udp)\/([0-9]{1,5})$/i', $text, $m) || (int)$m[2] < 1 || (int)$m[2] > 65535) {
                $error = gettext('Enter a protocol and port, e.g. tcp/8443 or udp/51820.');
            }
        } elseif (!preg_match('/^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)*[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.?$/i', $text)) {
            $error = gettext('Enter a domain, e.g. example.com.');
        }
        return $error === null ? null : ['result' => 'failed', 'validations' => ['service.suffix' => $error]];
    }

    public function addServiceAction()
    {
        return $this->checkService() ?? $this->addBase('service', 'services.service');
    }

    public function setServiceAction($uuid)
    {
        return $this->checkService() ?? $this->setBase('service', 'services.service', $uuid);
    }

    public function delServiceAction($uuid)
    {
        return $this->delBase('services.service', $uuid);
    }

    private const RULE_FIELDS = [
        'vendor' => 'MAC vendor', 'hostname' => 'Host name', 'mdns_model' => 'Bonjour model',
        'mdns_service' => 'Bonjour service', 'dhcp_vendor' => 'DHCP vendor class', 'talks_to' => 'Talks to',
        'mac_prefix' => 'MAC prefix',
    ];

    public function searchRuleAction()
    {
        $result = $this->searchBase('rules.rule', ['field', 'pattern', 'type', 'description'], 'type');
        foreach ($result['rows'] as &$row) {
            $row['field'] = gettext(self::RULE_FIELDS[$row['field']] ?? $row['field']);
        }
        return $result;
    }

    public function getRuleAction($uuid = null)
    {
        return $this->getBase('rule', 'rules.rule', $uuid);
    }

    /**
     * Host name, Bonjour model and DHCP vendor patterns are regular expressions: refuse ones that don't
     * compile here, where the dialog can show it, instead of loupd skipping them later.
     */
    private function checkRule()
    {
        $rule = $this->request->getPost('rule');
        $field = is_array($rule) ? ($rule['field'] ?? '') : '';
        $pattern = is_array($rule) ? (string)($rule['pattern'] ?? '') : '';
        if ($field === 'mac_prefix' && $pattern !== '') {
            foreach (explode(',', $pattern) as $prefix) {
                if (!preg_match('/^[0-9a-f]{2}([:.-]?[0-9a-f]{2}){0,5}$/i', trim($prefix))) {
                    return ['result' => 'failed', 'validations' => ['rule.pattern' => gettext('Enter MAC prefixes like 52:54:00, separated by commas.')]];
                }
            }
        }
        if (in_array($field, ['hostname', 'mdns_model', 'dhcp_vendor']) && $pattern !== '') {
            if (@preg_match('/' . str_replace('/', '\\/', $pattern) . '/u', '') === false) {
                return ['result' => 'failed', 'validations' => ['rule.pattern' => gettext('This is not a valid regular expression.')]];
            }
        }
        return null;
    }

    public function addRuleAction()
    {
        return $this->checkRule() ?? $this->addBase('rule', 'rules.rule');
    }

    public function setRuleAction($uuid)
    {
        return $this->checkRule() ?? $this->setBase('rule', 'rules.rule', $uuid);
    }

    public function delRuleAction($uuid)
    {
        return $this->delBase('rules.rule', $uuid);
    }

    public function searchDeviceAction()
    {
        return $this->searchBase('devices.device', ['mac', 'name', 'type', 'icon'], 'name');
    }

    public function getDeviceAction($uuid = null)
    {
        return $this->getBase('device', 'devices.device', $uuid);
    }

    public function addDeviceAction()
    {
        return $this->addBase('device', 'devices.device');
    }

    public function setDeviceAction($uuid)
    {
        return $this->setBase('device', 'devices.device', $uuid);
    }

    public function delDeviceAction($uuid)
    {
        return $this->delBase('devices.device', $uuid);
    }

    /**
     * Set (or clear, when name, type and icon are all empty) the name/type/icon for one MAC address,
     * as used by the edit button on the report pages. Takes effect without a service restart.
     */
    public function overrideAction()
    {
        $result = ['result' => 'failed'];
        if (!$this->request->isPost()) {
            return $result;
        }
        $mac = strtolower(trim((string)$this->request->getPost('mac')));
        $name = trim((string)$this->request->getPost('name'));
        $type = trim((string)$this->request->getPost('type'));
        $icon = trim((string)$this->request->getPost('icon'));
        if (!preg_match('/^([0-9a-f]{2}:){5}[0-9a-f]{2}$/', $mac)) {
            return ['result' => 'failed', 'message' => gettext('Invalid MAC address')];
        }
        Config::getInstance()->lock();
        $devices = $this->getModel()->devices->device;
        $found = null;
        foreach ($devices->iterateItems() as $uuid => $dev) {
            if (str_replace('-', ':', strtolower((string)$dev->mac)) === $mac) {
                $found = $uuid;
                break;
            }
        }
        if ($name === '' && $type === '' && $icon === '') {
            if ($found !== null) {
                $devices->del($found);
            }
            $result = $this->save(false, true);
        } else {
            $node = $found !== null ? $devices->$found : $devices->Add();
            $node->mac = $mac;
            $node->name = $name;
            $node->type = $type;
            $node->icon = $icon;
            $result = $this->validateAndSave($node, 'device');
        }
        if (($result['result'] ?? '') === 'saved') {
            (new Backend())->configdRun('template reload OPNsense/Loupe');
        }
        return $result;
    }
}
