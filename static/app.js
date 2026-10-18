"use strict";
const closeDropdowns = new Set();
document.querySelectorAll("select[data-custom-select]").forEach((select) => {
    const dropdown = document.createElement("div");
    dropdown.className = "filter-dropdown";
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "filter-trigger";
    trigger.id = `${select.id}-trigger`;
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", `${select.id}-options`);
    const list = document.createElement("div");
    list.id = `${select.id}-options`;
    list.className = "filter-options";
    list.setAttribute("role", "listbox");
    list.hidden = true;
    const label = document.querySelector(`label[for="${select.id}"]`);
    if (label) {
        label.htmlFor = trigger.id;
        list.setAttribute("aria-labelledby", label.id);
        trigger.setAttribute("aria-labelledby", `${label.id} ${select.id}-value`);
    }
    const iconFor = (option) => {
        const icon = document.createElement("span");
        icon.className = "filter-icon";
        icon.textContent = option.dataset.icon || "✦";
        icon.dataset.tone = option.dataset.tone || "all";
        icon.setAttribute("aria-hidden", "true");
        return icon;
    };
    const updateTrigger = () => {
        const option = select.selectedOptions[0];
        const value = document.createElement("span");
        value.id = `${select.id}-value`;
        value.className = "filter-value";
        value.textContent = option.dataset.title || option.textContent;
        const chevron = document.createElement("span");
        chevron.className = "filter-chevron";
        chevron.setAttribute("aria-hidden", "true");
        trigger.replaceChildren(iconFor(option), value, chevron);
    };
    const close = (restoreFocus = false) => {
        list.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
        if (restoreFocus) trigger.focus();
    };
    closeDropdowns.add(close);
    const options = Array.from(select.options, (option) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "filter-option";
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", String(option.selected));
        item.tabIndex = -1;
        const copy = document.createElement("span");
        copy.className = "filter-option-copy";
        const title = document.createElement("span");
        title.className = "filter-option-title";
        title.textContent = option.dataset.title || option.textContent;
        copy.append(title);
        if (option.dataset.hint) {
            const hint = document.createElement("span");
            hint.className = "filter-option-hint";
            hint.textContent = option.dataset.hint;
            copy.append(hint);
        }
        const check = document.createElement("span");
        check.className = "filter-check";
        check.textContent = "✓";
        check.setAttribute("aria-hidden", "true");
        item.append(iconFor(option), copy, check);
        item.addEventListener("click", () => {
            select.value = option.value;
            options.forEach((entry, index) => entry.setAttribute("aria-selected", String(select.options[index].selected)));
            updateTrigger();
            close(true);
            select.dispatchEvent(new Event("change", { bubbles: true }));
        });
        list.append(item);
        return item;
    });
    const open = (index = select.selectedIndex) => {
        closeDropdowns.forEach((closeOther) => closeOther());
        list.hidden = false;
        trigger.setAttribute("aria-expanded", "true");
        options[index].focus();
    };
    trigger.addEventListener("click", () => list.hidden ? open() : close());
    trigger.addEventListener("keydown", (event) => {
        if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            open(event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : select.selectedIndex);
        }
    });
    list.addEventListener("keydown", (event) => {
        const current = options.indexOf(document.activeElement);
        if (event.key === "Escape") {
            event.preventDefault(); close(true);
        } else if (event.key === "Tab") {
            // Put focus on the trigger so native Tab continues to the next field.
            close(true);
        } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
                : (current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
            options[next].focus();
        }
    });
    document.addEventListener("pointerdown", (event) => {
        if (!dropdown.contains(event.target)) close();
    });
    dropdown.addEventListener("focusout", (event) => {
        if (!dropdown.contains(event.relatedTarget)) close();
    });
    updateTrigger();
    dropdown.append(trigger, list);
    select.after(dropdown);
    select.hidden = true;
});
document.querySelectorAll("form[data-auto-submit] select").forEach((select) => {
    select.addEventListener("change", () => select.form.requestSubmit());
});
