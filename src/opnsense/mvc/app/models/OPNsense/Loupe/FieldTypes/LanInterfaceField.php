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


namespace OPNsense\Loupe\FieldTypes;

use OPNsense\Base\FieldTypes\InterfaceField;
use OPNsense\Core\Config;

/**
 * Interfaces devices can be watched on: InterfaceField's list without WAN and without bridge members, whose traffic
 * is seen on the bridge. The bridge itself stays, with or without an address.
 */
class LanInterfaceField extends InterfaceField
{
    protected function actionPostLoadingEvent()
    {
        parent::actionPostLoadingEvent();
        unset($this->internalOptionList['wan']);
        $config = Config::getInstance()->object();
        if (!empty($config->bridges)) {
            foreach ($config->bridges->children() as $bridge) {
                foreach (explode(',', (string)$bridge->members) as $member) {
                    unset($this->internalOptionList[$member]);
                }
            }
        }
    }
}
