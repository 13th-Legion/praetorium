#!/usr/bin/env node
/**
 * 47th Legion Roster Builder
 * Processes Discord member data into roster format
 */

const fs = require('fs');
const path = require('path');

// Role IDs from Discord
const ROLES = {
  OFFICERS: '179482607589785600',
  NCOS: '179482772086325250',
  ENLISTED: '326821477720522752',
  RESERVISTS: '1131980166122438686',
  BASE_47TH: '179985023607373824',
  AGENTS: '406136095395545088',
  ADMINISTRATOR: '471776406339190784',
  // Game roles (for tracking)
  HELLDIVERS: '446695304343846913',
  VAULT_DWELLERS: '455749225515319308',
  IRL_OPERATORS: '461401559452876820',
  DOONERS: '471727189025488896',
  TAMRIELITES: '812939607485841458',
  IMPERIALS: '830084560724820000',
  COLONIAL_MARINES: '1058087377383477349',
  MECHWARRIORS: '1249842357919285248',
};

// Bot role IDs to exclude
const BOT_ROLES = [
  '348218663385563138',  // Bots
  '310474846498717698',  // Discord.RSS
  '356848356796006400',  // Bridge
  '1100253527021998082', // Tatsu
  '1217504674056245429', // MEE6
  '1379602263437344854', // Zapier
  '1138252081090789418', // Emoji.gg
];

// Usernames to exclude (alts, etc.)
const EXCLUDED_USERNAMES = [
  'centurion6',  // Cavadus alt
];

// Manual member overrides (Discord ID -> overrides)
// Use for correcting names, ranks, or join dates that Discord doesn't have right
const MEMBER_OVERRIDES = {
  '179481162710908928': {  // Cavadus
    historicalJoinDate: '2007-12-04T05:56:00.000Z',
  },
  '242836923394162688': {  // Kolya
    historicalJoinDate: '2008-01-16T01:18:00.000Z',
  },
  '180332042720903168': {  // Odin
    historicalJoinDate: '2013-08-08T15:03:00.000Z',
  },
  '917992606892429443': {  // Dragonsoul
    historicalJoinDate: '2013-06-18T18:31:00.000Z',
  },
  '180127552126320640': {  // Lightfoot
    displayName: 'Lightfoot',
    rank: { code: 'E8', name: 'Signifier', category: 'Enlisted' },
    historicalJoinDate: '2013-05-07T18:02:00.000Z',
  },
  '169887739406319618': {  // Maximus
    historicalJoinDate: '2013-05-07T10:42:00.000Z',
  },
  '180087410619711488': {  // Bealin
    historicalJoinDate: '2013-07-18T01:36:00.000Z',
  },
  '179849584124624897': {  // Payton
    historicalJoinDate: '2008-12-30T05:15:00.000Z',
  },
  '1245124464082026573': {  // Snowmanha
    historicalJoinDate: '2017-06-01T00:00:00.000Z',
  },
  '141289033245392896': {  // Jungels
    historicalJoinDate: '2013-02-07T02:49:00.000Z',
  },
  '151814353354489856': {  // Lodowar
    historicalJoinDate: '2013-07-05T16:48:00.000Z',
  },
  '157228969446342666': {  // Bonetti
    historicalJoinDate: '2013-03-12T05:12:00.000Z',
  },
  '173679004883091457': {  // Dralzen
    historicalJoinDate: '2013-05-18T20:58:00.000Z',
  },
  '179782530264727552': {  // Destroy
    historicalJoinDate: '2011-02-05T00:04:00.000Z',
  },
  '180125977429540865': {  // Campbell
    historicalJoinDate: '2008-01-27T17:13:00.000Z',
  },
  '183274731825266688': {  // bikerb12
    historicalJoinDate: '2013-06-30T01:00:00.000Z',
  },
  '231905245372874763': {  // Rojnaar
    historicalJoinDate: '2013-03-11T19:14:00.000Z',
  },
  '269643017583853569': {  // Munster
    historicalJoinDate: '2013-03-24T16:47:00.000Z',
  },
  '299772766490722306': {  // Revoco
    historicalJoinDate: '2008-02-06T10:05:00.000Z',
  },
  '341207893288288257': {  // Striker
    historicalJoinDate: '2011-01-06T13:56:00.000Z',
  },
  '367819237252923393': {  // Toomey
    historicalJoinDate: '2013-01-11T02:01:00.000Z',
  },
  '537332237507756043': {  // Jenkins
    historicalJoinDate: '2013-03-13T15:34:00.000Z',
  },
};

// Rank mapping based on role hierarchy
// O4 Centurion (Cavadus), O3 Optio (Kolya), O2 Tesserarian (Odin), O1 Vexillarian (other officers)
const CAVADUS_ID = '179481162710908928';
const KOLYA_ID = '242836923394162688';
const ODIN_ID = '180332042720903168';

// Time-based auto-promotion thresholds (days since join)
const PROMOTION_DAYS = {
  E2: 30,   // Tiro → Gregarius after 30 days
  E3: 60,   // Gregarius → Miles after 60 days  
  E4: 120,  // Miles → Decanus (NCO) after 120 days
};

// Full rank structure (updated 2026-01-30 per official 47th Legion Rank Structure doc)
const RANKS = {
  // Officers (O-1 to O-8)
  'O8': { name: 'Imperator', category: 'Officers' },
  'O7': { name: 'Legate', category: 'Officers' },
  'O6': { name: 'Prefect', category: 'Officers' },
  'O5': { name: 'Tribune', category: 'Officers' },
  'O4': { name: 'Centurion', category: 'Officers' },
  'O3': { name: 'Optio', category: 'Officers' },
  'O2': { name: 'Tesserarian', category: 'Officers' },
  'O1': { name: 'Vexillarian', category: 'Officers' },
  // Enlisted (E-1 to E-8)
  'E8': { name: 'Signifier', category: 'Enlisted' },
  'E7': { name: 'Triplicarian', category: 'Enlisted' },
  'E6': { name: 'Duplicarian', category: 'Enlisted' },
  'E5': { name: 'Carian', category: 'Enlisted' },
  'E4': { name: 'Decanus', category: 'Enlisted' },
  'E3': { name: 'Miles Gregarius', category: 'Enlisted' },
  'E2': { name: 'Miles', category: 'Enlisted' },
  'E1': { name: 'Tiron', category: 'Enlisted' },
};

function determineRank(roles, userId, daysInService) {
  // Check for manual override first
  const override = MEMBER_OVERRIDES[userId];
  if (override?.rank) {
    return override.rank;
  }
  
  // Officers - based on specific users and roles
  if (userId === CAVADUS_ID || roles.includes(ROLES.ADMINISTRATOR)) {
    return { code: 'O4', name: 'Centurion', category: 'Officers' };
  }
  if (userId === KOLYA_ID) return { code: 'O3', name: 'Optio', category: 'Officers' };
  if (userId === ODIN_ID) return { code: 'O2', name: 'Tesserarian', category: 'Officers' };
  if (roles.includes(ROLES.OFFICERS)) return { code: 'O1', name: 'Vexillarian', category: 'Officers' };
  
  // Senior Enlisted - E5+ based on having NCO role
  if (roles.includes(ROLES.NCOS)) {
    // Has NCO role - default to E6 Duplicarian (can be overridden manually for higher)
    return { code: 'E6', name: 'Duplicarian', category: 'Enlisted' };
  }
  
  // Reservists
  if (roles.includes(ROLES.RESERVISTS)) {
    return { code: 'RES', name: 'Veteranus', category: 'Reserves' };
  }
  
  // Enlisted - time-based promotion
  if (roles.includes(ROLES.ENLISTED) || roles.includes(ROLES.BASE_47TH)) {
    // Auto-promote to E4 Decanus after 120 days
    if (daysInService >= PROMOTION_DAYS.E4) {
      return { code: 'E4', name: 'Decanus', category: 'Enlisted' };
    }
    // E3 Miles Gregarius after 60 days
    if (daysInService >= PROMOTION_DAYS.E3) {
      return { code: 'E3', name: 'Miles Gregarius', category: 'Enlisted' };
    }
    // E2 Miles after 30 days
    if (daysInService >= PROMOTION_DAYS.E2) {
      return { code: 'E2', name: 'Miles', category: 'Enlisted' };
    }
    // E1 Tiron (new recruit)
    return { code: 'E1', name: 'Tiron', category: 'Enlisted' };
  }
  
  return { code: 'RES', name: 'Veteranus', category: 'Reserves' };
}

// Calculate service time from join date
function calculateServiceTime(joinedAt) {
  const joined = new Date(joinedAt);
  const now = new Date();
  const days = Math.floor((now - joined) / (1000 * 60 * 60 * 24));
  const years = Math.floor(days / 365.25);
  return { days, years };
}

// Get display name
function getDisplayName(member) {
  return member.nick || member.user.global_name || member.user.username;
}

// Check if member should be excluded (bots, alts)
function isExcluded(member) {
  if (member.user.bot) return true;
  if (member.roles.some(r => BOT_ROLES.includes(r))) return true;
  const uname = member.user.username.toLowerCase().replace(/\.$/, ''); // strip trailing period
  if (EXCLUDED_USERNAMES.includes(uname)) return true;
  return false;
}

// Get game affiliations
// Game name to ribbon key mapping
const GAME_RIBBONS = {
  'Helldivers 2': 'helldivers',
  'Fallout 76': 'fallout76',
  'Elder Scrolls Online': 'elder_scrolls',
  'Star Wars: Galaxies': 'swg',
  'Aliens: Fireteam Elite': 'colonial_marines',
  'MechWarrior Online': 'MWO',
};

function getGames(roles) {
  const games = [];
  if (roles.includes(ROLES.HELLDIVERS)) games.push('Helldivers 2');
  if (roles.includes(ROLES.VAULT_DWELLERS)) games.push('Fallout 76');
  if (roles.includes(ROLES.TAMRIELITES)) games.push('Elder Scrolls Online');
  if (roles.includes(ROLES.IMPERIALS)) games.push('Star Wars: Galaxies');
  if (roles.includes(ROLES.COLONIAL_MARINES)) games.push('Aliens: Fireteam Elite');
  if (roles.includes(ROLES.MECHWARRIORS)) games.push('MechWarrior Online');
  return games;
}

// Main processing
function buildRoster() {
  const rawPath = path.join(__dirname, 'data', 'members-raw.json');
  const awardsPath = path.join(__dirname, 'data', 'awards.json');
  
  if (!fs.existsSync(rawPath)) {
    console.error('Run fetch-roster.sh first!');
    process.exit(1);
  }
  
  const rawMembers = JSON.parse(fs.readFileSync(rawPath, 'utf-8'));
  
  // Load existing awards if present
  let awards = {};
  if (fs.existsSync(awardsPath)) {
    awards = JSON.parse(fs.readFileSync(awardsPath, 'utf-8'));
  }
  
  const roster = {
    generated: new Date().toISOString(),
    guildId: '179481732217569280',
    guildName: '47th Legion ComNet',
    members: []
  };
  
  for (const member of rawMembers) {
    // Skip bots and excluded accounts
    if (isExcluded(member)) continue;
    
    const userId = member.user.id;
    const override = MEMBER_OVERRIDES[userId] || {};
    
    // Use override join date if available, otherwise Discord join date
    const effectiveJoinDate = override.historicalJoinDate || member.joined_at;
    const { days: daysInService, years: yearsOfService } = calculateServiceTime(effectiveJoinDate);
    
    // Determine rank (with time-based promotion)
    const rank = determineRank(member.roles, userId, daysInService);
    
    // Get display name (with override support)
    let displayName = override.displayName || getDisplayName(member);
    
    // Get games and generate game ribbons
    const games = getGames(member.roles);
    const gameRibbons = games.map(g => GAME_RIBBONS[g]).filter(Boolean);
    
    // Combine manual awards with auto-generated game ribbons (dedupe)
    const manualAwards = awards[userId] || [];
    const memberAwards = [...new Set([...manualAwards, ...gameRibbons])];
    
    roster.members.push({
      id: userId,
      username: member.user.username,
      displayName,
      avatarUrl: member.user.avatar 
        ? `https://cdn.discordapp.com/avatars/${userId}/${member.user.avatar}.png`
        : null,
      joinedAt: member.joined_at,
      historicalJoinDate: override.historicalJoinDate || null,
      yearsOfService,
      daysInService,
      rank,
      games,
      awards: memberAwards,
      isAgent: member.roles.includes(ROLES.AGENTS),
    });
  }
  
  // Sort by category priority, then by rank (descending), then by years of service
  const categoryOrder = { 'Officers': 0, 'Enlisted': 1, 'Reserves': 2 };
  roster.members.sort((a, b) => {
    const catDiff = categoryOrder[a.rank.category] - categoryOrder[b.rank.category];
    if (catDiff !== 0) return catDiff;
    // Within category, sort by rank code descending (E8 > E6 > E4, O3 > O2 > O1)
    const rankA = parseInt(a.rank.code.slice(1));
    const rankB = parseInt(b.rank.code.slice(1));
    if (rankA !== rankB) return rankB - rankA;
    // Same rank, sort by years of service
    return b.yearsOfService - a.yearsOfService;
  });
  
  // Write output
  const outPath = path.join(__dirname, 'data', 'roster.json');
  fs.writeFileSync(outPath, JSON.stringify(roster, null, 2));
  
  console.log(`Roster built: ${roster.members.length} members`);
  console.log(`  Officers: ${roster.members.filter(m => m.rank.category === 'Officers').length}`);
  console.log(`  Enlisted: ${roster.members.filter(m => m.rank.category === 'Enlisted').length}`);
  console.log(`  Reserves: ${roster.members.filter(m => m.rank.category === 'Reserves').length}`);
}

buildRoster();
