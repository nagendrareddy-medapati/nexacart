/* ── Nexacart Main JS ── */

/* Password toggle */
function togglePw(id, btn) {
  const inp = document.getElementById(id);
  if (!inp) return;
  const isText = inp.type === 'text';
  inp.type = isText ? 'password' : 'text';
  btn.innerHTML = isText
    ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>'
    : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
}

/* Profile dropdown */
function toggleProfile() {
  const d = document.getElementById('profileDropdown');
  if (d) d.classList.toggle('open');
}
document.addEventListener('click', e => {
  const pm = document.querySelector('.profile-menu');
  if (pm && !pm.contains(e.target)) {
    const d = document.getElementById('profileDropdown');
    if (d) d.classList.remove('open');
  }
});

/* Catbar scroll arrows */
function scrollCatbar(dir) {
  const s = document.getElementById('catbarScroll');
  if (s) s.scrollBy({ left: dir, behavior: 'smooth' });
}
(function initCatbarArrows() {
  const wrap = document.querySelector('.catbar-wrap');
  const scroll = document.getElementById('catbarScroll');
  const left = document.querySelector('.catbar-arrow-left');
  const right = document.querySelector('.catbar-arrow-right');
  if (!wrap || !scroll) return;
  const update = () => {
    if (left)  left.style.opacity  = scroll.scrollLeft > 10 ? '1' : '0';
    if (right) right.style.opacity = scroll.scrollLeft < scroll.scrollWidth - scroll.clientWidth - 10 ? '1' : '0';
  };
  scroll.addEventListener('scroll', update);
  window.addEventListener('resize', update);
  setTimeout(update, 200);
})();

/* Live search suggestions */
(function initSearch() {
  const forms = document.querySelectorAll('.nav-search-form');
  forms.forEach(form => {
    const input = form.querySelector('.nav-search-input');
    if (!input) return;
    const dropdown = document.createElement('div');
    dropdown.className = 'search-dropdown';
    form.style.position = 'relative';
    form.appendChild(dropdown);
    let timer;
    input.addEventListener('input', () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2) { dropdown.classList.remove('open'); return; }
      timer = setTimeout(async () => {
        try {
          const res = await fetch('/api/search?q=' + encodeURIComponent(q));
          const data = await res.json();
          if (!data.length) { dropdown.classList.remove('open'); return; }
          dropdown.innerHTML = data.map(p =>
            `<a href="/product/${p.id}" class="sd-item">
               <span class="sd-name">${p.name}</span>
               <span class="sd-cat">${p.category}</span>
               <span class="sd-price">₹${Number(p.price).toLocaleString('en-IN',{maximumFractionDigits:0})}</span>
             </a>`
          ).join('');
          dropdown.classList.add('open');
        } catch(e) { dropdown.classList.remove('open'); }
      }, 250);
    });
    document.addEventListener('click', e => {
      if (!form.contains(e.target)) dropdown.classList.remove('open');
    });
    input.addEventListener('keydown', e => {
      if (e.key === 'Escape') dropdown.classList.remove('open');
    });
  });
})();

/* Filter panel helpers */
function setPrice(mn, mx) {
  const minEl = document.querySelector('[name=min_price]');
  const maxEl = document.querySelector('[name=max_price]');
  if (minEl) minEl.value = mn;
  if (maxEl) maxEl.value = mx;
  const form = document.getElementById('filterForm');
  if (form) form.submit();
}
function toggleBrands(btn) {
  const extra = btn.nextElementSibling;
  const hidden = extra.style.display === 'none';
  extra.style.display = hidden ? 'block' : 'none';
  btn.textContent = hidden ? '− Show less' : `+ ${extra.querySelectorAll('.ff-check').length} more`;
}
function toggleMobFilter() {
  const el = document.getElementById('cfsFilters');
  if (el) el.classList.toggle('mob-open');
}

/* Auto-scroll to filter results */
document.addEventListener('DOMContentLoaded', () => {
  const sec = document.getElementById('catProductsSection');
  if (sec) setTimeout(() => sec.scrollIntoView({ behavior: 'smooth', block: 'start' }), 200);
});

/* Location (home page) */
function requestLocation() {
  const btn = document.querySelector('.loc-refresh');
  if (btn) btn.textContent = 'Locating…';
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(async pos => {
    try {
      const { latitude: lat, longitude: lon } = pos.coords;
      const r = await fetch(`https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json`);
      const d = await r.json();
      const city = d.address?.city || d.address?.town || d.address?.village || '';
      const state = d.address?.state || '';
      const bar = document.getElementById('locationBar');
      const txt = document.getElementById('locationText');
      if (bar && txt) {
        txt.textContent = [city, state].filter(Boolean).join(', ') || 'Your location';
        bar.style.display = 'flex';
      }
      if (btn) btn.textContent = 'Update';
    } catch(e) { if (btn) btn.textContent = 'Update'; }
  }, () => { if (btn) btn.textContent = 'Update'; });
}

/* Toast notification */
function showToast(msg, type='success') {
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.classList.add('show'), 50);
  setTimeout(() => { t.classList.remove('show'); setTimeout(()=>t.remove(),400); }, 3000);
}

/* Auto-dismiss flash messages after 4s */
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.flash-msg').forEach(el => {
    setTimeout(() => { el.style.opacity='0'; setTimeout(()=>el.remove(),400); }, 4000);
  });
});


/* ════════════════════
   AI CHATBOT
════════════════════ */
let chatOpen = false;
let chatHistory = [];
let chatFirstOpen = true;

function toggleChat() {
  chatOpen = !chatOpen;
  const win  = document.getElementById('chatWindow');
  const ico1 = document.getElementById('chatIconOpen');
  const ico2 = document.getElementById('chatIconClose');
  const badge= document.getElementById('chatUnread');
  if (!win) return;
  if (chatOpen) {
    win.style.display = 'flex';
    win.style.flexDirection = 'column';
    ico1.style.display = 'none';
    ico2.style.display = 'block';
    if (badge) badge.style.display = 'none';
    if (chatFirstOpen) { chatFirstOpen = false; }
    setTimeout(() => {
      const inp = document.getElementById('chatInput');
      if (inp) inp.focus();
      scrollChatBottom();
    }, 100);
  } else {
    win.style.display = 'none';
    ico1.style.display = 'block';
    ico2.style.display = 'none';
  }
}

function scrollChatBottom() {
  const msgs = document.getElementById('chatMessages');
  if (msgs) msgs.scrollTop = msgs.scrollHeight;
}

function addChatMsg(role, text) {
  const msgs = document.getElementById('chatMessages');
  if (!msgs) return;
  const now  = new Date().toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'});
  const div  = document.createElement('div');
  div.className = 'chat-msg ' + role;
  // Convert **text** to <strong>
  const formatted = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/\n/g,'<br>');
  div.innerHTML = '<div class="chat-bubble">' + formatted + '</div><div class="chat-time">' + now + '</div>';
  msgs.appendChild(div);
  setTimeout(scrollChatBottom, 50);
}

function addTypingIndicator() {
  const msgs = document.getElementById('chatMessages');
  if (!msgs) return;
  const div = document.createElement('div');
  div.className = 'chat-msg bot';
  div.id = 'chatTyping';
  div.innerHTML = '<div class="chat-typing"><span class="chat-dot"></span><span class="chat-dot"></span><span class="chat-dot"></span></div>';
  msgs.appendChild(div);
  setTimeout(scrollChatBottom, 50);
}

function removeTypingIndicator() {
  const t = document.getElementById('chatTyping');
  if (t) t.remove();
}

async function sendChat() {
  const inp = document.getElementById('chatInput');
  if (!inp) return;
  const msg = inp.value.trim();
  if (!msg) return;
  inp.value = '';

  // Hide chips after first message
  const chips = document.getElementById('chatChips');
  if (chips) chips.style.display = 'none';

  addChatMsg('user', msg);
  chatHistory.push({role:'user', content:msg});
  addTypingIndicator();

  try {
    const res = await fetch('/api/chat', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message:msg, history:chatHistory})
    });
    const data = await res.json();
    removeTypingIndicator();
    const reply = data.reply || 'Sorry, something went wrong. Try again!';
    addChatMsg('bot', reply);
    chatHistory.push({role:'assistant', content:reply});
    if (chatHistory.length > 20) chatHistory = chatHistory.slice(-20);
  } catch(e) {
    removeTypingIndicator();
    addChatMsg('bot', 'Connection issue. Please try again!');
  }
}

function sendQuick(text) {
  const inp = document.getElementById('chatInput');
  if (inp) inp.value = text;
  sendChat();
}

/* ════════════════════
   AI RECOMMENDATIONS
════════════════════ */
(async function loadAIRecs() {
  const section = document.getElementById('aiRecsSection');
  const grid    = document.getElementById('aiRecsGrid');
  if (!section || !grid) return;
  try {
    const res  = await fetch('/api/recommendations');
    if (!res.ok) return;
    const data = await res.json();
    if (!data || data.length === 0) return;
    grid.innerHTML = data.map(p => {
      const stars   = '★'.repeat(Math.floor(p.rating)) + '☆'.repeat(5 - Math.floor(p.rating));
      const reviews = Number(p.reviews).toLocaleString('en-IN');
      const price   = Number(p.price).toLocaleString('en-IN', {maximumFractionDigits:0});
      const badge   = p.badge ? `<div class="card-badge badge-${p.badge.toLowerCase().replace(/ /g,'')}">${p.badge}</div>` : '';
      return `
        <div class="product-card">
          ${badge}
          <a href="/product/${p.id}" class="card-thumb-link">
            <div class="card-thumb">
              <img src="/static/${p.image}" alt="${p.name}" loading="lazy">
            </div>
          </a>
          <div class="card-body">
            <div class="card-cat">${p.category}</div>
            <a href="/product/${p.id}" class="card-name-link">
              <div class="card-name">${p.name}</div>
            </a>
            <div class="card-stars">${stars} <span class="card-reviews">(${reviews})</span></div>
            <div class="card-price"><sub>₹</sub>${price}</div>
            <div class="card-actions">
              <a href="/product/${p.id}" class="card-view-btn">View Details</a>
              <a href="/add_to_cart/${p.id}" class="card-add" title="Add to Cart">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
                  <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                </svg>
              </a>
            </div>
          </div>
        </div>`;
    }).join('');
    section.style.display = 'block';
  } catch(e) {}
})();

/* ════════════════════
   LAZY IMAGE LOADING
════════════════════ */
(function initLazyImages() {
  if (!('IntersectionObserver' in window)) return;
  const obs = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const img = entry.target;
        img.classList.add('loaded');
        obs.unobserve(img);
      }
    });
  }, {rootMargin:'100px'});
  document.querySelectorAll('img[loading="lazy"]').forEach(img => obs.observe(img));
})();
