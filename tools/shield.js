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

/*
 * Screenshot shield: blurs any text containing one of your words (names, your domain, your public IP) on
 * the page you are looking at, so a screenshot can be shared. Not part of the plugin: tools/shield.py wraps
 * it into a bookmarklet with your words, which never go into the repo. Click the bookmark again (or reload)
 * to undo. Keeps up with grids that redraw. Canvas charts can't be blurred.
 */
(function (words) {
    'use strict';
    if (window.loupeShield) {
        window.loupeShield.off();
        return;
    }
    const escape = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const parts = words.filter(w => w).map(escape);                       // anywhere in a text, any case
    if (!parts.length) return;
    const pattern = new RegExp(parts.join('|'), 'gi');                     // just the word: "Bob" of Bobs-iPhone
    const matches = s => { pattern.lastIndex = 0; return pattern.test(s); };
    const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'NOSCRIPT']);
    const BLUR = 'filter: blur(0.3em); user-select: none;';            // in em: a heading needs more than body text
    const TITLES = ['title', 'data-original-title'];

    function blurText(node) {
        const text = node.nodeValue;
        pattern.lastIndex = 0;
        if (!pattern.test(text)) return;
        const frag = document.createDocumentFragment();
        let last = 0;
        pattern.lastIndex = 0;
        for (let m; (m = pattern.exec(text));) {
            frag.appendChild(document.createTextNode(text.slice(last, m.index)));
            const span = document.createElement('span');
            span.className = 'loupe-shield';
            span.style.cssText = BLUR;
            span.textContent = m[0];
            frag.appendChild(span);
            last = m.index + m[0].length;
        }
        frag.appendChild(document.createTextNode(text.slice(last)));
        node.parentNode.replaceChild(frag, node);
    }

    function blurElement(el) {
        TITLES.forEach(function (attr) {                                     /* tooltips: masked, not blurred */
            const v = el.getAttribute(attr);
            if (v && matches(v)) {
                el.setAttribute('data-shield-' + attr, v);
                el.setAttribute(attr, v.replace(pattern, '•••'));
            }
        });
        if ((el.tagName === 'INPUT' || el.tagName === 'SELECT') && matches(el.value || '')) {
            el.setAttribute('data-shield-style', el.getAttribute('style') || '');
            el.style.filter = 'blur(0.3em)';
        }
    }

    function scan(root) {
        if (root.nodeType === Node.TEXT_NODE) {
            if (root.parentNode && !SKIP.has(root.parentNode.nodeName) && !root.parentNode.closest('.loupe-shield')) {
                blurText(root);
            }
            return;
        }
        if (root.nodeType !== Node.ELEMENT_NODE || SKIP.has(root.nodeName) || root.closest('.loupe-shield')) return;
        blurElement(root);
        root.querySelectorAll('*').forEach(blurElement);
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        const texts = [];
        for (let n; (n = walker.nextNode());) texts.push(n);
        texts.forEach(n => scan(n));
    }

    /* grids and tabs redraw: blur what they add, without reacting to our own changes */
    const observer = new MutationObserver(function (records) {
        observer.disconnect();
        records.forEach(function (r) {
            if (r.type === 'characterData') scan(r.target);
            else if (r.type === 'attributes') blurElement(r.target);
            else r.addedNodes.forEach(scan);
        });
        watch();
    });
    const watch = () => observer.observe(document.body, {childList: true, subtree: true, characterData: true,
        attributes: true, attributeFilter: TITLES});

    window.loupeShield = {
        off: function () {
            observer.disconnect();
            document.querySelectorAll('span.loupe-shield').forEach(function (s) {
                const parent = s.parentNode;
                parent.replaceChild(document.createTextNode(s.textContent), s);
                parent.normalize();
            });
            TITLES.forEach(function (attr) {
                document.querySelectorAll('[data-shield-' + attr + ']').forEach(function (el) {
                    el.setAttribute(attr, el.getAttribute('data-shield-' + attr));
                    el.removeAttribute('data-shield-' + attr);
                });
            });
            document.querySelectorAll('[data-shield-style]').forEach(function (el) {
                el.setAttribute('style', el.getAttribute('data-shield-style'));
                el.removeAttribute('data-shield-style');
            });
            delete window.loupeShield;
        }
    };
    scan(document.body);
    watch();
})(window.LOUPE_SHIELD_WORDS || []);
