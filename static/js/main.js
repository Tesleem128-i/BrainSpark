// Enhanced KnowItNow JS - Premium Interactions 2024

// Theme System (Enhanced)
const MODE_TOGGLE = document.getElementById('mode-toggle');
const BODY = document.documentElement;
let currentTheme = localStorage.getItem('theme') || 'light';

function setTheme(theme) {
  BODY.setAttribute('data-theme', theme);
  localStorage.setItem('theme', theme);
  MODE_TOGGLE.querySelector('.mode-text').textContent = theme === 'dark' ? 'Light Mode' : 'Dark Mode';
  MODE_TOGGLE.querySelector('.sun-icon').style.opacity = theme === 'dark' ? '1' : '0.3';
  MODE_TOGGLE.querySelector('.moon-icon').style.opacity = theme === 'light' ? '1' : '0.3';
}

// Server-client sync + init
document.addEventListener('DOMContentLoaded', () => {
  setTheme(currentTheme);
  
  // Particles.js background
  if (particlesJS) {
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

  // GSAP Magic
  gsap.registerPlugin(TextPlugin);
  
  // Hero animations
  gsap.timeline()
    .from('#hero-logo', { scale: 0.3, rotation: -180, duration: 1.5, ease: 'back.out(1.7)' })
    .to('.hero-title', { text: 'KnowItNow', duration: 1.5, ease: 'none' }, '-=1')
    .from('.animate-fade-in-up', {
      opacity: 0,
      y: 80,
      duration: 1.2,
      stagger: 0.2,
      ease: 'power3.out'
    }, '-=1');

  // Continuous floating elements
  gsap.to('.group', {
    y: -10,
    rotationY: 5,
    duration: 4,
    repeat: -1,
    yoyo: true,
    stagger: 0.3,
    ease: 'sine.inOut'
  });

  // Mouse parallax
  document.addEventListener('mousemove', e => {
    const mouseX = e.clientX / window.innerWidth;
    const mouseY = e.clientY / window.innerHeight;
    gsap.to('#hero', {
      x: mouseX * 20,
      y: mouseY * 20,
      duration: 1,
      ease: 'power2.out'
    });
  });

  // Stats counters
  const counters = document.querySelectorAll('[data-target]');
  const animateCounters = () => {
    counters.forEach(counter => {
      const target = +counter.getAttribute('data-target');
      const count = +counter.innerText.replace(/,/g, '');
      const increment = target / 100;
      const timer = setInterval(() => {
        counter.innerText = Math.ceil(count + increment).toLocaleString();
        if (count >= target) clearInterval(timer);
      }, 30);
    });
  };

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        animateCounters();
        observer.unobserve(entry.target);
      }
    });
  });

  observer.observe(document.querySelector('.py-24')); // Stats section

  // AOS Premium
  AOS.init({
    duration: 1200,
    easing: 'cubic-bezier(0.175, 0.885, 0.32, 1.275)',
    once: false,
    mirror: true,
    offset: 80
  });

  // Contact Form Enhanced
  const contactForm = document.getElementById('contact-form');
  contactForm?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(contactForm);
    const data = Object.fromEntries(formData);
    
    // Loading state
    const btn = contactForm.querySelector('button[type="submit"]');
    const original = btn.innerHTML;
    btn.innerHTML = 'Sending... ⏳';
    btn.disabled = true;

    try {
      const response = await fetch('/send_email', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      });
      
      const result = await response.json();
      // Success animation
      gsap.to(contactForm, { scale: 0.95, duration: 0.15, yoyo: true, repeat: 1 });
      alert(result.message || 'Success!');
      contactForm.reset();
    } catch (error) {
      alert('Connection error. Please try again.');
    } finally {
      btn.innerHTML = original;
      btn.disabled = false;
    }
  });

  // Smooth scrolling + nav highlight
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', e => {
      e.preventDefault();
      const target = document.querySelector(anchor.getAttribute('href'));
      target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });

  // Navbar scroll effect
  let lastScroll = 0;
  window.addEventListener('scroll', () => {
    const current = window.scrollY;
    if (current > lastScroll && current > 100) {
      document.querySelector('nav').style.transform = 'translateY(-100%)';
    } else {
      document.querySelector('nav').style.transform = 'translateY(0)';
    }
    lastScroll = current;
  });

  // Mode toggle enhanced
  MODE_TOGGLE.addEventListener('click', () => {
    currentTheme = currentTheme === 'dark' ? 'light' : 'dark';
    setTheme(currentTheme);
    // Glow effect
    gsap.to(MODE_TOGGLE, { scale: 1.2, rotation: 360, duration: 0.6, ease: 'back.out(1.7)' });
  });

  // Hamburger menu
  const hamburger = document.querySelector('.hamburger-btn');
  const navItems = document.querySelector('.md\\:flex');
  hamburger?.addEventListener('click', () => {
    hamburger.classList.toggle('active');
    navItems.classList.toggle('hidden');
  });

  // Intersection animations
  const sectionObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.style.opacity = '1';
        entry.target.style.transform = 'translateY(0)';
      }
    });
  });
  document.querySelectorAll('section').forEach(section => {
    section.style.opacity = '0';
    section.style.transform = 'translateY(50px)';
    sectionObserver.observe(section);
  });
});

// Study Buddies page-specific handlers
if (window.location.pathname.includes('/study-buddies')) {
  // Auto-load first tab content
  setTimeout(() => {
    if (document.querySelector('.tab-btn[data-tab="find-buddies"]')) {
      document.querySelector('.tab-btn[data-tab="find-buddies"]').click();
    }
  }, 100);
}

// Preloader (optional)
window.addEventListener('load', () => {
  document.body.classList.add('loaded');
});

// Notification bell polling (every 30s)
setInterval(() => {
  if (window.location.pathname.includes('/study-buddies') && document.querySelector('#notification-bell')) {
    checkNotifications();
  }
}, 30000);

async function checkNotifications() {
  try {
    const response = await fetch('/api/get-unread-notifications');
    const data = await response.json();
    const bell = document.querySelector('#notification-bell');
    if (bell && data.total_notifications > 0) {
      bell.querySelector('.badge').textContent = data.total_notifications;
      bell.querySelector('.badge').classList.remove('hidden');
    }
  } catch (e) {
    console.log('Notification check failed:', e);
  }
}

