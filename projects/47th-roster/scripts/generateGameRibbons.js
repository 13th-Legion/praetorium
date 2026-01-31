#!/usr/bin/env node
/**
 * Generate game campaign ribbons
 * Style matches existing 47th Legion ribbons
 */

const { createCanvas } = require('canvas');
const fs = require('fs');
const path = require('path');

// Ribbon dimensions (matches existing ribbons)
const WIDTH = 72;
const HEIGHT = 28;

// Game ribbon designs - stripes from left to right
const RIBBONS = {
  'helldivers': {
    name: 'Helldivers 2',
    // Super Earth colors: black, yellow, black with white accents
    stripes: [
      { color: '#1a1a1a', width: 8 },
      { color: '#FFD700', width: 6 },
      { color: '#1a1a1a', width: 14 },
      { color: '#FFFFFF', width: 4 },
      { color: '#FFD700', width: 8 },
      { color: '#FFFFFF', width: 4 },
      { color: '#1a1a1a', width: 14 },
      { color: '#FFD700', width: 6 },
      { color: '#1a1a1a', width: 8 },
    ]
  },
  'fallout76': {
    name: 'Fallout 76',
    // Vault-Tec colors: blue and yellow
    stripes: [
      { color: '#0A3161', width: 10 },
      { color: '#FFD700', width: 8 },
      { color: '#0A3161', width: 8 },
      { color: '#FFD700', width: 4 },
      { color: '#0A3161', width: 12 },
      { color: '#FFD700', width: 4 },
      { color: '#0A3161', width: 8 },
      { color: '#FFD700', width: 8 },
      { color: '#0A3161', width: 10 },
    ]
  },
  'swg': {
    name: 'Star Wars Galaxies',
    // Imperial/Rebel mix: red, black, gray, blue accents
    stripes: [
      { color: '#8B0000', width: 10 },
      { color: '#1a1a1a', width: 6 },
      { color: '#4a4a4a', width: 8 },
      { color: '#1a1a1a', width: 4 },
      { color: '#C0C0C0', width: 8 },
      { color: '#1a1a1a', width: 4 },
      { color: '#4a4a4a', width: 8 },
      { color: '#1a1a1a', width: 6 },
      { color: '#8B0000', width: 10 },
    ]
  },
  'colonial_marines': {
    name: 'Aliens: Fireteam Elite',
    // Colonial Marines: olive drab, black, orange accents
    stripes: [
      { color: '#556B2F', width: 12 },
      { color: '#1a1a1a', width: 4 },
      { color: '#FF6600', width: 6 },
      { color: '#1a1a1a', width: 8 },
      { color: '#556B2F', width: 12 },
      { color: '#1a1a1a', width: 8 },
      { color: '#FF6600', width: 6 },
      { color: '#1a1a1a', width: 4 },
      { color: '#556B2F', width: 12 },
    ]
  },
};

function generateRibbon(key, config) {
  const canvas = createCanvas(WIDTH, HEIGHT);
  const ctx = canvas.getContext('2d');
  
  // Draw stripes
  let x = 0;
  for (const stripe of config.stripes) {
    ctx.fillStyle = stripe.color;
    ctx.fillRect(x, 0, stripe.width, HEIGHT);
    x += stripe.width;
  }
  
  // Add subtle border
  ctx.strokeStyle = 'rgba(0, 0, 0, 0.3)';
  ctx.lineWidth = 1;
  ctx.strokeRect(0.5, 0.5, WIDTH - 1, HEIGHT - 1);
  
  // Add subtle highlight at top
  const gradient = ctx.createLinearGradient(0, 0, 0, 6);
  gradient.addColorStop(0, 'rgba(255, 255, 255, 0.2)');
  gradient.addColorStop(1, 'rgba(255, 255, 255, 0)');
  ctx.fillStyle = gradient;
  ctx.fillRect(1, 1, WIDTH - 2, 6);
  
  // Add subtle shadow at bottom
  const shadowGradient = ctx.createLinearGradient(0, HEIGHT - 6, 0, HEIGHT);
  shadowGradient.addColorStop(0, 'rgba(0, 0, 0, 0)');
  shadowGradient.addColorStop(1, 'rgba(0, 0, 0, 0.15)');
  ctx.fillStyle = shadowGradient;
  ctx.fillRect(1, HEIGHT - 6, WIDTH - 2, 6);
  
  return canvas;
}

// Generate all ribbons
const outputDir = path.join(__dirname, '../assets/ribbons');

for (const [key, config] of Object.entries(RIBBONS)) {
  const canvas = generateRibbon(key, config);
  const buffer = canvas.toBuffer('image/png');
  const filepath = path.join(outputDir, `${key}.png`);
  
  fs.writeFileSync(filepath, buffer);
  console.log(`Generated ${key}.png (${config.name})`);
}

console.log('\nDone! Game ribbons generated.');
