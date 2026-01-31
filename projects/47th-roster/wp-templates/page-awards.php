<?php
/**
 * Template Name: 47th Legion Awards
 * 
 * Decorations and awards reference page
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

get_header(); 
$asset_base = get_stylesheet_directory_uri() . '/assets';
?>

<style>
.legion-awards-wrap {
  background: #0a0a0f;
  color: #e8e6e3;
  font-family: 'Cinzel', 'Times New Roman', serif;
  min-height: 100vh;
  padding: 2rem;
  line-height: 1.6;
}

.legion-awards-wrap * { box-sizing: border-box; }

.awards-container {
  max-width: 1200px;
  margin: 0 auto;
}

.awards-container h1 {
  text-align: center;
  color: #c9a227;
  font-size: 2.5rem;
  margin-bottom: 0.5rem;
  letter-spacing: 3px;
}

.awards-container .subtitle {
  text-align: center;
  color: #888;
  font-style: italic;
  margin-bottom: 3rem;
}

.awards-container h2 {
  color: #c9a227;
  border-bottom: 1px solid #2a2a3a;
  padding-bottom: 0.5rem;
  margin: 2rem 0 1rem;
  font-size: 1.5rem;
  letter-spacing: 2px;
}

.awards-container h3 {
  color: #c9a227;
  font-size: 1.1rem;
  margin: 1.5rem 0 1rem;
}

.section-intro {
  color: #888;
  margin-bottom: 1.5rem;
  font-family: Georgia, serif;
}

.awards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 1rem;
}

.award-card {
  background: #12121a;
  border: 1px solid #2a2a3a;
  border-radius: 8px;
  padding: 1rem;
  display: flex;
  gap: 1rem;
  align-items: flex-start;
  transition: border-color 0.3s, transform 0.2s;
}

.award-card:hover {
  border-color: #c9a227;
  transform: translateY(-2px);
}

.award-ribbon {
  width: 72px;
  height: 28px;
  flex-shrink: 0;
  border: 1px solid rgba(0,0,0,0.3);
  box-shadow: 0 2px 4px rgba(0,0,0,0.3);
}

.award-info { flex: 1; }
.award-name { font-weight: bold; color: #c9a227; margin-bottom: 0.25rem; font-size: 0.95rem; }
.award-desc { color: #888; font-size: 0.85rem; font-family: Georgia, serif; }
</style>

<div class="legion-awards-wrap">
  <div class="awards-container">
    <h1>DECORATIONS & AWARDS</h1>
    <p class="subtitle">The honors of the 47th Legion</p>

    <h2>🎖️ LEGION DECORATIONS</h2>
    <p class="section-intro">Legionaries earn awards for valor, service, and participation in campaigns.</p>

    <h3>Valor Decorations</h3>
    <div class="awards-grid">
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/corona_obsidionalis.png" class="award-ribbon"><div class="award-info"><div class="award-name">Corona Obsidionalis</div><div class="award-desc">Breaking the siege of beleaguered Imperial forces.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/corona_civica.png" class="award-ribbon"><div class="award-info"><div class="award-name">Corona Civica</div><div class="award-desc">Saving the life of another Legionary.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/medal.png" class="award-ribbon"><div class="award-info"><div class="award-name">Legionary's Medal</div><div class="award-desc">Heroism above and beyond the call of duty.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/corona_aurea.png" class="award-ribbon"><div class="award-info"><div class="award-name">Corona Aurea</div><div class="award-desc">Killing an enemy in single combat.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/torques.png" class="award-ribbon"><div class="award-info"><div class="award-name">Torques</div><div class="award-desc">Display valor in combat.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/wounded.png" class="award-ribbon"><div class="award-info"><div class="award-name">Wounded in Combat</div><div class="award-desc">Suffer injury from enemy contact.</div></div></div>
    </div>

    <h3>Service Awards</h3>
    <div class="awards-grid">
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/service.png" class="award-ribbon"><div class="award-info"><div class="award-name">Imperial Service</div><div class="award-desc">Begin service with the Legion.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/good_conduct.png" class="award-ribbon"><div class="award-info"><div class="award-name">Good Conduct</div><div class="award-desc">60 days without reprimand.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/nco_development.png" class="award-ribbon"><div class="award-info"><div class="award-name">NCO Development</div><div class="award-desc">Display leadership as an NCO.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/recruiter.png" class="award-ribbon"><div class="award-info"><div class="award-name">Recruiter</div><div class="award-desc">Recruit 2 Legionaries.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/commendation.png" class="award-ribbon"><div class="award-info"><div class="award-name">Commendation</div><div class="award-desc">Meritorious service.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/achievement.png" class="award-ribbon"><div class="award-info"><div class="award-name">Achievement</div><div class="award-desc">Lesser meritorious service.</div></div></div>
    </div>

    <h3>Campaign Ribbons</h3>
    <div class="awards-grid">
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/helldivers.png" class="award-ribbon"><div class="award-info"><div class="award-name">Helldivers 2</div><div class="award-desc">Served in Helldivers 2.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/fallout76.png" class="award-ribbon"><div class="award-info"><div class="award-name">Fallout 76</div><div class="award-desc">Served in Fallout 76.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/elder_scrolls.png" class="award-ribbon"><div class="award-info"><div class="award-name">Elder Scrolls Online</div><div class="award-desc">Served in ESO.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/swg.png" class="award-ribbon"><div class="award-info"><div class="award-name">Star Wars Galaxies</div><div class="award-desc">Origin game of the 47th.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/colonial_marines.png" class="award-ribbon"><div class="award-info"><div class="award-name">Colonial Marines</div><div class="award-desc">Served in Aliens: Fireteam Elite.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/MWO.png" class="award-ribbon"><div class="award-info"><div class="award-name">MechWarrior Online</div><div class="award-desc">Served in MWO.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/star_citizen.png" class="award-ribbon"><div class="award-info"><div class="award-name">Star Citizen</div><div class="award-desc">Served in Star Citizen.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/swtor.png" class="award-ribbon"><div class="award-info"><div class="award-name">SWTOR</div><div class="award-desc">Served in The Old Republic.</div></div></div>
    </div>
  </div>
</div>

<?php get_footer(); ?>
