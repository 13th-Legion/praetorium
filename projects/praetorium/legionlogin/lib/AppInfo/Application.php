<?php

declare(strict_types=1);

/**
 * SPDX-FileCopyrightText: 2026 13th Legion IT (Spooky)
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */

namespace OCA\LegionLogin\AppInfo;

use OCP\AppFramework\App;
use OCP\AppFramework\Bootstrap\IBootContext;
use OCP\AppFramework\Bootstrap\IBootstrap;
use OCP\AppFramework\Bootstrap\IRegistrationContext;
use OCP\Util;

class Application extends App implements IBootstrap {
    public const APP_ID = 'legionlogin';

    public function __construct(array $params = []) {
        parent::__construct(self::APP_ID, $params);
    }

    public function register(IRegistrationContext $context): void {
        // No services to register.
    }

    public function boot(IBootContext $context): void {
        // boot() runs on every request, including the unauthenticated
        // login / OAuth2 authorize (grant) page. We inject the single-shot
        // button guard ONLY on those pages so it isn't loaded across the
        // whole authenticated app. This mirrors how announcementbanner
        // reliably injects onto /login on NC 33 (addScript inside boot()).
        $server = $context->getServerContainer();
        try {
            /** @var \OCP\IRequest $request */
            $request = $server->get(\OCP\IRequest::class);
            $path = $request->getRawPathInfo();
        } catch (\Throwable $e) {
            // If we can't resolve the request, do nothing (never break boot).
            return;
        }

        // Match the login page and the OAuth2 grant/authorize endpoint.
        // getRawPathInfo() returns the path WITHOUT the webroot, e.g.
        // "/login", "/apps/oauth2/authorize", "/index.php/apps/oauth2/authorize".
        if (
            strpos($path, '/login') !== false ||
            strpos($path, '/apps/oauth2/authorize') !== false ||
            strpos($path, '/index.php/login') !== false
        ) {
            // Loads js/login-guard.js (classic addScript convention:
            // NO appid prefix on the filename).
            Util::addScript(self::APP_ID, 'login-guard');
        }
    }
}
