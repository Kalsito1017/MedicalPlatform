// Custom 404 page: apply the stored theme, mount the theme switcher, and wire
// the shared About dialog. No API calls and no session data are involved.
import * as i18n from './i18n.js';
import * as theme from './theme.js';
import * as ui from './ui.js';

ui.onReady(() => {
  i18n.mountSwitcher(document.querySelector('.error-header'));
  theme.mountSwitcher(document.querySelector('.error-header'));
  ui.wireFooterDialogs();
});
