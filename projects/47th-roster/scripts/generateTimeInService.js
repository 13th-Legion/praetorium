const { createCanvas } = require('canvas');
const fs = require('fs');
const path = require('path');

// Ribbon dimensions
const WIDTH = 80;
const HEIGHT = 20;

// Colors
const RED_FILL = '#8B0000'; // Dark red
const GOLD_BORDER = '#FFD700'; // Gold
const SILVER_BORDER = '#C0C0C0'; // Silver
const BACKGROUND = 'transparent';

// Stripe dimensions
const STRIPE_WIDTH = 12;
const STRIPE_HEIGHT = 16;
const BORDER_WIDTH = 2;
const STRIPE_SPACING = 3;

function generateYearRibbon(years) {
    const canvas = createCanvas(WIDTH, HEIGHT);
    const ctx = canvas.getContext('2d');
    
    // Transparent background
    ctx.clearRect(0, 0, WIDTH, HEIGHT);
    
    // Calculate stripes: gold = 5 years, silver = 1 year
    const goldStripes = Math.floor(years / 5);
    const silverStripes = years % 5;
    const totalStripes = goldStripes + silverStripes;
    
    if (totalStripes === 0) return canvas;
    
    // Calculate total width needed and center the stripes
    const totalStripesWidth = totalStripes * STRIPE_WIDTH + (totalStripes - 1) * STRIPE_SPACING;
    let startX = (WIDTH - totalStripesWidth) / 2;
    
    // Draw gold stripes first (representing 5 years each)
    for (let i = 0; i < goldStripes; i++) {
        drawStripe(ctx, startX, GOLD_BORDER);
        startX += STRIPE_WIDTH + STRIPE_SPACING;
    }
    
    // Draw silver stripes (representing 1 year each)
    for (let i = 0; i < silverStripes; i++) {
        drawStripe(ctx, startX, SILVER_BORDER);
        startX += STRIPE_WIDTH + STRIPE_SPACING;
    }
    
    return canvas;
}

function drawStripe(ctx, x, borderColor) {
    const y = (HEIGHT - STRIPE_HEIGHT) / 2;
    
    // Draw border
    ctx.fillStyle = borderColor;
    ctx.fillRect(x, y, STRIPE_WIDTH, STRIPE_HEIGHT);
    
    // Draw red fill inside
    ctx.fillStyle = RED_FILL;
    ctx.fillRect(
        x + BORDER_WIDTH, 
        y + BORDER_WIDTH, 
        STRIPE_WIDTH - BORDER_WIDTH * 2, 
        STRIPE_HEIGHT - BORDER_WIDTH * 2
    );
}

// Generate ribbons for 1-25 years (covers up to 25 years of service)
const outputDir = path.join(__dirname, '../assets/timeInService');

// Ensure directory exists
if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
}

for (let year = 1; year <= 25; year++) {
    const canvas = generateYearRibbon(year);
    const buffer = canvas.toBuffer('image/png');
    const filename = `${String(year).padStart(2, '0')}_year.png`;
    const filepath = path.join(outputDir, filename);
    
    fs.writeFileSync(filepath, buffer);
    
    const goldStripes = Math.floor(year / 5);
    const silverStripes = year % 5;
    console.log(`Generated ${filename}: ${goldStripes} gold + ${silverStripes} silver stripes`);
}

console.log('\nDone! All year-in-service ribbons generated.');
