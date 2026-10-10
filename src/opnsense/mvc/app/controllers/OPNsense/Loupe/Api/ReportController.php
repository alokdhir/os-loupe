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
        if (!preg_match('/^[0-9a-fA-F:.]{2,45}$/', $id)) {
            return ['error' => 'invalid device'];
        }
        return $this->query(['device', $id, $this->hours($hours)]);
    }

    public function lookupAction($text = '', $hours = 24)
    {
        if (!preg_match('/^[a-zA-Z0-9.:_-]{1,253}$/', $text)) {
            return ['error' => 'enter a name or address'];
        }
        return $this->query(['lookup', $text, $this->hours($hours)]);
    }

    public function widgetAction()
    {
        return $this->query(['widget']);
    }
}
