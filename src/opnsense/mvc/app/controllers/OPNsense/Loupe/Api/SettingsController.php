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

    /* a host name: labels of letters, digits, _ and -, up to 253 characters, optional final dot; it may start
       with a label prefix ("cdn-*.example.com": names under example.com whose next label starts with cdn-) */
    private const LABEL = '[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?';
    private const DOMAIN = '/^(?=.{1,253}$)([a-z0-9_-]{1,62}\*\.)?(' . self::LABEL . '\.)*' . self::LABEL . '\.?$/i';

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
            if (
                filter_var($ip, FILTER_VALIDATE_IP) === false
                || ($bits !== null && !(ctype_digit($bits) && (int)$bits <= ($v6 ? 128 : 32)))
            ) {
                $error = gettext('Enter a valid IP address or range, e.g. 192.0.2.10 or 203.0.113.0/24.');
            }
        } elseif ($kind === 'port') {
            if (!preg_match('/^(tcp|udp)\/([0-9]{1,5})$/i', $text, $m) || (int)$m[2] < 1 || (int)$m[2] > 65535) {
                $error = gettext('Enter a protocol and port, e.g. tcp/8443 or udp/51820.');
            }
        } elseif (!preg_match(self::DOMAIN, $text)) {
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

    /* a rule's clues, strongest first (the order loupd tries them in, lib/devid.py TIERS) */
    private const RULE_FIELDS = [
        'mac_prefix' => 'MAC prefix', 'mdns_model' => 'Bonjour model', 'mdns_name' => 'Bonjour name',
        'hostname' => 'Host name', 'category' => 'Category code', 'dhcp_vendor' => 'DHCP vendor class',
        'talks_to' => 'Talks to', 'mdns_service' => 'Bonjour service',
        'vendor' => 'MAC vendor', 'private_mac' => 'Private MAC',
    ];

    public function searchRuleAction()
    {
        $fields = array_merge(array_keys(self::RULE_FIELDS), ['type', 'description', 'source']);
        /* the grid's Source filter: manual or AI rules only */
        $source = (string)$this->request->getPost('source', null, '');
        $only = in_array($source, ['manual', 'ai'], true) ? fn($rule) => (string)$rule->source === $source : null;
        $result = $this->searchBase('rules.rule', $fields, 'type', $only);
        foreach ($result['rows'] as &$row) {
            $parts = [];
            foreach (self::RULE_FIELDS as $field => $label) {
                $want = (string)($row[$field] ?? '');
                if ($field === 'private_mac' && $want !== '') {
                    $parts[] = $want === '1' ? gettext($label) : sprintf(gettext('not %s'), gettext($label));
                } elseif ($want !== '') {
                    $parts[] = gettext($label) . ' ' . $want;
                }
            }
            $row['match'] = implode(' ' . gettext('and') . ' ', $parts);
        }
        /* the icon shown: the one picked, else (Automatic) the built-in type's of the same name, as loupd uses */
        $loose = fn($t) => preg_replace('/[^a-z0-9]/', '', strtolower($t));
        $data = json_decode((string)@file_get_contents('/usr/local/opnsense/scripts/loupe/data/devices.json'), true);
        $builtin = $words = [];
        foreach (($data['types'] ?? []) as $type => $props) {
            if (!empty($props['icon'])) {
                $builtin[$loose($type)] = $props['icon'];
                /* a type's last word names an icon too (lib/devid.py Rules.word_icons), not a generic type's */
                $parts = preg_split('/\s+/', trim($type));
                $word = $loose(end($parts));
                if (empty($props['generic']) && !in_array($word, ['device', 'appliance', 'module', 'accessory']) && !isset($words[$word])) {
                    $words[$word] = $props['icon'];
                }
            }
        }
        foreach (($data['icon_words'] ?? []) as $word => $icon) {
            $words[$loose($word)] = $icon;
        }
        $byWord = function ($type) use ($words, $loose) {
            foreach (array_reverse(preg_split('/\s+/', trim($type))) as $w) {
                if (isset($words[$loose($w)])) {
                    return $words[$loose($w)];
                }
            }
            return '';
        };
        $picked = [];
        foreach ($this->getModel()->rules->rule->iterateItems() as $uuid => $rule) {
            $picked[$uuid] = (string)$rule->icon;
        }
        foreach ($result['rows'] as &$row) {
            $type = html_entity_decode($row['type']);
            $row['icon_class'] = ($picked[$row['uuid']] ?? '') ?: (($builtin[$loose($type)] ?? '') ?: $byWord($type));
        }
        return $result;
    }

    public function getRuleAction($uuid = null)
    {
        return $this->getBase('rule', 'rules.rule', $uuid);
    }

    /**
     * A rule needs at least one clue. Host name, Bonjour model and DHCP vendor patterns are regular expressions:
     * refuse ones that don't compile here, where the dialog can show it, instead of loupd skipping them later.
     */
    private function checkRule(?array $rule = null)
    {
        $rule = $rule ?? $this->request->getPost('rule');
        $rule = is_array($rule) ? $rule : [];
        $clue = fn($field) => trim((string)($rule[$field] ?? ''));
        if (count(array_filter(array_keys(self::RULE_FIELDS), fn($f) => $clue($f) !== '')) === 0) {
            return $this->ruleError('rule.vendor', gettext('Fill in at least one clue.'));
        }
        foreach ($clue('mac_prefix') === '' ? [] : explode(',', $clue('mac_prefix')) as $prefix) {
            if (!preg_match('/^[0-9a-f]{2}([:.-]?[0-9a-f]{2}){0,5}$/i', trim($prefix))) {
                $message = gettext('Enter MAC prefixes like 52:54:00, separated by commas.');
                return $this->ruleError('rule.mac_prefix', $message);
            }
        }
        foreach ($clue('category') === '' ? [] : explode(',', $clue('category')) as $code) {
            if (!preg_match('/^(hap:\d+|matter:(0x[0-9a-f]+|\d+))$/i', trim($code))) {
                $message = gettext('Enter codes like hap:9 or matter:0x0301, separated by commas.');
                return $this->ruleError('rule.category', $message);
            }
        }
        foreach (['hostname', 'mdns_model', 'mdns_name', 'dhcp_vendor'] as $field) {
            $pattern = $clue($field);
            if ($pattern !== '' && @preg_match('/' . str_replace('/', '\\/', $pattern) . '/u', '') === false) {
                return $this->ruleError("rule.{$field}", gettext('This is not a valid regular expression.'));
            }
        }
        return null;
    }

    private function ruleError(string $field, string $message)
    {
        return ['result' => 'failed', 'validations' => [$field => $message]];
    }

    /* ---- AI Rule Generator: the table and prompt, the reply checked, the rules picked saved (query.py, lib/airules.py) */

    private function aiQuery(array $args)
    {
        $out = json_decode((new Backend())->configdpRun('loupe query', $args) ?? '', true);
        return is_array($out) ? $out : ['error' => gettext('No answer from Loupe.')];
    }

    /* Home Assistant names: when loupd last fetched them, how many devices, the last error */
    public function hassStatusAction()
    {
        return $this->aiQuery(['hass']);
    }

    /* the devices to ask about: the ones Loupe couldn't identify, or (scope=all) every device */
    public function aiTableAction()
    {
        return $this->aiQuery(['aitable', $this->request->getPost('scope', null, '') === 'all' ? 'all' : '']);
    }

    /* the AI's reply, checked against every device: left in a file for query.py (too long for an argument), which removes it */
    public function aiPreviewAction()
    {
        $reply = (string)$this->request->getPost('reply', null, '');
        if (trim($reply) === '' || strlen($reply) > 262144) {
            return ['error' => gettext('Paste the AI\'s answer first.')];
        }
        $path = '/tmp/loupe-ai-' . bin2hex(random_bytes(8)) . '.json';
        $json = json_encode(['reply' => $reply], JSON_INVALID_UTF8_SUBSTITUTE);
        if ($json === false || @file_put_contents($path, $json) === false) {
            return ['error' => gettext('Could not keep the answer for checking.')];
        }
        @chmod($path, 0600);
        $out = $this->aiQuery(['aipreview', $path]);
        @unlink($path);
        return $out;
    }

    /* Model names' AI prompt: the codes devices announce that have no name yet (maker and type only) */
    public function aiModelsAction()
    {
        return $this->aiQuery(['aimodels']);
    }

    /* the AI's names for those codes, checked against what devices announce (via a file, as ai_preview) */
    public function aiModelsPreviewAction()
    {
        $reply = (string)$this->request->getPost('reply', null, '');
        if (trim($reply) === '' || strlen($reply) > 262144) {
            return ['error' => gettext('Paste the AI\'s answer first.')];
        }
        $path = '/tmp/loupe-ai-' . bin2hex(random_bytes(8)) . '.json';
        $json = json_encode(['reply' => $reply], JSON_INVALID_UTF8_SUBSTITUTE);
        if ($json === false || @file_put_contents($path, $json) === false) {
            return ['error' => gettext('Could not keep the answer for checking.')];
        }
        @chmod($path, 0600);
        $out = $this->aiQuery(['aimodelpreview', $path]);
        @unlink($path);
        return $out;
    }

    /* the rules picked, saved as the user's own with source AI; validated as the rule dialog does */
    public function aiSaveAction()
    {
        $rules = $this->request->getPost('rules');
        $rules = is_array($rules) ? $rules : [];
        $models = $this->request->getPost('models');
        $models = is_array($models) ? $models : [];
        if (!$this->request->isPost() || (empty($rules) && empty($models))) {
            return ['result' => 'failed', 'error' => gettext('Nothing to save.')];
        }
        $allowed = array_merge(array_keys(self::RULE_FIELDS), ['type', 'description', 'icon', 'model']);
        Config::getInstance()->lock();
        $mdl = $this->getModel();
        $saved = 0;
        foreach ($rules as $i => $rule) {
            $rule = is_array($rule) ? array_intersect_key($rule, array_flip($allowed)) : [];
            $rule['description'] = mb_substr(trim((string)($rule['description'] ?? '')), 0, 255);
            $rule['model'] = mb_substr(trim((string)($rule['model'] ?? '')), 0, 64);
            $bad = $this->checkRule($rule);
            if ($bad !== null || trim((string)($rule['type'] ?? '')) === '') {
                return ['result' => 'failed', 'error' => sprintf(gettext('Rule %d: %s'), $i + 1,
                    $bad ? implode(' ', $bad['validations']) : gettext('it has no type.'))];
            }
            $node = $mdl->rules->rule->Add();
            $node->setNodes($rule + ['source' => 'ai']);
            $saved++;
        }
        /* model names: an identifier already named gets the new name */
        $named = 0;
        foreach ($models as $m) {
            $identifier = trim((string)($m['identifier'] ?? ''));
            $name = mb_substr(trim((string)($m['name'] ?? '')), 0, 100);
            if ($identifier === '' || $name === '' || strlen($identifier) > 64) {
                continue;
            }
            $node = null;
            foreach ($mdl->models->model->iterateItems() as $existing) {
                if ((string)$existing->identifier === $identifier) {
                    $node = $existing;
                }
            }
            $node = $node ?? $mdl->models->model->Add();
            $node->setNodes(['identifier' => $identifier, 'name' => $name]);
            $named++;
        }
        $messages = $mdl->performValidation();
        if (count($messages)) {
            foreach ($messages as $msg) {
                return ['result' => 'failed', 'error' => $msg->getField() . ': ' . $msg->getMessage()];
            }
        }
        $this->save(false, true);
        return ['result' => 'saved', 'saved' => $saved, 'named' => $named];
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
        $result = $this->searchBase('devices.device', ['mac', 'name', 'type', 'icon'], 'name');
        /* the Type column shows the icon: the one picked here, else one picked for the same type, else what the
           reports show (saved picks count before Apply; a pick applies to every device of that type) */
        $loose = function ($type) {
            return preg_replace('/[^a-z0-9]/', '', strtolower($type));
        };
        $picked = $byType = [];
        foreach ($this->getModel()->devices->device->iterateItems() as $uuid => $dev) {
            $picked[$uuid] = (string)$dev->icon;
            if ((string)$dev->icon !== '' && (string)$dev->type !== '' && !isset($byType[$loose((string)$dev->type)])) {
                $byType[$loose((string)$dev->type)] = (string)$dev->icon;
            }
        }
        $auto = json_decode((new Backend())->configdpRun('loupe query', ['icons']) ?? '', true);
        foreach ($result['rows'] as &$row) {
            $mac = str_replace('-', ':', strtolower($row['mac']));
            $type = $loose(html_entity_decode($row['type']));
            $sameType = $type !== '' ? ($byType[$type] ?? '') : '';
            $row['icon_class'] = ($picked[$row['uuid']] ?? '') ?: ($sameType ?: ($auto[$mac] ?? ''));
        }
        return $result;
    }

    /* names for model identifiers devices announce (Device rules > Model names) */
    /* Switches: saved at once, with nothing to Apply, and a saved switch is read right away so the tab has it */
    private function publishSettings()
    {
        /* as Apply: loupe.json carries every saved setting, so loupd reads it again too; otherwise the reports would
           use settings saved but not applied (a new service name) that loupd doesn't */
        $backend = new Backend();
        $backend->configdRun('template reload OPNsense/Loupe');
        if ((string)$this->getModel()->general->enabled === '1') {
            $backend->configdRun('loupe restart');
        }
    }

    public function searchSwitchAction()
    {
        $result = $this->searchBase('switches.switch', ['enabled', 'name', 'address', 'community'], 'address');
        $data = $this->aiQuery(['switchmap']);
        $map = [];
        foreach ($data['switches'] ?? [] as $s) {
            $map[$s['id']] = $s;
        }
        foreach ($result['rows'] as &$row) {
            $s = $map[$row['uuid']] ?? [];
            $row['shown_name'] = $data['names'][$row['uuid']] ?? $row['address'];
            $row['error'] = $s['error'] ?? '';
            $row['note'] = $s['note'] ?? '';
            $row['fetched'] = $s['fetched'] ?? 0;
            $row['ports'] = ($s['total'] ?? 0) ?: '';
            $row['up'] = $s['up'] ?? 0;
            $row['devices'] = $s['devices'] ?? 0;
        }
        return $result;
    }

    public function getSwitchAction($uuid = null)
    {
        return $this->getBase('switch', 'switches.switch', $uuid);
    }

    private function switchSaved(array $result, $uuid)
    {
        if (in_array($result['result'] ?? '', ['saved', 'deleted'], true)) {
            $this->publishSettings();
            if ($uuid !== null) {
                $this->getModel()->refreshSwitches($uuid);
            }
        }
        return $result;
    }

    public function addSwitchAction()
    {
        $result = $this->addBase('switch', 'switches.switch');
        return $this->switchSaved($result, $result['uuid'] ?? null);
    }

    public function setSwitchAction($uuid)
    {
        return $this->switchSaved($this->setBase('switch', 'switches.switch', $uuid), $uuid);
    }

    public function delSwitchAction($uuid)
    {
        return $this->switchSaved($this->delBase('switches.switch', $uuid), null);
    }

    public function searchModelNameAction()
    {
        return $this->searchBase('models.model', ['identifier', 'name'], 'identifier');
    }

    public function getModelNameAction($uuid = null)
    {
        return $this->getBase('model', 'models.model', $uuid);
    }

    public function addModelNameAction()
    {
        return $this->addBase('model', 'models.model');
    }

    public function setModelNameAction($uuid)
    {
        return $this->setBase('model', 'models.model', $uuid);
    }

    public function delModelNameAction($uuid)
    {
        return $this->delBase('models.model', $uuid);
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
            $this->publishSettings();
        }
        return $result;
    }
}
