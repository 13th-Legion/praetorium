<?php
/**
 * Template Name: 47th Legion Rank Structure
 * 
 * Rank structure reference page
 * Updated: 2026-01-30
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

get_header(); 
$asset_base = get_stylesheet_directory_uri() . '/assets';
?>

<style>
.legion-ranks-wrap {
  background: #0a0a0f;
  color: #e8e6e3;
  font-family: 'Cinzel', 'Times New Roman', serif;
  min-height: 100vh;
  padding: 2rem;
  line-height: 1.6;
}

.legion-ranks-wrap * { box-sizing: border-box; }

.ranks-container {
  max-width: 1200px;
  margin: 0 auto;
}

.ranks-container h1 {
  text-align: center;
  color: #c9a227;
  font-size: 2.5rem;
  margin-bottom: 0.5rem;
  letter-spacing: 3px;
}

.ranks-container .subtitle {
  text-align: center;
  color: #888;
  font-style: italic;
  margin-bottom: 3rem;
}

.ranks-container h2 {
  color: #c9a227;
  border-bottom: 1px solid #2a2a3a;
  padding-bottom: 0.5rem;
  margin: 2rem 0 1rem;
  font-size: 1.5rem;
  letter-spacing: 2px;
}

.ranks-container h3 {
  color: #c9a227;
  font-size: 1.1rem;
  margin: 1.5rem 0 1rem;
}

.section-intro {
  color: #888;
  margin-bottom: 1.5rem;
  font-family: Georgia, serif;
}

.rank-table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 2rem;
}

.rank-table th {
  background: #12121a;
  color: #c9a227;
  text-align: left;
  padding: 0.75rem 1rem;
  border-bottom: 2px solid #c9a227;
  font-weight: normal;
  letter-spacing: 1px;
}

.rank-table td {
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #2a2a3a;
  vertical-align: middle;
}

.rank-table tr:hover { background: rgba(201, 162, 39, 0.05); }

.rank-img { width: 40px; height: 40px; object-fit: contain; }
.rank-code { font-family: monospace; color: #888; font-size: 0.9rem; }
.rank-name { font-weight: bold; }

.promotion-info {
  background: #12121a;
  border: 1px solid #2a2a3a;
  border-radius: 8px;
  padding: 1.5rem;
  margin: 1rem 0;
}

.promotion-info h3 { color: #c9a227; margin-bottom: 1rem; margin-top: 0; }
.promotion-info ul { list-style: none; padding: 0; margin: 0; }
.promotion-info li { padding: 0.5rem 0; border-bottom: 1px solid #2a2a3a; font-family: Georgia, serif; }
.promotion-info li:last-child { border-bottom: none; }
.days { color: #c9a227; font-weight: bold; }
</style>

<div class="legion-ranks-wrap">
  <div class="ranks-container">
    <h1>RANK STRUCTURE</h1>
    <p class="subtitle">The military structure of the 47th Legion</p>

    <h2>⚔️ LEGION RANKS</h2>
    <p class="section-intro">
      The 47th Legion uses a Roman-inspired military rank structure. Junior enlisted ranks (E-1 to E-3) 
      are earned through time in service, while senior enlisted (E-4+) and Officer ranks require demonstrated 
      leadership and are awarded by command.
    </p>

    <div class="promotion-info">
      <h3>📈 Automatic Promotion Schedule</h3>
      <ul>
        <li><strong>E-1 Tiron → E-2 Miles:</strong> <span class="days">30 days</span> of service</li>
        <li><strong>E-2 Miles → E-3 Miles Gregarius:</strong> <span class="days">60 days</span> of service</li>
        <li><strong>E-3 Miles Gregarius → E-4 Decanus:</strong> <span class="days">120 days</span> of service</li>
        <li><strong>E-5+ Senior Enlisted:</strong> Awarded by command based on merit</li>
        <li><strong>Officer Ranks (O-1 to O-8):</strong> Appointed by Legion command</li>
      </ul>
    </div>

    <h3>Officers</h3>
    <table class="rank-table">
      <thead><tr><th style="width:60px">Insignia</th><th style="width:60px">Grade</th><th>Title</th><th>Description</th></tr></thead>
      <tbody>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/o8.png" class="rank-img"></td><td class="rank-code">O-8</td><td class="rank-name">Imperator</td><td>Supreme Commander</td></tr>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/o7.png" class="rank-img"></td><td class="rank-code">O-7</td><td class="rank-name">Legate</td><td>Legion Commander</td></tr>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/o6.png" class="rank-img"></td><td class="rank-code">O-6</td><td class="rank-name">Prefect</td><td>Senior Staff Officer</td></tr>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/o5.png" class="rank-img"></td><td class="rank-code">O-5</td><td class="rank-name">Tribune</td><td>Staff Officer</td></tr>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/o4.png" class="rank-img"></td><td class="rank-code">O-4</td><td class="rank-name">Centurion</td><td>Century Commander</td></tr>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/o3.png" class="rank-img"></td><td class="rank-code">O-3</td><td class="rank-name">Optio</td><td>Second-in-command of a Century</td></tr>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/o2.png" class="rank-img"></td><td class="rank-code">O-2</td><td class="rank-name">Tesserarian</td><td>Guard Commander, Watch Officer</td></tr>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/o1.png" class="rank-img"></td><td class="rank-code">O-1</td><td class="rank-name">Vexillarian</td><td>Standard Bearer, Junior Officer</td></tr>
      </tbody>
    </table>

    <h3>Enlisted</h3>
    <table class="rank-table">
      <thead><tr><th style="width:60px">Insignia</th><th style="width:60px">Grade</th><th>Title</th><th>Description</th></tr></thead>
      <tbody>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/e8.png" class="rank-img"></td><td class="rank-code">E-8</td><td class="rank-name">Signifier</td><td>Standard Bearer of the Century</td></tr>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/e7.png" class="rank-img"></td><td class="rank-code">E-7</td><td class="rank-name">Triplicarian</td><td>Senior Pay Grade (Triple Pay)</td></tr>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/e6.png" class="rank-img"></td><td class="rank-code">E-6</td><td class="rank-name">Duplicarian</td><td>Senior Enlisted (Double Pay)</td></tr>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/e5.png" class="rank-img"></td><td class="rank-code">E-5</td><td class="rank-name">Carian</td><td>Veteran Soldier</td></tr>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/e4.png" class="rank-img"></td><td class="rank-code">E-4</td><td class="rank-name">Decanus</td><td>Squad Leader (Contubernium)</td></tr>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/e3.png" class="rank-img"></td><td class="rank-code">E-3</td><td class="rank-name">Miles Gregarius</td><td>Common Soldier (60+ days)</td></tr>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/e2.png" class="rank-img"></td><td class="rank-code">E-2</td><td class="rank-name">Miles</td><td>Soldier (30+ days)</td></tr>
        <tr><td><img src="<?php echo $asset_base; ?>/ranks/e1.png" class="rank-img"></td><td class="rank-code">E-1</td><td class="rank-name">Tiron</td><td>Recruit, New Legionary</td></tr>
      </tbody>
    </table>

    <h3>Reserves</h3>
    <table class="rank-table">
      <thead><tr><th style="width:60px">Insignia</th><th style="width:60px">Grade</th><th>Title</th><th>Description</th></tr></thead>
      <tbody>
        <tr><td>—</td><td class="rank-code">RES</td><td class="rank-name">Veteranus</td><td>Inactive or reserve Legionary</td></tr>
      </tbody>
    </table>
  </div>
</div>

<?php get_footer(); ?>
