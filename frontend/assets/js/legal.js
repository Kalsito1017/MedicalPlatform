// Public legal pages: apply the stored theme and language, mount the shared
// switchers, wire the About dialog, and show a truthful "Back to dashboard"
// action when the visitor still has a valid session. The page itself stays
// public: anonymous or failed session checks keep the normal "Sign in" link.
import * as auth from './auth.js';
import * as i18n from './i18n.js';
import * as theme from './theme.js';
import * as ui from './ui.js';

ui.onReady(async () => {
  const actions = document.querySelector('.legal-header__actions');
  i18n.start();
  i18n.mountSwitcher(actions || document.querySelector('.legal-header'));
  theme.mountSwitcher(actions || document.querySelector('.legal-header'));
  ui.wireFooterDialogs();
  i18n.applyLanguage();

  const links = document.querySelectorAll('[data-legal-session-link]');
  if (links.length === 0) return;
  try {
    const user = await auth.currentSession();
    if (!user) return;
    for (const link of links) {
      link.setAttribute('href', 'dashboard.html');
      link.textContent = 'Back to dashboard';
      i18n.applyLanguage(link);
    }
  } catch (error) {
    // Public page: keep the default sign-in action if the session check fails.
  }
});
