<?php
/**
 * Astra Child Theme - 47th Legion
 */

// Load Legion rank system and Ultimate Member customizations
require_once get_stylesheet_directory() . '/functions-ranks.php';
require_once get_stylesheet_directory() . '/functions-um-fields.php';
require_once get_stylesheet_directory() . '/functions-legion-profile.php';

// Enqueue styles with proper priority
add_action("wp_enqueue_scripts", "legion_enqueue_styles", 20);
function legion_enqueue_styles() {
    wp_enqueue_style("astra-parent-style", get_template_directory_uri() . "/style.css");
    wp_enqueue_style("astra-child-style", get_stylesheet_uri(), array("astra-parent-style"), "2.0.3");
    wp_enqueue_style("legion-fonts", "https://fonts.googleapis.com/css2?family=Cinzel:wght@400;500;600;700&family=Crimson+Text:ital,wght@0,400;0,600;1,400&display=swap", array(), null);
    wp_enqueue_style("legion-custom", get_stylesheet_directory_uri() . "/legion-custom.css", array("astra-child-style"), "2.0.9");
    
    // Mobile menu fix script
    wp_enqueue_script("legion-mobile-menu-fix", get_stylesheet_directory_uri() . "/mobile-menu-fix.js", array(), "1.0.0", true);
}

// Add inline CSS for critical overrides - THIS LOADS LAST
add_action("wp_head", "legion_critical_css", 9999);
function legion_critical_css() {
    ?>
    <style id="legion-critical-css">
        /* Critical Dark Theme */
        body, html { background: #0a0a0f !important; }
        .ast-separate-container, .ast-plain-container, .site-content { background: #0a0a0f !important; }
        body, p, .entry-content { color: #e8e6e3 !important; }
        h1, h2, h3, h4, h5, h6, .entry-title { color: #c9a227 !important; font-family: 'Cinzel', serif !important; }
        a { color: #c9a227 !important; }
        .site-header, #masthead, .main-header-bar { background: #06060a !important; border-bottom: 1px solid #2a2a3a !important; }
        .site-footer { background: #06060a !important; border-top: 1px solid #2a2a3a !important; color: #888 !important; }
        
        /* HIDE SITE TITLE TEXT - CRITICAL */
        .site-title,
        .ast-site-identity .site-title,
        a.site-title-link,
        .site-branding .site-title,
        .site-branding > a:not(.custom-logo-link),
        .ast-site-identity > a:not(.custom-logo-link) {
            display: none !important;
            visibility: hidden !important;
            width: 0 !important;
            height: 0 !important;
            overflow: hidden !important;
        }
        
        /* Keep logo visible */
        .custom-logo-link,
        a.custom-logo-link,
        .custom-logo-link img,
        .custom-logo {
            display: inline-block !important;
            visibility: visible !important;
        }
        
        /* Logo size */
        .custom-logo {
            max-height: 70px !important;
            width: auto !important;
        }
        
        /* Nav items smaller */
        .main-header-menu > li > a,
        .ast-nav-menu > li > a {
            padding: 0.4rem 0.7rem !important;
            font-size: 0.72rem !important;
        }
        
        /* Make sure nav doesn't wrap */
        .main-header-menu,
        .ast-nav-menu {
            flex-wrap: nowrap !important;
        }
        
        /* Footer - single clean border only */
        .site-footer *, 
        .site-below-footer-wrap, 
        .site-below-footer-wrap *,
        .ast-builder-layout-element,
        .ast-footer-copyright,
        .ast-builder-footer-grid-columns,
        .site-footer-section,
        #colophon * {
            border: none !important;
            border-top: none !important;
            border-bottom: none !important;
        }
        .site-footer, footer.site-footer, #colophon {
            border-top: 1px solid #2a2a3a !important;
        }
        
        /* Mobile menu critical fixes */
        @media (max-width: 921px) {
            .ast-mobile-header-content {
                display: none;
                background: #0a0a0f !important;
            }
            body.ast-main-header-nav-open .ast-mobile-header-content {
                display: block !important;
                position: absolute !important;
                top: 100% !important;
                left: 0 !important;
                right: 0 !important;
                width: 100% !important;
                z-index: 9999 !important;
                background: #0a0a0f !important;
                border-bottom: 2px solid #c9a227 !important;
            }
            .ast-mobile-header-content .menu-link {
                display: block !important;
                padding: 15px 20px !important;
                color: #e8e6e3 !important;
                border-bottom: 1px solid #2a2a3a !important;
            }
            .ast-mobile-header-content .menu-link:hover {
                color: #c9a227 !important;
                background: rgba(201, 162, 39, 0.1) !important;
            }
        }
    </style>
    <?php
}

// Register custom page templates
add_filter("theme_page_templates", "legion_add_page_templates");
function legion_add_page_templates($templates) {
    $templates["page-roster.php"] = "47th Legion Roster";
    $templates["page-awards.php"] = "47th Legion Awards";
    $templates["page-ranks.php"] = "47th Legion Rank Structure";
    return $templates;
}

// Add body class
add_filter("body_class", "legion_body_class");
function legion_body_class($classes) {
    $classes[] = "legion-dark-theme";
    return $classes;
}

// Hide topic/reply counts from sub-forum list
add_filter('bbp_list_forums', 'legion_clean_subforum_list', 10, 2);
function legion_clean_subforum_list($output, $args) {
    // Remove the (X, Y) counts from sub-forum links
    $output = preg_replace('/\s*\(\d+,\s*\d+\)/', '', $output);
    return $output;
}

/**
 * Auto-sync Discord ID from miniOrange linked accounts to user meta
 * This runs on every login to keep discord_id synced
 */
add_action('wp_login', 'legion_sync_discord_id_on_login', 10, 2);
function legion_sync_discord_id_on_login($user_login, $user) {
    global $wpdb;
    
    // Check if user has a Discord link in miniOrange table
    $discord_id = $wpdb->get_var($wpdb->prepare(
        "SELECT identifier FROM {$wpdb->prefix}mo_openid_linked_user 
         WHERE user_id = %d AND linked_social_app = 'discord'",
        $user->ID
    ));
    
    if ($discord_id) {
        update_user_meta($user->ID, 'discord_id', $discord_id);
    }
}

/**
 * Also sync on user registration via miniOrange
 */
add_action('mo_user_register', 'legion_sync_discord_id_on_register', 10, 2);
function legion_sync_discord_id_on_register($user_id, $user_profile_url) {
    global $wpdb;
    
    $discord_id = $wpdb->get_var($wpdb->prepare(
        "SELECT identifier FROM {$wpdb->prefix}mo_openid_linked_user 
         WHERE user_id = %d AND linked_social_app = 'discord'",
        $user_id
    ));
    
    if ($discord_id) {
        update_user_meta($user_id, 'discord_id', $discord_id);
    }
}

/**
 * Add Discord linking notice to profile edit page
 */
add_action('um_after_profile_fields', 'legion_discord_link_notice', 10);
function legion_discord_link_notice() {
    // Only show on profile edit mode
    if (!um_is_on_edit_profile()) return;
    
    $user_id = um_profile_id();
    $discord_id = get_user_meta($user_id, 'discord_id', true);
    
    // Check if user has Discord linked
    global $wpdb;
    $has_discord_link = $wpdb->get_var($wpdb->prepare(
        "SELECT COUNT(*) FROM {$wpdb->prefix}mo_openid_linked_user 
         WHERE user_id = %d AND linked_social_app = 'discord'",
        $user_id
    ));
    
    if (!$has_discord_link) {
        ?>
        <div class="legion-discord-notice" style="
            background: linear-gradient(135deg, #5865F2 0%, #4752C4 100%);
            border-radius: 8px;
            padding: 15px 20px;
            margin: 20px 0;
            color: #fff;
            font-family: 'Cinzel', serif;
        ">
            <div style="display: flex; align-items: center; gap: 12px;">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z"/>
                </svg>
                <div>
                    <strong style="font-size: 1.1em;">Link Your Discord Account</strong>
                    <p style="margin: 5px 0 0 0; font-size: 0.9em; opacity: 0.9; font-family: sans-serif;">
                        Connect your Discord to automatically link your profile to the Legion roster.
                        <a href="<?php echo wp_login_url(um_user_profile_url()); ?>" style="color: #fff; text-decoration: underline;">
                            Click here to link Discord →
                        </a>
                    </p>
                </div>
            </div>
        </div>
        <?php
    } else if ($discord_id) {
        ?>
        <div class="legion-discord-linked" style="
            background: rgba(87, 242, 135, 0.15);
            border: 1px solid rgba(87, 242, 135, 0.3);
            border-radius: 8px;
            padding: 12px 16px;
            margin: 20px 0;
            color: #57F287;
            font-size: 0.9em;
        ">
            ✓ Discord linked — Your profile is connected to the Legion roster
        </div>
        <?php
    }
}

/**
 * Add Discord recommendation notice to login form
 */
add_action('um_before_form', 'legion_login_discord_notice', 10, 1);
function legion_login_discord_notice($args) {
    // Only show on login form
    if (!isset($args['mode']) || $args['mode'] !== 'login') return;
    ?>
    <div class="legion-login-discord-tip" style="
        background: linear-gradient(135deg, rgba(88, 101, 242, 0.15) 0%, rgba(71, 82, 196, 0.1) 100%);
        border: 1px solid rgba(88, 101, 242, 0.3);
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 20px;
        text-align: center;
    ">
        <p style="margin: 0; color: #e8e6e3; font-size: 0.9em;">
            <strong style="color: #5865F2;">Tip:</strong> Log in with Discord to automatically link your profile to the Legion roster!
        </p>
    </div>
    <?php
}

/**
 * Add Discord recommendation notice to registration form
 */
add_action('um_before_form', 'legion_register_discord_notice', 10, 1);
function legion_register_discord_notice($args) {
    // Only show on register form
    if (!isset($args['mode']) || $args['mode'] !== 'register') return;
    ?>
    <div class="legion-register-discord-tip" style="
        background: linear-gradient(135deg, rgba(88, 101, 242, 0.15) 0%, rgba(71, 82, 196, 0.1) 100%);
        border: 1px solid rgba(88, 101, 242, 0.3);
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 20px;
        text-align: center;
    ">
        <p style="margin: 0; color: #e8e6e3; font-size: 0.9em;">
            <strong style="color: #5865F2;">Recommended:</strong> Register with Discord to automatically connect your profile to the Legion roster!
        </p>
    </div>
    <?php
}
