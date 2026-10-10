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

    public function searchServiceAction()
    {
        return $this->searchBase('services.service', ['suffix', 'name'], 'suffix');
    }

    public function getServiceAction($uuid = null)
    {
        return $this->getBase('service', 'services.service', $uuid);
    }

    public function addServiceAction()
    {
        return $this->addBase('service', 'services.service');
    }

    public function setServiceAction($uuid)
    {
        return $this->setBase('service', 'services.service', $uuid);
    }

    public function delServiceAction($uuid)
    {
        return $this->delBase('services.service', $uuid);
    }

    public function searchDeviceAction()
    {
        return $this->searchBase('devices.device', ['mac', 'name', 'type'], 'name');
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
     * Set (or clear, when name and type are both empty) the name/type for one MAC address,
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
        if ($name === '' && $type === '') {
            if ($found !== null) {
                $devices->del($found);
            }
            $result = $this->save(false, true);
        } else {
            $node = $found !== null ? $devices->$found : $devices->Add();
            $node->mac = $mac;
            $node->name = $name;
            $node->type = $type;
            $result = $this->validateAndSave($node, 'device');
        }
        if (($result['result'] ?? '') === 'saved') {
            (new Backend())->configdRun('template reload OPNsense/Loupe');
        }
        return $result;
    }
}
