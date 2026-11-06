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

use OPNsense\Base\BaseModel;
use OPNsense\Core\Backend;

class Loupe extends BaseModel
{
    /**
     * What only reports use (overrides, switches) has a template of its own: saving it needs no restart of loupd, and
     * doesn't publish other settings saved but not applied.
     */
    public function publishReportSettings()
    {
        (new Backend())->configdRun('template reload OPNsense/LoupeReports');
        @chmod('/usr/local/etc/loupe-reports.json', 0600);      // it holds the switches' communities
    }

    /**
     * Read the switches now ('all', or one's id) and keep what they say. os-eero's units and Wi-Fi clients go along,
     * when that plugin is there (configd answers an unknown action with text, which isn't JSON), in a file for
     * query.py, since they are too long for an argument.
     */
    public function refreshSwitches($target = 'all')
    {
        $backend = new Backend();
        $units = json_decode($backend->configdpRun('eero query', ['units']) ?? '', true);
        $clients = json_decode($backend->configdpRun('eero query', ['clients', 'all']) ?? '', true);
        $path = '-';
        if (is_array($units) && is_array($clients)) {
            $file = '/tmp/loupe-ai-' . bin2hex(random_bytes(8)) . '.json';
            $json = json_encode(['units' => $units, 'clients' => $clients], JSON_INVALID_UTF8_SUBSTITUTE);
            if ($json !== false && @file_put_contents($file, $json) !== false) {
                @chmod($file, 0600);
                $path = $file;
            }
        }
        $out = json_decode($backend->configdpRun('loupe query', ['switchrefresh', $path, $target]) ?? '', true);
        if ($path !== '-') {
            @unlink($path);
        }
        return is_array($out) ? $out : ['error' => gettext('No answer from Loupe.')];
    }
}
