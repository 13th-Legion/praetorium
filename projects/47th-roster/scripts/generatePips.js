#!/usr/bin/env node
/**
 * Generate time-in-service pip images
 * Gold pip = 5 years, Silver pip = 1 year
 */

const { createCanvas } = require('canvas');
const fs = require('fs');
const path = require('path');

// Pip dimensions - tight, no padding
const WIDTH = 12;
const HEIGHT = 16;
const BORDER = 2;

function generatePip(borderColor, fillColor) {
  const canvas = createCanvas(WIDTH, HEIGHT);
  const ctx = canvas.getContext('2d');
  
  // Draw border
  ctx.fillStyle = borderColor;
  ctx.fillRect(0, 0, WIDTH, HEIGHT);
  
  // Draw fill
  ctx.fillStyle = fillColor;
  ctx.fillRect(BORDER, BORDER, WIDTH - BORDER * 2, HEIGHT - BORDER * 2);
  
  return canvas;
}

const outputDir = path.join(__dirname, '../assets/timeInService');

// Gold pip (5 years)
const goldPip = generatePip('#FFD700', '#8B0000');
fs.writeFileSync(path.join(outputDir, 'pip_5year.png'), goldPip.toBuffer('image/png'));
console.log('Generated pip_5year.png (gold border)');

// Silver pip (1 year)
const silverPip = generatePip('#C0C0C0', '#8B0000');
fs.writeFileSync(path.join(outputDir, 'pip_1year.png'), silverPip.toBuffer('image/png'));
console.log('Generated pip_1year.png (silver border)');

console.log('\nDone! Pips are now 12x16 pixels with no padding.');
