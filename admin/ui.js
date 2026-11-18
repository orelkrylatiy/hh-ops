(() => {
  'use strict';

  const PAGE_SUBTITLES = {
    home: 'Стартовая точка и быстрые действия',
    dashboard: 'Воронка, снимки и состояние аккаунта',
    negotiations: 'История откликов и текущие статусы',
    vacancies: 'Вакансии, сохранённые в локальной базе',
    skipped: 'Что отфильтровали и почему',
    employers: 'Компании из локального снимка',
    resumes: 'Резюме, публикации и просмотры',
    inbox: 'Диалоги, где может требоваться ответ',
    'apply-form': 'Параметры следующего запуска откликов',
    letter: 'Ручная проверка генерации сопроводительного',
    'letter-template': 'Статический шаблон и fallback письма',
    run: 'Ручной запуск и контроль операций',
    config: 'Конфигурация выбранного аккаунта',
    automation: 'Режим работы cron и безопасные лимиты',
  };

  const NAV_SECTIONS = [
    ['home', 'Обзор'],
    ['negotiations', 'Данные'],
    ['apply-form', 'Автоматизация'],
    ['config', 'Система'],
  ];

  const TEXT_TRANSLATIONS = new Map([
    ['Account', 'Аккаунт'],
    ['No applications', 'Нет откликов'],
    ['No vacancies', 'Нет вакансий'],
    ['No skipped vacancies', 'Нет пропущенных вакансий'],
  ]);

  let enhanceFrame = null;

  function currentPageName() {
    const active = document.querySelector('.nav-item.active[data-page]');
    return active?.dataset.page || 'home';
  }

  function scheduleEnhance() {
    if (enhanceFrame !== null) return;
    enhanceFrame = requestAnimationFrame(() => {
      enhanceFrame = null;
      enhanceDynamicContent();
      updatePageContext();
    });
  }

  function installTitleShell() {
    const topbar = document.querySelector('.topbar');
    const title = document.getElementById('page-title');
    if (!topbar || !title || title.closest('.admin-title-wrap')) return;

    const wrap = document.createElement('div');
    wrap.className = 'admin-title-wrap';
    topbar.insertBefore(wrap, title);
    wrap.appendChild(title);

    const subtitle = document.createElement('div');
    subtitle.id = 'admin-page-subtitle';
    subtitle.className = 'admin-page-subtitle';
    wrap.appendChild(subtitle);
  }

  function installContextChip() {
    const topbar = document.querySelector('.topbar');
    const actions = document.getElementById('topbar-actions');
    if (!topbar || !actions || document.getElementById('admin-topbar-context')) return;

    const chip = document.createElement('div');
    chip.id = 'admin-topbar-context';
    chip.className = 'admin-topbar-context';
    chip.setAttribute('aria-label', 'Текущий источник данных');
    chip.innerHTML = '<span class="admin-context-dot" aria-hidden="true"></span><span id="admin-context-label"></span>';
    topbar.insertBefore(chip, actions);
  }

  function updatePageContext() {
    const page = currentPageName();
    const subtitle = document.getElementById('admin-page-subtitle');
    if (subtitle) subtitle.textContent = PAGE_SUBTITLES[page] || '';

    const chip = document.getElementById('admin-topbar-context');
    const label = document.getElementById('admin-context-label');
    if (!chip || !label) return;

    let aggregate = false;
    let profile = 'default';
    try {
      aggregate = typeof viewScope !== 'undefined' && viewScope === 'all';
      profile = typeof currentProfile !== 'undefined' ? currentProfile : profile;
    } catch (_) {
      // The legacy app can still be booting. The observer will retry.
    }

    chip.classList.toggle('aggregate', aggregate);
    label.textContent = aggregate ? 'Все аккаунты · снимки' : `Аккаунт · ${profile}`;
    chip.title = aggregate
      ? 'Агрегированные локальные снимки. Live-действия отключены.'
      : `Live-действия выполняются от аккаунта ${profile}`;
  }

  function installNavSections() {
    const nav = document.getElementById('nav');
    if (!nav || nav.querySelector('.nav-section-label')) return;

    NAV_SECTIONS.forEach(([page, label]) => {
      const item = nav.querySelector(`.nav-item[data-page="${page}"]`);
      if (!item) return;
      const section = document.createElement('div');
      section.className = 'nav-section-label';
      section.textContent = label;
      section.setAttribute('aria-hidden', 'true');
      nav.insertBefore(section, item);
    });
  }

  function enhanceNavigation() {
    installNavSections();
    document.querySelectorAll('.nav-item[data-page]').forEach(item => {
      const label = item.textContent.trim();
      item.setAttribute('role', 'button');
      item.setAttribute('tabindex', '0');
      item.setAttribute('title', label);
      item.setAttribute('aria-label', label);
      if (item.classList.contains('active')) {
        item.setAttribute('aria-current', 'page');
      } else {
        item.removeAttribute('aria-current');
      }
      if (item.dataset.keyboardReady === '1') return;
      item.dataset.keyboardReady = '1';
      item.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          item.click();
        }
      });
      item.addEventListener('click', closeMobileNav);
    });
  }

  function installMobileNav() {
    const topbar = document.querySelector('.topbar');
    const titleWrap = document.querySelector('.admin-title-wrap');
    if (!topbar || !titleWrap || document.getElementById('admin-mobile-toggle')) return;

    const button = document.createElement('button');
    button.id = 'admin-mobile-toggle';
    button.className = 'admin-mobile-toggle';
    button.type = 'button';
    button.setAttribute('aria-label', 'Открыть навигацию');
    button.setAttribute('aria-controls', 'nav');
    button.setAttribute('aria-expanded', 'false');
    button.innerHTML = '<span aria-hidden="true">☰</span>';
    button.addEventListener('click', toggleMobileNav);
    topbar.insertBefore(button, titleWrap);

    const backdrop = document.createElement('div');
    backdrop.className = 'admin-sidebar-backdrop';
    backdrop.id = 'admin-sidebar-backdrop';
    backdrop.setAttribute('aria-hidden', 'true');
    backdrop.addEventListener('click', closeMobileNav);
    document.body.appendChild(backdrop);
  }

  function toggleMobileNav() {
    const shouldOpen = !document.body.classList.contains('admin-mobile-nav-open');
    document.body.classList.toggle('admin-mobile-nav-open', shouldOpen);
    const button = document.getElementById('admin-mobile-toggle');
    if (button) {
      button.setAttribute('aria-expanded', String(shouldOpen));
      button.setAttribute('aria-label', shouldOpen ? 'Закрыть навигацию' : 'Открыть навигацию');
    }
  }

  function closeMobileNav() {
    document.body.classList.remove('admin-mobile-nav-open');
    const button = document.getElementById('admin-mobile-toggle');
    if (button) {
      button.setAttribute('aria-expanded', 'false');
      button.setAttribute('aria-label', 'Открыть навигацию');
    }
  }

  function translateLeafText(root) {
    root.querySelectorAll('th, td, span, div').forEach(node => {
      if (node.children.length) return;
      const translated = TEXT_TRANSLATIONS.get(node.textContent.trim());
      if (translated) node.textContent = translated;
    });
  }

  function enhanceTables(root) {
    root.querySelectorAll('table').forEach(table => {
      table.classList.add('admin-responsive-table');
      const headers = Array.from(table.querySelectorAll('thead th')).map(th => th.textContent.trim());
      table.querySelectorAll('tbody tr').forEach(row => {
        Array.from(row.children).forEach((cell, index) => {
          if (cell.tagName !== 'TD') return;
          if (!cell.hasAttribute('data-label')) {
            cell.setAttribute('data-label', headers[index] || '');
          }
        });
      });
    });
  }

  function enhanceLoadingAndErrors(root) {
    Array.from(root.children).forEach(node => {
      if (!(node instanceof HTMLElement)) return;
      const text = node.textContent.trim();
      node.classList.toggle('admin-loading-state', /^Загрузка(?:\.|\s|…)/i.test(text));
      node.classList.toggle('admin-error-state', /^Ошибка(?::|\s)/i.test(text));
    });
  }

  function enhanceLinks(root) {
    root.querySelectorAll('a[target="_blank"]').forEach(link => {
      if (!link.title) link.title = 'Открыть в новой вкладке';
    });
  }

  function enhanceModal() {
    const modal = document.getElementById('apply-modal');
    if (!modal) return;
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    const heading = modal.querySelector('h2');
    if (heading) {
      heading.id ||= 'apply-modal-title';
      modal.setAttribute('aria-labelledby', heading.id);
    }
  }

  function enhanceToasts() {
    const container = document.getElementById('toast-container');
    if (!container) return;
    container.setAttribute('aria-live', 'polite');
    container.setAttribute('aria-relevant', 'additions');
    container.querySelectorAll('.toast').forEach(toast => {
      toast.setAttribute('role', toast.classList.contains('error') ? 'alert' : 'status');
    });
  }

  function enhanceDynamicContent() {
    const content = document.getElementById('content');
    if (content) {
      translateLeafText(content);
      enhanceTables(content);
      enhanceLoadingAndErrors(content);
      enhanceLinks(content);
    }
    enhanceNavigation();
    enhanceModal();
    enhanceToasts();
  }

  function visibleSearchInput() {
    const selectors = [
      '#vac-search',
      '#emp-search',
      '.filters input:not([type="hidden"])',
      'input[type="search"]',
    ];
    for (const selector of selectors) {
      const input = document.querySelector(selector);
      if (input && input.offsetParent !== null && !input.disabled) return input;
    }
    return null;
  }

  function targetIsEditable(target) {
    return target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target instanceof HTMLSelectElement ||
      target?.isContentEditable;
  }

  function installKeyboardShortcuts() {
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        const modal = document.getElementById('apply-modal');
        if (modal && modal.style.display !== 'none' && typeof window.closeApplyModal === 'function') {
          window.closeApplyModal();
          return;
        }
        if (document.getElementById('profile-popover')?.classList.contains('open') && typeof window.closeProfilePopover === 'function') {
          window.closeProfilePopover();
          return;
        }
        closeMobileNav();
        return;
      }

      const searchShortcut = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k';
      const slashShortcut = event.key === '/' && !targetIsEditable(event.target);
      if (!searchShortcut && !slashShortcut) return;

      const input = visibleSearchInput();
      if (!input) return;
      event.preventDefault();
      input.focus();
      if (typeof input.select === 'function') input.select();
    });
  }

  function wrapLegacyFunction(name, after) {
    const original = window[name];
    if (typeof original !== 'function' || original.__adminUiWrapped) return;

    const wrapped = function(...args) {
      const result = original.apply(this, args);
      Promise.resolve(result).finally(() => {
        after();
        scheduleEnhance();
      });
      return result;
    };
    wrapped.__adminUiWrapped = true;
    window[name] = wrapped;
  }

  function installLegacyHooks() {
    wrapLegacyFunction('goPage', () => {
      closeMobileNav();
      updatePageContext();
    });
    wrapLegacyFunction('onProfileChange', updatePageContext);
    wrapLegacyFunction('onScopeChange', updatePageContext);
  }

  function installObservers() {
    const content = document.getElementById('content');
    if (content) {
      new MutationObserver(scheduleEnhance).observe(content, {
        childList: true,
        subtree: true,
      });
    }

    const nav = document.getElementById('nav');
    if (nav) {
      new MutationObserver(() => {
        enhanceNavigation();
        updatePageContext();
      }).observe(nav, {
        attributes: true,
        subtree: true,
        attributeFilter: ['class'],
      });
    }

    const toasts = document.getElementById('toast-container');
    if (toasts) {
      new MutationObserver(enhanceToasts).observe(toasts, {
        childList: true,
      });
    }
  }

  function install() {
    document.body.classList.add('admin-ui-v2');
    installTitleShell();
    installContextChip();
    installMobileNav();
    enhanceNavigation();
    enhanceModal();
    enhanceToasts();
    installKeyboardShortcuts();
    installLegacyHooks();
    installObservers();
    updatePageContext();
    scheduleEnhance();

    window.addEventListener('resize', () => {
      if (window.innerWidth > 900) closeMobileNav();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install, {once: true});
  } else {
    install();
  }
})();
