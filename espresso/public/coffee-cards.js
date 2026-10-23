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
      image.width = 1024;
      image.height = 1536;
      image.loading = "eager";
      image.decoding = "async";
      host.append(image);
    }
  }

  const menu = document.querySelector("#cafe-menu");
  if (menu) ready.then(data => {
    let filter = "all", selected = "caramel-latte", character = "sabrina";
    const list = menu.querySelector("#drink-list");
    const preview = menu.querySelector("#drink-card-demo");
    const name = menu.querySelector("#drink-recipient");
    const status = menu.querySelector("#drink-menu-status");
    const characterPicker = menu.querySelector("#drink-character");
    const mobilePicker = menu.querySelector("#drink-mobile-choice");
    characterPicker.replaceChildren(...data.characters.map(item => new Option(item.name, item.id)));
    const visible = () => data.drinks.filter(item => filter === "all" || item.category === filter);
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
    renderMenu();
    menu.querySelectorAll("[data-menu-control]").forEach(node => { node.disabled = false; });
    status.textContent = "12 drinks. Four friendly faces. Always on the house.";
  }).catch(error => {
    menu.querySelector("#drink-menu-status").textContent = error.message;
  });
  return {ready, renderCard};
})();
