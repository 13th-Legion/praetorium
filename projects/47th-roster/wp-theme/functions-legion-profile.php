<?php
/**
 * 47th Legion Profile Enhancements
 * Service Pips + Ribbon Rack + Rank + Steam on Ultimate Member Profiles
 */

if (!defined('ABSPATH')) exit;

/**
 * Get roster data for a user by Discord ID
 */
function legion_get_roster_member($discord_id) {
    static $roster_data = null;
    
    if ($roster_data === null) {
        $roster_file = get_stylesheet_directory() . '/data/roster.json';
        if (file_exists($roster_file)) {
            $json = file_get_contents($roster_file);
            $roster_data = json_decode($json, true);
        } else {
            $roster_data = array('members' => array());
        }
    }
    
    foreach ($roster_data['members'] as $member) {
        if ($member['id'] === $discord_id) {
            return $member;
        }
    }
    return null;
}

/**
 * Display service pips and ribbon rack in the profile content area (alongside bio)
 */
function legion_display_profile_extras($args) {
    $user_id = um_profile_id();
    $discord_id = get_user_meta($user_id, 'discord_id', true);
    
    if (!$discord_id) return;
    
    $member = legion_get_roster_member($discord_id);
    if (!$member) return;
    
    $asset_base = get_stylesheet_directory_uri() . '/assets';
    $years = isset($member['yearsOfService']) ? (int)$member['yearsOfService'] : 0;
    
    // Service Pips
    $gold_pips = floor($years / 5);
    $silver_pips = $years % 5;
    
    echo '<div class="legion-profile-military-info">';
    
    // Service pips
    if ($years > 0) {
        echo '<div class="legion-service-pips-wrap">';
        echo '<div class="legion-service-pips">';
        for ($i = 0; $i < $gold_pips; $i++) {
            echo '<img src="' . esc_url($asset_base . '/timeInService/pip_5year.png') . '" alt="5yr" class="service-pip">';
        }
        for ($i = 0; $i < $silver_pips; $i++) {
            echo '<img src="' . esc_url($asset_base . '/timeInService/pip_1year.png') . '" alt="1yr" class="service-pip">';
        }
        echo '</div>';
        echo '<span class="legion-service-text">' . esc_html($years) . ' year' . ($years !== 1 ? 's' : '') . ' of service</span>';
        echo '</div>';
    }
    
    // Ribbon Rack
    if (!empty($member['awards'])) {
        $precedence = array(
            // Valor Decorations (highest precedence)
            'corona_obsidionalis' => 1, 'corona_civica' => 2, 'medal' => 3, 'corona_aurea' => 4,
            'corona_vallaris' => 5, 'corona_muralis' => 6, 'torques' => 7, 'wounded' => 8,
            'prisoner' => 9, 'armillae' => 10,
            // Service Awards
            'service' => 11, 'good_conduct' => 12, 'nco_development' => 13, 'recruiter' => 14,
            'commendation' => 15, 'achievement' => 16, 'organizational_excellence' => 17,
            'joint_operations' => 18, 'joint_training' => 19, 'defense' => 20,
            'army_occupation' => 21, 'humane_action' => 22, 'mng_2014' => 23,
            // Campaign Ribbons - Historical (chronological)
            'swg' => 30, 'tabula_rasa' => 31, 'fallen_earth' => 32, 'global_agenda' => 33,
            'sto' => 34, 'swtor' => 35, 'earthrise' => 36, 'planetside2' => 37,
            'defiance' => 38, 'MWO' => 39, 'elder_scrolls' => 40, 'firefall' => 41,
            'empyrion' => 42, 'repopulation' => 43, 'division' => 44, 'division2' => 45,
            // Campaign Ribbons - Active
            'dune_awakening' => 50, 'fallout76' => 51, 'colonial_marines' => 52,
            'war_thunder' => 53, 'helldivers' => 54, 'space_marine2' => 55,
            // Campaign Ribbons - Upcoming
            'stars_reach' => 60
        );
        
        $awards = $member['awards'];
        usort($awards, function($a, $b) use ($precedence) {
            $pa = isset($precedence[$a]) ? $precedence[$a] : 999;
            $pb = isset($precedence[$b]) ? $precedence[$b] : 999;
            return $pa - $pb;
        });
        
        $ribbons_per_row = 4;
        $total = count($awards);
        $top_row_count = $total % $ribbons_per_row;
        
        echo '<div class="legion-ribbon-rack">';
        echo '<h4 class="ribbon-rack-title">Awards & Campaigns</h4>';
        
        $idx = 0;
        if ($top_row_count > 0) {
            echo '<div class="ribbon-row ribbon-row-partial">';
            for ($i = 0; $i < $top_row_count; $i++) {
                $award = $awards[$idx++];
                $label = ucwords(str_replace('_', ' ', $award));
                echo '<img src="' . esc_url($asset_base . '/ribbons/' . $award . '.png') . '" alt="' . esc_attr($label) . '" title="' . esc_attr($label) . '" class="ribbon">';
            }
            echo '</div>';
        }
        
        while ($idx < $total) {
            echo '<div class="ribbon-row">';
            for ($i = 0; $i < $ribbons_per_row && $idx < $total; $i++) {
                $award = $awards[$idx++];
                $label = ucwords(str_replace('_', ' ', $award));
                echo '<img src="' . esc_url($asset_base . '/ribbons/' . $award . '.png') . '" alt="' . esc_attr($label) . '" title="' . esc_attr($label) . '" class="ribbon">';
            }
            echo '</div>';
        }
        
        echo '</div>';
    }
    
    echo '</div>';
}
// Hook into profile content area, run early so it appears first
add_action('um_profile_content_main', 'legion_display_profile_extras', 1);

/**
 * Display rank info below Display Name field
 */
function legion_display_rank_in_content($args) {
    $user_id = um_profile_id();
    $discord_id = get_user_meta($user_id, 'discord_id', true);
    
    if (!$discord_id) return;
    
    $member = legion_get_roster_member($discord_id);
    if (!$member || empty($member['rank'])) return;
    
    $asset_base = get_stylesheet_directory_uri() . '/assets';
    $rank_code = $member['rank']['code'];
    $rank_name = $member['rank']['name'];
    
    // Format rank code (O3 -> O-3)
    $rank_grade = preg_replace('/^([A-Z])(\d+)$/', '$1-$2', $rank_code);
    
    // Rank image path
    $rank_img = strtolower($rank_code) . '.png';
    
    echo '<div class="legion-rank-display">';
    echo '<img src="' . esc_url($asset_base . '/ranks/' . $rank_img) . '" alt="' . esc_attr($rank_code) . '" class="rank-icon">';
    echo '<span class="rank-name">' . esc_html($rank_name) . '</span>';
    echo '<span class="rank-grade">(' . esc_html($rank_grade) . ')</span>';
    echo '</div>';
}
add_action('um_profile_content_main', 'legion_display_rank_in_content', 2);

/**
 * Display Steam profile widget at bottom of profile (only if Steam ID is set)
 */
function legion_display_steam_profile($args) {
    $user_id = um_profile_id();
    $steam_id = get_user_meta($user_id, 'steam_id', true);
    
    // Only show if user has entered a Steam ID
    if (empty($steam_id)) return;
    
    // Validate it looks like a Steam ID (17 digits)
    if (!preg_match('/^\d{17}$/', $steam_id)) return;
    
    echo '<div class="legion-steam-section">';
    echo '<h4 class="steam-section-title">Steam Activity</h4>';
    echo do_shortcode('[steammanager_profile steam_id=' . esc_attr($steam_id) . ']');
    echo do_shortcode('[steammanager_recently_played steam_id=' . esc_attr($steam_id) . ']');
    echo '</div>';
}
add_action('um_profile_content_main', 'legion_display_steam_profile', 100);

/**
 * CSS for profile military info
 */
function legion_profile_military_css() {
    if (!function_exists('um_profile_id')) return;
    ?>
    <style>
    /* ===== Legion Profile Layout - Side by Side ===== */
    
    /* Profile body becomes a flex row */
    .um-profile-body.main.main-default {
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: wrap;
        gap: 30px;
        align-items: flex-start;
    }
    
    /* Military info on the left */
    .legion-profile-military-info {
        flex: 0 0 auto;
        text-align: center;
        padding: 0;
    }
    
    /* Profile fields (Bio, Display Name) on the right */
    .um-profile-body.main .um-row._um_row_1 {
        flex: 1;
        min-width: 250px;
    }
    
    /* ===== Hide unwanted fields ===== */
    .um-profile .um-field-first_name,
    .um-profile .um-field-last_name,
    .um-profile .um-field-user_login,
    .um-profile .um-field-discord_id,
    .um-profile .um-field-steam_id {
        display: none !important;
    }
    
    /* ===== Rank Display (below Display Name) ===== */
    .legion-rank-display {
        display: flex;
        align-items: center;
        gap: 8px;
        margin: -10px 0 15px 0;
        padding: 8px 12px;
        background: rgba(18, 18, 26, 0.6);
        border-radius: 6px;
        width: fit-content;
    }
    .legion-rank-display .rank-icon {
        width: 28px;
        height: 28px;
        object-fit: contain;
    }
    .legion-rank-display .rank-name {
        color: #c9a227;
        font-family: 'Cinzel', serif;
        font-size: 0.95rem;
    }
    .legion-rank-display .rank-grade {
        color: #888;
        font-size: 0.85rem;
    }
    
    /* ===== Service Pips Styling ===== */
    .legion-service-pips-wrap {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 6px;
        margin-bottom: 15px;
        font-family: 'Cinzel', serif;
    }
    .legion-service-pips {
        display: flex;
        gap: 0;
    }
    .legion-service-pips .service-pip {
        width: 16px;
        height: 22px;
        display: block;
        filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.5));
    }
    .legion-service-text {
        color: #888;
        font-size: 0.85em;
    }
    
    /* ===== Ribbon Rack Styling ===== */
    .legion-ribbon-rack {
        display: inline-block;
        padding: 15px 20px;
        background: rgba(18, 18, 26, 0.9);
        border: 1px solid #2a2a3a;
        border-radius: 8px;
        text-align: center;
    }
    .ribbon-rack-title {
        color: #c9a227 !important;
        font-size: 0.85rem !important;
        margin: 0 0 12px 0 !important;
        padding: 0 !important;
        text-transform: uppercase;
        letter-spacing: 1px;
        font-family: 'Cinzel', serif !important;
    }
    .legion-ribbon-rack .ribbon-row {
        display: flex;
        justify-content: center;
        gap: 0;
        margin-bottom: -1px;
    }
    .legion-ribbon-rack .ribbon {
        width: 44px;
        height: 17px;
        border: 1px solid rgba(0, 0, 0, 0.3);
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.15);
        transition: transform 0.2s ease;
    }
    .legion-ribbon-rack .ribbon:hover {
        transform: scale(2);
        z-index: 100;
        position: relative;
    }
    
    /* ===== Steam Section ===== */
    .legion-steam-section {
        margin-top: 30px;
        padding-top: 20px;
        border-top: 1px solid #2a2a3a;
        clear: both;
        width: 100%;
    }
    .steam-section-title {
        color: #c9a227 !important;
        font-size: 1rem !important;
        margin: 0 0 15px 0 !important;
        text-transform: uppercase;
        letter-spacing: 1px;
        font-family: 'Cinzel', serif !important;
    }
    
    /* ===== Fix Update Profile button being cut off ===== */
    .um-profile-edit .um-col-alt input[type="submit"],
    .um-profile-edit input.um-button,
    .um input[type="submit"].um-button {
        min-width: 160px !important;
        width: auto !important;
        padding: 10px 20px !important;
        white-space: nowrap !important;
    }
    
    /* ===== Mobile: Stack vertically ===== */
    @media (max-width: 700px) {
        .um-profile-body.main.main-default {
            flex-direction: column !important;
        }
        .legion-profile-military-info {
            width: 100%;
        }
    }
    </style>
    
    <script>
    document.addEventListener('DOMContentLoaded', function() {
        // Reorder: Move Display Name before Bio
        var displayName = document.querySelector('.um-field-display_name');
        var bio = document.querySelector('.um-field-description');
        if (displayName && bio && bio.parentNode) {
            bio.parentNode.insertBefore(displayName, bio);
        }
        
        // Move rank display after display name
        var rankDisplay = document.querySelector('.legion-rank-display');
        if (rankDisplay && displayName) {
            displayName.after(rankDisplay);
        }
    });
    </script>
    <?php
}
add_action('wp_head', 'legion_profile_military_css');
