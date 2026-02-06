<?php
/**
 * 47th Legion Rank System for Ultimate Member
 * 
 * Registers WordPress roles for each military rank and configures
 * Ultimate Member integration.
 */

// Prevent direct access
if (!defined('ABSPATH')) exit;

/**
 * 47th Legion Rank Definitions
 * Based on official rank structure document (2026-01-30)
 */
function legion_get_ranks() {
    return array(
        // Officers (O-1 to O-8)
        'legion_o8' => array(
            'label' => 'O-8 Imperator',
            'name' => 'Imperator',
            'grade' => 'O-8',
            'category' => 'Officers',
            'priority' => 8,
        ),
        'legion_o7' => array(
            'label' => 'O-7 Legate',
            'name' => 'Legate',
            'grade' => 'O-7',
            'category' => 'Officers',
            'priority' => 7,
        ),
        'legion_o6' => array(
            'label' => 'O-6 Prefect',
            'name' => 'Prefect',
            'grade' => 'O-6',
            'category' => 'Officers',
            'priority' => 6,
        ),
        'legion_o5' => array(
            'label' => 'O-5 Tribune',
            'name' => 'Tribune',
            'grade' => 'O-5',
            'category' => 'Officers',
            'priority' => 5,
        ),
        'legion_o4' => array(
            'label' => 'O-4 Centurion',
            'name' => 'Centurion',
            'grade' => 'O-4',
            'category' => 'Officers',
            'priority' => 4,
        ),
        'legion_o3' => array(
            'label' => 'O-3 Optio',
            'name' => 'Optio',
            'grade' => 'O-3',
            'category' => 'Officers',
            'priority' => 3,
        ),
        'legion_o2' => array(
            'label' => 'O-2 Tesserarian',
            'name' => 'Tesserarian',
            'grade' => 'O-2',
            'category' => 'Officers',
            'priority' => 2,
        ),
        'legion_o1' => array(
            'label' => 'O-1 Vexillarian',
            'name' => 'Vexillarian',
            'grade' => 'O-1',
            'category' => 'Officers',
            'priority' => 1,
        ),
        // Enlisted (E-1 to E-8)
        'legion_e8' => array(
            'label' => 'E-8 Signifier',
            'name' => 'Signifier',
            'grade' => 'E-8',
            'category' => 'Enlisted',
            'priority' => -1,
        ),
        'legion_e7' => array(
            'label' => 'E-7 Triplicarian',
            'name' => 'Triplicarian',
            'grade' => 'E-7',
            'category' => 'Enlisted',
            'priority' => -2,
        ),
        'legion_e6' => array(
            'label' => 'E-6 Duplicarian',
            'name' => 'Duplicarian',
            'grade' => 'E-6',
            'category' => 'Enlisted',
            'priority' => -3,
        ),
        'legion_e5' => array(
            'label' => 'E-5 Carian',
            'name' => 'Carian',
            'grade' => 'E-5',
            'category' => 'Enlisted',
            'priority' => -4,
        ),
        'legion_e4' => array(
            'label' => 'E-4 Decanus',
            'name' => 'Decanus',
            'grade' => 'E-4',
            'category' => 'Enlisted',
            'priority' => -5,
        ),
        'legion_e3' => array(
            'label' => 'E-3 Miles Gregarius',
            'name' => 'Miles Gregarius',
            'grade' => 'E-3',
            'category' => 'Enlisted',
            'priority' => -6,
        ),
        'legion_e2' => array(
            'label' => 'E-2 Miles',
            'name' => 'Miles',
            'grade' => 'E-2',
            'category' => 'Enlisted',
            'priority' => -7,
        ),
        'legion_e1' => array(
            'label' => 'E-1 Tiron',
            'name' => 'Tiron',
            'grade' => 'E-1',
            'category' => 'Enlisted',
            'priority' => -8,
        ),
        // Reserve
        'legion_res' => array(
            'label' => 'Veteranus (Reserve)',
            'name' => 'Veteranus',
            'grade' => 'RES',
            'category' => 'Reserves',
            'priority' => -99,
        ),
    );
}

/**
 * Register all Legion rank roles on theme activation
 */
function legion_register_rank_roles() {
    $ranks = legion_get_ranks();
    
    // Base capabilities for all legion members
    $base_caps = array(
        'read' => true,
        'edit_posts' => false,
        'delete_posts' => false,
    );
    
    // Officer capabilities (can moderate forums)
    $officer_caps = array_merge($base_caps, array(
        'moderate' => true,
        'edit_others_posts' => false,
    ));
    
    // Senior officer capabilities 
    $senior_caps = array_merge($officer_caps, array(
        'edit_others_posts' => true,
        'manage_categories' => true,
    ));
    
    foreach ($ranks as $role_slug => $rank) {
        // Determine capabilities based on rank category and grade
        if ($rank['category'] === 'Officers') {
            $priority = $rank['priority'];
            if ($priority >= 5) {
                $caps = $senior_caps;
            } else {
                $caps = $officer_caps;
            }
        } else {
            $caps = $base_caps;
        }
        
        // Remove existing role if it exists (for updates)
        remove_role($role_slug);
        
        // Add the role
        add_role($role_slug, $rank['label'], $caps);
    }
}
add_action('after_switch_theme', 'legion_register_rank_roles');

/**
 * Also register roles on init if they don't exist (in case theme was already active)
 */
function legion_ensure_roles_exist() {
    $ranks = legion_get_ranks();
    foreach ($ranks as $role_slug => $rank) {
        if (!get_role($role_slug)) {
            legion_register_rank_roles();
            break;
        }
    }
}
add_action('init', 'legion_ensure_roles_exist');

/**
 * Add Legion ranks to Ultimate Member role dropdown
 */
function legion_um_roles($roles) {
    $ranks = legion_get_ranks();
    foreach ($ranks as $role_slug => $rank) {
        $roles[$role_slug] = $rank['label'];
    }
    return $roles;
}
add_filter('um_roles', 'legion_um_roles');

/**
 * Set default role for new registrations to E-1 Tiron
 */
function legion_um_default_role($role) {
    return 'legion_e1';
}
add_filter('um_registration_default_role', 'legion_um_default_role');

/**
 * Display rank insignia next to username
 */
function legion_get_rank_insignia($user_id = null) {
    if (!$user_id) {
        $user_id = get_current_user_id();
    }
    
    $user = get_userdata($user_id);
    if (!$user) return '';
    
    $ranks = legion_get_ranks();
    $user_roles = $user->roles;
    
    foreach ($ranks as $role_slug => $rank) {
        if (in_array($role_slug, $user_roles)) {
            $grade = strtolower(str_replace('-', '', $rank['grade']));
            $img_url = get_stylesheet_directory_uri() . '/assets/ranks/' . $grade . '.png';
            return sprintf(
                '<img src="%s" alt="%s" title="%s" class="legion-rank-insignia" style="height:20px;vertical-align:middle;margin-right:5px;">',
                esc_url($img_url),
                esc_attr($rank['grade']),
                esc_attr($rank['label'])
            );
        }
    }
    
    return '';
}

/**
 * Get user's rank info
 */
function legion_get_user_rank($user_id = null) {
    if (!$user_id) {
        $user_id = get_current_user_id();
    }
    
    $user = get_userdata($user_id);
    if (!$user) return null;
    
    $ranks = legion_get_ranks();
    $user_roles = $user->roles;
    
    foreach ($ranks as $role_slug => $rank) {
        if (in_array($role_slug, $user_roles)) {
            return array_merge($rank, array('role' => $role_slug));
        }
    }
    
    return null;
}

/**
 * Display rank in Ultimate Member profile header (below the display name)
 */
function legion_um_profile_rank($args) {
    $user_id = um_profile_id();
    $rank = legion_get_user_rank($user_id);
    
    if ($rank) {
        $insignia = legion_get_rank_insignia($user_id);
        echo '<div class="legion-profile-header-rank">';
        echo $insignia;
        echo '<span class="rank-name">' . esc_html($rank['name']) . '</span>';
        echo '<span class="rank-grade">(' . esc_html($rank['grade']) . ')</span>';
        echo '</div>';
    }
}
add_action('um_after_profile_header_name_args', 'legion_um_profile_rank', 5);

/**
 * Add rank to bbPress forum posts
 */
function legion_bbp_author_rank($author_links, $args) {
    if (!isset($args['post_id'])) return $author_links;
    
    $user_id = bbp_get_reply_author_id($args['post_id']);
    if (!$user_id) {
        $user_id = bbp_get_topic_author_id($args['post_id']);
    }
    
    if ($user_id) {
        $insignia = legion_get_rank_insignia($user_id);
        if ($insignia) {
            $author_links = $insignia . $author_links;
        }
    }
    
    return $author_links;
}
add_filter('bbp_get_author_link', 'legion_bbp_author_rank', 10, 2);

/**
 * Admin notice for rank role registration
 */
function legion_admin_notice_roles() {
    if (!current_user_can('manage_options')) return;
    
    $ranks = legion_get_ranks();
    $missing = array();
    
    foreach ($ranks as $role_slug => $rank) {
        if (!get_role($role_slug)) {
            $missing[] = $rank['label'];
        }
    }
    
    if (!empty($missing)) {
        echo '<div class="notice notice-warning"><p>';
        echo '<strong>47th Legion:</strong> Some rank roles are missing. ';
        echo '<a href="' . admin_url('themes.php') . '">Re-activate the theme</a> to register them.';
        echo '</p></div>';
    }
}
add_action('admin_notices', 'legion_admin_notice_roles');

/**
 * CLI command to manually register roles (for wp-cli)
 */
if (defined('WP_CLI') && WP_CLI) {
    WP_CLI::add_command('legion register-roles', function() {
        legion_register_rank_roles();
        WP_CLI::success('Legion rank roles registered!');
    });
}
