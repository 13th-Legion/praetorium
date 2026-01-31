#!/usr/bin/env node
/**
 * Auto-assign awards based on service time and game affiliations
 */

const fs = require('fs');
const path = require('path');

const rosterPath = path.join(__dirname, 'data', 'roster.json');
const awardsPath = path.join(__dirname, 'data', 'awards.json');

const roster = JSON.parse(fs.readFileSync(rosterPath, 'utf-8'));

// Load existing awards or start fresh
let awards = {};
if (fs.existsSync(awardsPath)) {
  awards = JSON.parse(fs.readFileSync(awardsPath, 'utf-8'));
}

// Award mappings
const GAME_RIBBONS = {
  'Helldivers 2': 'helldivers',
  'Elder Scrolls Online': 'elder_scrolls',
  'Star Wars: Galaxies': 'swg',
  'Fallout 76': 'fallout76',
  'MechWarrior Online': 'MWO',
  'Star Citizen': 'star_citizen',
  'Aliens: Fireteam': 'colonial_marines',
  'IRL': 'irl_operators',
};

// Cavadus (CO) has played all games the 47th has played
const CAVADUS_ID = '179481162710908928';
const ALL_GAME_RIBBONS = Object.values(GAME_RIBBONS);

function assignAwards(member) {
  const memberAwards = new Set(awards[member.id] || []);
  
  // Service ribbon for everyone
  memberAwards.add('service');
  
  // Good conduct for 2+ years
  if (member.yearsOfService >= 2) {
    memberAwards.add('good_conduct');
  }
  
  // Achievement for 5+ years
  if (member.yearsOfService >= 5) {
    memberAwards.add('achievement');
  }
  
  // Commendation for 10+ years
  if (member.yearsOfService >= 10) {
    memberAwards.add('commendation');
  }
  
  // Corona Aurea (golden crown) for 15+ years - founding members
  if (member.yearsOfService >= 15) {
    memberAwards.add('corona_aurea');
  }
  
  // NCO Development for NCOs and above
  if (['NCOs', 'Officers'].includes(member.rank.category)) {
    memberAwards.add('nco_development');
  }
  
  // Organizational Excellence for Officers
  if (member.rank.category === 'Officers') {
    memberAwards.add('organizational_excellence');
  }
  
  // Game-specific ribbons
  // Cavadus gets all game ribbons (has played everything)
  if (member.id === CAVADUS_ID) {
    for (const ribbon of ALL_GAME_RIBBONS) {
      memberAwards.add(ribbon);
    }
  } else {
    for (const game of member.games || []) {
      const ribbon = GAME_RIBBONS[game];
      if (ribbon) {
        memberAwards.add(ribbon);
      }
    }
  }
  
  // Joint Training for members in 3+ games
  if ((member.games || []).length >= 3) {
    memberAwards.add('joint_training');
  }
  
  // Recruiter badge for Agents
  if (member.isAgent) {
    memberAwards.add('recruiter');
  }
  
  return Array.from(memberAwards);
}

// Process all members
let totalAwarded = 0;
for (const member of roster.members) {
  const memberAwards = assignAwards(member);
  if (memberAwards.length > 0) {
    awards[member.id] = memberAwards;
    totalAwarded += memberAwards.length;
  }
}

// Save awards
fs.writeFileSync(awardsPath, JSON.stringify(awards, null, 2));

// Rebuild roster with awards
for (const member of roster.members) {
  member.awards = awards[member.id] || [];
}
fs.writeFileSync(rosterPath, JSON.stringify(roster, null, 2));

console.log(`Awards assigned!`);
console.log(`Total ribbons awarded: ${totalAwarded}`);
console.log(`Members with awards: ${Object.keys(awards).length}`);

// Show sample
console.log('\nSample awards:');
for (const member of roster.members.slice(0, 10)) {
  console.log(`  ${member.displayName.padEnd(25)} ${member.awards.join(', ')}`);
}
