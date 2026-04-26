// Enhanced KnowItNow JS - Premium Interactions 2024 (FULLY FIXED)

// Theme System (Enhanced)
const MODE_TOGGLE = document.getElementById('mode-toggle');
const BODY = document.documentElement;
let currentTheme = localStorage.getItem('theme') || 'light';

function setTheme(theme) {
  BODY.setAttribute('data-theme', theme);
  localStorage.setItem('theme', theme);
  const sunIcon = MODE_TOGGLE?.querySelector('.sun-icon');
  const moonIcon = MODE_TOGGLE?.querySelector('.moon-icon');
  if (sunIcon) sunIcon.style.opacity = theme === 'dark' ? '1' : '0.3';
  if (moonIcon) moonIcon.style.opacity = theme === 'light' ? '1' : '0.3';
}

// Server-client sync + init
document.addEventListener('DOMContentLoaded', () => {
  setTheme(currentTheme);
  
  // Particles.js background (Safe)
  if (typeof particlesJS !== 'undefined') {
    particlesJS('particles-js', {
      particles: {
        number: { value: 80, density: { enable: true, value_area: 800 } },
        color: { value: ['#3b82f6', '#ec4899', '#10b981', '#f59e0b'] },
        shape: { type: 'circle' },
        opacity: { value: 0.3, random: true },
        size: { value: 3, random: true },
        line_linked: { enable: true, distance: 150, color: '#3b82f6', opacity: 0.2, width: 1 },
        move: { enable: true, speed: 2, direction: 'none', random: true }
      },
      interactivity: {
        events: { onhover: { enable: true, mode: 'repulse' }, onclick: { enable: true, mode: 'push' } },
        modes: { repulse: { distance: 100 }, push: { particles_nb: 4 } }
      },
      retina_detect: true
    });
  }

  // GSAP Magic (Safe)
  if (typeof gsap !== 'undefined') {
    gsap.registerPlugin(TextPlugin);
    
    // Hero animations (FIXED)
    const timeline = gsap.timeline();
    const logo = document.getElementById('hero-logo');
    if (logo) timeline.from(logo, { scale: 0.3, rotation: -180, duration: 1.5, ease: 'back.out(1.7)' });
    
    const fadeElements = document.querySelectorAll('.animate-fade-in-up');
    timeline.from(fadeElements, {
      opacity: 0,
      y: 80,
      duration: 1.2,
      stagger: 0.2,
      ease: 'power3.out'
    }, '-=1');

    // Continuous floating elements (FIXED selector)
    gsap.utils.toArray('.group').forEach((el, i) => {
      gsap.to(el, {
        y: -10,
        rotationY: 5,
        duration: 4,
        repeat: -1,
        yoyo: true,
        ease: 'sine.inOut',
        delay: i * 0.1
      });
    });

    // Mouse parallax
    document.addEventListener('mousemove', e => {
      const mouseX = e.clientX / window.innerWidth;
      const mouseY = e.clientY / window.innerHeight;
      const hero = document.getElementById('hero');
      if (hero) {
        gsap.to(hero, { x: mouseX * 20, y: mouseY * 20, duration: 1, ease: 'power2.out' });
      }
    });
  }

  // Stats counters (COMPLETELY FIXED)
  const counters = document.querySelectorAll('[data-target]');
  const statsObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        counters.forEach(counter => {
          const target = parseInt(counter.getAttribute('data-target'));
          let current = 0;
          const increment = target / 100;
          const updateCounter = () => {
            current += increment;
            if (current >= target) {
              counter.textContent = target + (target > 100 ? '' : '%');
            } else {
              counter.textContent = Math.floor(current) + (target > 100 ? '' : '%');
              requestAnimationFrame(updateCounter);
            }
          };
          updateCounter();
        });
        statsObserver.unobserve(entry.target);
      }
    });
  });

  const statsSection = document.querySelector('.py-24');
  if (statsSection) statsObserver.observe(statsSection);

  // AOS Premium (Safe)
  if (typeof AOS !== 'undefined') {
    AOS.init({
      duration: 1200,
      easing: 'cubic-bezier(0.175, 0.885, 0.32, 1.275)',
      once: false,
      mirror: true,
      offset: 80
    });
  }

  // Contact Form Enhanced (Safe)
  const contactForm = document.getElementById('contact-form');
  if (contactForm) {
    contactForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const formData = new FormData(contactForm);
      const data = Object.fromEntries(formData);
      
      const btn = contactForm.querySelector('button[type="submit"]');
      const original = btn.innerHTML;
      btn.innerHTML = 'Sending... ⏳';
      btn.disabled = true;

      try {
        // Demo response (replace with real endpoint)
        await new Promise(resolve => setTimeout(resolve, 1500));
        if (typeof gsap !== 'undefined') {
          gsap.to(contactForm, { scale: 0.95, duration: 0.15, yoyo: true, repeat: 1 });
        }
        alert('Thank you! Your message has been sent. 🚀');
        contactForm.reset();
      } catch (error) {
        alert('Please try again.');
      } finally {
        btn.innerHTML = original;
        btn.disabled = false;
      }
    });
  }

  // Smooth scrolling + nav highlight
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', e => {
      e.preventDefault();
      const target = document.querySelector(anchor.getAttribute('href'));
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });

  // Navbar scroll effect (IMPROVED)
  let lastScroll = 0;
  const nav = document.querySelector('nav');
  window.addEventListener('scroll', () => {
    const current = window.scrollY;
    if (current > lastScroll && current > 100) {
      if (nav) nav.style.transform = 'translateY(-100%)';
    } else {
      if (nav) nav.style.transform = 'translateY(0)';
    }
    lastScroll = current;
  });

  // Mode toggle enhanced (FIXED)
  if (MODE_TOGGLE) {
    MODE_TOGGLE.addEventListener('click', () => {
      currentTheme = currentTheme === 'dark' ? 'light' : 'dark';
      setTheme(currentTheme);
      if (typeof gsap !== 'undefined') {
        gsap.to(MODE_TOGGLE, { scale: 1.2, rotation: 360, duration: 0.6, ease: 'back.out(1.7)' });
      }
    });
  }

  // Hamburger menu (COMPLETELY FIXED)
  const hamburger = document.querySelector('.hamburger-btn');
  const mobileMenu = document.querySelector('.mobile-menu');
  if (hamburger && mobileMenu) {
    hamburger.addEventListener('click', () => {
      hamburger.classList.toggle('active');
      mobileMenu.classList.toggle('hidden');
    });
  }

  // Close mobile menu on outside click
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.hamburger-btn') && !e.target.closest('.mobile-menu')) {
      mobileMenu?.classList.add('hidden');
      hamburger?.classList.remove('active');
    }
  });

  // Intersection animations for sections
  const sectionObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.style.opacity = '1';
        entry.target.style.transform = 'translateY(0)';
        entry.target.style.transition = 'all 0.8s ease';
      }
    });
  });
  document.querySelectorAll('section').forEach(section => {
    section.style.opacity = '0';
    section.style.transform = 'translateY(50px)';
    sectionObserver.observe(section);
  });
});

// Preloader
window.addEventListener('load', () => {
  document.body.classList.add('loaded');
  const preloader = document.querySelector('.preloader');
  if (preloader) {
    preloader.style.opacity = '0';
    preloader.style.visibility = 'hidden';
  }
});