#!/usr/bin/env node
/**
 * 47th Legion Ribbon Sync
 * Checks Discord roles and ADDS ribbons (never removes)
 * 
 * Run: node sync-ribbons.js
 */

const fs = require('fs');
const path = require('path');

const rosterPath = path.join(__dirname, 'data', 'roster.json');
const membersRawPath = path.join(__dirname, 'data', 'members-raw.json');

// Discord Role ID → Ribbon Code mapping
// Only roles that grant campaign/game ribbons
const ROLE_TO_RIBBON = {
  // Active Game Campaigns
  '446695304343846913': 'helldivers',        // Helldivers
  '455749225515319308': 'fallout76',         // Vault Dwellers
  '812939607485841458': 'elder_scrolls',     // Tamrielites
  '471727189025488896': 'dune_awakening',    // Dooners
  '1058087377383477349': 'colonial_marines', // Colonial Marines
  '1249842357919285248': 'MWO',              // Mechwarriors
  '830084560724820000': 'swtor',             // Imperials (SWTOR)
  // '461401559452876820': 'irl_operators',  // No ribbon image yet     // IRL Operators
  
  // Additional Campaigns
  '765592732710076437': 'stars_reach',       // Reachers → Stars Reach
  '765595242736386048': 'war_thunder',       // Thunderites → War Thunder
  
  // NOT mapped (SWG profession roles - not campaign ribbons):
  // Bounty Hunter, Commando, Doctor, Entertainer, Jedi, Merchant, Ranger, Smuggler, Squad Leader
  
  // Service Awards based on roles
  '406136095395545088': 'recruiter',         // Agents → Recruiter ribbon
};

// Load current roster
const roster = JSON.parse(fs.readFileSync(rosterPath, 'utf-8'));

// Load raw Discord member data (has role arrays)
const membersRaw = JSON.parse(fs.readFileSync(membersRawPath, 'utf-8'));

// Build a map of Discord ID → role IDs
const memberRoles = {};
for (const m of membersRaw) {
  if (m.user && m.user.id) {
    memberRoles[m.user.id] = m.roles || [];
  }
}

let totalAdded = 0;
let membersUpdated = 0;

// Process each roster member
for (const member of roster.members) {
  const discordId = member.id;
  const roles = memberRoles[discordId] || [];
  
  // Current awards as a Set
  const currentAwards = new Set(member.awards || []);
  const originalCount = currentAwards.size;
  
  // Check each role for ribbon mappings
  for (const roleId of roles) {
    const ribbon = ROLE_TO_RIBBON[roleId];
    if (ribbon && !currentAwards.has(ribbon)) {
      currentAwards.add(ribbon);
      console.log(`  + ${member.displayName}: added "${ribbon}" ribbon`);
    }
  }
  
  // Only update if we added something (never remove!)
  if (currentAwards.size > originalCount) {
    member.awards = Array.from(currentAwards);
    membersUpdated++;
    totalAdded += (currentAwards.size - originalCount);
  }
}

// Update generated timestamp
roster.generated = new Date().toISOString();

// Write updated roster
fs.writeFileSync(rosterPath, JSON.stringify(roster, null, 2));

console.log('');
console.log(`✅ Ribbon sync complete:`);
console.log(`   ${totalAdded} ribbons added to ${membersUpdated} members`);
console.log(`   Roster saved to ${rosterPath}`);
