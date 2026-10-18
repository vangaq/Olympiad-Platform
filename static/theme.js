"use strict";
(() => {
    const root = document.documentElement;
    const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
    const storageKey = "olympiad-theme";
    let preference = null;
    try {
        const saved = localStorage.getItem(storageKey);
        if (saved === "light" || saved === "dark") preference = saved;
    } catch (_) { /* The switch still works when browser storage is unavailable. */ }

    const applyTheme = (theme) => {
        root.dataset.theme = theme;
        root.style.colorScheme = theme;
        const dark = theme === "dark";
        document.querySelectorAll("[data-theme-set]").forEach((button) => {
            button.setAttribute("aria-pressed", String(button.dataset.themeSet === theme));
        });
        const meta = document.querySelector('meta[name="theme-color"]');
        if (meta) meta.content = dark ? "#171a24" : "#f0f3fa";
    };

    applyTheme(preference || (systemTheme.matches ? "dark" : "light"));
    root.classList.add("theme-ready");
    document.addEventListener("DOMContentLoaded", () => {
        applyTheme(root.dataset.theme);
        document.querySelectorAll("[data-theme-set]").forEach((button) => {
            button.addEventListener("click", () => {
                preference = button.dataset.themeSet;
                try { localStorage.setItem(storageKey, preference); } catch (_) {}
                applyTheme(preference);
            });
        });
    });
    systemTheme.addEventListener("change", (event) => {
        if (!preference) applyTheme(event.matches ? "dark" : "light");
    });
    window.addEventListener("storage", (event) => {
        if (event.key !== storageKey && event.key !== null) return;
        preference = event.newValue === "light" || event.newValue === "dark" ? event.newValue : null;
        applyTheme(preference || (systemTheme.matches ? "dark" : "light"));
    });
})();
