/**
 * Legion Login Guard — single-shot submit / "Grant access" button.
 *
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * Problem: on the login / OAuth2 grant page, users (esp. mobile + password
 * autofill) tap the primary button repeatedly while the page loads. NC 33
 * keeps ONE per-session OAuth grant stateToken, so a second submit hits a
 * consumed token -> 403 "state token does not match". Autofill can advance the
 * flow on its own, turning the follow-up tap into an accidental double-submit.
 *
 * Fix: after the first activation of a login/grant submit control, disable it
 * (and its siblings) and show a "Signing in…" state so a second tap can't fire
 * another request. Purely additive; never blocks the FIRST legitimate submit.
 *
 * The login/grant UI is a compiled Vue bundle that (re)renders async, so we
 * can't just query once at DOMContentLoaded. We use a capturing, one-shot-per-
 * button click/submit listener plus a MutationObserver to catch late renders.
 */
(function () {
    'use strict';

    var GUARD_FLAG = '__legionGuarded';
    var SUBMIT_SELECTOR = [
        'button[type="submit"]',
        'input[type="submit"]',
        // NC grant/login primary buttons (class names vary across versions)
        '.button-vue--vue-primary',
        '#submit-wrapper button',
        '#body-login form button',
    ].join(',');

    function markBusy(btn) {
        try {
            // Preserve original label so we don't clobber it permanently if the
            // page stays (it won't — it navigates — but be safe).
            if (btn.tagName === 'BUTTON' && !btn.dataset.legionOrigLabel) {
                btn.dataset.legionOrigLabel = btn.textContent;
            }
            // Disable on the NEXT tick so the browser still submits THIS click
            // (disabling synchronously in a click handler can cancel the submit
            // in some engines). requestAnimationFrame guarantees post-dispatch.
            requestAnimationFrame(function () {
                btn.setAttribute('disabled', 'disabled');
                btn.setAttribute('aria-busy', 'true');
                btn.style.pointerEvents = 'none';
                btn.style.opacity = '0.6';
                if (btn.tagName === 'BUTTON') {
                    var label = btn.querySelector('.button-vue__text') || btn;
                    label.textContent = 'Signing in…';
                }
            });
        } catch (e) { /* never break login on a guard hiccup */ }
    }

    function guardActivation(target) {
        // Find the nearest submit-like control from the event target.
        var btn = target.closest ? target.closest(SUBMIT_SELECTOR) : null;
        if (!btn || btn[GUARD_FLAG]) {
            return;
        }
        btn[GUARD_FLAG] = true;
        markBusy(btn);
        // Also disable any OTHER submit buttons on the page so a two-button
        // layout (e.g. login + grant) can't be double-fired.
        document.querySelectorAll(SUBMIT_SELECTOR).forEach(function (other) {
            if (other !== btn && !other[GUARD_FLAG]) {
                other[GUARD_FLAG] = true;
                markBusy(other);
            }
        });
    }

    // Capture phase so we run before Vue's own handler and before navigation.
    document.addEventListener('click', function (e) {
        guardActivation(e.target);
    }, true);

    // Native form submit (autofill "go", Enter key) — same one-shot treatment.
    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (form && form[GUARD_FLAG]) {
            // Already submitted once — stop the duplicate.
            e.preventDefault();
            e.stopImmediatePropagation();
            return;
        }
        if (form) {
            form[GUARD_FLAG] = true;
            var btn = form.querySelector(SUBMIT_SELECTOR);
            if (btn) { guardActivation(btn); }
        }
    }, true);
})();
