"use strict";
window.espressoDrinks = (() => {
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  let catalog = null;
  const ready = fetch("/assets/drinks.json").then(response => {
    if (!response.ok) throw new Error("The café menu couldn't load. Refresh to try again.");
    return response.json();
  }).then(data => {
    if (!Array.isArray(data.drinks) || !data.drinks.length || !Array.isArray(data.characters)) {
      throw new Error("The café menu is taking a break. Please try again later.");
    }
    catalog = data;
    return data;
  });
  // Consumers show their own friendly fallback when the menu is unavailable.
  ready.catch(() => {});
  ready.then(data => {
    const picker = document.querySelector("#coffee-preview-drink");
    if (!picker) return;
    const selected = picker.value;
    picker.replaceChildren(...data.drinks.map(item => new Option(item.name, item.id)));
    picker.value = selected;
    document.dispatchEvent(new Event("espresso:drinks-ready"));
  }).catch(() => {
    const host = document.querySelector("#coffee-card-preview");
    if (host) host.textContent = "Card preview is unavailable. Your card settings can still be saved. Refresh to reload the beverage menu.";
  });

  function renderCard(host, values = {}) {
    if (!catalog || !host) return;
    const drink = catalog.drinks.find(item => item.id === values.drink) || catalog.drinks[0];
    const character = catalog.characters.find(item => item.id === (values.character || "sabrina"));
    const colors = {classic: "#d8a7b1", pastel: "#c9b5eb", midnight: "#41314d"};
    const color = /^[a-f0-9]{6}$/i.test(values.color || "") ? "#" + values.color : colors.classic;
    host.style.setProperty("--coffee-color", colors[values.style] || color);
    host.dataset.character = character?.id || "none";
    host.classList.add("drink-card");
    const body = make("div", undefined, "drink-card-copy");
    body.append(make("p", "A LITTLE GOOD COMPANY", "drink-card-eyebrow"));
    body.append(make("h3", values.title || "A little pick-me-up ☕"));
    const message = (values.message || "{sender} brought {member} {drink}.\nMade with a little kindness.")
      .replaceAll("{sender}", values.sender || "You")
      .replaceAll("{member}", values.member || "a friend")
      .replaceAll("{drink}", drink.description);
    body.append(make("p", message, "drink-card-message"));
    body.append(make("p", drink.emoji + " " + drink.name, "drink-card-order"));
    body.append(make("p", drink.note, "drink-card-note"));
    const signature = make("p", character && character.id !== "none"
      ? `${character.name} · ${character.tagline}` : "Freshly brewed. Kindly delivered.", "drink-card-signature");
    body.append(signature);
    const previousImage = host.querySelector(".drink-card-character");
    host.replaceChildren(body);
    if (character?.image && /^\/assets\/[a-z0-9-]+\.png$/.test(character.image)) {
      const image = previousImage?.getAttribute("src") === character.image
        ? previousImage : make("img", undefined, "drink-card-character");
      image.src = character.image;
      image.alt = "";
      image.width = 1254;
      image.height = 1254;
      image.loading = "eager";
      image.decoding = "async";
      host.append(image);
    }
  }

  const menu = document.querySelector("#cafe-menu");
  if (menu) ready.then(data => {
    let filter = "all", selected = data.drinks.some(item => item.id === "caramel-latte") ? "caramel-latte" : data.drinks[0].id;
    let character = data.characters.some(item => item.id === "sabrina") ? "sabrina" : data.characters[0]?.id;
    const list = menu.querySelector("#drink-list");
    const preview = menu.querySelector("#drink-card-demo");
    const name = menu.querySelector("#drink-recipient");
    const status = menu.querySelector("#drink-menu-status");
    const characterPicker = menu.querySelector("#drink-character");
    const mobilePicker = menu.querySelector("#drink-mobile-choice");
    const previewKey = "espresso.menu-preview.v1";
    const remember = menu.querySelector("#drink-remember-preview");
    const load = menu.querySelector("#drink-load-preview");
    const clear = menu.querySelector("#drink-clear-preview");
    const preferenceStatus = menu.querySelector("#drink-preview-status");
    let savedPreview = null, preferenceState = "unknown";
    characterPicker.replaceChildren(...data.characters.map(item => new Option(item.name, item.id)));
    characterPicker.value = character;
    const visible = () => data.drinks.filter(item => filter === "all" || item.category === filter);

    function validPreview(value) {
      if (!value || typeof value !== "object" || Array.isArray(value) ||
          Object.keys(value).length !== 2 || !Object.hasOwn(value, "drink") || !Object.hasOwn(value, "character")) return null;
      const validId = id => typeof id === "string" && /^[a-z][a-z0-9-]{0,63}$/.test(id);
      if (!validId(value.drink) || !validId(value.character) ||
          !data.drinks.some(item => item.id === value.drink) || !data.characters.some(item => item.id === value.character)) return null;
      return {drink:value.drink, character:value.character};
    }
    function readPreview() {
      savedPreview = null;
      try {
        const raw = localStorage.getItem(previewKey);
        if (raw === null) { preferenceState = "none"; return; }
        if (raw.length > 256) { preferenceState = "invalid"; return; }
        try { savedPreview = validPreview(JSON.parse(raw)); }
        catch { /* Invalid browser data is ignored without changing the current card. */ }
        preferenceState = savedPreview ? "saved" : "invalid";
      } catch { preferenceState = "unavailable"; }
    }
    function updatePreviewPreference(message) {
      remember.disabled = false;
      load.disabled = !savedPreview;
      clear.disabled = preferenceState === "none";
      if (message) { preferenceStatus.textContent = message; return; }
      if (savedPreview) {
        const drink = data.drinks.find(item => item.id === savedPreview.drink);
        const companion = data.characters.find(item => item.id === savedPreview.character);
        preferenceStatus.textContent = `Saved preview available: ${drink.name} · ${companion.name}. Choose Load my preview to apply it.`;
      } else if (preferenceState === "invalid") {
        preferenceStatus.textContent = "The saved preview no longer matches this menu and was ignored. Remember a new preview or clear the saved entry.";
      } else if (preferenceState === "unavailable") {
        preferenceStatus.textContent = "Your browser couldn't read saved previews. You can still try the menu without saving.";
      } else {
        preferenceStatus.textContent = "No saved preview in this browser. Remembering is optional; changing the menu saves nothing.";
      }
    }
    function updateCard() {
      renderCard(preview, {drink: selected, character, member: name.value.trim() || "a friend", style: "pastel"});
      mobilePicker.value = selected;
      menu.querySelectorAll("[data-drink]").forEach(button => {
        const active = button.dataset.drink === selected;
        button.setAttribute("aria-pressed", String(active));
      });
    }
    function renderMenu() {
      mobilePicker.replaceChildren(...visible().map(item => new Option(item.name, item.id)));
      list.replaceChildren(...visible().map(drink => {
        const button = make("button", undefined, "drink-option");
        button.type = "button";
        button.dataset.drink = drink.id;
        button.setAttribute("aria-pressed", String(drink.id === selected));
        const heading = make("span", undefined, "drink-option-name");
        const icon = make("span", drink.emoji, "drink-option-icon");
        icon.setAttribute("aria-hidden", "true");
        heading.append(icon, document.createTextNode(drink.name));
        button.append(heading, make("span", drink.note, "drink-option-note"));
        button.append(make("span", `${drink.temperature === "iced" ? "Over ice" : "Served warm"} · ${drink.category === "cocoa" ? "Coffee-free" : "Contains caffeine"}`, "drink-option-detail"));
        button.addEventListener("click", () => { selected = drink.id; updateCard(); });
        return button;
      }));
      updateCard();
    }
    menu.querySelectorAll("[data-drink-filter]").forEach(button => button.addEventListener("click", () => {
      filter = button.dataset.drinkFilter;
      menu.querySelectorAll("[data-drink-filter]").forEach(other => other.setAttribute("aria-pressed", String(other === button)));
      if (!visible().some(item => item.id === selected)) selected = visible()[0].id;
      renderMenu();
    }));
    characterPicker.addEventListener("change", () => { character = characterPicker.value; updateCard(); });
    mobilePicker.addEventListener("change", () => { selected = mobilePicker.value; updateCard(); });
    name.addEventListener("input", updateCard);
    menu.querySelector("#drink-surprise").addEventListener("click", () => {
      const choices = visible().filter(item => item.id !== selected);
      if (choices.length) selected = choices[Math.floor(Math.random() * choices.length)].id;
      updateCard();
      status.textContent = "On the house: " + data.drinks.find(item => item.id === selected).name + ".";
    });
    menu.querySelector("#drink-copy-command").addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText("/drinks");
        status.textContent = "Copied /drinks. Paste it in your Discord server to choose a drink and send a card.";
      } catch {
        status.textContent = "Type /drinks in your Discord server to open the beverage menu.";
      }
    });
    remember.addEventListener("click", () => {
      const choice = validPreview({drink:selected, character});
      if (!choice) { updatePreviewPreference("Choose a drink and café companion from the current menu before remembering a preview."); return; }
      try {
        localStorage.setItem(previewKey, JSON.stringify(choice));
        readPreview();
        if (!savedPreview || savedPreview.drink !== choice.drink || savedPreview.character !== choice.character) throw new Error("Save could not be confirmed");
        updatePreviewPreference("Preview remembered in this browser. Only the drink and café companion were saved; your Discord usual is separate.");
      } catch {
        savedPreview = null; preferenceState = "unavailable";
        updatePreviewPreference("Your browser couldn't save or confirm this preview. Check its storage settings before trying again.");
      }
    });
    load.addEventListener("click", () => {
      readPreview();
      if (!savedPreview) { updatePreviewPreference(); return; }
      selected = savedPreview.drink; character = savedPreview.character;
      if (!visible().some(item => item.id === selected)) {
        filter = "all";
        menu.querySelectorAll("[data-drink-filter]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.drinkFilter === "all")));
      }
      characterPicker.value = character;
      renderMenu();
      updatePreviewPreference("Loaded your preview's drink and café companion. Recipient text was never saved. Nothing was sent to Discord.");
    });
    clear.addEventListener("click", () => {
      try {
        localStorage.removeItem(previewKey);
        if (localStorage.getItem(previewKey) !== null) throw new Error("Clear could not be confirmed");
        savedPreview = null; preferenceState = "none";
        updatePreviewPreference("Saved preview cleared from this browser. The current card and your Discord usual are unchanged.");
      } catch {
        savedPreview = null; preferenceState = "unavailable";
        updatePreviewPreference("Your browser couldn't clear or confirm removing the saved preview. Check its storage settings or clear this site's data there.");
      }
    });
    window.addEventListener("storage", event => {
      if (event.key !== previewKey && event.key !== null) return;
      readPreview(); updatePreviewPreference();
    });
    renderMenu();
    menu.querySelectorAll("[data-menu-control]").forEach(node => { node.disabled = false; });
    readPreview(); updatePreviewPreference();
    status.textContent = "12 drinks. Four friendly faces. Always on the house.";
  }).catch(error => {
    menu.querySelector("#drink-menu-status").textContent = error.message;
    const preferenceStatus = menu.querySelector("#drink-preview-status");
    if (preferenceStatus) preferenceStatus.textContent = "Saved previews are unavailable while the menu cannot load. Nothing was loaded or changed.";
  });
  return {ready, renderCard};
})();
