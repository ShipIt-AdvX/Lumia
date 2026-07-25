'use strict';
(function () {
  const fsBtn = document.getElementById('tbFs');
  const closeBtn = document.getElementById('tbClose');
  const titleEl = document.getElementById('tbTitle');
  const barEl = document.querySelector('.lumia-titlebar');

  if (titleEl && window.lumia) titleEl.textContent = window.lumia.title || 'Lumia';

  if (fsBtn) fsBtn.addEventListener('click', () => window.lumia.win.toggleFullscreen());
  if (closeBtn) closeBtn.addEventListener('click', () => window.lumia.win.close());

  if (barEl) barEl.addEventListener('mousedown', (e) => {
    if (e.button !== 0 || e.target.closest('.tb-btn')) return;
    const sx = e.screenX, sy = e.screenY;
    let dragging = false;
    const onMove = (ev) => {
      if (!dragging && (Math.abs(ev.screenX - sx) > 3 || Math.abs(ev.screenY - sy) > 3)) {
        dragging = true;
        window.lumia.win.dragStart();
      }
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      if (dragging) window.lumia.win.dragEnd();
      else window.lumia.win.collapseToggle();
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  });

  document.body.addEventListener('mouseenter', () => window.lumia.win.setHover(true));
  document.body.addEventListener('mouseleave', () => window.lumia.win.setHover(false));

  window.lumia.win.onRetractedChanged((retracted) => {
    document.body.classList.toggle('is-retracted', retracted);
  });

  window.lumia.win.onFullscreenChanged((isFull) => {
    document.body.classList.toggle('is-fullscreen', isFull);
    if (fsBtn) fsBtn.textContent = isFull ? '退出全屏' : '全屏';
  });

  window.lumia.win.onFsZoom((z) => {
    const el = document.querySelector('.lumia-window');
    if (!el) return;
    const ease = 'cubic-bezier(0.33, 1, 0.68, 1)';
    if (z.from) {
      el.animate([
        { transform: `translate(${z.from.x}px, ${z.from.y}px)`, width: z.from.width + 'px', height: z.from.height + 'px' },
        { transform: 'translate(0px, 0px)', width: '100vw', height: '100vh' },
      ], { duration: z.ms, easing: ease });
    } else if (z.to) {
      const anim = el.animate([
        { transform: 'translate(0px, 0px)', width: '100vw', height: '100vh' },
        { transform: `translate(${z.to.x}px, ${z.to.y}px)`, width: z.to.width + 'px', height: z.to.height + 'px' },
      ], { duration: z.ms, easing: ease, fill: 'forwards' });
      const done = () => anim.cancel();
      window.addEventListener('resize', done, { once: true });
    }
  });

  window.lumia.win.setHover(false);
})();
