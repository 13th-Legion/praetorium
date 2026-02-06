<?php
/**
 * 47th Legion Custom Profile Fields for Ultimate Member
 */

if (!defined('ABSPATH')) exit;

/**
 * Register custom profile fields with Ultimate Member
 * These fields are added programmatically and will appear in UM forms
 */
function legion_um_add_custom_fields($fields) {
    
    // Discord ID (for roster sync)
    $fields['discord_id'] = array(
        'title' => 'Discord ID',
        'metakey' => 'discord_id',
        'public' => 0,  // Hidden from public profile
        'editable' => 0, // Auto-filled by Discord login
        'type' => 'text',
        'label' => 'Discord User ID',
        'placeholder' => '179481162710908928',
        'required' => 0,
        'icon' => 'um-faicon-discord',
    );
    
    // Discord Username
    $fields['discord_username'] = array(
        'title' => 'Discord Username',
        'metakey' => 'discord_username',
        'type' => 'text',
        'label' => 'Discord Username',
        'placeholder' => 'username',
        'required' => 0,
        'public' => 1,
        'editable' => 1,
        'icon' => 'um-faicon-discord',
    );
    
    // Callsign / Nickname
    $fields['callsign'] = array(
        'title' => 'Callsign',
        'metakey' => 'callsign',
        'type' => 'text',
        'label' => 'Callsign / Nickname',
        'placeholder' => 'Your in-game callsign',
        'required' => 0,
        'public' => 1,
        'editable' => 1,
        'icon' => 'um-faicon-id-badge',
    );
    
    // Join Date (historical)
    $fields['legion_join_date'] = array(
        'title' => 'Legion Join Date',
        'metakey' => 'legion_join_date',
        'type' => 'date',
        'label' => 'Date Joined 47th Legion',
        'required' => 0,
        'public' => 1,
        'editable' => 0, // Only admins can edit
        'icon' => 'um-faicon-calendar',
    );
    
    // Active Games (multi-select)
    $fields['active_games'] = array(
        'title' => 'Active Games',
        'metakey' => 'active_games',
        'type' => 'multiselect',
        'label' => 'Games You Play',
        'options' => array(
            'helldivers2' => 'Helldivers 2',
            'fallout76' => 'Fallout 76',
            'eso' => 'Elder Scrolls Online',
            'swg' => 'Star Wars: Galaxies (Legends)',
            'aliens' => 'Aliens: Fireteam Elite',
            'mwo' => 'MechWarrior Online',
            'starcitizen' => 'Star Citizen',
        ),
        'required' => 0,
        'public' => 1,
        'editable' => 1,
        'icon' => 'um-faicon-gamepad',
    );
    
    // Gaming Timezone
    $fields['gaming_timezone'] = array(
        'title' => 'Gaming Timezone',
        'metakey' => 'gaming_timezone',
        'type' => 'select',
        'label' => 'Your Timezone',
        'options' => array(
            'est' => 'Eastern (EST/EDT)',
            'cst' => 'Central (CST/CDT)',
            'mst' => 'Mountain (MST/MDT)',
            'pst' => 'Pacific (PST/PDT)',
            'utc' => 'UTC/GMT',
            'cet' => 'Central European (CET)',
            'other' => 'Other',
        ),
        'required' => 0,
        'public' => 1,
        'editable' => 1,
        'icon' => 'um-faicon-clock-o',
    );
    
    // Play Schedule
    $fields['play_schedule'] = array(
        'title' => 'Typical Play Times',
        'metakey' => 'play_schedule',
        'type' => 'select',
        'label' => 'When Do You Usually Play?',
        'options' => array(
            'mornings' => 'Mornings (6am-12pm)',
            'afternoons' => 'Afternoons (12pm-6pm)',
            'evenings' => 'Evenings (6pm-12am)',
            'nights' => 'Late Night (12am-6am)',
            'weekends' => 'Weekends Only',
            'varies' => 'Varies',
        ),
        'required' => 0,
        'public' => 1,
        'editable' => 1,
        'icon' => 'um-faicon-calendar-check-o',
    );
    
    // Steam ID
    $fields['steam_id'] = array(
        'title' => 'Steam Profile',
        'metakey' => 'steam_id',
        'type' => 'url',
        'label' => 'Steam Profile URL',
        'placeholder' => 'https://steamcommunity.com/id/yourname',
        'required' => 0,
        'public' => 1,
        'editable' => 1,
        'icon' => 'um-faicon-steam',
    );
    
    // Specialties
    $fields['specialties'] = array(
        'title' => 'Combat Specialties',
        'metakey' => 'specialties',
        'type' => 'multiselect',
        'label' => 'Your Specialties',
        'options' => array(
            'assault' => 'Assault / Frontline',
            'support' => 'Support / Medic',
            'recon' => 'Recon / Scout',
            'heavy' => 'Heavy Weapons',
            'pilot' => 'Pilot / Vehicle',
            'engineer' => 'Engineer / Tech',
            'sniper' => 'Sniper / Marksman',
            'leadership' => 'Squad Leadership',
        ),
        'required' => 0,
        'public' => 1,
        'editable' => 1,
        'icon' => 'um-faicon-crosshairs',
    );
    
    // Bio
    $fields['legion_bio'] = array(
        'title' => 'Legionary Bio',
        'metakey' => 'legion_bio',
        'type' => 'textarea',
        'label' => 'About You',
        'placeholder' => 'Tell us about yourself...',
        'max_chars' => 500,
        'required' => 0,
        'public' => 1,
        'editable' => 1,
        'icon' => 'um-faicon-user',
    );
    
    // How They Found Us
    $fields['referral_source'] = array(
        'title' => 'How Did You Find Us?',
        'metakey' => 'referral_source',
        'type' => 'select',
        'label' => 'How did you find the 47th Legion?',
        'options' => array(
            'friend' => 'Friend / Current Member',
            'discord' => 'Discord Server Listing',
            'reddit' => 'Reddit',
            'game' => 'Met In-Game',
            'search' => 'Web Search',
            'other' => 'Other',
        ),
        'required' => 0,
        'public' => 0,
        'editable' => 1,
        'icon' => 'um-faicon-search',
    );
    
    return $fields;
}
add_filter('um_predefined_fields_hook', 'legion_um_add_custom_fields');

/**
 * Add custom fields to the default profile form
 */
function legion_um_profile_fields($args) {
    // These fields will be shown on profiles
    $args['custom_fields']['discord_username'] = array('position' => '1');
    $args['custom_fields']['callsign'] = array('position' => '2');
    $args['custom_fields']['legion_join_date'] = array('position' => '3');
    $args['custom_fields']['active_games'] = array('position' => '4');
    $args['custom_fields']['specialties'] = array('position' => '5');
    $args['custom_fields']['gaming_timezone'] = array('position' => '6');
    $args['custom_fields']['steam_id'] = array('position' => '7');
    $args['custom_fields']['legion_bio'] = array('position' => '8');
    
    return $args;
}
// Note: This filter may need adjustment based on your UM form setup

/**
 * Display years of service on profile
 */
function legion_display_years_of_service($user_id) {
    $join_date = get_user_meta($user_id, 'legion_join_date', true);
    
    if ($join_date) {
        $join = new DateTime($join_date);
        $now = new DateTime();
        $diff = $now->diff($join);
        $years = $diff->y;
        
        echo '<div class="legion-years-of-service">';
        echo '<span class="yos-number">' . $years . '</span>';
        echo '<span class="yos-label"> year' . ($years !== 1 ? 's' : '') . ' of service</span>';
        echo '</div>';
    }
}
add_action('um_after_profile_header_name_args', 'legion_display_years_of_service');

/**
 * Display active games as badges on profile
 */
function legion_display_game_badges($user_id) {
    $games = get_user_meta($user_id, 'active_games', true);
    
    if (!empty($games) && is_array($games)) {
        $game_labels = array(
            'helldivers2' => 'Helldivers 2',
            'fallout76' => 'Fallout 76',
            'eso' => 'Elder Scrolls Online',
            'swg' => 'SWG Legends',
            'aliens' => 'Aliens: Fireteam',
            'mwo' => 'MechWarrior Online',
            'starcitizen' => 'Star Citizen',
        );
        
        echo '<div class="legion-game-badges">';
        foreach ($games as $game) {
            if (isset($game_labels[$game])) {
                echo '<span class="game-badge game-' . esc_attr($game) . '">';
                echo esc_html($game_labels[$game]);
                echo '</span>';
            }
        }
        echo '</div>';
    }
}
add_action('um_after_profile_header_name_args', 'legion_display_game_badges', 20);

/**
 * CSS for profile customizations
 */
function legion_um_profile_css() {
    ?>
    <style>
    /* Rank display in profile HEADER - below the big gold name */
    .legion-profile-header-rank {
        display: flex !important;
        align-items: center;
        gap: 8px;
        font-family: 'Cinzel', serif;
        color: #c9a227;
        margin-top: 5px;
        width: 100%;
        clear: both;
    }
    .legion-profile-header-rank::before {
        content: '';
        display: table;
        clear: both;
    }
    .legion-profile-header-rank .rank-name {
        font-weight: 600;
        font-size: 1em;
    }
    .legion-profile-header-rank .rank-grade {
        color: #888;
        font-size: 0.9em;
    }
    .legion-rank-insignia {
        height: 24px;
        width: auto;
        vertical-align: middle;
    }
    
    /* Force rank to its own line in UM header */
    .um-profile-headericon-name .legion-profile-header-rank,
    .um-name .legion-profile-header-rank {
        display: block !important;
        width: 100% !important;
        margin-top: 8px !important;
    }
    .um-name .legion-profile-header-rank {
        display: flex !important;
    }
    .legion-years-of-service {
        color: #c9a227;
        font-family: 'Cinzel', serif;
        margin-top: 5px;
    }
    .legion-years-of-service .yos-number {
        font-weight: bold;
        font-size: 1.1em;
    }
    .legion-game-badges {
        display: flex;
        flex-wrap: wrap;
        gap: 5px;
        margin-top: 10px;
    }
    .game-badge {
        display: inline-block;
        padding: 3px 8px;
        font-size: 0.75em;
        border-radius: 3px;
        background: #2a2a3a;
        color: #e8e6e3;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .game-badge.game-helldivers2 { background: #4a7c59; }
    .game-badge.game-fallout76 { background: #5c7a29; }
    .game-badge.game-eso { background: #8b6914; }
    .game-badge.game-swg { background: #2d4a6f; }
    .game-badge.game-aliens { background: #1a3a2a; }
    .game-badge.game-mwo { background: #6b3a1a; }
    .game-badge.game-starcitizen { background: #2a4a6a; }
    </style>
    <?php
}
add_action('wp_head', 'legion_um_profile_css');
