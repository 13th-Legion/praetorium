#!/usr/bin/env node
/**
 * Merge historical vBulletin user data with Discord roster
 * Uses historical join dates where usernames match
 */

const fs = require('fs');
const path = require('path');

// Load data
const historicalPath = path.join(__dirname, 'data', 'historical_users.json');
const rosterPath = path.join(__dirname, 'data', 'roster.json');

const historical = JSON.parse(fs.readFileSync(historicalPath, 'utf-8'));
const roster = JSON.parse(fs.readFileSync(rosterPath, 'utf-8'));

console.log(`Historical users: ${historical.length}`);
console.log(`Discord members: ${roster.members.length}`);

// Build lookup by username (lowercase)
const historicalByName = {};
for (const user of historical) {
  const name = user.username.toLowerCase();
  // Keep the earliest entry if duplicate usernames
  if (!historicalByName[name] || parseInt(user.joindate) < parseInt(historicalByName[name].joindate)) {
    historicalByName[name] = user;
  }
}

// Match Discord members to historical records
let matched = 0;
let unmatched = 0;

for (const member of roster.members) {
  // Try to match by display name or username
  const tryNames = [
    member.displayName.toLowerCase(),
    member.username.toLowerCase(),
    // Try first word of display name (for nicknames like "SGR Mareth Lightfoot")
    member.displayName.split(' ').pop().toLowerCase(),
  ];
  
  let histUser = null;
  for (const name of tryNames) {
    if (historicalByName[name]) {
      histUser = historicalByName[name];
      break;
    }
  }
  
  if (histUser) {
    // Use historical join date
    const histJoinDate = new Date(parseInt(histUser.joindate) * 1000);
    member.historicalJoinDate = histJoinDate.toISOString();
    member.forumUsername = histUser.username;
    member.forumPosts = parseInt(histUser.posts) || 0;
    
    // Recalculate years of service from historical date
    const now = new Date();
    member.yearsOfService = Math.floor((now - histJoinDate) / (1000 * 60 * 60 * 24 * 365.25));
    
    matched++;
  } else {
    unmatched++;
  }
}

console.log(`\nMatched: ${matched}`);
console.log(`Unmatched: ${unmatched}`);

// Re-sort by category, then rank (descending), then years of service
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

// Update generation timestamp
roster.generated = new Date().toISOString();
roster.historicalDataMerged = true;

// Save updated roster
fs.writeFileSync(rosterPath, JSON.stringify(roster, null, 2));
console.log('\nRoster updated with historical data!');

// Show matched members with longest service
console.log('\nTop 15 by historical service:');
const byService = [...roster.members].sort((a, b) => b.yearsOfService - a.yearsOfService);
for (const m of byService.slice(0, 15)) {
  const hist = m.historicalJoinDate ? new Date(m.historicalJoinDate).toISOString().slice(0, 10) : 'Discord only';
  console.log(`  ${m.displayName.padEnd(25)} ${m.yearsOfService}yr  (${hist})`);
}
