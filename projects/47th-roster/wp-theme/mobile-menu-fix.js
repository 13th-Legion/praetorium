/**
 * 47th Legion - Mobile Menu Toggle Fix
 * Fixes Astra mobile menu dropdown toggle behavior
 */
(function() {
  'use strict';
  
  document.addEventListener('DOMContentLoaded', function() {
    const menuToggle = document.querySelector('.menu-toggle, .main-header-menu-toggle');
    const mobileContent = document.querySelector('.ast-mobile-header-content');
    
    if (!menuToggle || !mobileContent) return;
    
    // Add click handler for menu toggle
    menuToggle.addEventListener('click', function(e) {
      e.preventDefault();
      e.stopPropagation();
      
      const isExpanded = this.getAttribute('aria-expanded') === 'true';
      
      // Toggle aria-expanded
      this.setAttribute('aria-expanded', !isExpanded);
      this.classList.toggle('toggled', !isExpanded);
      
      // Toggle body class
      document.body.classList.toggle('ast-main-header-nav-open', !isExpanded);
      
      // Toggle menu visibility
      if (isExpanded) {
        mobileContent.style.display = 'none';
      } else {
        mobileContent.style.display = 'block';
      }
    });
    
    // Close menu when clicking outside
    document.addEventListener('click', function(e) {
      if (!menuToggle.contains(e.target) && !mobileContent.contains(e.target)) {
        if (menuToggle.getAttribute('aria-expanded') === 'true') {
          menuToggle.setAttribute('aria-expanded', 'false');
          menuToggle.classList.remove('toggled');
          document.body.classList.remove('ast-main-header-nav-open');
          mobileContent.style.display = 'none';
        }
      }
    });
    
    // Close menu on escape key
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && menuToggle.getAttribute('aria-expanded') === 'true') {
        menuToggle.setAttribute('aria-expanded', 'false');
        menuToggle.classList.remove('toggled');
        document.body.classList.remove('ast-main-header-nav-open');
        mobileContent.style.display = 'none';
      }
    });
  });
})();
