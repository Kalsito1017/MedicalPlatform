// Public legal pages: apply the stored theme, mount the theme switcher, and
// wire the shared About dialog. No API calls and no session data are involved.
import * as i18n from './i18n.js';
import * as theme from './theme.js';
import * as ui from './ui.js';

ui.onReady(() => {
  const actions = document.querySelector('.legal-header__actions');
  i18n.mountSwitcher(actions || document.querySelector('.legal-header'));
  theme.mountSwitcher(actions || document.querySelector('.legal-header'));
  ui.wireFooterDialogs();
});
