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
/* Break out of Astra container */
.ast-separate-container #primary,
.ast-plain-container #primary,
#primary {
  max-width: 100% !important;
  width: 100% !important;
  margin: 0 !important;
  padding: 0 !important;
}

.ast-separate-container .ast-article-single,
.ast-plain-container .ast-article-single,
article.page {
  max-width: 100% !important;
  padding: 0 !important;
}

.ast-container:has(.legion-awards-wrap) {
  max-width: 100% !important;
  padding: 0 !important;
}

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
  max-width: 100%;
  padding: 0 2rem;
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
  width: 100%;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  max-width: 100%;
  gap: 1rem;
}

@media (max-width: 1400px) {
  .awards-grid { grid-template-columns: repeat(4, 1fr); }
}
@media (max-width: 1100px) {
  .awards-grid { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 800px) {
  .awards-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 500px) {
  .awards-grid { grid-template-columns: 1fr; }
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

    <h2>🎗️ CAMPAIGN RIBBONS</h2>
    <p class="section-intro">Awarded for participation in Legion campaigns, ordered by precedence (oldest campaigns first).</p>

    <h3>Campaign Ribbons — Historical</h3>
    <div class="awards-grid">
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/swg.png" class="award-ribbon"><div class="award-info"><div class="award-name">Star Wars Galaxies</div><div class="award-desc">Origin game of the 47th (2007).</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/tabula_rasa.png" class="award-ribbon"><div class="award-info"><div class="award-name">Tabula Rasa</div><div class="award-desc">Served in Tabula Rasa (2007).</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/fallen_earth.png" class="award-ribbon"><div class="award-info"><div class="award-name">Fallen Earth</div><div class="award-desc">Served in Fallen Earth (2009).</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/global_agenda.png" class="award-ribbon"><div class="award-info"><div class="award-name">Global Agenda</div><div class="award-desc">Served in Global Agenda (2010).</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/sto.png" class="award-ribbon"><div class="award-info"><div class="award-name">Star Trek Online</div><div class="award-desc">Served in Star Trek Online (2010).</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/swtor.png" class="award-ribbon"><div class="award-info"><div class="award-name">SWTOR</div><div class="award-desc">Served in Star Wars: The Old Republic (2011).</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/earthrise.png" class="award-ribbon"><div class="award-info"><div class="award-name">Earthrise</div><div class="award-desc">Served in Earthrise (2011).</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/planetside2.png" class="award-ribbon"><div class="award-info"><div class="award-name">Planetside 2</div><div class="award-desc">Served in Planetside 2 (2012).</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/defiance.png" class="award-ribbon"><div class="award-info"><div class="award-name">Defiance</div><div class="award-desc">Served in Defiance (2013).</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/MWO.png" class="award-ribbon"><div class="award-info"><div class="award-name">MechWarrior Online</div><div class="award-desc">Served in MechWarrior Online (2013).</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/elder_scrolls.png" class="award-ribbon"><div class="award-info"><div class="award-name">Elder Scrolls Online</div><div class="award-desc">Served in Elder Scrolls Online (2014).</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/firefall.png" class="award-ribbon"><div class="award-info"><div class="award-name">Firefall</div><div class="award-desc">Served in Firefall (2014).</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/empyrion.png" class="award-ribbon"><div class="award-info"><div class="award-name">Empyrion</div><div class="award-desc">Served in Empyrion: Galactic Survival (2015).</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/repopulation.png" class="award-ribbon"><div class="award-info"><div class="award-name">The Repopulation</div><div class="award-desc">Served in The Repopulation (2015).</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/division.png" class="award-ribbon"><div class="award-info"><div class="award-name">The Division</div><div class="award-desc">Served in The Division (2016).</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/division2.png" class="award-ribbon"><div class="award-info"><div class="award-name">The Division 2</div><div class="award-desc">Served in The Division 2 (2019).</div></div></div>
    </div>

    <h3>Campaign Ribbons — Active</h3>
    <div class="awards-grid">
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/dune_awakening.png" class="award-ribbon"><div class="award-info"><div class="award-name">Dune: Awakening</div><div class="award-desc">Served in Dune: Awakening.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/fallout76.png" class="award-ribbon"><div class="award-info"><div class="award-name">Fallout 76</div><div class="award-desc">Served in Fallout 76.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/colonial_marines.png" class="award-ribbon"><div class="award-info"><div class="award-name">Aliens: Fireteam Elite</div><div class="award-desc">Served in Aliens: Fireteam Elite.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/war_thunder.png" class="award-ribbon"><div class="award-info"><div class="award-name">War Thunder</div><div class="award-desc">Served in War Thunder.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/helldivers.png" class="award-ribbon"><div class="award-info"><div class="award-name">Helldivers 2</div><div class="award-desc">Served in Helldivers 2.</div></div></div>
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/space_marine2.png" class="award-ribbon"><div class="award-info"><div class="award-name">Space Marine 2</div><div class="award-desc">Served in Warhammer 40K: Space Marine 2.</div></div></div>
    </div>

    <h3>Campaign Ribbons — Upcoming</h3>
    <div class="awards-grid">
      <div class="award-card"><img src="<?php echo $asset_base; ?>/ribbons/stars_reach.png" class="award-ribbon"><div class="award-info"><div class="award-name">Stars Reach</div><div class="award-desc">Served in Stars Reach.</div></div></div>
    </div>

  </div>
</div>

<?php get_footer(); ?>
