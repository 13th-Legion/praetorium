<?php
/**
 * Astra Child Theme - 47th Legion
 */

// Load Legion rank system and Ultimate Member customizations
require_once get_stylesheet_directory() . '/functions-ranks.php';
require_once get_stylesheet_directory() . '/functions-um-fields.php';

// Enqueue styles with proper priority
add_action("wp_enqueue_scripts", "legion_enqueue_styles", 20);
function legion_enqueue_styles() {
    wp_enqueue_style("astra-parent-style", get_template_directory_uri() . "/style.css");
    wp_enqueue_style("astra-child-style", get_stylesheet_uri(), array("astra-parent-style"), "2.0.0");
    wp_enqueue_style("legion-fonts", "https://fonts.googleapis.com/css2?family=Cinzel:wght@400;500;600;700&family=Crimson+Text:ital,wght@0,400;0,600;1,400&display=swap", array(), null);
    wp_enqueue_style("legion-custom", get_stylesheet_directory_uri() . "/legion-custom.css", array("astra-child-style"), "2.0.1");
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
