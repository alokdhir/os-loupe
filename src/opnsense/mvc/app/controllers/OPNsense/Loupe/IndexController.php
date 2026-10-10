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
     * One page with tabs (like Reporting: Unbound DNS): Devices, Services, Settings (General, Service names,
     * Device names). The old addresses open the matching tab.
     */
    private function page($activetab)
    {
        $this->view->activetab = $activetab;
        $this->view->formSettings = $this->getForm('settings');
        $this->view->formDialogService = $this->getForm('dialogService');
        $this->view->formDialogDevice = $this->getForm('dialogDevice');
        $this->view->formGridServices = $this->getFormGrid('dialogService', 'grid-servicenames');
        $this->view->formGridOverrides = $this->getFormGrid('dialogDevice', 'grid-devicenames');
        $this->view->pick('OPNsense/Loupe/index');
    }

    public function indexAction()
    {
        $this->page('devices');
    }

    public function lookupAction()
    {
        $this->page('services');
    }

    public function settingsAction()
    {
        $this->page('general');
    }
}
