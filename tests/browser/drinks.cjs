// Offline checks: no Discord credentials, account, server, or external requests.
const assert = require("node:assert/strict");
const path = require("node:path");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = path.resolve(__dirname, "../../espresso/public");
const site = "https://espresso.test";
const previewKey = "espresso.menu-preview.v1";
async function main() {
  const browser = await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL || (process.platform === "win32" ? "msedge" : undefined)});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:"reduce"});
    let failMenu = false, menuGate = null;
    const unexpectedWrites = [];
    await context.route("**/*", async route => {
      const url = new URL(route.request().url());
      if (url.origin !== site) return route.abort();
      if (route.request().method() !== "GET") unexpectedWrites.push(route.request().method()+" "+url.pathname);
      if (url.pathname === "/api/stats") return route.fulfill({json:{status:"preview",server_count:null}});
      if (url.pathname === "/assets/drinks.json" && menuGate) await menuGate;
      if (url.pathname === "/assets/drinks.json" && failMenu) return route.fulfill({status:503,body:"Unavailable"});
      const file = url.pathname === "/" ? "index.html" : url.pathname.startsWith("/assets/") ? url.pathname.slice(8) : "";
      if (!/^[a-z0-9.-]+$/.test(file)) return route.fulfill({status:404,body:"Not found"});
      const contentType = {".js":"application/javascript",".css":"text/css",".html":"text/html",".json":"application/json",".png":"image/png"}[path.extname(file)];
      return route.fulfill({path:path.join(root,file),contentType});
    });
    const page = await context.newPage(), errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(site);
    await page.locator("#drink-copy-command:enabled").waitFor();
    assert.equal(await page.locator("[data-drink]").count(),12);
    await page.locator("[data-drink-filter=cocoa]").click();
    assert.equal(await page.locator("[data-drink]").count(),1);
    assert.match(await page.locator("#drink-card-demo").innerText(),/a hot chocolate/);
    assert.match(await page.locator("#drink-list").innerText(),/Coffee-free/);
    await page.locator("#drink-character").selectOption("dreamer");
    assert.match(await page.locator("#drink-card-demo").innerText(),/Cloud Dreamer/);
    await page.locator("#drink-recipient").fill("<img src=x onerror=alert(1)>");
    assert.equal(await page.locator(".drink-card-copy img").count(),0);
    assert.match(await page.locator(".drink-card-message").innerText(),/<img src=x/);
    await page.locator("#drink-recipient").fill("your favorite person");
    await page.locator("#drink-surprise").click();
    assert.match(await page.locator("#drink-menu-status").innerText(),/Hot chocolate/);
    await page.locator("[data-drink-filter=tea]").click();
    assert.equal(await page.locator("[data-drink]").count(),2);
    await page.locator("[data-drink=matcha-latte]").click();
    assert.match(await page.locator("#drink-card-demo").innerText(),/a matcha latte/);
    await page.locator("#drink-character").selectOption("none");
    assert.equal(await page.locator("#drink-card-demo img").count(),0);
    for (const character of ["sabrina","guardian","dreamer","spark"]) {
      await page.locator("#drink-character").selectOption(character);
      await page.waitForFunction(() => {
        const image = document.querySelector("#drink-card-demo img");
        return image.complete && image.naturalWidth > 0;
      });
    }
    await page.locator("[data-drink-filter=all]").click();
    await page.locator("[data-drink=iced-latte]").focus();
    await page.keyboard.press("Enter");
    assert.equal(await page.locator("[data-drink=iced-latte]").getAttribute("aria-pressed"),"true");
    assert.match(await page.locator("#drink-card-demo").innerText(),/an iced latte/);
    await context.grantPermissions(["clipboard-read","clipboard-write"],{origin:site});
    await page.locator("#drink-copy-command").click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()),"/drinks");

    // A website preview is an explicit browser preference, independent of Discord.
    assert.equal(await page.evaluate(key => localStorage.getItem(key),previewKey),null);
    assert.equal(await page.locator("#drink-load-preview").isDisabled(),true);
    assert.match(await page.locator(".drink-preview-preference").innerText(),/separate from your Discord server/);
    await page.locator("[data-drink=hot-chocolate]").click();
    await page.locator("#drink-character").selectOption("dreamer");
    await page.locator("#drink-recipient").fill("Never store this recipient");
    await page.locator("#drink-remember-preview").click();
    const remembered = {drink:"hot-chocolate",character:"dreamer"};
    assert.deepEqual(await page.evaluate(key => JSON.parse(localStorage.getItem(key)),previewKey),remembered);
    assert.match(await page.locator("#drink-preview-status").innerText(),/remembered in this browser/);
    await page.locator("[data-drink=iced-latte]").click();
    await page.locator("#drink-character").selectOption("none");
    assert.deepEqual(await page.evaluate(key => JSON.parse(localStorage.getItem(key)),previewKey),remembered);
    await page.reload();
    await page.locator("#drink-copy-command:enabled").waitFor();
    assert.match(await page.locator("#drink-preview-status").innerText(),/Saved preview available: Hot chocolate · Cloud Dreamer/);
    assert.match(await page.locator("#drink-card-demo").innerText(),/a caramel latte/);
    assert.equal(await page.locator("#drink-character").inputValue(),"sabrina");
    assert.equal(await page.locator("#drink-recipient").inputValue(),"");
    await page.locator("[data-drink-filter=coffee]").click();
    await page.locator("#drink-recipient").fill("A new unsaved recipient");
    await page.locator("#drink-load-preview").click();
    assert.match(await page.locator("#drink-card-demo").innerText(),/a hot chocolate/);
    assert.equal(await page.locator("#drink-character").inputValue(),"dreamer");
    assert.equal(await page.locator("#drink-recipient").inputValue(),"A new unsaved recipient");
    assert.equal(await page.locator("[data-drink-filter=all]").getAttribute("aria-pressed"),"true");
    assert.equal(await page.locator("#drink-mobile-choice").inputValue(),"hot-chocolate");

    const other = await context.newPage();
    other.on("pageerror", error => errors.push(error.message));
    await other.goto(site);
    await other.locator("#drink-copy-command:enabled").waitFor();
    await page.locator("[data-drink=matcha-latte]").click();
    await page.locator("#drink-character").selectOption("guardian");
    await page.locator("#drink-remember-preview").click();
    await other.locator("#drink-preview-status").filter({hasText:"Matcha latte · Velvet Guardian"}).waitFor();
    assert.match(await other.locator("#drink-card-demo").innerText(),/a caramel latte/);
    await other.locator("#drink-load-preview").click();
    assert.match(await other.locator("#drink-card-demo").innerText(),/a matcha latte/);
    assert.equal(await other.locator("#drink-character").inputValue(),"guardian");
    await other.close();

    // Blocked or silently refused storage must not report a successful operation.
    await page.evaluate(() => {
      window.previewOriginalSet = Storage.prototype.setItem;
      Storage.prototype.setItem = function() { throw new DOMException("Fixture blocked storage","SecurityError"); };
    });
    await page.locator("[data-drink=espresso]").click();
    await page.locator("#drink-remember-preview").click();
    assert.match(await page.locator("#drink-preview-status").innerText(),/couldn't save or confirm/);
    await page.evaluate(() => { Storage.prototype.setItem = () => {}; });
    await page.locator("#drink-remember-preview").click();
    assert.match(await page.locator("#drink-preview-status").innerText(),/couldn't save or confirm/);
    await page.evaluate(() => { Storage.prototype.setItem = window.previewOriginalSet; delete window.previewOriginalSet; });
    assert.deepEqual(await page.evaluate(key => JSON.parse(localStorage.getItem(key)),previewKey),{drink:"matcha-latte",character:"guardian"});
    await page.locator("#drink-remember-preview").click();
    await page.evaluate(() => {
      window.previewOriginalGet = Storage.prototype.getItem;
      Storage.prototype.getItem = function() { throw new DOMException("Fixture blocked read","SecurityError"); };
    });
    await page.locator("#drink-load-preview").click();
    assert.match(await page.locator("#drink-preview-status").innerText(),/couldn't read saved previews/);
    assert.match(await page.locator("#drink-card-demo").innerText(),/an espresso/);
    await page.evaluate(() => { Storage.prototype.getItem = window.previewOriginalGet; delete window.previewOriginalGet; });
    await page.evaluate(() => {
      window.previewOriginalRemove = Storage.prototype.removeItem;
      Storage.prototype.removeItem = function() { throw new DOMException("Fixture blocked removal","SecurityError"); };
    });
    await page.locator("#drink-clear-preview").click();
    assert.match(await page.locator("#drink-preview-status").innerText(),/couldn't clear or confirm/);
    await page.evaluate(() => { Storage.prototype.removeItem = () => {}; });
    await page.locator("#drink-clear-preview").click();
    assert.match(await page.locator("#drink-preview-status").innerText(),/couldn't clear or confirm/);
    assert.notEqual(await page.evaluate(key => localStorage.getItem(key),previewKey),null);
    await page.evaluate(() => { Storage.prototype.removeItem = window.previewOriginalRemove; delete window.previewOriginalRemove; });
    const beforeClear = await page.locator("#drink-card-demo").innerText();
    await page.locator("#drink-clear-preview").click();
    assert.equal(await page.evaluate(key => localStorage.getItem(key),previewKey),null);
    assert.equal(await page.locator("#drink-load-preview").isDisabled(),true);
    assert.equal(await page.locator("#drink-card-demo").innerText(),beforeClear);
    assert.match(await page.locator("#drink-preview-status").innerText(),/cleared from this browser/);

    for (const invalid of [
      {drink:"removed-drink",character:"sabrina"},
      {drink:"espresso",character:"removed-character"},
      {drink:"espresso",character:"sabrina",recipient:"must not be accepted"},
      ["espresso","sabrina"],
      "not-json",
    ]) {
      const raw = typeof invalid === "string" ? invalid : JSON.stringify(invalid);
      await page.evaluate(({key,raw}) => localStorage.setItem(key,raw),{key:previewKey,raw});
      await page.reload();
      await page.locator("#drink-copy-command:enabled").waitFor();
      assert.equal(await page.locator("#drink-load-preview").isDisabled(),true);
      assert.match(await page.locator("#drink-preview-status").innerText(),/was ignored/);
      assert.match(await page.locator("#drink-card-demo").innerText(),/a caramel latte/);
      assert.equal(await page.evaluate(key => localStorage.getItem(key),previewKey),raw);
    }
    await page.locator("#drink-clear-preview").click();
    assert.equal(await page.evaluate(key => localStorage.getItem(key),previewKey),null);

    await page.locator("#drink-character").selectOption("none");
    await page.locator("#drink-remember-preview").click();
    const loadingSaved = await page.evaluate(key => localStorage.getItem(key),previewKey);
    let releaseMenu;
    menuGate = new Promise(resolve => { releaseMenu = resolve; });
    await page.reload({waitUntil:"domcontentloaded"});
    for (const id of ["drink-remember-preview","drink-load-preview","drink-clear-preview"]) assert.equal(await page.locator("#"+id).isDisabled(),true);
    assert.equal(await page.evaluate(key => localStorage.getItem(key),previewKey),loadingSaved);
    menuGate = null; releaseMenu();
    await page.locator("#drink-copy-command:enabled").waitFor();
    assert.equal(await page.locator("#drink-character").inputValue(),"sabrina");
    await page.locator("#drink-load-preview").click();
    assert.equal(await page.locator("#drink-character").inputValue(),"none");
    for (const width of [1440,768,390,320]) {
      await page.setViewportSize({width,height:900});
      await page.locator("#drink-card-demo").scrollIntoViewIfNeeded();
      if (width < 760) {
        await page.locator("#drink-mobile-choice").selectOption("hot-chocolate");
        assert.match(await page.locator("#drink-card-demo").innerText(),/a hot chocolate/);
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),false,`Overflow at ${width}px`);
    }
    failMenu = true;
    await page.reload();
    await page.locator("#drink-menu-status").filter({hasText:"couldn't load"}).waitFor();
    assert.equal(await page.locator("#drink-copy-command").isDisabled(),true);
    for (const id of ["drink-remember-preview","drink-load-preview","drink-clear-preview"]) assert.equal(await page.locator("#"+id).isDisabled(),true);
    assert.match(await page.locator("#drink-preview-status").innerText(),/Nothing was loaded or changed/);
    assert.equal(await page.evaluate(key => localStorage.getItem(key),previewKey),loadingSaved);
    assert.match(await page.locator("#drink-list").innerText(),/hot chocolate/);
    assert.deepEqual(unexpectedWrites,[]);
    assert.deepEqual(errors,[]);
    console.log("Beverage browser checks passed: filters, drink details/artwork, text safety, keyboard/clipboard/mobile layout, explicit local preview save/load/clear, no recipient storage or automatic restore, cross-tab notices, invalid IDs, blocked/refused storage, and menu loading/failure safety.");
  } finally {await browser.close();}
}
main().catch(error => {console.error(error);process.exitCode=1;});
